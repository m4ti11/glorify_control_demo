from __future__ import annotations

import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from models import output_json_schema


class OpenAIError(RuntimeError):
    pass


class ResponsesClient:

    def __init__(self, model, timeout_seconds=45, max_response_bytes=128 * 1024, opener=None):
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        # Never inherit arbitrary proxy settings that may redirect the secret.
        self.opener = opener or build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()))

    def analyze(self, api_key, payload: bytes) -> str:
        body = json.dumps({
            "model": self.model,
            "store": False,
            "max_output_tokens": 6000,
            "input": [
                {"role": "system", "content": "Analyze only the supplied operational observations. Evaluate host_metrics.coverage explicitly: percentage measures distinct expected time slots observed, largest_gap_seconds includes day edges. Insufficient or lower-bound coverage does not support conclusions about the entire day, even if the last sample is recent. Missing, stale, incomplete and unavailable sources are not zero. PostgreSQL data is a point-in-time snapshot, not a daily delta. Counters identified as cumulative since stats_reset or another reset marker must not be described as daily deltas unless actual interval deltas are supplied. Distinguish reporting_date, collection_started_at, and captured_at. Do not infer facts not supported by supplied evidence. Every finding and recommendation must cite evidence_ref values that appear in the input. Write the summary, finding titles/descriptions and recommendation actions in Spanish. Keep identifiers and evidence references unchanged. Return concise, actionable analysis."},
                {"role": "user", "content": payload.decode("utf-8")},
            ],
            "text": {"format": {"type": "json_schema", "name": "operational_report", "strict": True,
                                 "schema": output_json_schema()}},
        }, separators=(",", ":")).encode()
        request = Request("https://api.openai.com/v1/responses", data=body,
                          headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                raw = response.read(self.max_response_bytes + 1)
        except HTTPError as error:
            # Do not retain or log the provider response body; it may echo inputs.
            raise OpenAIError(f"OpenAI request failed with HTTP {error.code}") from None
        except (URLError, TimeoutError, OSError) as error:
            raise OpenAIError("OpenAI request failed due to a network error") from None
        if len(raw) > self.max_response_bytes:
            raise OpenAIError("OpenAI response exceeded the transport limit")
        try:
            response = json.loads(raw)
            if response.get("status") != "completed":
                raise ValueError()
            texts = [part["text"] for item in response.get("output", [])
                     if item.get("type") == "message" for part in item.get("content", [])
                     if part.get("type") == "output_text"]
            if len(texts) != 1:
                raise ValueError()
            return texts[0]
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise OpenAIError("OpenAI returned an incomplete or malformed response") from None
