"""Bounded event presentations for SNS text and SES multipart email."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html import escape
import re


METRICS = {"cpu_percent": "CPU", "memory_percent": "Memory", "disk_percent": "Disk",
           "network_rx_bytes_per_second": "Inbound traffic", "network_tx_bytes_per_second": "Outbound traffic"}
TYPES = {"container.exited": "Container stopped", "container.changed": "Container changed",
         "docker.availability": "Docker availability", "postgres.availability": "PostgreSQL availability",
         "host.extended_metrics": "Host report", "postgres.daily_metrics": "Daily PostgreSQL report",
         "agent.unreachable": "Agent unreachable", "agent.recovered": "Agent recovered"}
LABELS = {"error": "Error", "message": "Message", "reason": "Reason", "exit_code": "Exit code",
          "oom_killed": "Terminated due to memory exhaustion (OOM)", "previous_state": "Previous state",
          "current_state": "Current state", "previous": "Previous", "current": "Current", "state": "State",
          "health": "Health", "restart_count": "Restarts", "started_at": "Started", "reminder": "Reminder",
          "last_received_at": "Last metric received", "outage_started_at": "Outage start",
          "observed_age_seconds": "Observed age (seconds)", "stale_after_seconds": "Inactivity threshold (seconds)",
          "confirmation_seconds": "Inactivity confirmation (seconds)"}


def clean(value, limit=500):
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value))
    return text if len(text) <= limit else text[:limit] + "… [shortened]"


def value_text(value):
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    if value is None:
        return "Unavailable"
    if isinstance(value, (int, float, Decimal)):
        return format(value, "g")
    return clean(value)


def duration(value):
    try:
        seconds = float(value)
        return f"{seconds / 60:g} min" if seconds % 60 == 0 else f"{seconds:g} s"
    except (TypeError, ValueError):
        return value_text(value)


def local_time(value):
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if at.tzinfo is None:
            return clean(value)
        return at.astimezone(timezone(timedelta(hours=-3))).strftime("%d/%m/%Y %H:%M:%S") + " (Argentina, UTC-03)"
    except (ValueError, TypeError):
        return clean(value or "Unavailable")


def detail_lines(details, depth=0):
    """Bound both breadth and depth; full details remain in the stored event."""
    lines = []
    for key, value in list(details.items())[:20]:
        label = LABELS.get(key, clean(key.replace("_", " ").capitalize(), 80))
        indent = "  " * (depth + 1)
        if isinstance(value, dict) and depth < 2:
            lines.append(f"{indent}{label}:")
            lines.extend(detail_lines(value, depth + 1))
        elif isinstance(value, (dict, list)):
            lines.append(f"{indent}{label}: {clean(value, 300)}")
        else:
            lines.append(f"{indent}{label}: {value_text(value)}")
    if len(details) > 20:
        lines.append("  " * (depth + 1) + "More fields are available in the stored event.")
    return lines


def event_presentation(item):
    details = item.get("details", {})
    kind = item["event_type"]
    metric_key = clean(details.get("metric", "Host metric"), 120)
    metric = METRICS.get(metric_key, metric_key)
    recovery = kind in {"host.threshold_recovered", "agent.recovered"} or details.get("abnormal") is False
    alert = (kind in {"host.threshold_exceeded", "container.exited"} or
             kind == "agent.unreachable" or details.get("abnormal") is True or
             any(word in kind for word in ("error", "failed", "unhealthy")))
    status = "RECOVERY" if recovery else "ALERT" if alert else "INFORMATION"
    if kind in {"host.threshold_exceeded", "host.threshold_recovered"}:
        title = f"{metric}: {'recovered' if recovery else 'threshold exceeded'}"
    else:
        title = TYPES.get(kind, clean(kind.replace(".", " · ").replace("_", " "), 120))
    system = clean(item.get("system_id", "Unknown system"), 120)
    subject = clean(f"[{status}] {system} | {title}", 99)[:99]
    resource = item.get("subject", {})
    resource_type = {"host": "Host", "container": "Container", "disk": "Disk", "database": "Database",
                     "daemon": "Service"}.get(resource.get("type"), resource.get("type", "Resource"))
    unit = clean(details.get("unit", "%"), 10)
    limit = details.get("recovery_threshold") if recovery else details.get("threshold")
    operator = details.get("operator", ">=")
    if recovery:
        operator = "<=" if operator == ">=" else ">="
    if kind in {"host.threshold_exceeded", "host.threshold_recovered"}:
        summary = f"{metric} recorded {value_text(details.get('value'))}{unit}. " + (
            "The rule recovery condition was met." if recovery else "The rule alert condition was met.")
        action = ("Check that the metric stays stable in subsequent readings." if recovery else
                  "Review metric trends and affected processes before intervening.")
    elif details.get("oom_killed") is True:
        summary = "The container was terminated due to memory exhaustion (OOM)."
        action = "Review container memory usage, limits and logs before restarting it."
    elif kind in {"docker.availability", "postgres.availability"}:
        name = "Docker" if kind.startswith("docker.") else "PostgreSQL"
        summary = f"{name}: " + ("the agent reported availability again." if recovery else
                                 "the agent reports unavailability." if alert else "an availability change was recorded.")
        action = "Review service state and logs on the affected host."
    elif kind == "agent.unreachable":
        summary = "No recent agent metrics were received."
        action = "Check VPS connectivity, the agent service and the ingestion API."
    elif kind == "agent.recovered":
        summary = "The agent resumed sending recent metrics."
        action = "Monitoring continues; check the VPS if the alert returns."
    elif kind in {"container.changed", "container.exited"}:
        current = details.get("current", {})
        state = current.get("state") if isinstance(current, dict) else current
        state = details.get("current_state", state)
        summary = f"The container {clean(resource.get('id', '—'))} " + (
            f"informa estado {value_text(state)}." if state is not None else "recorded a state change.")
        action = "Review container state, restarts and logs."
    else:
        summary = "An event was recorded for this resource. Details preserve data reported by the source."
        action = "Review event details and the current resource state."
    return {"status": status, "title": clean(title), "subject": subject, "system": system,
            "resource": f"{clean(resource_type)} / {clean(resource.get('id', '—'))}",
            "time": local_time(item.get("occurred_at")), "metric": clean(metric), "unit": unit,
            "limit": value_text(limit), "operator": clean(operator, 5), "summary": summary,
            "action": action, "recovery": recovery, "threshold": kind in {"host.threshold_exceeded", "host.threshold_recovered"}}


def render_notification(item):
    details = item.get("details", {})
    kind = item["event_type"]
    view = event_presentation(item)
    lines = [f"{view['status']} · {view['title']}", "=" * 48, "", view["summary"], "",
             f"System:   {view['system']}", f"Resource:   {view['resource']}",
             f"Date:     {view['time']}", ""]
    if view["threshold"]:
        lines.extend(["CONDITION", "-" * 48,
                      f"{view['metric']}: {value_text(details.get('value'))}{view['unit']}",
                      f"{'Recovery threshold' if view['recovery'] else 'Alert threshold'}: {view['operator']} {view['limit']}{view['unit']}",
                      f"Required duration: {duration(details.get('duration_seconds'))}",
                      f"Minimum readings: {value_text(details.get('consecutive_samples'))}"])
        if "max_sample_gap_seconds" in details:
            lines.append(f"Maximum gap between readings: {duration(details['max_sample_gap_seconds'])}")
        if details.get("rule_name"):
            lines.append("Rule: " + clean(details["rule_name"]))
        lines.append("")
    else:
        if details.get("oom_killed") is True:
            lines.extend(["MOTIVO", "-" * 48, "The container was terminated due to memory exhaustion (OOM).", ""])
        if details:
            lines.extend(["DETALLE", "-" * 48, *detail_lines(details), ""])
    lines.extend(["WHAT TO CHECK", "-" * 48, view["action"], "", "REFERENCIA", "-" * 48, f"Event type: {clean(kind)}",
                  f"Source: {clean(item.get('source', '—'))}", f"Event ID: {clean(item['event_id'])}"])
    body = "\n".join(lines)
    if len(body) > 16000:
        body = body[:14000] + "\n\n[Details shortened. Review the stored event.]\nEvent ID: " + clean(item["event_id"])
    return view["subject"], body


def render_html_notification(item):
    """Email-safe tables and inline styles; never interpolate unescaped event data."""
    view = event_presentation(item)
    details = item.get("details", {})
    color, background = {"ALERT": ("#b42318", "#fef3f2"), "RECOVERY": ("#067647", "#ecfdf3"),
                         "INFORMATION": ("#175cd3", "#eff8ff")}[view["status"]]
    safe = lambda value: escape(str(value), quote=True)

    def row(label, value):
        return (f'<tr><th align="left" valign="top" style="padding:11px 0;border-bottom:1px solid #eaecf0;'
                f'font-size:13px;font-weight:500;color:#667085;width:38%">{safe(label)}</th>'
                f'<td valign="top" style="padding:11px 0 11px 12px;border-bottom:1px solid #eaecf0;'
                f'font-size:14px;color:#101828;overflow-wrap:anywhere">{safe(value)}</td></tr>')

    context = "".join(row(label, value) for label, value in [
        ("System", view["system"]), ("Resource", view["resource"]), ("Event time", view["time"])])
    if view["threshold"]:
        conditions = [
            ("Regla", clean(details.get("rule_name", "Unnamed"))),
            ("Recovery threshold" if view["recovery"] else "Alert threshold", f"{view['operator']} {view['limit']}{view['unit']}"),
            ("Required duration", duration(details.get("duration_seconds"))),
            ("Minimum readings", value_text(details.get("consecutive_samples"))),
        ]
        if "max_sample_gap_seconds" in details:
            conditions.append(("Maximum gap between readings", duration(details["max_sample_gap_seconds"])))
        measurement = (f'<div style="padding:22px;background:{background};border-radius:12px;margin:24px 0">'
                       f'<p style="margin:0 0 6px;font-size:13px;color:#475467">{safe(view["metric"])} · Valor observado</p>'
                       f'<p style="margin:0;font-size:38px;font-weight:700;color:{color}">'
                       f'{safe(value_text(details.get("value")))}<span style="font-size:20px">{safe(view["unit"])}</span></p></div>')
        detail = '<h2 style="font-size:16px;margin:24px 0 8px">Rule condition</h2><table role="presentation" width="100%" cellspacing="0">' + "".join(row(*condition) for condition in conditions) + '</table>'
    else:
        measurement = ""
        detail_text = "\n".join(detail_lines(details))[:12000]
        detail = ('<h2 style="font-size:16px;margin:24px 0 8px">Event details</h2>'
                  '<pre style="white-space:pre-wrap;overflow-wrap:anywhere;background:#f9fafb;padding:16px;'
                  'border-radius:10px;font:13px/1.7 monospace;color:#344054">' + safe(detail_text) + '</pre>') if details else ""
    reference = "".join(row(label, clean(value)) for label, value in [
        ("Event type", item["event_type"]), ("Source", item.get("source", "—")), ("Event ID", item["event_id"])])
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{safe(view['subject'])}</title></head>
<body style="margin:0;background:#f2f4f7;font-family:Arial,Helvetica,sans-serif;color:#101828">
<div style="display:none;max-height:0;overflow:hidden">{safe(view['summary'])}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:28px 12px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:600px;background:#fff;border:1px solid #eaecf0;border-radius:16px;overflow:hidden">
<tr><td style="height:6px;background:{color}"></td></tr>
<tr><td style="padding:28px">
<span style="display:inline-block;padding:6px 10px;border-radius:6px;background:{background};color:{color};font-size:12px;font-weight:700;letter-spacing:1px">{view['status']}</span>
<h1 style="font-size:26px;line-height:1.25;margin:18px 0 12px">{safe(view['title'])}</h1>
<p style="font-size:15px;line-height:1.7;color:#475467;margin:0 0 20px">{safe(view['summary'])}</p>
<table role="presentation" width="100%" cellspacing="0">{context}</table>
{measurement}{detail}
<div style="border-left:3px solid {color};padding:4px 0 4px 16px;margin:26px 0"><h2 style="font-size:15px;margin:0 0 8px">What to check</h2><p style="font-size:14px;line-height:1.7;color:#475467;margin:0">{safe(view['action'])}</p></div>
<h2 style="font-size:14px;margin:26px 0 6px">Event reference</h2>
<table role="presentation" width="100%" cellspacing="0">{reference}</table>
</td></tr></table></td></tr></table></body></html>'''
