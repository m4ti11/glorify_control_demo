data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "ingest_metrics" {
  name               = "${local.ingest_metrics_function_name}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "ingest_metrics" {
  statement {
    sid    = "WriteMetrics"
    effect = "Allow"
    actions = [
      "dynamodb:TransactWriteItems",
      "dynamodb:PutItem",
    ]
    resources = [aws_dynamodb_table.metrics.arn]
  }

  statement {
    sid    = "WriteApplicationLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.ingest_metrics.arn}:*"]
  }
}

resource "aws_iam_role_policy" "ingest_metrics" {
  name   = "${local.ingest_metrics_function_name}-policy"
  role   = aws_iam_role.ingest_metrics.id
  policy = data.aws_iam_policy_document.ingest_metrics.json
}

resource "aws_iam_role" "get_latest_metrics" {
  name               = "${local.name_prefix}-get-latest-metrics-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "get_latest_metrics" {
  statement {
    sid       = "ReadLatestMetrics"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.metrics.arn]
  }

  statement {
    sid       = "WriteApplicationLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.get_latest_metrics.arn}:*"]
  }
}

resource "aws_iam_role_policy" "get_latest_metrics" {
  name   = "${local.name_prefix}-get-latest-metrics-policy"
  role   = aws_iam_role.get_latest_metrics.id
  policy = data.aws_iam_policy_document.get_latest_metrics.json
}

resource "aws_iam_role" "vps_authorizer" {
  name               = "${local.name_prefix}-vps-authorizer-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "vps_authorizer" {
  statement {
    sid       = "ReadVpsToken"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.metrics_ingest_token.arn]
  }

  statement {
    sid       = "WriteApplicationLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.vps_authorizer.arn}:*"]
  }
}

resource "aws_iam_role_policy" "vps_authorizer" {
  name   = "${local.name_prefix}-vps-authorizer-policy"
  role   = aws_iam_role.vps_authorizer.id
  policy = data.aws_iam_policy_document.vps_authorizer.json
}

resource "aws_iam_role" "events_gateway" {
  name = "${local.name_prefix}-events-gateway-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "apigateway.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "events_gateway" {
  name = "${local.name_prefix}-events-gateway-policy"
  role = aws_iam_role.events_gateway.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:SendMessage"]
      Resource = aws_sqs_queue.events.arn
    }]
  })
}

resource "aws_iam_role" "process_events" {
  name               = "${local.name_prefix}-process-events-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy" "process_events" {
  name = "${local.name_prefix}-process-events-policy"
  role = aws_iam_role.process_events.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.events.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.events.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:Query"]
        Resource = aws_dynamodb_table.configuration.arn
      },
      {
        Effect   = "Allow"
        Action   = ["sns:Publish", "sns:ListSubscriptionsByTopic"]
        Resource = local.channel_topic_arns
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.process_events.arn}:*"
      }
    ]
  })
}

resource "aws_iam_role" "get_events" {
  name               = "${local.name_prefix}-get-events-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy" "get_events" {
  name = "${local.name_prefix}-get-events-policy"
  role = aws_iam_role.get_events.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:Query"]
        Resource = "${aws_dynamodb_table.events.arn}/index/events-by-date"
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:BatchGetItem"]
        Resource = aws_dynamodb_table.events.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.get_events.arn}:*"
      }
    ]
  })
}

