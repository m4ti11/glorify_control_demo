from __future__ import annotations

import base64
import binascii
from datetime import date, datetime, time, timedelta, timezone
import json
import re
from zoneinfo import ZoneInfo

TIMEZONE = "America/Argentina/Buenos_Aires"


class EventsQuery:
    """A bounded page, using inclusive local calendar dates and a bound cursor."""

    def __init__(self, params, now=None):
        params = params or {}
        if set(params) - {"from", "to", "limit", "cursor"}:
            raise ValueError("Unknown query parameter")
        today = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(TIMEZONE)).date()
        self.start = self._date(params.get("from", (today - timedelta(days=today.weekday())).isoformat()))
        self.end = self._date(params.get("to", today.isoformat()))
        if self.start > self.end or (self.end - self.start).days > 365:
            raise ValueError("Choose an ordered date range of at most 366 days")
        raw_limit = params.get("limit", "10")
        if not isinstance(raw_limit, str) or not re.fullmatch(r"[0-9]{1,2}", raw_limit):
            raise ValueError("limit must be an integer between 1 and 10")
        self.limit = int(raw_limit)
        if not 1 <= self.limit <= 10:
            raise ValueError("limit must be between 1 and 10")
        zone = ZoneInfo(TIMEZONE)
        lower = datetime.combine(self.start, time.min, zone).astimezone(timezone.utc)
        upper = datetime.combine(self.end + timedelta(days=1), time.min, zone).astimezone(timezone.utc) - timedelta(seconds=1)
        # Omit fractions at boundaries to include all existing millisecond/microsecond keys.
        self.lower = "EVENT#" + lower.strftime("%Y-%m-%dT%H:%M:%S")
        self.upper = "EVENT#" + upper.strftime("%Y-%m-%dT%H:%M:%S") + "~"
        self.key = self._decode(params["cursor"]) if params.get("cursor") else None

    @staticmethod
    def _date(value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("Dates must use YYYY-MM-DD")
        result = date.fromisoformat(value)
        if not 1970 <= result.year <= 9998:
            raise ValueError("Date outside supported range")
        return result

    def filters(self):
        return {"from": self.start.isoformat(), "to": self.end.isoformat(), "limit": self.limit, "timezone": TIMEZONE}

    def cursor(self, key):
        if not key:
            return None
        data = {"filters": self.filters(), "key": key}
        return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode().rstrip("=")

    def _decode(self, value):
        try:
            if not isinstance(value, str) or len(value) > 4096 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
                raise ValueError()
            decoded = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
            if decoded["filters"] != self.filters():
                raise ValueError()
            key = decoded["key"]
            if not isinstance(key, dict) or set(key) != {"PK", "SK", "record_type"}:
                raise ValueError()
            if any(not isinstance(v, dict) or set(v) != {"S"} or not isinstance(v["S"], str) for v in key.values()):
                raise ValueError()
            if key["record_type"]["S"] != "event" or not key["PK"]["S"].startswith("SYSTEM#"):
                raise ValueError()
            if not self.lower <= key["SK"]["S"] <= self.upper:
                raise ValueError()
            return key
        except (ValueError, KeyError, TypeError, binascii.Error, UnicodeDecodeError) as error:
            raise ValueError("Invalid cursor or cursor does not match the date range and limit") from error
