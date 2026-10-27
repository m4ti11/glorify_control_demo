from __future__ import annotations

from datetime import date

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Window(StrictModel):
    start: str
    end_exclusive: str


class Finding(StrictModel):
    severity: Literal["critical", "high", "medium", "low", "info"]
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[str] = Field(min_length=1, max_length=12)


class Recommendation(StrictModel):
    priority: Literal["high", "normal", "low"]
    action: str = Field(min_length=1, max_length=1000)
    evidence_refs: list[str] = Field(min_length=1, max_length=12)


class Analysis(StrictModel):
    summary: str = Field(min_length=1, max_length=3000)
    findings: list[Finding] = Field(max_length=20)
    recommendations: list[Recommendation] = Field(max_length=20)


class MetricsCoverage(StrictModel):
    expected_interval_seconds: int = Field(ge=1)
    expected_intervals: int = Field(ge=1)
    present_intervals: int = Field(ge=0)
    percentage: float = Field(ge=0, le=100)
    largest_gap_seconds: float = Field(ge=0)
    sufficient: bool
    is_lower_bound: bool = False


class SourceStatus(StrictModel):
    status: Literal["available", "missing", "incomplete", "stale", "unavailable"]
    issues: list[Literal["missing", "incomplete", "stale", "unavailable"]] = Field(default_factory=list)
    count: int
    omitted_count: int | None = None
    observed_count: int | None = None
    observed_from: str | None = None
    observed_to: str | None = None
    last_heartbeat_at: str | None = None
    last_received_at: str | None = None
    report_date: str | None = None
    captured_at: str | None = None
    received_at: str | None = None
    snapshot_semantics: dict[str, JsonValue] | None = None
    coverage: MetricsCoverage | None = None


class SourceStatuses(StrictModel):
    host_metrics: SourceStatus
    events: SourceStatus
    postgresql: SourceStatus


class OperationalReport(StrictModel):
    schema_version: Literal[1]
    system_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    report_date: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    timezone: Literal["America/Argentina/Buenos_Aires"]
    window: Window
    generated_at: str
    source_status: SourceStatuses
    analysis: Analysis


class ReportQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    system_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    report_date: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

    format: Literal["json", "pdf"] = "json"

    @property
    def key(self):
        day = date.fromisoformat(self.report_date)
        return f"generated/operational/{self.system_id}/{day:%Y/%m/%d}.json.gz"


def parse_query(params):
    if not isinstance(params, dict) or not {"system_id", "report_date"} <= set(params) or set(params) - {"system_id", "report_date", "format"}:
        raise ValueError("system_id and report_date are required")
    query = ReportQuery.model_validate(params)
    date.fromisoformat(query.report_date)
    return query
