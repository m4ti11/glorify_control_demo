from __future__ import annotations

import logging
import os

from boto3.dynamodb.types import TypeDeserializer

from models import HOST_PREFIX
from repository import AlertRepository
from service import HostAlertService

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    repository = AlertRepository(os.environ["METRICS_TABLE_NAME"], os.environ["CONFIGURATION_TABLE_NAME"],
                                 os.environ["EVENTS_QUEUE_URL"], max_history_items=int(os.environ.get("MAX_HISTORY_ITEMS", "10000")))
    service = HostAlertService(repository, max_sample_gap_seconds=int(os.environ.get("MAX_SAMPLE_GAP_SECONDS", "600")))
    decoder = TypeDeserializer()
    for record in event.get("Records", []):
        sequence = record["dynamodb"]["SequenceNumber"]
        if context is not None and context.get_remaining_time_in_millis() < 5000:
            return {"batchItemFailures": [{"itemIdentifier": sequence}]}
        try:
            if record.get("eventName") != "INSERT":
                continue
            item = {key: decoder.deserialize(value) for key, value in record["dynamodb"].get("NewImage", {}).items()}
            if item.get("record_type") != "metric" or item.get("resource_type") != "HOST" or item.get("resource_id") != "host":
                continue
            if item.get("PK") != "SYSTEM#" + item["system_id"] or item.get("SK") != HOST_PREFIX + item["measured_at"]:
                raise ValueError("Invalid host metric key")
            logger.info("stream.host_received sequence=%s system_id=%s measured_at=%s", sequence, item["system_id"], item["measured_at"])
            service.process(item)
            logger.info("stream.host_processed sequence=%s system_id=%s measured_at=%s", sequence, item["system_id"], item["measured_at"])
        except Exception as error:
            logger.error("Host evaluation failed sequence=%s error_type=%s", sequence, type(error).__name__)
            # Stop at the earliest failure: later records in this batch must not
            # advance state before the failed reading has been retried.
            return {"batchItemFailures": [{"itemIdentifier": sequence}]}
    return {"batchItemFailures": []}
