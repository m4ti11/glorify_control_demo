resource "aws_cloudwatch_log_group" "ingest_metrics" {
  name              = "/aws/lambda/${local.ingest_metrics_function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "metrics_api" {
  name              = "/aws/apigateway/${local.name_prefix}-metrics"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "get_latest_metrics" {
  name              = "/aws/lambda/${local.name_prefix}-get-latest-metrics"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "vps_authorizer" {
  name              = "/aws/lambda/${local.name_prefix}-vps-authorizer"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "process_events" {
  name              = "/aws/lambda/${local.name_prefix}-process-events"
  retention_in_days = 14
}

resource "aws_cloudwatch_metric_alarm" "events_dlq" {
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  ok_actions          = [aws_sns_topic.ops_alerts.arn]
  alarm_name          = "${local.name_prefix}-events-dlq-not-empty"
  alarm_description   = "Failed events require inspection and manual redrive after correcting the cause."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { QueueName = aws_sqs_queue.events_dlq.name }
}

resource "aws_cloudwatch_metric_alarm" "events_age" {
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  ok_actions          = [aws_sns_topic.ops_alerts.arn]
  alarm_name          = "${local.name_prefix}-events-backlog"
  alarm_description   = "Events have waited more than ten minutes for processing."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 600
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { QueueName = aws_sqs_queue.events.name }
}

resource "aws_cloudwatch_log_group" "get_events" {
  name              = "/aws/lambda/${local.name_prefix}-get-events"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "ingest_pg_statements" {
  name              = "/aws/lambda/${local.name_prefix}-ingest-pg-statements"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "get_pg_statements" {
  name              = "/aws/lambda/${local.name_prefix}-get-pg-statements"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "generate_report" {
  name              = "/aws/lambda/${local.name_prefix}-generate-report"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "get_operational_report" {
  name              = "/aws/lambda/${local.name_prefix}-get-operational-report"
  retention_in_days = 14
}

resource "aws_cloudwatch_metric_alarm" "report_generation_errors" {
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  ok_actions          = [aws_sns_topic.ops_alerts.arn]
  alarm_name          = "${local.name_prefix}-report-generation-errors"
  alarm_description   = "A scheduled operational report generation invocation failed."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.generate_report.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "report_recovery_dlq" {
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  ok_actions          = [aws_sns_topic.ops_alerts.arn]
  alarm_name          = "${local.name_prefix}-report-recovery-dlq-not-empty"
  alarm_description   = "Operational report jobs exhausted SQS retries and require investigation."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { QueueName = aws_sqs_queue.report_recovery_dlq.name }
}

resource "aws_cloudwatch_metric_alarm" "report_enqueue_dlq" {
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  ok_actions          = [aws_sns_topic.ops_alerts.arn]
  alarm_name          = "${local.name_prefix}-report-enqueue-dlq-not-empty"
  alarm_description   = "Scheduler enqueue invocation failures exhausted Lambda asynchronous retries."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { QueueName = aws_sqs_queue.report_enqueue_dlq.name }
}

resource "aws_cloudwatch_metric_alarm" "report_scheduler_dlq" {
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  ok_actions          = [aws_sns_topic.ops_alerts.arn]
  alarm_name          = "${local.name_prefix}-report-scheduler-dlq-not-empty"
  alarm_description   = "EventBridge Scheduler exhausted target delivery retries for an operational report."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { QueueName = aws_sqs_queue.report_scheduler_dlq.name }
}

resource "aws_cloudwatch_metric_alarm" "report_queue_age" {
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  ok_actions          = [aws_sns_topic.ops_alerts.arn]
  alarm_name          = "${local.name_prefix}-report-recovery-backlog"
  alarm_description   = "Operational report recovery jobs are waiting longer than two hours."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = aws_sqs_queue.report_recovery.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 7200
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_log_group" "configuration" {
  name              = "/aws/lambda/${local.name_prefix}-configuration"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "sync_notification_channels" {
  name              = "/aws/lambda/${local.name_prefix}-sync-notification-channels"
  retention_in_days = 14
}

resource "aws_cloudwatch_metric_alarm" "configuration_sync_failures" {
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  ok_actions          = [aws_sns_topic.ops_alerts.arn]
  alarm_name          = "${local.name_prefix}-configuration-sync-failures"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { QueueName = aws_sqs_queue.configuration_sync_failures.name }
}

# Operational notifications are independent of application notification channels.
resource "aws_sns_topic" "ops_alerts" {
  name = "${local.name_prefix}-ops-alerts"
}

resource "aws_sns_topic_subscription" "ops_alerts_email" {
  topic_arn = aws_sns_topic.ops_alerts.arn
  protocol  = "email"
  endpoint  = var.ops_alert_email
}

resource "aws_sns_topic_policy" "ops_alerts" {
  arn = aws_sns_topic.ops_alerts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowOwnedCloudWatchAlarms"
      Effect    = "Allow"
      Principal = { Service = "cloudwatch.amazonaws.com" }
      Action    = "sns:Publish"
      Resource  = aws_sns_topic.ops_alerts.arn
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        ArnLike      = { "aws:SourceArn" = "arn:${data.aws_partition.current.partition}:cloudwatch:${var.aws_region}:${data.aws_caller_identity.current.account_id}:alarm:${local.name_prefix}-*" }
      }
    }]
  })
}

resource "aws_cloudwatch_log_metric_filter" "heartbeats" {
  name           = "${local.name_prefix}-persisted-heartbeats"
  log_group_name = aws_cloudwatch_log_group.ingest_metrics.name
  pattern        = "\"metrics.persisted\" \"created=True\""
  metric_transformation {
    name          = "PersistedHeartbeats"
    namespace     = "${local.name_prefix}/Monitor"
    value         = "1"
    default_value = 0
  }
}

resource "aws_cloudwatch_metric_alarm" "heartbeats_missing" {
  alarm_name          = "${local.name_prefix}-heartbeats-missing"
  alarm_description   = "No newly persisted heartbeats for two five-minute periods. Independent of health_check."
  namespace           = "${local.name_prefix}/Monitor"
  metric_name         = "PersistedHeartbeats"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  ok_actions          = [aws_sns_topic.ops_alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "pipeline_lambda_errors" {
  for_each = {
    ingest_metrics             = aws_lambda_function.ingest_metrics.function_name
    get_latest_metrics         = aws_lambda_function.get_latest_metrics.function_name
    vps_authorizer             = aws_lambda_function.vps_authorizer.function_name
    process_events             = aws_lambda_function.process_events.function_name
    get_events                 = aws_lambda_function.get_events.function_name
    ingest_pg_statements       = aws_lambda_function.ingest_pg_statements.function_name
    get_pg_statements          = aws_lambda_function.get_pg_statements.function_name
    configuration              = aws_lambda_function.configuration.function_name
    sync_notification_channels = aws_lambda_function.sync_notification_channels.function_name
    evaluate_host_alerts       = aws_lambda_function.evaluate_host_alerts.function_name
    dashboard_token_scopes     = aws_lambda_function.dashboard_token_scopes.function_name
    get_operational_report     = aws_lambda_function.get_operational_report.function_name
  }
  alarm_name          = "${local.name_prefix}-${replace(each.key, "_", "-")}-lambda-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  ok_actions          = [aws_sns_topic.ops_alerts.arn]
}
