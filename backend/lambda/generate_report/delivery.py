"""Persist the PDF and optionally mail it to confirmed notification recipients."""
from email.message import EmailMessage
from email.policy import SMTP
import gzip
import json
from hashlib import sha256
from html import escape
import os
import time
from uuid import uuid4

import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config
from botocore.exceptions import ClientError

from report_pdf import render_pdf

CONFIG = Config(connect_timeout=2, read_timeout=10, retries={"mode": "standard", "total_max_attempts": 2})


class ReportPublisher:
    def __init__(self, bucket, jobs_table, configuration_table, sender="", email_enabled=False, s3=None, dynamodb=None, sns=None, ses=None, topic_prefix=""):
        self.bucket, self.jobs_table, self.configuration_table = bucket, jobs_table, configuration_table
        self.sender, self.email_enabled, self.topic_prefix = sender, email_enabled, topic_prefix
        self.s3 = s3 or boto3.client("s3", config=CONFIG)
        self.dynamodb = dynamodb or boto3.client("dynamodb", config=CONFIG)
        self.sns = sns or boto3.client("sns", config=CONFIG)
        self.ses = ses or boto3.client("ses", config=CONFIG)

    @classmethod
    def from_environment(cls):
        return cls(os.environ["GENERATED_REPORTS_BUCKET"], os.environ["REPORT_JOBS_TABLE_NAME"], os.environ["CONFIGURATION_TABLE_NAME"],
                   sender=os.environ.get("REPORT_EMAIL_SENDER", ""), email_enabled=os.environ.get("REPORT_EMAIL_ENABLED") == "true",
                   topic_prefix=os.environ["CHANNEL_TOPIC_ARN_PREFIX"])

    def recipients(self):
        decoder = TypeDeserializer()
        recipients, cursor = set(), None
        while True:
            args = {"TableName": self.configuration_table, "ConsistentRead": True,
                    "KeyConditionExpression": "PK = :pk AND begins_with(SK, :prefix)",
                    "ExpressionAttributeValues": {":pk": {"S": "CONFIG#DEFAULT"}, ":prefix": {"S": "NOTIFICATION_CHANNEL#"}}}
            if cursor:
                args["ExclusiveStartKey"] = cursor
            page = self.dynamodb.query(**args)
            for raw in page.get("Items", []):
                item = {key: decoder.deserialize(value) for key, value in raw.items()}
                if not item.get("data", {}).get("enabled"):
                    continue
                delivery = item.get("delivery", {})
                if delivery.get("status") != "SYNCED" or delivery.get("synced_version") != item.get("version"):
                    raise RuntimeError("Report notification channels are not synchronized")
                topic = delivery.get("topic_arn", "")
                if not self.topic_prefix or not topic.startswith(self.topic_prefix):
                    raise RuntimeError("Report notification topic is outside the configured prefix")
                token = None
                while True:
                    params = {"TopicArn": topic}
                    if token:
                        params["NextToken"] = token
                    subscriptions = self.sns.list_subscriptions_by_topic(**params)
                    for sub in subscriptions.get("Subscriptions", []):
                        if sub.get("Protocol") == "email" and sub.get("SubscriptionArn", "").startswith("arn:") and sub.get("Endpoint") in item["data"]["emails"]:
                            recipients.add(sub["Endpoint"])
                    token = subscriptions.get("NextToken")
                    if not token:
                        break
            cursor = page.get("LastEvaluatedKey")
            if not cursor:
                return sorted(recipients)

    def publish(self, report):
        # Lazily copy legacy JSON into the shared bucket using a conditional
        # write. Historical objects are preserved and concurrent reports win.
        system, day = report["system_id"], report["report_date"]
        json_key = f"generated/operational/{system}/{day.replace('-', '/')}.json.gz"
        try:
            self.s3.put_object(Bucket=self.bucket, Key=json_key, Body=gzip.compress(json.dumps(report, ensure_ascii=False, allow_nan=False).encode(), mtime=0),
                               ContentType="application/json", ContentEncoding="gzip", ServerSideEncryption="AES256", IfNoneMatch="*")
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") not in ("PreconditionFailed", "412"):
                raise
        pdf = render_pdf(report)
        system, day = report["system_id"], report["report_date"]
        key = f"generated/operational/{system}/{day.replace('-', '/')}.pdf"
        try:
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=pdf, ContentType="application/pdf", ServerSideEncryption="AES256",
                               IfNoneMatch="*", Metadata={"system-id": system, "report-date": day})
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") not in ("PreconditionFailed", "412"):
                raise
        if not self.email_enabled:
            return
        if not self.sender:
            raise RuntimeError("Report email sender is not configured")
        for recipient in self.recipients():
            self.send(report, pdf, recipient)

    def send(self, report, pdf, recipient):
        system, day = report["system_id"], report["report_date"]
        owner, now = str(uuid4()), int(time.time())
        key = {"PK": {"S": f"SYSTEM#{system}"}, "SK": {"S": f"REPORT_MAIL#{day}#{sha256(recipient.lower().encode()).hexdigest()}"}}
        try:
            self.dynamodb.update_item(TableName=self.jobs_table, Key=key,
                UpdateExpression="SET #s=:sending, owner_token=:owner, lease_expires_at=:lease",
                ConditionExpression="attribute_not_exists(PK) OR #s=:retry OR (#s=:sending AND lease_expires_at < :now)",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":sending": {"S": "SENDING"}, ":retry": {"S": "RETRYABLE"}, ":owner": {"S": owner}, ":lease": {"N": str(now + 300)}, ":now": {"N": str(now)}})
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            item = self.dynamodb.get_item(TableName=self.jobs_table, Key=key, ConsistentRead=True).get("Item", {})
            if item.get("status", {}).get("S") == "SENT":
                return
            raise RuntimeError("Report mail delivery is already in progress") from None
        try:
            message = EmailMessage(policy=SMTP)
            message["From"], message["To"] = self.sender, recipient
            message["Subject"] = f"Glorify Control | Reporte {system} | {day}"
            summary = report["analysis"]["summary"]
            message.set_content(f"Operational report for {system} on {day}.\n\n{summary}\n\nThe complete report is attached as a PDF.\n")
            message.add_alternative(f"<h1>Daily operational report</h1><p>{escape(system)} · {escape(day)}</p><p>{escape(summary).replace(chr(10), '<br>')}</p><p>The complete report is attached as a PDF.</p>", subtype="html")
            message.add_attachment(pdf, maintype="application", subtype="pdf", filename=f"glorify-{system}-{day}.pdf")
            result = self.ses.send_raw_email(Source=self.sender, Destinations=[recipient], RawMessage={"Data": message.as_bytes()})
            self.dynamodb.update_item(TableName=self.jobs_table, Key=key,
                UpdateExpression="SET #s=:sent, message_id=:message REMOVE lease_expires_at",
                ConditionExpression="owner_token=:owner", ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":sent": {"S": "SENT"}, ":message": {"S": result["MessageId"]}, ":owner": {"S": owner}})
        except Exception:
            # A send accepted by SES followed by a failed status write can still
            # duplicate on retry. SES does not provide exactly-once mail delivery.
            self.dynamodb.update_item(TableName=self.jobs_table, Key=key,
                UpdateExpression="SET #s=:retry REMOVE lease_expires_at", ConditionExpression="owner_token=:owner",
                ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":retry": {"S": "RETRYABLE"}, ":owner": {"S": owner}})
            raise
