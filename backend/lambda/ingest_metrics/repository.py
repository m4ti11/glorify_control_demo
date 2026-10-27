from __future__ import annotations

from decimal import Decimal
from datetime import datetime
from typing import Any
from urllib.parse import quote

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError


def _to_dynamodb_value(value: Any) -> Any:
    """DynamoDB rejects Python floats; convert them without losing decimal precision."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _to_dynamodb_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_dynamodb_value(item) for item in value]
    return value


def _resource_key(resource_type: str, resource_id: str) -> str:
    return f"RESOURCE#{resource_type}#{quote(resource_id, safe='._-')}"


class MetricsRepository:
    def __init__(self, table_name: str, dynamodb_client=None) -> None:
        self.table_name = table_name
        self.client = dynamodb_client or boto3.client("dynamodb")
        self.serializer = TypeSerializer()

    def _serialize_item(self, item: dict[str, Any]) -> dict[str, Any]:
        item = _to_dynamodb_value(item)
        return {name: self.serializer.serialize(value) for name, value in item.items()}

    def save_resource_metrics(
        self, system_id: str, measured_at: str, received_at: str, resources: list[dict[str, Any]]
    ) -> bool:
        partition_key = f"SYSTEM#{system_id}"
        # Retention starts at the AWS receipt time, never the agent's clock.
        expires_at = int(datetime.fromisoformat(received_at.replace("Z", "+00:00")).timestamp()) + 7 * 24 * 60 * 60
        transaction_items: list[dict[str, Any]] = []
        for resource in resources:
            resource_type = resource["resource_type"]
            resource_id = resource["resource_id"]
            resource_prefix = _resource_key(resource_type, resource_id)
            common = {
                "system_id": system_id,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "measured_at": measured_at,
                "received_at": received_at,
                "metrics": resource["metrics"],
            }
            historical = {
                "PK": partition_key,
                "SK": f"{resource_prefix}#METRIC#{measured_at}",
                "record_type": "metric",
                "expires_at": expires_at,
                **common,
            }
            transaction_items.extend(
                [
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": self._serialize_item(historical),
                            "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                        }
                    }
                ]
            )
        latest_snapshot = {
            "PK": "METRICS",
            "SK": "SNAPSHOT#LATEST",
            "record_type": "latest_snapshot",
            "system_id": system_id,
            "measured_at": measured_at,
            "received_at": received_at,
            "resources": resources,
        }
        created = True
        try:
            self.client.transact_write_items(TransactItems=transaction_items)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "TransactionCanceledException":
                raise
            reasons = error.response.get("CancellationReasons", [])
            # Every action is a conditional historical Put. Incomplete or mixed
            # reasons are ambiguous and must never acknowledge an unpersisted batch.
            duplicate = (len(reasons) == len(transaction_items)
                         and any(r.get("Code") == "ConditionalCheckFailed" for r in reasons)
                         and all(r.get("Code") in {"None", "ConditionalCheckFailed"} for r in reasons))
            if not duplicate:
                raise
            created = False
        try:
            # Also repair this boundary on replay after history succeeded but
            # the snapshot write failed. An old sample still retains its history.
            self.client.put_item(TableName=self.table_name, Item=self._serialize_item(latest_snapshot),
                ConditionExpression="attribute_not_exists(measured_at) OR measured_at <= :m",
                ExpressionAttributeValues={":m": {"S": measured_at}})
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
        return created
