# The legacy topic has been retired after verifying migration to the dynamic
# channel topic. Retain this historical state handoff for older checkouts.
removed {
  from = aws_sns_topic_subscription.alert_email
  lifecycle {
    destroy = false
  }
}
