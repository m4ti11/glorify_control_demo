from __future__ import annotations

import gzip
import io
import json

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from models import MAX_REPORT_BYTES


class PgStatementsRepository:
    def __init__(self, bucket, client=None):
        self.bucket = bucket
        self.client = client or boto3.client("s3", config=Config(
            connect_timeout=2, read_timeout=5, retries={"mode": "standard", "total_max_attempts": 2}))

    def get(self, key):
        try:
            result = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise
        stream = result["Body"]
        try:
            compressed = stream.read(MAX_REPORT_BYTES + 4097)
            if len(compressed) > MAX_REPORT_BYTES + 4096:
                raise RuntimeError("Stored object exceeds the report limit")
            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as archive:
                raw = archive.read(MAX_REPORT_BYTES + 1)
            if len(raw) > MAX_REPORT_BYTES:
                raise RuntimeError("Stored report exceeds the decompressed limit")
            report = json.loads(raw)
            if not isinstance(report, dict):
                raise RuntimeError("Stored report is invalid")
        except (OSError, EOFError, ValueError) as error:
            raise RuntimeError("Stored report is invalid") from error
        finally:
            stream.close()
        return {"report": report, "received_at": result.get("Metadata", {}).get("received-at")}
