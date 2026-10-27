from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from uuid import NAMESPACE_URL, uuid5

from models import available, instant, metric_value, timestamp

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class HostAlertService:
    def __init__(self, repository, max_sample_gap_seconds=600, clock=None):
        self.repository = repository
        self.max_gap = max_sample_gap_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def drain(self, rule, state, now):
        pending = state.get("pending_event")
        if not pending:
            return state
        current = self.repository.rule(rule["id"])
        # Configuration edits invalidate notices that have not yet been delivered.
        if current and current["version"] == pending["details"]["rule_version"] and available(current, instant(pending["occurred_at"]), now):
            logger.info("sqs.enqueue_started event_id=%s rule_id=%s system_id=%s", pending["event_id"], rule["id"], rule["data"]["system_id"])
            message_id = self.repository.enqueue(pending)
            logger.info("sqs.enqueued event_id=%s rule_id=%s message_id=%s", pending["event_id"], rule["id"], message_id)
        else:
            logger.info("event.pending_discarded event_id=%s rule_id=%s reason=configuration_changed_or_paused", pending["event_id"], rule["id"])
        self.repository.acknowledge(state)
        logger.info("event.pending_acknowledged event_id=%s rule_id=%s", pending["event_id"], rule["id"])
        return self.repository.state(rule["data"]["system_id"], rule["id"])

    def sustained(self, rule, readings, end, recovering=False):
        data = rule["data"]
        threshold = data["recovery_threshold"] if recovering else data["threshold"]
        upward = (data["operator"] == ">=") != recovering
        cutoff = end - timedelta(seconds=int(data["duration_seconds"]))
        effective = instant(rule["updated_at"])
        previous, count = end, 0
        reason, gap = "insufficient_history", 0
        for reading in readings:
            at = instant(reading["measured_at"])
            if at > end:
                continue
            if at < effective:
                reason = "before_rule_update"
                break
            gap = (previous - at).total_seconds()
            if gap > self.max_gap:
                reason = "sample_gap_exceeded"
                break
            value = metric_value(reading, data["metric"])
            if not (value >= threshold if upward else value <= threshold):
                reason = "threshold_interrupted"
                break
            count += 1
            if at <= cutoff and count >= int(data["consecutive_samples"]):
                logger.info("rule.window_checked rule_id=%s metric=%s phase=%s decision=ready samples=%s covered_seconds=%s required_seconds=%s required_samples=%s",
                            rule["id"], data["metric"], "recovery" if recovering else "alert", count, (end - at).total_seconds(), data["duration_seconds"], data["consecutive_samples"])
                return True
            previous = at
        covered = (end - previous).total_seconds()
        if reason == "insufficient_history":
            reason = "insufficient_samples" if count < int(data["consecutive_samples"]) else "duration_not_met"
        logger.info("rule.window_checked rule_id=%s metric=%s phase=%s decision=waiting reason=%s samples=%s covered_seconds=%s required_seconds=%s required_samples=%s gap_seconds=%s max_gap_seconds=%s",
                    rule["id"], data["metric"], "recovery" if recovering else "alert", reason, count, covered, data["duration_seconds"], data["consecutive_samples"], gap, self.max_gap)
        return False

    def event(self, rule, reading, phase):
        data, at = rule["data"], reading["measured_at"]
        uid = str(uuid5(NAMESPACE_URL, f"host-alert:{rule['id']}:{rule['version']}:{phase}:{at}"))
        return {"schema_version": 1, "record_type": "event", "event_id": uid,
                "system_id": data["system_id"], "source": "host-alert-evaluator",
                "event_type": "host.threshold_recovered" if phase == "recovery" else "host.threshold_exceeded",
                "occurred_at": at, "subject": {"type": "host", "id": "host"},
                "notification_channel_ids": data.get("notification_channel_ids", []),
                "details": {"rule_id": rule["id"], "rule_version": int(rule["version"]), "rule_name": data["name"],
                    "metric": data["metric"], "value": metric_value(reading, data["metric"]), "unit": "%",
                    "operator": data["operator"], "threshold": data["threshold"],
                    "recovery_threshold": data["recovery_threshold"], "duration_seconds": int(data["duration_seconds"]),
                    "consecutive_samples": int(data["consecutive_samples"]), "max_sample_gap_seconds": self.max_gap}}

    def process(self, reading):
        now, end = self.clock(), instant(reading["measured_at"])
        # A broken agent clock must not advance the state watermark into the future.
        if end > now + timedelta(minutes=5):
            raise ValueError("Host measurement is in the future")
        plans = []
        rules = self.repository.rules(reading["system_id"])
        logger.info("evaluation.started system_id=%s measured_at=%s rule_count=%s", reading["system_id"], reading["measured_at"], len(rules))
        for rule in rules:
            state = self.drain(rule, self.repository.state(reading["system_id"], rule["id"]), now)
            if state.get("last_measured_at") and instant(state["last_measured_at"]) >= end:
                logger.info("rule.skipped rule_id=%s reason=old_or_duplicate measured_at=%s", rule["id"], reading["measured_at"])
                continue
            if not available(rule, end, now) or end < instant(rule["updated_at"]):
                logger.info("rule.skipped rule_id=%s reason=disabled_maintenance_or_before_update", rule["id"])
                continue
            data = rule["data"]
            active = bool(state.get("active") and state.get("rule_version") == rule["version"])
            value = metric_value(reading, data["metric"])
            breach = value >= data["threshold"] if data["operator"] == ">=" else value <= data["threshold"]
            recovered = value <= data["recovery_threshold"] if data["operator"] == ">=" else value >= data["recovery_threshold"]
            last_alert = state.get("last_alert_at")
            cooldown_elapsed = not last_alert or (end - instant(last_alert)).total_seconds() >= int(data["cooldown_seconds"])
            needs_history = (breach and (not active or cooldown_elapsed)) or (active and recovered)
            logger.info("rule.evaluating rule_id=%s metric=%s value=%s threshold=%s active=%s cooldown_elapsed=%s needs_history=%s",
                        rule["id"], data["metric"], value, data["threshold"], active, cooldown_elapsed, needs_history)
            window = max(int(data["duration_seconds"]), (int(data["consecutive_samples"]) - 1) * self.max_gap)
            plans.append((rule, state, active, breach, recovered, window if needs_history else None))
        windows = [p[5] for p in plans if p[5] is not None]
        # One bounded HOST query serves all candidate metrics. The lookbehind covers
        # the duration boundary when it falls between two agent samples.
        readings = []
        if windows:
            start = end - timedelta(seconds=max(windows) + self.max_gap)
            logger.info("history.query_started system_id=%s start=%s end=%s", reading["system_id"], timestamp(start), timestamp(end))
            readings = self.repository.history(reading["system_id"], start, end)
            logger.info("history.loaded system_id=%s sample_count=%s", reading["system_id"], len(readings))
        for rule, previous, active, breach, recovered, window in plans:
            data = rule["data"]
            state = {"rule_version": rule["version"], "last_measured_at": timestamp(end), "active": active,
                     "notified": bool(previous.get("notified") and active), "last_alert_at": previous.get("last_alert_at")}
            phase = None
            if active and recovered and self.sustained(rule, readings, end, recovering=True):
                if data.get("notify_recovery", True) and state["notified"]:
                    phase = "recovery"
                state.update(active=False, notified=False)
            elif breach and window is not None and self.sustained(rule, readings, end):
                state["active"] = True
                last = state["last_alert_at"]
                if not last or (end - instant(last)).total_seconds() >= int(data["cooldown_seconds"]):
                    phase = "alert"
                    state.update(last_alert_at=timestamp(end), notified=True)
            if phase:
                state["pending_event"] = self.event(rule, reading, phase)
            elif breach and state["active"] and state["last_alert_at"]:
                remaining = max(0, int(data["cooldown_seconds"]) - int((end - instant(state["last_alert_at"])).total_seconds()))
                if remaining:
                    logger.info("rule.no_event rule_id=%s metric=%s reason=cooldown remaining_seconds=%s",
                                rule["id"], data["metric"], remaining)
            saved = self.repository.save(rule, previous, state)
            logger.info("rule.state_saved rule_id=%s metric=%s system_id=%s measured_at=%s active=%s phase=%s event_id=%s",
                        rule["id"], data["metric"], reading["system_id"], reading["measured_at"], saved["active"], phase or "none", state.get("pending_event", {}).get("event_id"))
            self.drain(rule, saved, now)
        logger.info("evaluation.completed system_id=%s measured_at=%s evaluated_rules=%s", reading["system_id"], reading["measured_at"], len(plans))
