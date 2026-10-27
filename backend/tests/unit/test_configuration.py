from copy import deepcopy
from datetime import datetime, timezone
import json
import os
import runpy
from pathlib import Path
import unittest
from uuid import uuid4
from unittest.mock import Mock, patch

import boto3
from moto import mock_aws
from botocore.exceptions import ClientError

from lambda_loader import load_lambda

cfg = load_lambda("configuration")
sync = load_lambda("sync_notification_channels")
events = load_lambda("process_events")
HEALTH = {"name": "Public API", "url": "https://example.com/health"}
RULE = {"name": "CPU alta", "system_id": "vps", "resource_id": "host", "metric": "cpu_percent", "threshold": 80, "recovery_threshold": 70}
CHANNEL = {"name": "Operaciones", "emails": ["admin@example.com"]}


class ConfigurationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.aws = mock_aws(); self.aws.start(); self.addCleanup(self.aws.stop)
        self.ddb = boto3.client("dynamodb", region_name="us-east-1")
        self.ddb.create_table(TableName="configuration", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}, {"AttributeName": "SK", "AttributeType": "S"}])
        self.sns = boto3.client("sns", region_name="us-east-1")
        self.topic = self.sns.create_topic(Name="legacy-alerts")["TopicArn"]
        self.repo = cfg["repository"].ConfigurationRepository("configuration", self.ddb)
        self.subscriptions = cfg["repository"].SubscriptionsRepository(self.topic, self.sns)
        self.service = cfg["service"].ConfigurationService(self.repo, self.subscriptions)
        self.prefix = "test-notification"
        self.arn_prefix = "arn:aws:sns:us-east-1:123456789012:"
        self.sync_repo = sync["repository"].SynchronizationRepository("configuration", self.prefix, self.ddb, self.sns)
        self.sync_service = sync["service"].SynchronizationService(self.sync_repo, self.arn_prefix)

    def save(self, kind, data, uid=None, version=None):
        return self.service.save(kind, uid or str(uuid4()), data, "actor", version)

    def initialize(self):
        return self.service.import_notifications("actor")

    def record(self, item, old=None, removed=False):
        return {"eventName": "REMOVE" if removed else "MODIFY" if old else "INSERT", "dynamodb": {
            "Keys": self.repo.key(item["kind"], item["id"]), "SequenceNumber": "1",
            "OldImage": self.repo.encode(old) if old else {}, "NewImage": {} if removed else self.repo.encode(item)}}

    def test_crud_pagination_and_metadata_use_query_without_scan(self):
        ids = []
        for i in range(3):
            created, item = self.save("health-checks", dict(HEALTH, name=f"Check {i}")); ids.append(item["id"])
            self.assertTrue(created); self.assertEqual(item["version"], 1); self.assertEqual(item["created_by"], "actor")
        page = cfg["models"].Page("health-checks", {"limit": "2"})
        first = self.service.list("health-checks", page)
        self.assertEqual(len(first["items"]), 2)
        second = self.service.list("health-checks", cfg["models"].Page("health-checks", {"limit": "2", "cursor": first["next_cursor"]}))
        self.assertEqual(len(second["items"]), 1)
        self.assertIsNone(second["next_cursor"])
        uid = ids[0]
        created, item = self.save("health-checks", dict(HEALTH, name="Editado"), uid, 1)
        self.assertFalse(created); self.assertEqual(item["version"], 2)
        with self.assertRaises(cfg["repository"].Conflict): self.save("health-checks", HEALTH, uid, 1)
        self.service.delete("health-checks", uid, 2)
        self.assertIsNone(self.repo.get("health-checks", uid))
        with self.assertRaises(cfg["repository"].Conflict): self.save("health-checks", HEALTH, uid)

    def test_atomic_revision_and_quota_prevent_lost_updates(self):
        self.save("health-checks", HEALTH)
        state = self.repo.state()
        _, item = self.save("health-checks", dict(HEALTH, name="Another"))
        raw = self.repo.get("health-checks", item["id"])
        raw["version"] = 2
        with self.assertRaises(cfg["repository"].Conflict):
            self.repo.mutate("health-checks", item["id"], raw, 1, state)
        self.assertEqual(self.repo.get("health-checks", item["id"])["version"], 1)
        self.ddb.update_item(TableName="configuration", Key=self.repo.encode({"PK": "CONFIG#DEFAULT", "SK": "QUOTA#HEALTH_CHECK"}),
            UpdateExpression="SET #count = :limit", ExpressionAttributeNames={"#count": "count"}, ExpressionAttributeValues={":limit": {"N": "100"}})
        with self.assertRaises(cfg["repository"].Conflict): self.save("health-checks", HEALTH)

    def test_create_retry_with_fractional_threshold_is_idempotent(self):
        uid = str(uuid4()); data = dict(RULE, threshold=80.1, recovery_threshold=70.1)
        self.save("alert-rules", data, uid)
        created, item = self.save("alert-rules", data, uid)
        self.assertFalse(created); self.assertEqual(item["version"], 1)

    def test_alert_rule_identity_and_deletion_are_protected(self):
        _, rule = self.save("alert-rules", RULE)
        with self.assertRaises(cfg["repository"].Conflict):
            self.service.delete("alert-rules", rule["id"], 1)
        for changes in ({"metric": "memory_percent"}, {"system_id": "other"}, {"resource_id": "other"}, {"resource_type": "container"}):
            with self.assertRaises((cfg["repository"].Conflict, ValueError)):
                self.save("alert-rules", dict(RULE, **changes), rule["id"], 1)
        _, updated = self.save("alert-rules", dict(RULE, threshold=90, enabled=False), rule["id"], 1)
        self.assertEqual(updated["data"]["threshold"], 90)
        self.assertFalse(updated["data"]["enabled"])

    def test_seed_creates_three_rules_and_preserves_edits_on_retry(self):
        with patch.dict("sys.modules", {"repository": cfg["repository"], "service": cfg["service"]}):
            script = runpy.run_path(str(Path(__file__).resolve().parents[3] / "scripts/seed_configuration.py"))
        script["seed"](self.repo, "demo-vps-01", "seed-actor")
        rules = self.repo.all("alert-rules")
        self.assertEqual(len(rules), 3)
        cpu = next(r for r in rules if r["data"]["metric"] == "cpu_percent")
        self.save("alert-rules", dict(RULE, name="CPU", system_id="demo-vps-01", threshold=95), cpu["id"], 1)
        script["seed"](self.repo, "demo-vps-01", "seed-actor")
        self.assertEqual(len(self.repo.all("alert-rules")), 3)
        self.assertEqual(self.repo.get("alert-rules", cpu["id"])["data"]["threshold"], 95)

    def test_import_is_once_and_preserves_all_existing_emails_without_hardcoded_defaults(self):
        self.sns.subscribe(TopicArn=self.topic, Protocol="email", Endpoint="existing@example.com")
        imported = self.initialize()
        self.assertEqual(imported["emails"], ["existing@example.com"])
        self.assertEqual(self.repo.all("notification-channels")[0]["data"]["emails"], ["existing@example.com"])
        with self.assertRaises(cfg["repository"].Conflict): self.initialize()

    def test_initialization_without_legacy_topic_and_status_reads_still_work(self):
        subscriptions = cfg["repository"].SubscriptionsRepository(None, self.sns)
        service = cfg["service"].ConfigurationService(self.repo, subscriptions)
        with patch.object(self.sns, "list_subscriptions_by_topic", wraps=self.sns.list_subscriptions_by_topic) as listing:
            result = service.import_notifications("actor")
        self.assertTrue(result["notifications_initialized"])
        self.assertEqual(result["emails"], [])
        listing.assert_not_called()
        event = {"requestContext": {"http": {"method": "GET"}, "authorizer": {"jwt": {"claims": {"sub": "reader", "scope": "metrics/read"}}}},
                 "pathParameters": {"kind": "alert-rules"}}
        with patch.dict(os.environ, {"CONFIGURATION_TABLE_NAME": "configuration"}, clear=True), \
                patch.object(cfg["handler"], "ConfigurationRepository", return_value=self.repo), \
                patch.object(cfg["handler"], "SubscriptionsRepository", return_value=subscriptions):
            response = cfg["handler"].lambda_handler(event, None)
        self.assertEqual(response["statusCode"], 200)

    def test_notification_references_and_email_uniqueness(self):
        with self.assertRaises(cfg["repository"].Conflict): self.save("notification-channels", CHANNEL)
        self.initialize()
        _, channel = self.save("notification-channels", CHANNEL)
        with self.assertRaises(ValueError): self.save("notification-channels", CHANNEL)
        _, rule = self.save("alert-rules", dict(RULE, notification_channel_ids=[channel["id"]]))
        with self.assertRaises(cfg["repository"].Conflict): self.service.delete("notification-channels", channel["id"], 1)
        self.save("alert-rules", dict(rule["data"], notification_channel_ids=[]), rule["id"], 1)
        self.service.delete("notification-channels", channel["id"], 1)
        self.assertTrue(self.repo.state()["notifications_initialized"])

    def test_sns_sync_email_edit_and_disabled_channel_follow_dynamodb(self):
        self.initialize(); _, public = self.save("notification-channels", CHANNEL)
        current = self.repo.get("notification-channels", public["id"])
        self.sync_service.sync(self.record(current))
        synced = self.repo.get("notification-channels", public["id"])
        self.assertEqual(synced["delivery"]["status"], "SYNCED")
        self.assertEqual(synced["version"], 1)
        old_topic = synced["delivery"]["topic_arn"]
        self.assertEqual(self.sns.list_subscriptions_by_topic(TopicArn=old_topic)["Subscriptions"][0]["Endpoint"], "admin@example.com")
        self.save("notification-channels", dict(CHANNEL, emails=["new@example.com"]), public["id"], 1)
        current = self.repo.get("notification-channels", public["id"])
        self.sync_service.sync(self.record(current, synced))
        changed = self.repo.get("notification-channels", public["id"])
        self.assertNotEqual(changed["delivery"]["topic_arn"], old_topic)
        topics = {topic["TopicArn"] for topic in self.sns.list_topics()["Topics"]}
        self.assertNotIn(old_topic, topics); self.assertIn(self.topic, topics)
        self.save("notification-channels", dict(CHANNEL, emails=["new@example.com"], enabled=False), public["id"], 2)
        current = self.repo.get("notification-channels", public["id"])
        self.sync_service.sync(self.record(current, changed))
        self.assertEqual(self.repo.get("notification-channels", public["id"])["delivery"]["status"], "DISABLED")
        self.assertEqual(self.service.delivery_status(public["id"])["status"], "DISABLED")

    def test_event_delivery_checkpoints_survive_a_partial_failure_and_retry(self):
        self.initialize()
        _, first = self.save("notification-channels", CHANNEL)
        _, second = self.save("notification-channels", {"name": "Otro", "emails": ["second@example.com"]})
        for public_item in (first, second):
            item = self.repo.get("notification-channels", public_item["id"])
            self.sync_service.sync(self.record(item))
        self.ddb.create_table(TableName="events", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}, {"AttributeName": "SK", "AttributeType": "S"}])
        event_repo = events["repository"].EventsRepository("events", self.ddb)
        config = events["repository"].NotificationConfigurationRepository("configuration", self.ddb)
        alerts = Mock(); alerts.publish.side_effect = ["message-one", RuntimeError("SNS unavailable")]
        worker = events["service"].EventsService(event_repo, alerts, config)
        raw = (Path(__file__).resolve().parents[2] / "lambda/process_events/test/payload.json").read_text()
        event_id = json.loads(raw)["event_id"]
        with self.assertRaises(RuntimeError): worker.process(raw, "1792268133001")
        partial = event_repo.get_control(event_id)
        self.assertEqual(len(partial["publications"]), 1)
        self.ddb.update_item(TableName="events", Key=event_repo.key(event_id), UpdateExpression="SET lease_until = :past", ExpressionAttributeValues={":past": {"N": "0"}})
        alerts.publish.side_effect = None; alerts.publish.return_value = "message-two"; alerts.publish.reset_mock()
        worker.process(raw, "1792268133001")
        self.assertEqual(alerts.publish.call_count, 1)
        completed = event_repo.get_control(event_id)
        self.assertEqual(completed["notification_status"], "PUBLISHED")
        self.assertEqual(set(completed["email_recipients"]), {"admin@example.com", "second@example.com"})
        alerts.publish.reset_mock(); worker.process(raw, "1792268133001"); alerts.publish.assert_not_called()

    def test_intentionally_empty_configuration_completes_events_without_sending(self):
        self.initialize()
        self.ddb.create_table(TableName="events", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}, {"AttributeName": "SK", "AttributeType": "S"}])
        repo = events["repository"].EventsRepository("events", self.ddb)
        alerts = Mock()
        worker = events["service"].EventsService(repo, alerts, events["repository"].NotificationConfigurationRepository("configuration", self.ddb))
        raw = (Path(__file__).resolve().parents[2] / "lambda/process_events/test/payload.json").read_text()
        worker.process(raw, "1792268133001"); worker.process(raw, "1792268133001")
        self.assertEqual(repo.get_control(json.loads(raw)["event_id"])["notification_status"], "SKIPPED")
        alerts.publish.assert_not_called()

    def test_stream_metadata_updates_do_not_trigger_reconciliation_loop(self):
        self.initialize(); _, public = self.save("notification-channels", CHANNEL)
        item = self.repo.get("notification-channels", public["id"])
        old = deepcopy(item); item["delivery"] = {"status": "SYNCED"}
        self.assertFalse(sync["models"].relevant(self.record(item, old)))
        item["version"] = 2
        self.assertTrue(sync["models"].relevant(self.record(item, old)))

    def test_event_targets_are_blocked_until_import_and_until_sns_is_ready(self):
        targets = events["repository"].NotificationConfigurationRepository("configuration", self.ddb)
        with self.assertRaises(RuntimeError): targets.targets()
        self.initialize(); self.assertEqual(targets.targets(), [])
        _, public = self.save("notification-channels", CHANNEL)
        with self.assertRaises(RuntimeError): targets.targets()
        item = self.repo.get("notification-channels", public["id"])
        self.sync_service.sync(self.record(item))
        target = targets.targets()[0]
        self.assertTrue(targets.still_active(target))
        self.save("notification-channels", dict(CHANNEL, enabled=False), public["id"], 1)
        self.assertFalse(targets.still_active(target))

    def test_synchronizer_does_not_overwrite_a_newer_edit(self):
        self.initialize(); _, public = self.save("notification-channels", CHANNEL)
        old = self.repo.get("notification-channels", public["id"])
        self.save("notification-channels", dict(CHANNEL, name="Changed"), public["id"], 1)
        with self.assertRaises(RuntimeError): self.sync_repo.synced(old, "topic", [], "SYNCED")
        self.assertEqual(self.repo.get("notification-channels", public["id"])["version"], 2)


