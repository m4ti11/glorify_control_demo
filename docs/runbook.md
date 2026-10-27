# Runbook

These are procedures for a hypothetical separate deployment, not actions performed against a live account. Start by assuming the intended account/region/role and confirming the system ID (`demo-vps-01` is only an example). Do not paste SQL, credentials or live payloads into public issues.

## Alarm responses

All pipeline alarm and OK actions should reach the owned operations SNS topic. Confirm its email subscription before relying on it. After an OK transition, verify a fresh reading and that the affected backlog is draining.

| Alarm resource / instance | Meaning | Diagnose | Recover | Definition |
| --- | --- | --- | --- | --- |
| events_dlq | Event messages exhausted retries | Inspect DLQ payload/event ID, worker logs and delivery checkpoints. | Correct schema, IAM or destination failure, then redrive a small batch and verify stored status before a full redrive. | cloudwatch.tf |
| events_age | Old event messages are waiting | Check queue age, Lambda concurrency, throttles and downstream SNS/SES errors. | Restore the dependency or worker capacity; watch age decrease and avoid deleting the queue. | cloudwatch.tf |
| report_generation_errors | Report worker invocation failed | Correlate system/day, Scheduler nominal time, recovery message and active claim lease. | Fix inputs/provider access; allow the owner lease to expire or finish, then retry the original day. Reuse existing immutable output. | cloudwatch.tf |
| report_recovery_dlq | Report retries exhausted | Inspect original system/day, saved S3 JSON/PDF and claim state. | Correct the root cause and redrive the original dated job; do not delete a completed report to force regeneration. | cloudwatch.tf |
| report_enqueue_dlq | Report enqueue/delivery path failed | Inspect failed destination message, queue policy and role permissions. | Repair the enqueue integration and replay with the original system/day. | cloudwatch.tf |
| report_scheduler_dlq | Scheduled report target could not be delivered | Inspect Scheduler target role, queue policy and nominal scheduled time. | Repair the target and replay the dated job; preserve calendar identity. | cloudwatch.tf |
| report_queue_age | Recovery jobs are too old | Check claim lease/visibility configuration and worker error logs. | Restore the worker/provider, let existing leases settle and retry bounded batches. | cloudwatch.tf |
| configuration_sync_failures | Configuration stream sync failed | Inspect destination queue, channel revision, SNS permissions and subscription state. | Repair access or destination settings and request sync for the current revision, then verify recipients. | cloudwatch.tf |
| heartbeats_missing | No successfully persisted heartbeats within the alarm window | Check agent service/spool, internet access, authorizer, ingestion errors and metrics persistence logs. Compare measured time with receipt time. | Restore the agent/ingestion path and confirm measurement timestamps advance. | cloudwatch.tf |
| Any Lambda error alarm | A function invocation failed | Identify the function and request ID in CloudWatch, then inspect the error, input and dependency permissions. | Fix the cause and retry the original input according to that function’s idempotency contract. Check errors and backlog after recovery. | cloudwatch.tf |
| host_alert_failures | Host rule evaluation failed | Correlate stream sequence, rule ID, watermark and referenced raw payload. | Repair the evaluator or rule, then replay retained input before history expires. Verify no duplicate transition. | host_alerts.tf |
| host_alert_stream_age | DynamoDB stream evaluator is falling behind | Inspect iterator age, throttles, concurrency and poisoned records. | Restore capacity/dependencies and watch age drop before stream retention expires. | host_alerts.tf |
| host_alert_destination_failures | Failed evaluation output could not reach its destination | Inspect Lambda destination permissions, SQS policy and pending evaluation state. | Repair the destination; recover original input and retry with deterministic IDs. | host_alerts.tf |
| health_check_errors | Cloud availability monitor failed | Check Scheduler invocations, state table access and conditional conflicts. | Restore permissions/configuration; verify a fresh successful invocation and independent heartbeat alarm recovery. | health_monitor.tf |
| health_check_scheduler_dlq | Availability Scheduler delivery failed | Inspect its DLQ and target invocation role. | Repair the role/target, then replay cautiously and verify outage state before notifications. | health_monitor.tf |
| website_health | Optional Route 53 example website is unhealthy | Verify the Terraform hostname/path and response status independently. | Restore the monitored service or correct the reviewed check definition, then verify both health and recovery notification. | website_health.tf |

## Agent and replay

Check service status and bounded spool counts without printing credentials. Preserve SQLite before reinstalling; distinguish retryable backoff from quarantined messages. Diagnose rejection before requeueing. The installer prepares dependencies before stopping the service, but automatic rollback is not implemented.

Read DLQ records using a limited operator role. Correlate system ID, event UUID, rule ID, SQS message ID, Lambda request ID and provider message ID. Fix the underlying error before redrive. Keep the original UUID and report date; changing them may create new records or notifications. Sending to SNS/SES and acknowledging DynamoDB cannot be one atomic transaction.

## Restore and daily close

Raw snapshots and generated reports are immutable. A late source does not revise the report; record a correction separately until versioned reports exist. Back up state, configuration and operational stores according to an explicit policy. No restore drill or RPO/RTO measurement has been completed by this showcase. Review retention before accepting an offline recovery window.
