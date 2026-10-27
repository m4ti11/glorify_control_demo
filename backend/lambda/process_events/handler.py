from __future__ import annotations

import logging
import os

from repository import AlertsRepository, EventEmailRepository, EventsRepository, NotificationConfigurationRepository, PushAlertsRepository
from service import EventsService

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    sender = os.environ.get("EVENT_EMAIL_SENDER", "")
    email = EventEmailRepository(sender, os.environ["CHANNEL_TOPIC_ARN_PREFIX"]) if sender else None
    push_topic = os.environ.get("EVENT_PUSH_TOPIC_ARN", "")
    push = PushAlertsRepository(push_topic) if push_topic else None
    service = EventsService(EventsRepository(os.environ["EVENTS_TABLE_NAME"]), AlertsRepository(None),
                            NotificationConfigurationRepository(os.environ["CONFIGURATION_TABLE_NAME"]), email=email, push=push)
    failures = []
    for record in event["Records"]:
        message_id = record["messageId"]
        # Preserve time to return partial failures if batch size increases later.
        if context is not None and context.get_remaining_time_in_millis() < 5000:
            failures.append({"itemIdentifier": message_id})
            continue
        try:
            logger.info("sqs.received message_id=%s", message_id)
            service.process(record["body"], record["attributes"]["SentTimestamp"])
            logger.info("sqs.processed message_id=%s", message_id)
        except Exception as error:
            # Avoid logging credentials, payload contents or potentially sensitive SDK errors.
            logger.error("Event processing failed message_id=%s error_type=%s", message_id, type(error).__name__)
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}
