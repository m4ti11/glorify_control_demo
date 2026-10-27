import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import httpx
import pytest

from glorify_control_agent.scheduler import Agent
from glorify_control_agent.transport.client import Client
from glorify_control_agent.transport.retry import backoff


def test_transport_status_errors_and_headers(settings):
    requests = []
    real_client = httpx.Client

    def create(**kwargs):
        assert kwargs.pop("verify") is True
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        assert kwargs["timeout"].connect == settings.http_connect_timeout

        def handle(request):
            requests.append(request)
            return httpx.Response(503)

        return real_client(transport=httpx.MockTransport(handle), **kwargs)

    with patch("glorify_control_agent.transport.client.httpx.Client", side_effect=create):
        client = Client(settings)
    assert client.send("events", "{}") == 503
    assert requests[0].headers["x-api-token"] == "secret-for-test"
    assert requests[0].url.path == "/events"
    assert client.probe()["reachable"] is True
    client.close()


def test_transport_network_failure(settings):
    http = Mock()
    http.post.side_effect = httpx.ConnectError("network down")
    http.head.side_effect = httpx.ConnectError("network down")
    client = Client(settings, http=http)
    assert client.send("metrics", "{}") is None
    assert client.probe()["reachable"] is False


@pytest.mark.parametrize(
    "status,remaining,dead",
    [
        (201, 0, False),
        (202, 0, False),
        (503, 1, False),
        (429, 1, False),
        (401, 1, False),
        (403, 1, False),
        (422, 1, True),
        (302, 1, False),
        (None, 1, False),
    ],
)
def test_flush_retry_and_quarantine(settings, spool, status, remaining, dead):
    spool.enqueue("events", "{}")
    agent = Agent(settings, spool, Mock(send=Mock(return_value=status)))
    agent.flush()
    rows = spool.db.execute("SELECT * FROM queue").fetchall()
    assert len(rows) == remaining
    if rows:
        assert bool(rows[0]["dead"]) is dead
        assert rows[0]["attempts"] == 1
        assert not spool.pending()


def test_retry_backoff_is_bounded():
    for attempt in (1, 2, 10, 100000):
        delay = backoff(attempt, 5, 3600)
        assert 2.5 <= delay <= 3600


def test_poll_isolates_collectors(settings, spool):
    agent = Agent(settings, spool, Mock())
    agent.docker.collect = Mock(return_value=({"available": False, "containers": []}, False))
    agent.postgres.connections = Mock(
        return_value={
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "available": False,
            "connections": [],
        }
    )
    with patch("glorify_control_agent.scheduler.host.collect", side_effect=RuntimeError("host failed")):
        agent.poll()
        agent.postgres_poll()
    types = [json.loads(row["payload"])["event_type"] for row in spool.pending()]
    assert "docker.availability" in types and "postgres.availability" in types


def test_daily_report_only_once_after_configured_time(settings, spool):
    settings.postgres_dsn = settings.token
    settings.daily_hour = 0
    settings.daily_minute = 0
    agent = Agent(settings, spool, Mock())
    agent.postgres.collect = Mock(
        return_value=[{"scope": "cluster", "database": "cluster", "available": False}]
    )
    agent.daily()
    agent.daily()
    agent.postgres.collect.assert_called_once()
    assert len(spool.pending()) == 1
    assert json.loads(spool.pending()[0]["payload"])["details"]["available"] is False


def test_heartbeat_survives_unavailable_docker(settings, spool, ready_cpu):
    from pathlib import Path

    from glorify_control_agent.models import MetricsPayload

    sample = json.loads(
        (Path(__file__).resolve().parents[2] / "backend/lambda/ingest_metrics/test/payload.json").read_text()
    )
    agent = Agent(settings, spool, Mock(probe=Mock(return_value={"reachable": False})))
    agent.docker.collect = Mock(
        return_value=(
            {"available": False, "running_containers": 0, "total_containers": 0, "containers": []},
            False,
        )
    )
    ready_cpu(agent)
    with patch("glorify_control_agent.scheduler.host.collect", return_value=(sample["host"], {})):
        agent.heartbeat()
    rows = spool.pending()
    assert len(rows) == 1
    assert rows[0]["route"] == "metrics"
    assert MetricsPayload.model_validate_json(rows[0]["payload"]).docker.available is False


