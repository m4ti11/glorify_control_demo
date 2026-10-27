import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from glorify_control_agent.control import SERVICES, ControlServer, execute_command, parser, send_command
from glorify_control_agent.main import main
from glorify_control_agent.scheduler import Agent
from glorify_control_agent.storage.spool import Spool


def test_pauses_persist_without_retention_expiry(settings, spool):
    agent = Agent(settings, spool, Mock())
    agent.control("pause", ["metrics", "events"])
    spool.db.execute("UPDATE state SET updated=0 WHERE key='control'")
    spool.db.commit()
    spool.prune()
    restarted = Agent(settings, spool, Mock())
    assert not restarted.enabled("metrics")
    assert not restarted.enabled("events")
    assert restarted.enabled("pg-statements")
    restarted.control("resume", ["metrics"])
    assert restarted.enabled("metrics") and not restarted.enabled("events")


def test_all_paused_jobs_do_no_collection_or_delivery(settings, spool):
    agent = Agent(settings, spool, Mock())
    agent.control("pause", list(SERVICES))
    agent.docker.collect = Mock()
    agent.postgres.connections = Mock()
    agent.postgres.collect = Mock()
    with patch("glorify_control_agent.scheduler.host.collect") as host:
        agent.heartbeat()
        agent.postgres_poll()
        agent.poll()
        agent.daily()
        agent.flush()
    for collect in (host, agent.docker.collect, agent.postgres.connections, agent.postgres.collect):
        collect.assert_not_called()
    agent.client.send.assert_not_called()
    assert not spool.pending()


def test_events_paused_still_collects_metrics_without_any_events(settings, spool, ready_cpu):
    agent = Agent(settings, spool, Mock(probe=Mock(return_value={"reachable": True})))
    agent.control("pause", ["events"])
    fixture = json.loads(
        (Path(__file__).resolve().parents[2] / "backend/lambda/ingest_metrics/test/payload.json").read_text()
    )
    agent.docker.collect = Mock(return_value=(fixture["docker"], True))
    agent.postgres.connections = Mock(
        return_value={
            "timestamp": "2026-10-22T12:00:00Z",
            "available": False,
            "connections": [],
        }
    )
    agent.events.postgres = Mock()
    agent.postgres_poll()
    ready_cpu(agent)
    with patch("glorify_control_agent.scheduler.host.collect", return_value=(fixture["host"], {})):
        agent.heartbeat()
    assert [row["route"] for row in spool.pending()] == ["metrics"]
    agent.events.postgres.assert_not_called()


def test_paused_routes_do_not_block_other_pending_messages(settings, spool):
    settings.batch_size = 1
    client = Mock(send=Mock(return_value=201))
    agent = Agent(settings, spool, client)
    spool.enqueue("metrics", "{}")
    spool.enqueue("events", "{}")
    spool.enqueue("pg-statements", "{}")
    agent.control("pause", ["metrics"])
    agent.flush()
    client.send.assert_called_once_with("events", "{}")
    assert {row["route"] for row in spool.pending()} == {"metrics", "pg-statements"}
    agent.control("pause", ["delivery"])
    agent.flush()
    assert client.send.call_count == 1
    agent.control("resume", ["delivery", "metrics"])
    agent.flush()
    assert client.send.call_count == 2
    assert [row["route"] for row in spool.pending()] == ["pg-statements"]


def test_pause_during_delivery_stops_following_sends(settings, spool):
    client = Mock()
    agent = Agent(settings, spool, client)
    for _ in range(3):
        spool.enqueue("metrics", "{}")

    def send(*_):
        agent.control("pause", ["delivery"])
        return 201

    client.send.side_effect = send
    agent.flush()
    client.send.assert_called_once()
    assert len(spool.pending()) == 2


def test_resume_wakes_only_selected_jobs(settings, spool):
    agent = Agent(settings, spool, Mock())
    agent.control("pause", list(SERVICES))
    with patch.object(agent.scheduler, "start"):
        agent.start()
    with patch.object(type(agent.scheduler), "running", new_callable=lambda: property(lambda _: True)):
        with patch.object(agent.scheduler, "modify_job") as modify:
            agent.control("resume", ["metrics"])
    assert [call.args[0] for call in modify.call_args_list] == ["postgres", "heartbeat"]
    assert not agent.enabled("events")