class ConfigurationValidationTests(unittest.TestCase):
    def test_invalid_health_check_and_metrics_are_rejected(self):
        for kind, data in [("health-checks", dict(HEALTH, url="file:///etc/passwd")),
                           ("health-checks", dict(HEALTH, url="https://user:secret@example.com/")),
                           ("health-checks", dict(HEALTH, timeout_seconds=60, interval_seconds=30)),
                           ("health-checks", dict(HEALTH, failure_count=0)),
                           ("alert-rules", dict(RULE, threshold=101)),
                           ("alert-rules", dict(RULE, recovery_threshold=90)),
                           ("alert-rules", dict(RULE, metric="network_rx_bytes_per_second", resource_type="host")),
                           ("notification-channels", dict(CHANNEL, emails=["bad"])),
                           ("notification-channels", dict(CHANNEL, emails=["A@example.com", "a@example.com"]))]:
            with self.subTest(kind=kind, data=data), self.assertRaises(ValueError): cfg["models"].settings(kind, data)

    def test_unsupported_rules_are_rejected_by_api_with_clear_422(self):
        claims = {"sub": "admin", "scope": "metrics/read metrics/configuration.write", "cognito:groups": ["configuration-admins"]}
        cases = [{"resource_type": kind} for kind in ("container", "cpu_core", "interface", "disk")]
        cases += [{"resource_id": "other"}, {"metric": "network_rx_bytes_per_second"}, {"metric": "network_tx_bytes_per_second"}]
        for changes in cases:
            with self.subTest(changes=changes), patch.dict(os.environ, {"CONFIGURATION_TABLE_NAME": "test"}, clear=True), \
                    patch.object(cfg["handler"], "ConfigurationRepository"), patch.object(cfg["handler"], "SubscriptionsRepository"):
                request = self.request("POST", claims, kind="alert-rules", body={"id": str(uuid4()), "data": dict(RULE, **changes)})
                result = cfg["handler"].lambda_handler(request, None)
                self.assertEqual(result["statusCode"], 422)
                self.assertIn("host", json.loads(result["body"])["message"])

    def test_supported_host_matrix(self):
        for metric in ("cpu_percent", "memory_percent", "disk_percent"):
            self.assertEqual(cfg["models"].settings("alert-rules", dict(RULE, metric=metric))["metric"], metric)

    def request(self, method="GET", claims=None, kind="health-checks", uid=None, body=None):
        return {"requestContext": {"http": {"method": method}, "authorizer": {"jwt": {"claims": claims or {"sub": "user", "scope": "metrics/read"}}}},
                "pathParameters": {"kind": kind, **({"id": uid} if uid else {})}, "body": json.dumps(body or {})}

    def test_read_users_cannot_write_even_with_configuration_scope_without_group(self):
        with patch.object(cfg["handler"], "ConfigurationRepository") as repo:
            for claims in ({"sub": "u", "scope": "metrics/read"}, {"sub": "u", "scope": "metrics/read metrics/configuration.write"}):
                result = cfg["handler"].lambda_handler(self.request("POST", claims), None)
                self.assertEqual(result["statusCode"], 403)
            repo.assert_not_called()

    def test_admin_claim_formats_and_cron_category_is_unavailable(self):
        for groups in (["configuration-admins"], '["configuration-admins"]', '[configuration-admins]'):
            self.assertTrue(cfg["handler"].is_admin({"cognito:groups": groups}))
        self.assertFalse(cfg["handler"].is_admin({"cognito:groups": ["configuration-admins-fake"]}))
        result = cfg["handler"].lambda_handler(self.request(kind="schedules"), None)
        self.assertEqual(result["statusCode"], 404)


