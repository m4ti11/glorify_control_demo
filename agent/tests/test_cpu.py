import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from glorify_control_agent.collectors import host
from glorify_control_agent.scheduler import Agent


def test_cpu_integrates_the_whole_two_cpu_window_and_exposes_steal(cpu_clock):
    # 240 CPU seconds over 120 wall seconds on two vCPUs; 13% busy,
    # with guest time already included in user (must not be counted twice).
    cpu_clock.advance(120, user=15.6, idle=207.84, iowait=0.96, steal=10.56, system=5.04, guest=2)
    reading = cpu_clock.sampler.sample(120)
    assert reading.percent == 13
    assert reading.metadata == {
        "method": "cumulative_counters",
        "window_seconds": 120,
        "window_start": "2026-10-23T12:00:00Z",
        "window_end": "2026-10-23T12:02:00Z",
        "logical_cpus": 2,
        "steal_percent": 4.4,
        "iowait_percent": 0.4,
    }


def test_brief_70_percent_spike_does_not_replace_the_interval_average(cpu_clock):
    cpu_clock.advance(0.1, user=0.14, idle=0.06)  # 70% on two CPUs for 100 ms.
    cpu_clock.advance(119.9, user=23.98, idle=215.82)  # 10% during the remainder.
    assert cpu_clock.sampler.sample(120).percent == 10.05


def test_cpu_interval_survives_switching_scheduler_threads(cpu_clock):
    cpu_clock.advance(120, user=24, idle=216)
    with ThreadPoolExecutor(1) as first:
        reading = first.submit(cpu_clock.sampler.sample, 120).result()
    cpu_clock.sampler.commit(reading)
    cpu_clock.advance(120, user=48, idle=192)
    with ThreadPoolExecutor(1) as second:
        following = second.submit(cpu_clock.sampler.sample, 120).result()
    assert following.percent == 20
    assert following.metadata["window_seconds"] == 120
    assert following.metadata["window_start"] == reading.metadata["window_end"]


def test_startup_warmup_no_fake_zero_and_real_idle_can_be_zero(cpu_clock):
    assert cpu_clock.sampler.sample(120) is None
    cpu_clock.advance(10, idle=20)
    assert cpu_clock.sampler.sample(120) is None
    cpu_clock.advance(110, idle=220)
    assert cpu_clock.sampler.sample(120).percent == 0


@pytest.mark.parametrize("counter,change", [("user", -1), ("logical_cpus", 4)])
def test_counter_reset_or_cpu_hotplug_requires_a_new_window(cpu_clock, counter, change):
    cpu_clock.advance(120, user=24, idle=216)
    cpu_clock.sampler.commit(cpu_clock.sampler.sample(120))
    cpu_clock.advance(120, idle=240)
    if counter == "logical_cpus":
        cpu_clock.logical_cpus = change
    else:
        cpu_clock.values[counter] = change
    assert cpu_clock.sampler.sample(120) is None
    cpu_clock.advance(120, user=24, idle=216)
    assert cpu_clock.sampler.sample(120).percent == 10


def test_iowait_can_decrease_without_creating_negative_cpu(cpu_clock):
    cpu_clock.values["iowait"] = 10
    cpu_clock.sampler.reset()
    cpu_clock.advance(120, user=24, idle=216, iowait=-1)
    assert cpu_clock.sampler.sample(120).percent == 10


def test_small_scheduler_jitter_keeps_actual_window(cpu_clock):
    cpu_clock.advance(119.99, user=24, idle=216)
    assert cpu_clock.sampler.sample(120).metadata["window_seconds"] == 119.99


def make_agent(settings, spool, cpu_clock):
    fixture = json.loads(
        (Path(__file__).resolve().parents[2] / "backend/lambda/ingest_metrics/test/payload.json").read_text()
    )
    agent = Agent(settings, spool, Mock(probe=Mock(return_value={"reachable": True})))
    agent.cpu = cpu_clock.sampler
    agent.docker.collect = Mock(return_value=(fixture["docker"], True))

    def collect(mounts, reading):
        return {**fixture["host"], "cpu_percent": reading.percent}, {"cpu": reading.metadata}

    return agent, collect


def test_failed_enqueue_preserves_window_and_delivery_retries_keep_the_reading(settings, spool, cpu_clock):
    agent, collect = make_agent(settings, spool, cpu_clock)
    with patch.object(host, "collect", side_effect=collect):
        agent.heartbeat()
        assert not spool.pending()  # Startup never publishes a fabricated CPU.
        cpu_clock.advance(120, user=24, idle=216)
        with patch.object(spool, "enqueue", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                agent.heartbeat()
        cpu_clock.advance(120, user=48, idle=192)
        agent.heartbeat()
    original = spool.pending()[0]["payload"]
    payload = json.loads(original)
    assert payload["host"]["cpu_percent"] == 15
    assert payload["host"]["extended"]["cpu"]["window_seconds"] == 240
    assert payload["timestamp"] == "2026-10-23T12:04:00Z"
    import runpy

    backend = Path(__file__).resolve().parents[2] / "backend/lambda"
    cloud = runpy.run_path(str(backend / "ingest_metrics/models.py"))["MetricsPayload"]
    cloud.model_validate_json(original)
    reader = runpy.run_path(str(backend / "get_latest_metrics/models.py"))["LatestMetricsSnapshot"]
    reader.model_validate(
        {
            "system_id": payload["system_id"],
            "measured_at": payload["timestamp"],
            "received_at": payload["timestamp"],
            "resources": [{"resource_type": "HOST", "resource_id": "host", "metrics": payload["host"]}],
        }
    )
    agent.client.send.return_value = 503
    agent.flush()
    stored = spool.db.execute("SELECT payload FROM queue").fetchone()["payload"]
    assert stored == original
    cpu_clock.advance(120, user=72, idle=168)
    with patch.object(host, "collect", side_effect=collect):
        agent.heartbeat()
    newest = json.loads(
        spool.db.execute("SELECT payload FROM queue ORDER BY id DESC LIMIT 1").fetchone()["payload"]
    )
    assert newest["host"]["cpu_percent"] == 30
    assert newest["host"]["extended"]["cpu"]["window_seconds"] == 120


def test_pause_resume_excludes_paused_time_and_discards_inflight_baseline(settings, spool, cpu_clock):
    agent, collect = make_agent(settings, spool, cpu_clock)
    cpu_clock.advance(120, user=24, idle=216)
    in_flight = agent.cpu.sample(120)
    agent.control("pause", ["metrics"])
    cpu_clock.advance(3600, user=7200)
    agent.control("resume", ["metrics"])
    agent.cpu.commit(in_flight)
    with patch.object(host, "collect", side_effect=collect):
        agent.heartbeat()
        assert not spool.pending()
        cpu_clock.advance(120, user=24, idle=216)
        agent.heartbeat()
    payload = json.loads(spool.pending()[0]["payload"])
    assert payload["host"]["cpu_percent"] == 10
    assert payload["host"]["extended"]["cpu"]["window_seconds"] == 120
