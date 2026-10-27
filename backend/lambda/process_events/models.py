from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class EventModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventSubject(EventModel):
    type: str = Field(min_length=1, max_length=64)
    id: str = Field(min_length=1, max_length=512)


class EventPayload(EventModel):
    record_type: Literal["event"] = "event"
    schema_version: Literal[1]
    event_id: UUID
    system_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    event_type: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=64)
    occurred_at: datetime
    subject: EventSubject
    details: dict[str, JsonValue] = Field(default_factory=dict)
    notification_channel_ids: list[UUID] | None = Field(default=None, max_length=10)

    @field_validator("notification_channel_ids")
    @classmethod
    def unique_channels(cls, value):
        if value is not None and len(value) != len(set(value)):
            raise ValueError("Duplicate notification channel")
        return value

    @model_validator(mode="before")
    @classmethod
    def discard_server_fields(cls, value):
        # Accept the full stored-record example, but never trust client keys or receipt time.
        if isinstance(value, dict):
            return {key: item for key, item in value.items() if key not in {"PK", "SK", "received_at"}}
        return value

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value.astimezone(timezone.utc)


def timestamp(value: datetime) -> str:
    precision = "milliseconds" if value.microsecond % 1000 == 0 else "microseconds"
    return value.astimezone(timezone.utc).isoformat(timespec=precision).replace("+00:00", "Z")
