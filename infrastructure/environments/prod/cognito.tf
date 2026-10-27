resource "aws_cognito_user_pool" "dashboard" {
  name                     = "${local.name_prefix}-dashboard"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  user_pool_tier           = "ESSENTIALS"

  lambda_config {
    pre_token_generation_config {
      lambda_arn     = aws_lambda_function.dashboard_token_scopes.arn
      lambda_version = "V2_0"
    }
  }

  username_configuration {
    case_sensitive = false
  }

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

resource "aws_cognito_resource_server" "metrics" {
  identifier   = "${local.name_prefix}-metrics"
  name         = "Glorify metrics API"
  user_pool_id = aws_cognito_user_pool.dashboard.id

  scope {
    scope_name        = "configuration.write"
    scope_description = "Edit dashboard configuration (configuration-admins group also required)"
  }

  scope {
    scope_name        = "read"
    scope_description = "Read system metrics, events and PostgreSQL reports"
  }
}

resource "aws_cognito_user_group" "configuration_admins" {
  name         = "configuration-admins"
  user_pool_id = aws_cognito_user_pool.dashboard.id
  description  = "May edit dashboard configuration with configuration.write scope."
}

resource "aws_cognito_user_pool_client" "dashboard" {
  name                                 = "${local.name_prefix}-dashboard"
  user_pool_id                         = aws_cognito_user_pool.dashboard.id
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "${aws_cognito_resource_server.metrics.identifier}/read", "${aws_cognito_resource_server.metrics.identifier}/configuration.write"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = distinct(concat(var.dashboard_callback_urls, [local.dashboard_url]))
  logout_urls                          = distinct(concat(var.dashboard_logout_urls, [local.dashboard_url]))
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  explicit_auth_flows                  = ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_CUSTOM_AUTH"]
  access_token_validity                = 60
  id_token_validity                    = 60
  refresh_token_validity               = 1

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

resource "aws_cognito_user_pool_domain" "dashboard" {
  domain       = "${local.name_prefix}-${replace(lower(aws_cognito_user_pool.dashboard.id), "_", "-")}"
  user_pool_id = aws_cognito_user_pool.dashboard.id
}

# Keep Hosted UI classic branding scoped to this dashboard app client.
resource "aws_cognito_user_pool_ui_customization" "dashboard" {
  user_pool_id = aws_cognito_user_pool.dashboard.id
  client_id    = aws_cognito_user_pool_client.dashboard.id
  css          = file("${path.module}/branding/hosted-ui.css")
  image_file   = filebase64("${path.module}/branding/glorify-control.png")

  depends_on = [aws_cognito_user_pool_domain.dashboard]
}