class ChannelCheckpointTests(unittest.TestCase):
    def test_successful_channel_is_not_published_again_when_another_one_retries(self):
        repo, alerts, config = Mock(), Mock(), Mock()
        target1 = {"id": "one", "emails": ["one@example.com"], "topic_arn": "topic-one"}
        target2 = {"id": "two", "emails": ["two@example.com"], "topic_arn": "topic-two"}
        repo.get_control.return_value = {"notification_targets": [target1, target2], "publications": {
            "one": {"status": "PUBLISHED", "emails": target1["emails"], "published_at": "now", "sns_message_id": "id-one"}}}
        repo.event.return_value = {"event_id": "event"}; config.still_active.return_value = True; alerts.publish.return_value = "id-two"
        events["service"].EventsService(repo, alerts, config).publish_channels("event", "owner")
        alerts.publish.assert_called_once_with({"event_id": "event"}, topic_arn="topic-two")
        repo.complete_channels.assert_called_once()

    def test_removed_or_changed_channel_is_skipped_instead_of_sending_to_old_address(self):
        repo, alerts, config = Mock(), Mock(), Mock()
        repo.get_control.return_value = {"notification_targets": [{"id": "one", "emails": ["old@example.com"], "topic_arn": "old"}], "publications": {}}
        config.still_active.return_value = False
        events["service"].EventsService(repo, alerts, config).publish_channels("event", "owner")
        alerts.publish.assert_not_called()
        self.assertEqual(repo.record_publication.call_args.args[3]["status"], "SKIPPED_CONFIGURATION_CHANGED")
