from __future__ import annotations

import time

import boto3


class TokenRepository:
    def __init__(self, secret_arn: str, secrets_client=None) -> None:
        self.secret_arn = secret_arn
        self.client = secrets_client
        self._token: str | None = None
        self._expires_at = 0.0

    def get_expected_token(self) -> str:
        if self._token is not None and time.monotonic() < self._expires_at:
            return self._token
        if self.client is None:
            self.client = boto3.client("secretsmanager")
        result = self.client.get_secret_value(SecretId=self.secret_arn)
        token = result.get("SecretString")
        if not isinstance(token, str) or not token:
            raise ValueError("VPS token secret must contain a non-empty SecretString")
        self._token = token
        self._expires_at = time.monotonic() + 60
        return token
