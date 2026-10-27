import json
import time
from unittest.mock import patch

from glorify_control_agent.collectors.events import EventDetector


def observe(detector, value, abnormal=False):
    detector.observe("test", value, abnormal, "docker", "daemon", "docker")


def test_change_dedup_and_recovery(settings, spool):
    detector = EventDetector(settings, spool)
    observe(detector, True)
    assert not spool.pending()
    observe(detector, False, True)
    observe(detector, False, True)
    # Recreating detector preserves transition state.
    observe(EventDetector(settings, spool), False, True)
    assert len(spool.pending()) == 1
    observe(detector, True)
    assert len(spool.pending()) == 2
    assert json.loads(spool.pending()[-1]["payload"])["details"]["abnormal"] is False


def test_reminder(settings, spool):
    detector = EventDetector(settings, spool)
    observe(detector, False, True)
    future = time.time() + settings.reminder_seconds + 1
    with patch("glorify_control_agent.collectors.events.time.time", return_value=future):
        observe(detector, False, True)
        observe(detector, False, True)
        rows = spool.pending()
    assert len(rows) == 2
    assert json.loads(rows[-1]["payload"])["details"]["reminder"] is True


def docker_snapshot(containers, available=True):
    return {"available": available, "containers": containers}


def test_container_restart_disappearance_and_partial_snapshot(settings, spool):
    detector = EventDetector(settings, spool)
    container = {
        "name": "app",
        "state": "running",
        "health": "healthy",
        "restart_count": 0,
        "started_at": "start",
    }
    detector.docker(docker_snapshot([container]), True)
    assert not spool.pending()
    container["restart_count"] = 1
    detector.docker(docker_snapshot([container]), True)
    assert len(spool.pending()) == 1
    detector.docker(docker_snapshot([]), False)
    assert len(spool.pending()) == 1
    detector.docker(docker_snapshot([]), True)
    assert len(spool.pending()) == 2
    detector.docker(docker_snapshot([], False), False)
    assert len(spool.pending()) == 3  # Only daemon availability, no invented container exits.


def test_expected_container_missing_on_first_poll(settings, spool):
    settings.expected_containers = ["expected"]
    EventDetector(settings, spool).docker(docker_snapshot([]), True)
    assert json.loads(spool.pending()[0]["payload"])["subject"]["id"] == "expected"
