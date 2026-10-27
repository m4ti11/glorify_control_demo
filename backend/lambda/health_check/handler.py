from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from repository import HealthRepository
from service import HealthCheckService

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    try:
        system_ids = json.loads(os.environ["MONITORED_SYSTEM_IDS"])
        service = HealthCheckService(
            HealthRepository(
                metrics_table_name=os.environ["METRICS_TABLE_NAME"],
                state_table_name=os.environ["HEALTH_STATE_TABLE_NAME"],
                events_queue_url=os.environ["EVENTS_QUEUE_URL"],
            ),
            stale_after_seconds=int(os.environ.get("METRICS_STALE_AFTER_SECONDS", "180")),
            confirmation_seconds=int(os.environ.get("STALE_CONFIRMATION_SECONDS", "120")),
        )
        now = datetime.now(timezone.utc)
        for system_id in system_ids:
            result = service.check(system_id, now)
            logger.info(
                "health_check.completed system_id=%s status=%s transition=%s",
                system_id,
                result["status"],
                result["transition"],
            )
    except Exception as error:
        # Avoid logging event bodies, DynamoDB items, or SDK error payloads.
        logger.error("health_check.failed error_type=%s", type(error).__name__)
        raise
