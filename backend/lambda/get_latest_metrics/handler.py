from __future__ import annotations

from decimal import Decimal
import json
import logging
import os
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from repository import MetricsRepository
from service import MetricsService


logger = logging.getLogger(__name__)


def _json_number(value: Any) -> int | float:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body, default=_json_number, separators=(",", ":")),
    }


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    if method != "GET":
        return _response(405, {"message": "Method not allowed"})
    try:
        snapshot = MetricsService(MetricsRepository(os.environ["METRICS_TABLE_NAME"])).get_latest()
    except (BotoCoreError, ClientError):
        logger.exception("Could not read the latest metrics")
        return _response(503, {"message": "Metrics temporarily unavailable"})
    if snapshot is None:
        return _response(404, {"message": "No metrics received yet"})
    return _response(200, snapshot.model_dump())
