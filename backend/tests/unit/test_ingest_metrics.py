from datetime import datetime, timezone
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError
from moto import mock_aws
import boto3

from lambda_loader import load_lambda


modules = load_lambda("ingest_metrics")
repository = modules["repository"]
handler = modules["handler"]


class IngestMetricsTests(unittest.TestCase):
    def test_extended_metrics_are_persisted_under_host(self):
        payload = json.loads((Path(__file__).resolve().parents[2] / "lambda/ingest_metrics/test/payload.json").read_text())
        extended = {"memory_available_mb": 1000, "network": {"bytes_sent": 1234}, "disks": []}
        payload["host"]["extended"] = extended
        model = modules["models"].MetricsPayload.model_validate(payload)
        resources = modules["service"].MetricsService._resources(model)
        self.assertEqual(resources[0]["resource_type"], "HOST")
        self.assertEqual(resources[0]["metrics"]["extended"], extended)

    def test_history_expires_but_global_snapshot_is_preserved(self):
        client = Mock()
        received_at = "2026-10-17T15:00:00Z"
        metrics = repository.MetricsRepository("metrics-test", dynamodb_client=client)
        metrics.save_resource_metrics(
            "vps-01",
            "2000-01-01T00:00:00Z",
            received_at,
            [
                {
                    "resource_type": "HOST",
                    "resource_id": "host",
                    "metrics": {"cpu_percent": 32.4},
                },
            ],
        )
        transactions = client.transact_write_items.call_args.kwargs["TransactItems"]
        decoder = TypeDeserializer()
        records = [
            {
                key: decoder.deserialize(value)
                for key, value in tx["Put"]["Item"].items()
            }
            for tx in [*transactions, {"Put": client.put_item.call_args.kwargs}]
        ]
        expected = (
            int(datetime(2026, 10, 17, 15, tzinfo=timezone.utc).timestamp()) + 604800
        )
        self.assertTrue(
            all(record["expires_at"] == expected for record in records[:-1])
        )
        self.assertNotIn("expires_at", records[-1])
        self.assertEqual(records[0]["PK"], "SYSTEM#vps-01")
        self.assertEqual(records[-1]["PK"], "METRICS")
        self.assertEqual(records[-1]["SK"], "SNAPSHOT#LATEST")

    def test_postgres_is_persisted_as_its_own_resource(self):
        payload = json.loads(
            (
                Path(__file__).resolve().parents[2]
                / "lambda/ingest_metrics/test/payload.json"
            ).read_text()
        )
        payload["postgres"] = {
            "samples": [
                {
                    "timestamp": "2026-10-22T12:00:00Z",
                    "available": True,
                    "connections": [
                        {"database": "app", "connections": 6, "active": 1, "idle": 5}
                    ],
                }
            ]
        }
        model = modules["models"].MetricsPayload.model_validate(payload)
        resources = modules["service"].MetricsService._resources(model)
        pg = next(item for item in resources if item["resource_type"] == "POSTGRES")
        self.assertEqual(pg["resource_id"], "postgres")
        self.assertEqual(pg["metrics"], payload["postgres"])
        latest = load_lambda("get_latest_metrics")["models"].LatestMetricsSnapshot
        latest.model_validate(
            {
                "system_id": model.system_id,
                "measured_at": "now",
                "received_at": "now",
                "resources": resources,
            }
        )

    def test_handler_processes_payload_without_fetching_a_token(self):
        payload = (
            Path(__file__).resolve().parents[2]
            / "lambda/ingest_metrics/test/payload.json"
        ).read_text()
        client = Mock()
        with (
            patch.dict(os.environ, {"METRICS_TABLE_NAME": "metrics-test"}, clear=True),
            patch.object(repository.boto3, "client", return_value=client) as sdk,
        ):
            response = handler.lambda_handler({"body": payload}, None)
        self.assertEqual(response["statusCode"], 201)
        self.assertEqual(json.loads(response["body"])["system_id"], "demo-vps-01")
        sdk.assert_called_once_with("dynamodb")
        client.transact_write_items.assert_called_once()

    def payload(self, containers=0, postgres=False):
        payload = json.loads((Path(__file__).resolve().parents[2] / "lambda/ingest_metrics/test/payload.json").read_text())
        template = payload["docker"]["containers"][0]
        payload["docker"]["containers"] = [{**template, "name": f"c{i}"} for i in range(containers)]
        payload["docker"]["total_containers"] = containers
        payload["docker"]["running_containers"] = containers
        payload["postgres"] = {"samples": []} if postgres else None
        return payload

    def invoke(self, client, payload):
        with patch.dict(os.environ, {"METRICS_TABLE_NAME": "test"}, clear=True), patch.object(repository.boto3, "client", return_value=client):
            return handler.lambda_handler({"body": json.dumps(payload)}, None)

    def test_cancellation_reasons_determine_http_status(self):
        for reason in ("ConditionalCheckFailed", "TransactionConflict", "ThrottlingError",
                       "ProvisionedThroughputExceeded", "ValidationError", "Unknown", None):
            with self.subTest(reason=reason):
                client = Mock()
                response = {"Error": {"Code": "TransactionCanceledException"}}
                if reason:
                    response["CancellationReasons"] = [{"Code": reason}]
                client.transact_write_items.side_effect = ClientError(response, "TransactWriteItems")
                result = self.invoke(client, self.payload())
                self.assertEqual(result["statusCode"], 200 if reason == "ConditionalCheckFailed" else 503)
                if reason != "ConditionalCheckFailed":
                    client.put_item.assert_not_called()

    def test_mixed_or_incomplete_cancellation_is_never_replay(self):
        for reasons in ([{"Code": "ConditionalCheckFailed"}, {"Code": "TransactionConflict"}],
                        [{"Code": "None"}], [{"Code": "ConditionalCheckFailed"}]):
            with self.subTest(reasons=reasons):
                client = Mock()
                client.transact_write_items.side_effect = ClientError({"Error": {"Code": "TransactionCanceledException"},
                    "CancellationReasons": reasons}, "TransactWriteItems")
                self.assertEqual(self.invoke(client, self.payload(1))["statusCode"], 503)

    def test_container_limits_with_and_without_postgres(self):
        for count in (97, 98, 99, 100):
            for postgres in (False, True):
                with self.subTest(count=count, postgres=postgres):
                    client = Mock()
                    result = self.invoke(client, self.payload(count, postgres))
                    self.assertEqual(result["statusCode"], 201 if count <= 98 else 422)
                    if count <= 98:
                        tx = client.transact_write_items.call_args.kwargs["TransactItems"]
                        self.assertLessEqual(len(tx), 100)
                        self.assertTrue(all(item["Put"]["Item"]["PK"]["S"].startswith("SYSTEM#") for item in tx))
                    else:
                        client.transact_write_items.assert_not_called()

    @mock_aws
    def test_older_sample_keeps_history_without_regressing_snapshot(self):
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(TableName="test", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"} for key in ("PK", "SK")])
        repo = repository.MetricsRepository("test", client)
        resource = [{"resource_type": "HOST", "resource_id": "host", "metrics": {"cpu_percent": 10}}]
        for at in ("2026-10-26T10:00:00Z", "2026-10-26T09:00:00Z"):
            self.assertTrue(repo.save_resource_metrics("vps", at, "2026-10-26T11:00:00Z", resource))
        snapshot = client.get_item(TableName="test", Key={"PK": {"S": "METRICS"}, "SK": {"S": "SNAPSHOT#LATEST"}})["Item"]
        self.assertEqual(snapshot["measured_at"]["S"], "2026-10-26T10:00:00Z")
        self.assertEqual(len(client.scan(TableName="test")["Items"]), 3)

    def test_snapshot_failure_retries_and_replay_repairs_snapshot(self):
        client = Mock()
        client.put_item.side_effect = ClientError({"Error": {"Code": "InternalServerError"}}, "PutItem")
        self.assertEqual(self.invoke(client, self.payload())["statusCode"], 503)
        client.put_item.side_effect = None
        client.transact_write_items.side_effect = ClientError({"Error": {"Code": "TransactionCanceledException"},
            "CancellationReasons": [{"Code": "ConditionalCheckFailed"}]}, "TransactWriteItems")
        self.assertEqual(self.invoke(client, self.payload())["statusCode"], 200)
        self.assertEqual(client.put_item.call_count, 2)


if __name__ == "__main__":
    unittest.main()
