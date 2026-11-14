"""FastAPI entry point. Run from the project root with one Uvicorn process."""
import asyncio
import json
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated

# Keep `python FireDetectionAPP/main.py` working as well as module-based startup.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # Shared module retained for compatibility with existing test mocks.
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import ValidationError

from FireDetectionAPP import nasa
from FireDetectionAPP.archives import MAX_UPLOAD_BYTES, parse_archive
from FireDetectionAPP.jobs import TERMINAL, AnalysisService
from FireDetectionAPP.nasa import AVAILABILITY_BASE_URL, FIRMS_BASE_URL, get_map_key, parse_csv
from FireDetectionAPP.regions import REGIONS
from FireDetectionAPP.schemas import (
    DEFAULT_SOURCE, AcceptedJobResponse, AnalysisRequest, AnalysisResult, ErrorResponse,
    FireDataResponse, HealthResponse, JobResponse, ParsedDataResponse, Source,
)

load_dotenv(Path(__file__).with_name(".env"))
SOURCE = DEFAULT_SOURCE.value
AREA = "world"
DAY_RANGE = 1
DATE = None  # NASA's latest available observations; explicit dates are query parameters.
_service_lock = threading.Lock()


def create_service():
    folder = os.getenv("FIREHARMONICS_DATA_DIR") or str(Path(__file__).with_name(".data"))
    ttl = int(os.getenv("FIREHARMONICS_CACHE_TTL_SECONDS", "3600"))
    if ttl < 0:
        raise RuntimeError("FIREHARMONICS_CACHE_TTL_SECONDS must be nonnegative")
    return AnalysisService(folder, cache_ttl=ttl)


@asynccontextmanager
async def lifespan(application):
    application.state.service = create_service()
    try:
        yield
    finally:
        application.state.service.close()


app = FastAPI(
    title="FireHarmonics API", version="1.0.0", lifespan=lifespan,
    description="NASA FIRMS detections, archive imports and background descriptive analyses. No forecast model is installed.",
    responses={
        500: {"model": ErrorResponse, "description": "Missing configuration or internal failure"},
        502: {"model": ErrorResponse, "description": "NASA request or CSV failure"},
        504: {"model": ErrorResponse, "description": "NASA request timeout"},
    },
)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    content = {"status_code": exc.status_code, "detail": exc.detail,
               "recoverable": getattr(exc, "recoverable", False)}
    if getattr(exc, "retry_after", None):
        content["retry_after_seconds"] = exc.retry_after
    return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content=jsonable_encoder({
        "status_code": 422, "detail": "Request validation failed", "errors": exc.errors(), "recoverable": False,
    }, custom_encoder={ValueError: str}))


def service(request: Request) -> AnalysisService:
    with _service_lock:
        if not hasattr(request.app.state, "service"):
            request.app.state.service = create_service()
    return request.app.state.service


Service = Annotated[AnalysisService, Depends(service)]


def location_parameters(
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    radius_km: float = Query(default=25, gt=0, le=500),
    source: Source = DEFAULT_SOURCE,
    lookback_days: int = Query(default=30, ge=1, le=3650),
    start_date: date | None = None,
    end_date: date | None = None,
):
    try:
        return AnalysisRequest(latitude=latitude, longitude=longitude, radius_km=radius_km,
                               source=source, lookback_days=lookback_days,
                               start_date=start_date, end_date=end_date)
    except ValidationError as exc:
        raise HTTPException(422, "Invalid analysis dates or coordinates") from exc


Location = Annotated[AnalysisRequest, Depends(location_parameters)]


def fetch_firms_data(source=SOURCE, area=AREA, day_range=DAY_RANGE, selected_date=DATE):
    return nasa.fetch_area(source, area, day_range, selected_date)


def fetch_available_dates(source=SOURCE):
    return nasa.fetch_availability(source)


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health():
    """Backend liveness only; no NASA request or model check."""
    return HealthResponse()


@app.get("/", response_model=FireDataResponse, tags=["NASA data"])
@app.get("/fires", response_model=FireDataResponse, tags=["NASA data"])
@app.get("/test", response_model=FireDataResponse, tags=["NASA data"])
@app.get("/api/v1/fires", response_model=FireDataResponse, tags=["NASA data"])
def fire_detections(
    source: Source = DEFAULT_SOURCE,
    area: str = Query(default="world", max_length=160),
    day_range: int = Query(default=1, ge=1, le=5),
    selected_date: date | None = Query(default=None, alias="date"),
):
    """All raw CSV columns and string values. Area is world or west,south,east,north."""
    area = nasa.validate_area(area)
    if selected_date and selected_date > datetime.now(timezone.utc).date():
        raise HTTPException(422, "date must not be in the future")
    rows = fetch_firms_data(source.value, area, day_range, selected_date)
    return FireDataResponse(source=source.value, area=area, date=str(selected_date) if selected_date else None,
                            day_range=day_range, count=len(rows), rows=rows)


