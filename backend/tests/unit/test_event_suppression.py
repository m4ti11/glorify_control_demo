from copy import deepcopy
import json
from unittest.mock import Mock

import boto3
from moto import mock_aws
import pytest

from lambda_loader import load_lambda


modules = load_lambda("process_events")
repository, service = modules["repository"], modules["service"]
RUNNING = {"state": "running", "health": "healthy", "restart_count": 0, "started_at": "2026-10-17T01:24:57Z"}


def event(previous=None, current=None, **details):
    return {"schema_version": 1, "event_id": "cf144d7b-97c6-4df2-8d20-d03a36efe5d1", "system_id": "demo-vps-01",
            "event_type": "container.changed", "source": "docker", "occurred_at": "2026-10-24T20:00:00Z",
            "subject": {"type": "container", "id": "web"},
            "details": {"previous": previous, "current": current, "abnormal": True, **details}}


@pytest.mark.parametrize("previous,current,extra", [
    (RUNNING, dict(RUNNING, health="unhealthy"), {}),
    (dict(RUNNING, health="unhealthy"), dict(RUNNING, health="unhealthy"), {"reminder": True}),
    (dict(RUNNING, health="unhealthy"), RUNNING, {"abnormal": False}),
    (None, dict(RUNNING, health="unhealthy"), {}),
    (RUNNING, dict(RUNNING, health="starting"), {}),
])
def test_health_only_events_are_suppressed(previous, current, extra):
    assert service.suppression_reason(event(previous, current, **extra)) == "container_health_only"


@pytest.mark.parametrize("previous,current,extra", [
    (RUNNING, dict(RUNNING, state="exited"), {}),
    (RUNNING, {"state": "missing"}, {}),
    (dict(RUNNING, state="exited"), RUNNING, {}),
    (RUNNING, dict(RUNNING, health="unhealthy", restart_count=1), {}),
    (RUNNING, dict(RUNNING, health="unhealthy", started_at="2026-10-24T19:00:00Z"), {}),
    (RUNNING, dict(RUNNING, health="unhealthy"), {"oom_killed": True}),
    (RUNNING, dict(RUNNING, health="unhealthy"), {"exit_code": 137}),
    (RUNNING, dict(RUNNING, health="unhealthy"), {"error": "connection failed"}),
    (None, None, {}),
])
def test_lifecycle_changes_and_failures_are_not_suppressed(previous, current, extra):
    assert service.suppression_reason(event(previous, current, **extra)) is None


def test_other_event_types_are_not_affected():
    item = event(RUNNING, dict(RUNNING, health="unhealthy"))
    for kind in ("container.exited", "host.threshold_exceeded", "host.threshold_recovered", "postgres.availability"):
        assert service.suppression_reason(dict(item, event_type=kind)) is None


@mock_aws
def test_suppression_preserves_event_history_and_skips_all_transports_and_retries():
    client = boto3.client("dynamodb", region_name="us-east-1")
    client.create_table(TableName="events", BillingMode="PAY_PER_REQUEST",
                        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
                        AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}, {"AttributeName": "SK", "AttributeType": "S"}])
    repo = repository.EventsRepository("events", client)
    alerts, email, push, configuration = Mock(), Mock(), Mock(), Mock()
    item = event(RUNNING, dict(RUNNING, health="unhealthy"), reminder=True)
    original = deepcopy(item)
    worker = service.EventsService(repo, alerts, configuration, email=email, push=push)
    worker.process(json.dumps(item), "1792872000000")
    control = repo.get_control(item["event_id"])
    assert control["notification_status"] == "SKIPPED"
    assert control["email_status"] == "SUPPRESSED" and control["push_status"] == "SUPPRESSED"
    assert control["notification_suppression_reason"] == "container_health_only"
    assert "lease_owner" not in control
    stored = repo.event(control)
    assert stored["details"] == original["details"]
    worker.process(json.dumps(item), "1792872000000")
    assert client.scan(TableName="events")["Count"] == 2
    alerts.publish.assert_not_called()
    email.send.assert_not_called()
    push.publish.assert_not_called()
    configuration.targets.assert_not_called()


def test_suppression_retains_already_accepted_partial_email_history():
    client = Mock()
    repo = repository.EventsRepository("events", client)
    receipt = {"email": "recipient@example.com", "ses_message_id": "ses-id", "accepted_at": "2026-10-24T20:00:00Z"}
    client.get_item.return_value = {"Item": repo.serialize({"publications": {"channel": {
        "status": "PARTIAL", "transport": "SES", "emails": [receipt["email"]], "recipients": {"hash": receipt}}}})}
    repo.suppress("event", "owner", "container_health_only")
    request = client.update_item.call_args.kwargs
    values = request["ExpressionAttributeValues"]
    assert values[":status"] == {"S": "PUBLISHED"}
    assert values[":email"] == {"S": "ACCEPTED"}
    assert values[":recipient"] == {"S": receipt["email"]}
    assert "lease_owner = :owner" in request["ConditionExpression"]
    assert "if_not_exists(push_status" in request["UpdateExpression"]
