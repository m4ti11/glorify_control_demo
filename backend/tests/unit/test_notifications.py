from decimal import Decimal
import unittest

from lambda_loader import load_lambda

render = load_lambda("process_events")["notification"].render_notification


class NotificationTests(unittest.TestCase):
    def event(self, **changes):
        item = {"event_id": "event-123", "system_id": "demo-vps-01", "event_type": "host.threshold_exceeded",
                "source": "host-alert-evaluator", "occurred_at": "2026-10-22T20:04:50Z",
                "subject": {"type": "host", "id": "host"}, "PK": "INTERNAL-PK", "SK": "INTERNAL-SK",
                "details": {"metric": "cpu_percent", "value": Decimal("90"), "threshold": Decimal("80"),
                            "recovery_threshold": Decimal("70"), "operator": ">=", "unit": "%",
                            "duration_seconds": Decimal("60"), "consecutive_samples": Decimal("2"),
                            "max_sample_gap_seconds": Decimal("600"), "rule_name": "Host CPU"}}
        return dict(item, **changes)

    def test_cpu_email_has_actionable_values_resource_time_and_reference(self):
        subject, body = render(self.event())
        self.assertEqual(subject, "[ALERT] demo-vps-01 | CPU: threshold exceeded")
        for expected in ("CPU: 90%", "Alert threshold: >= 80%", "Required duration: 1 min", "Minimum readings: 2",
                         "Maximum gap between readings: 10 min", "Host / host", "22/10/2026 17:04:50", "UTC-03",
                         "Host CPU", "host.threshold_exceeded", "event-123"):
            self.assertIn(expected, body)
        self.assertNotIn("INTERNAL-", body)

    def test_recovery_uses_recovery_limit_and_inverts_operator(self):
        subject, body = render(self.event(event_type="host.threshold_recovered"))
        self.assertIn("RECOVERY", subject)
        self.assertIn("Recovery threshold: <= 70%", body)
        item = self.event(event_type="host.threshold_recovered")
        item["details"]["operator"] = "<="
        self.assertIn("Recovery threshold: >= 70%", render(item)[1])

    def test_container_oom_names_error_and_container(self):
        subject, body = render(self.event(event_type="container.exited", source="docker",
            subject={"type": "container", "id": "postgres"},
            details={"exit_code": Decimal("137"), "oom_killed": True, "previous_state": "running", "current_state": "exited"}))
        self.assertIn("ALERT", subject)
        for expected in ("Container / postgres", "memory exhaustion (OOM)", "Exit code: 137", "Current state: exited"):
            self.assertIn(expected, body)

    def test_agent_recovery_and_nested_container_state(self):
        subject, body = render(self.event(event_type="container.changed", details={"abnormal": False,
            "previous": {"state": "exited"}, "current": {"state": "running", "restart_count": 2}}))
        self.assertIn("RECOVERY", subject)
        self.assertIn("State: running", body)
        self.assertIn("Restarts: 2", body)

    def test_unknown_event_and_missing_optional_fields_do_not_break_delivery(self):
        subject, body = render(self.event(event_type="custom.event", details={}, occurred_at="invalid"))
        self.assertIn("INFORMATION", subject)
        self.assertIn("custom.event", body)
        self.assertNotIn("Error:", body)

    def test_subject_rejects_control_characters_and_is_under_100_characters(self):
        subject, body = render(self.event(system_id="x\r\n\x1b" * 100))
        self.assertLess(len(subject), 100)
        self.assertFalse(any(ord(char) < 32 for char in subject))
        self.assertNotIn("\x1b", body)

    def test_large_nested_details_are_bounded_without_changing_the_event(self):
        details = {f"field_{i}": {f"nested_{j}": "á" * 10000 for j in range(30)} for i in range(30)}
        item = self.event(event_type="custom.event", details=details)
        subject, body = render(item)
        self.assertLess(len(body.encode("utf-8")), 64000)
        self.assertIn("shortened", body)
        self.assertIn("event-123", body)
        self.assertEqual(len(item["details"]["field_0"]["nested_0"]), 10000)