def test_heartbeats_send_extended_metrics_directly_and_only_stop_generates_event(settings, spool, ready_cpu):
    import runpy
    from pathlib import Path

    fixture = json.loads(
        (Path(__file__).resolve().parents[2] / "backend/lambda/ingest_metrics/test/payload.json").read_text()
    )
    container = fixture["docker"]["containers"][0]
    docker = {"available": True, "running_containers": 1, "total_containers": 1, "containers": [container]}
    agent = Agent(settings, spool, Mock(probe=Mock(return_value={"reachable": True, "http_status": 405})))
    agent.docker.collect = Mock(side_effect=lambda: (docker, True))
    extras = {"memory_available_mb": 1000, "network": {"bytes_sent": 1234}, "disks": []}
    ready_cpu(agent)
    with patch("glorify_control_agent.scheduler.host.collect", return_value=(fixture["host"], extras)):
        agent.poll()
        agent.heartbeat()
        agent.heartbeat()
    assert [row["route"] for row in spool.pending()] == ["metrics", "metrics"]
    cloud = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "backend/lambda/ingest_metrics/models.py")
    )["MetricsPayload"]
    payload = cloud.model_validate_json(spool.pending()[0]["payload"])
    assert payload.host.extended["memory_available_mb"] == 1000
    assert payload.host.extended["network"] == {"bytes_sent": 1234}
    assert payload.host.extended["docker_snapshot_complete"] is True
    assert container["name"] in payload.host.extended["container_uptime_seconds"]
    container["state"] = "exited"
    agent.poll()
    events = [json.loads(row["payload"]) for row in spool.pending() if row["route"] == "events"]
    assert len(events) == 1
    assert events[0]["event_type"] == "container.changed"
    assert events[0]["details"]["abnormal"] is True


def test_legacy_periodic_events_are_quarantined_without_blocking_real_events(settings, spool):
    spool.enqueue("events", json.dumps({"event_type": "host.extended_metrics"}))
    spool.enqueue("events", json.dumps({"event_type": "container.changed"}))
    spool.enqueue("metrics", "{}")
    client = Mock(send=Mock(return_value=201))
    Agent(settings, spool, client).flush()
    assert [call.args[0] for call in client.send.call_args_list] == ["events", "metrics"]
    assert "container.changed" in client.send.call_args_list[0].args[1]
    rows = spool.db.execute("SELECT * FROM queue").fetchall()
    assert len(rows) == 1 and rows[0]["dead"] == 1
    assert "host.extended_metrics" in rows[0]["payload"]


def test_rejected_payload_logs_route_and_fields_without_input_or_response_text(settings, spool, caplog):
    def respond(request):
        return httpx.Response(
            422,
            json={
                "message": "secret-message",
                "errors": [
                    {
                        "type": "extra_forbidden",
                        "loc": ["postgres"],
                        "input": "secret-payload",
                        "msg": "secret-sql",
                        "ctx": {"password": "secret-password"},
                    }
                ],
            },
        )

    spool.enqueue("metrics", "{}")
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        Agent(settings, spool, Client(settings, http=http)).flush()
    assert "delivery_failed route=metrics status=422 quarantined=True" in caplog.text
    assert "'field': 'postgres', 'type': 'extra_forbidden'" in caplog.text
    assert "secret-" not in caplog.text


def test_scheduler_configures_serial_jobs_and_coalescing(settings, spool):
    agent = Agent(settings, spool, Mock())
    with patch.object(agent.scheduler, "start"):
        agent.start()
    jobs = agent.scheduler.get_jobs()
    assert {job.id for job in jobs} == {"heartbeat", "events", "daily", "flush", "postgres"}
    assert agent.scheduler._job_defaults["max_instances"] == 1
    assert agent.scheduler._job_defaults["coalesce"] is True
    assert all(job.next_run_time <= datetime.now(timezone.utc) for job in jobs if job.id not in ("postgres", "heartbeat"))
    assert agent.scheduler.get_job("postgres").trigger.interval.total_seconds() == 60
    assert agent.scheduler.get_job("heartbeat").next_run_time > datetime.now(timezone.utc)


def test_outage_uses_persistent_global_backoff(settings, spool):
    for _ in range(10):
        spool.enqueue("events", "{}")
    client = Mock(send=Mock(return_value=503))
    Agent(settings, spool, client).flush()
    client.send.assert_called_once()
    assert spool.get_state("delivery_backoff")["attempts"] == 1
    restarted = Agent(settings, spool, client)
    restarted.flush()
    assert client.send.call_count == 1
    assert len(spool.db.execute("SELECT * FROM queue").fetchall()) == 10


