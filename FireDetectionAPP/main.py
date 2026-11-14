import csv
import io
import os
from pathlib import Path
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

load_dotenv(Path(__file__).with_name(".env"))

FIRMS_BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
AVAILABILITY_BASE_URL = (
    "https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv"
)

SOURCE = "VIIRS_SNPP_NRT"
AREA = "world"
DAY_RANGE = 1
DATE = "2026-11-02"


class HealthResponse(BaseModel):
    status_code: Literal[200] = 200
    status: Literal["ok"] = "ok"
    service: Literal["FireHarmonics"] = "FireHarmonics"


class ParsedDataResponse(BaseModel):
    status_code: Literal[200] = 200
    source: str
    count: int
    rows: list[dict[str, str]]


class FireDataResponse(ParsedDataResponse):
    area: str
    date: str
    day_range: int


class ErrorResponse(BaseModel):
    status_code: int
    detail: str


app = FastAPI(
    title="FireHarmonics API",
    description="NASA FIRMS fire detections and data availability as parsed JSON.",
    responses={
        500: {"model": ErrorResponse, "description": "Missing server configuration"},
        502: {"model": ErrorResponse, "description": "FIRMS request or CSV failure"},
        504: {"model": ErrorResponse, "description": "FIRMS request timed out"},
    },
)


try:
    from .firelens_api import router as firelens_router
except ImportError:  # run as a script: python FireDetectionAPP/main.py
    from firelens_api import router as firelens_router

app.include_router(firelens_router)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"status_code": exc.status_code, "detail": exc.detail},
        headers=exc.headers,
    )


def get_map_key() -> str:
    map_key = os.getenv("NASA_FIRMS_MAP_KEY", "").strip()
    if not map_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "NASA_FIRMS_MAP_KEY is not configured. "
                "Set it in FireDetectionAPP/.env or your environment."
            ),
        )
    return map_key


def parse_csv(response_text: str) -> list[dict[str, str]]:
    """Preserve CSV values as strings and reject non-CSV error responses."""
    text = response_text.lstrip("\ufeff").strip()
    if not text:
        return []

    try:
        reader = csv.DictReader(io.StringIO(text), strict=True)
        columns = reader.fieldnames
        if (
            not columns
            or len(columns) < 2
            or any(not column.strip() for column in columns)
            or len(set(columns)) != len(columns)
            or text.lower().startswith("invalid")
            or text.startswith("<")
        ):
            raise ValueError("Invalid CSV headers")

        rows = list(reader)
        if any(None in row or None in row.values() for row in rows):
            raise ValueError("CSV row does not match the headers")
        return rows
    except (csv.Error, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="FIRMS returned an error or malformed CSV data.",
        ) from exc


def fetch_csv(url: str, timeout: float) -> list[dict[str, str]]:
    try:
        response = httpx.get(url, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="The NASA FIRMS request timed out.",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not connect to NASA FIRMS.",
        ) from exc

    if response.status_code != status.HTTP_200_OK:
        # Upstream URLs and error bodies may contain the private MAP_KEY.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"NASA FIRMS returned HTTP {response.status_code}.",
        )
    return parse_csv(response.text)


def fetch_firms_data() -> list[dict[str, str]]:
    # DATE must be at the end of the Area API URL.
    url = (
        f"{FIRMS_BASE_URL}/{get_map_key()}/"
        f"{SOURCE}/{AREA}/{DAY_RANGE}/{DATE}"
    )
    return fetch_csv(url, timeout=60.0)


def fetch_available_dates() -> list[dict[str, str]]:
    url = f"{AVAILABILITY_BASE_URL}/{get_map_key()}/{SOURCE}"
    return fetch_csv(url, timeout=30.0)


@app.get("/health", response_model=HealthResponse, status_code=status.HTTP_200_OK)
async def health() -> HealthResponse:
    """Check backend liveness without requiring a NASA key or contacting NASA."""
    return HealthResponse()


@app.get("/", response_model=FireDataResponse, status_code=status.HTTP_200_OK)
@app.get("/fires", response_model=FireDataResponse, status_code=status.HTTP_200_OK)
@app.get("/test", response_model=FireDataResponse, status_code=status.HTTP_200_OK)
def fire_detections() -> FireDataResponse:
    """Return every parsed detection; / and /test are aliases for /fires."""
    rows = fetch_firms_data()
    return FireDataResponse(
        source=SOURCE,
        area=AREA,
        date=DATE,
        day_range=DAY_RANGE,
        count=len(rows),
        rows=rows,
    )


@app.get("/availability", response_model=ParsedDataResponse, status_code=200)
def availability() -> ParsedDataResponse:
    rows = fetch_available_dates()
    return ParsedDataResponse(source=SOURCE, count=len(rows), rows=rows)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
