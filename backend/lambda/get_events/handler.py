from __future__ import annotations

from decimal import Decimal
import json
import logging
import os

from botocore.exceptions import BotoCoreError, ClientError

from models import EventsQuery
from repository import EventsRepository
from service import EventsService

logger = logging.getLogger(__name__)


def _number(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(type(value).__name__)


def _response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(body, default=_number, separators=(",", ":"))}


def lambda_handler(event, context):
    if event.get("requestContext", {}).get("http", {}).get("method", "GET") != "GET":
        return _response(405, {"message": "Method not allowed"})
    try:
        query = EventsQuery(event.get("queryStringParameters"))
    except (ValueError, OverflowError):
        return _response(400, {"message": "Invalid filters. Use from/to as YYYY-MM-DD, limit 1–10 and the cursor for the same filters."})
    try:
        result = EventsService(EventsRepository(os.environ["EVENTS_TABLE_NAME"], os.environ.get("EVENTS_INDEX_NAME", "events-by-date"))).list(query)
    except (BotoCoreError, ClientError, RuntimeError):
        logger.error("Could not read events")
        return _response(503, {"message": "Events temporarily unavailable"})
    return _response(200, result)
