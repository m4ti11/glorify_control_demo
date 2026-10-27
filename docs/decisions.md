# Decisions

## ADR 01: One global latest snapshot

Context: we monitor one VPS in one trust unit and use this project to practice failure-mode design. Decision: retain a global latest snapshot and system-scoped historical keys. Consequence: the dashboard is simple, but adding a second VPS requires new snapshot keys, selectors and authorization. This is an explicit scope limit, not multi-host support.

## ADR 02: Direct API Gateway to SQS for events

Context: events should wait through worker failures. Decision: queue directly from API Gateway and validate/process in the worker. Consequence: fewer synchronous components and bounded retries; malformed accepted messages can reach the DLQ and must be diagnosed.

## ADR 03: Deterministic UUID idempotency

Context: reconnects, stream retries and pending outputs can enqueue the same observation more than once. Decision: derive stable UUIDs for repeatable events and combine them with conditional persistence/checkpoints. Consequence: retries converge on the same record. Provider acceptance and database acknowledgement use separate checkpoints; delivery risks are covered in [security](security.md).

## ADR 04: Persist pending output before enqueue

Context: a worker can crash between state transition and SQS publication. Decision: persist pending output first, then send and acknowledge conditionally. Consequence: retries can recover the intended event. Compare-and-swap state and watermarks add complexity but prevent silently losing an alert transition.

## ADR 05: Hysteresis and leases

Context: noisy metrics and concurrent workers can produce flapping or duplicate work. Decision: use distinct alert/recovery thresholds, sustained windows, cooldowns and expiring conditional claims. Consequence: alerts are delayed intentionally and recovery resumes work after an expired claim.

## ADR 06: Suppress container health-only email

Context: a running container can emit noisy health transitions without exiting. Decision: store those events and mark the email suppression reason explicitly. Consequence: operators retain evidence while receiving fewer emails; a health-only issue may require inspecting the dashboard rather than waiting for mail.

## ADR 07: Serverless without a VPC

Context: the agent initiates outbound HTTPS and Lambdas access public managed service endpoints. Decision: keep these functions outside a VPC. Consequence: avoid NAT and private networking complexity for this scope; egress controls remain a deployment concern.

## ADR 08: Immutable daily report

Context: we want retries to reuse the original result and avoid another model call after successful persistence. Decision: raw and generated objects use first-write-wins semantics; claim recovery reconciles saved output. Consequence: late data does not revise a report. A future revision mechanism must preserve prior evidence and approval history.

## ADR 09: What a deployed production environment would require

Context: static hosting and workload deployment need different permissions and release cycles. Proposed approach: a real deployment should use a dedicated AWS workload account, deployment role and remote state, separate from the Pages workflow. Infrastructure changes should require a reviewed plan and approval. These controls have not been exercised in this showcase. The intended benefit is to release frontend updates independently of backend changes.

## ADR 10: Static portfolio demo

Context: readers should be able to explore the interface without credentials, infrastructure or model cost. Decision: replace data access with a closed map of local JSON files, calculate relative timestamps at load and allow writes only in module memory. Consequence: operational behavior is illustrated, not exercised against AWS. Rules can be created/deleted in the demo; deployed host rules are permanent and protect their identity. A fixed sample PDF retains its fictional capture date while on-screen dates move.