def test_daily_failed_enqueue_does_not_mark_report_completed(settings, spool):
    settings.postgres_dsn = settings.token
    settings.daily_hour = 0
    settings.daily_minute = 0
    agent = Agent(settings, spool, Mock())
    agent.postgres.collect = Mock(
        return_value=[{"scope": "cluster", "database": "cluster", "available": False}]
    )
    with patch.object(spool, "enqueue", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            agent.daily()
    assert spool.get_state("daily_completed") is None
    assert spool.get_state("daily:cluster:cluster") is None
    agent.daily()
    assert len(spool.pending()) == 1


def test_normalize_pg_values():
    from decimal import Decimal

    from glorify_control_agent.scheduler import normalize

    assert normalize({"ratio": Decimal("12.5"), "reset": datetime(2026, 1, 1, tzinfo=timezone.utc)}) == {
        "ratio": 12.5,
        "reset": "2026-01-01T00:00:00+00:00",
    }


def test_scheduler_prevents_real_overlapping_executions(settings, spool):
    import threading
    import time

    agent = Agent(settings, spool, Mock())
    entered = threading.Event()
    release = threading.Event()
    counter_lock = threading.Lock()
    active, maximum = 0, 0

    def slow():
        nonlocal active, maximum
        with counter_lock:
            active += 1
            maximum = max(maximum, active)
        entered.set()
        release.wait(2)
        with counter_lock:
            active -= 1

    agent.scheduler.add_job(slow, "interval", seconds=0.02)
    agent.scheduler.start()
    try:
        assert entered.wait(1)
        time.sleep(0.12)
    finally:
        release.set()
        agent.stop()
    assert maximum == 1


def test_daily_aggregates_all_databases_into_one_s3_report(settings, spool):
    import runpy
    from pathlib import Path

    settings.postgres_dsn = settings.token
    settings.daily_hour = 0
    settings.daily_minute = 0
    agent = Agent(settings, spool, Mock())
    agent.postgres.collect = Mock(
        return_value=[
            {"scope": "cluster", "database": "cluster", "available": True},
            {
                "scope": "database",
                "database": "app",
                "available": True,
                "top_total": {"available": True, "rows": [{"queryid": "7823456789012345678", "calls": 10}]},
            },
            {"scope": "database", "database": "another", "available": False},
        ]
    )
    agent.daily()
    rows = spool.pending()
    assert len(rows) == 1
    assert rows[0]["route"] == "pg-statements"
    payload = json.loads(rows[0]["payload"])
    assert len(payload["details"]["reports"]) == 3
    assert payload["details"]["scope"] == "system"
    cloud = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "backend/lambda/ingest_pg_statements/models.py")
    )["PgStatementsPayload"]
    cloud.model_validate_json(rows[0]["payload"])
    assert spool.get_state("daily_completed") == payload["details"]["report_date"]


def test_daily_report_labels_previous_local_day_and_records_capture_time(settings, spool):
    from zoneinfo import ZoneInfo
    import glorify_control_agent.scheduler as scheduler

    settings.postgres_dsn = settings.token
    settings.daily_hour = 0
    settings.daily_minute = 0
    agent = Agent(settings, spool, Mock())
    agent.postgres.collect = Mock(return_value=[{"scope": "cluster", "database": "cluster", "available": True}])
    fixed = datetime(2026, 10, 20, 3, 0, tzinfo=ZoneInfo(settings.timezone))
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)
    with patch.object(scheduler, "datetime", FixedDateTime):
        agent.daily()
    payload = json.loads(spool.pending()[0]["payload"])
    assert payload["details"]["report_date"] == "2026-10-19"
    assert payload["details"]["captured_at"] == "2026-10-20T06:00:00.000Z"
    assert payload["details"]["collection_started_at"] == "2026-10-20T06:00:00.000Z"


def test_duplicate_daily_report_is_acknowledged_but_event_conflict_is_not(settings, spool):
    spool.enqueue("pg-statements", "{}")
    agent = Agent(settings, spool, Mock(send=Mock(return_value=409)))
    agent.flush()
    assert not spool.pending()
    spool.enqueue("events", "{}")
    agent.flush()
    assert spool.db.execute("SELECT dead FROM queue").fetchone()[0] == 1


