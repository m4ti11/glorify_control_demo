# Threat model and assumptions

We assume a single trusted VPS and a single operator group. We protect against unauthenticated ingestion and dashboard access, accidental retries, stale snapshots, concurrent state changes and accidental secret publication. The design provides no tenant isolation or security boundary against a compromised monitored host.

## Authentication in the backend design

The hosted dashboard flow uses Cognito authorization code flow with PKCE: generate a verifier and S256 challenge plus a random state; redirect to the hosted authorization endpoint; validate returned state; exchange the code with the verifier; then call API Gateway with the access JWT. API Gateway validates issuer/audience and required resource-server scopes. A read user receives the read scope; configuration writes require the write scope and the `configuration-admins` group checked again in the backend. A V2 pre-token-generation Lambda also grants appropriate scopes for native Cognito sign-in. PKCE is the hosted flow; native sign-in is a separate retained backend capability.

The login implementation is deliberately absent from this showcase. Its frontend has no Cognito SDK, session token, client configuration or HTTP API adapter. A local file reader supplies data and anyone can edit the in-memory demo.

The VPS uses a shared ingest credential checked by a custom authorizer. The credential is not bound to a system ID. A holder can claim any accepted ID; global read/configuration storage also lacks per-user ownership. One environment must be one trust unit.

## Data leaving the VPS

Hostnames, utilization, container names/state, event details and PostgreSQL statistics/query text can leave the VPS. Raw SQL is stored in S3; events and metrics enter DynamoDB. The report worker selects a bounded daily source window and may send it, including SQL and evidence references, to the configured OpenAI API. It asks for `store: false`; SQL redaction and provider retention terms must be configured for the deployment. The fictional demo does not call OpenAI.

## Boundaries and remaining risks

- Docker socket access can grant broad host control even when collector code is read-only. PostgreSQL statistics privileges can expose other users' SQL.
- TLS, private S3 access blocks, encryption at rest and conditional writes are declared in IaC. IAM still needs human review of wildcards, dynamic notification topics, caller-identity resources and report prefixes.
- Secret values stay outside Terraform. Rotation, MFA policy, browser security headers beyond existing HSTS, WAF and cost controls require an explicit deployment policy.
- Backoff and DLQs support recovery within finite retention windows. Provider acceptance does not confirm inbox delivery; a crash before checkpointing can duplicate a notification.
- Event/report retention and restore drills remain incomplete. Sensitive-data classification must precede real collection or external analysis.


## Follow-up work

The next authentication change is to bind ingest credentials to individual system IDs, so a revoked or compromised agent cannot submit another host’s data. Credential rotation would require coordinated agent rollout and a short overlap window for the old and new credentials. Per-system credentials and automated credential rotation are not implemented in the current single-host design.
