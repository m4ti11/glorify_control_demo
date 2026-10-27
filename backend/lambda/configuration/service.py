from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4, uuid5, NAMESPACE_URL

from models import KINDS, settings
from repository import Conflict, NotFound


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def public(item):
    return {key: value for key, value in item.items() if key not in {"PK", "SK"}}


class ConfigurationService:
    def __init__(self, repository, subscriptions):
        self.repository = repository
        self.subscriptions = subscriptions

    def list(self, kind, page):
        items, key = self.repository.page(kind, page.limit, page.key)
        return {"items": [public(item) for item in items], "next_cursor": page.cursor(key),
                "notifications_initialized": bool(self.repository.state().get("notifications_initialized"))}

    def get(self, kind, uid):
        item = self.repository.get(kind, uid)
        if not item:
            raise NotFound()
        return public(item)

    def references(self, data):
        for uid in data.get("notification_channel_ids", []):
            if not self.repository.get("notification-channels", uid):
                raise ValueError("A referenced notification channel does not exist")

    def save(self, kind, uid, data, actor, version=None):
        data = settings(kind, data)
        state = self.repository.state()
        current = self.repository.get(kind, uid)
        if version is None and current:
            if self.repository.encode(current["data"]) == self.repository.encode(data):
                return False, public(current)
            raise Conflict("ID already exists with other settings")
        if version is not None and (not current or current["version"] != version):
            raise Conflict("This item changed; reload it before saving")
        if kind == "alert-rules" and current:
            identity = ("system_id", "resource_type", "resource_id", "metric")
            if any(data[key] != current["data"][key] for key in identity):
                raise Conflict("Alert rule identity is permanent; edit its parameters only")
        if kind == "notification-channels":
            if not state.get("notifications_initialized"):
                raise Conflict("Import existing recipients before editing notification channels")
            other = self.repository.all(kind)
            wanted = set(data["emails"])
            if any(item["id"] != uid and wanted.intersection(item["data"]["emails"]) for item in other):
                raise ValueError("An email already belongs to another channel")
        self.references(data)
        now = stamp()
        item = {"PK": "CONFIG#DEFAULT", "SK": f"{KINDS[kind]}#{uid}", "id": uid, "kind": kind,
                "schema_version": 1, "version": 1 if current is None else int(current["version"]) + 1,
                "data": data, "created_at": current["created_at"] if current else now,
                "created_by": current["created_by"] if current else actor, "updated_at": now, "updated_by": actor}
        if kind == "notification-channels":
            item["delivery"] = {**(current or {}).get("delivery", {}), "status": "PENDING_SYNC"}
        self.repository.mutate(kind, uid, item, version, state)
        return current is None, public(item)

    def delete(self, kind, uid, version):
        if kind == "alert-rules":
            raise Conflict("Alert rules cannot be deleted; edit their parameters instead")
        state = self.repository.state()
        current = self.repository.get(kind, uid)
        if not current:
            raise NotFound()
        if current["version"] != version:
            raise Conflict("This item changed; reload it before deleting")
        if kind == "notification-channels":
            for group in ("health-checks", "alert-rules"):
                if any(uid in item["data"].get("notification_channel_ids", []) for item in self.repository.all(group)):
                    raise Conflict("Remove references to this channel before deleting it")
        self.repository.mutate(kind, uid, None, version, state)

    def import_notifications(self, actor):
        state = self.repository.state()
        if state.get("notifications_initialized"):
            raise Conflict("Recipients were already imported; existing configuration is preserved")
        emails = self.subscriptions.existing_emails()
        records = []
        if emails:
            uid = str(uuid5(NAMESPACE_URL, "glorify-control:legacy-alerts"))
            data = settings("notification-channels", {"name": "Notifications existentes", "emails": emails, "enabled": True})
            now = stamp()
            records.append({"PK": "CONFIG#DEFAULT", "SK": "NOTIFICATION_CHANNEL#" + uid,
                            "id": uid, "kind": "notification-channels", "schema_version": 1, "version": 1,
                            "data": data, "created_at": now, "updated_at": now,
                            "created_by": actor, "updated_by": actor, "delivery": {"status": "PENDING_SYNC"}})
        self.repository.mutate("notification-channels", None, None, None, state, importing=records)
        return {"imported": len(records), "emails": emails, "notifications_initialized": True}

    def delivery_status(self, uid):
        item = self.repository.get("notification-channels", uid)
        if not item:
            raise NotFound()
        topic = item.get("delivery", {}).get("topic_arn")
        if not item["data"]["enabled"]:
            return {"status": "DISABLED", "recipients": []}
        if not topic or item.get("delivery", {}).get("synced_version") != item["version"]:
            return {"status": "PENDING_SYNC", "recipients": []}
        return {"status": "SYNCED", "recipients": self.subscriptions.status(topic)}

    def resync(self, uid, actor, version):
        current = self.repository.get("notification-channels", uid)
        if not current:
            raise NotFound()
        return self.save("notification-channels", uid, current["data"], actor, version)[1]
