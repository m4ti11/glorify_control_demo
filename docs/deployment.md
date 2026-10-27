# Deployment notes

This is a description of a possible separate installation. Nothing here has been applied. Do not deploy the static demo expecting live data; its data layer always reads local files.

1. Choose a dedicated workload account, an operator/deployment role and `us-east-1` or another reviewed region. The account `123456789012` is fictitious. Do not use account IDs or state from another installation.
2. Inspect bootstrap IAM and bucket policies. Replace its placeholder account and example bucket with owned values. Bootstrap with local state only for the initial setup; keep that state private. Define separate workload/bootstrap state keys and enable versioning, encryption, TLS and locking. Uncomment/review the S3 backend examples only after the bucket exists.
3. Build Lambda archives with `python scripts/prepare_lambda.py --packages-only`. It uses pinned wheels for Python 3.12/x86_64; archives are generated locally and never committed. The script does not write tfvars in this showcase.
4. Create private local Terraform variables from `infrastructure/environments/prod/terraform.tfvars.example`. Set the approved model ID, an owned operations mailbox, one system ID, callback/logout URLs and allowed origins. Keep secrets out of tfvars.
5. Run `terraform fmt -check -recursive` and `python scripts/validate_terraform.py`. The valid Terraform sequence is `init -backend=false` followed by `validate`; `validate` itself has no `-backend=false` option. Validation uses temporary dummy archives and cannot prove packages are deployable.
6. Under the selected role, initialize the reviewed backend, inspect a saved plan, and require manual approval before apply. CI here does not execute these steps. Check IAM wildcards, service trusts, cross-service source conditions, S3 prefixes and deletion settings.
7. Provision the ingest token and OpenAI key into the created Secrets Manager entries outside Terraform. Create Cognito users/groups and confirm SNS recipients. Verify SES identities before enabling SES event/PDF delivery. Check the operations subscription before relying on alarms.
8. Review SQL contents and authorize any OpenAI processing before enabling report jobs. Set exactly one monitored/reported system for this design. Install the agent using its deploy helper; preserve the SQLite spool and restrict configuration permissions.
9. A live authenticated frontend requires restoring/reimplementing the production login and data adapter in a separate deployment project. It is intentionally not available in this repository. The retained CloudFront resources show the hosting design; publishing this `frontend/` there would still publish a mock demo.
10. Exercise ingestion, duplicate replay, expired lease recovery, missing-heartbeat notification, DLQ redrive, backups and restoration using invented data first. Record results and RPO/RTO before treating the installation as operational.

For the portfolio only: push `main`, choose GitHub Actions as the Pages source, and enable Actions. The Pages workflow publishes `frontend/` after a successful push CI run or a manual dispatch. No AWS keys are required.
