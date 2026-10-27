import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError


SOURCE = Path(__file__).resolve().parents[2] / "lambda/get_latest_metrics"
# Each Lambda has its own top-level modules inside its deployment ZIP.
# Isolate these imports so other Lambda tests can use the same module names.
modules = {}
with patch.dict(sys.modules):
    for name in ("models", "repository", "service", "handler"):
        spec = importlib.util.spec_from_file_location(name, SOURCE / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        modules[name] = module
handler = modules["handler"]
repository = modules["repository"]


class LatestMetricsTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.sdk = patch.object(repository.boto3, "client", return_value=self.client).start()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"METRICS_TABLE_NAME": "metrics-test"}).start()

    def call(self, method="GET"):
        return handler.lambda_handler({
            "requestContext": {"http": {"method": method}},
        }, None)

    def test_latest_snapshot_and_nested_numbers(self):
        self.client.get_item.return_value = {"Item": {
            "PK": {"S": "METRICS"}, "SK": {"S": "SNAPSHOT#LATEST"},
            "system_id": {"S": "vps-01"},
            "measured_at": {"S": "2026-10-17T15:00:00Z"},
            "received_at": {"S": "2026-10-17T15:00:01Z"},
            "resources": {"L": [{"M": {"resource_type": {"S": "HOST"}, "resource_id": {"S": "host"}, "metrics": {"M": {
                "cpu_percent": {"N": "32.4"}, "uptime_seconds": {"N": "123"},
            }}}}]},
        }}
        response = self.call()
        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertNotIn("PK", body)
        self.assertEqual(body["resources"][0]["metrics"], {"cpu_percent": 32.4, "uptime_seconds": 123})
        self.sdk.assert_called_once_with("dynamodb")
        self.client.get_item.assert_called_once_with(
            TableName="metrics-test", ConsistentRead=True,
            Key={"PK": {"S": "METRICS"}, "SK": {"S": "SNAPSHOT#LATEST"}},
        )

    def test_reads_actual_ingestion_snapshot_with_postgres_and_host_extended(self):
        from lambda_loader import load_lambda

        ingest = load_lambda("ingest_metrics", ["models", "repository", "service"])
        fixture = Path(__file__).resolve().parents[3] / "agent/examples/heartbeat.json"
        payload = ingest["models"].MetricsPayload.model_validate_json(fixture.read_text())
        payload.host.extended = {"docker_snapshot_complete": True}
        writer = Mock()
        producer = ingest["service"].MetricsService(ingest["repository"].MetricsRepository("metrics-test", writer))
        producer.ingest(payload)
        snapshot = writer.put_item.call_args.kwargs["Item"]
        self.client.get_item.return_value = {"Item": snapshot}

        response = self.call()
        self.assertEqual(response["statusCode"], 200)
        resources = json.loads(response["body"])["resources"]
        postgres = next(item for item in resources if item["resource_type"] == "POSTGRES")
        self.assertEqual(postgres["metrics"], payload.postgres.model_dump(mode="json"))
        host = next(item for item in resources if item["resource_type"] == "HOST")
        self.assertTrue(host["metrics"]["extended"]["docker_snapshot_complete"])

    def test_old_snapshot_is_still_returned_even_with_legacy_expiry(self):
        self.client.get_item.return_value = {"Item": {
            "expires_at": {"N": "1"}, "system_id": {"S": "vps"},
            "measured_at": {"S": "2020-01-01T00:00:00Z"},
            "received_at": {"S": "2020-01-01T00:00:01Z"}, "resources": {"L": []},
        }}
        result = self.call()
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(json.loads(result["body"])["received_at"], "2020-01-01T00:00:01Z")

    def test_method_not_allowed(self):
        self.assertEqual(self.call(method="POST")["statusCode"], 405)
        self.sdk.assert_not_called()

    def test_missing_snapshot(self):
        self.client.get_item.return_value = {}
        self.assertEqual(self.call()["statusCode"], 404)

    def test_dynamodb_failure_does_not_expose_details(self):
        self.client.get_item.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "private details"}}, "GetItem",
        )
        with self.assertLogs(handler.logger, level="ERROR"):
            response = self.call()
        self.assertEqual(response["statusCode"], 503)
        self.assertNotIn("private details", response["body"])


if __name__ == "__main__":
    unittest.main()