@app.get("/availability", response_model=ParsedDataResponse, tags=["NASA data"])
@app.get("/api/v1/availability", response_model=ParsedDataResponse, tags=["NASA data"])
def availability(source: str = Query(default=SOURCE)):
    if source != "ALL" and source not in {item.value for item in Source}:
        raise HTTPException(422, "source must be ALL or a documented FIRMS source")
    rows = fetch_available_dates(source)
    return ParsedDataResponse(source=source, count=len(rows), rows=rows)


@app.get("/api/v1/sources", tags=["NASA data"])
def sources():
    return {"status_code": 200, "default": SOURCE, "sources": [item.value for item in Source],
            "area_day_range": {"min": 1, "max": 5},
            "notes": ["S-NPP observation delivery ended on 2026-11-02. Use NOAA-20 or NOAA-21 for current observations.",
                      "LANDSAT_NRT covers the US and Canada only. Check availability before requesting dates."],
            "documentation": {"area": "https://firms.modaps.eosdis.nasa.gov/api/area/",
                              "availability": "https://firms.modaps.eosdis.nasa.gov/api/data_availability/"}}


@app.post("/api/v1/analyses", response_model=AcceptedJobResponse, status_code=202, tags=["Analyses"])
def start_analysis(payload: AnalysisRequest, manager: Service):
    """Queue work and return immediately. A fresh saved result avoids NASA downloads."""
    return manager.submit(payload)


@app.get("/api/v1/analyses", tags=["Analyses"])
def list_analyses(manager: Service, limit: int = Query(default=20, ge=1, le=100)):
    jobs = manager.store.jobs()
    return {"status_code": 200, "count": len(jobs), "jobs": [JobResponse.model_validate(job).model_dump(mode="json") for job in jobs[:limit]]}


@app.get("/api/v1/analyses/{job_id}", response_model=JobResponse, tags=["Analyses"])
def analysis_status(job_id: str, manager: Service):
    return manager.require_job(job_id)


@app.get("/api/v1/analyses/{job_id}/events", tags=["Analyses"], response_class=StreamingResponse,
         responses={200: {"description": "SSE events with persistent numeric IDs", "content": {"text/event-stream": {"schema": {"type": "string"}}}}})
async def analysis_events(job_id: str, request: Request, manager: Service,
                          after_id: int = Query(default=0, ge=0),
                          last_event_id: Annotated[int | None, Header(ge=0)] = None):
    manager.require_job(job_id)

    async def stream():
        cursor = max(after_id, last_event_id or 0)
        heartbeat = time.monotonic()
        while not await request.is_disconnected():
            events = await asyncio.to_thread(manager.store.events, job_id, cursor)
            for seq, snapshot in events:
                cursor = seq
                kind = "completed" if snapshot["status"] == "completed" else "failed" if snapshot["status"] == "failed" else "progress"
                data = JobResponse.model_validate(snapshot).model_dump(mode="json")
                yield f"id: {seq}\nevent: {kind}\ndata: {json.dumps(data, allow_nan=False)}\n\n"
            current = await asyncio.to_thread(manager.require_job, job_id)
            if current["status"] in TERMINAL:
                # A state transition may have occurred after the first events read.
                if await asyncio.to_thread(manager.store.events, job_id, cursor):
                    continue
                break
            if time.monotonic() - heartbeat >= 15:
                yield ": heartbeat\n\n"
                heartbeat = time.monotonic()
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/v1/analyses/{job_id}/result", tags=["Analyses"])
def analysis_result(job_id: str, manager: Service):
    job = manager.require_job(job_id)
    if job["status"] == "completed":
        result = AnalysisResult.model_validate(job["result"]).model_dump(mode="json")
        return {"status_code": 200, "job_id": job_id, "status": "completed", "result": result}
    if job["status"] == "failed":
        return JSONResponse(status_code=409, content={"status_code": 409, "job_id": job_id, "status": "failed", "error": job["error"]})
    return JSONResponse(status_code=202, headers={"Retry-After": "2"}, content={
        "status_code": 202, "job_id": job_id, "status": job["status"], "progress": job["progress"], "result": None,
    })


