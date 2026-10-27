locals {
  events_worker_timeout            = 30
  report_system_suffixes           = var.report_system_ids
  report_source_object_arns        = [for id in local.report_system_suffixes : "${aws_s3_bucket.pg_reports.arn}/raw/pg-statements/${id}/*"]
  report_output_object_arns        = [for id in local.report_system_suffixes : "${aws_s3_bucket.pg_reports.arn}/generated/operational/${id}/*"]
  legacy_report_output_object_arns = [for id in local.report_system_suffixes : "${aws_s3_bucket.operational_reports.arn}/generated/operational/${id}/*"]
  report_source_prefixes           = [for id in local.report_system_suffixes : "raw/pg-statements/${id}/*"]
  report_output_prefixes           = [for id in local.report_system_suffixes : "generated/operational/${id}/*"]
  report_partition_keys            = [for id in local.report_system_suffixes : "SYSTEM#${id}"]
  report_schedule_minute_of_day    = (var.agent_pg_capture_hour * 60 + var.agent_pg_capture_minute + var.report_grace_period_minutes) % 1440
  report_schedule_hour             = floor(local.report_schedule_minute_of_day / 60)
  report_schedule_minute           = local.report_schedule_minute_of_day % 60
  health_monitor_system_ids        = length(var.health_monitor_system_ids) > 0 ? var.health_monitor_system_ids : var.report_system_ids

  name_prefix = "${var.project_name}-${var.environment}"

  metrics_table_name              = "${local.name_prefix}-metrics"
  health_monitor_state_table_name = "${local.name_prefix}-health-monitor-state"
  health_check_function_name      = "${local.name_prefix}-health-check"
  ingest_metrics_function_name    = "${local.name_prefix}-ingest-metrics"
  ingest_metrics_secret_name      = "${local.name_prefix}/metrics-ingest-token"
}

data "aws_partition" "current" {}

locals {
  channel_topic_prefix = "${local.name_prefix}-notification"
  sns_arn_prefix       = "arn:${data.aws_partition.current.partition}:sns:${var.aws_region}:${data.aws_caller_identity.current.account_id}:"
  channel_topic_arns   = "${local.sns_arn_prefix}${local.channel_topic_prefix}-*"
}
