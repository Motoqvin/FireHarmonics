"""FireLens model endpoints for the FireHarmonics backend.

The model itself lives in the separate ``firelens`` package (installed with
``pip install -e <path to firelens>``). This module only exposes it over HTTP:

* ``/api/v1/analyses`` ...  any point on Earth, as a background job with
  progress (polling or Server-Sent Events). New places download NASA FIRMS
  data and can take 20-30 minutes; cached places take 1-3 seconds.
* ``/api/v1/locations/analysis`` ... the same analysis, synchronous and
  cache-only (never downloads), for places that are already cached.
* ``/api/v1/regions/...`` and ``/api/v1/models`` ... the pre-computed files
  for the five project regions (``firelens/exports/api_v1``).

``firelens`` is imported lazily, so the rest of the backend keeps working
when it is not installed; these endpoints then answer 503.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1", tags=["FireLens model"])

REGION_ID = re.compile(r"^[a-z_]{1,40}$")
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
SSE_KEEPALIVE_SECONDS = 15.0

# HTTP status for each FireLens job error type (see firelens.jobs.classify_error).
ERROR_STATUS = {
    "invalid_request": 422,
    "firms_auth": 502,
    "firms_request_rejected": 502,
    "firms_rate_limited": 503,
    "network": 504,
    "data_unavailable": 503,
    "missing_file": 503,
    "interrupted": 503,
    "internal_error": 500,
}


# ---------------------------------------------------------------- request models
class AnalysisRequest(BaseModel):
    latitude: float = Field(..., description="-90 to 90")
    longitude: float = Field(..., description="-180 to 180")
    radius_km: float = Field(25.0, description="1 to 200 km")
    target_month: str | None = Field(None, description="YYYY-MM; default: next calendar month")
    history_years: int = Field(10, description="3 to 14 years of history")
    allow_download: bool = Field(True, description="False: use cached data only")


# ---------------------------------------------------------------- firelens access
def _firelens():
    """Import firelens on first use; 503 if it is not installed."""
    try:
        from firelens import inference, jobs
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The FireLens model package is not installed. Run: pip install -e <path to firelens>",
        ) from exc
    return inference, jobs


def get_manager():
    """The process-wide FireLens job manager (tests replace this function)."""
    _, jobs = _firelens()
    return jobs.default_manager()


def exports_dir() -> Path:
    if os.getenv("FIRELENS_EXPORTS_DIR"):
        return Path(os.environ["FIRELENS_EXPORTS_DIR"])
    from firelens.config import PROJECT_ROOT
    return PROJECT_ROOT / "exports" / "api_v1"


def _job_or_404(fn, job_id: str, *args, **kwargs):
    _, jobs = _firelens()
    try:
        return fn(job_id, *args, **kwargs)
    except jobs.JobNotFound as exc:
        raise HTTPException(status_code=404, detail=f"Unknown job {job_id}") from exc


def _failed_response(job_id: str, error: dict) -> JSONResponse:
    code = ERROR_STATUS.get(error.get("type"), 500)
    return JSONResponse(status_code=code, content={
        "status_code": code, "detail": error.get("message") or "Analysis failed",
        "job_id": job_id, "error": error})


def _links(job_id: str) -> dict:
    base = f"/api/v1/analyses/{job_id}"
    return {"status": base, "result": f"{base}/result", "events": f"{base}/events", "retry": f"{base}/retry"}


# ---------------------------------------------------------------- model status
@router.get("/model")
def model_status() -> dict[str, Any]:
    """Whether the FireLens engine can run here. Never reveals the key."""
    inference, _ = _firelens()
    from firelens import global_model as G
    from firelens.config import load_settings
    settings = load_settings(require_key=False)
    model_file = settings.data_dir / "models" / "global" / f"{G.MODEL_NAME}.joblib"
    return {"status_code": 200, "engine": "firelens", "schema_version": inference.SCHEMA_VERSION,
            "model_trained": model_file.exists(), "firms_key_configured": bool(settings.map_key),
            "forecast_horizon_months": inference.FORECAST_HORIZON_MONTHS}


# ---------------------------------------------------------------- any location: jobs
@router.post("/analyses", status_code=status.HTTP_202_ACCEPTED)
def start_analysis(body: AnalysisRequest) -> dict[str, Any]:
    """Queue an analysis and return its job ID at once."""
    inference, _ = _firelens()
    try:
        job_id = get_manager().start_analysis(
            body.latitude, body.longitude, body.radius_km, body.target_month,
            history_years=body.history_years, allow_download=body.allow_download)
    except inference.InvalidRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    snap = get_manager().get_job_status(job_id)
    return {"status_code": 202, "job_id": job_id, "status": snap["status"], "links": _links(job_id)}


@router.get("/analyses")
def list_analyses() -> dict[str, Any]:
    _firelens()
    jobs = get_manager().list_jobs()
    return {"status_code": 200, "count": len(jobs), "jobs": jobs}


@router.get("/analyses/{job_id}")
def analysis_status(job_id: str) -> dict[str, Any]:
    """Status, stage, progress_percent, eta_seconds, waiting, warnings, error."""
    snap = _job_or_404(get_manager().get_job_status, job_id)
    return {"status_code": 200, **snap, "links": _links(job_id)}


@router.get("/analyses/{job_id}/result")
def analysis_result(job_id: str):
    """200 with the result; 202 while queued/running; 4xx/5xx with the error if it failed."""
    _, jobs = _firelens()
    m = get_manager()
    try:
        result = _job_or_404(m.get_job_result, job_id)
    except jobs.JobNotFinished:
        snap = m.get_job_status(job_id)
        return JSONResponse(status_code=202, content={"status_code": 202, "job_id": job_id,
                                                      "status": snap["status"],
                                                      "progress_percent": snap["progress_percent"],
                                                      "detail": "The analysis is not finished yet."})
    except jobs.JobFailed as exc:
        return _failed_response(job_id, exc.error)
    return {"status_code": 200, "job_id": job_id, "result": result}


@router.post("/analyses/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_analysis(job_id: str) -> dict[str, Any]:
    new_id = _job_or_404(get_manager().retry_job, job_id)
    return {"status_code": 202, "job_id": new_id, "retry_of": job_id, "links": _links(new_id)}


@router.get("/analyses/{job_id}/events")
async def analysis_events(request: Request, job_id: str, after_seq: int = Query(0, ge=0),
                          last_event_id: str | None = Header(None)):
    """Server-Sent Events: one ``data:`` line per FireLens progress event.

    Ends after the ``completed`` or ``failed`` event. Reconnecting clients send
    ``Last-Event-ID`` (browsers do this automatically) and get only newer events.
    A comment line is sent every 15 s so proxies keep the connection open
    during FIRMS rate-limit pauses.
    """
    m = get_manager()
    st = _job_or_404(m.get_job_status, job_id)
    if last_event_id and last_event_id.isdigit():
        after_seq = max(after_seq, int(last_event_id))

    def frame(e: dict) -> str:
        return f"id: {e['seq']}\nevent: {e['type']}\ndata: {json.dumps(e)}\n\n"

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    if st.get("restored_from_disk"):  # job from before a restart: replay what was stored, then end
        stored = m.get_events(job_id, after_seq)

        async def replay():
            for e in stored:
                yield frame(e)
        return StreamingResponse(replay(), media_type="text/event-stream", headers=headers)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    unsubscribe = m.subscribe(job_id, lambda e: loop.call_soon_threadsafe(queue.put_nowait, e), replay=True)

    async def gen():
        try:
            while True:
                try:
                    e = await asyncio.wait_for(queue.get(), SSE_KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        return
                    yield ": keepalive\n\n"
                    continue
                if e["seq"] <= after_seq:
                    continue
                yield frame(e)
                if e["type"] in ("completed", "failed"):
                    return
        finally:
            unsubscribe()

    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


# ---------------------------------------------------------------- any location: cached, synchronous
@router.get("/locations/analysis")
def cached_location_analysis(latitude: float, longitude: float, radius_km: float = 25.0,
                             target_month: str | None = None, history_years: int = 10) -> dict[str, Any]:
    """Synchronous analysis from cached data only (never downloads).

    For a place that is not cached the result has status ``partial`` or
    ``insufficient_data``; use ``POST /api/v1/analyses`` to download it.
    """
    inference, _ = _firelens()
    try:
        result = inference.analyze_location(latitude, longitude, radius_km, target_month,
                                            history_years=history_years, allow_download=False)
    except inference.InvalidRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status_code": 200, "result": result}


# ---------------------------------------------------------------- five project regions (pre-computed)
@lru_cache(maxsize=1)
def _region_ids(path: str) -> frozenset[str]:
    data = json.loads((Path(path) / "regions.json").read_text(encoding="utf-8"))
    return frozenset(r["region_id"] for r in data["regions"])


def _export(rel: str) -> dict:
    _firelens()
    path = exports_dir() / rel
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"No FireLens export for {rel}")
    return json.loads(path.read_text(encoding="utf-8"))


def _region(region_id: str) -> str:
    _firelens()
    d = exports_dir()
    if not REGION_ID.match(region_id) or not (d / "regions.json").is_file() or region_id not in _region_ids(str(d)):
        raise HTTPException(status_code=404, detail=f"Unknown region {region_id}")
    return region_id


@router.get("/regions")
def regions() -> dict:
    return _export("regions.json")


@router.get("/regions/{region_id}/activity")
def region_activity(region_id: str) -> dict:
    return _export(f"activity/{_region(region_id)}_monthly.json")


@router.get("/regions/{region_id}/harmonized")
def region_harmonized(region_id: str) -> dict:
    return _export(f"harmonized/{_region(region_id)}_monthly.json")


@router.get("/regions/{region_id}/patterns")
def region_patterns(region_id: str) -> dict:
    return _export(f"patterns/{_region(region_id)}.json")


@router.get("/regions/{region_id}/forecast")
def region_forecast(region_id: str, month: str = Query(..., description="YYYY-MM")) -> dict:
    rid = _region(region_id)
    if not MONTH.match(month):
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")
    return _export(f"forecast/{rid}_{month}.json")


@router.get("/models")
def model_cards() -> dict:
    return _export("model_cards.json")
