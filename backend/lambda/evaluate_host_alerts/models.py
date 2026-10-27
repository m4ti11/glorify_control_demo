from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import math

METRICS = {"cpu_percent": ("cpu_percent",), "memory_percent": ("memory", "percent"),
           "disk_percent": ("disk", "percent")}
HOST_PREFIX = "RESOURCE#HOST#host#METRIC#"


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("A metric timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def timestamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def json_body(value):
    def number(item):
        if isinstance(item, Decimal):
            return int(item) if item == item.to_integral_value() else float(item)
        raise TypeError(type(item).__name__)
    return json.dumps(value, default=number, separators=(",", ":"), allow_nan=False)


def metric_value(reading, metric):
    value = reading["metrics"]
    for part in METRICS[metric]:
        value = value[part]
    if isinstance(value, bool) or not math.isfinite(float(value)) or not 0 <= value <= 100:
        raise ValueError("Invalid host percentage")
    return value


def available(rule, measured_at, now):
    data = rule["data"]
    until = data.get("maintenance_until")
    return bool(data["enabled"] and (not until or instant(until) < min(measured_at, now)))
