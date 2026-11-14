"""Public request and response schemas. Forecasts are intentionally unavailable."""
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Source(str, Enum):
    landsat = "LANDSAT_NRT"
    modis = "MODIS_NRT"
    modis_standard = "MODIS_SP"
    noaa20 = "VIIRS_NOAA20_NRT"
    noaa20_standard = "VIIRS_NOAA20_SP"
    noaa21 = "VIIRS_NOAA21_NRT"
    noaa21_standard = "VIIRS_NOAA21_SP"
    snpp = "VIIRS_SNPP_NRT"
    snpp_standard = "VIIRS_SNPP_SP"


DEFAULT_SOURCE = Source.noaa20


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
    date: str | None
    day_range: int


class ErrorResponse(BaseModel):
    status_code: int
    detail: str
    recoverable: bool = False


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_km: float = Field(default=25, gt=0, le=500)
    source: Source = DEFAULT_SOURCE
    lookback_days: int = Field(default=30, ge=1, le=3650)
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def validate_dates(self):
        today = datetime.now(timezone.utc).date()
        if any(value and value > today for value in (self.start_date, self.end_date)):
            raise ValueError("Dates must not be in the future")
        if self.start_date and self.end_date:
            if self.start_date > self.end_date:
                raise ValueError("start_date must be on or before end_date")
            if (self.end_date - self.start_date).days >= 3650:
                raise ValueError("An analysis can cover at most 3650 days")
        return self


class JobError(BaseModel):
    code: str
    detail: str
    recoverable: bool
    status_code: int
    retry_after_seconds: int | None = None
    retry_not_before: str | None = None


class JobResponse(BaseModel):
    status_code: Literal[200] = 200
    job_id: str
    status: Literal["queued", "running", "paused", "completed", "failed"]
    progress: int = Field(ge=0, le=100)
    stage: str
    eta_seconds: int | None = None
    nasa_pause_seconds: int | None = None
    created_at: str
    updated_at: str
    request: AnalysisRequest
    cache_hit: bool = False
    attempt: int = 1
    error: JobError | None = None
    links: dict[str, str]


class AcceptedJobResponse(JobResponse):
    status_code: Literal[202] = 202


class Prediction(BaseModel):
    probability: None = None
    explanation: str = "A prediction model has not been added. Observed detections are not a forecast."


class AnalysisResult(BaseModel):
    location: dict[str, float]
    source: str
    coverage: dict[str, Any]
    detection_count: int
    historical_activity: dict[str, Any]
    seasonal_patterns: dict[str, Any]
    long_term_trends: dict[str, Any]
    anomalies: dict[str, Any]
    hotspots: list[dict[str, Any]]
    prediction: Prediction = Field(default_factory=Prediction)
    warnings: list[str]
