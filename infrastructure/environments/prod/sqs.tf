resource "aws_sqs_queue" "events_dlq" {
  name                      = "${local.name_prefix}-events-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "events" {
  name                       = "${local.name_prefix}-events"
  visibility_timeout_seconds = local.events_worker_timeout * 6
  message_retention_seconds  = 345600
  max_message_size           = 245760
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.events_dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "events_dlq" {
  queue_url = aws_sqs_queue.events_dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.events.arn]
  })
}

resource "aws_lambda_event_source_mapping" "events" {
  event_source_arn        = aws_sqs_queue.events.arn
  function_name           = aws_lambda_function.process_events.arn
  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"]
  enabled                 = true

  depends_on = [aws_iam_role_policy.process_events]
}

resource "aws_sqs_queue" "configuration_sync_failures" {
  name                      = "${local.name_prefix}-configuration-sync-failures"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_lambda_event_source_mapping" "configuration_sync" {
  event_source_arn              = aws_dynamodb_table.configuration.stream_arn
  function_name                 = aws_lambda_function.sync_notification_channels.arn
  starting_position             = "TRIM_HORIZON"
  batch_size                    = 1
  maximum_retry_attempts        = 3
  maximum_record_age_in_seconds = 3600
  function_response_types       = ["ReportBatchItemFailures"]

  destination_config {
    on_failure {
      destination_arn = aws_sqs_queue.configuration_sync_failures.arn
    }
  }
  depends_on = [aws_iam_role_policy.sync_notification_channels]
}
