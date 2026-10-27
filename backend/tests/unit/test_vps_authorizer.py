import os
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

from lambda_loader import load_lambda


modules = load_lambda("vps_authorizer")
handler = modules["handler"]
repository = modules["repository"]


class VpsAuthorizerTests(unittest.TestCase):
    def setUp(self):
        handler._service.cache_clear()
        self.addCleanup(handler._service.cache_clear)
        self.client = Mock()
        self.client.get_secret_value.return_value = {"SecretString": "test-token"}
        self.sdk = patch.object(repository.boto3, "client", return_value=self.client).start()
        patch.dict(os.environ, {"METRICS_INGEST_SECRET_ARN": "arn:test:secret"}).start()
        self.addCleanup(patch.stopall)

    def call(self, headers):
        return handler.lambda_handler({"version": "2.0", "headers": headers}, None)

    def test_both_headers_and_secret_cache(self):
        for headers in ({"X-API-Token": "test-token"}, {"authorization": "bEaReR test-token"}):
            self.assertEqual(self.call(headers), {"isAuthorized": True})
        self.client.get_secret_value.assert_called_once_with(SecretId="arn:test:secret")
        self.sdk.assert_called_once_with("secretsmanager")

    def test_missing_credentials_do_not_read_secret(self):
        for headers in (None, {}, {"Authorization": "Basic bad"}, {"Authorization": "Bearer "}):
            self.assertEqual(self.call(headers), {"isAuthorized": False})
        self.sdk.assert_not_called()

    def test_invalid_credentials_are_rejected(self):
        self.assertEqual(self.call({"x-api-token": "wrong"}), {"isAuthorized": False})

    def test_secret_rotation_refreshes_after_sixty_seconds(self):
        with patch.object(repository.time, "monotonic", return_value=0):
            self.assertTrue(self.call({"x-api-token": "test-token"})["isAuthorized"])
        self.client.get_secret_value.return_value = {"SecretString": "rotated-token"}
        with patch.object(repository.time, "monotonic", return_value=61):
            self.assertFalse(self.call({"x-api-token": "test-token"})["isAuthorized"])
            self.assertTrue(self.call({"x-api-token": "rotated-token"})["isAuthorized"])
        self.assertEqual(self.client.get_secret_value.call_count, 2)

    def test_secret_failures_deny_access_and_are_not_cached(self):
        self.client.get_secret_value.side_effect = ClientError({"Error": {"Code": "AccessDeniedException"}}, "GetSecretValue")
        with self.assertLogs(handler.logger, level="ERROR"):
            for _ in range(2):
                self.assertEqual(self.call({"x-api-token": "test-token"}), {"isAuthorized": False})
        self.assertEqual(self.client.get_secret_value.call_count, 2)

    def test_empty_secret_does_not_allow_empty_token(self):
        self.client.get_secret_value.return_value = {"SecretString": ""}
        with self.assertLogs(handler.logger, level="ERROR"):
            self.assertEqual(self.call({"x-api-token": "test-token"}), {"isAuthorized": False})


if __name__ == "__main__":
    unittest.main()
