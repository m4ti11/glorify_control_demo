from __future__ import annotations

import base64
import binascii
import json
import logging
import os

from botocore.exceptions import BotoCoreError, ClientError
from models import MAX_REPORT_BYTES
from repository import PgStatementsRepository
from service import PgStatementsService

logger = logging.getLogger(__name__)


def response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(body, separators=(",", ":"))}


def lambda_handler(event, context):
    if event.get("requestContext", {}).get("http", {}).get("method", "POST") != "POST":
        return response(405, {"message": "Method not allowed"})
    try:
        raw = event.get("body")
        if not isinstance(raw, str):
            raise ValueError("JSON body required")
        if len(raw) > MAX_REPORT_BYTES * 2:
            raise OverflowError()
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw, validate=True).decode("utf-8")
        created, result = PgStatementsService(PgStatementsRepository(os.environ["PG_REPORTS_BUCKET"])).ingest(raw)
    except OverflowError:
        return response(413, {"message": "Report exceeds the 1 MiB JSON limit"})
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return response(422, {"message": "Invalid PostgreSQL report or future report_date"})
    except (BotoCoreError, ClientError):
        logger.error("Could not store PostgreSQL report")
        return response(503, {"message": "Report storage temporarily unavailable; retry with the same report date"})
    return response(201 if created else 409, result if created else dict(result, message="A report already exists for this system and report date"))
