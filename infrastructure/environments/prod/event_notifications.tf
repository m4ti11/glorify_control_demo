resource "aws_sns_topic" "event_push" {
  name         = "${local.name_prefix}-events-push"
  display_name = "Glorify Events"
}

resource "aws_iam_role_policy" "event_push" {
  name = "${local.name_prefix}-event-push-policy"
  role = aws_iam_role.process_events.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sns:Publish"]
      Resource = aws_sns_topic.event_push.arn
    }]
  })
}

# Registering the identity sends verification mail, but keeps the SNS transport
# active until event_email_sender is explicitly set after verification.
resource "aws_ses_email_identity" "event_alerts" {
  count = var.event_email_identity_address != "" ? 1 : 0
  email = var.event_email_identity_address
}

resource "aws_ses_email_identity" "event_recipients" {
  for_each = setsubtract(var.event_email_recipient_identities, toset([var.event_email_identity_address]))
  email    = each.value
}

# SES stays disabled until an owner selects and verifies a sender in this region.
resource "aws_iam_role_policy" "event_email" {
  count = var.event_email_sender != "" ? 1 : 0

  name = "${local.name_prefix}-event-email-policy"
  role = aws_iam_role.process_events.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["ses:SendEmail"]
      Resource = [
        "arn:${data.aws_partition.current.partition}:ses:${var.aws_region}:${data.aws_caller_identity.current.account_id}:identity/${var.event_email_sender}",
        "arn:${data.aws_partition.current.partition}:ses:${var.aws_region}:${data.aws_caller_identity.current.account_id}:identity/${split("@", var.event_email_sender)[1]}",
      ]
      Condition = { StringEquals = { "ses:FromAddress" = var.event_email_sender } }
    }]
  })
}
