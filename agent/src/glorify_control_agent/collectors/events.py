import time
from datetime import datetime, timezone

from glorify_control_agent.models import EventPayload


def event(settings, kind, source, subject_type, subject_id, details):
    return EventPayload(
        system_id=settings.system_id,
        event_type=kind,
        source=source,
        occurred_at=datetime.now(timezone.utc),
        subject={"type": subject_type, "id": subject_id},
        details=details,
    )


class EventDetector:
    def __init__(self, settings, spool):
        self.settings, self.spool = settings, spool

    def observe(
        self, key, value, abnormal, source, subject_type, subject_id, kind="state.changed", details=None
    ):
        previous = self.spool.get_state(key)
        now = time.time()
        changed = previous is not None and previous["value"] != value
        remind = (
            previous is not None and abnormal and now - previous["notified"] >= self.settings.reminder_seconds
        )
        should_send = changed or remind or (previous is None and abnormal)
        state = {"value": value, "notified": now if should_send else (previous or {}).get("notified", now)}
        if should_send:
            payload = event(
                self.settings,
                kind,
                source,
                subject_type,
                subject_id,
                {
                    "previous": previous["value"] if previous else None,
                    "current": value,
                    "abnormal": abnormal,
                    "reminder": bool(remind and not changed),
                    **(details or {}),
                },
            )
            self.spool.enqueue("events", payload.model_dump_json(), state=(key, state))
        else:
            self.spool.set_state(key, state)

    def docker(self, metrics, complete):
        available = metrics["available"]
        self.observe("docker", available, not available, "docker", "daemon", "docker", "docker.availability")
        if not available:
            return
        names = {c["name"] for c in metrics["containers"]}
        known = set(self.spool.get_state("container_names") or [])
        if complete:
            for name in (known | set(self.settings.expected_containers)) - names:
                self.observe(
                    "container:" + name,
                    {"state": "missing"},
                    True,
                    "docker",
                    "container",
                    name,
                    "container.changed",
                )
        for container in metrics["containers"]:
            value = {key: container[key] for key in ("state", "health", "restart_count", "started_at")}
            abnormal = value["state"] != "running" or value["health"] == "unhealthy"
            self.observe(
                "container:" + container["name"],
                value,
                abnormal,
                "docker",
                "container",
                container["name"],
                "container.changed",
            )
        if complete:
            self.spool.set_state("container_names", sorted(names | set(self.settings.expected_containers)))

    def postgres(self, available):
        if available is not None:
            self.observe(
                "postgres",
                available,
                not available,
                "postgres",
                "database",
                "cluster",
                "postgres.availability",
            )