def test_postgres_route_uses_new_authenticated_endpoint(settings):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(201)

    with httpx.Client(
        transport=httpx.MockTransport(handle), headers={"X-Api-Token": "secret-for-test"}
    ) as http:
        client = Client(settings, http=http)
        assert client.send("pg-statements", "{}") == 201
    assert requests[0].url.path == "/pg-statements"
    assert requests[0].headers["x-api-token"] == "secret-for-test"


def test_daily_large_report_uses_separate_bounded_spool_limit(settings, spool):
    large = "x" * (300 * 1024)
    spool.enqueue("pg-statements", large)
    assert spool.pending()[0]["route"] == "pg-statements"
    with pytest.raises(ValueError):
        spool.enqueue("events", large)
    with pytest.raises(ValueError):
        spool.enqueue("pg-statements", "x" * (1024 * 1024 + 1))


def test_poll_does_not_generate_host_threshold_events(settings, spool):
    agent = Agent(settings, spool, Mock())
    agent.docker.collect = Mock(return_value=({"available": True, "containers": []}, True))
    with patch("glorify_control_agent.scheduler.host.collect") as collect:
        agent.poll()
    collect.assert_not_called()
    assert not spool.pending()


def test_minute_samples_survive_restart_and_transfer_atomically(settings, spool, ready_cpu):
    from pathlib import Path

    settings.postgres_dsn = settings.token
    agent = Agent(settings, spool, Mock(probe=Mock(return_value={"reachable": True})))
    sample = {
        "timestamp": "2026-10-22T12:00:00Z",
        "available": True,
        "connections": [{"database": "app", "connections": 6, "active": 1, "idle": 5}],
    }
    agent.postgres.connections = Mock(return_value=sample)
    agent.postgres_poll()
    agent.postgres_poll()
    restarted = Agent(settings, spool, agent.client)
    fixture = json.loads(
        (Path(__file__).resolve().parents[2] / "backend/lambda/ingest_metrics/test/payload.json").read_text()
    )
    restarted.docker.collect = Mock(return_value=(fixture["docker"], True))
    ready_cpu(restarted)
    with patch("glorify_control_agent.scheduler.host.collect", return_value=(fixture["host"], {})):
        with patch.object(spool, "enqueue", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                restarted.heartbeat()
        assert len(spool.get_state("postgres_samples")) == 2
        restarted.heartbeat()
    assert spool.get_state("postgres_samples") == []
    payload = json.loads(next(row for row in spool.pending() if row["route"] == "metrics")["payload"])
    assert len(payload["postgres"]["samples"]) == 2
    assert payload["postgres"]["samples"][0]["connections"] == sample["connections"]
    import runpy

    cloud = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "backend/lambda/ingest_metrics/models.py")
    )["MetricsPayload"]
    cloud.model_validate(payload)


def test_daily_collects_requested_statement_snapshot_and_serializes_all_fields(settings, spool):
    import runpy
    from decimal import Decimal
    from pathlib import Path
    from unittest.mock import MagicMock

    example = json.loads(
        (Path(__file__).resolve().parents[1] / "examples/daily-pg-statements.json").read_text()
    )
    expected = example["details"]["reports"][0]["pg_stat_statements"]["rows"][0]
    row = dict(expected)
    row["queryid"] = int(row["queryid"])
    for key in ("collected_at", "stats_reset"):
        row[key] = datetime.fromisoformat(row[key])
    for field in ("total", "mean", "min", "max", "stddev"):
        key = f"{field}_exec_ms"
        row[key] = Decimal(str(row[key]))
    settings.postgres_dsn = settings.token
    settings.daily_hour = settings.daily_minute = 0
    conn = MagicMock()
    conn.__enter__.return_value = conn
    metadata, result = MagicMock(), MagicMock()
    metadata.fetchone.return_value = {"nspname": "public", "split_io_times": False}
    result.fetchall.return_value = [row]
    conn.execute.side_effect = [metadata, result]
    agent = Agent(settings, spool, Mock())
    with patch("glorify_control_agent.collectors.postgres.psycopg.connect", return_value=conn) as connect:
        agent.daily()
    connect.assert_called_once()
    queued = spool.pending()[0]
    assert queued["route"] == "pg-statements"
    payload = json.loads(queued["payload"])
    assert payload["details"]["reports"][0]["pg_stat_statements"]["rows"] == [expected]
    cloud = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "backend/lambda/ingest_pg_statements/models.py")
    )["PgStatementsPayload"]
    cloud.model_validate(payload)
