import base64
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError
from lambda_loader import load_lambda

upload = load_lambda("ingest_pg_statements")
read = load_lambda("get_pg_statements")
PAYLOAD = json.loads((Path(__file__).resolve().parents[2] / "lambda/ingest_pg_statements/test/payload.json").read_text())
NOW = datetime(2026, 10, 20, 6, tzinfo=timezone.utc)
KEY = "raw/pg-statements/demo-vps-01/2026/10/19.json.gz"


def error(code):
    return ClientError({"Error": {"Code": code, "Message": "private details"}}, "Test")


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.repo = Mock()
        self.repo.create.return_value = True
        self.service = upload["service"].PgStatementsService(self.repo)

    def ingest(self, payload=None, now=NOW):
        return self.service.ingest(json.dumps(PAYLOAD if payload is None else payload), now=now)

    def test_payload_date_determines_object_key_not_receipt_or_occurred_at(self):
        created, result = self.ingest()
        self.assertTrue(created)
        key, data, metadata = self.repo.create.call_args.args
        self.assertEqual(key, KEY)
        self.assertEqual(metadata["report-date"], "2026-10-19")
        self.assertEqual(metadata["received-at"], "2026-10-20T06:00:00.000Z")
        stored = json.loads(gzip.decompress(data))
        self.assertEqual(stored["details"]["top_total"]["rows"][0]["queryid"], "7823456789012345678")
        self.assertEqual(stored["details"]["database_stats"], PAYLOAD["details"]["database_stats"])
        self.assertEqual(result["report_date"], "2026-10-19")

    def test_daily_statement_fields_and_query_text_are_preserved_in_s3(self):
        example_path = Path(__file__).resolve().parents[3] / "agent/examples/daily-pg-statements.json"
        payload = json.loads(example_path.read_text())
        self.ingest(payload, now=datetime(2026, 10, 23, tzinfo=timezone.utc))
        key, compressed, _ = self.repo.create.call_args.args
        self.assertEqual(key, "raw/pg-statements/demo-vps-01/2026/10/22.json.gz")
        stored = json.loads(gzip.decompress(compressed))
        expected = payload["details"]["reports"][0]["pg_stat_statements"]["rows"]
        self.assertEqual(stored["details"]["reports"][0]["pg_stat_statements"]["rows"], expected)
        client = Mock()
        client.get_object.return_value = {"Body": io.BytesIO(compressed), "Metadata": {}}
        downloaded = read["repository"].PgStatementsRepository("bucket", client).get(key)
        self.assertEqual(downloaded["report"]["details"]["reports"][0]["pg_stat_statements"]["rows"], expected)

    def test_duplicate_same_date_even_with_changed_event_id_does_not_create(self):
        self.repo.create.return_value = False
        changed = deepcopy(PAYLOAD)
        changed["event_id"] = "00000000-0000-0000-0000-123456789012"
        created, result = self.ingest(changed)
        self.assertFalse(created)
        self.assertFalse(result["created"])
        self.assertEqual(self.repo.create.call_args.args[0], KEY)

    def test_future_date_is_rejected_in_report_timezone(self):
        # At 02:00 UTC the local date is still the 18th.
        with self.assertRaises(ValueError):
            self.ingest(now=datetime(2026, 10, 19, 2, tzinfo=timezone.utc))
        self.repo.create.assert_not_called()
        self.ingest(now=datetime(2026, 10, 19, 3, tzinfo=timezone.utc))
        self.repo.create.assert_called_once()

    def test_invalid_paths_dates_timezones_and_sections_are_rejected(self):
        for mutate in (
            lambda p: p.update(system_id="../escape"),
            lambda p: p["details"].update(report_date="2026-02-30"),
            lambda p: p["details"].update(timezone="bad/timezone"),
            lambda p: p.update(event_type="container.exited"),
            lambda p: p.update(occurred_at="2026-10-19T00:00:00"),
            lambda p: p["details"].update(scope="system"),
            lambda p: p["details"]["top_total"].update(rows="wrong"),
        ):
            payload = deepcopy(PAYLOAD); mutate(payload)
            with self.subTest(payload=payload["details"].get("scope")), self.assertRaises(ValueError):
                self.ingest(payload)
        self.repo.create.assert_not_called()

    def test_system_envelope_preserves_all_database_reports(self):
        payload = deepcopy(PAYLOAD)
        single = {k: v for k, v in payload["details"].items() if k not in {"report_date", "timezone"}}
        payload["details"] = {"report_date": "2026-10-19", "timezone": "America/Argentina/Buenos_Aires",
                              "scope": "system", "database": "all", "available": True,
                              "reports": [{"scope": "cluster", "database": "cluster", "available": False}, single]}
        self.ingest(payload)
        stored = json.loads(gzip.decompress(self.repo.create.call_args.args[1]))
        self.assertEqual(len(stored["details"]["reports"]), 2)

    def test_payload_limit_and_nonfinite_numbers_do_not_write(self):
        with self.assertRaises(OverflowError):
            self.service.ingest("x" * (1024 * 1024 + 1), now=NOW)
        changed = deepcopy(PAYLOAD)
        changed["details"]["top_total"]["rows"][0]["total_exec_time"] = float("nan")
        with self.assertRaises(ValueError):
            self.ingest(changed)
        self.repo.create.assert_not_called()


