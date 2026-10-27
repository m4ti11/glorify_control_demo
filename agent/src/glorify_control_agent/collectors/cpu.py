"""Average host CPU from cumulative Linux counters, independent of worker thread."""

import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock

import psutil


@dataclass(frozen=True)
class CpuSnapshot:
    counters: dict[str, float]
    monotonic: float
    at: datetime
    logical_cpus: int | None


@dataclass(frozen=True)
class CpuReading:
    percent: float
    sampled_at: datetime
    metadata: dict
    start: CpuSnapshot
    end: CpuSnapshot


class CpuSampler:
    def __init__(self, counters=None, clock=None, now=None, cpu_count=None):
        self.counters = counters or psutil.cpu_times
        self.clock = clock or time.monotonic
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.cpu_count = cpu_count or psutil.cpu_count
        self.baseline = None
        self.lock = Lock()

    def _snapshot(self):
        counters = {name: float(value) for name, value in self.counters()._asdict().items()}
        return CpuSnapshot(counters, self.clock(), self.now(), self.cpu_count())

    def reset(self):
        with self.lock:
            self.baseline = self._snapshot()

    def sample(self, minimum_seconds):
        with self.lock:
            end = self._snapshot()
            start = self.baseline
            if start is None:
                self.baseline = end
                return None
            elapsed = end.monotonic - start.monotonic
            if (
                elapsed <= 0
                or start.logical_cpus != end.logical_cpus
                or start.counters.keys() != end.counters.keys()
            ):
                self.baseline = end
                return None
            # Scheduler jitter must not skip a whole heartbeat for a few milliseconds.
            if elapsed + min(0.1, minimum_seconds * 0.01) < minimum_seconds:
                return None
            deltas = {key: value - start.counters[key] for key, value in end.counters.items()}
            if any(
                not math.isfinite(value) or (value < 0 and key != "iowait") for key, value in deltas.items()
            ):
                # Reset/discontinuity: warm up again instead of inventing utilization.
                self.baseline = end
                return None
            # Linux can decrease iowait. Match psutil's nonnegative-delta handling.
            deltas = {key: max(0.0, value) for key, value in deltas.items()}
            # guest and guest_nice are already included in user/nice on Linux.
            total = sum(value for key, value in deltas.items() if key not in ("guest", "guest_nice"))
            if total <= 0:
                self.baseline = end
                return None
            busy = total - deltas.get("idle", 0.0) - deltas.get("iowait", 0.0)

            def percent(value):
                return round(min(100.0, max(0.0, value / total * 100)), 2)

            metadata = {
                "method": "cumulative_counters",
                "window_seconds": round(elapsed, 3),
                "window_start": start.at.isoformat().replace("+00:00", "Z"),
                "window_end": end.at.isoformat().replace("+00:00", "Z"),
                "logical_cpus": end.logical_cpus,
                "steal_percent": percent(deltas.get("steal", 0.0)),
                "iowait_percent": percent(deltas.get("iowait", 0.0)),
            }
            return CpuReading(percent(busy), end.at, metadata, start, end)

    def commit(self, reading):
        with self.lock:
            # A concurrent pause/resume must not restore an obsolete baseline.
            if self.baseline is reading.start:
                self.baseline = reading.end
