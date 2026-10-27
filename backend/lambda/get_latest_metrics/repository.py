from __future__ import annotations

from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer


class MetricsRepository:
    def __init__(self, table_name: str, dynamodb_client=None) -> None:
        self.table_name = table_name
        self.client = dynamodb_client or boto3.client("dynamodb")
        self.deserializer = TypeDeserializer()

    def get_latest_snapshot(self) -> dict[str, Any] | None:
        result = self.client.get_item(
            TableName=self.table_name,
            Key={"PK": {"S": "METRICS"}, "SK": {"S": "SNAPSHOT#LATEST"}},
            ConsistentRead=True,
        )
        item = result.get("Item")
        if not item:
            return None
        snapshot = {key: self.deserializer.deserialize(value) for key, value in item.items()}
        return snapshot
