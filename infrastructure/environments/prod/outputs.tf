output "metrics_endpoint_url" {
  description = "HTTPS endpoint used by Glorify Agents to submit heartbeats."
  value       = "${aws_apigatewayv2_api.metrics.api_endpoint}/metrics"
}

output "latest_metrics_endpoint_url" {
  description = "GET endpoint for the latest snapshot; no system_id is required."
  value       = "${aws_apigatewayv2_api.metrics.api_endpoint}/metrics/latest"
}

output "vps_authorizer_id" {
  description = "Reusable CUSTOM authorizer ID for future routes called by the VPS."
  value       = aws_apigatewayv2_authorizer.vps.id
}

output "metrics_table_name" {
  description = "DynamoDB table receiving historical resource metric records."
  value       = aws_dynamodb_table.metrics.name
}

output "ingest_metrics_function_name" {
  description = "Name of the metrics ingestion Lambda function."
  value       = aws_lambda_function.ingest_metrics.function_name
}

output "metrics_ingest_secret_arn" {
  description = "Secrets Manager ARN where the agent token must be stored."
  value       = aws_secretsmanager_secret.metrics_ingest_token.arn
}

output "cognito_user_pool_id" {
  description = "User pool where dashboard users are created by administrators."
  value       = aws_cognito_user_pool.dashboard.id
}

output "dashboard_config" {
  description = "Public browser configuration. Generate frontend/config.js after apply."
  value = {
    cognitoDomain             = "https://${aws_cognito_user_pool_domain.dashboard.domain}.auth.${var.aws_region}.amazoncognito.com"
    clientId                  = aws_cognito_user_pool_client.dashboard.id
    userPoolId                = aws_cognito_user_pool.dashboard.id
    nativeLoginEnabled        = try(contains(aws_cognito_user_pool_client.dashboard.explicit_auth_flows, "ALLOW_USER_PASSWORD_AUTH") && aws_cognito_user_pool.dashboard.lambda_config[0].pre_token_generation_config[0].lambda_version == "V2_0", false)
    redirectUri               = var.dashboard_callback_urls[0]
    logoutUri                 = var.dashboard_logout_urls[0]
    scopes                    = ["openid", "email", "${aws_cognito_resource_server.metrics.identifier}/read", "${aws_cognito_resource_server.metrics.identifier}/configuration.write"]
    configurationEndpoint     = "${aws_apigatewayv2_api.metrics.api_endpoint}/configuration"
    configurationAdminGroup   = aws_cognito_user_group.configuration_admins.name
    pgStatementsEndpoint      = "${aws_apigatewayv2_api.metrics.api_endpoint}/pg-statements"
    eventsEndpoint            = "${aws_apigatewayv2_api.metrics.api_endpoint}/events"
    latestMetricsEndpoint     = "${aws_apigatewayv2_api.metrics.api_endpoint}/metrics/latest"
    operationalReportEndpoint = "${aws_apigatewayv2_api.metrics.api_endpoint}/operational-reports"
  }
}

output "events_endpoint_url" {
  value       = "${aws_apigatewayv2_api.metrics.api_endpoint}/events"
  description = "Authenticated asynchronous VPS event ingestion endpoint."
}

output "events_table_name" {
  value = aws_dynamodb_table.events.name
}

output "events_queue_url" {
  value = aws_sqs_queue.events.url
}

output "events_dlq_url" {
  value = aws_sqs_queue.events_dlq.url
}

output "pg_statements_endpoint_url" {
  value       = "${aws_apigatewayv2_api.metrics.api_endpoint}/pg-statements"
  description = "POST with VPS authorizer; GET with Cognito, system_id and report_date."
}

output "pg_reports_bucket_name" {
  value = aws_s3_bucket.pg_reports.id
}

output "configuration_table_name" {
  value = aws_dynamodb_table.configuration.name
}

output "configuration_admin_group" {
  value = aws_cognito_user_group.configuration_admins.name
}
output "host_alert_evaluator_function_name" {
  value = aws_lambda_function.evaluate_host_alerts.function_name
}

output "health_check_function_name" {
  description = "Scheduled agent-liveness checker."
  value       = aws_lambda_function.health_check.function_name
}

output "host_alert_failure_bucket_name" {
  value = aws_s3_bucket.host_alert_failures.id
}

output "operational_reports_bucket_name" {
  value = aws_s3_bucket.pg_reports.id
}

output "openai_api_key_secret_arn" {
  description = "Dedicated secret metadata ARN; secret value must be provisioned outside Terraform."
  value       = aws_secretsmanager_secret.openai_api_key.arn
}

output "legacy_operational_reports_bucket_name" {
  value = aws_s3_bucket.operational_reports.id
}
output "event_push_topic_arn" {
  description = "SNS fan-out for event push adapters. A compatible mobile delivery subscriber is still required."
  value       = aws_sns_topic.event_push.arn
}

output "dashboard_url" {
  description = "Public HTTPS dashboard URL; also registered in Cognito and API CORS."
  value       = local.dashboard_url
}

output "dashboard_bucket_name" {
  description = "Private static frontend bucket. Upload application files separately."
  value       = aws_s3_bucket.dashboard.id
}

output "dashboard_distribution_id" {
  description = "CloudFront distribution ID used for frontend invalidations."
  value       = aws_cloudfront_distribution.dashboard.id
}

output "dashboard_certificate_dns_records" {
  description = "Publish these CNAME records in the external DNS provider and retain them for certificate renewal."
  value = var.dashboard_domain_name == "" ? [] : [for option in aws_acm_certificate.dashboard[0].domain_validation_options : {
    name  = option.resource_record_name
    type  = option.resource_record_type
    value = option.resource_record_value
  }]
}

output "dashboard_cloudfront_hostname" {
  description = "DNS CNAME target for the custom dashboard hostname."
  value       = aws_cloudfront_distribution.dashboard.domain_name
}

output "ops_alerts_topic_arn" {
  description = "Operations topic; its email subscription requires confirmation."
  value       = aws_sns_topic.ops_alerts.arn
}
