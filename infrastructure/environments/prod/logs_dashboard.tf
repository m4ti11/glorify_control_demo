locals {
  app_log_group_names = [
    aws_cloudwatch_log_group.metrics_api.name,
    aws_cloudwatch_log_group.ingest_metrics.name,
    aws_cloudwatch_log_group.get_latest_metrics.name,
    aws_cloudwatch_log_group.vps_authorizer.name,
    aws_cloudwatch_log_group.process_events.name,
    aws_cloudwatch_log_group.get_events.name,
    aws_cloudwatch_log_group.ingest_pg_statements.name,
    aws_cloudwatch_log_group.get_pg_statements.name,
    aws_cloudwatch_log_group.configuration.name,
    aws_cloudwatch_log_group.sync_notification_channels.name,
    aws_cloudwatch_log_group.evaluate_host_alerts.name,
  ]
  alert_flow_log_group_names = [
    aws_cloudwatch_log_group.ingest_metrics.name,
    aws_cloudwatch_log_group.evaluate_host_alerts.name,
    aws_cloudwatch_log_group.process_events.name,
  ]
  app_log_columns       = <<-QUERY
    fields @timestamp as hora, replace(replace(@log, '${data.aws_caller_identity.current.account_id}:/aws/lambda/${local.name_prefix}-', ''), '${data.aws_caller_identity.current.account_id}:/aws/apigateway/${local.name_prefix}-metrics', 'api-gateway') as servicio
    | parse @message /^\[(?<parsed_level>[A-Z]+)\]\s+\S+\s+\S+\s+(?<parsed_message>.*)$/
    | fields coalesce(parsed_level, level, if(status >= 500, "ERROR", if(status >= 400, "WARN", "INFO"))) as nivel, coalesce(parsed_message, message, @message) as mensaje
  QUERY
  app_logs_query        = <<-QUERY
    ${local.app_log_columns}
    | filter @message not like /^(INIT_START|INIT_REPORT|START RequestId:|END RequestId:|REPORT RequestId:)/ or @message like /ERROR|Error|Exception|Status: timeout/
    | sort @timestamp desc
    | limit 200
    | display hora, servicio, nivel, mensaje
  QUERY
  alert_flow_logs_query = <<-QUERY
    ${local.app_log_columns}
    | parse @message /event_id=(?<trace_event_id>[0-9a-f-]+)/
    | parse @message /rule_id=(?<trace_rule_id>[0-9a-f-]+)/
    | parse @message /message_id=(?<trace_message_id>[0-9a-f-]+)/
    | filter @message like /metrics\.persisted|stream\.host_|evaluation\.|rule\.|history\.|sqs\.|event\.|sns\./
    | sort @timestamp asc
    | limit 500
    | display hora, servicio, nivel, mensaje, trace_event_id, trace_rule_id, trace_message_id
  QUERY
  app_errors_query      = <<-QUERY
    ${local.app_log_columns}
    | filter @message like /ERROR|Error|Exception|exception|failed|failure/ or status >= 500
    | sort @timestamp desc
    | limit 200
    | display hora, servicio, nivel, mensaje
  QUERY
}

resource "aws_cloudwatch_query_definition" "app_logs" {
  name            = "${local.name_prefix}/app/all-services"
  log_group_names = local.app_log_group_names
  query_string    = local.app_logs_query
}

resource "aws_cloudwatch_query_definition" "alert_flow" {
  name            = "${local.name_prefix}/app/alert-flow"
  log_group_names = local.alert_flow_log_group_names
  query_string    = local.alert_flow_logs_query
}

resource "aws_cloudwatch_query_definition" "app_errors" {
  name            = "${local.name_prefix}/app/errors"
  log_group_names = local.app_log_group_names
  query_string    = local.app_errors_query
}

resource "aws_cloudwatch_dashboard" "app_logs" {
  dashboard_name = "${local.name_prefix}-app-logs"
  dashboard_body = jsonencode({
    start = "-PT30M"
    widgets = [
      {
        type = "text", x = 0, y = 0, width = 24, height = 3
        properties = {
          markdown = "# Glorify Control · App Logs\nLogs for all services and API Gateway. Each row identifies its source. Adjust the time range and refresh interval in the toolbar. Follow an alert using **alert-flow** in Logs Insights, filtered by **event_id**. Before an event exists, correlate by **system_id** and **measured_at**."
        }
      },
      {
        type = "log", x = 0, y = 3, width = 24, height = 10
        properties = {
          title  = "All services · newest first"
          region = var.aws_region
          view   = "table"
          query  = "${join(" | ", [for name in local.app_log_group_names : "SOURCE '${name}'"])} | ${local.app_logs_query}"
        }
      },
      {
        type = "log", x = 0, y = 13, width = 16, height = 10
        properties = {
          title  = "Alert flow · ingest → evaluation → SQS → SNS"
          region = var.aws_region
          view   = "table"
          query  = "${join(" | ", [for name in local.alert_flow_log_group_names : "SOURCE '${name}'"])} | ${local.alert_flow_logs_query}"
        }
      },
      {
        type = "log", x = 16, y = 13, width = 8, height = 10
        properties = {
          title  = "Application errors"
          region = var.aws_region
          view   = "table"
          query  = "${join(" | ", [for name in local.app_log_group_names : "SOURCE '${name}'"])} | ${local.app_errors_query}"
        }
      }
    ]
  })
}

output "app_logs_dashboard_url" {
  description = "Unified application log dashboard in CloudWatch."
  value       = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.app_logs.dashboard_name}"
}
