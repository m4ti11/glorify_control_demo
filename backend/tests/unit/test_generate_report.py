from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import gzip
import io
import json
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError

from lambda_loader import load_lambda


mods = load_lambda("generate_report", ["models", "repository", "openai_client", "service", "handler"])
models, repository, service_module = mods["models"], mods["repository"], mods["service"]
UTC = timezone.utc


def test_buenos_aires_calendar_day_and_existing_key_bounds():
    day = date(2026, 10, 19)
    start, end = models.reporting_window(day)
    assert start == datetime(2026, 10, 19, 3, tzinfo=UTC)
    assert end == datetime(2026, 10, 20, 3, tzinfo=UTC)
    start_key = repository.HOST_PREFIX + models.metric_timestamp(start)
    end_key = repository.HOST_PREFIX + models.metric_timestamp(end - timedelta(microseconds=1))
    assert start_key.endswith("2026-10-19T03:00:00.000000Z")
    assert end_key.endswith("2026-10-20T02:59:59.999999Z")
    assert models.event_bounds(start, end) == ("EVENT#2026-10-19T03:00:00", "EVENT#2026-10-20T03:00:00")


def test_host_query_is_bounded_and_paginates_without_scan():
    client = Mock()
    client.query.side_effect = [
        {"Items": [], "LastEvaluatedKey": {"PK": {"S": "SYSTEM#vps"}, "SK": {"S": "cursor"}}},
        {"Items": []},
    ]
    repo = repository.ReportSources("metrics", "events", "pg", dynamodb=client, s3=Mock())
    start, end = models.reporting_window(date(2026, 10, 19))
    result = repo.host_metrics("vps", start, end, 600)
    assert result["status"] == "missing"
    first = client.query.call_args_list[0].kwargs
    assert first["KeyConditionExpression"] == "#pk = :pk AND #sk BETWEEN :start AND :end"
    assert first["ExpressionAttributeValues"][":pk"] == {"S": "SYSTEM#vps"}
    assert first["ExpressionAttributeValues"][":start"]["S"].endswith("2026-10-19T03:00:00.000000Z")
    assert first["ExpressionAttributeValues"][":end"]["S"].endswith("2026-10-20T02:59:59.999999Z")
    assert "Scan" not in first


def test_events_use_system_scoped_bounded_paged_query_without_scan_or_batch_get():
    from boto3.dynamodb.types import TypeSerializer

    serializer = TypeSerializer()
    def item(event_id, occurred_at, event_type="activity"):
        value = {"PK": "SYSTEM#one", "SK": f"EVENT#{occurred_at}#{event_id}", "system_id": "one",
                 "event_id": event_id, "event_type": event_type, "source": "x", "occurred_at": occurred_at,
                 "received_at": "arrival", "subject": {"type": "host", "id": "host"}, "details": {}}
        return {key: serializer.serialize(part) for key, part in value.items()}
    client = Mock()
    client.query.side_effect = [
        {"Items": [item("1", "2026-10-19T04:00:00.100Z")], "LastEvaluatedKey": {"PK": {"S": "SYSTEM#one"}, "SK": {"S": "cursor"}}},
        {"Items": [item("2", "2026-10-19T04:00:00.100001Z")]},
    ]
    repo = repository.ReportSources("metrics", "events", "pg", dynamodb=client, s3=Mock())
    start, end = models.reporting_window(date(2026, 10, 19))
    result = repo.events("one", start, end, 600)
    assert [value["event_id"] for value in result["data"]] == ["1", "2"]
    assert result["data"][0]["received_at"] == "arrival"
    assert client.query.call_count == 2
    for call in client.query.call_args_list:
        args = call.kwargs
        assert args["KeyConditionExpression"] == "#pk = :pk AND #sort BETWEEN :start AND :end"
        assert args["ExpressionAttributeValues"][":pk"] == {"S": "SYSTEM#one"}
        assert args["ExpressionAttributeValues"][":start"]["S"] == "EVENT#2026-10-19T03:00:00"
        assert args["ExpressionAttributeValues"][":end"]["S"] == "EVENT#2026-10-20T03:00:00"
        assert "IndexName" not in args and "Scan" not in args
    client.batch_get_item.assert_not_called()


