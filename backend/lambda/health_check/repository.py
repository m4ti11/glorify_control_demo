from __future__ import annotations

import json

import boto3
from boto3.dynamodb.conditions import Attr


class HealthRepository:
    def __init__(self, metrics_table_name, state_table_name, events_queue_url,
                 dynamodb=None, sqs=None):
        dynamodb = dynamodb or boto3.resource("dynamodb")
        self.metrics = dynamodb.Table(metrics_table_name)
        self.state = dynamodb.Table(state_table_name)
        self.events_queue_url = events_queue_url
        self.sqs = sqs or boto3.client("sqs")

    def get_latest_snapshot(self):
        response = self.metrics.get_item(
            Key={"PK": "METRICS", "SK": "SNAPSHOT#LATEST"},
            ConsistentRead=True,
        )
        return response.get("Item")

    def get_state(self, system_id):
        response = self.state.get_item(
            Key={"system_id": system_id},
            ConsistentRead=True,
        )
        return response.get("Item")

    def put_state(self, item, expected_version):
        item = dict(item)
        item["version"] = (expected_version or 0) + 1
        condition = Attr("system_id").not_exists() if expected_version is None else Attr("version").eq(expected_version)
        self.state.put_item(Item=item, ConditionExpression=condition)
        return item

    def enqueue_event(self, event_body):
        self.sqs.send_message(
            QueueUrl=self.events_queue_url,
            MessageBody=json.dumps(event_body, separators=(",", ":"), sort_keys=True),
        )
