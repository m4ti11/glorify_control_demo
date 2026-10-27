from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError
from lambda_loader import load_lambda

modules = load_lambda("get_events")
models, repository, service, handler = [modules[name] for name in ("models", "repository", "service", "handler")]
NOW = datetime(2026, 10, 19, 17, tzinfo=timezone.utc)
KEY = {"PK": {"S": "SYSTEM#vps"}, "SK": {"S": "EVENT#2026-10-19T15:00:00.000Z#id"}, "record_type": {"S": "event"}}
EVENT = {"PK": "SYSTEM#vps", "SK": KEY["SK"]["S"], "record_type": "event", "event_id": "id", "occurred_at": "2026-10-19T15:00:00.000Z", "details": {"code": Decimal("137")}}
CONTROL = {"PK": "EVENT#id", "SK": "CONTROL", "notification_status": "PUBLISHED", "published_at": "now", "email_status": "PUBLISHED", "email_recipient": "alerts@example.com", "payload_hash": "private"}


def query(params=None):
    return models.EventsQuery(params, now=NOW)


class EventsQueryTests(unittest.TestCase):
    def test_default_is_current_calendar_week_in_buenos_aires(self):
        result = query()
        self.assertEqual(result.filters(), {"from": "2026-10-19", "to": "2026-10-19", "limit": 10, "timezone": models.TIMEZONE})
        self.assertEqual(result.lower, "EVENT#2026-10-19T03:00:00")
        self.assertEqual(result.upper, "EVENT#2026-10-20T02:59:59~")
        sunday = models.EventsQuery({}, now=datetime(2026, 10, 19, 2, tzinfo=timezone.utc))
        self.assertEqual(sunday.start.isoformat(), "2026-10-12")
        self.assertEqual(sunday.end.isoformat(), "2026-10-18")

    def test_date_range_includes_local_end_day(self):
        result = query({"from": "2026-09-01", "to": "2026-09-30", "limit": "5"})
        self.assertEqual(result.lower, "EVENT#2026-09-01T03:00:00")
        self.assertEqual(result.upper, "EVENT#2026-10-01T02:59:59~")
        self.assertEqual(result.limit, 5)

    def test_invalid_unbounded_or_unknown_parameters_are_rejected(self):
        for params in ({"from": "bad"}, {"from": "2026-02-30"}, {"from": "2026-10-21", "to": "2026-10-19"},
                       {"from": "2020-01-01", "to": "2026-10-19"}, {"limit": "0"}, {"limit": "11"},
                       {"limit": "1.0"}, {"limit": "-1"}, {"system_id": "injected"}, {"from": "9999-01-01"}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                query(params)

    def test_cursor_round_trip_and_binding_to_range_and_limit(self):
        token = query().cursor(KEY)
        self.assertEqual(query({"cursor": token}).key, KEY)
        for changes in ({"limit": "5"}, {"from": "2026-10-18"}, {"to": "2026-10-20"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                query(dict(changes, cursor=token))

    def test_cursor_rejects_injected_partition_or_control_key(self):
        for key in (dict(KEY, PK={"S": "EVENT#id"}), dict(KEY, record_type={"S": "event_control"}),
                    dict(KEY, SK={"S": "CONTROL"}), dict(KEY, extra={"S": "extra"})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                query({"cursor": query().cursor(key)})
        for token in ("not-json", "%%%", "x" * 4097):
            with self.subTest(token=token[:10]), self.assertRaises(ValueError):
                query({"cursor": token})


class EventsRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.repo = repository.EventsRepository("events", client=self.client)
        from boto3.dynamodb.types import TypeSerializer
        self.encoder = TypeSerializer()

    def encode(self, item):
        return {k: self.encoder.serialize(v) for k, v in item.items()}

    def test_bounded_query_and_strongly_consistent_hydration_without_scan(self):
        self.client.query.return_value = {"Items": [KEY], "LastEvaluatedKey": KEY}
        self.client.batch_get_item.side_effect = [
            {"Responses": {"events": [self.encode(EVENT)]}},
            {"Responses": {"events": [self.encode(CONTROL)]}},
        ]
        events, controls, last = self.repo.page(query())
        args = self.client.query.call_args.kwargs
        self.assertEqual(args["IndexName"], "events-by-date")
        self.assertFalse(args["ScanIndexForward"])
        self.assertEqual(args["Limit"], 10)
        self.assertIn("BETWEEN", args["KeyConditionExpression"])
        self.assertNotIn("FilterExpression", args)
        self.client.scan.assert_not_called()
        self.assertEqual(events, [EVENT])
        self.assertEqual(controls[("EVENT#id", "CONTROL")], CONTROL)
        self.assertEqual(last, KEY)
        for call in self.client.batch_get_item.call_args_list:
            self.assertTrue(call.kwargs["RequestItems"]["events"]["ConsistentRead"])

    def test_next_cursor_is_used_as_exclusive_start_key(self):
        self.client.query.return_value = {}
        self.repo.page(query({"cursor": query().cursor(KEY)}))
        self.assertEqual(self.client.query.call_args.kwargs["ExclusiveStartKey"], KEY)
        self.client.batch_get_item.assert_not_called()

    def test_batch_get_preserves_query_order_even_with_reordered_results(self):
        second = dict(EVENT, PK="SYSTEM#other", event_id="second", SK="EVENT#2026-10-19T14:00:00.000Z#second")
        key2 = dict(KEY, PK={"S": second["PK"]}, SK={"S": second["SK"]})
        self.client.query.return_value = {"Items": [KEY, key2]}
        self.client.batch_get_item.side_effect = [
            {"Responses": {"events": [self.encode(second), self.encode(EVENT)]}},
            {"Responses": {"events": []}},
        ]
        events, _, _ = self.repo.page(query())
        self.assertEqual([event["event_id"] for event in events], ["id", "second"])

    def test_unprocessed_keys_are_retried_and_not_silently_dropped(self):
        request = {"events": {"Keys": [{"PK": KEY["PK"], "SK": KEY["SK"]}], "ConsistentRead": True}}
        self.client.batch_get_item.side_effect = [
            {"UnprocessedKeys": request}, {"Responses": {"events": [self.encode(EVENT)]}},
        ]
        with patch.object(repository.time, "sleep"):
            result = self.repo._batch(request["events"]["Keys"])
        self.assertEqual(len(result), 1)
        self.assertEqual(self.client.batch_get_item.call_count, 2)
        self.client.batch_get_item.side_effect = None
        self.client.batch_get_item.return_value = {"UnprocessedKeys": request}
        with patch.object(repository.time, "sleep"), self.assertRaises(RuntimeError):
            self.repo._batch(request["events"]["Keys"])

    def test_missing_indexed_event_fails_page_but_missing_control_is_allowed(self):
        self.client.batch_get_item.return_value = {"Responses": {"events": []}}
        with self.assertRaises(RuntimeError):
            self.repo._batch([KEY])
        self.assertEqual(self.repo._batch([KEY], allow_missing=True), {})


class EventsServiceTests(unittest.TestCase):
    def test_response_has_public_event_and_recorded_email_state_only(self):
        repo = Mock()
        repo.page.return_value = ([EVENT], {("EVENT#id", "CONTROL"): CONTROL}, KEY)
        result = service.EventsService(repo).list(query())
        item = result["items"][0]
        self.assertNotIn("PK", item)
        self.assertNotIn("SK", item)
        self.assertNotIn("payload_hash", json.dumps(item, default=str))
        self.assertEqual(item["notification"]["email_status"], "PUBLISHED")
        self.assertEqual(item["notification"]["email_recipient"], "alerts@example.com")
        self.assertIsNone(item["notification"]["delivery_confirmed"])
        self.assertIsNotNone(result["next_cursor"])

    def test_legacy_or_missing_control_never_claims_email_was_sent(self):
        for controls in ({}, {("EVENT#id", "CONTROL"): {"notification_status": "PUBLISHED"}}):
            repo = Mock()
            repo.page.return_value = ([EVENT], controls, None)
            result = service.EventsService(repo).list(query())
            self.assertEqual(result["items"][0]["notification"]["email_status"], "NOT_TRACKED")


class EventsHandlerTests(unittest.TestCase):
    def test_invalid_query_and_method_do_not_call_aws(self):
        with patch.object(handler, "EventsRepository") as repo:
            self.assertEqual(handler.lambda_handler({"queryStringParameters": {"limit": "100"}}, None)["statusCode"], 400)
            self.assertEqual(handler.lambda_handler({"requestContext": {"http": {"method": "POST"}}}, None)["statusCode"], 405)
        repo.assert_not_called()

    def test_empty_results_return_200_and_no_cache(self):
        with patch.dict(os.environ, EVENTS_TABLE_NAME="events"), patch.object(handler, "EventsRepository") as repo:
            repo.return_value.page.return_value = ([], {}, None)
            result = handler.lambda_handler({}, None)
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(result["headers"]["Cache-Control"], "no-store")
        self.assertEqual(json.loads(result["body"])["items"], [])

    def test_aws_failure_returns_503_without_internal_details(self):
        with patch.dict(os.environ, EVENTS_TABLE_NAME="events"), patch.object(handler, "EventsRepository") as repo, self.assertLogs(handler.logger):
            repo.return_value.page.side_effect = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "private"}}, "Query")
            result = handler.lambda_handler({}, None)
        self.assertEqual(result["statusCode"], 503)
        self.assertNotIn("private", result["body"])
