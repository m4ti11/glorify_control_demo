from datetime import datetime, timedelta, timezone
import json
import os
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError
from moto import mock_aws

from lambda_loader import load_lambda

worker = load_lambda("evaluate_host_alerts")
events = load_lambda("process_events")
models, repository, service, handler = (worker[key] for key in ("models", "repository", "service", "handler"))
BASE = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
NOW = BASE + timedelta(days=1)
SYSTEM = "demo-vps-01"


class HostAlertsTests(unittest.TestCase):
    def setUp(self):
        aws = mock_aws(); aws.start(); self.addCleanup(aws.stop)
        self.ddb = boto3.client("dynamodb", region_name="us-east-1")
        for table in ("metrics", "configuration", "events"):
            self.ddb.create_table(TableName=table, BillingMode="PAY_PER_REQUEST",
                KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
                AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}, {"AttributeName": "SK", "AttributeType": "S"}])
        self.sqs = boto3.client("sqs", region_name="us-east-1")
        self.queue = self.sqs.create_queue(QueueName="events")["QueueUrl"]
        self.repo = repository.AlertRepository("metrics", "configuration", self.queue, self.ddb, self.sqs)
        self.worker = service.HostAlertService(self.repo, max_sample_gap_seconds=180, clock=lambda: NOW)
        self.rules = []

    def put(self, table, item):
        self.ddb.put_item(TableName=table, Item=self.repo.encode(item))

    def rule(self, **changes):
        uid = str(uuid4())
        data = {"name": "CPU", "system_id": SYSTEM, "resource_type": "host", "resource_id": "host",
            "metric": "cpu_percent", "enabled": True, "operator": ">=", "threshold": 80,
            "recovery_threshold": 70, "duration_seconds": 600, "consecutive_samples": 3,
            "cooldown_seconds": 1800, "notify_recovery": True, "maintenance_until": None,
            "notification_channel_ids": [], **changes}
        rule = {"PK": "CONFIG#DEFAULT", "SK": "ALERT_RULE#" + uid, "kind": "alert-rules", "id": uid,
                "version": 1, "updated_at": models.timestamp(BASE - timedelta(days=1)), "data": data}
        self.put("configuration", rule); self.rules.append(rule)
        return rule

    def reading(self, seconds, cpu=85, memory=90, disk=95, system=SYSTEM, resource="HOST", resource_id="host"):
        at = models.timestamp(BASE + timedelta(seconds=seconds))
        reading = {"PK": "SYSTEM#" + system, "SK": f"RESOURCE#{resource}#{resource_id}#METRIC#" + at,
                   "system_id": system, "record_type": "metric", "resource_type": resource,
                   "resource_id": resource_id, "measured_at": at,
                   "metrics": {"cpu_percent": cpu, "memory": {"percent": memory}, "disk": {"percent": disk}}}
        self.put("metrics", reading)
        return reading

    def window(self, **values):
        for seconds in range(0, 601, 120):
            reading = self.reading(seconds, **values)
        return reading

    def messages(self):
        records = self.sqs.receive_message(QueueUrl=self.queue, MaxNumberOfMessages=10).get("Messages", [])
        for record in records:
            self.sqs.delete_message(QueueUrl=self.queue, ReceiptHandle=record["ReceiptHandle"])
        return [json.loads(record["Body"]) for record in records]

    def test_ten_minute_window_only_reads_host_for_this_system(self):
        rule = self.rule()
        reading = self.window(cpu=80)
        self.reading(300, cpu=0, resource="CONTAINER", resource_id="db")
        self.reading(300, cpu=0, system="other-vps")
        self.reading(-3600, cpu=0)
        self.reading(720, cpu=0)  # Future relative to this stream record must not be read.
        with patch.object(self.ddb, "query", wraps=self.ddb.query) as query:
            self.worker.process(reading)
        queries = [call.kwargs for call in query.call_args_list if call.kwargs["TableName"] == "metrics"]
        self.assertEqual(len(queries), 1)
        request = queries[0]
        self.assertEqual(request["ExpressionAttributeValues"][":pk"], {"S": "SYSTEM#" + SYSTEM})
        self.assertIn("SK BETWEEN", request["KeyConditionExpression"])
        self.assertNotIn("FilterExpression", request)
        self.assertTrue(request["ConsistentRead"])
        self.assertEqual(request["ExpressionAttributeValues"][":start"], {"S": models.HOST_PREFIX + models.timestamp(BASE - timedelta(seconds=180))})
        message, = self.messages()
        self.assertEqual(message["details"]["rule_id"], rule["id"])
        self.assertEqual(message["event_type"], "host.threshold_exceeded")
        self.assertEqual(message["details"]["value"], 80)
        self.worker.process(reading)
        self.assertEqual(self.messages(), [])

    def test_short_window_gap_or_intermediate_low_sample_do_not_alert(self):
        for scenario in ("short", "gap", "low"):
            with self.subTest(scenario=scenario):
                rule = self.rule(system_id="vps-" + scenario)
                times = [0, 120, 240] if scenario == "short" else [0, 120, 480, 600] if scenario == "gap" else list(range(0, 601, 120))
                for seconds in times:
                    reading = self.reading(seconds, cpu=60 if scenario == "low" and seconds == 240 else 85, system=rule["data"]["system_id"])
                self.worker.process(reading)
                self.assertEqual(self.messages(), [])

    def test_ten_minute_gap_accepts_two_samples_and_rejects_larger_gap(self):
        for gap, expected in ((480, 1), (600, 1), (601, 0)):
            with self.subTest(gap=gap):
                rule = self.rule(system_id=f"gap-{gap}", duration_seconds=60, consecutive_samples=2)
                self.reading(0, system=rule["data"]["system_id"])
                reading = self.reading(gap, system=rule["data"]["system_id"])
                evaluator = service.HostAlertService(self.repo, clock=lambda: NOW)
                with self.assertLogs(service.logger, level="INFO") as captured:
                    evaluator.process(reading)
                self.assertEqual(len(self.messages()), expected)
                logs = " ".join(captured.output)
                self.assertIn("history.query_started", logs)
                self.assertIn("rule.window_checked", logs)
                if expected:
                    self.assertIn("sqs.enqueued", logs)
                else:
                    self.assertIn("sample_gap_exceeded", logs)
                    self.assertIn("max_gap_seconds=600", logs)

    def test_low_intermediate_sample_breaks_sequence_with_ten_minute_tolerance(self):
        self.rule(duration_seconds=60, consecutive_samples=2)
        self.reading(0)
        self.reading(240, cpu=20)
        reading = self.reading(480)
        evaluator = service.HostAlertService(self.repo, clock=lambda: NOW)
        with self.assertLogs(service.logger, level="INFO") as captured:
            evaluator.process(reading)
        self.assertEqual(self.messages(), [])
        self.assertIn("threshold_interrupted", " ".join(captured.output))

    def test_boundary_lookbehind_and_minimum_samples(self):
        rule = self.rule(duration_seconds=550, consecutive_samples=7)
        reading = self.window()
        self.worker.process(reading)
        self.assertEqual(self.messages(), [])  # Six samples cover ten minutes, but seven are required.
        reading = self.reading(720)
        self.worker.process(reading)
        self.assertEqual(len(self.messages()), 1)
        self.assertTrue(self.repo.state(SYSTEM, rule["id"])["active"])

    def test_three_metrics_share_one_history_query(self):
        for metric in ("cpu_percent", "memory_percent", "disk_percent"):
            self.rule(metric=metric)
        reading = self.window()
        with patch.object(self.repo, "history", wraps=self.repo.history) as history:
            self.worker.process(reading)
        history.assert_called_once()
        self.assertEqual(len(self.messages()), 3)

    def test_normal_reading_does_not_query_history(self):
        self.rule()
        with patch.object(self.repo, "history", wraps=self.repo.history) as history:
            self.worker.process(self.reading(0, cpu=20))
        history.assert_not_called()
        self.assertEqual(self.messages(), [])

    def test_disabled_maintenance_and_unsupported_rules_are_not_evaluated(self):
        self.rule(enabled=False)
        self.rule(maintenance_until=models.timestamp(NOW + timedelta(hours=1)))
        self.rule(resource_type="container", resource_id="db")
        self.rule(resource_type="disk", resource_id="/")
        self.rule(metric="network_rx_bytes_per_second", resource_type="interface", resource_id="eth0")
        self.rule(system_id="other")
        with patch.object(self.repo, "history", wraps=self.repo.history) as history:
            self.worker.process(self.reading(0))
        history.assert_not_called()
        self.assertEqual(self.messages(), [])

    def test_cooldown_recovery_and_hysteresis(self):
        rule = self.rule(duration_seconds=0, consecutive_samples=1, cooldown_seconds=600)
        self.worker.process(self.reading(0)); self.assertEqual(len(self.messages()), 1)
        with patch.object(self.repo, "history", wraps=self.repo.history) as history:
            self.worker.process(self.reading(120))
        history.assert_not_called()
        self.assertEqual(self.messages(), [])
        self.worker.process(self.reading(240, cpu=75)); self.assertEqual(self.messages(), [])
        self.worker.process(self.reading(360, cpu=70))
        recovered, = self.messages(); self.assertEqual(recovered["event_type"], "host.threshold_recovered")
        self.worker.process(self.reading(480)); self.assertEqual(self.messages(), [])
        self.worker.process(self.reading(600)); self.assertEqual(len(self.messages()), 1)
        self.assertTrue(self.repo.state(SYSTEM, rule["id"])["notified"])

    def test_recovery_also_requires_a_complete_duration_window(self):
        self.rule()
        self.worker.process(self.window()); self.messages()
        self.worker.process(self.reading(720, cpu=60)); self.assertEqual(self.messages(), [])
        for seconds in range(840, 1321, 120):
            reading = self.reading(seconds, cpu=60)
        self.worker.process(reading)
        recovered, = self.messages(); self.assertEqual(recovered["event_type"], "host.threshold_recovered")

    def test_low_operator_and_disabled_recovery_notice(self):
        rule = self.rule(operator="<=", threshold=20, recovery_threshold=30, duration_seconds=0,
                         consecutive_samples=1, notify_recovery=False)
        self.worker.process(self.reading(0, cpu=10)); self.assertEqual(len(self.messages()), 1)
        self.worker.process(self.reading(120, cpu=40)); self.assertEqual(self.messages(), [])
        self.assertFalse(self.repo.state(SYSTEM, rule["id"])["active"])

    def test_enqueue_failure_keeps_durable_event_and_retries_same_id(self):
        rule = self.rule()
        reading = self.window()
        with patch.object(self.repo, "enqueue", side_effect=RuntimeError("SQS unavailable")):
            with self.assertRaises(RuntimeError): self.worker.process(reading)
        pending = self.repo.state(SYSTEM, rule["id"])["pending_event"]
        self.worker.process(reading)
        message, = self.messages()
        self.assertEqual(message["event_id"], pending["event_id"])
        self.assertNotIn("pending_event", self.repo.state(SYSTEM, rule["id"]))

    def test_send_success_before_ack_failure_reuses_identical_event(self):
        self.rule(); reading = self.window()
        with patch.object(self.repo, "acknowledge", side_effect=RuntimeError("DynamoDB unavailable")):
            with self.assertRaises(RuntimeError): self.worker.process(reading)
        self.worker.process(reading)
        first, second = self.messages()
        self.assertEqual(first, second)

    def test_pending_notice_is_discarded_if_rule_was_disabled(self):
        rule = self.rule(); reading = self.window()
        with patch.object(self.repo, "enqueue", side_effect=RuntimeError()):
            with self.assertRaises(RuntimeError): self.worker.process(reading)
        rule["version"] = 2; rule["data"]["enabled"] = False; self.put("configuration", rule)
        self.worker.process(reading)
        self.assertEqual(self.messages(), [])
        self.assertNotIn("pending_event", self.repo.state(SYSTEM, rule["id"]))

    def test_out_of_order_samples_do_not_rewind_state(self):
        rule = self.rule(duration_seconds=0, consecutive_samples=1)
        older = self.reading(0)
        self.worker.process(self.reading(120, cpu=10))
        self.worker.process(older)
        self.assertEqual(self.messages(), [])
        self.assertEqual(self.repo.state(SYSTEM, rule["id"])["last_measured_at"], models.timestamp(BASE + timedelta(seconds=120)))

    def test_rule_edits_restart_duration_coverage(self):
        rule = self.rule(); reading = self.window()
        rule.update(version=2, updated_at=models.timestamp(BASE + timedelta(seconds=480)))
        self.put("configuration", rule)
        self.worker.process(reading)
        self.assertEqual(self.messages(), [])

    def test_atomic_save_rejects_concurrent_state_or_configuration_changes(self):
        rule = self.rule()
        state = {"last_measured_at": models.timestamp(BASE), "active": False}
        self.repo.save(rule, {}, state)
        with self.assertRaises(repository.EvaluationConflict): self.repo.save(rule, {}, state)
        previous = self.repo.state(SYSTEM, rule["id"])
        changed = dict(rule, version=2); self.put("configuration", changed)
        with self.assertRaises(repository.EvaluationConflict): self.repo.save(rule, previous, state)
        self.assertEqual(self.repo.state(SYSTEM, rule["id"])["revision"], 1)

    def test_history_pagination_and_safety_limit(self):
        fake = Mock()
        fake.query.side_effect = [
            {"Items": [self.repo.encode({"measured_at": models.timestamp(BASE), "metrics": {}})], "LastEvaluatedKey": {"PK": {"S": "key"}}},
            {"Items": [self.repo.encode({"measured_at": models.timestamp(BASE + timedelta(seconds=1)), "metrics": {}})]},
        ]
        repo = repository.AlertRepository("metrics", "configuration", self.queue, fake, self.sqs)
        self.assertEqual(len(repo.history(SYSTEM, BASE, BASE + timedelta(seconds=2))), 2)
        self.assertIn("ExclusiveStartKey", fake.query.call_args.kwargs)
        repo.max_history_items = 1
        fake.query.side_effect = None
        fake.query.return_value = {"Items": [self.repo.encode({"measured_at": models.timestamp(BASE), "metrics": {}})] * 2}
        with self.assertRaises(RuntimeError): repo.history(SYSTEM, BASE, BASE)

    def channel(self, name):
        uid = str(uuid4()); sns = boto3.client("sns", region_name="us-east-1")
        topic = sns.create_topic(Name=name)["TopicArn"]
        item = {"PK": "CONFIG#DEFAULT", "SK": "NOTIFICATION_CHANNEL#" + uid,
                "id": uid, "version": 1, "data": {"name": name, "enabled": True, "emails": [name + "@example.com"]},
                "delivery": {"status": "SYNCED", "synced_version": 1, "topic_arn": topic}}
        self.put("configuration", item)
        return item

    def test_end_to_end_selected_channels_duplicate_events_and_legacy_broadcast(self):
        chosen, other = self.channel("chosen"), self.channel("other")
        self.put("configuration", {"PK": "CONFIG#DEFAULT", "SK": "STATE", "notifications_initialized": True})
        self.rule(notification_channel_ids=[chosen["id"]])
        self.worker.process(self.window())
        message, = self.messages()
        alerts = Mock(); alerts.publish.return_value = "sns-id"
        event_repo = events["repository"].EventsRepository("events", self.ddb)
        config = events["repository"].NotificationConfigurationRepository("configuration", self.ddb)
        consumer = events["service"].EventsService(event_repo, alerts, config)
        for _ in range(2): consumer.process(json.dumps(message), "1792660000000")
        alerts.publish.assert_called_once()
        self.assertEqual(alerts.publish.call_args.kwargs["topic_arn"], chosen["delivery"]["topic_arn"])
        self.assertEqual(event_repo.get_control(message["event_id"])["email_recipients"], ["chosen@example.com"])
        legacy = dict(message, event_id=str(uuid4()), source="agent")
        legacy.pop("notification_channel_ids")
        consumer.process(json.dumps(legacy), "1792660000000")
        self.assertEqual(alerts.publish.call_count, 3)

    def test_queued_event_is_skipped_after_rule_edit(self):
        chosen = self.channel("chosen")
        self.put("configuration", {"PK": "CONFIG#DEFAULT", "SK": "STATE", "notifications_initialized": True})
        rule = self.rule(notification_channel_ids=[chosen["id"]])
        self.worker.process(self.window()); message, = self.messages()
        rule.update(version=2); self.put("configuration", rule)
        alerts = Mock()
        event_repo = events["repository"].EventsRepository("events", self.ddb)
        consumer = events["service"].EventsService(event_repo, alerts,
            events["repository"].NotificationConfigurationRepository("configuration", self.ddb))
        consumer.process(json.dumps(message), "1792660000000")
        alerts.publish.assert_not_called()
        self.assertEqual(event_repo.get_control(message["event_id"])["notification_status"], "SKIPPED")


