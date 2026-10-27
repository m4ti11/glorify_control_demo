resource "aws_apigatewayv2_api" "metrics" {
  name          = "${local.name_prefix}-metrics-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = distinct(concat(var.dashboard_allowed_origins, [local.dashboard_origin]))
    allow_methods = ["GET", "POST", "PUT", "DELETE"]
    allow_headers = ["Authorization", "Content-Type"]
    max_age       = 300
  }
}

resource "aws_apigatewayv2_integration" "ingest_metrics" {
  api_id                 = aws_apigatewayv2_api.metrics.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ingest_metrics.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "ingest_metrics" {
  api_id             = aws_apigatewayv2_api.metrics.id
  route_key          = "POST /metrics"
  target             = "integrations/${aws_apigatewayv2_integration.ingest_metrics.id}"
  authorization_type = "CUSTOM"
  authorizer_id      = aws_apigatewayv2_authorizer.vps.id
}

resource "aws_apigatewayv2_authorizer" "vps" {
  api_id                            = aws_apigatewayv2_api.metrics.id
  name                              = "${local.name_prefix}-vps"
  authorizer_type                   = "REQUEST"
  authorizer_uri                    = aws_lambda_function.vps_authorizer.invoke_arn
  authorizer_payload_format_version = "2.0"
  enable_simple_responses           = true
  authorizer_result_ttl_in_seconds  = 0
  # No required identity sources: accept either token header, not both.
  identity_sources = []
}

resource "aws_lambda_permission" "allow_api_gateway_vps_authorizer" {
  statement_id  = "AllowApiGatewayInvokeVpsAuthorizer"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.vps_authorizer.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.metrics.execution_arn}/authorizers/${aws_apigatewayv2_authorizer.vps.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.metrics.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.metrics_api.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      requestTime    = "$context.requestTime"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      responseLength = "$context.responseLength"
      integrationErr = "$context.integrationErrorMessage"
    })
  }
}

resource "aws_lambda_permission" "allow_api_gateway_ingest_metrics" {
  statement_id  = "AllowApiGatewayInvokeIngestMetrics"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest_metrics.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.metrics.execution_arn}/*/*"
}

resource "aws_apigatewayv2_integration" "get_latest_metrics" {
  api_id                 = aws_apigatewayv2_api.metrics.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.get_latest_metrics.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "get_latest_metrics" {
  api_id               = aws_apigatewayv2_api.metrics.id
  route_key            = "GET /metrics/latest"
  target               = "integrations/${aws_apigatewayv2_integration.get_latest_metrics.id}"
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.dashboard.id
  authorization_scopes = ["${aws_cognito_resource_server.metrics.identifier}/read"]
}

resource "aws_apigatewayv2_authorizer" "dashboard" {
  api_id           = aws_apigatewayv2_api.metrics.id
  name             = "${local.name_prefix}-dashboard"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    audience = [aws_cognito_user_pool_client.dashboard.id]
    issuer   = "https://${aws_cognito_user_pool.dashboard.endpoint}"
  }
}

resource "aws_lambda_permission" "allow_api_gateway_get_latest_metrics" {
  statement_id  = "AllowApiGatewayInvokeGetLatestMetrics"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.get_latest_metrics.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.metrics.execution_arn}/*/GET/metrics/latest"
}

resource "aws_apigatewayv2_integration" "events" {
  api_id              = aws_apigatewayv2_api.metrics.id
  integration_type    = "AWS_PROXY"
  integration_subtype = "SQS-SendMessage"
  credentials_arn     = aws_iam_role.events_gateway.arn

  request_parameters = {
    QueueUrl    = aws_sqs_queue.events.url
    MessageBody = "$request.body"
  }

  response_parameters {
    status_code = 200
    mappings = {
      "overwrite:statuscode" = "202"
    }
  }

  depends_on = [aws_iam_role_policy.events_gateway]
}

resource "aws_apigatewayv2_route" "events" {
  api_id             = aws_apigatewayv2_api.metrics.id
  route_key          = "POST /events"
  target             = "integrations/${aws_apigatewayv2_integration.events.id}"
  authorization_type = "CUSTOM"
  authorizer_id      = aws_apigatewayv2_authorizer.vps.id
}

