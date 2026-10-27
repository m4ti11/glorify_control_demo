import gzip
import io
import json
import os
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

from lambda_loader import load_lambda


mods = load_lambda("get_operational_report")
handler, models, repository, service = mods["handler"], mods["models"], mods["repository"], mods["service"]
REPORT = {
    "schema_version": 1, "system_id": "vps", "report_date": "2026-10-19",
    "timezone": "America/Argentina/Buenos_Aires",
    "window": {"start": "2026-10-19T03:00:00Z", "end_exclusive": "2026-10-20T03:00:00Z"},
    "generated_at": "2026-10-20T06:30:00Z",
    "source_status": {
        name: {"status": "missing", "count": 0}
        for name in ("host_metrics", "events", "postgresql")
    },
    "analysis": {"summary": "ok", "findings": [], "recommendations": []},
}


def test_query_validates_date_and_uses_exact_generated_prefix():
    query = models.parse_query({"system_id": "vps", "report_date": "2026-10-19"})
    assert query.key == "generated/operational/vps/2026/10/19.json.gz"
    for params in ({}, {"system_id": "../bad", "report_date": "2026-10-19"},
                   {"system_id": "vps", "report_date": "2026-02-30"},
                   {"system_id": "vps", "report_date": "2026-10-19", "extra": "x"}):
        try:
            models.parse_query(params)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid query {params}")


def test_repository_reads_bounded_gzip_and_distinguishes_missing_object():
    client = Mock(); body = io.BytesIO(gzip.compress(json.dumps(REPORT).encode()))
    client.get_object.return_value = {"Body": body}
    repo = repository.OperationalReportsRepository("bucket", client, 4096)
    assert repo.get("key") == REPORT
    assert body.closed
    client.get_object.side_effect = ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")
    assert repo.get("key") is None

    client.get_object.side_effect = ClientError({"Error": {"Code": "AccessDenied", "Message": "missing"}}, "GetObject")
    client.list_objects_v2.return_value = {"Contents": []}
    assert repo.get("key") is None
    client.list_objects_v2.assert_called_with(Bucket="bucket", Prefix="key", MaxKeys=1)


def test_reader_preserves_multiple_source_issues_and_legacy_v1_shape():
    client = Mock()
    repo = repository.OperationalReportsRepository("bucket", client, 4096)
    client.get_object.return_value = {"Body": io.BytesIO(gzip.compress(json.dumps(REPORT).encode()))}
    legacy = repo.get("key")
    assert "issues" not in legacy["source_status"]["host_metrics"]
    updated = json.loads(json.dumps(REPORT))
    updated["source_status"]["host_metrics"] = {"status": "stale", "issues": ["stale", "incomplete"], "count": 2}
    updated["source_status"]["postgresql"]["snapshot_semantics"] = {
        "schema_version": 1, "kind": "point_in_time_snapshot", "captured_at": "2026-10-20T06:00:00Z"}
    client.get_object.return_value = {"Body": io.BytesIO(gzip.compress(json.dumps(updated).encode()))}
    current = repo.get("key")
    assert current["source_status"]["host_metrics"]["issues"] == ["stale", "incomplete"]
    assert current["source_status"]["postgresql"]["snapshot_semantics"]["kind"] == "point_in_time_snapshot"


def test_service_returns_matching_report_and_rejects_key_identity_mismatch():
    query = models.parse_query({"system_id": "vps", "report_date": "2026-10-19"})
    repo = Mock()
    repo.get.return_value = REPORT
    assert service.OperationalReportService(repo).get(query) == REPORT
    repo.get.assert_called_once_with(query.key)

    repo.get.return_value = {**REPORT, "system_id": "another-system"}
    try:
        service.OperationalReportService(repo).get(query)
    except RuntimeError as error:
        assert "identity mismatch" in str(error)
    else:
        raise AssertionError("service returned a report for a different system")


def test_handler_auth_route_contract_and_safe_responses():
    request = {"requestContext": {"http": {"method": "GET"}},
               "queryStringParameters": {"system_id": "vps", "report_date": "2026-10-19"}}
    with patch.dict(os.environ, {"GENERATED_REPORTS_BUCKET": "bucket", "REPORT_SYSTEM_IDS": '["vps"]'}), \
         patch.object(handler, "OperationalReportsRepository") as repo:
        repo.return_value.get.return_value = REPORT
        result = handler.lambda_handler(request, None)
        assert result["statusCode"] == 200
        assert json.loads(result["body"]) == REPORT
        repo.return_value.get.return_value = None
        assert handler.lambda_handler(request, None)["statusCode"] == 404
        repo.return_value.get.return_value = {**REPORT, "system_id": "another-system"}
        assert handler.lambda_handler(request, None)["statusCode"] == 503
        assert handler.lambda_handler({**request, "requestContext": {"http": {"method": "POST"}}}, None)["statusCode"] == 405
        assert handler.lambda_handler({**request, "queryStringParameters": {}}, None)["statusCode"] == 400


def test_pdf_query_and_authenticated_binary_response_preserve_json_contract():
    import base64
    query = models.parse_query({"system_id": "vps", "report_date": "2026-10-19", "format": "pdf"})
    assert query.format == "pdf"
    request = {"requestContext": {"http": {"method": "GET"}}, "queryStringParameters": {"system_id": "vps", "report_date": "2026-10-19", "format": "pdf"}}
    with patch.dict(os.environ, {"GENERATED_REPORTS_BUCKET": "shared", "REPORT_SYSTEM_IDS": '["vps"]'}), patch.object(handler, "OperationalReportsRepository") as repo:
        repo.return_value.get.return_value = REPORT
        repo.return_value.get_pdf.return_value = None
        result = handler.lambda_handler(request, None)
        assert result["statusCode"] == 200 and result["isBase64Encoded"] is True
        assert result["headers"]["Content-Type"] == "application/pdf"
        assert base64.b64decode(result["body"]).startswith(b"%PDF-")
        assert "glorify-vps-2026-10-19.pdf" in result["headers"]["Content-Disposition"]


def test_reader_falls_back_to_historical_bucket_without_exposing_raw_prefix():
    client = Mock()
    client.get_object.side_effect = [ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject"), {"Body": io.BytesIO(gzip.compress(json.dumps(REPORT).encode()))}]
    repo = repository.OperationalReportsRepository("shared", client, fallback_bucket="legacy")
    key = models.parse_query({"system_id": "vps", "report_date": "2026-10-19"}).key
    assert repo.get(key) == REPORT
    assert [c.kwargs["Bucket"] for c in client.get_object.call_args_list] == ["shared", "legacy"]
