resource "aws_lambda_function" "evaluate_host_alerts" {
  function_name    = "${local.name_prefix}-evaluate-host-alerts"
  description      = "Evaluates bounded host metric windows and queues durable alert events."
  role             = aws_iam_role.evaluate_host_alerts.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = var.evaluate_host_alerts_package_path
  source_code_hash = filebase64sha256(var.evaluate_host_alerts_package_path)
  memory_size      = 256
  timeout          = 60

  environment {
    variables = {
      METRICS_TABLE_NAME       = aws_dynamodb_table.metrics.name
      CONFIGURATION_TABLE_NAME = aws_dynamodb_table.configuration.name
      EVENTS_QUEUE_URL         = aws_sqs_queue.events.url
      MAX_SAMPLE_GAP_SECONDS   = tostring(var.host_alert_max_sample_gap_seconds)
      MAX_HISTORY_ITEMS        = "10000"
    }
  }
  depends_on = [aws_cloudwatch_log_group.evaluate_host_alerts, aws_iam_role_policy.evaluate_host_alerts]
}

resource "aws_iam_role" "evaluate_host_alerts" {
  name               = "${local.name_prefix}-evaluate-host-alerts-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy" "evaluate_host_alerts" {
  name = "${local.name_prefix}-evaluate-host-alerts-policy"
  role = aws_iam_role.evaluate_host_alerts.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.metrics.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:ConditionCheckItem"]
        Resource = aws_dynamodb_table.configuration.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:DescribeStream", "dynamodb:GetRecords", "dynamodb:GetShardIterator", "dynamodb:ListStreams"]
        Resource = aws_dynamodb_table.metrics.stream_arn
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.events.arn
      },
      {
        Effect    = "Allow"
        Action    = ["s3:PutObject", "s3:ListBucket"]
        Resource  = [aws_s3_bucket.host_alert_failures.arn, "${aws_s3_bucket.host_alert_failures.arn}/*"]
        Condition = { StringEquals = { "s3:ResourceAccount" = data.aws_caller_identity.current.account_id } }
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.evaluate_host_alerts.arn}:*"
      }
    ]
  })
}

resource "aws_lambda_event_source_mapping" "host_alerts" {
  event_source_arn               = aws_dynamodb_table.metrics.stream_arn
  function_name                  = aws_lambda_function.evaluate_host_alerts.arn
  starting_position              = "TRIM_HORIZON"
  batch_size                     = 10
  parallelization_factor         = 1
  maximum_retry_attempts         = 10
  maximum_record_age_in_seconds  = 3600
  bisect_batch_on_function_error = true
  function_response_types        = ["ReportBatchItemFailures"]

  filter_criteria {
    filter {
      pattern = jsonencode({
        eventName = ["INSERT"]
        dynamodb = { NewImage = {
          record_type   = { S = ["metric"] }
          resource_type = { S = ["HOST"] }
          resource_id   = { S = ["host"] }
        } }
      })
    }
  }
  destination_config {
    on_failure {
      destination_arn = aws_s3_bucket.host_alert_failures.arn
    }
  }
  depends_on = [aws_iam_role_policy.evaluate_host_alerts, aws_s3_bucket_policy.host_alert_failures]
}

# S3 retains the actual failed stream payload, rather than only failure metadata.
resource "aws_s3_bucket" "host_alert_failures" {
  bucket        = "${local.name_prefix}-host-alert-failures-${data.aws_caller_identity.current.account_id}"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "host_alert_failures" {
  bucket                  = aws_s3_bucket.host_alert_failures.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "host_alert_failures" {
  bucket = aws_s3_bucket.host_alert_failures.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "host_alert_failures" {
  bucket = aws_s3_bucket.host_alert_failures.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "host_alert_failures" {
  bucket = aws_s3_bucket.host_alert_failures.id
  rule {
    id     = "expire-failed-evaluations"
    status = "Enabled"
    filter {}
    expiration {
      days = 30
    }
  }
}

resource "aws_s3_bucket_policy" "host_alert_failures" {
  bucket = aws_s3_bucket.host_alert_failures.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "RequireTLS"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.host_alert_failures.arn, "${aws_s3_bucket.host_alert_failures.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

resource "aws_cloudwatch_log_group" "evaluate_host_alerts" {
  name              = "/aws/lambda/${local.name_prefix}-evaluate-host-alerts"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_metric_filter" "host_alert_failures" {
  name           = "host-alert-evaluation-failures"
  log_group_name = aws_cloudwatch_log_group.evaluate_host_alerts.name
  pattern        = "\"Host evaluation failed\""
  metric_transformation {
    name      = "EvaluationFailures"
    namespace = "${local.name_prefix}/HostAlerts"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "host_alert_failures" {
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  ok_actions          = [aws_sns_topic.ops_alerts.arn]
  alarm_name          = "${local.name_prefix}-host-alert-evaluation-failures"
  alarm_description   = "Inspect evaluator logs and retained failed batches in S3; replay after fixing the cause."
  namespace           = "${local.name_prefix}/HostAlerts"
  metric_name         = "EvaluationFailures"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "host_alert_stream_age" {
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  ok_actions          = [aws_sns_topic.ops_alerts.arn]
  alarm_name          = "${local.name_prefix}-host-alert-stream-backlog"
  namespace           = "AWS/Lambda"
  metric_name         = "IteratorAge"
  dimensions          = { FunctionName = aws_lambda_function.evaluate_host_alerts.function_name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 600
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "host_alert_destination_failures" {
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  ok_actions          = [aws_sns_topic.ops_alerts.arn]
  alarm_name          = "${local.name_prefix}-host-alert-destination-failures"
  namespace           = "AWS/Lambda"
  metric_name         = "DestinationDeliveryFailures"
  dimensions          = { FunctionName = aws_lambda_function.evaluate_host_alerts.function_name }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}
