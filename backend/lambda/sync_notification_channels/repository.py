from __future__ import annotations

from datetime import datetime, timezone

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.config import Config
from botocore.exceptions import ClientError

CONFIG = Config(connect_timeout=2, read_timeout=3, retries={"mode": "standard", "total_max_attempts": 2})


class SynchronizationRepository:
    def __init__(self, table, prefix, client=None, sns=None):
        self.table, self.prefix = table, prefix
        self.client = client or boto3.client("dynamodb", config=CONFIG)
        self.sns = sns or boto3.client("sns", config=CONFIG)
        self.encoder, self.decoder = TypeSerializer(), TypeDeserializer()

    def decode(self, item):
        return {key: self.decoder.deserialize(value) for key, value in item.items()}

    def current(self, key):
        result = self.client.get_item(TableName=self.table, Key={name: key[name] for name in ("PK", "SK")}, ConsistentRead=True)
        return self.decode(result["Item"]) if result.get("Item") else None

    def topic(self, name):
        return self.sns.create_topic(Name=name)["TopicArn"]

    def delete_topic(self, arn):
        # Only application-owned channel topics may be removed; never the legacy topic.
        if not arn.split(":")[-1].startswith(self.prefix + "-"):
            raise RuntimeError("Unexpected topic namespace")
        self.sns.delete_topic(TopicArn=arn)

    def recipients(self, topic, emails):
        existing = {}
        args = {"TopicArn": topic}
        while True:
            result = self.sns.list_subscriptions_by_topic(**args)
            for sub in result.get("Subscriptions", []):
                if sub["Protocol"] != "email" or sub["Endpoint"].lower() not in emails:
                    # A channel topic is private to this exact recipient set. Stop
                    # before publishing if it was edited outside the configuration.
                    raise RuntimeError("Unexpected subscription on channel topic")
                existing[sub["Endpoint"].lower()] = sub["SubscriptionArn"]
            if not result.get("NextToken"):
                break
            args["NextToken"] = result["NextToken"]
        statuses = []
        for email in emails:
            arn = existing.get(email)
            if arn is None:
                arn = self.sns.subscribe(TopicArn=topic, Protocol="email", Endpoint=email, ReturnSubscriptionArn=True)["SubscriptionArn"]
                status = "PENDING_CONFIRMATION"
            else:
                status = "PENDING_CONFIRMATION" if arn == "PendingConfirmation" else "CONFIRMED"
            statuses.append({"email": email, "status": status})
        return statuses

    def save_status(self, item, delivery):
        values = {":version": self.encoder.serialize(item["version"]), ":delivery": self.encoder.serialize(delivery)}
        try:
            self.client.update_item(TableName=self.table,
                Key={"PK": {"S": item["PK"]}, "SK": {"S": item["SK"]}},
                UpdateExpression="SET delivery = :delivery", ConditionExpression="#version = :version",
                ExpressionAttributeNames={"#version": "version"}, ExpressionAttributeValues=values)
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            raise RuntimeError("Configuration changed during synchronization") from error

    def synced(self, item, topic, recipients, status):
        self.save_status(item, {"status": status, "topic_arn": topic, "recipients": recipients,
                               "synced_version": item["version"], "synced_at": datetime.now(timezone.utc).isoformat()})
