# AWS account structure

This repository is not attached to an AWS account. Every literal account identifier is the fictional `123456789012`; `us-east-1`, `example.com`, `demo-vps-01` and `admin@example.com` are examples.

We keep state bootstrap separate from application resources. Bootstrap defines a private, versioned, encrypted state bucket and a policy scoped to the bootstrap/application keys and their lockfiles. Both `backend.tf` files are commented examples; no remote state backend is enabled.

The application resources live in `infrastructure/environments/prod`. A real deployment should use a dedicated workload account, separate remote state and a deployment role. Operators should authenticate through an organizational identity provider, review saved plans and approve changes before apply. These are proposed deployment controls; we have not operated or verified them in this showcase. The Pages workflow publishes only the static frontend.

Lambda roles trust the Lambda service and use resource-specific permissions where practical. API Gateway has separate integration permissions; Scheduler has invoke/enqueue roles. Cognito issues dashboard identities; the VPS credential is a different trust boundary and is not a Cognito user.

The example workload region is `us-east-1`. CloudFront viewer certificates and Route 53 alarm integrations have region constraints reflected by the provider alias. Before deployment, decide each region explicitly and check state location independently from workload location. Public AWS service principals and provider registry URLs are necessary code references, not private endpoints.
