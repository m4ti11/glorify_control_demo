from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

spec = importlib.util.spec_from_file_location("app_logs", Path(__file__).resolve().parents[3] / "scripts/app_logs.py")
viewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(viewer)
PREFIX = "glorify-control-prod"
GROUP = "/aws/lambda/" + PREFIX + "-ingest-metrics"
AT = 1792698268000  # 2026-10-22 19:44:28 UTC


class AppLogsTests(unittest.TestCase):
    def event(self, message, uid="one", at=AT):
        return {"message": message, "eventId": uid, "timestamp": at}

    def test_terminal_columns_use_local_time_short_service_and_readable_message(self):
        event = self.event("[INFO]\t2026-10-22T19:44:28.137Z\trequest-id\tmetrics.persisted system_id=demo-vps-01 created=True cpu_percent=90.0")
        row = viewer.format_event(GROUP, event, PREFIX, ZoneInfo("America/Argentina/Buenos_Aires"))
        self.assertTrue(row.startswith("16:44:28 | ingest-metrics"), row)
        self.assertIn("| INFO", row)
        self.assertIn("Metrics persisted · CPU 90%", row)
        self.assertNotIn("request-id", row)

    def test_runtime_noise_is_hidden_but_runtime_errors_are_never_hidden(self):
        for message in ("START RequestId: x", "END RequestId: x", "REPORT RequestId: x Duration: 10", "INIT_START Runtime Version: python"):
            self.assertIsNone(viewer.format_event(GROUP, self.event(message), PREFIX, ZoneInfo("UTC")))
            self.assertIsNotNone(viewer.format_event(GROUP, self.event(message), PREFIX, ZoneInfo("UTC"), platform=True))
        error = "REPORT RequestId: x Duration: 100 Status: timeout Error Type: Runtime.ExitError"
        row = viewer.format_event(GROUP, self.event(error), PREFIX, ZoneInfo("UTC"))
        self.assertIn("ERROR", row)
        self.assertIn("Runtime.ExitError", row)

    def test_json_runtime_success_hidden_and_failure_preserved(self):
        normal = json.dumps({"type": "platform.report", "record": {"status": "success"}})
        failed = json.dumps({"type": "platform.report", "record": {"status": "error", "errorType": "Runtime.ExitError"}})
        self.assertTrue(viewer.decode_message(normal)[2])
        self.assertFalse(viewer.decode_message(failed)[2])
        self.assertEqual(viewer.decode_message(failed)[0], "ERROR")

    def test_api_gateway_has_a_clear_service_and_http_result(self):
        for status, expected in ((201, "INFO"), (403, "WARN"), (500, "ERROR")):
            raw = json.dumps({"routeKey": "POST /metrics", "status": status, "requestId": "api-request", "integrationErr": "-"})
            row = viewer.format_event("/aws/apigateway/" + PREFIX + "-metrics", self.event(raw), PREFIX, ZoneInfo("UTC"))
            self.assertIn("api-gateway", row)
            self.assertIn(expected, row)
            self.assertIn(f"POST /metrics → HTTP {status}", row)
            self.assertIn("api-request", row)

    def test_json_application_logs_and_unknown_exceptions_are_preserved(self):
        raw = json.dumps({"level": "ERROR", "message": "SNS AccessDeniedException", "requestId": "abc"})
        self.assertEqual(viewer.decode_message(raw), ("ERROR", "SNS AccessDeniedException", False))
        self.assertEqual(viewer.summarize("Traceback: an unexpected exception"), "Traceback: an unexpected exception")

    def test_waiting_and_cooldown_explain_why_no_notification_was_sent(self):
        waiting = "rule.window_checked metric=cpu_percent phase=alert decision=waiting reason=insufficient_samples samples=1 required_samples=2 covered_seconds=0 required_seconds=60"
        text = viewer.summarize(waiting)
        self.assertIn("CPU: waiting", text)
        self.assertIn("not enough readings", text)
        self.assertIn("1/2", text)
        cooldown = viewer.summarize("rule.no_event metric=cpu_percent reason=cooldown remaining_seconds=90")
        self.assertIn("cooldown, remaining 90 s", cooldown)

    def test_raw_mode_preserves_full_ids_and_multiline_errors_stay_readable(self):
        raw = "[INFO]\t2026-10-22T19:44:28Z\treq\tsqs.enqueued event_id=12345678-1234-1234-1234-123456789012 message_id=message"
        row = viewer.format_event(GROUP, self.event(raw), PREFIX, ZoneInfo("UTC"), raw=True)
        self.assertIn("12345678-1234-1234-1234-123456789012", row)
        multiline = viewer.format_event(GROUP, self.event("Traceback\nValueError: failed"), PREFIX, ZoneInfo("UTC"))
        self.assertEqual(multiline.count("| ERROR"), 2)

    def test_historical_decision_logs_get_metric_name_from_previous_rule_log(self):
        context = {}
        viewer.format_event(GROUP, self.event("rule.evaluating rule_id=abc metric=cpu_percent value=90 threshold=80"),
                            PREFIX, ZoneInfo("UTC"), rule_metrics=context)
        row = viewer.format_event(GROUP, self.event("rule.window_checked rule_id=abc decision=waiting reason=insufficient_samples samples=1 required_samples=2"),
                                  PREFIX, ZoneInfo("UTC"), rule_metrics=context)
        self.assertIn("CPU: waiting", row)

    def test_overlapping_polls_deduplicate_by_group_and_event_id_not_timestamp(self):
        seen = {}
        a, b = self.event("first", "a"), self.event("second", "b")
        self.assertEqual(len(viewer.fresh_events(GROUP, [a, b], seen)), 2)
        self.assertEqual(viewer.fresh_events(GROUP, [a, b], seen), [])
        self.assertEqual(len(viewer.fresh_events("other-group", [a], seen)), 1)

    def test_once_merges_api_and_lambda_logs_chronologically(self):
        gateway = "/aws/apigateway/" + PREFIX + "-metrics"
        events = {GROUP: [self.event("metrics.persisted created=True cpu_percent=90", at=AT + 1000)],
                  gateway: [self.event(json.dumps({"routeKey": "POST /metrics", "status": 201}), at=AT)]}

        class Api:
            def __init__(self, *args): pass
            def groups(self, prefix): return list(events)
            def events(self, group, start, end): return events[group]

        output = io.StringIO()
        with patch.object(viewer, "AwsLogs", Api), patch("sys.argv", ["app_logs.py", "--once"]), redirect_stdout(output):
            self.assertEqual(viewer.main(), 0)
        text = output.getvalue()
        self.assertLess(text.index("api-gateway"), text.index("ingest-metrics"))

    def test_group_failure_is_reported_and_does_not_hide_other_services(self):
        class Api:
            def __init__(self, *args): pass
            def groups(self, prefix): return [GROUP, "/aws/apigateway/" + PREFIX + "-metrics"]
            def events(self, group, start, end):
                if group == GROUP: raise RuntimeError("AccessDenied")
                return [{"eventId": "ok", "timestamp": AT, "message": "API request received"}]

        output, errors = io.StringIO(), io.StringIO()
        with patch.object(viewer, "AwsLogs", Api), patch("sys.argv", ["app_logs.py", "--once"]), redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(viewer.main(), 1)
        self.assertIn("API request received", output.getvalue())
        self.assertIn("ingest-metrics: AccessDenied", errors.getvalue())

    def test_live_terminal_inserts_delayed_gateway_log_before_existing_lambda_log(self):
        gateway = "/aws/apigateway/" + PREFIX + "-metrics"
        calls = {}

        class Api:
            def __init__(self, *args): pass
            def groups(self, prefix): return [GROUP, gateway]
            def events(inner, group, start, end):
                calls[group] = calls.get(group, 0) + 1
                if group == GROUP:
                    return [self.event("metrics.persisted created=True cpu_percent=90", at=AT + 5000)]
                return [] if calls[group] == 1 else [self.event(json.dumps({"routeKey": "POST /metrics", "status": 201}), at=AT)]

        class Terminal(io.StringIO):
            def isatty(self): return True

        output = Terminal()
        with patch.object(viewer, "AwsLogs", Api), patch("sys.argv", ["app_logs.py"]), redirect_stdout(output), \
                patch.object(viewer.time, "time", return_value=(AT + 10000) / 1000), \
                patch.object(viewer.time, "sleep", side_effect=[None, KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt):
                viewer.main()
        snapshot = output.getvalue().split("\x1b[2J\x1b[H")[-1]
        self.assertLess(snapshot.index("api-gateway"), snapshot.index("ingest-metrics"))
        self.assertEqual(snapshot.count("Metrics persisted"), 1)