class HostAlertHandlerTests(unittest.TestCase):
    def record(self, sequence, **changes):
        item = {"PK": "SYSTEM#" + SYSTEM, "SK": models.HOST_PREFIX + models.timestamp(BASE),
                "measured_at": models.timestamp(BASE), "system_id": SYSTEM,
                "record_type": "metric", "resource_type": "HOST", "resource_id": "host", **changes}
        encoder = repository.TypeSerializer()
        return {"eventName": "INSERT", "dynamodb": {"SequenceNumber": str(sequence),
                "NewImage": {key: encoder.serialize(value) for key, value in item.items()}}}

    def invoke(self, records, context=None, fail=None):
        processor = Mock(); processor.process.side_effect = fail
        with patch.dict(os.environ, METRICS_TABLE_NAME="metrics", CONFIGURATION_TABLE_NAME="configuration", EVENTS_QUEUE_URL="queue"), \
                patch.object(handler, "AlertRepository"), patch.object(handler, "HostAlertService", return_value=processor):
            result = handler.lambda_handler({"Records": records}, context)
        return result, processor

    def test_filters_non_host_records_and_stops_at_first_failure(self):
        records = [self.record(1, record_type="latest_snapshot"), self.record(2, resource_type="CONTAINER"),
                   self.record(3), self.record(4), self.record(5)]
        with self.assertLogs(handler.logger) as logs:
            result, processor = self.invoke(records, fail=[None, RuntimeError("sensitive details")])
        self.assertEqual(result, {"batchItemFailures": [{"itemIdentifier": "4"}]})
        self.assertEqual(processor.process.call_count, 2)
        self.assertNotIn("sensitive details", " ".join(logs.output))

    def test_low_remaining_time_retries_before_processing(self):
        context = Mock(); context.get_remaining_time_in_millis.return_value = 1000
        result, processor = self.invoke([self.record(1)], context=context)
        self.assertEqual(result, {"batchItemFailures": [{"itemIdentifier": "1"}]})
        processor.process.assert_not_called()

