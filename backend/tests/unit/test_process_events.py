from decimal import Decimal
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError
from pydantic import ValidationError

from lambda_loader import load_lambda

modules = load_lambda("process_events")
models, repository, service, handler = [modules[name] for name in ("models", "repository", "service", "handler")]
PAYLOAD = json.loads((Path(__file__).resolve().parents[2] / "lambda/process_events/test/payload.json").read_text())
EVENT_ID = PAYLOAD["event_id"]
SENT = "1792268133001"


def error(code):
    return ClientError({"Error": {"Code": code, "Message": "private data"}}, "Test")


class EventsServiceTests(unittest.TestCase):
    def setUp(self):
        self.repo = Mock()
        self.alerts = Mock()
        self.repo.ensure_event.return_value = {"notification_status": "PENDING"}
        self.repo.acquire.return_value = True
        self.repo.event.return_value = dict(PAYLOAD, received_at="2026-10-17T20:15:33.001Z")
        self.alerts.publish.return_value = "sns-id"
        self.service = service.EventsService(self.repo, self.alerts)

    def process(self, payload=None):
        return self.service.process(json.dumps(PAYLOAD if payload is None else payload), SENT)

    def test_keys_match_user_schema_and_receipt_comes_from_sqs(self):
        self.process(dict(PAYLOAD, PK="untrusted", SK="untrusted", received_at="2000-01-01"))
        stored, fingerprint = self.repo.ensure_event.call_args.args
        self.assertEqual(stored["PK"], "SYSTEM#demo-vps-01")
        self.assertEqual(stored["SK"], f"EVENT#2026-10-17T20:15:32.123Z#{EVENT_ID}")
        self.assertEqual(stored["received_at"], "2026-10-17T20:15:33.001Z")
        self.assertEqual(stored["details"], PAYLOAD["details"])
        self.assertEqual(len(fingerprint), 64)
        self.repo.complete.assert_called_once()
        self.assertEqual(self.alerts.publish.call_args.args[0], self.repo.event.return_value)

    def test_published_duplicate_skips_sns(self):
        self.repo.ensure_event.return_value = {"notification_status": "PUBLISHED"}
        self.process()
        self.repo.acquire.assert_not_called()
        self.alerts.publish.assert_not_called()

    def test_retries_pending_notification_using_original_stored_event(self):
        self.alerts.publish.side_effect = [RuntimeError("SNS failed"), "sns-id"]
        with self.assertRaises(RuntimeError):
            self.process()
        self.repo.complete.assert_not_called()
        self.process()
        self.repo.complete.assert_called_once()
        self.assertEqual(self.alerts.publish.call_count, 2)
        self.assertEqual(self.alerts.publish.call_args.args[0]["received_at"], "2026-10-17T20:15:33.001Z")

    def test_database_failure_never_publishes(self):
        self.repo.ensure_event.side_effect = RuntimeError("DynamoDB unavailable")
        with self.assertRaises(RuntimeError):
            self.process()
        self.alerts.publish.assert_not_called()

    def test_failure_after_publish_is_reported_for_retry(self):
        self.repo.complete.side_effect = RuntimeError("DynamoDB unavailable")
        with self.assertRaises(RuntimeError):
            self.process()
        self.alerts.publish.assert_called_once()

    def test_concurrent_owner_prevents_publication(self):
        self.repo.acquire.side_effect = repository.EventBusy()
        with self.assertRaises(repository.EventBusy):
            self.process()
        self.alerts.publish.assert_not_called()

    def test_simultaneous_completion_skips_publication(self):
        self.repo.acquire.return_value = False
        self.process()
        self.alerts.publish.assert_not_called()

    def test_canonical_hash_ignores_key_order_server_fields_and_timezone_spelling(self):
        self.process()
        original_hash = self.repo.ensure_event.call_args.args[1]
        changed = dict(reversed(list(PAYLOAD.items())))
        changed.update(occurred_at="2026-10-17T17:15:32.123-03:00", received_at="ignored", PK="ignored")
        self.process(changed)
        self.assertEqual(original_hash, self.repo.ensure_event.call_args.args[1])
        changed["details"] = {"different": True}
        self.process(changed)
        self.assertNotEqual(original_hash, self.repo.ensure_event.call_args.args[1])

    def test_invalid_payloads_do_not_write(self):
        for changes in ({"event_id": "bad"}, {"occurred_at": "2026-10-17T20:15:32"},
                        {"system_id": "bad#key"}, {"schema_version": 2}, {"unexpected": True}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.process(dict(PAYLOAD, **changes))
        self.repo.ensure_event.assert_not_called()

    def test_invalid_json_and_oversize_payload_do_not_write(self):
        for raw in ("{", "[]", "x" * (240 * 1024 + 1)):
            with self.subTest(raw=raw[:10]), self.assertRaises(ValueError):
                self.service.process(raw, SENT)
        self.repo.ensure_event.assert_not_called()

    def test_nonfinite_details_do_not_write(self):
        with self.assertRaises(ValueError):
            self.process(dict(PAYLOAD, details={"invalid": float("nan")}))
        self.repo.ensure_event.assert_not_called()


class EventsRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.repo = repository.EventsRepository("events-test", self.client)
        self.item = dict(PAYLOAD, PK="SYSTEM#demo-vps-01", SK=f"EVENT#date#{EVENT_ID}", received_at="receipt")

    def existing(self, **changes):
        control = dict(payload_hash="hash", notification_status="PENDING", **changes)
        self.client.get_item.return_value = {"Item": self.repo.serialize(control)}

    def test_atomic_conditional_event_and_global_id_control_without_ttl(self):
        self.item["details"] = {"number": 12.34, "boolean": True}
        self.repo.ensure_event(self.item, "hash")
        writes = self.client.transact_write_items.call_args.kwargs["TransactItems"]
        self.assertEqual(len(writes), 2)
        decoder = TypeDeserializer()
        records = [{k: decoder.deserialize(v) for k, v in write["Put"]["Item"].items()} for write in writes]
        self.assertEqual(records[0]["PK"], f"EVENT#{EVENT_ID}")
        self.assertEqual(records[1]["PK"], "SYSTEM#demo-vps-01")
        self.assertEqual(records[1]["details"]["number"], Decimal("12.34"))
        self.assertIs(records[1]["details"]["boolean"], True)
        for write, record in zip(writes, records):
            self.assertEqual(write["Put"]["ConditionExpression"], "attribute_not_exists(PK)")
            self.assertNotIn("expires_at", record)

    def test_duplicate_reads_control_strongly_consistently(self):
        self.client.transact_write_items.side_effect = error("TransactionCanceledException")
        self.existing()
        self.assertEqual(self.repo.ensure_event(self.item, "hash")["notification_status"], "PENDING")
        self.assertTrue(self.client.get_item.call_args.kwargs["ConsistentRead"])

    def test_id_reused_with_changed_date_or_system_is_a_conflict(self):
        self.client.transact_write_items.side_effect = error("TransactionCanceledException")
        self.existing()
        for changed in (dict(self.item, system_id="another"), dict(self.item, occurred_at="another")):
            with self.assertRaises(repository.EventConflict):
                self.repo.ensure_event(changed, "changed hash")

    def test_cancel_without_existing_control_is_not_a_duplicate(self):
        self.client.transact_write_items.side_effect = error("TransactionCanceledException")
        self.client.get_item.return_value = {}
        with self.assertRaises(ClientError):
            self.repo.ensure_event(self.item, "hash")

    def test_unrelated_database_error_propagates(self):
        self.client.transact_write_items.side_effect = error("AccessDeniedException")
        with self.assertRaises(ClientError):
            self.repo.ensure_event(self.item, "hash")
        self.client.get_item.assert_not_called()

    def test_active_lease_is_retried_and_expiry_is_longer_than_lambda_timeout(self):
        self.assertTrue(self.repo.acquire(EVENT_ID, "owner", 100))
        args = self.client.update_item.call_args.kwargs
        self.assertEqual(args["ExpressionAttributeValues"][":until"], {"N": "160"})
        self.assertIn("lease_until < :now", args["ConditionExpression"])
        self.client.update_item.side_effect = error("ConditionalCheckFailedException")
        self.existing()
        with self.assertRaises(repository.EventBusy):
            self.repo.acquire(EVENT_ID, "another-owner", 101)

    def test_completed_race_returns_success(self):
        self.client.update_item.side_effect = error("ConditionalCheckFailedException")
        self.client.get_item.return_value = {"Item": self.repo.serialize({"notification_status": "PUBLISHED"})}
        self.assertFalse(self.repo.acquire(EVENT_ID, "owner", 100))

    def test_completion_requires_same_owner_and_pending_state(self):
        self.repo.complete(EVENT_ID, "owner", "sns-id", "now")
        args = self.client.update_item.call_args.kwargs
        self.assertEqual(args["ConditionExpression"], "notification_status = :pending AND lease_owner = :owner")
        self.assertEqual(args["ExpressionAttributeValues"][":owner"], {"S": "owner"})
        self.assertIn("REMOVE lease_owner, lease_until", args["UpdateExpression"])

    def test_email_state_and_recipient_are_persisted_after_sns_acceptance(self):
        repo = repository.EventsRepository("events-test", self.client, email_recipient="alerts@example.com")
        repo.ensure_event(self.item, "hash")
        control = self.client.transact_write_items.call_args.kwargs["TransactItems"][0]["Put"]["Item"]
        self.assertEqual(control["email_status"], {"S": "PENDING"})
        repo.complete(EVENT_ID, "owner", "sns-id", "now")
        update = self.client.update_item.call_args.kwargs
        self.assertIn("email_status = :published", update["UpdateExpression"])
        self.assertEqual(update["ExpressionAttributeValues"][":recipient"], {"S": "alerts@example.com"})

    def test_sns_includes_event_id_and_handles_dynamodb_numbers(self):
        self.client.publish.return_value = {"MessageId": "sns-id"}
        self.item["details"] = {"code": Decimal("137"), "fraction": Decimal("1.25")}
        self.assertEqual(repository.AlertsRepository("topic", self.client).publish(self.item), "sns-id")
        request = self.client.publish.call_args.kwargs
        self.assertIn("Code: 137", request["Message"])
        self.assertIn("Fraction: 1.25", request["Message"])
        self.assertNotIn("SYSTEM#", request["Message"])
        self.assertIn("demo-vps-01", request["Subject"])
        self.assertEqual(request["MessageAttributes"]["event_id"]["StringValue"], EVENT_ID)


class EventsHandlerTests(unittest.TestCase):
    def test_partial_batch_acknowledges_only_successful_messages(self):
        worker = Mock()
        worker.process.side_effect = [None, ValueError("secret payload"), None]
        records = [{"messageId": str(i), "body": "{}", "attributes": {"SentTimestamp": SENT}} for i in range(3)]
        with patch.dict(os.environ, EVENTS_TABLE_NAME="events", CONFIGURATION_TABLE_NAME="configuration"), \
                patch.object(handler, "EventsRepository"), patch.object(handler, "AlertsRepository"), patch.object(handler, "NotificationConfigurationRepository"), \
                patch.object(handler, "EventsService", return_value=worker), self.assertLogs(handler.logger) as logs:
            result = handler.lambda_handler({"Records": records}, None)
        self.assertEqual(result, {"batchItemFailures": [{"itemIdentifier": "1"}]})
        self.assertEqual(worker.process.call_count, 3)
        self.assertNotIn("secret payload", " ".join(logs.output))

    def test_low_remaining_time_preserves_unprocessed_messages(self):
        context = Mock()
        context.get_remaining_time_in_millis.return_value = 1000
        with patch.dict(os.environ, EVENTS_TABLE_NAME="events", CONFIGURATION_TABLE_NAME="configuration"), \
                patch.object(handler, "EventsRepository"), patch.object(handler, "AlertsRepository"), patch.object(handler, "NotificationConfigurationRepository"), \
                patch.object(handler, "EventsService") as worker:
            result = handler.lambda_handler({"Records": [{"messageId": "1"}]}, context)
        self.assertEqual(result, {"batchItemFailures": [{"itemIdentifier": "1"}]})
        worker.return_value.process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
