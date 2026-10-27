locals {
  dashboard_origin = var.dashboard_custom_domain_enabled ? "https://${var.dashboard_domain_name}" : "https://${aws_cloudfront_distribution.dashboard.domain_name}"
  dashboard_url    = "${local.dashboard_origin}/"
}

# CloudFront requires ACM viewer certificates in us-east-1 even when S3 is elsewhere.
resource "aws_acm_certificate" "dashboard" {
  provider          = aws.us_east_1
  count             = var.dashboard_domain_name != "" ? 1 : 0
  domain_name       = var.dashboard_domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_acm_certificate_validation" "dashboard" {
  provider                = aws.us_east_1
  count                   = var.dashboard_custom_domain_enabled ? 1 : 0
  certificate_arn         = aws_acm_certificate.dashboard[0].arn
  validation_record_fqdns = [for option in aws_acm_certificate.dashboard[0].domain_validation_options : option.resource_record_name]
}

resource "aws_cloudfront_response_headers_policy" "dashboard_https" {
  name = "${local.name_prefix}-dashboard-https"
  security_headers_config {
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = false
      preload                    = false
      override                   = true
    }
  }
}

resource "aws_s3_bucket" "dashboard" {
  bucket        = "${local.name_prefix}-dashboard-${data.aws_caller_identity.current.account_id}"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "dashboard" {
  bucket                  = aws_s3_bucket.dashboard.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_cloudfront_origin_access_control" "dashboard" {
  name                              = "${local.name_prefix}-dashboard"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Files have stable names rather than content hashes. Revalidate by default;
# origin Cache-Control may opt into caching for at most five minutes.
resource "aws_cloudfront_cache_policy" "dashboard" {
  name        = "${local.name_prefix}-dashboard-revalidate"
  min_ttl     = 0
  default_ttl = 0
  max_ttl     = 300

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_gzip   = true
    enable_accept_encoding_brotli = true
    cookies_config {
      cookie_behavior = "none"
    }
    headers_config {
      header_behavior = "none"
    }
    query_strings_config {
      query_string_behavior = "none"
    }
  }
}

resource "aws_cloudfront_distribution" "dashboard" {
  aliases             = var.dashboard_custom_domain_enabled ? [var.dashboard_domain_name] : []
  enabled             = true
  comment             = "${local.name_prefix} static dashboard"
  default_root_object = "index.html"
  is_ipv6_enabled     = true
  price_class         = "PriceClass_100"

  origin {
    domain_name              = aws_s3_bucket.dashboard.bucket_regional_domain_name
    origin_id                = "dashboard-s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.dashboard.id
  }

  default_cache_behavior {
    target_origin_id           = "dashboard-s3"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = aws_cloudfront_cache_policy.dashboard.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.dashboard_https.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = !var.dashboard_custom_domain_enabled
    acm_certificate_arn            = var.dashboard_custom_domain_enabled ? aws_acm_certificate_validation.dashboard[0].certificate_arn : null
    ssl_support_method             = var.dashboard_custom_domain_enabled ? "sni-only" : null
    minimum_protocol_version       = var.dashboard_custom_domain_enabled ? "TLSv1.2_2021" : "TLSv1"
  }
}

resource "aws_s3_bucket_policy" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowCloudFrontRead"
        Effect    = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.dashboard.arn}/*"
        Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.dashboard.arn } }
      },
      {
        Sid       = "RequireTLS"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.dashboard.arn, "${aws_s3_bucket.dashboard.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      }
    ]
  })
}
