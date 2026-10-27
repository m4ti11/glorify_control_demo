resource "aws_lambda_function" "ingest_metrics" {
  function_name = local.ingest_metrics_function_name
  description   = "Receives and stores Glorify Control resource metrics."
  role          = aws_iam_role.ingest_metrics.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"

  filename         = var.ingest_metrics_package_path
  source_code_hash = filebase64sha256(var.ingest_metrics_package_path)

  memory_size = 256
  timeout     = 10

  environment {
    variables = {
      METRICS_TABLE_NAME = aws_dynamodb_table.metrics.name
      LOG_LEVEL          = "INFO"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.ingest_metrics,
    aws_iam_role_policy.ingest_metrics,
  ]
}

resource "aws_lambda_function" "get_latest_metrics" {
  function_name    = "${local.name_prefix}-get-latest-metrics"
  description      = "Reads the most recently received unexpired metrics snapshot."
  role             = aws_iam_role.get_latest_metrics.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = var.get_latest_metrics_package_path
  source_code_hash = filebase64sha256(var.get_latest_metrics_package_path)
  memory_size      = 128
  timeout          = 10

  environment {
    variables = {
      METRICS_TABLE_NAME = aws_dynamodb_table.metrics.name
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.get_latest_metrics,
    aws_iam_role_policy.get_latest_metrics,
  ]
}

resource "aws_lambda_function" "vps_authorizer" {
  function_name    = "${local.name_prefix}-vps-authorizer"
  description      = "Reusable token authorizer for VPS API routes."
  role             = aws_iam_role.vps_authorizer.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = var.vps_authorizer_package_path
  source_code_hash = filebase64sha256(var.vps_authorizer_package_path)
  memory_size      = 128
  timeout          = 10

  environment {
    variables = {
      METRICS_INGEST_SECRET_ARN = aws_secretsmanager_secret.metrics_ingest_token.arn
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.vps_authorizer,
    aws_iam_role_policy.vps_authorizer,
  ]
}

resource "aws_lambda_function" "process_events" {
  function_name    = "${local.name_prefix}-process-events"
  description      = "Idempotently stores VPS events and publishes pending SNS alerts."
  role             = aws_iam_role.process_events.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = var.process_events_package_path
  source_code_hash = filebase64sha256(var.process_events_package_path)
  memory_size      = 256
  timeout          = local.events_worker_timeout

  environment {
    variables = {
      EVENTS_TABLE_NAME        = aws_dynamodb_table.events.name
      CONFIGURATION_TABLE_NAME = aws_dynamodb_table.configuration.name
      EVENT_EMAIL_SENDER       = var.event_email_sender
      CHANNEL_TOPIC_ARN_PREFIX = "${local.sns_arn_prefix}${local.channel_topic_prefix}-"
      EVENT_PUSH_TOPIC_ARN     = aws_sns_topic.event_push.arn
    }
  }

  depends_on = [aws_cloudwatch_log_group.process_events, aws_iam_role_policy.process_events,
  aws_iam_role_policy.event_email, aws_iam_role_policy.event_push]
}

resource "aws_lambda_function" "get_events" {
  function_name    = "${local.name_prefix}-get-events"
  description      = "Queries bounded event pages by date for the authenticated dashboard."
  role             = aws_iam_role.get_events.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = var.get_events_package_path
  source_code_hash = filebase64sha256(var.get_events_package_path)
  memory_size      = 256
  timeout          = 30

  environment {
    variables = {
      EVENTS_TABLE_NAME = aws_dynamodb_table.events.name
      EVENTS_INDEX_NAME = "events-by-date"
    }
  }
  depends_on = [aws_cloudwatch_log_group.get_events, aws_iam_role_policy.get_events]
}

resource "aws_lambda_function" "ingest_pg_statements" {
  function_name    = "${local.name_prefix}-ingest-pg-statements"
  role             = aws_iam_role.ingest_pg_statements.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = var.ingest_pg_statements_package_path
  source_code_hash = filebase64sha256(var.ingest_pg_statements_package_path)
  memory_size      = 256
  timeout          = 20

  environment {
    variables = {
      PG_REPORTS_BUCKET = aws_s3_bucket.pg_reports.id
    }
  }
  depends_on = [aws_cloudwatch_log_group.ingest_pg_statements, aws_iam_role_policy.ingest_pg_statements, aws_s3_bucket_policy.pg_reports]
}

resource "aws_lambda_function" "get_pg_statements" {
  function_name    = "${local.name_prefix}-get-pg-statements"
  role             = aws_iam_role.get_pg_statements.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = var.get_pg_statements_package_path
  source_code_hash = filebase64sha256(var.get_pg_statements_package_path)
  memory_size      = 256
  timeout          = 20

  environment {
    variables = {
      PG_REPORTS_BUCKET = aws_s3_bucket.pg_reports.id
    }
  }
  depends_on = [aws_cloudwatch_log_group.get_pg_statements, aws_iam_role_policy.get_pg_statements, aws_s3_bucket_policy.pg_reports]
}

resource "aws_lambda_function" "generate_report" {
  function_name    = "${local.name_prefix}-generate-report"
  description      = "Builds one immutable, AI-assisted operational report per system and date."
  role             = aws_iam_role.generate_report.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = var.generate_report_package_path
  source_code_hash = filebase64sha256(var.generate_report_package_path)
  memory_size      = 512
  timeout          = 300

  environment {
    variables = {
      REPORT_SYSTEM_IDS               = jsonencode(var.report_system_ids)
      EXPECTED_HEARTBEAT_SECONDS      = tostring(var.report_expected_heartbeat_seconds)
      OPENAI_MODEL                    = var.openai_model
      OPENAI_SECRET_ARN               = aws_secretsmanager_secret.openai_api_key.arn
      OPENAI_TIMEOUT_SECONDS          = tostring(var.openai_timeout_seconds)
      REPORT_LEASE_SECONDS            = tostring(var.report_lease_seconds)
      REPORT_INPUT_MAX_BYTES          = tostring(var.report_input_max_bytes)
      REPORT_OUTPUT_MAX_BYTES         = tostring(var.report_output_max_bytes)
      METRICS_STALE_AFTER_SECONDS     = tostring(var.metrics_stale_after_seconds)
      EVENTS_STALE_AFTER_SECONDS      = tostring(var.events_stale_after_seconds)
      PG_STALE_AFTER_SECONDS          = tostring(var.pg_report_stale_after_seconds)
      METRICS_TABLE_NAME              = aws_dynamodb_table.metrics.name
      EVENTS_TABLE_NAME               = aws_dynamodb_table.events.name
      REPORT_JOBS_TABLE_NAME          = aws_dynamodb_table.report_jobs.name
      PG_REPORTS_BUCKET               = aws_s3_bucket.pg_reports.id
      GENERATED_REPORTS_BUCKET        = aws_s3_bucket.pg_reports.id
      LEGACY_GENERATED_REPORTS_BUCKET = aws_s3_bucket.operational_reports.id
      CONFIGURATION_TABLE_NAME        = aws_dynamodb_table.configuration.name
      REPORT_EMAIL_ENABLED            = tostring(var.report_email_enabled)
      REPORT_EMAIL_SENDER             = var.report_email_sender
      CHANNEL_TOPIC_ARN_PREFIX        = "${local.sns_arn_prefix}${local.channel_topic_prefix}-"
    }
  }
  depends_on = [aws_cloudwatch_log_group.generate_report, aws_iam_role_policy.generate_report,
  aws_s3_bucket_policy.operational_reports]
}

resource "aws_lambda_function" "get_operational_report" {
  function_name    = "${local.name_prefix}-get-operational-report"
  description      = "Reads one saved operational report for an authenticated dashboard user."
  role             = aws_iam_role.get_operational_report.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = var.get_operational_report_package_path
  source_code_hash = filebase64sha256(var.get_operational_report_package_path)
  memory_size      = 128
  timeout          = 15

  environment {
    variables = {
      GENERATED_REPORTS_BUCKET        = aws_s3_bucket.pg_reports.id
      LEGACY_GENERATED_REPORTS_BUCKET = aws_s3_bucket.operational_reports.id
      REPORT_OUTPUT_MAX_BYTES         = tostring(var.report_output_max_bytes)
      REPORT_SYSTEM_IDS               = jsonencode(var.report_system_ids)
    }
  }
  depends_on = [aws_cloudwatch_log_group.get_operational_report, aws_iam_role_policy.get_operational_report,
  aws_s3_bucket_policy.operational_reports]
}

resource "aws_lambda_function" "configuration" {
  function_name    = "${local.name_prefix}-configuration"
  role             = aws_iam_role.configuration.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = var.configuration_package_path
  source_code_hash = filebase64sha256(var.configuration_package_path)
  memory_size      = 256
  timeout          = 30

  environment {
    variables = {
      CONFIGURATION_TABLE_NAME  = aws_dynamodb_table.configuration.name
      CONFIGURATION_ADMIN_GROUP = aws_cognito_user_group.configuration_admins.name
    }
  }
  depends_on = [aws_cloudwatch_log_group.configuration, aws_iam_role_policy.configuration]
}

resource "aws_lambda_function" "sync_notification_channels" {
  function_name    = "${local.name_prefix}-sync-notification-channels"
  role             = aws_iam_role.sync_notification_channels.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = var.sync_notification_channels_package_path
  source_code_hash = filebase64sha256(var.sync_notification_channels_package_path)
  memory_size      = 256
  timeout          = 30

  environment {
    variables = {
      CONFIGURATION_TABLE_NAME = aws_dynamodb_table.configuration.name
      CHANNEL_TOPIC_PREFIX     = local.channel_topic_prefix
      SNS_ARN_PREFIX           = local.sns_arn_prefix
    }
  }
  depends_on = [aws_cloudwatch_log_group.sync_notification_channels, aws_iam_role_policy.sync_notification_channels]
}
