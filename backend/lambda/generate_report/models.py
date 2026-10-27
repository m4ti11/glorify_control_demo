from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import json
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError


TIMEZONE = ZoneInfo("America/Argentina/Buenos_Aires")
INPUT_VERSION = 2
MAX_INPUT_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
MAX_METRIC_ITEMS = 1000
MAX_EVENT_ITEMS = 500
MAX_EVENT_QUERY_ITEMS = 2000
MAX_PG_BYTES = 1024 * 1024


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


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


class Window(StrictModel):
    start: str
    end_exclusive: str


class MetricsCoverage(StrictModel):
    expected_interval_seconds: int = Field(ge=1)
    expected_intervals: int = Field(ge=1)
    present_intervals: int = Field(ge=0)
    percentage: float = Field(ge=0, le=100)
    largest_gap_seconds: float = Field(ge=0)
    sufficient: bool
    is_lower_bound: bool = False


class SourceInput(StrictModel):
    status: Literal["available", "missing", "incomplete", "stale", "unavailable"]
    issues: list[Literal["missing", "incomplete", "stale", "unavailable"]] = Field(default_factory=list)
    count: int
    data: JsonValue | None
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


class InputSources(StrictModel):
    host_metrics: SourceInput
    events: SourceInput
    postgresql: SourceInput


class ReportInput(StrictModel):
    schema_version: Literal[2]
    system_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    report_date: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    timezone: Literal["America/Argentina/Buenos_Aires"]
    window: Window
    generated_at: str
    source_selection: dict[str, str]
    sources: InputSources


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
    report_date: str
    timezone: Literal["America/Argentina/Buenos_Aires"]
    window: Window
    generated_at: str
    source_status: SourceStatuses
    analysis: Analysis


def output_json_schema() -> dict:
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "findings": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "severity": {"type": "string", "enum": ["critical", "high", "medium", "low", "info"]},
                    "title": {"type": "string"}, "description": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                }, "required": ["severity", "title", "description", "evidence_refs"],
            }},
            "recommendations": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "priority": {"type": "string", "enum": ["high", "normal", "low"]},
                    "action": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                }, "required": ["priority", "action", "evidence_refs"],
            }},
        }, "required": ["summary", "findings", "recommendations"],
    }


def reporting_window(report_date: date) -> tuple[datetime, datetime]:
    start_local = datetime.combine(report_date, time.min, TIMEZONE)
    end_local = datetime.combine(report_date + timedelta(days=1), time.min, TIMEZONE)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def metric_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def event_bounds(start: datetime, end_exclusive: datetime) -> tuple[str, str]:
    # Stored sort keys append a variable-precision UTC timestamp and #event_id.
    # Bare second prefixes include all fractions at start and precede all keys
    # at the exclusive end boundary.
    return ("EVENT#" + start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "EVENT#" + end_exclusive.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))


def serialize_input(value: dict, max_bytes: int = MAX_INPUT_BYTES) -> bytes:
    report = ReportInput.model_validate(value)
    raw = json.dumps(report.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(raw) > max_bytes:
        raise ValueError("Report input exceeds configured limit")
    return raw


def serialize_output(value: dict, max_bytes: int = MAX_OUTPUT_BYTES) -> bytes:
    report = OperationalReport.model_validate(value)
    raw = json.dumps(report.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    if len(raw) > max_bytes:
        raise ValueError("Generated report exceeds configured limit")
    return raw


def validate_analysis(raw: str, allowed_evidence: set[str], max_bytes: int = MAX_OUTPUT_BYTES) -> Analysis:
    try:
        data = json.loads(raw)
        result = Analysis.model_validate(data)
    except (json.JSONDecodeError, ValidationError, TypeError) as error:
        raise ValueError("Model response failed report schema validation") from error
    encoded = json.dumps(result.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=False).encode()
    if len(encoded) > max_bytes:
        raise ValueError("Model response exceeds configured output size limit")
    for item in [*result.findings, *result.recommendations]:
        if not set(item.evidence_refs) <= allowed_evidence:
            raise ValueError("Model response cites evidence outside the supplied input")
    return result