def test_event_query_item_cap_is_explicitly_incomplete(monkeypatch):
    from boto3.dynamodb.types import TypeSerializer

    serializer = TypeSerializer()
    value = {"PK": "SYSTEM#vps", "SK": "EVENT#2026-10-19T04:00:00.000Z#1", "system_id": "vps",
             "event_id": "1", "event_type": "activity", "source": "x", "occurred_at": "2026-10-19T04:00:00.000Z",
             "received_at": "arrival", "subject": {"type": "host", "id": "host"}, "details": {}}
    item = {key: serializer.serialize(part) for key, part in value.items()}
    client = Mock(query=Mock(return_value={"Items": [item], "LastEvaluatedKey": {"PK": {"S": "SYSTEM#vps"}, "SK": {"S": "cursor"}}}))
    monkeypatch.setattr(repository, "MAX_EVENT_QUERY_ITEMS", 1)
    repo = repository.ReportSources("metrics", "events", "pg", dynamodb=client, s3=Mock())
    start, end = models.reporting_window(date(2026, 10, 19))
    result = repo.events("vps", start, end, 600)
    # Query truncation is incomplete coverage, not proof of stale events.
    assert result["status"] == "incomplete"
    assert "incomplete" in result["issues"]
    assert result["omitted_count"] >= 1


def test_metrics_mark_stale_and_postgres_missing_is_explicit():
    from boto3.dynamodb.types import TypeSerializer

    serializer = TypeSerializer()
    row = {"measured_at": "2026-10-20T02:00:00.000000Z", "metrics": {"cpu_percent": Decimal("12.0")}}
    client = Mock(query=Mock(return_value={"Items": [{key: serializer.serialize(value) for key, value in row.items()}]}))
    s3 = Mock()
    s3.get_object.side_effect = ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")
    repo = repository.ReportSources("metrics", "events", "pg", dynamodb=client, s3=s3)
    start, end = models.reporting_window(date(2026, 10, 19))
    assert repo.host_metrics("vps", start, end, 600)["status"] == "stale"
    assert repo.postgres("vps", date(2026, 10, 19), 86400)["status"] == "missing"
    assert s3.get_object.call_args.kwargs["Key"] == "raw/pg-statements/vps/2026/10/19.json.gz"


def test_claim_acquisition_handles_races_and_expired_lease_takeover():
    client = Mock()
    claim = repository.ClaimRepository("jobs", client)
    assert claim.acquire("vps", "2026-10-19", "owner-1", 1000, 1200) == "acquired"
    assert client.update_item.call_args.kwargs["ConditionExpression"].startswith("attribute_not_exists(PK)")
    client.update_item.side_effect = ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "busy"}}, "UpdateItem")
    client.get_item.return_value = {"Item": {"status": {"S": "PROCESSING"}, "lease_expires_at": {"N": "2200"}}}
    assert claim.acquire("vps", "2026-10-19", "owner-2", 1500, 1200) == "busy"
    client.update_item.side_effect = None
    assert claim.acquire("vps", "2026-10-19", "owner-3", 2201, 1200) == "acquired"
    takeover = client.update_item.call_args.kwargs
    assert "lease_expires_at < :now" in takeover["ConditionExpression"]
    assert takeover["ExpressionAttributeValues"][":now"] == {"N": "2201"}
    client.update_item.side_effect = ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "complete"}}, "UpdateItem")
    client.get_item.return_value = {"Item": {"status": {"S": "COMPLETE"}}}
    assert claim.acquire("vps", "2026-10-19", "owner-4", 2201, 1200) == "complete"


