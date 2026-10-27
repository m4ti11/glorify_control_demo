resource "aws_dynamodb_table" "health_monitor_state" {
  name                        = local.health_monitor_state_table_name
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "system_id"
  deletion_protection_enabled = true

  attribute {
    name = "system_id"
    type = "S"
  }
}

resource "aws_sqs_queue" "health_check_scheduler_dlq" {
  name                      = "${local.name_prefix}-health-check-scheduler-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_iam_role" "health_check" {
  name               = "${local.health_check_function_name}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy" "health_check" {
  name = "${local.health_check_function_name}-policy"
  role = aws_iam_role.health_check.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem"]
        Resource = aws_dynamodb_table.metrics.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem"]
        Resource = aws_dynamodb_table.health_monitor_state.arn
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.events.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.health_check.arn}:*"
      },
    ]
  })
}

resource "aws_lambda_function" "health_check" {
  function_name                  = local.health_check_function_name
  description                    = "Checks the latest VPS metrics and queues outage or recovery events."
  role                           = aws_iam_role.health_check.arn
  runtime                        = "python3.12"
  handler                        = "handler.lambda_handler"
  filename                       = var.health_check_package_path
  source_code_hash               = filebase64sha256(var.health_check_package_path)
  memory_size                    = 128
  timeout                        = 15
  reserved_concurrent_executions = 1

  environment {
    variables = {
      METRICS_TABLE_NAME          = aws_dynamodb_table.metrics.name
      HEALTH_STATE_TABLE_NAME     = aws_dynamodb_table.health_monitor_state.name
      EVENTS_QUEUE_URL            = aws_sqs_queue.events.url
      MONITORED_SYSTEM_IDS        = jsonencode(local.health_monitor_system_ids)
      METRICS_STALE_AFTER_SECONDS = tostring(var.health_monitor_stale_after_seconds)
      STALE_CONFIRMATION_SECONDS  = tostring(var.health_monitor_confirmation_seconds)
    }
  }

  depends_on = [aws_cloudwatch_log_group.health_check, aws_iam_role_policy.health_check]
}

resource "aws_iam_role" "health_check_scheduler" {
  name = "${local.health_check_function_name}-scheduler-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        ArnLike = {
          "aws:SourceArn" = "arn:${data.aws_partition.current.partition}:scheduler:${var.aws_region}:${data.aws_caller_identity.current.account_id}:schedule-group/default"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "health_check_scheduler" {
  name = "${local.health_check_function_name}-scheduler-policy"
  role = aws_iam_role.health_check_scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = aws_lambda_function.health_check.arn },
      { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = aws_sqs_queue.health_check_scheduler_dlq.arn },
    ]
  })
}

resource "aws_scheduler_schedule" "health_check" {
  name                         = "${local.name_prefix}-health-check"
  state                        = "ENABLED"
  schedule_expression          = "rate(2 minutes)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.health_check.arn
    role_arn = aws_iam_role.health_check_scheduler.arn
    input    = "{}"

    dead_letter_config {
      arn = aws_sqs_queue.health_check_scheduler_dlq.arn
    }

    retry_policy {
      maximum_event_age_in_seconds = 300
      maximum_retry_attempts       = 3
    }
  }

  depends_on = [aws_iam_role_policy.health_check_scheduler]
}

resource "aws_cloudwatch_log_group" "health_check" {
  name              = "/aws/lambda/${local.health_check_function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_metric_alarm" "health_check_errors" {
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  ok_actions          = [aws_sns_topic.ops_alerts.arn]
  alarm_name          = "${local.name_prefix}-health-check-errors"
  alarm_description   = "The scheduled VPS health-check Lambda is failing."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.health_check.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "health_check_scheduler_dlq" {
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  ok_actions          = [aws_sns_topic.ops_alerts.arn]
  alarm_name          = "${local.name_prefix}-health-check-scheduler-dlq-not-empty"
  alarm_description   = "EventBridge Scheduler could not invoke the health-check Lambda after retries."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.health_check_scheduler_dlq.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}
