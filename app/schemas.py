"""Pydantic request/response models for the REST API."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from app.anomaly import MIN_POINTS
from app.rules import RULE_TYPES  # single source of truth: shared with the UI

HTTP_METHODS = ("GET", "POST", "HEAD", "PUT", "PATCH", "DELETE")
MONITOR_STATUSES = ("unknown", "up", "down")
INCIDENT_STATUSES = ("open", "resolved")


class MonitorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2000)
    method: str = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    interval_seconds: int = Field(default=60, ge=5, le=86_400)
    timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    expected_status_min: int = Field(default=200, ge=100, le=599)
    expected_status_max: int = Field(default=399, ge=100, le=599)
    unhealthy_threshold: int = Field(default=3, ge=1, le=100)
    healthy_threshold: int = Field(default=2, ge=1, le=100)
    enabled: bool = True

    @field_validator("method")
    @classmethod
    def _upper_method(cls, value: str) -> str:
        value = value.upper().strip()
        if value not in HTTP_METHODS:
            raise ValueError(f"method must be one of {', '.join(HTTP_METHODS)}")
        return value

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        value = value.strip()
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return value

    @model_validator(mode="after")
    def _status_range(self) -> MonitorCreate:
        if self.expected_status_min > self.expected_status_max:
            raise ValueError("expected_status_min must be <= expected_status_max")
        return self


class MonitorUpdate(MonitorCreate):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    url: str | None = Field(default=None, min_length=1, max_length=2000)
    method: str | None = None
    headers: dict[str, str] | None = None
    interval_seconds: int | None = Field(default=None, ge=5, le=86_400)
    timeout_seconds: float | None = Field(default=None, gt=0, le=300)
    expected_status_min: int | None = Field(default=None, ge=100, le=599)
    expected_status_max: int | None = Field(default=None, ge=100, le=599)
    unhealthy_threshold: int | None = Field(default=None, ge=1, le=100)
    healthy_threshold: int | None = Field(default=None, ge=1, le=100)
    enabled: bool | None = None

    @field_validator("method")
    @classmethod
    def _upper_method(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.upper().strip()
        if value not in HTTP_METHODS:
            raise ValueError(f"method must be one of {', '.join(HTTP_METHODS)}")
        return value

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return value

    @model_validator(mode="after")
    def _status_range(self) -> MonitorUpdate:
        # None means "leave unchanged" on a partial update, so only validate
        # the pair when both bounds are supplied.
        if (
            self.expected_status_min is not None
            and self.expected_status_max is not None
            and self.expected_status_min > self.expected_status_max
        ):
            raise ValueError("expected_status_min must be <= expected_status_max")
        return self


class MonitorOut(BaseModel):
    id: int
    name: str
    url: str
    method: str
    headers: dict[str, str]
    interval_seconds: int
    timeout_seconds: float
    expected_status_min: int
    expected_status_max: int
    unhealthy_threshold: int
    healthy_threshold: int
    enabled: bool
    status: str
    consecutive_failures: int
    consecutive_successes: int
    last_checked_at: str | None
    last_ok: bool | None
    last_status_code: int | None
    last_latency_ms: float | None
    last_error: str | None
    created_at: str
    updated_at: str


class CheckOut(BaseModel):
    id: int
    monitor_id: int
    checked_at: str
    ok: bool
    status_code: int | None
    latency_ms: float | None
    error: str | None


class MetricBucket(BaseModel):
    start: str
    end: str
    total: int
    failures: int
    uptime: float
    avg_latency_ms: float | None
    p95_latency_ms: float | None
    max_latency_ms: float | None


class MetricsOut(BaseModel):
    monitor_id: int
    window_hours: float
    total: int
    failures: int
    uptime: float
    avg_latency_ms: float | None
    p95_latency_ms: float | None
    max_latency_ms: float | None
    buckets: list[MetricBucket]


class AnomalyPoint(BaseModel):
    index: int
    timestamp: str | None = None
    value_ms: float
    score: float


class AnomaliesOut(BaseModel):
    monitor_id: int
    window_hours: float
    contamination: float
    points_analyzed: int
    sufficient_data: bool
    anomalies: list[AnomalyPoint]


class RuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    monitor_id: int | None = None  # None => applies to every monitor
    type: str
    threshold: float = Field(gt=0)
    window_seconds: int = Field(default=300, ge=1, le=86_400)
    duration_seconds: int = Field(default=0, ge=0, le=86_400)
    min_samples: int = Field(default=5, ge=1, le=10_000)
    webhook_url: str = ""
    email_to: list[str] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        value = value.strip()
        if value not in RULE_TYPES:
            raise ValueError(f"type must be one of {', '.join(RULE_TYPES)}")
        return value

    @model_validator(mode="after")
    def _threshold_sane(self) -> RuleCreate:
        if self.type == "failure_streak" and self.threshold != int(self.threshold):
            raise ValueError("failure_streak threshold must be a whole number of checks")
        if self.type == "error_rate" and self.threshold > 1:
            raise ValueError("error_rate threshold is a ratio between 0 and 1")
        if self.type == "anomaly":
            if self.threshold > 0.5:
                raise ValueError("anomaly threshold is a contamination ratio between 0 and 0.5")
            if self.min_samples < MIN_POINTS:
                raise ValueError(
                    f"anomaly rule needs min_samples >= {MIN_POINTS} "
                    "(the Isolation Forest minimum)"
                )
        return self


class RuleUpdate(RuleCreate):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    monitor_id: int | None = None
    type: str | None = None
    threshold: float | None = Field(default=None, gt=0)
    window_seconds: int | None = Field(default=None, ge=1, le=86_400)
    duration_seconds: int | None = Field(default=None, ge=0, le=86_400)
    min_samples: int | None = Field(default=None, ge=1, le=10_000)
    webhook_url: str | None = None
    email_to: list[str] | None = None
    enabled: bool | None = None

    @field_validator("type")
    @classmethod
    def _known_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if value not in RULE_TYPES:
            raise ValueError(f"type must be one of {', '.join(RULE_TYPES)}")
        return value

    @model_validator(mode="after")
    def _threshold_sane(self) -> RuleUpdate:
        # Shadows RuleCreate's check: unset fields are None on a partial update.
        if self.type == "anomaly":
            if self.threshold is not None and self.threshold > 0.5:
                raise ValueError("anomaly threshold is a contamination ratio between 0 and 0.5")
            if self.min_samples is not None and self.min_samples < MIN_POINTS:
                raise ValueError(
                    f"anomaly rule needs min_samples >= {MIN_POINTS} "
                    "(the Isolation Forest minimum)"
                )
        return self


class RuleOut(BaseModel):
    id: int
    name: str
    monitor_id: int | None
    type: str
    threshold: float
    window_seconds: int
    duration_seconds: int
    min_samples: int
    webhook_url: str
    email_to: list[str]
    enabled: bool
    created_at: str
    updated_at: str


class IncidentOut(BaseModel):
    id: int
    monitor_id: int
    monitor_name: str | None = None
    rule_id: int | None
    status: str
    reason: str
    opened_at: str
    updated_at: str
    closed_at: str | None
    acknowledged_at: str | None


class SummaryMonitor(BaseModel):
    id: int
    name: str
    url: str
    status: str
    enabled: bool
    last_checked_at: str | None
    last_latency_ms: float | None
    last_status_code: int | None
    last_error: str | None
    uptime_24h: float | None
    avg_latency_24h: float | None
    open_incidents: int


class SummaryOut(BaseModel):
    total_monitors: int
    up: int
    down: int
    unknown: int
    disabled: int
    open_incidents: int
    checks_24h: int
    failures_24h: int
    monitors: list[SummaryMonitor]