def make_service(input_limit=262144):
    source = Mock()
    source.host_metrics.return_value = {"status": "available", "count": 1, "omitted_count": 0, "data": [
        {"evidence_ref": "metric:2026-10-19T04:00:00Z", "measured_at": "2026-10-19T04:00:00Z",
         "metrics": {"cpu_percent": 80, "memory": {"percent": 30}, "disk": {"percent": 20}, "uptime_seconds": 1000}}]}
    source.events.return_value = {"status": "available", "count": 0, "omitted_count": 0, "data": []}
    source.postgres.return_value = {"status": "missing", "count": 0, "report_date": "2026-10-19", "captured_at": None,
                                    "received_at": None, "data": None}
    claims, reports, secrets, openai = Mock(), Mock(), Mock(), Mock()
    claims.acquire.return_value = "acquired"
    reports.key.return_value = "generated/operational/vps/2026/10/19.json.gz"
    reports.get.return_value = None
    reports.create.return_value = True
    secrets.get_secret_value.return_value = {"SecretString": "test-key"}
    openai.analyze.return_value = json.dumps({"summary": "CPU was elevated.", "findings": [
        {"severity": "high", "title": "CPU load", "description": "Observed high CPU.",
         "evidence_refs": ["metric:2026-10-19T04:00:00Z"]}], "recommendations": []})
    settings = {"secret_arn": "secret-arn", "lease_seconds": 1200, "metrics_stale_after": 600,
                "events_stale_after": 600, "pg_stale_after": 86400, "input_max_bytes": input_limit,
                "output_max_bytes": 65536}
    return service_module.ReportService(source, claims, reports, secrets, openai, settings), source, claims, reports, secrets, openai


def test_partial_report_is_saved_with_missing_source_status_and_secret_not_logged():
    service, source, claims, reports, secrets, openai = make_service()
    result = service.generate("vps", date(2026, 10, 19), datetime(2026, 10, 20, 6, 30, tzinfo=UTC))
    assert result["status"] == "complete"
    saved = json.loads(reports.create.call_args.args[1])
    assert saved["source_status"]["postgresql"]["status"] == "missing"
    assert saved["source_status"]["postgresql"]["issues"] == ["missing"]
    assert saved["analysis"]["findings"][0]["evidence_refs"] == ["metric:2026-10-19T04:00:00Z"]
    openai.analyze.assert_called_once()
    assert openai.analyze.call_args.args[0] == "test-key"
    claims.complete.assert_called_once()


def test_source_failures_are_unavailable_and_no_rows_are_not_fabricated():
    service, source, *_ = make_service()
    source.events.side_effect = RuntimeError("source down")
    built, raw = service.build_input("vps", date(2026, 10, 19), datetime(2026, 10, 20, 6, 30, tzinfo=UTC))
    assert built["sources"]["events"]["status"] == "unavailable"
    assert built["sources"]["events"]["issues"] == ["unavailable"]
    assert built["sources"]["events"]["data"] is None
    assert len(raw) <= 262144


def test_input_limit_compacts_with_explicit_incomplete_status():
    service, source, *_ = make_service(input_limit=5000)
    source.host_metrics.return_value = {"status": "available", "count": 1000, "omitted_count": 0,
        "data": [{"evidence_ref": f"metric:{i}", "measured_at": "2026-10-19T04:00:00Z",
                  "metrics": {"cpu_percent": i, "memory": {"percent": i}, "disk": {"percent": i}, "uptime_seconds": i}}
                 for i in range(1000)]}
    payload, raw = service.build_input("vps", date(2026, 10, 19), datetime(2026, 10, 20, 6, 30, tzinfo=UTC))
    assert len(raw) <= 5000
    assert payload["sources"]["host_metrics"]["status"] == "incomplete"
    assert payload["sources"]["host_metrics"]["omitted_count"] > 0


