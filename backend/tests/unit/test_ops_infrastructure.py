"""Offline checks of operational alarm wiring; never load tfvars or state."""
from pathlib import Path
import hcl2

ROOT = Path(__file__).resolve().parents[3]
TF = ROOT / "infrastructure/environments/prod"


def resources():
    result = {}
    for name in ("cloudwatch.tf", "health_monitor.tf", "host_alerts.tf"):
        with (TF / name).open() as source:
            for block in hcl2.load(source).get("resource", []):
                for kind, entries in block.items():
                    result.setdefault(kind, {}).update(entries)
    return result


def test_every_pipeline_alarm_has_alarm_and_recovery_notifications():
    alarms = resources()["aws_cloudwatch_metric_alarm"]
    assert len(alarms) >= 15
    for name, alarm in alarms.items():
        assert alarm.get("alarm_actions") == ["${aws_sns_topic.ops_alerts.arn}"], name
        assert alarm.get("ok_actions") == ["${aws_sns_topic.ops_alerts.arn}"], name


def test_heartbeat_alarm_is_independent_and_fails_closed_when_logs_stop():
    r = resources()
    alarm = r["aws_cloudwatch_metric_alarm"]["heartbeats_missing"]
    assert alarm["treat_missing_data"] == "breaching"
    assert alarm["comparison_operator"] == "LessThanThreshold"
    assert alarm["threshold"] == 1 and alarm["period"] * alarm["evaluation_periods"] == 600
    metric = r["aws_cloudwatch_log_metric_filter"]["heartbeats"]["metric_transformation"][0]
    assert metric["name"] == alarm["metric_name"]
    assert metric["namespace"] == alarm["namespace"]
    assert "${aws_lambda_function.health_check" not in str(alarm)
    assert "ingest_metrics" in r["aws_cloudwatch_log_metric_filter"]["heartbeats"]["log_group_name"]


def test_ops_address_is_required_and_only_used_for_owned_email_subscription():
    with (TF / "variables.tf").open() as source:
        variables = {key: value for block in hcl2.load(source)["variable"] for key, value in block.items()}
    assert "default" not in variables["ops_alert_email"]
    sub = resources()["aws_sns_topic_subscription"]["ops_alerts_email"]
    assert sub["protocol"] == "email" and sub["endpoint"] == "${var.ops_alert_email}"
