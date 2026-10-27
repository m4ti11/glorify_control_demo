from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from glorify_control_agent.collectors import host
from glorify_control_agent.collectors.docker import DockerCollector
from glorify_control_agent.collectors.postgres import PostgresCollector
from glorify_control_agent.models import HostMetrics


def test_host_metrics_with_optional_failures(cpu_clock):
    memory = SimpleNamespace(total=1024**3, used=512 * 1024**2, available=512 * 1024**2, percent=50)
    disk = SimpleNamespace(total=10 * 1024**3, used=5 * 1024**3, free=5 * 1024**3, percent=50)
    with (
        patch.object(host.psutil, "virtual_memory", return_value=memory),
        patch.object(host.psutil, "disk_usage", side_effect=[disk, OSError()]),
        patch.object(host.psutil, "net_io_counters", side_effect=OSError()),
        patch.object(host.os, "getloadavg", side_effect=OSError()),
    ):
        cpu_clock.advance(120, user=48, idle=192)
        metrics, extras = host.collect(["/", "/missing"], cpu_clock.sampler.sample(120))
    HostMetrics.model_validate(metrics)
    assert metrics["cpu_percent"] == 20
    assert extras["cpu"]["window_seconds"] == 120
    assert extras["network"] is None
    assert extras["load_average"] is None
    assert len(extras["disks"]) == 1


def test_primary_disk_failure_is_explicit():
    with patch.object(host.psutil, "disk_usage", side_effect=OSError()):
        with pytest.raises(RuntimeError):
            host.collect(["/"], None)


def test_docker_unavailable(settings):
    with patch(
        "glorify_control_agent.collectors.docker.docker.APIClient", side_effect=RuntimeError("socket down")
    ):
        result, complete = DockerCollector(settings).collect()
    assert not result["available"] and not complete


def test_docker_stats_failure_does_not_hide_container(settings):
    client = MagicMock()
    client.containers.return_value = [{"Id": "abc"}]
    client.inspect_container.return_value = {
        "Name": "/app",
        "Config": {"Image": "app:latest"},
        "RestartCount": 2,
        "State": {
            "Status": "running",
            "Running": True,
            "StartedAt": datetime.now(timezone.utc).isoformat(),
            "Health": {"Status": "healthy"},
        },
    }
    client.stats.side_effect = RuntimeError("stats missing")
    with patch("glorify_control_agent.collectors.docker.docker.APIClient", return_value=client):
        result, complete = DockerCollector(settings).collect()
    assert result["available"] and complete
    assert result["containers"][0]["restart_count"] == 2
    assert result["containers"][0]["cpu_percent"] is None
    assert result["containers"][0]["memory_usage_mb"] is None
    assert result["running_containers"] == 1
    client.close.assert_called_once()
    assert not client.start.called and not client.stop.called


def test_postgres_unavailable(settings):
    settings.postgres_dsn = settings.token
    with patch(
        "glorify_control_agent.collectors.postgres.psycopg.connect",
        side_effect=RuntimeError("password=secret"),
    ):
        collector = PostgresCollector(settings)
        assert collector.connections()["available"] is False
        assert collector.collect() == [
            {
                "scope": "cluster",
                "database": "cluster",
                "available": False,
                "pg_stat_statements": {"available": False, "rows": []},
            }
        ]


def test_postgres_readonly_and_timeout(settings):
    settings.postgres_dsn = settings.token
    with patch("glorify_control_agent.collectors.postgres.psycopg.connect") as connect:
        PostgresCollector(settings).connect("app")
    kwargs = connect.call_args.kwargs
    assert kwargs["dbname"] == "app"
    assert "default_transaction_read_only=on" in kwargs["options"]
    assert "statement_timeout=" in kwargs["options"]
    assert kwargs["connect_timeout"] > 0


@pytest.mark.parametrize("split_io_times", [False, True])
def test_postgres_daily_global_top_30_includes_requested_fields(settings, split_io_times):
    settings.postgres_dsn = settings.token
    conn = MagicMock()
    conn.__enter__.return_value = conn
    row = {"queryid": 7823456789012345678, "query": "SELECT * FROM orders WHERE id = $1"}
    metadata, result = MagicMock(), MagicMock()
    metadata.fetchone.return_value = {"nspname": 'monitor"stats', "split_io_times": split_io_times}
    result.fetchall.return_value = [row]
    conn.execute.side_effect = [metadata, result]
    with patch("glorify_control_agent.collectors.postgres.psycopg.connect", return_value=conn) as connect:
        reports = PostgresCollector(settings).collect()
    connect.assert_called_once()
    assert "dbname" not in connect.call_args.kwargs  # Uses only the DSN database.
    assert len(reports) == 1
    assert reports[0]["available"] is True
    rows = reports[0]["pg_stat_statements"]["rows"]
    assert rows == [{**row, "queryid": "7823456789012345678"}]
    statement = conn.execute.call_args_list[1].args[0].as_string()
    assert 'FROM "monitor""stats"."pg_stat_statements" s' in statement
    assert 'CROSS JOIN "monitor""stats"."pg_stat_statements_info" i' in statement
    assert "ORDER BY s.total_exec_time DESC" in statement
    assert "LIMIT 30" in statement
    assert "current_database()" not in statement and "GROUP BY" not in statement
    for field in (
        "dbid",
        "userid",
        "queryid",
        "toplevel",
        "query",
        "calls",
        "rows",
        "shared_blks_hit",
        "shared_blks_read",
        "shared_blks_dirtied",
        "shared_blks_written",
        "temp_blks_read",
        "temp_blks_written",
    ):
        assert f"s.{field}" in statement
    for field in ("total", "mean", "min", "max", "stddev"):
        assert f"ROUND(s.{field}_exec_time::numeric, 2) AS {field}_exec_ms" in statement
    assert "current_timestamp AS collected_at" in statement
    assert "version() AS postgres_version" in statement
    assert "d.datname AS database" in statement
    assert "i.stats_reset" in statement
    prefix = "shared_" if split_io_times else ""
    for operation in ("read", "write"):
        assert f's."{prefix}blk_{operation}_time" AS blk_{operation}_time' in statement
    assert "pg_stat_statements_reset" not in statement


