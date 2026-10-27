from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from lambda_loader import load_lambda

modules = load_lambda("process_events")
notification, repository, service = (modules[name] for name in ("notification", "repository", "service"))
PAYLOAD = json.loads((Path(__file__).resolve().parents[2] / "lambda/process_events/test/payload.json").read_text())


def threshold(**changes):
    return dict(PAYLOAD, event_type="host.threshold_exceeded", source="host-alert-evaluator",
                details={"metric": "cpu_percent", "value": Decimal("92.5"), "threshold": 80,
                         "recovery_threshold": 70, "operator": ">=", "unit": "%",
                         "duration_seconds": 60, "consecutive_samples": 3, "rule_name": "Host CPU"}, **changes)


def test_html_has_rule_context_and_distinct_alert_recovery_and_information():
    event = threshold()
    html = notification.render_html_notification(event)
    for text in ("#b42318", "ALERT", "CPU: threshold exceeded", "92.5", "Host CPU", "&gt;= 80%", "1 min", "Minimum readings", PAYLOAD["event_id"]):
        assert text in html
    event["event_type"] = "host.threshold_recovered"
    html = notification.render_html_notification(event)
    assert "#067647" in html and "RECOVERY" in html and "&lt;= 70%" in html
    event["event_type"], event["details"] = "custom.event", {}
    assert "#175cd3" in notification.render_html_notification(event)


def test_html_escapes_untrusted_event_data_and_bounds_large_nested_details():
    event = dict(PAYLOAD, event_type="custom.event", system_id='<img src=x onerror="x">',
                 details={str(i): {"danger": "<script>alert(1)</script>" * 1000} for i in range(30)})
    html = notification.render_html_notification(event)
    assert "<script>" not in html and "<img" not in html
    assert "&lt;script&gt;" in html and "&lt;img" in html
    assert len(html.encode()) < 100000
    assert PAYLOAD["event_id"] in html


def test_network_threshold_uses_bytes_per_second_instead_of_percent():
    event = threshold()
    event["details"].update(metric="network_rx_bytes_per_second", unit="B/s", threshold=1000)
    assert "&gt;= 1000B/s" in notification.render_html_notification(event)
    assert "Alert threshold: >= 1000B/s" in notification.render_notification(event)[1]


def test_ses_uses_only_confirmed_configured_email_recipients_with_pagination():
    sns, ses = Mock(), Mock()
    sns.list_subscriptions_by_topic.side_effect = [
        {"Subscriptions": [{"Protocol": "email", "Endpoint": "yes@example.com", "SubscriptionArn": "arn:confirmed"},
                           {"Protocol": "email", "Endpoint": "pending@example.com", "SubscriptionArn": "PendingConfirmation"}], "NextToken": "next"},
        {"Subscriptions": [{"Protocol": "email", "Endpoint": "outside@example.com", "SubscriptionArn": "arn:outside"},
                           {"Protocol": "https", "Endpoint": "https://example.com", "SubscriptionArn": "arn:https"}]}]
    email = repository.EventEmailRepository("sender@example.com", "arn:channel-", ses, sns)
    assert email.recipients({"topic_arn": "arn:channel-one", "emails": ["yes@example.com", "pending@example.com"]}) == ["yes@example.com"]
    with pytest.raises(RuntimeError):
        email.recipients({"topic_arn": "arn:other", "emails": ["yes@example.com"]})
    ses.send_email.return_value = {"MessageId": "ses-id"}
    assert email.send(threshold(), "yes@example.com") == "ses-id"
    request = ses.send_email.call_args.kwargs
    assert request["Destination"] == {"ToAddresses": ["yes@example.com"]}
    assert "Html" in request["Message"]["Body"] and "Text" in request["Message"]["Body"]
    assert request["Message"]["Subject"]["Data"].startswith("[ALERT]")


