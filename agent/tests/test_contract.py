import json
import runpy
from pathlib import Path

import pytest

from glorify_control_agent.collectors.events import event
from glorify_control_agent.config import Settings
from glorify_control_agent.models import MetricsPayload

BACKEND = Path(__file__).resolve().parents[2] / "backend" / "lambda"


def test_existing_metrics_fixture_and_backend_contract():
    payload = json.loads((BACKEND / "ingest_metrics/test/payload.json").read_text())
    local = MetricsPayload.model_validate(payload)
    cloud = runpy.run_path(str(BACKEND / "ingest_metrics/models.py"))["MetricsPayload"]
    cloud.model_validate_json(local.model_dump_json())


def test_event_envelope_validates_against_backend(settings):
    payload = event(settings, "postgres.daily_metrics", "postgres", "database", "app", {"available": False})
    cloud = runpy.run_path(str(BACKEND / "process_events/models.py"))["EventPayload"]
    cloud.model_validate_json(payload.model_dump_json())


@pytest.mark.parametrize(
    "url",
    ["http://example/metrics", "https://user:password@example/metrics", "https://example/m?token=secret"],
)
def test_config_requires_secure_urls(url):
    with pytest.raises(ValueError):
        Settings(system_id="vps", metrics_url=url, events_url="https://example/events", token="secret")


def test_config_secrets_are_redacted(settings):
    assert "secret-for-test" not in repr(settings)


def test_invalid_timezone(settings):
    with pytest.raises(Exception):
        Settings(**{**settings.model_dump(), "timezone": "not/a-timezone"})


def test_pg_endpoint_defaults_to_same_api_and_can_be_explicit(settings):
    assert settings.pg_statements_url == "https://ingest.example/pg-statements"
    changed = Settings(
        **{**settings.model_dump(), "pg_statements_url": "https://other.example/pg-statements"}
    )
    assert changed.pg_statements_url == "https://other.example/pg-statements"
    with pytest.raises(ValueError):
        Settings(**{**settings.model_dump(), "pg_statements_url": "http://insecure.example/reports"})


def test_documented_postgres_heartbeat_matches_both_contracts():
    payload = json.loads((Path(__file__).resolve().parents[1] / "examples/heartbeat.json").read_text())
    local = MetricsPayload.model_validate(payload)
    cloud = runpy.run_path(str(BACKEND / "ingest_metrics/models.py"))["MetricsPayload"]
    validated = cloud.model_validate_json(local.model_dump_json())
    assert len(validated.postgres.samples) == 2
    assert len(validated.postgres.samples[0].connections) == 6


def test_actual_agent_jobs_match_metrics_events_and_daily_ingestion(settings, spool, ready_cpu):
    from unittest.mock import Mock, patch
    from glorify_control_agent.scheduler import Agent

    sample = MetricsPayload.model_validate_json((Path(__file__).resolve().parents[1] / "examples/heartbeat.json").read_text())
    settings.postgres_dsn = settings.token
    settings.daily_hour = settings.daily_minute = 0
    agent = Agent(settings, spool, Mock(probe=Mock(return_value={"reachable": True, "http_status": 404})))
    agent.docker.collect = Mock(return_value=(sample.docker, True))
    agent.postgres.collect = Mock(return_value=[{"scope": "database", "database": "app", "available": True,
        "database_stats": {"available": True, "rows": [{"datname": "app", "size_bytes": 12345}]}}])
    spool.set_state("postgres_samples", [s.model_dump(mode="json") for s in sample.postgres.samples])
    ready_cpu(agent)
    with patch("glorify_control_agent.scheduler.host.collect", return_value=(sample.host, {})):
        agent.heartbeat()
    agent.daily()
    spool.enqueue("events", event(settings, "container.restart", "docker", "container", "web", {"restart_count": 1}).model_dump_json())
    received = {r["route"]: r["payload"] for r in spool.pending(10)}
    for route, model_name, folder in [("metrics", "MetricsPayload", "ingest_metrics"), ("events", "EventPayload", "process_events"), ("pg-statements", "PgStatementsPayload", "ingest_pg_statements")]:
        cloud = runpy.run_path(str(BACKEND / folder / "models.py"))[model_name]
        validated = cloud.model_validate_json(received[route])
        assert validated.system_id == settings.system_id
    pg = json.loads(received["pg-statements"])["details"]
    assert pg["captured_at"].endswith("Z") and pg["collection_started_at"].endswith("Z")
    assert pg["scope"] == "system" and len(pg["reports"]) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"active": 7},
        {"idle": -1},
    ],
)
def test_postgres_connection_counts_are_validated(change):
    payload = json.loads((Path(__file__).resolve().parents[1] / "examples/heartbeat.json").read_text())
    payload["postgres"]["samples"][0]["connections"][0].update(change)
    cloud = runpy.run_path(str(BACKEND / "ingest_metrics/models.py"))["MetricsPayload"]
    for model in (MetricsPayload, cloud):
        with pytest.raises(ValueError):
            model.model_validate(payload)


def test_container_cap_matches_cloud_and_agent_settings(settings):
    namespace = runpy.run_path(str(BACKEND / "ingest_metrics/models.py"))
    from glorify_control_agent.models import MAX_CONTAINERS
    assert MAX_CONTAINERS == namespace["MAX_CONTAINERS"] == 98
    assert settings.docker_max_containers == MAX_CONTAINERS
    with pytest.raises(ValueError):
        Settings(**{**settings.model_dump(), "docker_max_containers": 99})


def test_unknown_container_usage_crosses_agent_and_cloud_contracts():
    payload = json.loads((BACKEND / "ingest_metrics/test/payload.json").read_text())
    payload["docker"]["containers"][0].update(cpu_percent=None, memory_usage_mb=None,
        memory_limit_mb=None, memory_percent=None)
    local = MetricsPayload.model_validate(payload)
    cloud = runpy.run_path(str(BACKEND / "ingest_metrics/models.py"))["MetricsPayload"]
    validated = cloud.model_validate_json(local.model_dump_json())
    assert validated.docker.containers[0].cpu_percent is None
