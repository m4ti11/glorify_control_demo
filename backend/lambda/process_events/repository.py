from __future__ import annotations

import json
from decimal import Decimal
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.config import Config
from botocore.exceptions import ClientError
from notification import event_presentation, render_notification, render_html_notification


AWS_CONFIG = Config(connect_timeout=2, read_timeout=3, retries={"mode": "standard", "total_max_attempts": 2})


class EventConflict(ValueError):
    """A previously accepted event ID was reused with different content."""


class EventBusy(RuntimeError):
    """Another invocation owns the publication lease."""


class EventsRepository:
    def __init__(self, table_name: str, client=None, email_recipient=None):
        self.table_name = table_name
        self.email_recipient = email_recipient
        self.client = client or boto3.client("dynamodb", config=AWS_CONFIG)
        self.serializer = TypeSerializer()
        self.deserializer = TypeDeserializer()

    def serialize(self, item):
        item = json.loads(json.dumps(item, allow_nan=False), parse_float=Decimal)
        return {key: self.serializer.serialize(value) for key, value in item.items()}

    def key(self, event_id):
        return {"PK": {"S": f"EVENT#{event_id}"}, "SK": {"S": "CONTROL"}}

    def get_control(self, event_id):
        response = self.client.get_item(TableName=self.table_name, Key=self.key(event_id), ConsistentRead=True)
        return {key: self.deserializer.deserialize(value) for key, value in response.get("Item", {}).items()}

    def ensure_event(self, item, payload_hash):
        event_id = item["event_id"]
        control = {
            "PK": f"EVENT#{event_id}", "SK": "CONTROL", "record_type": "event_control",
            "payload_hash": payload_hash, "notification_status": "PENDING",
            "email_status": "PENDING", "email_recipient": self.email_recipient,
            "event_pk": item["PK"], "event_sk": item["SK"],
        }
        try:
            self.client.transact_write_items(TransactItems=[
                {"Put": {"TableName": self.table_name, "Item": self.serialize(record),
                         "ConditionExpression": "attribute_not_exists(PK)"}}
                for record in (control, item)
            ])
            return control
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            # Do not mistake throttling, validation or transaction conflicts for a duplicate.
            existing = self.get_control(event_id)
            if not existing:
                raise
            if existing["payload_hash"] != payload_hash:
                raise EventConflict("event_id already exists with different content") from error
            return existing

    def acquire(self, event_id, owner, now):
        try:
            self.client.update_item(
                TableName=self.table_name, Key=self.key(event_id),
                UpdateExpression="SET lease_owner = :owner, lease_until = :until",
                ConditionExpression="notification_status = :pending AND (attribute_not_exists(lease_until) OR lease_until < :now)",
                ExpressionAttributeValues=self.serialize({":owner": owner, ":until": now + 60, ":now": now, ":pending": "PENDING"}),
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            # A simultaneous invocation may have just finished publishing.
            if self.get_control(event_id).get("notification_status") in ("PUBLISHED", "SKIPPED"):
                return False
            raise EventBusy("event publication is already in progress") from error
        return True

    def event(self, control):
        response = self.client.get_item(
            TableName=self.table_name, ConsistentRead=True,
            Key=self.serialize({"PK": control["event_pk"], "SK": control["event_sk"]}),
        )
        if "Item" not in response:
            raise RuntimeError("Stored event is missing")
        return {key: self.deserializer.deserialize(value) for key, value in response["Item"].items()}

    def complete(self, event_id, owner, message_id, published_at):
        self.client.update_item(
            TableName=self.table_name, Key=self.key(event_id),
            UpdateExpression="SET notification_status = :published, sns_message_id = :message, published_at = :at, email_status = :published, email_recipient = :recipient REMOVE lease_owner, lease_until",
            ConditionExpression="notification_status = :pending AND lease_owner = :owner",
            ExpressionAttributeValues=self.serialize({":published": "PUBLISHED", ":pending": "PENDING", ":owner": owner,
                                                      ":message": message_id, ":at": published_at, ":recipient": self.email_recipient}),
        )

    def record_push(self, event_id, owner, message_id, published_at):
        self.client.update_item(
            TableName=self.table_name, Key=self.key(event_id),
            UpdateExpression="SET push_status = :published, push_message_id = :message, push_published_at = :at",
            ConditionExpression="notification_status = :pending AND lease_owner = :owner",
            ExpressionAttributeValues=self.serialize({":published": "PUBLISHED", ":pending": "PENDING", ":owner": owner,
                                                      ":message": message_id, ":at": published_at}),
        )

    def suppress(self, event_id, owner, reason):
        # A previously attempted event can already have accepted deliveries.
        # Retain that history while preventing further deliveries on retry.
        current = self.get_control(event_id)
        delivered = [value for value in current.get("publications", {}).values()
                     if value.get("status") == "PUBLISHED" or value.get("recipients")]
        recipients = sorted({email for value in delivered for email in value.get("emails", [])})
        accepted_at = [value["published_at"] for value in delivered if value.get("published_at")]
        accepted_at.extend(receipt["accepted_at"] for value in delivered for receipt in value.get("recipients", {}).values())
        email_status = ("ACCEPTED" if all(value.get("transport") == "SES" for value in delivered) else "PUBLISHED") if delivered else "SUPPRESSED"
        self.client.update_item(
            TableName=self.table_name, Key=self.key(event_id),
            UpdateExpression="SET notification_status = :status, email_status = :email, notification_suppression_reason = :reason, "
                             "email_recipients = :recipients, email_recipient = :recipient, published_at = :at, "
                             "push_status = if_not_exists(push_status, :suppressed) REMOVE lease_owner, lease_until",
            ConditionExpression="notification_status = :pending AND lease_owner = :owner",
            ExpressionAttributeValues=self.serialize({":status": "PUBLISHED" if delivered else "SKIPPED", ":email": email_status,
                ":reason": reason, ":recipients": recipients, ":recipient": ", ".join(recipients),
                ":at": max(accepted_at, default=None), ":suppressed": "SUPPRESSED", ":pending": "PENDING", ":owner": owner}),
        )


    def assign_targets(self, event_id, owner, targets):
        self.client.update_item(TableName=self.table_name, Key=self.key(event_id),
            UpdateExpression="SET notification_targets = :targets, publications = :empty",
            ConditionExpression="notification_status = :pending AND lease_owner = :owner AND attribute_not_exists(notification_targets)",
            ExpressionAttributeValues=self.serialize({":targets": targets, ":empty": {}, ":pending": "PENDING", ":owner": owner}))

    def record_publication(self, event_id, owner, channel_id, publication):
        self.client.update_item(TableName=self.table_name, Key=self.key(event_id),
            UpdateExpression="SET publications.#channel = :publication",
            ConditionExpression="notification_status = :pending AND lease_owner = :owner",
            ExpressionAttributeNames={"#channel": channel_id},
            ExpressionAttributeValues=self.serialize({":publication": publication, ":pending": "PENDING", ":owner": owner}))

    def complete_channels(self, event_id, owner, publications):
        published = [value for value in publications.values() if value["status"] == "PUBLISHED"]
        recipients = sorted({email for value in published for email in value["emails"]})
        email_status = "ACCEPTED" if published and all(value.get("transport") == "SES" for value in published) else "PUBLISHED" if published else "NOT_CONFIGURED"
        self.client.update_item(TableName=self.table_name, Key=self.key(event_id),
            UpdateExpression="SET notification_status = :status, email_status = :email, email_recipients = :recipients, email_recipient = :recipient, published_at = :at REMOVE lease_owner, lease_until",
            ConditionExpression="notification_status = :pending AND lease_owner = :owner",
            ExpressionAttributeValues=self.serialize({":status": "PUBLISHED" if published else "SKIPPED", ":email": email_status,
                ":recipients": recipients, ":recipient": ", ".join(recipients), ":at": max((value["published_at"] for value in published), default=None),
                ":pending": "PENDING", ":owner": owner}))


class AlertsRepository:
    def __init__(self, topic_arn: str, client=None):
        self.topic_arn = topic_arn
        self.client = client or boto3.client("sns", config=AWS_CONFIG)

    def publish(self, item, topic_arn=None):
        # SNS channels contain email subscriptions. Keep the full original event
        # in DynamoDB; publish a bounded presentation rather than database keys.
        subject, message = render_notification(item)
        return self.client.publish(
            TopicArn=topic_arn or self.topic_arn,
            Subject=subject,
            Message=message,
            MessageAttributes={"event_id": {"DataType": "String", "StringValue": item["event_id"]},
                               "event_type": {"DataType": "String", "StringValue": item["event_type"]}},
        )["MessageId"]


class EventEmailRepository:
    """SES delivery; SNS subscriptions remain the source of recipient consent."""

    def __init__(self, sender, topic_prefix, client=None, sns=None):
        if not sender or not topic_prefix:
            raise ValueError("An email sender and channel topic prefix are required")
        self.sender, self.topic_prefix = sender, topic_prefix
        self.client = client or boto3.client("ses", config=AWS_CONFIG)
        self.sns = sns or boto3.client("sns", config=AWS_CONFIG)

    def recipients(self, target):
        if not target["topic_arn"].startswith(self.topic_prefix):
            raise RuntimeError("Notification topic is outside the configured prefix")
        confirmed, token = set(), None
        allowed = {email.lower() for email in target["emails"]}
        while True:
            args = {"TopicArn": target["topic_arn"]}
            if token:
                args["NextToken"] = token
            page = self.sns.list_subscriptions_by_topic(**args)
            for sub in page.get("Subscriptions", []):
                email = sub.get("Endpoint", "").lower()
                if sub.get("Protocol") == "email" and sub.get("SubscriptionArn", "").startswith("arn:") and email in allowed:
                    confirmed.add(email)
            token = page.get("NextToken")
            if not token:
                return sorted(confirmed)

    def send(self, item, recipient):
        subject, text = render_notification(item)
        return self.client.send_email(
            Source=self.sender, Destination={"ToAddresses": [recipient]},
            Message={"Subject": {"Data": subject, "Charset": "UTF-8"},
                     "Body": {"Text": {"Data": text, "Charset": "UTF-8"},
                              "Html": {"Data": render_html_notification(item), "Charset": "UTF-8"}}},
        )["MessageId"]


class PushAlertsRepository(AlertsRepository):
    """A separate SNS fan-out for mobile delivery adapters, without email recipients."""

    def publish(self, item):
        view = event_presentation(item)
        return self.client.publish(
            TopicArn=self.topic_arn, Subject=view["subject"],
            Message=f"{view['summary']}\n{view['system']} · {view['resource']}\n{view['time']}\nEvento: {item['event_id']}",
            MessageAttributes={name: {"DataType": "String", "StringValue": value} for name, value in {
                "event_id": item["event_id"], "event_type": item["event_type"],
                "system_id": item["system_id"], "severity": view["status"],
            }.items()},
        )["MessageId"]


class NotificationConfigurationRepository:
    def __init__(self, table, client=None):
        self.table = table
        self.client = client or boto3.client("dynamodb", config=AWS_CONFIG)
        self.decoder = TypeDeserializer()

    def rule_active(self, rule):
        if rule is None:
            return True
        result = self.client.get_item(TableName=self.table, ConsistentRead=True,
            Key={"PK": {"S": "CONFIG#DEFAULT"}, "SK": {"S": "ALERT_RULE#" + rule["rule_id"]}})
        item = {key: self.decoder.deserialize(value) for key, value in result.get("Item", {}).items()}
        until = item.get("data", {}).get("maintenance_until")
        return bool(item and item["version"] == rule["rule_version"] and item["data"]["enabled"] and
                    (not until or datetime.fromisoformat(until.replace("Z", "+00:00")) < datetime.now(timezone.utc)))

    def targets(self, channel_ids=None, rule=None):
        if channel_ids == [] or not self.rule_active(rule):
            return []
        state = self.client.get_item(TableName=self.table, Key={"PK": {"S": "CONFIG#DEFAULT"}, "SK": {"S": "STATE"}}, ConsistentRead=True).get("Item", {})
        if not state.get("notifications_initialized", {}).get("BOOL"):
            raise RuntimeError("Import notification settings before processing events")
        result = self.client.query(TableName=self.table, ConsistentRead=True, Limit=11,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={":pk": {"S": "CONFIG#DEFAULT"}, ":prefix": {"S": "NOTIFICATION_CHANNEL#"}})
        if result.get("LastEvaluatedKey") or len(result.get("Items", [])) > 10:
            raise RuntimeError("Notification quota exceeded")
        items = [{key: self.decoder.deserialize(value) for key, value in item.items()} for item in result.get("Items", [])]
        targets = []
        for item in items:
            if not item["data"]["enabled"] or (channel_ids is not None and item["id"] not in channel_ids):
                continue
            delivery = item.get("delivery", {})
            if delivery.get("synced_version") != item["version"] or delivery.get("status") != "SYNCED":
                raise RuntimeError("Notification configuration is not synchronized")
            target = {"id": item["id"], "emails": item["data"]["emails"], "topic_arn": delivery["topic_arn"]}
            if rule is not None:
                target["rule"] = {"rule_id": rule["rule_id"], "rule_version": int(rule["rule_version"])}
            targets.append(target)
        return targets

    def still_active(self, target):
        result = self.client.get_item(TableName=self.table,
            Key={"PK": {"S": "CONFIG#DEFAULT"}, "SK": {"S": "NOTIFICATION_CHANNEL#" + target["id"]}}, ConsistentRead=True)
        item = {key: self.decoder.deserialize(value) for key, value in result.get("Item", {}).items()}
        return bool(item and item["data"]["enabled"] and item["data"]["emails"] == target["emails"] and
                    self.rule_active(target.get("rule")))
