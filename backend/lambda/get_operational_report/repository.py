from __future__ import annotations

import gzip
import io
import json

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from models import OperationalReport


MAX_OUTPUT_BYTES = 64 * 1024


class OperationalReportsRepository:
    def __init__(self, bucket, client=None, max_output_bytes=MAX_OUTPUT_BYTES, fallback_bucket=None):
        self.bucket = bucket
        self.fallback_bucket = fallback_bucket
        self.max_output_bytes = max_output_bytes
        self.client = client or boto3.client("s3", config=Config(connect_timeout=2, read_timeout=5,
            retries={"mode": "standard", "total_max_attempts": 2}))

    def get(self, key):
        report = self._get(self.bucket, key)
        if report is None and self.fallback_bucket and self.fallback_bucket != self.bucket:
            report = self._get(self.fallback_bucket, key)
        return report

    def _get(self, bucket, key):
        try:
            result = self.client.get_object(Bucket=bucket, Key=key)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404", "NotFound"):
                return None
            if code in ("AccessDenied", "403"):
                # S3 may mask a missing key as 403 when ListBucket is absent.
                # This role is prefix-scoped and lists only the exact report key.
                listed = self.client.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1)
                if not any(item.get("Key") == key for item in listed.get("Contents", [])):
                    return None
            raise
        body = result["Body"]
        try:
            compressed = body.read(self.max_output_bytes + 4097)
            if len(compressed) > self.max_output_bytes + 4096:
                raise RuntimeError("Stored report exceeds compressed limit")
            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as archive:
                raw = archive.read(self.max_output_bytes + 1)
            if len(raw) > self.max_output_bytes:
                raise RuntimeError("Stored report exceeds output limit")
            report = json.loads(raw)
            # Preserve the legacy v1 wire shape when additive status fields were
            # absent in an older saved object.
            report = OperationalReport.model_validate(report).model_dump(mode="json", exclude_defaults=True)
        except (OSError, EOFError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("Stored report is invalid") from error
        finally:
            body.close()
        return report


    def get_pdf(self, key):
        from report_pdf import MAX_PDF_BYTES
        for bucket in dict.fromkeys(filter(None, [self.bucket, self.fallback_bucket])):
            try:
                result = self.client.get_object(Bucket=bucket, Key=key)
            except ClientError as error:
                code = error.response.get("Error", {}).get("Code")
                if code in ("NoSuchKey", "404", "NotFound"):
                    continue
                if code in ("AccessDenied", "403"):
                    listed = self.client.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1)
                    if not any(i.get("Key") == key for i in listed.get("Contents", [])):
                        continue
                raise
            body = result["Body"]
            try:
                raw = body.read(MAX_PDF_BYTES + 1)
                if len(raw) > MAX_PDF_BYTES or not raw.startswith(b"%PDF-"):
                    raise RuntimeError("Stored PDF is invalid")
                return raw
            finally:
                body.close()
        return None
