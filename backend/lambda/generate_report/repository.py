from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
import io
import json
import time
import math

import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config
from botocore.exceptions import ClientError

from models import (MAX_EVENT_ITEMS, MAX_EVENT_QUERY_ITEMS, MAX_METRIC_ITEMS, MAX_OUTPUT_BYTES,
                    MAX_PG_BYTES, OperationalReport, event_bounds, metric_timestamp)


AWS_CONFIG = Config(connect_timeout=2, read_timeout=5, retries={"mode": "standard", "total_max_attempts": 2})
HOST_PREFIX = "RESOURCE#HOST#host#METRIC#"
MAX_CONDITIONAL_WRITE_ATTEMPTS = 3
CONDITIONAL_WRITE_RETRY_DELAY_SECONDS = 0.05


def _date_time(value):
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def sample_coverage(readings, start, end, interval_seconds=120, truncated=False):
    if interval_seconds < 1:
        raise ValueError("Expected heartbeat interval must be positive")
    expected = math.ceil((end - start).total_seconds() / interval_seconds)
    instants = sorted({_date_time(row["measured_at"]) for row in readings
                       if start <= _date_time(row["measured_at"]) < end})
    present = len({int((at - start).total_seconds() // interval_seconds) for at in instants})
    boundaries = [start, *instants, end]
    largest_gap = max((b - a).total_seconds() for a, b in zip(boundaries, boundaries[1:]))
    percentage = present / expected * 100
    return {"expected_interval_seconds": interval_seconds, "expected_intervals": expected,
            "present_intervals": present, "percentage": round(percentage, 2),
            "largest_gap_seconds": largest_gap, "sufficient": not truncated and percentage >= 95 and largest_gap <= 600,
            "is_lower_bound": truncated}


class ReportSources:
    def __init__(self, metrics_table, events_table, pg_bucket, dynamodb=None, s3=None, expected_heartbeat_seconds=120):
        self.expected_heartbeat_seconds = expected_heartbeat_seconds
        self.metrics_table, self.events_table = metrics_table, events_table
        self.pg_bucket = pg_bucket
        self.dynamodb = dynamodb or boto3.client("dynamodb", config=AWS_CONFIG)
        self.s3 = s3 or boto3.client("s3", config=AWS_CONFIG)
        self.decoder = TypeDeserializer()

    def _decode(self, item):
        return {key: self.decoder.deserialize(value) for key, value in item.items()}

    def host_metrics(self, system_id, start, end_exclusive, stale_after_seconds):
        first = HOST_PREFIX + metric_timestamp(start)
        last = HOST_PREFIX + metric_timestamp(end_exclusive - timedelta(microseconds=1))
        result, key = [], None
        truncated = False
        while True:
            args = {
                "TableName": self.metrics_table, "ConsistentRead": True, "ScanIndexForward": True,
                "Limit": min(250, MAX_METRIC_ITEMS - len(result)),
                "KeyConditionExpression": "#pk = :pk AND #sk BETWEEN :start AND :end",
                "ExpressionAttributeNames": {"#pk": "PK", "#sk": "SK", "#metrics": "metrics"},
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"SYSTEM#{system_id}"}, ":start": {"S": first}, ":end": {"S": last},
                },
                "ProjectionExpression": "measured_at, #metrics",
            }
            if key:
                args["ExclusiveStartKey"] = key
            page = self.dynamodb.query(**args)
            result.extend(self._decode(item) for item in page.get("Items", []))
            key = page.get("LastEvaluatedKey")
            if not key:
                break
            if len(result) >= MAX_METRIC_ITEMS:
                truncated = True
                break
        rows = [{"evidence_ref": f"metric:{item['measured_at']}", **item} for item in result]
        coverage = sample_coverage(result, start, end_exclusive, self.expected_heartbeat_seconds, truncated)
        if not rows:
            return {"coverage": coverage, "status": "incomplete" if truncated else "missing", "count": 0, "omitted_count": 0,
                    "observed_from": None, "observed_to": None, "data": rows}
        newest = max(_date_time(item["measured_at"]) for item in result)
        oldest = min(_date_time(item["measured_at"]) for item in result)
        stale = (end_exclusive - newest).total_seconds() > stale_after_seconds
        incomplete = truncated or not coverage["sufficient"]
        issues = (["stale"] if stale else []) + (["incomplete"] if incomplete else [])
        return {"coverage": coverage, "status": "stale" if stale else "incomplete" if incomplete else "available", "issues": issues,
                "count": len(rows), "omitted_count": 1 if truncated else 0,
                "observed_from": oldest.isoformat(), "observed_to": newest.isoformat(), "data": rows}

    def events(self, system_id, start, end_exclusive, stale_after_seconds):
        lower, upper = event_bounds(start, end_exclusive)
        selected, key = [], None
        truncated = False
        while True:
            args = {
                "TableName": self.events_table,
                "KeyConditionExpression": "#pk = :pk AND #sort BETWEEN :start AND :end",
                "ExpressionAttributeNames": {"#pk": "PK", "#sort": "SK"},
                "ExpressionAttributeValues": {":pk": {"S": f"SYSTEM#{system_id}"}, ":start": {"S": lower}, ":end": {"S": upper}},
                "ScanIndexForward": True, "Limit": min(100, MAX_EVENT_QUERY_ITEMS - len(selected)),
            }
            if key:
                args["ExclusiveStartKey"] = key
            page = self.dynamodb.query(**args)
            selected.extend(self._decode(item) for item in page.get("Items", []))
            key = page.get("LastEvaluatedKey")
            if not key:
                break
            if len(selected) >= MAX_EVENT_QUERY_ITEMS:
                truncated = True
                break
        selected.sort(key=lambda item: (_date_time(item["occurred_at"]), item.get("event_id", "")))
        heartbeats = [item for item in selected if item.get("event_type") == "host.extended_metrics"]
        activity = [item for item in selected if item.get("event_type") != "host.extended_metrics"]
        latest_heartbeat = max(heartbeats, key=lambda item: _date_time(item["occurred_at"])) if heartbeats else None
        omitted = max(0, len(activity) - MAX_EVENT_ITEMS)
        activity = activity[:MAX_EVENT_ITEMS]
        rows = [{"evidence_ref": f"event:{item['event_id']}", **{
            key: item[key] for key in ("event_id", "event_type", "source", "occurred_at", "received_at", "subject", "details")
            if key in item
        }} for item in activity]
        incomplete = truncated or omitted > 0
        return {"status": "incomplete" if incomplete else "available", "issues": ["incomplete"] if incomplete else [],
                "count": len(rows), "observed_count": len(selected), "omitted_count": omitted + (1 if truncated else 0),
                "last_heartbeat_at": latest_heartbeat.get("occurred_at") if latest_heartbeat else None,
                "last_received_at": latest_heartbeat.get("received_at") if latest_heartbeat else None, "data": rows}

    def postgres(self, system_id, report_date, stale_after_seconds, now=None):
        key = f"raw/pg-statements/{system_id}/{report_date:%Y/%m/%d}.json.gz"
        try:
            result = self.s3.get_object(Bucket=self.pg_bucket, Key=key)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404", "NotFound"):
                return {"status": "missing", "count": 0, "report_date": report_date.isoformat(), "captured_at": None, "received_at": None, "data": None}
            if code in ("AccessDenied", "403"):
                listed = self.s3.list_objects_v2(Bucket=self.pg_bucket, Prefix=key, MaxKeys=1)
                if not any(item.get("Key") == key for item in listed.get("Contents", [])):
                    return {"status": "missing", "count": 0, "report_date": report_date.isoformat(),
                            "captured_at": None, "received_at": None, "data": None}
            raise
        body = result["Body"]
        try:
            compressed = body.read(MAX_PG_BYTES + 4097)
            if len(compressed) > MAX_PG_BYTES + 4096:
                return {"status": "incomplete", "count": 0, "report_date": report_date.isoformat(), "captured_at": None,
                        "received_at": result.get("Metadata", {}).get("received-at"), "data": None}
            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as archive:
                raw = archive.read(MAX_PG_BYTES + 1)
            if len(raw) > MAX_PG_BYTES:
                return {"status": "incomplete", "count": 0, "report_date": report_date.isoformat(), "captured_at": None,
                        "received_at": result.get("Metadata", {}).get("received-at"), "data": None}
            report = json.loads(raw)
        except (OSError, EOFError, ValueError, UnicodeDecodeError) as error:
            raise RuntimeError("PostgreSQL report is unreadable") from error
        finally:
            body.close()
        details = report.get("details", {})
        if report.get("system_id") != system_id or details.get("report_date") != report_date.isoformat():
            raise RuntimeError("PostgreSQL report identity does not match requested key")
        captured = details.get("captured_at")
        received = result.get("Metadata", {}).get("received-at")
        if not captured:
            status = "incomplete"
            issues = ["incomplete"]
        else:
            age = ((now or datetime.now(timezone.utc)) - _date_time(captured)).total_seconds()
            status = "stale" if age > stale_after_seconds else "available"
            issues = ["stale"] if status == "stale" else []
        if details.get("available") is False:
            status = "incomplete"
            issues.append("incomplete")
        reports = details.get("reports") or [details]
        if any(section.get("available") is False for section in reports):
            status = "incomplete"
            issues.append("incomplete")
        issues = list(dict.fromkeys(issues))
        if "stale" in issues:
            status = "stale"
        return {"status": status, "issues": issues, "count": len(reports), "report_date": report_date.isoformat(), "captured_at": captured,
                "received_at": received, "data": report}


class ClaimRepository:
    def __init__(self, table_name, client=None):
        self.table_name = table_name
        self.client = client or boto3.client("dynamodb", config=AWS_CONFIG)

    @staticmethod
    def key(system_id, report_date):
        return {"PK": {"S": f"SYSTEM#{system_id}"}, "SK": {"S": f"REPORT#{report_date}"}}

    def acquire(self, system_id, report_date, owner, now_epoch, lease_seconds):
        try:
            self.client.update_item(
                TableName=self.table_name, Key=self.key(system_id, report_date),
                UpdateExpression="SET #status=:processing, owner_token=:owner, lease_expires_at=:lease, attempts=if_not_exists(attempts,:zero)+:one",
                ConditionExpression="attribute_not_exists(PK) OR #status=:retryable OR (#status=:processing AND lease_expires_at < :now)",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":processing": {"S": "PROCESSING"}, ":retryable": {"S": "RETRYABLE"},
                    ":owner": {"S": owner}, ":lease": {"N": str(now_epoch + lease_seconds)},
                    ":now": {"N": str(now_epoch)}, ":zero": {"N": "0"}, ":one": {"N": "1"}},
            )
            return "acquired"
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            current = self.client.get_item(TableName=self.table_name, Key=self.key(system_id, report_date), ConsistentRead=True).get("Item", {})
            if current.get("status", {}).get("S") == "COMPLETE":
                return "complete"
            return "busy"

    def complete(self, system_id, report_date, owner, report_key):
        self.client.update_item(TableName=self.table_name, Key=self.key(system_id, report_date),
            UpdateExpression="SET #status=:complete, report_s3_key=:key, completed_at=:at REMOVE lease_expires_at",
            ConditionExpression="#status=:processing AND owner_token=:owner",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":complete": {"S": "COMPLETE"}, ":processing": {"S": "PROCESSING"},
                ":key": {"S": report_key}, ":at": {"S": datetime.now(timezone.utc).isoformat()}, ":owner": {"S": owner}})

    def retryable(self, system_id, report_date, owner):
        self.client.update_item(TableName=self.table_name, Key=self.key(system_id, report_date),
            UpdateExpression="SET #status=:retryable REMOVE lease_expires_at",
            ConditionExpression="#status=:processing AND owner_token=:owner",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":retryable": {"S": "RETRYABLE"}, ":processing": {"S": "PROCESSING"}, ":owner": {"S": owner}})