@pytest.mark.parametrize(
    "action,services", [("delete", ["metrics"]), ("pause", []), ("pause", "metrics"), ("pause", ["unknown"])]
)
def test_invalid_commands_do_not_change_state(settings, spool, action, services):
    with pytest.raises(ValueError):
        Agent(settings, spool, Mock()).control(action, services)
    assert spool.get_state("control") is None


def test_socket_controls_running_agent(settings, spool, tmp_path):
    path = str(tmp_path / "ctl.sock")
    server = ControlServer(path, Agent(settings, spool, Mock()))
    server.start()
    try:
        assert os.stat(path).st_mode & 0o777 == 0o600
        result = send_command(path, "pause", ["events"])
        assert result["services"] == {"events": "paused"}
        assert send_command(path, "resume", ["events"])["services"] == {"events": "running"}
        with pytest.raises(ValueError):
            send_command(path, "pause", ["unknown"])
    finally:
        server.close()
    assert not Path(path).exists()


def test_cli_start_resumes_existing_process_without_systemctl():
    args = parser().parse_args(["start", "--metrics", "--events"])
    with (
        patch("glorify_control_agent.control.send_command", return_value={"ok": True}),
        patch("glorify_control_agent.control.subprocess.run") as run,
    ):
        assert execute_command(args) == 0
    run.assert_not_called()


def test_cli_start_starts_systemd_only_when_absent():
    args = parser().parse_args(["start", "--all"])
    with (
        patch(
            "glorify_control_agent.control.send_command", side_effect=[FileNotFoundError(), {"ok": True}]
        ) as send,
        patch("glorify_control_agent.control.subprocess.run") as run,
    ):
        assert execute_command(args) == 0
    run.assert_called_once()
    assert run.call_args.args[0] == ["systemctl", "start", "glorify-control-agent"]
    assert send.call_args.args[2] == list(SERVICES)


def test_cli_rejects_conflicting_selection_without_loading_settings():
    with patch("glorify_control_agent.main.Settings") as settings:
        with pytest.raises(SystemExit) as error:
            main(["pause", "--all", "--events"])
    assert error.value.code == 2
    settings.assert_not_called()


def test_cli_pause_without_daemon_does_not_start_it(capsys):
    with (
        patch("glorify_control_agent.control.send_command", side_effect=FileNotFoundError()),
        patch("glorify_control_agent.control.subprocess.run") as run,
    ):
        assert execute_command(parser().parse_args(["pause", "--all"])) == 1
    run.assert_not_called()
    assert "is not running" in capsys.readouterr().out


def test_cross_process_cli_and_restart(tmp_path):
    # Real daemon/socket/CLI with all collection paused: no Docker, PostgreSQL or HTTP.
    spool_path = str(tmp_path / "spool.sqlite3")
    with_spool = Spool(spool_path)
    with_spool.set_state("control", list(SERVICES))
    with_spool.close()
    source = str(Path(__file__).resolve().parents[1] / "src")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GLORIFY_")}
    env.update(
        PYTHONPATH=source,
        GLORIFY_SYSTEM_ID="test",
        GLORIFY_TOKEN="test-only",
        GLORIFY_METRICS_URL="https://example.invalid/metrics",
        GLORIFY_EVENTS_URL="https://example.invalid/events",
        GLORIFY_SPOOL_PATH=spool_path,
    )
    command = [sys.executable, "-m", "glorify_control_agent.main"]
    for _ in range(2):
        daemon = subprocess.Popen(
            [*command, "run"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        try:
            deadline = time.monotonic() + 5
            while not Path(spool_path + ".control.sock").exists():
                if daemon.poll() is not None or time.monotonic() >= deadline:
                    pytest.fail("Daemon did not start")
                time.sleep(0.02)
            status = subprocess.run(
                [*command, "status", "--all"], env=env, capture_output=True, text=True, timeout=5
            )
            assert status.returncode == 0, status.stdout
            assert set(json.loads(status.stdout)["services"].values()) == {"paused"}
            result = subprocess.run(
                [*command, "start", "--delivery"], env=env, capture_output=True, text=True, timeout=5
            )
            assert result.returncode == 0, result.stdout
            assert json.loads(result.stdout)["services"] == {"delivery": "running"}
            result = subprocess.run(
                [*command, "pause", "--all"], env=env, capture_output=True, text=True, timeout=5
            )
            assert result.returncode == 0
        finally:
            daemon.terminate()
            try:
                daemon.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.communicate()
                pytest.fail("Daemon failed to shut down")
        assert daemon.returncode == 0
        assert not Path(spool_path + ".control.sock").exists()
