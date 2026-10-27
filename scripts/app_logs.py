#!/usr/bin/env python3
"""Readable, combined CloudWatch logs using Python's standard library and AWS CLI."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import os
import re
import subprocess
import sys
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PLATFORM = re.compile(r"^(INIT_START|INIT_REPORT|START RequestId:|END RequestId:|REPORT RequestId:)")
LAMBDA_LINE = re.compile(r"^\[(DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL)\]\s+\S+\s+\S+\s+(.*)$", re.S)
METRICS = {"cpu_percent": "CPU", "memory_percent": "Memory", "disk_percent": "Disk"}


def numeric(value):
    try:
        return f"{float(value):g}"
    except (ValueError, TypeError):
        return str(value)


def short(value):
    return value[:8] if value and value != "None" else "—"


def service_name(group, prefix):
    if group.startswith("/aws/apigateway/"):
        return "api-gateway"
    return group.rsplit("/", 1)[-1].removeprefix(prefix + "-")


def decode_message(raw):
    """Return level, message, runtime-noise; preserve runtime failures."""
    message = raw.strip()
    try:
        item = json.loads(message)
    except (ValueError, TypeError):
        item = None
    if isinstance(item, dict):
        if "routeKey" in item and "status" in item:
            status = str(item["status"])
            level = "ERROR" if status.startswith("5") else "WARN" if status.startswith("4") else "INFO"
            text = f"{item['routeKey']} → HTTP {status} · solicitud {item.get('requestId', '—')}"
            if item.get("integrationErr") not in (None, "", "-"):
                text += " · " + str(item["integrationErr"])
            return level, text, False
        if "message" in item:
            content = item["message"]
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            return str(item.get("level", "INFO")).upper(), content, False
        if str(item.get("type", "")).startswith("platform."):
            record = item.get("record", {})
            failed = isinstance(record, dict) and (record.get("status") in {"error", "timeout", "failure"} or record.get("errorType"))
            return "ERROR" if failed else "RUNTIME", message, not failed
    match = LAMBDA_LINE.match(message)
    if match:
        return match[1], match[2], False
    failed = bool(re.search(r"ERROR|Error|Exception|Traceback|Task timed out|Status: (error|timeout)", message))
    return "ERROR" if failed else "INFO", message, bool(PLATFORM.match(message) and not failed)


def summarize(message):
    """Translate known application stages; unknown/error messages remain intact."""
    stage = message.split(" ", 1)[0]
    f = dict(re.findall(r"([A-Za-z_]+)=([^\s]+)", message))
    metric = METRICS.get(f.get("metric"), "Rule " + short(f.get("rule_id")))
    event = short(f.get("event_id"))
    if stage == "metrics.persisted":
        action = "Repeated reading" if f.get("created") == "False" else "Metrics persisted"
        return f"{action} · CPU {numeric(f.get('cpu_percent'))}% · {f.get('system_id', '')}"
    if stage == "auth.checked":
        return "Agent credential accepted" if f.get("authorized") == "True" else "Agent credential rejected"
    if stage == "stream.host_received":
        return f"Host reading received from Streams · {f.get('system_id', '')}"
    if stage == "stream.host_processed":
        return "Host reading processed"
    if stage == "evaluation.started":
        return f"Evaluating {f.get('rule_count', '?')} rules · {f.get('system_id', '')}"
    if stage == "evaluation.completed":
        return f"Evaluation finished · {f.get('evaluated_rules', '?')} rules"
    if stage == "rule.evaluating":
        return f"{metric}: {numeric(f.get('value'))}% · threshold {numeric(f.get('threshold'))}%"
    if stage == "history.query_started":
        return "Querying host history in DynamoDB"
    if stage == "history.loaded":
        count = f.get('sample_count', '?')
        return f"History read · {count} {'sample' if count == '1' else 'samples'}"
    if stage == "rule.window_checked":
        progress = f"{f.get('samples', '?')}/{f.get('required_samples', '?')} readings · {numeric(f.get('covered_seconds', 0))}/{f.get('required_seconds', '?')} s"
        if f.get("decision") == "ready":
            phase = "recovery" if f.get("phase") == "recovery" else "alert"
            return f"{metric}: condition for {phase} met · {progress}"
        reason = {
            "insufficient_samples": "not enough readings",
            "duration_not_met": "duration not covered yet",
            "sample_gap_exceeded": f"gap of {numeric(f.get('gap_seconds'))} s; maximum {f.get('max_gap_seconds', '?')} s",
            "threshold_interrupted": "a reading interrupted the condition",
            "before_rule_update": "reading precedes rule update",
        }.get(f.get("reason"), f.get("reason", "insufficient history"))
        return f"{metric}: waiting · {reason} · {progress}"
    if stage == "rule.no_event":
        if f.get("reason") == "cooldown":
            return f"{metric}: notification suppressed by cooldown, remaining {f.get('remaining_seconds', '?')} s"
        return f"{metric}: no event · {f.get('reason', '')}"
    if stage == "rule.skipped":
        reason = "repeated or old reading" if f.get("reason") == "old_or_duplicate" else "rule paused, in maintenance or recently modified"
        return f"Rule {short(f.get('rule_id'))}: skipped · {reason}"
    if stage == "rule.state_saved":
        if f.get("phase") in {"alert", "recovery"}:
            return f"{metric}: {'alert' if f['phase'] == 'alert' else 'recovery'} saved · event {event}"
        return f"{metric}: state updated"
    if stage == "sqs.enqueue_started":
        return f"Sending event {event} to SQS"
    if stage == "sqs.enqueued":
        return f"Event {event} sent to SQS · message {short(f.get('message_id'))}"
    if stage == "sqs.received":
        return f"Message {short(f.get('message_id'))} received from SQS"
    if stage == "sqs.processed":
        return f"Message {short(f.get('message_id'))} processed"
    if stage == "event.received":
        return f"Event {event} received · {f.get('event_type', '')}"
    if stage == "event.persisted":
        return f"Event {event} saved in DynamoDB · status {f.get('notification_status', '')}"
    if stage == "event.targets_selected":
        return f"Event {event}: {f.get('channel_count', '?')} channels selected"
    if stage == "sns.publish_started":
        return f"Publishing event {event} to SNS"
    if stage == "sns.published":
        return f"Event {event} published to SNS · message {short(f.get('sns_message_id'))}"
    if stage == "sns.skipped":
        return f"Event {event}: delivery skipped due to configuration change"
    if stage == "event.completed":
        return f"Event {event} completed · {f.get('published_channels', '?')} channels published"
    if stage == "event.duplicate_skipped":
        return f"Event {event} repeated; already processed"
    if stage == "event.pending_acknowledged":
        return f"Event {event}: enqueue acknowledged"
    if stage == "event.pending_discarded":
        return f"Event {event} pending output discarded after rule edit or pause"
    return message


def format_event(group, event, prefix, zone, raw=False, platform=False, rule_metrics=None):
    level, message, noise = decode_message(event.get("message", ""))
    if noise and not platform:
        return None
    if rule_metrics is not None and message.startswith("rule."):
        fields = dict(re.findall(r"([A-Za-z_]+)=([^\s]+)", message))
        uid = fields.get("rule_id")
        if uid and fields.get("metric"):
            rule_metrics[uid] = fields["metric"]
        elif uid in rule_metrics and not raw:
            message += " metric=" + rule_metrics[uid]
    if not raw:
        message = summarize(message)
    message = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", message)
    at = datetime.fromtimestamp(event["timestamp"] / 1000, zone).strftime("%H:%M:%S")
    header = f"{at} | {service_name(group, prefix):<28} | {level:<7} | "
    return "\n".join(header + line for line in message.splitlines())


class AwsLogs:
    def __init__(self, profile, region):
        self.base = ["aws", "--profile", profile, "--region", region, "--output", "json", "--no-cli-pager", "logs"]

    def call(self, *arguments):
        result = subprocess.run(self.base + list(arguments), capture_output=True, text=True, timeout=30,
                                env={**os.environ, "AWS_PAGER": "", "AWS_CLI_AUTO_PROMPT": "off"})
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "AWS CLI failed")
        return json.loads(result.stdout)

    def groups(self, prefix):
        names = []
        for kind in ("lambda", "apigateway"):
            data = self.call("describe-log-groups", "--log-group-name-prefix", f"/aws/{kind}/{prefix}-")
            names.extend(group["logGroupName"] for group in data.get("logGroups", []))
        return sorted(set(names))

    def events(self, group, start, end):
        # AWS CLI paginates the API, including empty pages with a continuation token.
        return self.call("filter-log-events", "--log-group-name", group, "--start-time", str(start),
                         "--end-time", str(end)).get("events", [])


def fresh_events(group, events, seen):
    fresh = []
    for event in events:
        identity = (group, event["eventId"])
        if identity not in seen:
            seen[identity] = event["timestamp"]
            fresh.append((group, event))
    return fresh


def chronological(rows):
    return sorted(rows, key=lambda row: (row[1]["timestamp"], row[1].get("ingestionTime", 0), row[0], row[1]["eventId"]))


def render_rows(rows, args, zone, rule_metrics):
    lines = []
    for group, event in chronological(rows):
        if args.contains and args.contains not in event.get("message", ""):
            continue
        line = format_event(group, event, args.prefix, zone, args.raw, args.platform, rule_metrics)
        if line:
            lines.append(line)
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="glorify-control-prod")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--prefix", default="glorify-control-prod")
    parser.add_argument("--since", default="5m", help="Initial history, e.g. 30s, 5m, 1h")
    parser.add_argument("--timezone", default="America/Argentina/Buenos_Aires")
    parser.add_argument("--service", action="append", help="Select a service; repeat to select several")
    parser.add_argument("--contains", help="Only show raw messages containing this text or event ID")
    parser.add_argument("--interval", type=float, default=5, help="Polling delay in seconds")
    parser.add_argument("--once", action="store_true", help="Read history once and exit")
    parser.add_argument("--raw", action="store_true", help="Keep technical messages and full IDs")
    parser.add_argument("--platform", action="store_true", help="Include Lambda START/END/REPORT")
    parser.add_argument("--append", action="store_true", help="Append arrivals instead of refreshing the terminal (late logs may appear out of order)")
    parser.add_argument("--lines", type=int, default=200, help="Maximum visible log entries in the live terminal")
    args = parser.parse_args()
    match = re.fullmatch(r"(\d+)(s|m|h)", args.since)
    if not match or args.interval < 1 or args.lines < 1:
        parser.error("Use --since like 30s/5m/1h, --interval >= 1 and --lines >= 1")
    try:
        zone = ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError:
        parser.error("Unknown timezone")
    seconds = int(match[1]) * {"s": 1, "m": 60, "h": 3600}[match[2]]
    api = AwsLogs(args.profile, args.region)
    groups = api.groups(args.prefix)
    if args.service:
        groups = [g for g in groups if service_name(g, args.prefix) in args.service]
    if not groups:
        parser.error("No matching log groups")
    redraw = sys.stdout.isatty() and not args.once and not args.append
    header = f"App Logs · {len(groups)} services · {args.timezone} · Ctrl+C to exit"
    columns = "TIME     | SERVICE                      | LEVEL   | MESSAGE"
    if not redraw:
        print(header, flush=True)
        print(columns, flush=True)
        if not args.once:
            print("App Logs · append mode: delayed logs keep their original time; use --once to sort a period.", file=sys.stderr)
    cursors = dict.fromkeys(groups, max(0, int(time.time() * 1000) - seconds * 1000))
    seen, rule_metrics, retained = {}, {}, []
    with ThreadPoolExecutor(max_workers=2) as pool:
        while True:
            end = int(time.time() * 1000)
            rows, failures = [], []
            futures = {pool.submit(api.events, g, cursors[g], end): g for g in groups}
            for future in as_completed(futures):
                group = futures[future]
                try:
                    rows.extend(fresh_events(group, future.result(), seen))
                    # Re-read two minutes for delayed log ingestion; deduplicate by
                    # group/event ID. A failed group keeps its previous cursor.
                    cursors[group] = max(cursors[group], end - 120000)
                except (RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
                    failures.append(f"{service_name(group, args.prefix)}: {error}")
            if redraw:
                # Redraw the sorted snapshot: a finite delay cannot guarantee
                # chronological append order when CloudWatch delivers late logs.
                retained = chronological(retained + rows)
                lines = render_rows(retained, args, zone, rule_metrics)[-args.lines:]
                print("\x1b[2J\x1b[H" + header + " · time order\n" + columns + "\n" + "\n".join(lines), end="\n", flush=True)
                # Retain matching visible events, rather than runtime noise.
                retained = [(g, e) for g, e in retained if
                            (not args.contains or args.contains in e.get("message", "")) and
                            format_event(g, e, args.prefix, zone, args.raw, args.platform, rule_metrics)][-args.lines:]
            else:
                for line in render_rows(rows, args, zone, rule_metrics):
                    print(line, flush=True)
            for error in failures:
                print("App Logs · error al consultar " + error, file=sys.stderr, flush=True)
            if args.once:
                return 1 if failures else 0
            oldest = min(cursors.values())
            seen = {identity: at for identity, at in seen.items() if at >= oldest}
            time.sleep(args.interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nApp Logs cerrado.", file=sys.stderr)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"App Logs: {error}", file=sys.stderr)
        raise SystemExit(1)
