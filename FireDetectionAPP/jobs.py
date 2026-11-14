"""Persistent analysis jobs, bounded workers, real request progress and retry state."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException

from . import analytics, nasa
from .schemas import AnalysisRequest
from .storage import Store, fingerprint, now

TERMINAL = {"completed", "failed"}


class AnalysisService:
    def __init__(self, folder, *, cache_ttl=3600, workers=2):
        self.store = Store(folder)
        self.cache_ttl = cache_ttl
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="analysis")
        for job in self.store.jobs():
            if job["status"] not in TERMINAL:
                self.update(job["job_id"], status="failed", stage="interrupted", eta_seconds=None, nasa_pause_seconds=None,
                            error={"code": "interrupted", "detail": "The backend restarted. Retry the analysis.", "recoverable": True, "status_code": 503})

    def close(self):
        self.stop.set()
        self.executor.shutdown(wait=True, cancel_futures=True)

    def require_job(self, job_id):
        job = self.store.job(job_id)
        if job is None:
            raise HTTPException(404, "Analysis job not found")
        return job

    def update(self, job_id, **changes):
        with self.lock:
            job = self.require_job(job_id)
            job.update(changes, updated_at=now())
            self.store.save_job(job)
            return job

    def fresh(self, cached):
        if not cached:
            return False
        if cached["result"]["coverage"]["source_kind"] == "archive_import":
            return True
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached["cached_at"])).total_seconds()
        return age <= self.cache_ttl

    def submit(self, request):
        with self.lock:
            if self.stop.is_set():
                raise HTTPException(503, "Backend is shutting down")
            active = sum(job["status"] not in TERMINAL for job in self.store.jobs())
            if active >= 32:
                raise HTTPException(429, "The analysis queue is full. Retry later.", headers={"Retry-After": "10"})
            cached = self.store.cached(request)
            if not self.fresh(cached):
                nasa.get_map_key()
            stamp = now()
            job_id = str(uuid4())
            prefix = f"/api/v1/analyses/{job_id}"
            hit = self.fresh(cached)
            job = {"job_id": job_id, "status": "completed" if hit else "queued", "progress": 100 if hit else 0,
                   "stage": "completed" if hit else "queued", "eta_seconds": 0 if hit else None, "nasa_pause_seconds": None,
                   "created_at": stamp, "updated_at": stamp, "request": request.model_dump(mode="json"),
                   "cache_hit": hit, "error": None, "attempt": 1,
                   "links": {"status": prefix, "events": f"{prefix}/events", "result": f"{prefix}/result", "retry": f"{prefix}/retry"},
                   "result": cached["result"] if hit else None}
            self.store.save_job(job)
            if not hit:
                self.executor.submit(self.run, job_id)
            return job

    def retry(self, job_id):
        with self.lock:
            job = self.require_job(job_id)
            if job["status"] != "failed" or not job["error"] or not job["error"]["recoverable"]:
                raise HTTPException(409, "Only failed jobs with recoverable=true can be retried")
            not_before = job["error"].get("retry_not_before")
            if not_before:
                remaining = (datetime.fromisoformat(not_before) - datetime.now(timezone.utc)).total_seconds()
                if remaining > 0:
                    raise HTTPException(429, "NASA requested a longer pause before retrying.",
                                        headers={"Retry-After": str(int(remaining) + 1)})
            if sum(item["status"] not in TERMINAL for item in self.store.jobs()) >= 32:
                raise HTTPException(429, "The analysis queue is full. Retry later.")
            nasa.get_map_key()
            job = self.update(job_id, status="queued", stage="queued", progress=0, eta_seconds=None,
                              nasa_pause_seconds=None, error=None, result=None, cache_hit=False, attempt=job.get("attempt", 1) + 1)
            self.executor.submit(self.run, job_id)
            return job

    def call_nasa(self, job_id, operation, *args):
        for attempt in range(3):
            if self.stop.is_set():
                raise nasa.FIRMSException(503, "The backend stopped. Retry the analysis.", recoverable=True, code="interrupted")
            try:
                return operation(*args)
            except nasa.FIRMSException as exc:
                delay = exc.retry_after or 2 ** attempt
                if not exc.recoverable or attempt == 2 or delay > 60:
                    raise
                job = self.require_job(job_id)
                stage = job["stage"]
                self.update(job_id, status="paused", stage="waiting_for_nasa", nasa_pause_seconds=delay, eta_seconds=None)
                self.stop.wait(delay)
                self.update(job_id, status="running", stage=stage, nasa_pause_seconds=None)

    def run(self, job_id):
        try:
            job = self.update(job_id, status="running", stage="checking_availability", progress=1)
            request = AnalysisRequest.model_validate(job["request"])
            first, last = self.call_nasa(job_id, nasa.available_window, request.source.value)
            today = datetime.now(timezone.utc).date()
            requested_end = request.end_date or min(last, today)
            requested_start = request.start_date or requested_end - timedelta(days=request.lookback_days - 1)
            if (requested_end - requested_start).days >= 3650:
                raise HTTPException(422, "An analysis can cover at most 3650 days")
            start, end = max(first, requested_start), min(last, requested_end)
            if start > end:
                raise nasa.FIRMSException(422, "The requested period has no available NASA data.", code="insufficient_data")
            warnings = []
            if (start, end) != (requested_start, requested_end):
                warnings.append("The requested period was clipped to NASA's available dates. Statistics use the downloaded period only.")
            boxes = analytics.bounding_boxes(request.latitude, request.longitude, request.radius_km)
            batches = []
            cursor = start
            while cursor <= end:
                days = min(5, (end - cursor).days + 1)
                for box in boxes:
                    area = nasa.validate_area(",".join(f"{value:.8f}" for value in box))
                    batches.append((request.source.value, area, days, cursor))
                cursor += timedelta(days=days)
            raw_rows = []
            started = time.monotonic()
            for index, arguments in enumerate(batches, 1):
                self.update(job_id, stage="downloading", progress=5 + int(85 * (index - 1) / len(batches)))
                key = fingerprint([str(value) for value in arguments])
                rows = self.store.chunk(key, self.cache_ttl)
                if rows is None:
                    rows = self.call_nasa(job_id, nasa.fetch_area, *arguments)
                    self.store.save_chunk(key, rows)
                raw_rows.extend(rows)
                elapsed = time.monotonic() - started
                self.update(job_id, progress=5 + int(85 * index / len(batches)),
                            eta_seconds=round(elapsed / index * (len(batches) - index)))
            self.update(job_id, stage="parsing", progress=92, eta_seconds=None)
            try:
                normalized = analytics.normalize_rows(raw_rows)
            except HTTPException as exc:
                raise nasa.FIRMSException(502, "NASA returned invalid detection rows.", code="invalid_detections") from exc
            rows = analytics.filter_rows(normalized, request, start, end)
            self.update(job_id, stage="analyzing", progress=96)
            coverage = {"start_date": str(start), "end_date": str(end), "complete": True,
                        "requested_start_date": str(requested_start), "requested_end_date": str(requested_end),
                        "source_kind": "area_api", "downloaded_at": now(), "request_count": len(batches),
                        "raw_row_count": len(raw_rows), "unique_rows_before_radius_filter": len(normalized)}
            result = analytics.describe(rows, request, coverage, warnings)
            self.store.save_dataset(request, rows, result)
            self.update(job_id, status="completed", stage="completed", progress=100, eta_seconds=0,
                        nasa_pause_seconds=None, error=None, result=result)
        except HTTPException as exc:
            delay = getattr(exc, "retry_after", None)
            self.update(job_id, status="failed", stage="failed", eta_seconds=None, nasa_pause_seconds=None,
                        error={"code": getattr(exc, "code", "analysis_error"), "detail": str(exc.detail),
                               "recoverable": getattr(exc, "recoverable", False), "status_code": exc.status_code,
                               "retry_after_seconds": delay,
                               "retry_not_before": (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat() if delay else None})
        except Exception:
            # Raw exceptions can contain a private upstream URL. Never persist them.
            self.update(job_id, status="failed", stage="failed", eta_seconds=None, nasa_pause_seconds=None,
                        error={"code": "internal_error", "detail": "The analysis failed unexpectedly.", "recoverable": True, "status_code": 500})

    def import_rows(self, request, raw_rows, complete):
        normalized = analytics.normalize_rows(raw_rows)
        start, end = request.start_date, request.end_date
        rows = analytics.filter_rows(normalized, request, start, end)
        coverage = {"start_date": str(start), "end_date": str(end), "complete": complete,
                    "source_kind": "archive_import", "downloaded_at": now(), "raw_row_count": len(raw_rows),
                    "unique_rows_before_radius_filter": len(normalized), "coverage_declaration": "provided_by_uploader"}
        result = analytics.describe(rows, request, coverage)
        self.store.save_dataset(request, rows, result)
        return result
