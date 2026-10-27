from email import policy
from email.parser import BytesParser
from io import BytesIO
from unittest.mock import Mock, patch

from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError
from pypdf import PdfReader
import pytest

from lambda_loader import load_lambda

mods = load_lambda("generate_report", ["models", "repository", "delivery"])
render_pdf = mods["report_pdf"].render_pdf
Publisher = mods["delivery"].ReportPublisher
REPORT = {
    "schema_version": 1, "system_id": "vps", "report_date": "2026-10-19", "timezone": "America/Argentina/Buenos_Aires",
    "generated_at": "2026-10-20T06:30:00Z", "window": {"start": "2026-10-19T03:00:00Z", "end_exclusive": "2026-10-20T03:00:00Z"},
    "source_status": {"host_metrics": {"status": "available", "count": 720}, "events": {"status": "available", "count": 0}, "postgresql": {"status": "incomplete", "count": 1}},
    "analysis": {"summary": "Operational summary: stable load and PostgreSQL review.",
                 "findings": [{"severity": "high", "title": "CPU < 90%", "description": "Description: connection and analysis.", "evidence_refs": ["metric:one"]}],
                 "recommendations": [{"priority": "normal", "action": "Review configuration.", "evidence_refs": ["metric:one"]}]},
}


def publisher(enabled=True):
    return Publisher("shared", "jobs", "config", sender="reports@example.test", email_enabled=enabled,
                     s3=Mock(), dynamodb=Mock(), sns=Mock(), ses=Mock(), topic_prefix="arn:aws:sns:us-east-1:123:glorify-notification-")


def test_pdf_has_text_pages_and_safe_markup_for_generated_content():
    raw = render_pdf(REPORT)
    doc = PdfReader(BytesIO(raw))
    text = " ".join(page.extract_text() for page in doc.pages)
    assert raw.startswith(b"%PDF-") and len(raw) < 2 * 1024 * 1024
    assert "Daily operational report" in text and "CPU < 90%" in text
    assert "connection" in text and "metric:one" in text and "Recommendations" in text
    long = {**REPORT, "analysis": {**REPORT["analysis"], "summary": "Texto extenso de prueba. " * 900}}
    assert len(PdfReader(BytesIO(render_pdf(long))).pages) > 1


def test_pdf_and_json_use_shared_generated_prefix_and_disabled_mail_never_sends():
    pub = publisher(False); pub.publish(REPORT)
    calls = pub.s3.put_object.call_args_list
    assert [c.kwargs["Key"] for c in calls] == ["generated/operational/vps/2026/10/19.json.gz", "generated/operational/vps/2026/10/19.pdf"]
    assert all(c.kwargs["IfNoneMatch"] == "*" for c in calls)
    pub.ses.send_raw_email.assert_not_called(); pub.sns.list_subscriptions_by_topic.assert_not_called()


def test_only_confirmed_enabled_current_channel_emails_receive_reports():
    pub = publisher(); serializer = TypeSerializer()
    channel = {"version": 2, "data": {"enabled": True, "emails": ["confirmed@example.test", "pending@example.test"]},
               "delivery": {"status": "SYNCED", "synced_version": 2, "topic_arn": pub.topic_prefix + "one"}}
    paused = {**channel, "data": {"enabled": False, "emails": ["paused@example.test"]}}
    pub.dynamodb.query.return_value = {"Items": [{k: serializer.serialize(v) for k, v in c.items()} for c in [channel, paused]]}
    pub.sns.list_subscriptions_by_topic.return_value = {"Subscriptions": [
        {"Protocol": "email", "SubscriptionArn": "arn:confirmed", "Endpoint": "confirmed@example.test"},
        {"Protocol": "email", "SubscriptionArn": "PendingConfirmation", "Endpoint": "pending@example.test"},
        {"Protocol": "email", "SubscriptionArn": "arn:old", "Endpoint": "removed@example.test"},
    ]}
    assert pub.recipients() == ["confirmed@example.test"]
    channel["delivery"]["synced_version"] = 1
    pub.dynamodb.query.return_value = {"Items": [{k: serializer.serialize(v) for k, v in channel.items()}]}
    with pytest.raises(RuntimeError, match="not synchronized"):
        pub.recipients()


def test_email_has_readable_alternatives_pdf_attachment_and_delivery_marker():
    pub = publisher(); pub.ses.send_raw_email.return_value = {"MessageId": "ses-one"}
    pdf = render_pdf(REPORT); pub.send(REPORT, pdf, "person@example.test")
    sent = pub.ses.send_raw_email.call_args.kwargs
    message = BytesParser(policy=policy.default).parsebytes(sent["RawMessage"]["Data"])
    assert message["From"] == "reports@example.test"
    assert sent["Destinations"] == ["person@example.test"]
    assert message.get_body(preferencelist=("html",)).get_content_type() == "text/html"
    attachment = next(message.iter_attachments())
    assert attachment.get_content_type() == "application/pdf" and attachment.get_payload(decode=True) == pdf
    assert pub.dynamodb.update_item.call_args.kwargs["ExpressionAttributeValues"][":sent"] == {"S": "SENT"}


def test_previously_sent_recipient_is_not_sent_again_and_failed_send_is_retryable():
    pub = publisher()
    conflict = ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
    pub.dynamodb.update_item.side_effect = conflict
    pub.dynamodb.get_item.return_value = {"Item": {"status": {"S": "SENT"}}}
    pub.send(REPORT, b"%PDF-test", "person@example.test")
    pub.ses.send_raw_email.assert_not_called()
    pub.dynamodb.update_item.side_effect = None
    pub.ses.send_raw_email.side_effect = RuntimeError("mail failed")
    with pytest.raises(RuntimeError):
        pub.send(REPORT, b"%PDF-test", "person@example.test")
    assert pub.dynamodb.update_item.call_args.kwargs["ExpressionAttributeValues"][":retry"] == {"S": "RETRYABLE"}


def test_pdf_shows_explicit_day_coverage_and_largest_gap():
    report = {**REPORT, "source_status": {**REPORT["source_status"], "host_metrics": {
        "status": "incomplete", "count": 1, "coverage": {"percentage": 0.14, "present_intervals": 1,
        "expected_intervals": 720, "expected_interval_seconds": 120, "largest_gap_seconds": 86340,
        "sufficient": False, "is_lower_bound": False}}}}
    text = " ".join(p.extract_text() for p in PdfReader(BytesIO(render_pdf(report))).pages)
    assert "0.14%" in text and "1/720" in text and "86340" in text
    assert "insufficient" in text.lower()
