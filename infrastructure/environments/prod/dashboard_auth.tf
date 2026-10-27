# V2 scopes let native sign-in satisfy the same authorizer policy as hosted OAuth.
resource "aws_cloudwatch_log_group" "dashboard_token_scopes" {
  name              = "/aws/lambda/${local.name_prefix}-dashboard-token-scopes"
  retention_in_days = 14
}

resource "aws_iam_role" "dashboard_token_scopes" {
  name               = "${local.name_prefix}-dashboard-token-scopes-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy" "dashboard_token_scopes" {
  role = aws_iam_role.dashboard_token_scopes.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.dashboard_token_scopes.arn}:*"
    }]
  })
}

resource "aws_lambda_function" "dashboard_token_scopes" {
  function_name    = "${local.name_prefix}-dashboard-token-scopes"
  description      = "Adds dashboard API scopes to native Cognito access tokens."
  role             = aws_iam_role.dashboard_token_scopes.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = var.dashboard_token_scopes_package_path
  source_code_hash = filebase64sha256(var.dashboard_token_scopes_package_path)
  memory_size      = 128
  timeout          = 5

  environment {
    variables = {
      READ_SCOPE  = "${local.name_prefix}-metrics/read"
      WRITE_SCOPE = "${local.name_prefix}-metrics/configuration.write"
      ADMIN_GROUP = "configuration-admins"
    }
  }
  depends_on = [aws_iam_role_policy.dashboard_token_scopes]
}

resource "aws_lambda_permission" "cognito_token_scopes" {
  statement_id  = "AllowCognitoDashboardTokenScopes"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.dashboard_token_scopes.function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.dashboard.arn
}
