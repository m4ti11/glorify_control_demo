from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import logging
import os
from zoneinfo import ZoneInfo


from service import build_service

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
ZONE = ZoneInfo("America/Argentina/Buenos_Aires")


def lambda_handler(event, context):
    if not isinstance(event, dict):
        raise ValueError("Invalid report event")
    try:
        allowed = json.loads(os.environ["REPORT_SYSTEM_IDS"])
    except (KeyError, json.JSONDecodeError):
        logger.error("report_system_configuration_invalid")
        raise RuntimeError("Report system configuration is invalid") from None
    def validate_target(system_id, report_date):
        if not isinstance(system_id, str) or system_id not in allowed:
            raise ValueError("Scheduled system is not configured")
        try:
            parsed_date = date.fromisoformat(report_date)
        except (TypeError, ValueError):
            raise ValueError("An explicit valid report_date is required") from None
        if parsed_date.isoformat() != report_date:
            raise ValueError("An explicit valid report_date is required")
        return system_id, parsed_date

    def target(message):
        system_id = message.get("system_id")
        if "report_date" in message:
            system_id, report_date = validate_target(system_id, message.get("report_date"))
        elif "scheduled_time" in message:
            scheduled_time = message.get("scheduled_time")
            if not isinstance(scheduled_time, str):
                raise ValueError("Scheduler scheduled_time is required")
            try:
                nominal_time = datetime.fromisoformat(scheduled_time.replace("Z", "+00:00"))
                if nominal_time.tzinfo is None or nominal_time.utcoffset() is None:
                    raise ValueError()
                report_date = nominal_time.astimezone(ZONE).date() - timedelta(days=1)
            except ValueError:
                raise ValueError("Scheduler scheduled_time is invalid") from None
            system_id, report_date = validate_target(system_id, report_date.isoformat())
        else:
            raise ValueError("Provide report_date or Scheduler scheduled_time")
        if not isinstance(system_id, str) or system_id not in allowed:
            logger.error("report_system_not_configured")
            raise ValueError("Scheduled system is not configured")
        return system_id, report_date

    if "Records" in event:
        failures = []
        for record in event["Records"]:
            try:
                system_id, report_date = target(json.loads(record["body"]))
                result = build_service().generate(system_id, report_date, datetime.now(timezone.utc))
                if result["status"] == "busy":
                    raise RuntimeError("Report claim is still leased")
            except Exception as error:
                logger.error("report_queue_attempt_failed message_id=%s error_type=%s", record.get("messageId", "unknown"), type(error).__name__)
                failures.append({"itemIdentifier": record.get("messageId")})
        return {"batchItemFailures": failures}
    # Manual invocations remain explicit; Scheduler now sends directly to SQS.
    system_id, report_date = target(event)
    result = build_service().generate(system_id, report_date, datetime.now(timezone.utc))
    if result["status"] == "busy":
        raise RuntimeError("Report claim is still leased")
    return result
