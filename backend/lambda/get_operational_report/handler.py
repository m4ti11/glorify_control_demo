from __future__ import annotations

import base64
import json
import logging
import os

from botocore.exceptions import BotoCoreError, ClientError

from models import parse_query
from repository import OperationalReportsRepository
from service import OperationalReportService
from report_pdf import render_pdf

logger = logging.getLogger(__name__)


def response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(body, separators=(",", ":"), allow_nan=False)}


def lambda_handler(event, context):
    if event.get("requestContext", {}).get("http", {}).get("method", "GET") != "GET":
        return response(405, {"message": "Method not allowed"})
    try:
        query = parse_query(event.get("queryStringParameters"))
    except (ValueError, TypeError):
        return response(400, {"message": "Provide a valid system_id and report_date as YYYY-MM-DD"})
    try:
        allowed = json.loads(os.environ["REPORT_SYSTEM_IDS"])
    except (KeyError, json.JSONDecodeError):
        logger.error("Report system configuration is invalid")
        return response(503, {"message": "Report temporarily unavailable"})
    if query.system_id not in allowed:
        return response(404, {"message": "No operational report for this system and date"})
    try:
        repository = OperationalReportsRepository(os.environ["GENERATED_REPORTS_BUCKET"],
            max_output_bytes=int(os.environ.get("REPORT_OUTPUT_MAX_BYTES", "65536")),
            fallback_bucket=os.environ.get("LEGACY_GENERATED_REPORTS_BUCKET"))
        report = OperationalReportService(repository).get(query)
        if report is not None and query.format == "pdf":
            raw = repository.get_pdf(query.key.removesuffix(".json.gz") + ".pdf") or render_pdf(report)
            return {"statusCode": 200, "isBase64Encoded": True,
                    "headers": {"Content-Type": "application/pdf", "Cache-Control": "no-store",
                                "Content-Disposition": f'inline; filename="glorify-{query.system_id}-{query.report_date}.pdf"'},
                    "body": base64.b64encode(raw).decode("ascii")}
    except (BotoCoreError, ClientError, RuntimeError):
        logger.error("Could not read operational report")
        return response(503, {"message": "Report temporarily unavailable"})
    if report is None:
        return response(404, {"message": "No operational report for this system and date"})
    return response(200, report)