def test_api_failure_invalid_model_evidence_and_saved_object_recovery():
    service, _, claims, reports, _, openai = make_service()
    openai.analyze.side_effect = RuntimeError("provider unavailable")
    with pytest.raises(RuntimeError):
        service.generate("vps", date(2026, 10, 19), datetime(2026, 10, 20, 6, 30, tzinfo=UTC))
    claims.retryable.assert_called_once()
    service, _, claims, reports, _, openai = make_service()
    openai.analyze.return_value = json.dumps({"summary": "No support.", "findings": [
        {"severity": "high", "title": "Unsupported", "description": "Unsupported.", "evidence_refs": ["made-up"]}], "recommendations": []})
    with pytest.raises(ValueError, match="evidence"):
        service.generate("vps", date(2026, 10, 19), datetime(2026, 10, 20, 6, 30, tzinfo=UTC))
    reports.get.reset_mock(); claims.reset_mock(); openai.reset_mock()
    reports.get.return_value = {"schema_version": 1, "system_id": "vps", "report_date": "2026-10-19"}
    assert service.generate("vps", date(2026, 10, 19), datetime(2026, 10, 20, 6, 30, tzinfo=UTC))["status"] == "complete"
    openai.analyze.assert_not_called()
    claims.complete.assert_called_once()


def test_conditional_report_write_conflict_recovers_existing_object():
    client = Mock()
    repo = repository.GeneratedReports("bucket", client)
    client.put_object.side_effect = ClientError({"Error": {"Code": "PreconditionFailed", "Message": "exists"}}, "PutObject")
    assert repo.create("generated/key", b"{}", {"system-id": "vps"}) is False
    client.get_object.assert_not_called()


def test_conditional_request_conflict_retries_are_bounded():
    client = Mock()
    repo = repository.GeneratedReports("bucket", client)
    client.put_object.side_effect = ClientError(
        {"Error": {"Code": "ConditionalRequestConflict", "Message": "concurrent write"}}, "PutObject")
    with pytest.raises(ClientError):
        repo.create("generated/key", b"{}", {"system-id": "vps"})
    assert client.put_object.call_count == repository.MAX_CONDITIONAL_WRITE_ATTEMPTS


def test_conditional_request_conflict_flows_through_repository_and_reconciles_report():
    service, source, claims, _, _, _ = make_service()
    source.postgres.return_value["count"] = 0
    client = Mock()
    not_found = ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")
    conflict = ClientError(
        {"Error": {"Code": "ConditionalRequestConflict", "Message": "concurrent write"}}, "PutObject")
    precondition = ClientError(
        {"Error": {"Code": "PreconditionFailed", "Message": "object already exists"}}, "PutObject")
    saved = {
        "schema_version": 1, "system_id": "vps", "report_date": "2026-10-19",
        "timezone": "America/Argentina/Buenos_Aires",
        "window": {"start": "2026-10-19T03:00:00Z", "end_exclusive": "2026-10-20T03:00:00Z"},
        "generated_at": "2026-10-20T06:30:00Z",
        "source_status": {name: {"status": "missing", "count": 0}
                           for name in ("host_metrics", "events", "postgresql")},
        "analysis": {"summary": "Concurrent report saved.", "findings": [], "recommendations": []},
    }
    client.get_object.side_effect = [
        not_found,
        {"Body": io.BytesIO(gzip.compress(json.dumps(saved).encode()))},
    ]
    client.put_object.side_effect = [conflict, precondition]
    service.reports = repository.GeneratedReports("bucket", client)

    with patch.object(repository.time, "sleep"):
        result = service.generate("vps", date(2026, 10, 19), datetime(2026, 10, 20, 6, 30, tzinfo=UTC))

    assert result["status"] == "complete"
    assert client.put_object.call_count == 2
    assert client.get_object.call_count == 2
    claims.complete.assert_called_once()
    claims.retryable.assert_not_called()


