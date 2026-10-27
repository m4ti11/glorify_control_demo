from __future__ import annotations

from decimal import Decimal
import json
import logging
import os
import re

from botocore.exceptions import BotoCoreError, ClientError
from models import KINDS, Page, identifier
from repository import ConfigurationRepository, SubscriptionsRepository, Conflict, NotFound
from service import ConfigurationService

logger = logging.getLogger(__name__)


def response(status, body):
    def number(value):
        if isinstance(value, Decimal):
            return int(value) if value == value.to_integral_value() else float(value)
        raise TypeError(type(value).__name__)
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(body, default=number, separators=(",", ":"))}


def is_admin(claims):
    groups = claims.get("cognito:groups", [])
    if isinstance(groups, str):
        try:
            groups = json.loads(groups)
        except ValueError:
            groups = re.findall(r"[A-Za-z0-9_-]+", groups)
    return isinstance(groups, list) and os.environ.get("CONFIGURATION_ADMIN_GROUP", "configuration-admins") in groups


def lambda_handler(event, context):
    request = event.get("requestContext", {})
    method = request.get("http", {}).get("method", "GET")
    claims = request.get("authorizer", {}).get("jwt", {}).get("claims", {})
    scopes = claims.get("scope", "").split()
    if not claims.get("sub") or not any(scope.endswith("/read") for scope in scopes):
        return response(403, {"message": "Read permission required"})
    if method not in {"GET", "POST", "PUT", "DELETE"}:
        return response(405, {"message": "Method not allowed"})
    if method != "GET" and (not is_admin(claims) or not any(scope.endswith("/configuration.write") for scope in scopes)):
        return response(403, {"message": "Configuration administrator permission required"})
    params = event.get("pathParameters") or {}
    kind, uid = params.get("kind"), params.get("id")
    if kind not in KINDS:
        return response(404, {"message": "Configuration category not found"})
    action = params.get("action")
    if action and (kind != "notification-channels" or (method, action) not in {("GET", "status"), ("POST", "sync")}):
        return response(404, {"message": "Configuration action not found"})
    try:
        service = ConfigurationService(ConfigurationRepository(os.environ["CONFIGURATION_TABLE_NAME"]),
                                       SubscriptionsRepository(os.environ.get("LEGACY_ALERTS_TOPIC_ARN")))
        query = event.get("queryStringParameters") or {}
        actor = claims["sub"]
        if method == "POST" and kind == "notification-channels" and uid == "import":
            return response(201, service.import_notifications(actor))
        if uid:
            uid = identifier(uid)
        if method == "GET":
            if params.get("action") == "status" and kind == "notification-channels" and uid:
                return response(200, service.delivery_status(uid))
            return response(200, service.get(kind, uid) if uid else service.list(kind, Page(kind, query)))
        if method == "DELETE":
            if not uid or set(query) != {"version"} or not re.fullmatch(r"[1-9][0-9]{0,9}", query["version"]):
                raise ValueError("Version required")
            service.delete(kind, uid, int(query["version"]))
            return response(200, {"deleted": True})
        raw = event.get("body", "")
        if not isinstance(raw, str) or len(raw.encode()) > 32768 or event.get("isBase64Encoded"):
            raise ValueError("Use JSON of at most 32 KiB")
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError("JSON object required")
        if method == "POST" and params.get("action") == "sync" and kind == "notification-channels" and uid:
            if set(body) != {"version"} or type(body["version"]) is not int or body["version"] < 1:
                raise ValueError("Version required")
            return response(202, service.resync(uid, actor, body["version"]))
        if method == "POST" and uid:
            return response(405, {"message": "Use PUT to edit an item"})
        if method == "PUT" and (not uid or set(body) != {"version", "data"} or type(body["version"]) is not int or body["version"] < 1):
            raise ValueError("Version and data required")
        if method == "POST":
            if set(body) != {"id", "data"}:
                raise ValueError("id and data required")
            uid = identifier(body["id"])
        created, item = service.save(kind, uid, body["data"], actor, body.get("version"))
        return response(201 if created else 200, item)
    except NotFound:
        return response(404, {"message": "Configuration item not found"})
    except Conflict as error:
        return response(409, {"message": str(error)})
    except (ValueError, TypeError, KeyError):
        return response(422, {"message": "Only host/host CPU, memory and disk rules are supported; check thresholds, references and version" if kind == "alert-rules" else "Invalid configuration, references, version or cursor"})
    except (BotoCoreError, ClientError, RuntimeError):
        logger.error("Configuration operation failed")
        return response(503, {"message": "Configuration temporarily unavailable"})
