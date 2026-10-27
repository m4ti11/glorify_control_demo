from __future__ import annotations

import base64
import json
import os
from typing import Any

from pydantic import ValidationError
from botocore.exceptions import BotoCoreError, ClientError

from models import MetricsPayload
from repository import MetricsRepository
from service import MetricsService


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, separators=(",", ":")),
    }


def _body(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body")
    if not isinstance(body, str):
        raise ValueError("Request body must be a JSON string")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    decoded = json.loads(body)
    if not isinstance(decoded, dict):
        raise ValueError("JSON body must be an object")
    return decoded


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    if event.get("requestContext") and event.get("requestContext", {}).get("http", {}).get("method") not in (None, "POST"):
        return _response(405, {"message": "Method not allowed"})

    try:
        metrics = MetricsPayload.model_validate(_body(event))
    except ValidationError as error:
        return _response(422, {"message": "Invalid metrics payload", "errors": error.errors(include_url=False)})
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return _response(400, {"message": "Invalid JSON", "detail": str(error)})

    table_name = os.environ.get("METRICS_TABLE_NAME")
    if not table_name:
        raise RuntimeError("METRICS_TABLE_NAME is not configured")

    try:
        measured_at, received_at, resource_count, created = MetricsService(MetricsRepository(table_name)).ingest(
            metrics, request_id=event.get("requestContext", {}).get("requestId"))
    except (BotoCoreError, ClientError):
        return _response(503, {"message": "Metrics storage temporarily unavailable; retry this payload"})
    return _response(
        201 if created else 200,
        {
            "system_id": metrics.system_id,
            "measured_at": measured_at,
            "received_at": received_at,
            "resource_count": resource_count,
            "idempotent_replay": not created,
        },
    )
