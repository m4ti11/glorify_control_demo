variable "aws_region" {
  description = "AWS Region where Glorify Control example resources are created."
  type        = string
  default     = "us-east-1"
}

variable "website_health_check_enabled" {
  description = "Whether to monitor the configured example public website with Route 53 and send alarm/recovery email notifications."
  type        = bool
  default     = false
}

variable "website_health_check_hostname" {
  description = "Public DNS hostname checked over HTTPS by Route 53."
  type        = string
  default     = "example.com"

  validation {
    condition     = can(regex("^[A-Za-z0-9.-]+$", var.website_health_check_hostname))
    error_message = "Provide a hostname only, without a scheme, path, query, or fragment."
  }
}

variable "website_health_check_path" {
  description = "HTTPS request path checked on the example public website. URL fragments are not sent to web servers."
  type        = string
  default     = "/"

  validation {
    condition     = startswith(var.website_health_check_path, "/") && !strcontains(var.website_health_check_path, "#")
    error_message = "Provide an HTTP path beginning with / and without a URL fragment."
  }
}

variable "website_health_alert_email" {
  description = "Email endpoint for the dedicated us-east-1 SNS topic used by the website health alarm. The subscription must be confirmed."
  type        = string
  default     = ""

  validation {
    condition     = !var.website_health_check_enabled || can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.website_health_alert_email))
    error_message = "Set website_health_alert_email to the existing alert email before enabling the website health check."
  }
}

variable "dashboard_token_scopes_package_path" {
  description = "Path to the native dashboard authentication scope trigger ZIP."
  type        = string
  default     = "../../../build/dashboard_token_scopes.zip"
}

variable "project_name" {
  description = "Short, kebab-case project identifier used in AWS resource names."
  type        = string
  default     = "glorify-control"
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "prod"
}

variable "ingest_metrics_package_path" {
  description = "Path to the ZIP deployment package for the ingest_metrics Lambda."
  type        = string
}

variable "get_latest_metrics_package_path" {
  description = "Path to the get_latest_metrics Lambda ZIP, relative to this Terraform directory."
  type        = string
  default     = "../../../build/get_latest_metrics.zip"
}

variable "health_check_package_path" {
  description = "Path to the health_check Lambda ZIP, relative to this Terraform directory."
  type        = string
  default     = "../../../build/health_check.zip"
}

