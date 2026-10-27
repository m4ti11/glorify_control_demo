resource "aws_secretsmanager_secret" "metrics_ingest_token" {
  name                    = local.ingest_metrics_secret_name
  recovery_window_in_days = 0
}

# Terraform manages only the secret metadata. The owner provisions the value
# outside Terraform so the API key never enters Terraform state.
resource "aws_secretsmanager_secret" "openai_api_key" {
  name                    = "${local.name_prefix}/openai-api-key"
  recovery_window_in_days = 7
}

# The secret value is deliberately not managed by Terraform. Putting it in a
# Terraform variable or tfvars file would store it in terraform.tfstate.