class S3RepositoryTests(unittest.TestCase):
    def test_put_uses_atomic_condition_gzip_metadata_and_encryption(self):
        client = Mock()
        repo = upload["repository"].PgStatementsRepository("bucket", client)
        self.assertTrue(repo.create(KEY, b"compressed", {"report-date": "2026-10-19"}))
        client.put_object.assert_called_once_with(Bucket="bucket", Key=KEY, Body=b"compressed",
            ContentType="application/json", ContentEncoding="gzip", ServerSideEncryption="AES256",
            IfNoneMatch="*", Metadata={"report-date": "2026-10-19"})
        client.head_object.assert_not_called()

    def test_only_precondition_failure_is_a_duplicate_not_transient_conflict(self):
        client = Mock()
        repo = upload["repository"].PgStatementsRepository("bucket", client)
        client.put_object.side_effect = error("PreconditionFailed")
        self.assertFalse(repo.create(KEY, b"x", {}))
        for code in ("ConditionalRequestConflict", "AccessDenied", "SlowDown"):
            client.put_object.side_effect = error(code)
            with self.assertRaises(ClientError):
                repo.create(KEY, b"x", {})

    def test_get_decompresses_object_and_closes_body_without_listing(self):
        client = Mock(); body = io.BytesIO(gzip.compress(json.dumps(PAYLOAD).encode()))
        client.get_object.return_value = {"Body": body, "Metadata": {"received-at": "receipt"}}
        result = read["repository"].PgStatementsRepository("bucket", client).get(KEY)
        self.assertEqual(result["report"], PAYLOAD)
        self.assertEqual(result["received_at"], "receipt")
        self.assertTrue(body.closed)
        client.get_object.assert_called_once_with(Bucket="bucket", Key=KEY)
        client.list_objects_v2.assert_not_called()

    def test_missing_object_is_distinct_from_access_denied(self):
        client = Mock(); repo = read["repository"].PgStatementsRepository("bucket", client)
        client.get_object.side_effect = error("NoSuchKey")
        self.assertIsNone(repo.get(KEY))
        client.get_object.side_effect = error("AccessDenied")
        with self.assertRaises(ClientError):
            repo.get(KEY)

    def test_oversized_decompressed_data_and_corrupt_archive_fail_closed(self):
        for data in (gzip.compress(b"x" * (1024 * 1024 + 1)), b"not gzip", gzip.compress(b"[]")):
            client = Mock(); body = io.BytesIO(data); client.get_object.return_value = {"Body": body}
            with self.subTest(data=data[:10]), self.assertRaises(RuntimeError):
                read["repository"].PgStatementsRepository("bucket", client).get(KEY)
            self.assertTrue(body.closed)


class PgHandlerTests(unittest.TestCase):
    def test_upload_created_duplicate_bad_payload_and_transient_failure_statuses(self):
        handler = upload["handler"]
        with patch.dict(os.environ, PG_REPORTS_BUCKET="bucket"), patch.object(handler, "PgStatementsRepository"), patch.object(handler, "PgStatementsService") as worker:
            worker.return_value.ingest.return_value = (True, {"created": True})
            self.assertEqual(handler.lambda_handler({"body": "{}"}, None)["statusCode"], 201)
            worker.return_value.ingest.return_value = (False, {"created": False})
            self.assertEqual(handler.lambda_handler({"body": "{}"}, None)["statusCode"], 409)
            worker.return_value.ingest.side_effect = ValueError("private details")
            response = handler.lambda_handler({"body": "{}"}, None)
            self.assertEqual(response["statusCode"], 422)
            self.assertNotIn("private", response["body"])
            worker.return_value.ingest.side_effect = error("ConditionalRequestConflict")
            with self.assertLogs(handler.logger):
                self.assertEqual(handler.lambda_handler({"body": "{}"}, None)["statusCode"], 503)
            worker.return_value.ingest.side_effect = OverflowError()
            self.assertEqual(handler.lambda_handler({"body": "{}"}, None)["statusCode"], 413)

    def test_base64_body_is_supported_and_malformed_body_is_rejected(self):
        handler = upload["handler"]
        with patch.dict(os.environ, PG_REPORTS_BUCKET="bucket"), patch.object(handler, "PgStatementsRepository"), patch.object(handler, "PgStatementsService") as worker:
            worker.return_value.ingest.return_value = (True, {"created": True})
            raw = base64.b64encode(b'{}').decode()
            self.assertEqual(handler.lambda_handler({"body": raw, "isBase64Encoded": True}, None)["statusCode"], 201)
            worker.return_value.ingest.assert_called_with('{}')
            self.assertEqual(handler.lambda_handler({"body": "%", "isBase64Encoded": True}, None)["statusCode"], 422)
            self.assertEqual(handler.lambda_handler({"requestContext": {"http": {"method": "GET"}}}, None)["statusCode"], 405)

    def test_read_validates_inputs_before_aws_and_returns_404_for_absence(self):
        handler = read["handler"]
        with patch.object(handler, "PgStatementsRepository") as repo:
            for params in ({}, {"system_id": "../bad", "report_date": "2026-10-19"}, {"system_id": "vps", "report_date": "2026-02-30"}):
                self.assertEqual(handler.lambda_handler({"queryStringParameters": params}, None)["statusCode"], 400)
            repo.assert_not_called()
        with patch.dict(os.environ, PG_REPORTS_BUCKET="bucket"), patch.object(handler, "PgStatementsRepository") as repo:
            repo.return_value.get.return_value = None
            request = {"queryStringParameters": {"system_id": "demo-vps-01", "report_date": "2026-10-19"}}
            self.assertEqual(handler.lambda_handler(request, None)["statusCode"], 404)
            repo.return_value.get.assert_called_once_with(KEY)
            repo.return_value.get.return_value = {"report": PAYLOAD, "received_at": "receipt"}
            result = handler.lambda_handler(request, None)
            self.assertEqual(result["statusCode"], 200)
            self.assertEqual(result["headers"]["Cache-Control"], "no-store")
            repo.return_value.get.side_effect = error("AccessDenied")
            with self.assertLogs(handler.logger):
                self.assertEqual(handler.lambda_handler(request, None)["statusCode"], 503)