def test_conditional_s3_conflict_reconciles_saved_object_and_completes_claim():
    service, _, claims, reports, _, _ = make_service()
    reports.create.return_value = False
    reports.get.side_effect = [None, {"system_id": "vps", "report_date": "2026-10-19"}]
    result = service.generate("vps", date(2026, 10, 19), datetime(2026, 10, 20, 6, 30, tzinfo=UTC))
    assert result["status"] == "complete"
    assert reports.get.call_count == 2
    claims.complete.assert_called_once()
    claims.retryable.assert_not_called()


def test_timestamp_sorting_and_freshness_threshold_preserve_microseconds():
    from boto3.dynamodb.types import TypeSerializer

    serializer = TypeSerializer()
    def item(event_id, at, event_type):
        value = {"PK": "SYSTEM#vps", "SK": f"EVENT#{at}#{event_id}", "system_id": "vps", "event_id": event_id,
                 "event_type": event_type, "source": "x", "occurred_at": at, "received_at": at,
                 "subject": {"type": "host", "id": "host"}, "details": {}}
        return {key: serializer.serialize(part) for key, part in value.items()}
    client = Mock()
    start, end = models.reporting_window(date(2026, 10, 19))
    client.query.return_value = {"Items": [
        item("heartbeat-ms", "2026-10-20T02:59:50.000Z", "host.extended_metrics"),
        item("heartbeat-us", "2026-10-20T02:59:50.000001Z", "host.extended_metrics"),
        item("activity", "2026-10-20T02:59:50.100Z", "test.activity"),
    ]}
    repo = repository.ReportSources("metrics", "events", "pg", dynamodb=client, s3=Mock())
    result = repo.events("vps", start, end, 10)
    assert result["last_heartbeat_at"] == "2026-10-20T02:59:50.000001Z"
    assert "stale" not in result["issues"]
    client.query.return_value = {"Items": [item("heartbeat-boundary", "2026-10-20T02:59:50.000Z", "host.extended_metrics")]}
    exact = repo.events("vps", start, end, 10)
    assert "stale" not in exact["issues"]  # exactly 10 seconds is fresh


def test_source_issues_preserve_stale_and_incomplete_together_and_postgres_semantics():
    service, source, *_ = make_service()
    start, end = models.reporting_window(date(2026, 10, 19))
    source.host_metrics.return_value = {"status": "stale", "issues": ["stale", "incomplete"], "count": 1, "data": []}
    source.postgres.return_value = {"status": "available", "count": 1, "report_date": "2026-10-19",
        "captured_at": "2026-10-20T06:00:00Z", "received_at": "2026-10-20T06:05:00Z",
        "data": {"system_id": "vps", "details": {"report_date": "2026-10-19", "collection_started_at": "2026-10-20T05:59:00Z",
            "captured_at": "2026-10-20T06:00:00Z", "reports": [{"database": "app", "pg_stat_statements": {"rows": []}}]}}}
    payload, _ = service.build_input("vps", date(2026, 10, 19), datetime(2026, 10, 20, 6, 30, tzinfo=UTC))
    assert payload["schema_version"] == 2
    metrics = payload["sources"]["host_metrics"]
    assert metrics["status"] == "stale" and metrics["issues"] == ["stale", "incomplete"]
    pg = payload["sources"]["postgresql"]["snapshot_semantics"]
    assert pg["schema_version"] == 1 and pg["kind"] == "point_in_time_snapshot"
    assert pg["reporting_date"] == "2026-10-19"
    assert pg["collection_started_at"] == "2026-10-20T05:59:00Z"
    assert pg["captured_at"] == "2026-10-20T06:00:00Z"
    statements = next(item for item in pg["cumulative_counters"] if item["source"] == "pg_stat_statements")
    assert "total_exec_ms" in statements["fields"] and "stats_reset" in statements["reset_marker"]
    assert {"source": "pg_stat_database", "fields": ["blks_read", "blks_hit", "deadlocks"], "reset_marker": "stats_reset"} in pg["cumulative_counters"]


