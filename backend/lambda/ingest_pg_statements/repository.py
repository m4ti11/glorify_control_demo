from __future__ import annotations

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


class PgStatementsRepository:
    def __init__(self, bucket, client=None):
        self.bucket = bucket
        self.client = client or boto3.client("s3", config=Config(
            connect_timeout=2, read_timeout=5, retries={"mode": "standard", "total_max_attempts": 2}))

    def create(self, key, compressed, metadata):
        try:
            # Atomic even across concurrent requests; HEAD followed by PUT is not safe.
            self.client.put_object(Bucket=self.bucket, Key=key, Body=compressed,
                                   ContentType="application/json", ContentEncoding="gzip",
                                   ServerSideEncryption="AES256", IfNoneMatch="*", Metadata=metadata)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in ("PreconditionFailed", "412"):
                return False
            # A 409 ConditionalRequestConflict is transient, not proof of a saved object.
            raise
        return True
