from __future__ import annotations

import logging
import os
from models import relevant
from repository import SynchronizationRepository
from service import SynchronizationService

logger = logging.getLogger(__name__)


def lambda_handler(event, context):
    service = SynchronizationService(SynchronizationRepository(os.environ["CONFIGURATION_TABLE_NAME"], os.environ["CHANNEL_TOPIC_PREFIX"]), os.environ["SNS_ARN_PREFIX"])
    failures = []
    for record in event["Records"]:
        if not relevant(record):
            continue
        try:
            service.sync(record)
        except Exception as error:
            logger.error("Notification synchronization failed error_type=%s", type(error).__name__)
            failures.append({"itemIdentifier": record["dynamodb"]["SequenceNumber"]})
    return {"batchItemFailures": failures}
