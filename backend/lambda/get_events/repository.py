from __future__ import annotations

import time

import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config


class EventsRepository:
    def __init__(self, table_name, index_name="events-by-date", client=None):
        self.table_name = table_name
        self.index_name = index_name
        self.client = client or boto3.client("dynamodb", config=Config(
            connect_timeout=2, read_timeout=3, retries={"mode": "standard", "total_max_attempts": 2}))
        self.decoder = TypeDeserializer()

    def page(self, query):
        args = dict(TableName=self.table_name, IndexName=self.index_name,
                    KeyConditionExpression="#kind = :event AND #sort BETWEEN :start AND :end",
                    ExpressionAttributeNames={"#kind": "record_type", "#sort": "SK"},
                    ExpressionAttributeValues={":event": {"S": "event"}, ":start": {"S": query.lower}, ":end": {"S": query.upper}},
                    ScanIndexForward=False, Limit=query.limit)
        if query.key:
            args["ExclusiveStartKey"] = query.key
        result = self.client.query(**args)
        references = [{"PK": item["PK"], "SK": item["SK"]} for item in result.get("Items", [])]
        # KEYS_ONLY indexes keep pages small. Hydrate current records and publication state
        # through bounded, strongly consistent BatchGet requests (never Scan/FilterExpression).
        events = self._batch(references)
        ordered = [events[(key["PK"]["S"], key["SK"]["S"])] for key in references]
        controls = self._batch([{"PK": {"S": f"EVENT#{item['event_id']}"}, "SK": {"S": "CONTROL"}} for item in ordered], allow_missing=True)
        return ordered, controls, result.get("LastEvaluatedKey")

    def _batch(self, keys, allow_missing=False):
        if not keys:
            return {}
        # BatchGet rejects repeated keys, even if duplicates arise from damaged data.
        unique = {(key["PK"]["S"], key["SK"]["S"]): key for key in keys}
        request = {self.table_name: {"Keys": list(unique.values()), "ConsistentRead": True}}
        items = {}
        for attempt in range(3):
            result = self.client.batch_get_item(RequestItems=request)
            for item in result.get("Responses", {}).get(self.table_name, []):
                decoded = {name: self.decoder.deserialize(value) for name, value in item.items()}
                items[(decoded["PK"], decoded["SK"])] = decoded
            request = result.get("UnprocessedKeys", {})
            if not request:
                if not allow_missing and len(items) != len(unique):
                    raise RuntimeError("An indexed event is temporarily unavailable")
                return items
            if attempt < 2:
                time.sleep(0.05 * (2 ** attempt))
        raise RuntimeError("Events could not be fully read; retry the same page")
