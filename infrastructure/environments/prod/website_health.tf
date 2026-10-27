resource "aws_route53_health_check" "client_page" {
  count = var.website_health_check_enabled ? 1 : 0

  fqdn              = var.website_health_check_hostname
  port              = 443
  type              = "HTTPS"
  resource_path     = var.website_health_check_path
  request_interval  = 30
  failure_threshold = 2
  enable_sni        = true

  tags = {
    Name = "${local.name_prefix}-client-page-health-check"
  }
}

resource "aws_sns_topic" "website_health" {
  provider = aws.us_east_1
  count    = var.website_health_check_enabled ? 1 : 0

  name         = "${local.name_prefix}-website-health"
  display_name = "Glorify client website health"
}

resource "aws_sns_topic_subscription" "website_health_email" {
  provider = aws.us_east_1
  count    = var.website_health_check_enabled ? 1 : 0

  topic_arn = aws_sns_topic.website_health[0].arn
  protocol  = "email"
  endpoint  = var.website_health_alert_email
}

resource "aws_cloudwatch_metric_alarm" "website_health" {
  provider = aws.us_east_1
  count    = var.website_health_check_enabled ? 1 : 0

  alarm_name          = "${local.name_prefix}-client-page-unhealthy"
  alarm_description   = "The public client website HTTPS health check is unhealthy."
  namespace           = "AWS/Route53"
  metric_name         = "HealthCheckStatus"
  dimensions          = { HealthCheckId = aws_route53_health_check.client_page[0].id }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "missing"
  alarm_actions       = [aws_sns_topic.website_health[0].arn]
  ok_actions          = [aws_sns_topic.website_health[0].arn]
}

resource "aws_sns_topic_policy" "website_health" {
  provider = aws.us_east_1
  count    = var.website_health_check_enabled ? 1 : 0

  arn = aws_sns_topic.website_health[0].arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowAlarmPublish"
        Effect    = "Allow"
        Principal = { Service = "cloudwatch.amazonaws.com" }
        Action    = "sns:Publish"
        Resource  = aws_sns_topic.website_health[0].arn
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
          ArnLike      = { "aws:SourceArn" = aws_cloudwatch_metric_alarm.website_health[0].arn }
        }
      }
    ]
  })
}
