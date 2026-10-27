from __future__ import annotations

from models import topic_name


class SynchronizationService:
    def __init__(self, repository, arn_prefix):
        self.repository = repository
        self.arn_prefix = arn_prefix

    def sync(self, record):
        images = record["dynamodb"]
        old = self.repository.decode(images.get("OldImage", {}))
        current = self.repository.current(images["Keys"])
        target = self.arn_prefix + topic_name(self.repository.prefix, current) if current else None
        # Derive old targets from desired settings too, covering a failure before
        # delivery metadata was persisted. SNS DeleteTopic is idempotent.
        old_target = self.arn_prefix + topic_name(self.repository.prefix, old) if old else None
        if old_target and old_target != target:
            self.repository.delete_topic(old_target)
        if current is None:
            return
        if not current["data"]["enabled"]:
            # Pausing stops publication through the source configuration. Keep an
            # existing topic so confirmed recipients do not need to opt in again.
            delivery = current.get("delivery", {})
            topic = delivery.get("topic_arn") if delivery.get("topic_arn") == target else None
            self.repository.synced(current, topic, delivery.get("recipients", []) if topic else [], "DISABLED")
            return
        try:
            topic = self.repository.topic(topic_name(self.repository.prefix, current))
            if topic != target:
                raise RuntimeError("Unexpected SNS topic")
            recipients = self.repository.recipients(topic, current["data"]["emails"])
            self.repository.synced(current, topic, recipients, "SYNCED")
        except Exception:
            self.repository.save_status(current, {**current.get("delivery", {}), "status": "ERROR", "message": "SNS synchronization failed; retry synchronization"})
            raise
