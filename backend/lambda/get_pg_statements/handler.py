from __future__ import annotations

import json
import logging
import os

from botocore.exceptions import BotoCoreError, ClientError
from models import ReportQuery
from repository import PgStatementsRepository
from service import PgStatementsService

logger = logging.getLogger(__name__)


def response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(body, separators=(",", ":"), allow_nan=False)}


def lambda_handler(event, context):
    if event.get("requestContext", {}).get("http", {}).get("method", "GET") != "GET":
        return response(405, {"message": "Method not allowed"})
    try:
        query = ReportQuery(event.get("queryStringParameters"))
    except (ValueError, TypeError):
        return response(400, {"message": "Provide system_id and report_date as YYYY-MM-DD"})
    try:
        result = PgStatementsService(PgStatementsRepository(os.environ["PG_REPORTS_BUCKET"])).get(query)
    except (BotoCoreError, ClientError, RuntimeError):
        logger.error("Could not read PostgreSQL report")
        return response(503, {"message": "Report temporarily unavailable"})
    if result is None:
        return response(404, {"message": "No report for this system and report date"})
    return response(200, result)