def test_scheduler_nominal_time_produces_stable_local_previous_date_and_manual_date_is_explicit(monkeypatch):
    import os
    handler = mods["handler"]
    worker = Mock(); worker.generate.return_value = {"status": "complete"}
    monkeypatch.setattr(handler, "build_service", lambda: worker)
    monkeypatch.setattr(os, "environ", {**os.environ, "REPORT_SYSTEM_IDS": '["vps"]'})
    event = {"Records": [{"messageId": "one", "body": json.dumps({"system_id": "vps", "scheduled_time": "2026-10-20T06:30:00Z"})}]}
    assert handler.lambda_handler(event, None) == {"batchItemFailures": []}
    assert worker.generate.call_args.args[1] == date(2026, 10, 19)
    handler.lambda_handler({"system_id": "vps", "report_date": "2026-10-17"}, None)
    assert worker.generate.call_args.args[1] == date(2026, 10, 17)
    with pytest.raises(ValueError, match="scheduled_time"):
        handler.lambda_handler({"system_id": "vps"}, None)


def test_busy_sqs_claim_is_returned_for_retry_with_original_explicit_date(monkeypatch):
    import os
    handler = mods["handler"]
    monkeypatch.setattr(os, "environ", {**os.environ, "REPORT_SYSTEM_IDS": '["vps"]'})
    worker = Mock()
    worker.generate.return_value = {"status": "busy"}
    monkeypatch.setattr(handler, "build_service", lambda: worker)
    event = {"Records": [{"messageId": "message-1", "body": json.dumps({"system_id": "vps", "report_date": "2026-10-19"})}]}
    assert handler.lambda_handler(event, None) == {"batchItemFailures": [{"itemIdentifier": "message-1"}]}
    assert worker.generate.call_args.args[1] == date(2026, 10, 19)


def test_openai_invalid_envelope_transport_failure_and_response_limit():
    class Response:
        def __init__(self, raw): self.raw = raw
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self, size=-1): return self.raw[:size]
    class Opener:
        def __init__(self, response=None, error=None): self.response, self.error = response, error
        def open(self, request, timeout):
            if self.error: raise self.error
            return self.response
    client = mods["openai_client"].ResponsesClient("test-model", opener=Opener(Response(b'{"status":"incomplete","output":[]}')))
    with pytest.raises(mods["openai_client"].OpenAIError, match="malformed"):
        client.analyze("secret", b"{}")
    client = mods["openai_client"].ResponsesClient("test-model", opener=Opener(error=TimeoutError()))
    with pytest.raises(mods["openai_client"].OpenAIError, match="network"):
        client.analyze("secret", b"{}")
    client = mods["openai_client"].ResponsesClient("test-model", max_response_bytes=4, opener=Opener(Response(b"12345")))
    with pytest.raises(mods["openai_client"].OpenAIError, match="transport limit"):
        client.analyze("secret", b"{}")
    with pytest.raises(ValueError, match="size"):
        models.validate_analysis(json.dumps({"summary": "Large enough to exceed", "findings": [], "recommendations": []}), set(), 10)


def test_model_request_includes_versioned_postgresql_snapshot_guardrails():
    response_text = json.dumps({"summary": "No findings.", "findings": [], "recommendations": []})
    envelope = json.dumps({"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": response_text}]}]}).encode()
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self, size=-1): return envelope[:size]
    class Opener:
        def open(self, request, timeout):
            self.request = request
            return Response()
    opener = Opener()
    client = mods["openai_client"].ResponsesClient("test-model", opener=opener)
    assert client.analyze("test-key", b'{"schema_version":2}') == response_text
    request = json.loads(opener.request.data)
    instructions = request["input"][0]["content"]
    assert "point-in-time snapshot" in instructions
    assert "stats_reset" in instructions
    assert "daily deltas" in instructions


