import pytest

from glorify_control_agent.config import Settings
from glorify_control_agent.storage.spool import Spool


@pytest.fixture
def settings(tmp_path):
    return Settings(
        system_id="test-vps",
        metrics_url="https://ingest.example/metrics",
        events_url="https://ingest.example/events",
        token="secret-for-test",
        spool_path=str(tmp_path / "spool.sqlite3"),
    )


@pytest.fixture
def spool(settings):
    queue = Spool(settings.spool_path)
    yield queue
    queue.close()


@pytest.fixture
def cpu_clock():
    from collections import namedtuple
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from glorify_control_agent.collectors.cpu import CpuSampler

    fields = "user nice system idle iowait irq softirq steal guest guest_nice".split()
    times = namedtuple("CpuTimes", fields)
    clock = SimpleNamespace(seconds=0, logical_cpus=2, values={name: 0.0 for name in fields})
    start = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    clock.sampler = CpuSampler(
        counters=lambda: times(**clock.values),
        clock=lambda: clock.seconds,
        now=lambda: start + timedelta(seconds=clock.seconds),
        cpu_count=lambda: clock.logical_cpus,
    )

    def advance(seconds, **increments):
        clock.seconds += seconds
        for name, value in increments.items():
            clock.values[name] += value

    clock.advance = advance
    clock.sampler.reset()
    return clock


@pytest.fixture
def ready_cpu(cpu_clock):
    from unittest.mock import Mock

    cpu_clock.advance(120, user=24, idle=216)
    reading = cpu_clock.sampler.sample(120)

    def ready(agent):
        agent.cpu = Mock(sample=Mock(return_value=reading))

    return ready
