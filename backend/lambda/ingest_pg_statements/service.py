from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
from zoneinfo import ZoneInfo

from models import MAX_REPORT_BYTES, PgStatementsPayload


class PgStatementsService:
    def __init__(self, repository):
        self.repository = repository

    def ingest(self, raw, now=None):
        if len(raw.encode("utf-8")) > MAX_REPORT_BYTES:
            raise OverflowError("Report too large")
        payload = PgStatementsPayload.model_validate_json(raw)
        now = now or datetime.now(timezone.utc)
        report_date = payload.details.report_date
        if report_date > now.astimezone(ZoneInfo(payload.details.timezone)).date():
            raise ValueError("Future report_date is not allowed")
        record = payload.model_dump(mode="json", exclude_none=True, exclude={"event_id"})
        record["event_id"] = str(payload.event_id)
        content = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(content) > MAX_REPORT_BYTES:
            raise OverflowError("Normalized report too large")
        key = f"raw/pg-statements/{payload.system_id}/{report_date:%Y/%m/%d}.json.gz"
        received = now.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        metadata = {"system-id": payload.system_id, "report-date": report_date.isoformat(),
                    "received-at": received, "event-id": str(payload.event_id),
                    "payload-sha256": hashlib.sha256(content).hexdigest()}
        created = self.repository.create(key, gzip.compress(content, mtime=0), metadata)
        return created, {"system_id": payload.system_id, "report_date": report_date.isoformat(), "created": created}