class GeneratedReports:
    def __init__(self, bucket, client=None, max_output_bytes=MAX_OUTPUT_BYTES, fallback_bucket=None):
        self.bucket = bucket
        self.fallback_bucket = fallback_bucket
        self.max_output_bytes = max_output_bytes
        self.client = client or boto3.client("s3", config=AWS_CONFIG)

    @staticmethod
    def key(system_id, report_date):
        return f"generated/operational/{system_id}/{report_date:%Y/%m/%d}.json.gz"

    def get(self, key):
        report = self._get(self.bucket, key)
        if report is None and self.fallback_bucket and self.fallback_bucket != self.bucket:
            report = self._get(self.fallback_bucket, key)
        return report

    def _get(self, bucket, key):
        try:
            response = self.client.get_object(Bucket=bucket, Key=key)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404", "NotFound"):
                return None
            if code in ("AccessDenied", "403"):
                listed = self.client.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1)
                if not any(item.get("Key") == key for item in listed.get("Contents", [])):
                    return None
            raise
        body = response["Body"]
        try:
            compressed = body.read(self.max_output_bytes + 4097)
            if len(compressed) > self.max_output_bytes + 4096:
                raise RuntimeError("Stored operational report exceeds compressed limit")
            raw = gzip.GzipFile(fileobj=io.BytesIO(compressed)).read(self.max_output_bytes + 1)
            if len(raw) > self.max_output_bytes:
                raise RuntimeError("Stored operational report exceeds output limit")
            report = json.loads(raw)
            return OperationalReport.model_validate(report).model_dump(mode="json")
        except (OSError, EOFError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("Stored operational report is invalid") from error
        finally:
            body.close()

    def create(self, key, raw, metadata):
        for attempt in range(MAX_CONDITIONAL_WRITE_ATTEMPTS):
            try:
                self.client.put_object(Bucket=self.bucket, Key=key, Body=gzip.compress(raw, mtime=0),
                    ContentType="application/json", ContentEncoding="gzip", ServerSideEncryption="AES256",
                    IfNoneMatch="*", Metadata=metadata)
                return True
            except ClientError as error:
                code = error.response.get("Error", {}).get("Code")
                if code in ("PreconditionFailed", "412"):
                    return False
                if code not in ("ConditionalRequestConflict", "409") or attempt + 1 == MAX_CONDITIONAL_WRITE_ATTEMPTS:
                    raise
                # S3 recommends retrying conditional writes after a concurrent
                # operation conflict. Keep retries bounded and outside service logic.
                time.sleep(CONDITIONAL_WRITE_RETRY_DELAY_SECONDS * (2 ** attempt))
        raise RuntimeError("Conditional report write attempts were exhausted")
