from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

KINDS = {"health-checks": "HEALTH_CHECK", "alert-rules": "ALERT_RULE", "notification-channels": "NOTIFICATION_CHANNEL"}
LIMITS = {"health-checks": 100, "alert-rules": 100, "notification-channels": 10}


def identifier(value):
    return str(UUID(value))


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = Field(default=True, strict=True)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Name cannot be blank")
        return value.strip()


class RuleSettings(SettingsModel):
    notification_channel_ids: list[str] = Field(default_factory=list, max_length=10)
    notify_recovery: bool = True
    cooldown_seconds: int = Field(default=1800, ge=0, le=604800, strict=True)
    maintenance_until: datetime | None = None

    @field_validator("notification_channel_ids")
    @classmethod
    def channel_ids(cls, values):
        result = [identifier(value) for value in values]
        if len(result) != len(set(result)):
            raise ValueError("Duplicate channel")
        return result

    @field_validator("maintenance_until")
    @classmethod
    def aware(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("Maintenance timestamp requires a timezone")
        return value


class HealthCheckSettings(RuleSettings):
    url: HttpUrl
    method: Literal["GET", "HEAD"] = "GET"
    interval_seconds: int = Field(default=60, ge=10, le=86400, strict=True)
    timeout_seconds: int = Field(default=5, ge=1, le=60, strict=True)
    failure_count: int = Field(default=3, ge=1, le=20, strict=True)
    recovery_count: int = Field(default=2, ge=1, le=20, strict=True)
    expected_status_codes: list[int] = Field(default_factory=lambda: [200], min_length=1, max_length=20)

    @model_validator(mode="after")
    def valid_check(self):
        if self.url.username is not None or self.url.password is not None:
            raise ValueError("URL credentials are not supported")
        if self.timeout_seconds >= self.interval_seconds:
            raise ValueError("timeout must be shorter than interval")
        if any(isinstance(code, bool) or not 100 <= code <= 599 for code in self.expected_status_codes):
            raise ValueError("Invalid HTTP status code")
        return self


class AlertRuleSettings(RuleSettings):
    system_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    resource_type: Literal["host"] = "host"
    resource_id: Literal["host"]
    metric: Literal["cpu_percent", "memory_percent", "disk_percent"]
    operator: Literal[">=", "<="] = ">="
    threshold: float = Field(ge=0)
    recovery_threshold: float = Field(ge=0)
    duration_seconds: int = Field(default=180, ge=0, le=86400, strict=True)
    consecutive_samples: int = Field(default=3, ge=1, le=100, strict=True)

    @model_validator(mode="after")
    def valid_thresholds(self):
        if self.metric.endswith("percent") and max(self.threshold, self.recovery_threshold) > 100:
            raise ValueError("Percent thresholds must be between 0 and 100")
        if self.operator == ">=" and self.recovery_threshold >= self.threshold:
            raise ValueError("Recovery must be below the alert threshold")
        if self.operator == "<=" and self.recovery_threshold <= self.threshold:
            raise ValueError("Recovery must be above the alert threshold")
        return self


class NotificationSettings(SettingsModel):
    emails: list[str] = Field(min_length=1, max_length=20)

    @field_validator("emails")
    @classmethod
    def valid_emails(cls, values):
        result = []
        for value in values:
            value = value.strip().lower()
            if len(value) > 254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", value):
                raise ValueError("Invalid email address")
            result.append(value)
        if len(result) != len(set(result)):
            raise ValueError("Duplicate email address")
        return sorted(result)


MODELS = {"health-checks": HealthCheckSettings, "alert-rules": AlertRuleSettings, "notification-channels": NotificationSettings}


def settings(kind, value):
    return MODELS[kind].model_validate(value).model_dump(mode="json")


class Page:
    def __init__(self, kind, params):
        params = params or {}
        if set(params) - {"limit", "cursor"}:
            raise ValueError("Unknown filter")
        raw = params.get("limit", "20")
        if not re.fullmatch(r"[0-9]{1,2}", raw) or not 1 <= int(raw) <= 25:
            raise ValueError("limit must be 1–25")
        self.limit = int(raw)
        self.kind = kind
        self.key = None
        if params.get("cursor"):
            try:
                raw = params["cursor"]
                if len(raw) > 2048 or not re.fullmatch(r"[A-Za-z0-9_-]+", raw):
                    raise ValueError()
                data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
                if data["kind"] != kind or data["limit"] != self.limit:
                    raise ValueError()
                key = data["key"]
                if set(key) != {"PK", "SK"} or key["PK"] != {"S": "CONFIG#DEFAULT"}:
                    raise ValueError()
                prefix, uid = key["SK"]["S"].split("#", 1)
                if prefix != KINDS[kind] or identifier(uid) != uid:
                    raise ValueError()
                self.key = key
            except (ValueError, KeyError, TypeError, AttributeError, binascii.Error) as error:
                raise ValueError("Invalid configuration cursor") from error

    def cursor(self, key):
        if not key:
            return None
        return base64.urlsafe_b64encode(json.dumps({"kind": self.kind, "limit": self.limit, "key": key}, separators=(",", ":")).encode()).decode().rstrip("=")
