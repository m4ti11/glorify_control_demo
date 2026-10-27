from __future__ import annotations

from decimal import Decimal
import json

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.config import Config
from botocore.exceptions import ClientError

from models import KINDS, LIMITS

AWS_CONFIG = Config(connect_timeout=2, read_timeout=3, retries={"mode": "standard", "total_max_attempts": 2})
PK = "CONFIG#DEFAULT"


class Conflict(RuntimeError):
    pass


class NotFound(RuntimeError):
    pass


class ConfigurationRepository:
    def __init__(self, table, client=None):
        self.table = table
        self.client = client or boto3.client("dynamodb", config=AWS_CONFIG)
        self.encoder, self.decoder = TypeSerializer(), TypeDeserializer()

    def encode(self, value):
        def convert(item):
            if isinstance(item, float):
                return Decimal(str(item))
            if isinstance(item, dict):
                return {key: convert(child) for key, child in item.items()}
            if isinstance(item, list):
                return [convert(child) for child in item]
            return item
        return {key: self.encoder.serialize(convert(item)) for key, item in value.items()}

    def decode(self, item):
        return {key: self.decoder.deserialize(value) for key, value in item.items()}

    def key(self, kind, uid):
        return self.encode({"PK": PK, "SK": f"{KINDS[kind]}#{uid}"})

    def state(self):
        result = self.client.get_item(TableName=self.table, Key=self.encode({"PK": PK, "SK": "STATE"}), ConsistentRead=True)
        return self.decode(result.get("Item", {}))

    def get(self, kind, uid):
        result = self.client.get_item(TableName=self.table, Key=self.key(kind, uid), ConsistentRead=True)
        return self.decode(result["Item"]) if result.get("Item") else None

    def page(self, kind, limit=20, start=None):
        args = dict(TableName=self.table, KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
                    ExpressionAttributeValues=self.encode({":pk": PK, ":prefix": KINDS[kind] + "#"}),
                    ConsistentRead=True, Limit=limit)
        if start:
            args["ExclusiveStartKey"] = start
        result = self.client.query(**args)
        return [self.decode(item) for item in result.get("Items", [])], result.get("LastEvaluatedKey")

    def all(self, kind):
        result, key = self.page(kind, 25)
        while key:
            items, key = self.page(kind, 25, key)
            result.extend(items)
        if len(result) > LIMITS[kind]:
            raise RuntimeError("Configuration quota is inconsistent")
        return result

    def mutate(self, kind, uid, item, expected_version, state, importing=None):
        revision = int(state.get("revision", 0))
        notification = kind == "notification-channels"
        update = "SET revision = :next"
        values = {":next": revision + 1, ":old": revision}
        if notification:
            update += ", notifications_initialized = :yes"
            values[":yes"] = True
        transactions = [{"Update": {"TableName": self.table, "Key": self.encode({"PK": PK, "SK": "STATE"}),
                         "UpdateExpression": update,
                         "ConditionExpression": "attribute_not_exists(revision) OR revision = :old",
                         "ExpressionAttributeValues": self.encode(values)}}]
        if importing is not None:
            for record in importing:
                transactions.append({"Put": {"TableName": self.table, "Item": self.encode(record), "ConditionExpression": "attribute_not_exists(PK)"}})
            delta = len(importing)
        else:
            if item is not None:
                operation = {"TableName": self.table, "Item": self.encode(item),
                             "ConditionExpression": "attribute_not_exists(PK)" if expected_version is None else "#version = :version"}
                action = "Put"
            else:
                action = "Delete"
                operation = {"TableName": self.table, "Key": self.key(kind, uid), "ConditionExpression": "#version = :version"}
            if expected_version is not None:
                operation.update(ExpressionAttributeNames={"#version": "version"}, ExpressionAttributeValues=self.encode({":version": expected_version}))
            transactions.append({action: operation})
            delta = 1 if expected_version is None else -1 if item is None else 0
        if delta > 0:
            identities = importing if importing is not None else [item]
            for record in identities:
                transactions.append({"Put": {"TableName": self.table,
                    "Item": self.encode({"PK": PK, "SK": "USED#" + record["SK"], "created_at": record["created_at"]}),
                    "ConditionExpression": "attribute_not_exists(PK)"}})
        if delta:
            condition = "attribute_not_exists(#count) OR #count <= :ceiling" if delta > 0 else "#count >= :one"
            quota_values = {":delta": delta, ":zero": 0}
            quota_values.update({":ceiling": LIMITS[kind] - delta} if delta > 0 else {":one": 1})
            transactions.append({"Update": {"TableName": self.table, "Key": self.encode({"PK": PK, "SK": "QUOTA#" + KINDS[kind]}),
                "UpdateExpression": "SET #count = if_not_exists(#count, :zero) + :delta", "ConditionExpression": condition,
                "ExpressionAttributeNames": {"#count": "count"}, "ExpressionAttributeValues": self.encode(quota_values)}})
        try:
            self.client.transact_write_items(TransactItems=transactions)
        except ClientError as error:
            if error.response["Error"]["Code"] == "TransactionCanceledException" and any(
                reason.get("Code") == "ConditionalCheckFailed" for reason in error.response.get("CancellationReasons", [])):
                raise Conflict("Configuration changed or its quota was reached; refresh and retry") from error
            raise


class SubscriptionsRepository:
    def __init__(self, topic, client=None):
        self.topic = topic
        self.client = client or boto3.client("sns", config=AWS_CONFIG)

    def existing_emails(self):
        # After migration, new installations initialize without a legacy topic.
        if not self.topic:
            return []
        emails = set()
        args = {"TopicArn": self.topic}
        while True:
            result = self.client.list_subscriptions_by_topic(**args)
            emails.update(item["Endpoint"].lower() for item in result.get("Subscriptions", []) if item["Protocol"] in ("email", "email-json"))
            if not result.get("NextToken"):
                return sorted(emails)
            args["NextToken"] = result["NextToken"]

    def status(self, topic):
        result = []
        args = {"TopicArn": topic}
        while True:
            page = self.client.list_subscriptions_by_topic(**args)
            result.extend({"email": item["Endpoint"], "status": "PENDING_CONFIRMATION" if item["SubscriptionArn"] == "PendingConfirmation" else "CONFIRMED"}
                          for item in page.get("Subscriptions", []) if item["Protocol"] == "email")
            if not page.get("NextToken"):
                return result
            args["NextToken"] = page["NextToken"]
