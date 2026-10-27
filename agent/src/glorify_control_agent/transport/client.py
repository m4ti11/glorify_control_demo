import logging
import re

import httpx

log = logging.getLogger(__name__)


def validation_fields(response):
    """Only paths and error codes; never response text, inputs, ctx or SQL."""
    try:
        if len(response.content) > 64 * 1024:
            return []
        body = response.json()
    except ValueError:
        return []
    if not isinstance(body, dict) or not isinstance(body.get("errors"), list):
        return []
    fields = []
    for error in body["errors"][:10]:
        if not isinstance(error, dict):
            continue
        location, kind = error.get("loc"), error.get("type")
        if not isinstance(location, list) or not isinstance(kind, str):
            continue
        if not re.fullmatch(r"[a-z_]{1,64}", kind):
            continue
        if len(location) > 8 or any(
            not (
                type(part) is int
                or isinstance(part, str)
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", part)
            )
            for part in location
        ):
            continue
        fields.append({"field": ".".join(map(str, location)), "type": kind})
    return fields


class Client:
    def __init__(self, settings, http=None):
        self.settings = settings
        self.http = http or httpx.Client(
            timeout=httpx.Timeout(settings.http_timeout, connect=settings.http_connect_timeout),
            verify=True,
            follow_redirects=False,
            trust_env=False,
            headers={"X-Api-Token": settings.token.get_secret_value()},
        )

    def send(self, route, payload):
        urls = {
            "metrics": self.settings.metrics_url,
            "events": self.settings.events_url,
            "pg-statements": self.settings.pg_statements_url,
        }
        if route not in urls:
            raise ValueError("Unknown delivery route")
        url = urls[route]
        try:
            response = self.http.post(url, content=payload, headers={"Content-Type": "application/json"})
            status = response.status_code
            if status == 422:
                log.warning(
                    "payload_rejected route=%s validation_fields=%s", route, validation_fields(response)
                )
            response.close()
            return status
        except httpx.HTTPError:
            return None

    def probe(self):
        # HTTP 404/405 still confirms DNS, TCP and TLS connectivity; not authorization.
        try:
            response = self.http.head(self.settings.metrics_url)
            status = response.status_code
            response.close()
            return {"reachable": True, "http_status": status}
        except httpx.HTTPError:
            return {"reachable": False, "http_status": None}

    def close(self):
        self.http.close()
