# Architecture

We collect data from one trusted Linux VPS. The Python agent schedules host/Docker heartbeats, Docker state detection and PostgreSQL captures. A bounded SQLite spool survives restarts, backs off on failures and quarantines non-retryable messages. It does not retain data indefinitely.

## Flows

- Metrics: agent → API Gateway custom authorizer → ingest Lambda → transactional DynamoDB history → conditional latest snapshot. The history transaction is separate from the snapshot update; replay can repair the latter. The DynamoDB stream drives sustained host rules with watermarks and hysteresis.
- Alerts: evaluator persists pending output before sending SQS. Deterministic event UUIDs let retries refer to the same event. Rules evaluate CPU, memory and disk usage on the monitored host.
- Events: API Gateway sends directly to SQS; the event worker validates, saves and claims a lease before publishing. Delivery checkpoints reduce repeated work.
- Availability: a scheduled Lambda checks recent agent receipt and confirms an outage after a delay. A separate missing-heartbeat alarm still fires if this monitor stops running. Optional Route 53 website checks are configured in Terraform. The dashboard’s Site definitions tab stores proposed URL settings separately.
- Reports: raw PostgreSQL snapshots and daily operational analysis share a system and report date. The report pipeline is described below.

- Dashboard: read and configuration routes use Cognito JWT scopes. The showcase replaces these requests with local files; there is no deployed API behind the public demo.

## Reporting calendar

The UI and daily jobs use America/Argentina/Buenos_Aires (UTC−3) as their reporting calendar. Dates display as YYYY-MM-DD and timestamps use a 24-hour clock in that timezone.

## Report pipeline

The agent captures PostgreSQL statistics and writes the raw snapshot to S3. Statement timings are cumulative since the statistics reset; the capture records when they were observed.

A scheduled daily job gathers host metrics, events and the PostgreSQL snapshot for the reporting window. The worker claims the job, records source coverage and omissions, then sends selected evidence to OpenAI. The result contains a summary, findings, recommendations and evidence references.

The worker saves JSON and PDF artifacts in S3 and can send the PDF through SES. The dashboard presents raw SQL and operational analysis in separate tabs, with PDF preview and download. Failed jobs remain in recovery queues for diagnosis and retry.

## Routes

All route definitions live in `infrastructure/environments/prod/api_gateway.tf`.

| Route | Handler / integration | Trust boundary |
| --- | --- | --- |
| `POST /metrics` | `ingest_metrics` | Shared VPS credential / custom authorizer |
| `POST /events` | Direct SQS → `process_events` | Shared VPS credential / custom authorizer |
| `POST /pg-statements` | `ingest_pg_statements` | Shared VPS credential / custom authorizer |
| `GET /metrics/latest` | `get_latest_metrics` | Cognito read scope |
| `GET /events` | `get_events` | Cognito read scope |
| `GET /pg-statements` | `get_pg_statements` | Cognito read scope |
| `GET /operational-reports` | `get_operational_report` | Cognito read scope; `format=pdf` returns PDF |
| `GET /configuration/{kind}` | `configuration` | Read scope |
| `POST/PUT/DELETE /configuration/...` | `configuration` | Write scope plus administrator group check |

The exact configuration routes, supported kinds and action endpoints are in the route map in that Terraform file. They include notification recipient status, sync and initial import.

## Stores

| Store | Key / purpose | Lifetime / constraints |
| --- | --- | --- |
| Agent SQLite | Routes, due times, control/observation state | Bounded by age, rows and bytes |
| Metrics DynamoDB | Per-system/resource history; `METRICS / SNAPSHOT#LATEST` | History TTL; one global snapshot |
| Events DynamoDB | System/event timestamp+UUID; date index; checkpoints | No complete general retention policy |
| Configuration DynamoDB | `CONFIG#DEFAULT`, revisions and channel references | Global configuration, conditional revisions and quotas |
| Health state DynamoDB | Suspected outage, pending output | Conditional transitions and retry state |
| S3 raw reports | `raw/pg-statements/<system>/<year>/<month>/<day>.json.gz` | Conditional first write; compressed source snapshot |
| S3 generated reports | `generated/operational/<system>/...` | Immutable JSON/PDF; legacy read path retained |
| SQS / DLQs | Events, schedules, report recovery and failed stream output | Finite retention; redrive requires diagnosis |
| Secrets Manager | Ingest credential and OpenAI key | Values provisioned separately |

Terraform defines IAM permissions and alarm wiring. [Runbook](runbook.md) documents recovery boundaries.
