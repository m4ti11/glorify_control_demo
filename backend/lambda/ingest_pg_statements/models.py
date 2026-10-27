from __future__ import annotations

from datetime import date, datetime, timezone
import re
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

MAX_REPORT_BYTES = 1024 * 1024


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class StatisticsSection(ReportModel):
    available: bool = Field(strict=True)
    rows: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=1000)


class DatabaseReport(ReportModel):
    scope: Literal["database", "cluster"]
    database: str = Field(min_length=1, max_length=128)
    available: bool = Field(strict=True)
    database_stats: StatisticsSection | None = None
    tables: StatisticsSection | None = None
    top_total: StatisticsSection | None = None
    top_mean: StatisticsSection | None = None
    pg_stat_statements: StatisticsSection | None = None
    connection_totals: StatisticsSection | None = None
    connections: StatisticsSection | None = None
    locks: StatisticsSection | None = None
    blocked: StatisticsSection | None = None
    databases_truncated: bool | None = None


class ReportDetails(DatabaseReport):
    scope: Literal["database", "cluster", "system"]
    report_date: date
    timezone: str = Field(min_length=1, max_length=100)
    captured_at: datetime | None = None
    collection_started_at: datetime | None = None
    reports: list[DatabaseReport] | None = Field(default=None, min_length=1, max_length=101)

    @field_validator("report_date", mode="before")
    @classmethod
    def calendar_date(cls, value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("report_date must use YYYY-MM-DD")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("timezone must name an IANA timezone") from error
        return value

    @field_validator("captured_at", "collection_started_at")
    @classmethod
    def aware_capture_timestamp(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("captured_at requires a timezone")
        return value

    @model_validator(mode="after")
    def aggregate_shape(self):
        if (self.scope == "system") != (self.reports is not None):
            raise ValueError("system reports must contain reports; individual reports must not")
        if self.reports:
            keys = [(item.scope, item.database) for item in self.reports]
            if len(keys) != len(set(keys)):
                raise ValueError("duplicate database report")
        return self


class ReportSubject(ReportModel):
    type: Literal["database", "cluster", "host", "system"]
    id: str = Field(min_length=1, max_length=128)


class PgStatementsPayload(ReportModel):
    record_type: Literal["event"]
    schema_version: Literal[1]
    event_id: UUID
    system_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    event_type: Literal["postgres.daily_metrics"]
    source: Literal["postgres"]
    occurred_at: datetime
    subject: ReportSubject
    details: ReportDetails

    @field_validator("occurred_at")
    @classmethod
    def aware_timestamp(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at requires a timezone")
        return value.astimezone(timezone.utc)
