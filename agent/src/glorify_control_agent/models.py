from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


# HOST + optional POSTGRES leave 98 slots in one 100-action history transaction.
MAX_CONTAINERS = 98

class MetricModel(BaseModel):

    model_config = ConfigDict(extra="forbid")


class MemoryMetrics(MetricModel):
    total_mb: int = Field(gt=0, le=16_777_216)
    used_mb: float = Field(ge=0, le=16_777_216)
    percent: float = Field(ge=0, le=100)

    @field_validator("used_mb")
    @classmethod
    def used_memory_cannot_exceed_total(cls, value: float, info) -> float:
        total = info.data.get("total_mb")
        if total is not None and value > total:
            raise ValueError("used_mb cannot be greater than total_mb")
        return value


class DiskMetrics(MetricModel):
    path: str = Field(min_length=1, max_length=512)
    total_gb: float = Field(gt=0, le=16_777_216)
    used_gb: float = Field(ge=0, le=16_777_216)
    percent: float = Field(ge=0, le=100)

    @field_validator("used_gb")
    @classmethod
    def used_disk_cannot_exceed_total(cls, value: float, info) -> float:
        total = info.data.get("total_gb")
        if total is not None and value > total:
            raise ValueError("used_gb cannot be greater than total_gb")
        return value


class HostMetrics(MetricModel):
    hostname: str = Field(min_length=1, max_length=255)
    uptime_seconds: int = Field(ge=0, le=31_536_000_000)
    cpu_percent: float = Field(ge=0, le=100)
    memory: MemoryMetrics
    disk: DiskMetrics
    extended: dict[str, JsonValue] | None = None


class ContainerMetrics(MetricModel):
    name: str = Field(min_length=1, max_length=255)
    image: str = Field(min_length=1, max_length=1024)
    state: str = Field(min_length=1, max_length=64)
    health: Literal["healthy", "unhealthy", "starting"] | None = None
    started_at: datetime | None = None
    restart_count: int = Field(ge=0, le=1_000_000)
    cpu_percent: float | None = Field(default=None, ge=0, le=100)
    memory_usage_mb: float | None = Field(default=None, ge=0, le=16_777_216)
    memory_limit_mb: float | None = Field(default=None, gt=0, le=16_777_216)
    memory_percent: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def usage_cannot_exceed_limit(self) -> "ContainerMetrics":
        if self.memory_limit_mb is not None and self.memory_usage_mb > self.memory_limit_mb:
            raise ValueError("memory_usage_mb cannot be greater than memory_limit_mb")
        return self


class DockerMetrics(MetricModel):
    available: bool
    running_containers: int = Field(ge=0, le=100_000)
    total_containers: int = Field(ge=0, le=100_000)
    containers: list[ContainerMetrics] = Field(max_length=MAX_CONTAINERS)

    @field_validator("total_containers")
    @classmethod
    def total_cannot_be_less_than_running(cls, value: int, info) -> int:
        running = info.data.get("running_containers")
        if running is not None and value < running:
            raise ValueError("total_containers cannot be less than running_containers")
        return value


class PostgresConnections(MetricModel):
    database: str | None
    connections: int = Field(ge=0)
    active: int = Field(ge=0)
    idle: int = Field(ge=0)

    @model_validator(mode="after")
    def states_cannot_exceed_connections(self):
        if self.active + self.idle > self.connections:
            raise ValueError("active and idle cannot exceed connections")
        return self


class PostgresSample(MetricModel):
    timestamp: datetime
    available: bool
    connections: list[PostgresConnections]

    @field_validator("timestamp")
    @classmethod
    def aware_timestamp(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include timezone")
        return value


class PostgresMetrics(MetricModel):
    samples: list[PostgresSample]


class MetricsPayload(MetricModel):
    schema_version: Literal[1]
    system_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    agent_version: str = Field(min_length=1, max_length=64)
    timestamp: datetime
    host: HostMetrics
    docker: DockerMetrics
    postgres: PostgresMetrics | None = None

    @field_validator("timestamp")
    @classmethod
    def timestamp_requires_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value




class EventSubject(MetricModel):
    type: str = Field(min_length=1, max_length=64)
    id: str = Field(min_length=1, max_length=512)


class EventPayload(MetricModel):
    record_type: Literal["event"] = "event"
    schema_version: Literal[1] = 1
    event_id: UUID = Field(default_factory=uuid4)
    system_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    event_type: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=64)
    occurred_at: datetime
    subject: EventSubject
    details: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include timezone")
        return value
