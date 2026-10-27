from __future__ import annotations

from datetime import datetime, timezone
import uuid


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


class HealthCheckService:
    def __init__(self, repository, stale_after_seconds=180, confirmation_seconds=120):
        if stale_after_seconds < 120 or confirmation_seconds < 120:
            raise ValueError("Health thresholds must be at least one 120-second interval")
        self.repository = repository
        self.stale_after_seconds = stale_after_seconds
        self.confirmation_seconds = confirmation_seconds

    def check(self, system_id, now):
        now = now.astimezone(timezone.utc)
        snapshot = self.repository.get_latest_snapshot()
        received_at = None
        if snapshot and snapshot.get("system_id") == system_id:
            received_at = _parse_timestamp(snapshot.get("received_at"))

        age_seconds = None
        if received_at is not None:
            age_seconds = max(0, int((now - received_at).total_seconds()))
        fresh = age_seconds is not None and age_seconds < self.stale_after_seconds

        state = self.repository.get_state(system_id)
        if state and state.get("pending_event"):
            self._deliver_pending(state)
            state = self.repository.get_state(system_id)

        if fresh:
            return self._handle_fresh(system_id, state, received_at, now, age_seconds)
        return self._handle_stale(system_id, state, received_at, now, age_seconds)

    def _handle_fresh(self, system_id, state, received_at, now, age_seconds):
        if state is None:
            state = self._state(system_id, "HEALTHY", version=0, last_received_at=_timestamp(received_at))
            saved = self.repository.put_state(state, expected_version=None)
            return {"status": saved["status"], "transition": "initialized"}

        status = state.get("status", "HEALTHY")
        if status == "UNREACHABLE":
            event = self._event(
                system_id,
                "agent.recovered",
                now,
                {
                    "last_received_at": _timestamp(received_at),
                    "outage_started_at": state.get("suspect_since"),
                    "observed_age_seconds": age_seconds,
                    "stale_after_seconds": self.stale_after_seconds,
                },
            )
            next_state = self._state(
                system_id,
                "HEALTHY",
                version=state.get("version", 0),
                last_received_at=_timestamp(received_at),
                pending_event=event,
            )
            saved = self.repository.put_state(next_state, state.get("version"))
            self._deliver_pending(saved)
            return {"status": saved["status"], "transition": "recovered"}

        if status == "SUSPECT":
            next_state = self._state(
                system_id,
                "HEALTHY",
                version=state.get("version", 0),
                last_received_at=_timestamp(received_at),
            )
            saved = self.repository.put_state(next_state, state.get("version"))
            return {"status": saved["status"], "transition": "suspect_cleared"}

        return {"status": status, "transition": "none"}

    def _handle_stale(self, system_id, state, received_at, now, age_seconds):
        if state is None:
            state = self._state(
                system_id,
                "SUSPECT",
                version=0,
                suspect_since=_timestamp(now),
                last_received_at=_timestamp(received_at) if received_at else None,
            )
            saved = self.repository.put_state(state, expected_version=None)
            return {"status": saved["status"], "transition": "suspected"}

        status = state.get("status", "HEALTHY")
        if status == "UNREACHABLE":
            return {"status": status, "transition": "none"}

        if status != "SUSPECT":
            next_state = self._state(
                system_id,
                "SUSPECT",
                version=state.get("version", 0),
                suspect_since=_timestamp(now),
                last_received_at=_timestamp(received_at) if received_at else None,
            )
            saved = self.repository.put_state(next_state, state.get("version"))
            return {"status": saved["status"], "transition": "suspected"}

        suspect_since = _parse_timestamp(state.get("suspect_since"))
        if suspect_since is None or (now - suspect_since).total_seconds() < self.confirmation_seconds:
            return {"status": status, "transition": "awaiting_confirmation"}

        event = self._event(
            system_id,
            "agent.unreachable",
            now,
            {
                "last_received_at": _timestamp(received_at) if received_at else None,
                "outage_started_at": _timestamp(suspect_since),
                "observed_age_seconds": age_seconds,
                "stale_after_seconds": self.stale_after_seconds,
                "confirmation_seconds": self.confirmation_seconds,
            },
        )
        next_state = self._state(
            system_id,
            "UNREACHABLE",
            version=state.get("version", 0),
            suspect_since=state.get("suspect_since"),
            last_received_at=_timestamp(received_at) if received_at else None,
            pending_event=event,
        )
        saved = self.repository.put_state(next_state, state.get("version"))
        self._deliver_pending(saved)
        return {"status": saved["status"], "transition": "unreachable"}

    @staticmethod
    def _state(system_id, status, version, **values):
        item = {"system_id": system_id, "status": status, "version": version}
        item.update({key: value for key, value in values.items() if value is not None})
        return item

    @staticmethod
    def _event(system_id, event_type, now, details):
        return {
            "record_type": "event",
            "schema_version": 1,
            "event_id": str(uuid.uuid4()),
            "system_id": system_id,
            "event_type": event_type,
            "source": "health-monitor",
            "occurred_at": _timestamp(now),
            "subject": {"type": "vps", "id": system_id},
            "details": details,
        }

    def _deliver_pending(self, state):
        pending = state["pending_event"]
        self.repository.enqueue_event(pending)
        cleaned = dict(state)
        cleaned.pop("pending_event", None)
        self.repository.put_state(cleaned, state.get("version"))
