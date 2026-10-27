import json
import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler

from glorify_control_agent import __version__
from glorify_control_agent.collectors import host
from glorify_control_agent.collectors.cpu import CpuSampler
from glorify_control_agent.collectors.docker import DockerCollector
from glorify_control_agent.collectors.events import EventDetector, event
from glorify_control_agent.collectors.postgres import PostgresCollector
from glorify_control_agent.control import ROUTE_SERVICES, SERVICES
from glorify_control_agent.models import MetricsPayload, PostgresMetrics
from glorify_control_agent.transport.retry import backoff

log = logging.getLogger(__name__)


class Agent:
    def __init__(self, settings, spool, client):
        self.settings, self.spool, self.client = settings, spool, client
        self.cpu = CpuSampler()
        self.docker = DockerCollector(settings)
        self.postgres = PostgresCollector(settings)
        self.events = EventDetector(settings, spool)
        self.scheduler = BackgroundScheduler(
            timezone=ZoneInfo(settings.timezone),
            executors={"default": ThreadPoolExecutor(4)},
            job_defaults={"max_instances": 1, "coalesce": True, "misfire_grace_time": 60},
        )

    def enabled(self, service):
        return service not in (self.spool.get_state("control") or [])

    def control(self, action, services):
        if (
            action not in ("pause", "resume", "status")
            or not isinstance(services, list)
            or not services
            or any(not isinstance(service, str) or service not in SERVICES for service in services)
        ):
            raise ValueError("Invalid control command")
        with self.spool.lock:
            paused = set(self.spool.get_state("control") or [])
            resumed = paused.intersection(services) if action == "resume" else set()
            if action == "pause":
                paused.update(services)
            elif action == "resume":
                paused.difference_update(services)
            if action != "status":
                self.spool.set_state("control", sorted(paused))
        jobs = {
            "metrics": ("postgres", "heartbeat"),
            "events": ("events",),
            "pg-statements": ("daily",),
            "delivery": ("flush",),
        }
        if "metrics" in resumed:
            self.cpu.reset()
        if self.scheduler.running:
            for service in resumed:
                for job_id in jobs[service]:
                    delay = self.settings.heartbeat_seconds if job_id == "heartbeat" else 0
                    self.scheduler.modify_job(job_id, next_run_time=datetime.now(timezone.utc) + timedelta(seconds=delay))
        return {
            "running": self.scheduler.running,
            "services": {service: "paused" if service in paused else "running" for service in services},
        }

    @staticmethod
    def isolated(name, function):
        try:
            return function()
        except Exception:
            # Exception messages may contain credentials from third-party libraries.
            log.error("job_failed job=%s", name)
            return None

    def heartbeat(self):
        if not self.enabled("metrics"):
            return
        cpu = self.cpu.sample(self.settings.heartbeat_seconds)
        if cpu is None:
            log.info("cpu_window_warming_up")
            return
        metrics, extras = host.collect(self.settings.disk_mounts, cpu)
        docker, complete = self.docker.collect()
        extras["docker_snapshot_complete"] = complete
        payload = MetricsPayload(
            schema_version=1,
            system_id=self.settings.system_id,
            agent_version=__version__,
            timestamp=cpu.sampled_at,
            host=metrics,
            docker=docker,
        )
        extras["ingestion_connectivity"] = self.client.probe()
        extras["container_uptime_seconds"] = {
            c.name: max(0, int((payload.timestamp - c.started_at).total_seconds()))
            for c in payload.docker.containers
            if c.state == "running" and c.started_at
        }
        payload.host.extended = extras
        # Transfer buffered samples and clear them in the same SQLite transaction.
        # The shared lock prevents a minute sample being lost during the transfer.
        with self.spool.lock:
            samples = self.spool.get_state("postgres_samples") or []
            if self.settings.postgres_dsn:
                payload.postgres = PostgresMetrics(samples=samples)
            self.spool.enqueue("metrics", payload.model_dump_json(), state=("postgres_samples", []))
        self.cpu.commit(cpu)
        log.info("heartbeat_queued")

    def poll(self):
        if not self.enabled("events"):
            return
        self.isolated("docker_events", lambda: self.events.docker(*self.docker.collect()))

    def postgres_poll(self):
        if not self.enabled("metrics"):
            return
        sample = self.postgres.connections()
        if sample is None:
            return
        with self.spool.lock:
            samples = self.spool.get_state("postgres_samples") or []
            samples.append(sample)
            self.spool.set_state("postgres_samples", samples)
        if self.enabled("events"):
            self.events.postgres(sample["available"])

    def daily(self):
        if not self.enabled("pg-statements"):
            return
        if not self.settings.postgres_dsn:
            return
        now = datetime.now(ZoneInfo(self.settings.timezone))
        if (now.hour, now.minute) < (self.settings.daily_hour, self.settings.daily_minute):
            return
        # The collection runs after the reporting day closes. Associate this
        # point-in-time capture with the previous local calendar day.
        day = (now.date() - timedelta(days=1)).isoformat()
        if self.spool.get_state("daily_completed") == day:
            return
        collection_started_at = datetime.now(timezone.utc)
        reports = normalize(self.postgres.collect())
        if not reports:
            return
        # One immutable object per system/day must contain every database, not just
        # the first scope that wins a conditional write at the daily S3 key.
        captured_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        collection_started = collection_started_at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        details = {"report_date": day, "timezone": self.settings.timezone, "captured_at": captured_at,
                   "collection_started_at": collection_started,
                   "scope": "system", "database": "all",
                   "available": any(report.get("available", False) for report in reports),
                   "reports": reports}
        payload = event(self.settings, "postgres.daily_metrics", "postgres", "host", self.settings.system_id, details)
        self.spool.enqueue("pg-statements", payload.model_dump_json(), state=("daily_completed", day))
        log.info("postgres_daily_queued")

    def flush(self):
        if not self.enabled("delivery"):
            return
        self.spool.prune()
        delivery = self.spool.get_state("delivery_backoff") or {"attempts": 0, "due": 0}
        if delivery["due"] > time.time():
            return
        routes = [route for route, service in ROUTE_SERVICES.items() if self.enabled(service)]
        for row in self.spool.pending(self.settings.batch_size, routes=routes):
            if not self.enabled("delivery"):
                break
            if not self.enabled(ROUTE_SERVICES[row["route"]]):
                continue
            if row["route"] == "events":
                try:
                    old_event = json.loads(row["payload"])
                except ValueError:
                    old_event = None
                if isinstance(old_event, dict) and old_event.get("event_type") == "host.extended_metrics":
                    # Preserve old periodic samples in quarantine without mailing a backlog.
                    self.spool.retry(row["id"], 0, dead=True)
                    log.info("legacy_metric_event_quarantined route=events")
                    continue
            status = self.client.send(row["route"], row["payload"])
            if status is not None and (
                200 <= status < 300 or (row["route"] == "pg-statements" and status == 409)
            ):
                # 409 on daily reports means S3 already holds this system/day;
                # acknowledge it, including retries after a lost success response.
                self.spool.ack(row["id"])
                delivery = {"attempts": 0, "due": 0}
                self.spool.set_state("delivery_backoff", delivery)
            else:
                # Auth failures retain messages, allowing operators to rotate credentials.
                permanent = (
                    status is not None and 400 <= status < 500 and status not in (401, 403, 408, 425, 429)
                )
                delay = backoff(
                    max(row["attempts"], delivery["attempts"]) + 1,
                    self.settings.retry_base_seconds,
                    self.settings.retry_max_seconds,
                )
                self.spool.retry(row["id"], delay, dead=permanent)
                log.warning(
                    "delivery_failed route=%s status=%s quarantined=%s", row["route"], status, permanent
                )
                if not permanent:
                    self.spool.set_state(
                        "delivery_backoff",
                        {
                            "attempts": delivery["attempts"] + 1,
                            "due": time.time() + delay,
                        },
                    )
                    break

    def start(self):
        self.isolated("cpu_baseline", self.cpu.reset)
        self.isolated("postgres_connections", self.postgres_poll)
        self.scheduler.add_job(
            self.isolated,
            "interval",
            seconds=self.settings.postgres_poll_seconds,
            id="postgres",
            args=("postgres_connections", self.postgres_poll),
        )
        for name, interval, function in (
            ("heartbeat", self.settings.heartbeat_seconds, self.heartbeat),
            ("events", self.settings.poll_seconds, self.poll),
            ("flush", self.settings.flush_seconds, self.flush),
        ):
            self.scheduler.add_job(
                self.isolated,
                "interval",
                seconds=interval,
                id=name,
                args=(name, function),
                next_run_time=datetime.now(timezone.utc) + timedelta(seconds=interval if name == "heartbeat" else 0),
            )
        # Startup catch-up runs once if today's configured local time has already passed.
        self.scheduler.add_job(
            self.isolated,
            "interval",
            seconds=self.settings.daily_check_seconds,
            id="daily",
            args=("daily", self.daily),
            next_run_time=datetime.now(timezone.utc),
        )
        self.scheduler.start()

    def stop(self):
        self.scheduler.shutdown(wait=True)


def normalize(value):
    from datetime import date
    from decimal import Decimal

    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value
