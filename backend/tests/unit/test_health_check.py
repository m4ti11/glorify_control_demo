from datetime import datetime, timedelta, timezone
import copy
import unittest

from lambda_loader import load_lambda

health = load_lambda("health_check", components=["repository", "service", "handler"])
events = load_lambda("process_events", components=["models", "notification"])
HealthCheckService = health["service"].HealthCheckService
BASE = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
SYSTEM = "demo-vps-01"


class MemoryRepository:
    def __init__(self):
        self.snapshot = None
        self.state = None
        self.enqueued = []
        self.fail_next_enqueue = False

    def get_latest_snapshot(self):
        return copy.deepcopy(self.snapshot)

    def get_state(self, system_id):
        if self.state and self.state["system_id"] == system_id:
            return copy.deepcopy(self.state)
        return None

    def put_state(self, item, expected_version):
        if expected_version is None:
            if self.state is not None:
                raise RuntimeError("state already exists")
            version = 1
        else:
            if self.state is None or self.state["version"] != expected_version:
                raise RuntimeError("state version changed")
            version = expected_version + 1
        saved = copy.deepcopy(item)
        saved["version"] = version
        self.state = saved
        return copy.deepcopy(saved)

    def enqueue_event(self, event_body):
        if self.fail_next_enqueue:
            self.fail_next_enqueue = False
            raise RuntimeError("temporary SQS failure")
        self.enqueued.append(copy.deepcopy(event_body))

    def report(self, received_at):
        self.snapshot = {
            "system_id": SYSTEM,
            "received_at": received_at.isoformat().replace("+00:00", "Z"),
        }


class HealthCheckTests(unittest.TestCase):
    def setUp(self):
        self.repository = MemoryRepository()
        self.service = HealthCheckService(self.repository, stale_after_seconds=180, confirmation_seconds=120)

    def check(self, seconds):
        return self.service.check(SYSTEM, BASE + timedelta(seconds=seconds))

    def test_fresh_snapshot_initializes_healthy_without_event(self):
        self.repository.report(BASE - timedelta(seconds=30))

        result = self.check(0)

        self.assertEqual(result, {"status": "HEALTHY", "transition": "initialized"})
        self.assertEqual(self.repository.enqueued, [])

    def test_stale_snapshot_requires_suspect_then_confirmation_and_only_alerts_once(self):
        self.repository.report(BASE - timedelta(seconds=180))

        self.assertEqual(self.check(0)["transition"], "suspected")
        self.assertEqual(self.check(119)["transition"], "awaiting_confirmation")
        self.assertEqual(self.repository.enqueued, [])

        result = self.check(120)
        self.assertEqual(result, {"status": "UNREACHABLE", "transition": "unreachable"})
        self.assertEqual(len(self.repository.enqueued), 1)
        event = self.repository.enqueued[0]
        self.assertEqual(event["event_type"], "agent.unreachable")
        self.assertEqual(event["source"], "health-monitor")
        self.assertEqual(events["models"].EventPayload.model_validate(event).event_type, "agent.unreachable")
        self.assertEqual(self.check(240)["transition"], "none")
        self.assertEqual(len(self.repository.enqueued), 1)

    def test_recovery_emits_once_after_unreachable(self):
        self.repository.report(BASE - timedelta(seconds=180))
        self.check(0)
        self.check(120)
        unreachable_id = self.repository.enqueued[0]["event_id"]

        self.repository.report(BASE + timedelta(seconds=130))
        result = self.check(130)

        self.assertEqual(result, {"status": "HEALTHY", "transition": "recovered"})
        self.assertEqual([e["event_type"] for e in self.repository.enqueued], ["agent.unreachable", "agent.recovered"])
        for event in self.repository.enqueued:
            events["models"].EventPayload.model_validate(event)
        self.assertNotEqual(self.repository.enqueued[1]["event_id"], unreachable_id)
        self.assertEqual(self.check(140)["transition"], "none")
        self.assertEqual(len(self.repository.enqueued), 2)

    def test_fresh_metrics_clear_suspect_without_recovery_event(self):
        self.repository.report(BASE - timedelta(seconds=180))
        self.check(0)
        self.repository.report(BASE + timedelta(seconds=30))

        result = self.check(30)

        self.assertEqual(result, {"status": "HEALTHY", "transition": "suspect_cleared"})
        self.assertEqual(self.repository.enqueued, [])

    def test_failed_queue_send_retries_the_same_pending_event_id(self):
        self.repository.report(BASE - timedelta(seconds=180))
        self.check(0)
        self.repository.fail_next_enqueue = True

        with self.assertRaisesRegex(RuntimeError, "temporary SQS failure"):
            self.check(120)
        pending_id = self.repository.state["pending_event"]["event_id"]

        self.check(240)

        self.assertEqual(len(self.repository.enqueued), 1)
        self.assertEqual(self.repository.enqueued[0]["event_id"], pending_id)
        self.assertNotIn("pending_event", self.repository.state)

    def test_event_notifications_are_classified_as_alert_and_recovery(self):
        presentation = events["notification"].event_presentation
        common = {"system_id": SYSTEM, "occurred_at": BASE.isoformat(), "subject": {"type": "vps", "id": SYSTEM}}

        down = presentation({**common, "event_type": "agent.unreachable", "details": {}})
        recovered = presentation({**common, "event_type": "agent.recovered", "details": {}})

        self.assertEqual(down["status"], "ALERT")
        self.assertEqual(down["summary"], "No recent agent metrics were received.")
        self.assertEqual(recovered["status"], "RECOVERY")
        self.assertEqual(recovered["summary"], "The agent resumed sending recent metrics.")


if __name__ == "__main__":
    unittest.main()
