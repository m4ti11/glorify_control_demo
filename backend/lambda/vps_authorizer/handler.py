from __future__ import annotations

from functools import lru_cache
import logging
import os
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError

from models import AuthorizationRequest, AuthorizationResult
from repository import TokenRepository
from service import AuthorizationService


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@lru_cache(maxsize=1)
def _service() -> AuthorizationService:
    return AuthorizationService(TokenRepository(os.environ["METRICS_INGEST_SECRET_ARN"]))


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    try:
        request = AuthorizationRequest.model_validate({"headers": event.get("headers") or {}})
        authorized = _service().authorize(request)
    except ValidationError:
        # Validation errors can include header values; never log credentials.
        authorized = False
    except (BotoCoreError, ClientError, KeyError, ValueError):
        logger.exception("VPS authorization failed")
        authorized = False
    logger.info("auth.checked authorized=%s api_request_id=%s", authorized, event.get("requestContext", {}).get("requestId"))
    return AuthorizationResult(isAuthorized=authorized).model_dump()