variable "health_monitor_system_ids" {
  description = "Systems monitored for missing metrics. Empty uses report_system_ids."
  type        = list(string)
  default     = []

  validation {
    condition = (
      length(var.health_monitor_system_ids) <= 100 &&
      length(distinct(var.health_monitor_system_ids)) == length(var.health_monitor_system_ids) &&
      alltrue([for id in var.health_monitor_system_ids : can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", id))])
    )
    error_message = "Provide at most 100 unique system IDs using the supported format."
  }
}

variable "health_monitor_stale_after_seconds" {
  description = "Maximum age of a received metrics snapshot before a health check marks the system suspect."
  type        = number
  default     = 180
  validation {
    condition     = var.health_monitor_stale_after_seconds >= 120 && var.health_monitor_stale_after_seconds <= 3600 && floor(var.health_monitor_stale_after_seconds) == var.health_monitor_stale_after_seconds
    error_message = "The stale threshold must be a whole number from 120 through 3600 seconds."
  }
}

variable "health_monitor_confirmation_seconds" {
  description = "How long a system must remain stale after the first suspected check before an outage event is queued."
  type        = number
  default     = 120
  validation {
    condition     = var.health_monitor_confirmation_seconds >= 120 && var.health_monitor_confirmation_seconds <= 3600 && floor(var.health_monitor_confirmation_seconds) == var.health_monitor_confirmation_seconds
    error_message = "The stale confirmation period must be a whole number from 120 through 3600 seconds."
  }
}

variable "vps_authorizer_package_path" {
  description = "Path to the reusable VPS authorizer ZIP, relative to this Terraform directory."
  type        = string
  default     = "../../../build/vps_authorizer.zip"
}

variable "dashboard_allowed_origins" {
  description = "Browser origins allowed to read metrics. CORS does not authenticate requests."
  type        = list(string)
  default     = ["http://localhost:8080", "http://127.0.0.1:8080"]
}

variable "dashboard_callback_urls" {
  description = "Exact Cognito sign-in callback URLs. Use localhost HTTP only for local development."
  type        = list(string)
  default     = ["http://localhost:8080/"]

  validation {
    condition     = length(var.dashboard_callback_urls) > 0 && alltrue([for url in var.dashboard_callback_urls : can(regex("^(https://[^/?#]+|http://localhost(:[0-9]+)?)/[^#]*$", url))])
    error_message = "Provide at least one HTTPS callback URL, or an HTTP localhost URL, without fragments."
  }
}

variable "dashboard_logout_urls" {
  description = "Exact Cognito sign-out return URLs."
  type        = list(string)
  default     = ["http://localhost:8080/"]

  validation {
    condition     = length(var.dashboard_logout_urls) > 0 && alltrue([for url in var.dashboard_logout_urls : can(regex("^(https://[^/?#]+|http://localhost(:[0-9]+)?)/[^#]*$", url))])
    error_message = "Provide at least one HTTPS logout URL, or an HTTP localhost URL, without fragments."
  }
}

variable "process_events_package_path" {
  description = "Path to the process_events Lambda ZIP, relative to this Terraform directory."
  type        = string
  default     = "../../../build/process_events.zip"
}

variable "get_events_package_path" {
  description = "Path to the get_events Lambda ZIP."
  type        = string
  default     = "../../../build/get_events.zip"
}

variable "ingest_pg_statements_package_path" {
  description = "Path to the ingest_pg_statements Lambda ZIP."
  type        = string
  default     = "../../../build/ingest_pg_statements.zip"
}

variable "get_pg_statements_package_path" {
  description = "Path to the get_pg_statements Lambda ZIP."
  type        = string
  default     = "../../../build/get_pg_statements.zip"
}

variable "configuration_package_path" {
  type        = string
  description = "Path to the configuration Lambda ZIP."
  default     = "../../../build/configuration.zip"
}

variable "sync_notification_channels_package_path" {
  type        = string
  description = "Path to the sync_notification_channels Lambda ZIP."
  default     = "../../../build/sync_notification_channels.zip"
}
variable "evaluate_host_alerts_package_path" {
  type        = string
  description = "Path to the host alert evaluator Lambda ZIP."
  default     = "../../../build/evaluate_host_alerts.zip"
}

variable "host_alert_max_sample_gap_seconds" {
  type        = number
  description = "Largest gap allowed between host samples in a sustained threshold window."
  default     = 600
  validation {
    condition     = var.host_alert_max_sample_gap_seconds >= 1 && var.host_alert_max_sample_gap_seconds <= 3600 && floor(var.host_alert_max_sample_gap_seconds) == var.host_alert_max_sample_gap_seconds
    error_message = "The sample gap must be a whole number between 1 and 3600 seconds."
  }
}

variable "report_system_ids" {
  description = "Required explicit allowlist of systems receiving scheduled operational reports. No default is provided."
  type        = list(string)
  validation {
    condition     = length(var.report_system_ids) >= 1 && length(var.report_system_ids) <= 100 && length(distinct(var.report_system_ids)) == length(var.report_system_ids) && alltrue([for id in var.report_system_ids : can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", id))])
    error_message = "Provide 1 to 100 unique system IDs using the supported format."
  }
}

variable "openai_model" {
  description = "OpenAI model identifier approved for operational report generation."
  type        = string
  validation {
    condition     = length(trimspace(var.openai_model)) > 0
    error_message = "Set openai_model explicitly; no model is assumed by this configuration."
  }
}

variable "agent_pg_capture_hour" {
  description = "Agent PostgreSQL capture hour in reporting_timezone."
  type        = number
  default     = 3
  validation {
    condition     = var.agent_pg_capture_hour >= 0 && var.agent_pg_capture_hour <= 23 && floor(var.agent_pg_capture_hour) == var.agent_pg_capture_hour
    error_message = "Capture hour must be a whole number from 0 through 23."
  }
}

variable "agent_pg_capture_minute" {
  description = "Agent PostgreSQL capture minute in reporting_timezone."
  type        = number
  default     = 0
  validation {
    condition     = var.agent_pg_capture_minute >= 0 && var.agent_pg_capture_minute <= 59 && floor(var.agent_pg_capture_minute) == var.agent_pg_capture_minute
    error_message = "Capture minute must be a whole number from 0 through 59."
  }
}

variable "report_grace_period_minutes" {
  description = "Delay after the agent PostgreSQL capture before report generation."
  type        = number
  default     = 30
  validation {
    condition     = var.report_grace_period_minutes >= 0 && var.report_grace_period_minutes <= 720 && floor(var.report_grace_period_minutes) == var.report_grace_period_minutes
    error_message = "The grace period must be a whole number from 0 through 720 minutes."
  }
}

variable "reporting_timezone" {
  description = "IANA timezone used by agent reporting and EventBridge Scheduler."
  type        = string
  default     = "America/Argentina/Buenos_Aires"
  validation {
    condition     = var.reporting_timezone == "America/Argentina/Buenos_Aires"
    error_message = "Daily operational reports use the approved America/Argentina/Buenos_Aires timezone."
  }
}

variable "generate_report_package_path" {
  description = "Path to the Generate Report Lambda ZIP."
  type        = string
  default     = "../../../build/generate_report.zip"
}

variable "get_operational_report_package_path" {
  description = "Path to the authenticated operational report reader ZIP."
  type        = string
  default     = "../../../build/get_operational_report.zip"
}

variable "metrics_stale_after_seconds" {
  description = "Mark host metrics stale if the last sample is this far before the window end."
  type        = number
  default     = 600
  validation {
    condition     = var.metrics_stale_after_seconds >= 1 && floor(var.metrics_stale_after_seconds) == var.metrics_stale_after_seconds
    error_message = "Metrics stale threshold must be a positive whole number of seconds."
  }
}

variable "events_stale_after_seconds" {
  description = "Mark events stale if the latest occurred_at event is this far before the window end."
  type        = number
  default     = 600
  validation {
    condition     = var.events_stale_after_seconds >= 1 && floor(var.events_stale_after_seconds) == var.events_stale_after_seconds
    error_message = "Events stale threshold must be a positive whole number of seconds."
  }
}

variable "pg_report_stale_after_seconds" {
  description = "Mark PostgreSQL capture stale if it is older than this at report generation time."
  type        = number
  default     = 86400
  validation {
    condition     = var.pg_report_stale_after_seconds >= 1 && floor(var.pg_report_stale_after_seconds) == var.pg_report_stale_after_seconds
    error_message = "PostgreSQL stale threshold must be a positive whole number of seconds."
  }
}

variable "openai_timeout_seconds" {
  description = "Maximum OpenAI request duration in seconds."
  type        = number
  default     = 45
  validation {
    condition     = var.openai_timeout_seconds >= 1 && var.openai_timeout_seconds <= 90 && floor(var.openai_timeout_seconds) == var.openai_timeout_seconds
    error_message = "OpenAI timeout must be a whole number from 1 through 90 seconds."
  }
}

variable "report_lease_seconds" {
  description = "Claim lease duration; keep longer than the Lambda maximum runtime."
  type        = number
  default     = 1200
  validation {
    condition     = var.report_lease_seconds >= 360 && var.report_lease_seconds <= 3600 && floor(var.report_lease_seconds) == var.report_lease_seconds
    error_message = "Report lease must be a whole number from 360 through 3600 seconds, longer than the generator timeout."
  }
}

variable "report_recovery_visibility_seconds" {
  description = "SQS recovery delay; defaults to 30 minutes and must outlast the configured report lease."
  type        = number
  default     = 1800
  validation {
    condition     = var.report_recovery_visibility_seconds >= 1800 && var.report_recovery_visibility_seconds <= 43200 && floor(var.report_recovery_visibility_seconds) == var.report_recovery_visibility_seconds
    error_message = "Recovery visibility must be at least 1800 whole seconds, beyond the maximum claim lease and invocation margin, and no more than 12 hours."
  }
}

variable "report_recovery_max_receive_count" {
  description = "Maximum failed SQS report attempts before moving a job to the recovery DLQ."
  type        = number
  default     = 8
  validation {
    condition     = var.report_recovery_max_receive_count >= 3 && var.report_recovery_max_receive_count <= 20 && floor(var.report_recovery_max_receive_count) == var.report_recovery_max_receive_count
    error_message = "Recovery receive count must be a whole number from 3 through 20."
  }
}

variable "report_input_max_bytes" {
  description = "Maximum serialized source payload sent to the configured OpenAI model."
  type        = number
  default     = 262144
  validation {
    condition     = var.report_input_max_bytes >= 16384 && var.report_input_max_bytes <= 1048576 && floor(var.report_input_max_bytes) == var.report_input_max_bytes
    error_message = "Report input size must be a whole number from 16 KiB through 1 MiB."
  }
}

variable "report_output_max_bytes" {
  description = "Maximum serialized operational report size."
  type        = number
  default     = 65536
  validation {
    condition     = var.report_output_max_bytes >= 4096 && var.report_output_max_bytes <= 262144 && floor(var.report_output_max_bytes) == var.report_output_max_bytes
    error_message = "Report output size must be a whole number from 4 KiB through 256 KiB."
  }
}

variable "report_email_enabled" {
  description = "Email the generated PDF to confirmed, enabled notification-channel recipients. Requires a verified SES sender."
  type        = bool
  default     = false
}

variable "event_email_identity_address" {
  description = "Email identity to register with SES. Sends a verification email without enabling HTML event delivery."
  type        = string
  default     = ""

  validation {
    condition     = var.event_email_identity_address == "" || can(regex("^[^\\s@<>]+@[^\\s@<>]+\\.[^\\s@<>]+$", var.event_email_identity_address))
    error_message = "Provide an email address without display-name or header characters, or leave empty."
  }
}

variable "event_email_recipient_identities" {
  description = "Recipient email addresses to register for SES verification while the account is in sandbox."
  type        = set(string)
  default     = []

  validation {
    condition     = alltrue([for email in var.event_email_recipient_identities : can(regex("^[^\\s@<>]+@[^\\s@<>]+\\.[^\\s@<>]+$", email))])
    error_message = "Provide valid recipient email addresses without display-name or header characters."
  }
}

variable "event_email_sender" {
  description = "SES-verified From address for HTML event alerts. Empty keeps SNS text email active."
  type        = string
  default     = ""

  validation {
    condition     = var.event_email_sender == "" || can(regex("^[^\\s@<>]+@[^\\s@<>]+\\.[^\\s@<>]+$", var.event_email_sender))
    error_message = "Provide an SES-verified email address without display-name or header characters, or leave empty for SNS text email."
  }
}

variable "report_email_sender" {
  description = "SES-verified From address for operational PDF reports. No default address is assumed."
  type        = string
  default     = ""
  validation {
    condition     = var.report_email_sender == "" || can(regex("^[^\\s@<>]+@[^\\s@<>]+\\.[^\\s@<>]+$", var.report_email_sender))
    error_message = "Provide a valid SES-verified email address without display-name or header characters."
  }
}

variable "dashboard_domain_name" {
  description = "Optional dashboard hostname. Setting it requests an ACM certificate; validate its CNAME in the external DNS provider before enabling the alias."
  type        = string
  default     = ""

  validation {
    condition     = var.dashboard_domain_name == "" || can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", var.dashboard_domain_name))
    error_message = "Use a lowercase DNS hostname without https://, a path, or a wildcard."
  }
}

variable "dashboard_custom_domain_enabled" {
  description = "Attach the custom domain after publishing its ACM DNS validation record."
  type        = bool
  default     = false

  validation {
    condition     = !var.dashboard_custom_domain_enabled || var.dashboard_domain_name != ""
    error_message = "Set dashboard_domain_name before enabling the custom domain."
  }
}

variable "ops_alert_email" {
  description = "Owned operations mailbox for infrastructure alarms; confirm the SNS email subscription."
  type        = string
  validation {
    condition     = can(regex("^[^\\s@<>]+@[^\\s@<>]+\\.[^\\s@<>]+$", var.ops_alert_email))
    error_message = "Provide an operations email address without display-name or header characters."
  }
}

variable "report_expected_heartbeat_seconds" {
  description = "Expected agent heartbeat interval for day-coverage calculations; keep aligned with GLORIFY_HEARTBEAT_SECONDS."
  type        = number
  default     = 120
  validation {
    condition     = var.report_expected_heartbeat_seconds >= 1 && floor(var.report_expected_heartbeat_seconds) == var.report_expected_heartbeat_seconds
    error_message = "Expected heartbeat interval must be a positive integer."
  }
}
