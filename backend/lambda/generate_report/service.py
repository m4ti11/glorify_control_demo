from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
import logging
import os
from uuid import uuid4

import boto3
from botocore.config import Config

from models import INPUT_VERSION, MAX_INPUT_BYTES, TIMEZONE, reporting_window, serialize_input, serialize_output, validate_analysis
from openai_client import ResponsesClient
from repository import ClaimRepository, GeneratedReports, ReportSources

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
UTC = timezone.utc


def iso(value):
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def json_safe(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


class ReportService:
    def __init__(self, sources, claims, reports, secrets, openai, settings, publisher=None):
        self.sources, self.claims, self.reports = sources, claims, reports
        self.secrets, self.openai, self.settings = secrets, openai, settings
        self.publisher = publisher

    def build_input(self, system_id, report_date, now):
        start, end = reporting_window(report_date)
        source_data = {}
        for name, call in (
            ("host_metrics", lambda: self.sources.host_metrics(system_id, start, end, self.settings["metrics_stale_after"])),
            ("events", lambda: self.sources.events(system_id, start, end, self.settings["events_stale_after"])),
            ("postgresql", lambda: self.sources.postgres(system_id, report_date, self.settings["pg_stale_after"], now)),
        ):
            try:
                source_data[name] = call()
            except Exception:
                logger.warning("report_source_unavailable source=%s", name)
                source_data[name] = {"status": "unavailable", "count": 0, "omitted_count": 0, "data": None}
        source_data = json_safe(source_data)
        self._conditions(source_data)
        self._compact(source_data, self.settings["input_max_bytes"])
        payload = {
            "schema_version": INPUT_VERSION, "system_id": system_id, "report_date": report_date.isoformat(),
            "timezone": str(TIMEZONE), "window": {"start": iso(start), "end_exclusive": iso(end)},
            "generated_at": iso(now), "source_selection": {"events": "Activity selected by occurred_at. An empty successful query means no recorded activity, not missing telemetry. Collector coverage must be assessed separately from host_metrics; received_at is arrival metadata."},
            "sources": source_data,
        }
        raw = serialize_input(payload, self.settings["input_max_bytes"])
        return payload, raw

    @staticmethod
    def _compact(sources, max_input_bytes=MAX_INPUT_BYTES):
        metrics = sources["host_metrics"]
        if isinstance(metrics.get("data"), list):
            metrics["data"] = [{"evidence_ref": row["evidence_ref"], "measured_at": row["measured_at"],
                "cpu_percent": row.get("metrics", {}).get("cpu_percent"),
                "memory_percent": row.get("metrics", {}).get("memory", {}).get("percent"),
                "disk_percent": row.get("metrics", {}).get("disk", {}).get("percent"),
                "uptime_seconds": row.get("metrics", {}).get("uptime_seconds")} for row in metrics["data"]]
        events = sources["events"]
        if isinstance(events.get("data"), list):
            rows = []
            for row in events["data"]:
                item = dict(row)
                details = json.dumps(item.get("details", {}), separators=(",", ":"), ensure_ascii=False)
                if len(details.encode()) > 2048:
                    item["details"] = {"content_omitted": True}
                    item["detail_omitted"] = True
                    events["status"] = "incomplete"
                    events.setdefault("issues", []).append("incomplete")
                rows.append(item)
            events["data"] = rows
        postgres = sources["postgresql"]
        report = postgres.get("data")
        if isinstance(report, dict):
            details = report.get("details", {})
            postgres["snapshot_semantics"] = {
                "schema_version": 1,
                "kind": "point_in_time_snapshot",
                "reporting_date": details.get("report_date"),
                "collection_started_at": details.get("collection_started_at"),
                "captured_at": details.get("captured_at"),
                "cumulative_counters": [
                    {"source": "pg_stat_database", "fields": ["blks_read", "blks_hit", "deadlocks"],
                     "reset_marker": "stats_reset"},
                    {"source": "pg_stat_statements", "fields": ["calls", "total_exec_ms", "total_exec_time", "rows"],
                     "reset_marker": "stats_reset when supplied in statement rows; otherwise unknown"},
                ],
                "counter_reference": "snapshot counters may be cumulative since their reset marker; no daily delta is implied",
            }
            sections = details.get("reports") or [details]
            for section in sections:
                db = section.get("database", "unknown")
                section["evidence_ref"] = f"pg:{db}"
                for name, value in list(section.items()):
                    if isinstance(value, dict) and isinstance(value.get("rows"), list):
                        rows = value["rows"]
                        value["rows"] = rows[:50]
                        for index, row in enumerate(value["rows"]):
                            if isinstance(row, dict):
                                row["evidence_ref"] = f"pg:{db}:{name}:{index}"
                        if len(rows) > 50:
                            value["omitted_count"] = len(rows) - 50
                            postgres["status"] = "incomplete"
                            postgres.setdefault("issues", []).append("incomplete")
        # Keep records in stable order while trimming only source data, never raw
        # byte slices. Every removed source is reflected in its status/count.
        def encoded_size():
            candidate = {"schema_version": INPUT_VERSION, "sources": sources}
            return len(json.dumps(candidate, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode())
        target_size = max_input_bytes - 1024
        for name in ("events", "host_metrics"):
            rows = sources[name].get("data")
            while isinstance(rows, list) and rows and encoded_size() > target_size:
                rows.pop(0)
                sources[name]["status"] = "incomplete"
                sources[name].setdefault("issues", []).append("incomplete")
                sources[name]["omitted_count"] = (sources[name].get("omitted_count") or 0) + 1
        if encoded_size() > target_size and sources["postgresql"].get("data") is not None:
            sources["postgresql"]["data"] = None
            sources["postgresql"]["status"] = "incomplete"
            sources["postgresql"].setdefault("issues", []).append("incomplete")
        for source in sources.values():
            source["issues"] = list(dict.fromkeys(source.get("issues", [])))

    @staticmethod
    def _conditions(sources):
        for source in sources.values():
            issue = source.get("status")
            source["issues"] = list(dict.fromkeys([*source.get("issues", []), *([issue] if issue in ("missing", "incomplete", "stale", "unavailable") else [])]))

    def generate(self, system_id, report_date, now=None):
        now = now or datetime.now(UTC)
        report_date = report_date if isinstance(report_date, date) else date.fromisoformat(report_date)
        owner = str(uuid4())
        key = self.reports.key(system_id, report_date)
        outcome = self.claims.acquire(system_id, report_date.isoformat(), owner, int(now.timestamp()), self.settings["lease_seconds"])
        if outcome == "busy":
            return {"status": "busy", "system_id": system_id, "report_date": report_date.isoformat()}
        if outcome == "complete":
            if self.publisher is not None:
                existing = self.reports.get(key)
                if not existing or existing.get("system_id") != system_id or existing.get("report_date") != report_date.isoformat():
                    raise RuntimeError("Completed report is missing or has a different identity")
                self.publisher.publish(existing)
            return {"status": "complete", "system_id": system_id, "report_date": report_date.isoformat()}
        try:
            # Reconcile the S3/DynamoDB non-transactional boundary before spending
            # another API call after a prior write succeeded.
            existing = self.reports.get(key)
            if existing is not None:
                if existing.get("system_id") != system_id or existing.get("report_date") != report_date.isoformat():
                    raise RuntimeError("Saved report identity does not match its key")
                if self.publisher is not None:
                    self.publisher.publish(existing)
                self.claims.complete(system_id, report_date.isoformat(), owner, key)
                return {"status": "complete", "system_id": system_id, "report_date": report_date.isoformat()}
            payload, raw = self.build_input(system_id, report_date, now)
            if len(raw) > self.settings["input_max_bytes"]:
                raise ValueError("Report input exceeds configured limit")
            secret = self.secrets.get_secret_value(SecretId=self.settings["secret_arn"])["SecretString"]
            model_text = self.openai.analyze(secret, raw)
            evidence = set()

            def collect_evidence(value):
                if isinstance(value, dict):
                    if isinstance(value.get("evidence_ref"), str):
                        evidence.add(value["evidence_ref"])
                    for child in value.values():
                        collect_evidence(child)
                elif isinstance(value, list):
                    for child in value:
                        collect_evidence(child)

            collect_evidence(payload["sources"])
            analysis = validate_analysis(model_text, evidence, self.settings["output_max_bytes"])
            window = payload["window"]
            output = {
                "schema_version": 1, "system_id": system_id, "report_date": report_date.isoformat(),
                "timezone": str(TIMEZONE), "window": window, "generated_at": iso(datetime.now(UTC)),
                "source_status": {name: {key: value for key, value in source.items() if key != "data"}
                                  for name, source in payload["sources"].items()},
                "analysis": analysis.model_dump(mode="json"),
            }
            saved = serialize_output(output, self.settings["output_max_bytes"])
            if len(saved) > self.settings["output_max_bytes"]:
                raise ValueError("Generated report exceeds configured limit")
            created = self.reports.create(key, saved, {"system-id": system_id, "report-date": report_date.isoformat()})
            if not created:
                # Another valid immutable report already won the conditional write.
                existing = self.reports.get(key)
                if existing is None:
                    raise RuntimeError("Conditional write conflicted without a readable report")
                if existing.get("system_id") != system_id or existing.get("report_date") != report_date.isoformat():
                    raise RuntimeError("Saved report identity does not match its key")
            if self.publisher is not None:
                self.publisher.publish(existing if not created else output)
            self.claims.complete(system_id, report_date.isoformat(), owner, key)
            return {"status": "complete", "system_id": system_id, "report_date": report_date.isoformat()}
        except Exception:
            try:
                self.claims.retryable(system_id, report_date.isoformat(), owner)
            except Exception:
                logger.warning("report_claim_release_failed system_id=%s report_date=%s", system_id, report_date.isoformat())
            raise


def build_service():
    from delivery import ReportPublisher
    secret_client = boto3.client("secretsmanager", config=Config(connect_timeout=2, read_timeout=5,
                               retries={"mode": "standard", "total_max_attempts": 2}))
    settings = {
        "secret_arn": os.environ["OPENAI_SECRET_ARN"], "lease_seconds": int(os.environ.get("REPORT_LEASE_SECONDS", "1200")),
        "input_max_bytes": int(os.environ.get("REPORT_INPUT_MAX_BYTES", str(MAX_INPUT_BYTES))),
        "output_max_bytes": int(os.environ.get("REPORT_OUTPUT_MAX_BYTES", "65536")),
        "metrics_stale_after": int(os.environ.get("METRICS_STALE_AFTER_SECONDS", "600")),
        "events_stale_after": int(os.environ.get("EVENTS_STALE_AFTER_SECONDS", "600")),
        "pg_stale_after": int(os.environ.get("PG_STALE_AFTER_SECONDS", "86400")),
    }
    return ReportService(
        ReportSources(os.environ["METRICS_TABLE_NAME"], os.environ["EVENTS_TABLE_NAME"],
                      os.environ["PG_REPORTS_BUCKET"], expected_heartbeat_seconds=int(os.environ.get("EXPECTED_HEARTBEAT_SECONDS", "120"))),
        ClaimRepository(os.environ["REPORT_JOBS_TABLE_NAME"]),
        GeneratedReports(os.environ["GENERATED_REPORTS_BUCKET"],
                         max_output_bytes=int(os.environ.get("REPORT_OUTPUT_MAX_BYTES", "65536")),
                         fallback_bucket=os.environ.get("LEGACY_GENERATED_REPORTS_BUCKET")), secret_client,
        ResponsesClient(os.environ["OPENAI_MODEL"], int(os.environ.get("OPENAI_TIMEOUT_SECONDS", "45"))), settings, publisher=ReportPublisher.from_environment(),
    )