@pytest.mark.parametrize("failure", ["missing_extension", "permission_denied", "old_extension"])
def test_postgres_daily_unavailable_is_explicit(settings, failure):
    settings.postgres_dsn = settings.token
    conn = MagicMock()
    conn.__enter__.return_value = conn
    metadata = MagicMock()
    metadata.fetchone.return_value = (
        None
        if failure == "missing_extension"
        else {
            "nspname": "public",
            "split_io_times": False,
        }
    )
    conn.execute.side_effect = [metadata, RuntimeError(failure)]
    with patch("glorify_control_agent.collectors.postgres.psycopg.connect", return_value=conn):
        report = PostgresCollector(settings).collect()[0]
    assert report["available"] is False
    assert report["pg_stat_statements"] == {"available": False, "rows": []}


def test_optional_pg_query_failure_is_partial():
    connection = MagicMock()
    connection.execute.side_effect = RuntimeError("permission denied")
    assert PostgresCollector.query(connection, "SELECT 1") == {"available": False, "rows": []}


def test_docker_truncation_preserves_availability(settings):
    settings.docker_max_containers = 1
    client = MagicMock()
    client.containers.return_value = [{"Id": "a"}, {"Id": "b"}]
    client.inspect_container.return_value = {
        "Name": "/app",
        "Config": {"Image": "app:latest"},
        "RestartCount": 0,
        "State": {"Status": "exited", "Running": False, "StartedAt": "0001-01-01T00:00:00Z"},
    }
    with patch("glorify_control_agent.collectors.docker.docker.APIClient", return_value=client):
        result, complete = DockerCollector(settings).collect()
    assert result["available"] is True
    assert complete is False
    assert len(result["containers"]) == 1


def test_postgres_minute_connections_uses_client_backends(settings):
    settings.postgres_dsn = settings.token
    conn = MagicMock()
    conn.__enter__.return_value = conn
    rows = [{"database": "app", "connections": 6, "active": 1, "idle": 5}]
    conn.execute.return_value.fetchall.return_value = rows
    with patch("glorify_control_agent.collectors.postgres.psycopg.connect", return_value=conn) as connect:
        sample = PostgresCollector(settings).connections()
    assert sample["available"] is True
    assert sample["connections"] == rows
    query = conn.execute.call_args.args[0]
    assert "backend_type = 'client backend'" in query
    assert "FILTER (WHERE state = 'active')" in query
    assert "FILTER (WHERE state = 'idle')" in query
    assert "GROUP BY datname" in query
    connect.assert_called_once()


def test_postgres_connections_disabled_without_dsn(settings):
    with patch("glorify_control_agent.collectors.postgres.psycopg.connect") as connect:
        assert PostgresCollector(settings).connections() is None
    connect.assert_not_called()


def test_docker_first_cpu_sample_is_unknown_but_memory_is_measured(settings):
    client = MagicMock()
    client.containers.return_value = [{"Id": "abc"}]
    client.inspect_container.return_value = {"Name": "/app", "Config": {"Image": "app"}, "RestartCount": 0,
        "State": {"Status": "running", "Running": True}}
    client.stats.side_effect = [
        {"cpu_stats": {"system_cpu_usage": 100, "cpu_usage": {"total_usage": 20}, "online_cpus": 2}, "memory_stats": {"usage": 1024**2, "limit": 2*1024**2}},
        {"cpu_stats": {"system_cpu_usage": 200, "cpu_usage": {"total_usage": 30}, "online_cpus": 2}, "memory_stats": {"usage": 1024**2, "limit": 2*1024**2}},
    ]
    with patch("glorify_control_agent.collectors.docker.docker.APIClient", return_value=client):
        collector = DockerCollector(settings)
        first = collector.collect()[0]["containers"][0]
        second = collector.collect()[0]["containers"][0]
    assert first["cpu_percent"] is None
    assert first["memory_usage_mb"] == 1
    assert second["cpu_percent"] == 20