resource "aws_apigatewayv2_integration" "get_events" {
  api_id                 = aws_apigatewayv2_api.metrics.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.get_events.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "get_events" {
  api_id               = aws_apigatewayv2_api.metrics.id
  route_key            = "GET /events"
  target               = "integrations/${aws_apigatewayv2_integration.get_events.id}"
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.dashboard.id
  authorization_scopes = ["${aws_cognito_resource_server.metrics.identifier}/read"]
}

resource "aws_lambda_permission" "allow_api_gateway_get_events" {
  statement_id  = "AllowApiGatewayInvokeGetEvents"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.get_events.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.metrics.execution_arn}/*/GET/events"
}

resource "aws_apigatewayv2_integration" "ingest_pg_statements" {
  api_id                 = aws_apigatewayv2_api.metrics.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ingest_pg_statements.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "ingest_pg_statements" {
  api_id             = aws_apigatewayv2_api.metrics.id
  route_key          = "POST /pg-statements"
  target             = "integrations/${aws_apigatewayv2_integration.ingest_pg_statements.id}"
  authorization_type = "CUSTOM"
  authorizer_id      = aws_apigatewayv2_authorizer.vps.id
}

resource "aws_lambda_permission" "allow_api_gateway_ingest_pg_statements" {
  statement_id  = "AllowApiGatewayIngestPgStatements"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest_pg_statements.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.metrics.execution_arn}/*/POST/pg-statements"
}

resource "aws_apigatewayv2_integration" "get_pg_statements" {
  api_id                 = aws_apigatewayv2_api.metrics.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.get_pg_statements.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "get_pg_statements" {
  api_id               = aws_apigatewayv2_api.metrics.id
  route_key            = "GET /pg-statements"
  target               = "integrations/${aws_apigatewayv2_integration.get_pg_statements.id}"
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.dashboard.id
  authorization_scopes = ["${aws_cognito_resource_server.metrics.identifier}/read"]
}

resource "aws_lambda_permission" "allow_api_gateway_get_pg_statements" {
  statement_id  = "AllowApiGatewayGetPgStatements"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.get_pg_statements.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.metrics.execution_arn}/*/GET/pg-statements"
}

resource "aws_apigatewayv2_integration" "configuration" {
  api_id                 = aws_apigatewayv2_api.metrics.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.configuration.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "configuration" {
  for_each = toset([
    "GET /configuration/{kind}", "POST /configuration/{kind}",
    "GET /configuration/{kind}/{id}", "PUT /configuration/{kind}/{id}", "DELETE /configuration/{kind}/{id}",
    "GET /configuration/{kind}/{id}/{action}", "POST /configuration/{kind}/{id}/{action}"
  ])
  api_id               = aws_apigatewayv2_api.metrics.id
  route_key            = each.value
  target               = "integrations/${aws_apigatewayv2_integration.configuration.id}"
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.dashboard.id
  authorization_scopes = startswith(each.value, "GET ") ? ["${aws_cognito_resource_server.metrics.identifier}/read"] : ["${aws_cognito_resource_server.metrics.identifier}/configuration.write"]
}

resource "aws_lambda_permission" "allow_api_gateway_configuration" {
  statement_id  = "AllowApiGatewayConfiguration"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.configuration.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.metrics.execution_arn}/*/*/configuration/*"
}

resource "aws_apigatewayv2_integration" "get_operational_report" {
  api_id                 = aws_apigatewayv2_api.metrics.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.get_operational_report.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "get_operational_report" {
  api_id               = aws_apigatewayv2_api.metrics.id
  route_key            = "GET /operational-reports"
  target               = "integrations/${aws_apigatewayv2_integration.get_operational_report.id}"
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.dashboard.id
  authorization_scopes = ["${aws_cognito_resource_server.metrics.identifier}/read"]
}

resource "aws_lambda_permission" "allow_api_gateway_get_operational_report" {
  statement_id  = "AllowApiGatewayGetOperationalReport"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.get_operational_report.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.metrics.execution_arn}/*/GET/operational-reports"
}