@app.post("/api/v1/analyses/{job_id}/retry", response_model=AcceptedJobResponse, status_code=202, tags=["Analyses"])
def retry_analysis(job_id: str, manager: Service):
    return manager.retry(job_id)


@app.get("/api/v1/locations/analysis", tags=["Saved results"])
def saved_location(manager: Service, location: Location):
    """Read saved data only. Never start a download."""
    cached = manager.store.latest_location(location)
    if not cached:
        return {"status_code": 200, "status": "insufficient_data", "result": None,
                "explanation": "No matching saved analysis. POST /api/v1/analyses or import an archive first."}
    return {"status_code": 200, "status": "available", "cached_at": cached["cached_at"],
            "stale": not manager.fresh(cached), "result": cached["result"]}


@app.get("/api/v1/regions", tags=["Saved results"])
def regions(manager: Service, source: Source = DEFAULT_SOURCE):
    items = []
    for region in REGIONS:
        payload = AnalysisRequest(**{key: region[key] for key in ("latitude", "longitude", "radius_km")}, source=source)
        cached = manager.store.latest_location(payload)
        items.append({**region, "source": source.value, "status": "available" if cached else "insufficient_data",
                      "cached_at": cached["cached_at"] if cached else None,
                      "stale": not manager.fresh(cached) if cached else False,
                      "patterns_url": f"/api/v1/regions/{region['id']}/patterns"})
    return {"status_code": 200, "regions": items}


@app.get("/api/v1/regions/{region_id}/patterns", tags=["Saved results"])
@app.get("/api/v1/regions/{region_id}/activity", tags=["Saved results"])
def region_patterns(region_id: str, manager: Service, source: Source = DEFAULT_SOURCE):
    region = next((item for item in REGIONS if item["id"] == region_id), None)
    if region is None:
        raise HTTPException(404, "Region not found")
    payload = AnalysisRequest(**{key: region[key] for key in ("latitude", "longitude", "radius_km")}, source=source)
    cached = manager.store.latest_location(payload)
    return {"status_code": 200, "region": region, "status": "available" if cached else "insufficient_data",
            "cached_at": cached["cached_at"] if cached else None,
            "stale": not manager.fresh(cached) if cached else False,
            "result": cached["result"] if cached else None}


@app.get("/api/v1/harmonized", tags=["Saved results"])
def harmonized(manager: Service, location: Location,
               offset: int = Query(default=0, ge=0), limit: int = Query(default=1000, ge=1, le=10000)):
    cached = manager.store.latest_location(location)
    if not cached:
        return {"status_code": 200, "status": "insufficient_data", "count": 0, "rows": []}
    rows = cached["detections"]
    return {"status_code": 200, "status": "available", "count": len(rows), "offset": offset,
            "limit": limit, "rows": rows[offset:offset + limit], "coverage": cached["result"]["coverage"],
            "cached_at": cached["cached_at"], "stale": not manager.fresh(cached)}


@app.get("/api/v1/archives", tags=["Archives"])
def archive_information():
    return {"status_code": 200, "download_url": nasa.ARCHIVE_URL,
            "authentication_required": True, "authentication_methods": ["Earthdata Login", "Email Code"],
            "automatic_download_available": False,
            "explanation": "Request the export on NASA's website, then upload the downloaded file here. NASA emails download instructions after processing.",
            "import_url": "/api/v1/archives/import", "formats": ["CSV", "JSON", "GeoJSON", "ZIP containing CSV/JSON or a point shapefile"],
            "max_upload_bytes": MAX_UPLOAD_BYTES, "max_rows": 200000,
            "notes": ["Select a single dataset for each import. Supply the export's date range and location.",
                      "coverage_complete=true declares the export complete for that period and area; detection dates alone cannot establish coverage."]}


@app.post("/api/v1/archives/import", status_code=201, tags=["Archives"])
def import_archive(
    manager: Service, location: Location,
    file: Annotated[UploadFile, File()], coverage_complete: bool = False,
):
    if not location.start_date or not location.end_date:
        raise HTTPException(422, "Archive import requires start_date and end_date from the export request")
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Upload exceeds 20 MiB")
    rows = parse_archive(content, file.filename)
    result = manager.import_rows(location, rows, coverage_complete)
    return {"status_code": 201, "status": "imported", "result": result}


@app.get("/api/v1/model", tags=["Model status"])
@app.get("/api/v1/models", tags=["Model status"])
def model_status():
    return {"status_code": 200, "status": "not_configured", "available": False, "models": [],
            "prediction": {"probability": None, "explanation": "A prediction model will be added later. Descriptive data analysis is available now."}}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
