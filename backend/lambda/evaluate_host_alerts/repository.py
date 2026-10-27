from __future__ import annotations

from decimal import Decimal
import json

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.config import Config
from botocore.exceptions import ClientError

from models import HOST_PREFIX, instant, json_body, timestamp

AWS_CONFIG = Config(connect_timeout=2, read_timeout=3, retries={"mode": "standard", "total_max_attempts": 2})


class EvaluationConflict(RuntimeError):
    pass


class AlertRepository:
    def __init__(self, metrics_table, configuration_table, queue_url, client=None, sqs=None, max_history_items=10000):
        self.metrics_table, self.configuration_table, self.queue_url = metrics_table, configuration_table, queue_url
        self.client = client or boto3.client("dynamodb", config=AWS_CONFIG)
        self.sqs = sqs or boto3.client("sqs", config=AWS_CONFIG)
        self.encoder, self.decoder = TypeSerializer(), TypeDeserializer()
        self.max_history_items = max_history_items

    def encode(self, value):
        value = json.loads(json_body(value), parse_float=Decimal)
        return {key: self.encoder.serialize(item) for key, item in value.items()}

    def decode(self, value):
        return {key: self.decoder.deserialize(item) for key, item in value.items()}

    def rule(self, uid):
        result = self.client.get_item(TableName=self.configuration_table, ConsistentRead=True,
            Key=self.encode({"PK": "CONFIG#DEFAULT", "SK": "ALERT_RULE#" + uid}))
        return self.decode(result.get("Item", {}))

    def rules(self, system_id):
        items, key = [], None
        while True:
            args = dict(TableName=self.configuration_table, ConsistentRead=True, Limit=25,
                KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
                ExpressionAttributeValues=self.encode({":pk": "CONFIG#DEFAULT", ":prefix": "ALERT_RULE#"}))
            if key:
                args["ExclusiveStartKey"] = key
            result = self.client.query(**args)
            items.extend(self.decode(item) for item in result.get("Items", []))
            if len(items) > 100:
                raise RuntimeError("Alert configuration quota exceeded")
            key = result.get("LastEvaluatedKey")
            if not key:
                break
        return [r for r in items if r["data"]["system_id"] == system_id and
                r["data"]["resource_type"] == "host" and r["data"]["resource_id"] == "host" and
                r["data"]["metric"] in {"cpu_percent", "memory_percent", "disk_percent"}]

    def history(self, system_id, start, end):
        readings, key = [], None
        while True:
            args = dict(TableName=self.metrics_table, ConsistentRead=True, ScanIndexForward=False, Limit=250,
                KeyConditionExpression="PK = :pk AND SK BETWEEN :start AND :end",
                ExpressionAttributeValues=self.encode({":pk": "SYSTEM#" + system_id,
                    ":start": HOST_PREFIX + timestamp(start), ":end": HOST_PREFIX + timestamp(end)}),
                ProjectionExpression="measured_at, #metrics", ExpressionAttributeNames={"#metrics": "metrics"})
            if key:
                args["ExclusiveStartKey"] = key
            result = self.client.query(**args)
            readings.extend(self.decode(item) for item in result.get("Items", []))
            if len(readings) > self.max_history_items:
                raise RuntimeError("Host evaluation history limit exceeded")
            key = result.get("LastEvaluatedKey")
            if not key:
                return sorted(readings, key=lambda row: instant(row["measured_at"]), reverse=True)

    def state_key(self, system_id, uid):
        return {"PK": "ALERT_STATE#" + system_id, "SK": "RULE#" + uid}

    def state(self, system_id, uid):
        result = self.client.get_item(TableName=self.metrics_table, ConsistentRead=True,
            Key=self.encode(self.state_key(system_id, uid)))
        return self.decode(result.get("Item", {}))

    def save(self, rule, previous, state):
        item = {**self.state_key(rule["data"]["system_id"], rule["id"]), **state,
                "record_type": "alert_state", "revision": int(previous.get("revision", 0)) + 1}
        write = {"TableName": self.metrics_table, "Item": self.encode(item),
                 "ConditionExpression": "#revision = :revision" if previous else "attribute_not_exists(PK)"}
        if previous:
            write.update(ExpressionAttributeNames={"#revision": "revision"},
                         ExpressionAttributeValues=self.encode({":revision": previous["revision"]}))
        try:
            self.client.transact_write_items(TransactItems=[{"Put": write}, {"ConditionCheck": {
                "TableName": self.configuration_table,
                "Key": self.encode({"PK": "CONFIG#DEFAULT", "SK": "ALERT_RULE#" + rule["id"]}),
                "ConditionExpression": "#version = :version", "ExpressionAttributeNames": {"#version": "version"},
                "ExpressionAttributeValues": self.encode({":version": rule["version"]})}}])
        except ClientError as error:
            if error.response["Error"]["Code"] == "TransactionCanceledException" and any(
                reason.get("Code") == "ConditionalCheckFailed" for reason in error.response.get("CancellationReasons", [])):
                raise EvaluationConflict("Rule or evaluation state changed") from error
            raise
        return item

    def enqueue(self, event):
        return self.sqs.send_message(QueueUrl=self.queue_url, MessageBody=json_body(event))["MessageId"]

    def acknowledge(self, state):
        # A newer pending event must never be removed by an older sender.
        try:
            self.client.update_item(TableName=self.metrics_table,
                Key=self.encode({key: state[key] for key in ("PK", "SK")}),
                UpdateExpression="SET revision = revision + :one REMOVE pending_event",
                ConditionExpression="revision = :revision AND pending_event.event_id = :event",
                ExpressionAttributeValues=self.encode({":revision": state["revision"], ":one": 1,
                    ":event": state["pending_event"]["event_id"]}))
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            raise EvaluationConflict("Pending alert changed while acknowledging") from error
