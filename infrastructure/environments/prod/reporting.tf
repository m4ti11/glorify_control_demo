resource "aws_dynamodb_table" "report_jobs" {
  name         = "${local.name_prefix}-report-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }
  attribute {
    name = "SK"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }
}

resource "aws_sqs_queue" "report_recovery_dlq" {
  name                      = "${local.name_prefix}-report-recovery-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

# Legacy failed enqueue envelopes are retained for manual recovery; no new producer.
resource "aws_sqs_queue" "report_enqueue_dlq" {
  name                      = "${local.name_prefix}-report-enqueue-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "report_scheduler_dlq" {
  name                      = "${local.name_prefix}-report-scheduler-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "report_recovery" {
  name                       = "${local.name_prefix}-report-recovery"
  visibility_timeout_seconds = var.report_recovery_visibility_seconds
  message_retention_seconds  = 1209600
  sqs_managed_sse_enabled    = true
  lifecycle {
    precondition {
      condition     = var.report_recovery_visibility_seconds >= var.report_lease_seconds + 300
      error_message = "Queue visibility must exceed the report lease by at least five minutes."
    }
  }
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.report_recovery_dlq.arn
    maxReceiveCount     = var.report_recovery_max_receive_count
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "report_recovery_dlq" {
  queue_url = aws_sqs_queue.report_recovery_dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.report_recovery.arn]
  })
}

resource "aws_lambda_event_source_mapping" "report_recovery" {
  event_source_arn        = aws_sqs_queue.report_recovery.arn
  function_name           = aws_lambda_function.generate_report.arn
  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"]
  enabled                 = true
  depends_on              = [aws_iam_role_policy.generate_report]
}

resource "aws_scheduler_schedule" "operational_report" {
  for_each = toset(var.report_system_ids)
  name     = "${local.name_prefix}-operational-report-${substr(sha256(each.key), 0, 16)}"
  state    = "ENABLED"

  lifecycle {
    precondition {
      condition     = var.agent_pg_capture_hour * 60 + var.agent_pg_capture_minute + var.report_grace_period_minutes + 60 < 1440
      error_message = "Keep the configured report time and grace period within one local date. The report date is derived from the nominal Scheduler time."
    }
  }

  schedule_expression          = "cron(${local.report_schedule_minute} ${local.report_schedule_hour} * * ? *)"
  schedule_expression_timezone = var.reporting_timezone
  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sqs_queue.report_recovery.arn
    role_arn = aws_iam_role.report_scheduler.arn
    # jsonencode escapes angle brackets. Scheduler requires the literal token
    # before JSON decoding in order to substitute the nominal execution time.
    input = replace(
      jsonencode({ system_id = each.key, scheduled_time = "<aws.scheduler.scheduled-time>" }),
      "\\u003caws.scheduler.scheduled-time\\u003e",
      "<aws.scheduler.scheduled-time>"
    )
    dead_letter_config {
      arn = aws_sqs_queue.report_scheduler_dlq.arn
    }
    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 3
    }
  }
  depends_on = [aws_iam_role_policy.report_scheduler]
}
