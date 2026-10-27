from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import time
from uuid import uuid4

from models import EventPayload, timestamp

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def suppression_reason(event):
    """Ignore health-only container changes while preserving lifecycle failures."""
    if event["event_type"] != "container.changed" or event.get("subject", {}).get("type") != "container":
        return None
    details = event.get("details", {})
    current, previous = details.get("current"), details.get("previous")
    if not isinstance(current, dict) or current.get("state") != "running":
        return None
    if details.get("oom_killed") is True or details.get("exit_code") not in (None, 0, "0") or details.get("error"):
        return None
    if previous is None:
        return "container_health_only" if current.get("health") == "unhealthy" else None
    if not isinstance(previous, dict) or previous.get("state") != "running":
        return None
    lifecycle = ("restart_count", "started_at")
    if any(previous.get(key) != current.get(key) for key in lifecycle):
        return None
    if previous.get("health") != current.get("health") or current.get("health") == "unhealthy":
        return "container_health_only"
    return None


class EventsService:
    def __init__(self, repository, alerts, configuration=None, email=None, push=None):
        self.repository = repository
        self.alerts = alerts
        self.configuration = configuration
        self.email = email
        self.push = push

    def process(self, body: str, sent_timestamp: str):
        # Leave room below SNS's 256 KiB limit for keys and receipt metadata.
        if len(body.encode("utf-8")) > 240 * 1024:
            raise ValueError("Event exceeds the 240 KiB payload limit")
        payload = EventPayload.model_validate_json(body)
        event = payload.model_dump(mode="python", exclude={"event_id", "occurred_at"})
        # Preserve hashes of legacy events, which had no channel selection field.
        if payload.notification_channel_ids is None:
            event.pop("notification_channel_ids")
        else:
            event["notification_channel_ids"] = [str(uid) for uid in payload.notification_channel_ids]
        event["event_id"] = str(payload.event_id)
        event["occurred_at"] = timestamp(payload.occurred_at)
        logger.info("event.received event_id=%s system_id=%s event_type=%s source=%s rule_id=%s",
                    event["event_id"], event["system_id"], event["event_type"], event["source"], event["details"].get("rule_id"))
        canonical = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        if len(canonical.encode("utf-8")) > 240 * 1024:
            raise ValueError("Normalized event exceeds the 240 KiB payload limit")
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        event.update({
            "PK": f"SYSTEM#{payload.system_id}",
            "SK": f"EVENT#{event['occurred_at']}#{payload.event_id}",
            "received_at": timestamp(datetime.fromtimestamp(int(sent_timestamp) / 1000, timezone.utc)),
        })
        control = self.repository.ensure_event(event, fingerprint)
        logger.info("event.persisted event_id=%s notification_status=%s", event["event_id"], control["notification_status"])
        if control["notification_status"] in ("PUBLISHED", "SKIPPED"):
            logger.info("event.duplicate_skipped event_id=%s", event["event_id"])
            return
        owner = str(uuid4())
        if not self.repository.acquire(event["event_id"], owner, int(time.time())):
            return
        stored = self.repository.event(control)
        reason = suppression_reason(stored)
        if reason:
            self.repository.suppress(event["event_id"], owner, reason)
            logger.info("event.notification_suppressed event_id=%s reason=%s", event["event_id"], reason)
            return
        if self.push is not None:
            rule = stored["details"] if stored["source"] == "host-alert-evaluator" else None
            if self.configuration is None or self.configuration.rule_active(rule):
                current = self.repository.get_control(event["event_id"])
                if current.get("push_status") != "PUBLISHED":
                    message_id = self.push.publish(stored)
                    self.repository.record_push(event["event_id"], owner, message_id, timestamp(datetime.now(timezone.utc)))
                    logger.info("push.published event_id=%s sns_message_id=%s", event["event_id"], message_id)
        if self.configuration is not None:
            self.publish_channels(event["event_id"], owner)
            return
        message_id = self.alerts.publish(stored)
        self.repository.complete(event["event_id"], owner, message_id, timestamp(datetime.now(timezone.utc)))


    def publish_channels(self, event_id, owner):
        control = self.repository.get_control(event_id)
        targets = control.get("notification_targets")
        stored = self.repository.event(control)
        if targets is None:
            if "notification_channel_ids" in stored:
                rule = stored["details"] if stored["source"] == "host-alert-evaluator" else None
                targets = self.configuration.targets(stored["notification_channel_ids"], rule=rule)
            else:
                targets = self.configuration.targets()
            self.repository.assign_targets(event_id, owner, targets)
        logger.info("event.targets_selected event_id=%s channel_count=%s", event_id, len(targets))
        publications = control.get("publications", {})
        for target in targets:
            if target["id"] in publications and publications[target["id"]]["status"] != "PARTIAL":
                continue
            if not self.configuration.still_active(target):
                publication = {"status": "SKIPPED_CONFIGURATION_CHANGED", "emails": target["emails"]}
                previous = publications.get(target["id"], {})
                if previous.get("transport") == "SES" and previous.get("recipients"):
                    publication = dict(previous, status="PUBLISHED", published_at=max(
                        value["accepted_at"] for value in previous["recipients"].values()))
                logger.info("sns.skipped event_id=%s channel_id=%s reason=configuration_changed", event_id, target["id"])
            elif self.email is not None:
                publication = self.publish_email(stored, target, owner, publications.get(target["id"], {}))
            else:
                logger.info("sns.publish_started event_id=%s channel_id=%s", event_id, target["id"])
                message_id = self.alerts.publish(stored, topic_arn=target["topic_arn"])
                logger.info("sns.published event_id=%s channel_id=%s sns_message_id=%s", event_id, target["id"], message_id)
                publication = {"status": "PUBLISHED", "emails": target["emails"], "sns_message_id": message_id,
                               "published_at": timestamp(datetime.now(timezone.utc))}
            self.repository.record_publication(event_id, owner, target["id"], publication)
            publications[target["id"]] = publication
        self.repository.complete_channels(event_id, owner, publications)
        logger.info("event.completed event_id=%s published_channels=%s", event_id,
                    sum(value["status"] == "PUBLISHED" for value in publications.values()))

    def publish_email(self, stored, target, owner, previous):
        # Persist each accepted recipient before advancing. A different recipient's
        # failure must not resend mail whose acceptance was already recorded.
        receipts = dict(previous.get("recipients", {}))
        publication = {"status": "PARTIAL", "transport": "SES", "recipients": receipts}
        for recipient in self.email.recipients(target):
            key = hashlib.sha256(recipient.encode()).hexdigest()
            if key in receipts:
                continue
            if not self.configuration.still_active(target):
                break
            message_id = self.email.send(stored, recipient)
            receipts[key] = {"email": recipient, "ses_message_id": message_id,
                             "accepted_at": timestamp(datetime.now(timezone.utc))}
            publication["emails"] = sorted(value["email"] for value in receipts.values())
            self.repository.record_publication(stored["event_id"], owner, target["id"], publication)
            logger.info("ses.accepted event_id=%s channel_id=%s ses_message_id=%s", stored["event_id"], target["id"], message_id)
        publication.update({"status": "PUBLISHED" if receipts else "SKIPPED_NO_CONFIRMED_RECIPIENTS",
                            "emails": sorted(value["email"] for value in receipts.values()),
                            "published_at": max((value["accepted_at"] for value in receipts.values()), default=None)})
        return publication