resource "aws_iam_role" "ingest_pg_statements" {
  name               = "${local.name_prefix}-ingest-pg-statements-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy" "ingest_pg_statements" {
  name = "${local.name_prefix}-ingest-pg-statements-policy"
  role = aws_iam_role.ingest_pg_statements.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.pg_reports.arn}/raw/pg-statements/*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.ingest_pg_statements.arn}:*"
      }
    ]
  })
}

resource "aws_iam_role" "get_pg_statements" {
  name               = "${local.name_prefix}-get-pg-statements-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy" "get_pg_statements" {
  name = "${local.name_prefix}-get-pg-statements-policy"
  role = aws_iam_role.get_pg_statements.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.pg_reports.arn}/raw/pg-statements/*"
      },
      {
        # S3 needs ListBucket permission to distinguish a missing key (404) from
        # access denied (403). The endpoint does not list or search the bucket.
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.pg_reports.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.get_pg_statements.arn}:*"
      }
    ]
  })
}

resource "aws_iam_role" "generate_report" {
  name               = "${local.name_prefix}-generate-report-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy" "generate_report" {
  name = "${local.name_prefix}-generate-report-policy"
  role = aws_iam_role.generate_report.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["dynamodb:Query"], Resource = [aws_dynamodb_table.metrics.arn, aws_dynamodb_table.events.arn],
      Condition = { "ForAllValues:StringEquals" = { "dynamodb:LeadingKeys" = local.report_partition_keys } } },
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:UpdateItem"], Resource = aws_dynamodb_table.report_jobs.arn,
      Condition = { "ForAllValues:StringEquals" = { "dynamodb:LeadingKeys" = local.report_partition_keys } } },
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = local.report_source_object_arns },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = local.report_output_object_arns },
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = local.legacy_report_output_object_arns },
      { Effect = "Allow", Action = ["dynamodb:Query"], Resource = aws_dynamodb_table.configuration.arn,
      Condition = { "ForAllValues:StringEquals" = { "dynamodb:LeadingKeys" = ["CONFIG#DEFAULT"] } } },
      { Effect = "Allow", Action = ["sns:ListSubscriptionsByTopic"], Resource = local.channel_topic_arns },
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.pg_reports.arn,
      Condition = { StringLike = { "s3:prefix" = concat(local.report_source_prefixes, local.report_output_prefixes) } } },
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.operational_reports.arn,
      Condition = { StringLike = { "s3:prefix" = local.report_output_prefixes } } },
      { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = aws_secretsmanager_secret.openai_api_key.arn },
      { Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"], Resource = aws_sqs_queue.report_recovery.arn },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.generate_report.arn}:*" },
    ]
  })
}

resource "aws_iam_role" "get_operational_report" {
  name               = "${local.name_prefix}-get-operational-report-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy" "get_operational_report" {
  name = "${local.name_prefix}-get-operational-report-policy"
  role = aws_iam_role.get_operational_report.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = concat(local.report_output_object_arns, local.legacy_report_output_object_arns) },
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.pg_reports.arn,
      Condition = { StringLike = { "s3:prefix" = local.report_output_prefixes } } },
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.operational_reports.arn,
      Condition = { StringLike = { "s3:prefix" = local.report_output_prefixes } } },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.get_operational_report.arn}:*" },
    ]
  })
}


resource "aws_iam_role" "report_scheduler" {
  name = "${local.name_prefix}-report-scheduler-role"


  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "scheduler.amazonaws.com"
      }
      Action = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.current.account_id
        }
        ArnLike = {
          "aws:SourceArn" = "arn:${data.aws_partition.current.partition}:scheduler:${var.aws_region}:${data.aws_caller_identity.current.account_id}:schedule-group/default"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "report_scheduler" {
  name = "${local.name_prefix}-report-scheduler-policy"
  role = aws_iam_role.report_scheduler.id
  policy = jsonencode({

    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = aws_sqs_queue.report_recovery.arn },
      { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = aws_sqs_queue.report_scheduler_dlq.arn },
    ]
  })
}

resource "aws_iam_role" "configuration" {
  name               = "${local.name_prefix}-configuration-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role" "sync_notification_channels" {
  name               = "${local.name_prefix}-sync-notification-channels-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy" "configuration" {
  name = "${local.name_prefix}-configuration-policy"
  role = aws_iam_role.configuration.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"]
        Resource = aws_dynamodb_table.configuration.arn
      },
      {
        Effect   = "Allow"
        Action   = ["sns:ListSubscriptionsByTopic"]
        Resource = local.channel_topic_arns
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.configuration.arn}:*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "sync_notification_channels" {
  name = "${local.name_prefix}-sync-notification-channels-policy"
  role = aws_iam_role.sync_notification_channels.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.configuration.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:DescribeStream", "dynamodb:GetRecords", "dynamodb:GetShardIterator", "dynamodb:ListStreams"]
        Resource = aws_dynamodb_table.configuration.stream_arn
      },
      {
        Effect   = "Allow"
        Action   = ["sns:CreateTopic", "sns:DeleteTopic", "sns:Subscribe", "sns:ListSubscriptionsByTopic"]
        Resource = local.channel_topic_arns
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.configuration_sync_failures.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.sync_notification_channels.arn}:*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "report_email" {
  count = var.report_email_enabled ? 1 : 0
  role  = aws_iam_role.generate_report.id
  lifecycle {
    precondition {
      condition     = var.report_email_sender != ""
      error_message = "Configure and verify report_email_sender in SES before enabling report emails."
    }
  }
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = ["ses:SendRawEmail"]
      Resource  = "arn:${data.aws_partition.current.partition}:ses:${var.aws_region}:${data.aws_caller_identity.current.account_id}:identity/*"
      Condition = { StringEquals = { "ses:FromAddress" = var.report_email_sender } }
    }]
  })
}