def test_partial_ses_failure_retries_only_the_recipient_without_checkpoint():
    repo, alerts, config, email = Mock(), Mock(), Mock(), Mock()
    target = {"id": "one", "topic_arn": "arn:channel-one", "emails": ["a@example.com", "b@example.com"]}
    control = {"notification_targets": [target], "publications": {}}
    repo.get_control.return_value = control
    repo.event.return_value = threshold()
    repo.record_publication.side_effect = lambda event_id, owner, channel, value: control["publications"].update({channel: deepcopy(value)})
    email.recipients.return_value = target["emails"]
    email.send.side_effect = ["ses-a", RuntimeError("transient failure")]
    worker = service.EventsService(repo, alerts, config, email=email)
    with pytest.raises(RuntimeError):
        worker.publish_channels(PAYLOAD["event_id"], "owner")
    assert control["publications"]["one"]["status"] == "PARTIAL"
    email.send.reset_mock(side_effect=True)
    email.send.return_value = "ses-b"
    worker.publish_channels(PAYLOAD["event_id"], "owner")
    email.send.assert_called_once_with(repo.event.return_value, "b@example.com")
    alerts.publish.assert_not_called()
    assert control["publications"]["one"]["emails"] == target["emails"]
    assert control["publications"]["one"]["status"] == "PUBLISHED"


def test_no_confirmed_email_does_not_fall_back_to_sns_or_send_ses():
    repo, alerts, config, email = Mock(), Mock(), Mock(), Mock()
    repo.get_control.return_value = {"notification_targets": [{"id": "one", "topic_arn": "topic", "emails": ["pending@example.com"]}]}
    repo.event.return_value = threshold()
    email.recipients.return_value = []
    service.EventsService(repo, alerts, config, email=email).publish_channels(PAYLOAD["event_id"], "owner")
    email.send.assert_not_called()
    alerts.publish.assert_not_called()
    assert repo.record_publication.call_args.args[3]["status"] == "SKIPPED_NO_CONFIRMED_RECIPIENTS"


def test_push_checkpoint_survives_email_failure_and_respects_rule_pause():
    repo, alerts, config, push = Mock(), Mock(), Mock(), Mock()
    control = {"notification_status": "PENDING", "publications": {}}
    repo.ensure_event.return_value = control
    repo.get_control.return_value = control
    repo.event.return_value = dict(PAYLOAD)
    repo.record_push.side_effect = lambda *args: control.update(push_status="PUBLISHED")
    config.targets.return_value = [{"id": "one", "topic_arn": "topic", "emails": ["a@example.com"]}]
    push.publish.return_value = "push-id"
    alerts.publish.side_effect = [RuntimeError("mail transport failed"), "sns-email-id"]
    worker = service.EventsService(repo, alerts, config, push=push)
    with pytest.raises(RuntimeError):
        worker.process(json.dumps(PAYLOAD), "1792268133001")
    worker.process(json.dumps(PAYLOAD), "1792268133001")
    assert push.publish.call_count == 1 and repo.record_push.call_count == 1
    assert alerts.publish.call_count == 2
    control.pop("push_status")
    config.rule_active.return_value = False
    push.reset_mock()
    # A muted host rule cannot fan out to the mobile topic either.
    payload = threshold()
    payload["details"] = {"rule_id": "paused", "rule_version": 1}
    repo.event.return_value = payload
    config.targets.return_value = []
    worker.process(json.dumps(PAYLOAD), "1792268133001")
    push.publish.assert_not_called()


def test_push_payload_is_short_and_does_not_include_full_event_details():
    client = Mock()
    client.publish.return_value = {"MessageId": "push-id"}
    event = threshold()
    event["details"]["private_blob"] = "DO-NOT-FORWARD"
    assert repository.PushAlertsRepository("push-topic", client).publish(event) == "push-id"
    request = client.publish.call_args.kwargs
    assert "DO-NOT-FORWARD" not in request["Message"]
    assert len(request["Message"].encode()) < 4096
    assert request["MessageAttributes"]["severity"]["StringValue"] == "ALERT"