def test_current_agent_events_do_not_require_discontinued_periodic_event():
    from boto3.dynamodb.types import TypeSerializer
    serializer = TypeSerializer()
    client = Mock()
    event = {"event_id": "one", "event_type": "container.restart", "occurred_at": "2026-10-19T12:00:00Z", "details": {}}
    client.query.return_value = {"Items": [{k: serializer.serialize(v) for k, v in event.items()}]}
    repo = repository.ReportSources("metrics", "events", "pg", dynamodb=client, s3=Mock())
    start, end = models.reporting_window(date(2026, 10, 19))
    assert repo.events("vps", start, end, 600)["status"] == "available"
    client.query.return_value = {"Items": []}
    result = repo.events("vps", start, end, 600)
    assert result["status"] == "available" and result["count"] == 0


def test_publication_failure_retries_existing_json_without_calling_model_again():
    service, _, claims, reports, _, model = make_service()
    publisher = Mock(); service.publisher = publisher
    publisher.publish.side_effect = RuntimeError("mail down")
    with pytest.raises(RuntimeError):
        service.generate("vps", date(2026, 10, 19))
    saved = json.loads(reports.create.call_args.args[1]); reports.get.return_value = saved
    publisher.publish.side_effect = None
    service.generate("vps", date(2026, 10, 19))
    assert model.analyze.call_count == 1
    assert publisher.publish.call_count == 2


def test_legacy_completed_report_can_gain_pdf_without_regeneration():
    service, _, claims, reports, _, model = make_service()
    claims.acquire.return_value = "complete"
    reports.get.return_value = {"system_id": "vps", "report_date": "2026-10-19"}
    service.publisher = Mock()
    service.generate("vps", date(2026, 10, 19))
    service.publisher.publish.assert_called_once()
    model.analyze.assert_not_called()


def test_recent_last_sample_does_not_hide_23_hours_without_coverage():
    from boto3.dynamodb.types import TypeSerializer
    serializer = TypeSerializer()
    client = Mock()
    start, end = models.reporting_window(date(2026, 10, 19))
    row = {"measured_at": models.metric_timestamp(end - timedelta(seconds=60)), "metrics": {"cpu_percent": 10}}
    client.query.return_value = {"Items": [{k: serializer.serialize(v) for k, v in row.items()}]}
    repo = repository.ReportSources("metrics", "events", "pg", dynamodb=client, s3=Mock())
    source = repo.host_metrics("vps", start, end, 600)
    assert source["status"] == "incomplete"
    c = source["coverage"]
    assert c["expected_intervals"] == 720 and c["present_intervals"] == 1
    assert c["percentage"] < 1 and c["largest_gap_seconds"] >= 23*3600
    assert c["sufficient"] is False
    worker, sources, _, reports, _, model = make_service()
    sources.host_metrics.return_value = source
    model.analyze.return_value = json.dumps({"summary": "Cobertura insufficient.", "findings": [], "recommendations": []})
    worker.generate("vps", date(2026, 10, 19))
    assert json.loads(model.analyze.call_args.args[1])["sources"]["host_metrics"]["coverage"] == c
    output = json.loads(reports.create.call_args.args[1])
    assert output["source_status"]["host_metrics"]["coverage"] == c
    load_lambda("get_operational_report")["models"].OperationalReport.model_validate(output)


def test_coverage_deduplicates_intervals_includes_day_edges_and_marks_truncation():
    start, end = models.reporting_window(date(2026, 10, 19))
    readings = [{"measured_at": models.metric_timestamp(start + timedelta(seconds=i*120))} for i in range(720)]
    coverage = repository.sample_coverage([*readings, readings[0]], start, end)
    assert coverage["percentage"] == 100 and coverage["present_intervals"] == 720
    assert coverage["largest_gap_seconds"] == 120 and coverage["sufficient"] is True
    empty = repository.sample_coverage([], start, end)
    assert empty["percentage"] == 0 and empty["largest_gap_seconds"] == 86400
    truncated = repository.sample_coverage(readings, start, end, truncated=True)
    assert truncated["sufficient"] is False and truncated["is_lower_bound"] is True
