from __future__ import annotations

import hashlib
import json


def topic_name(prefix, item):
    emails = sorted(item["data"]["emails"])
    digest = hashlib.sha256(json.dumps(emails, separators=(",", ":")).encode()).hexdigest()[:16]
    return f"{prefix}-{item['id']}-{digest}"


def relevant(record):
    data = record["dynamodb"]
    new, old = data.get("NewImage", {}), data.get("OldImage", {})
    source = new or old
    if not source.get("SK", {}).get("S", "").startswith("NOTIFICATION_CHANNEL#"):
        return False
    return record["eventName"] in ("INSERT", "REMOVE") or new.get("version") != old.get("version")
