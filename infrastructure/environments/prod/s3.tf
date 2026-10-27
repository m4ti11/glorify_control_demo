data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "pg_reports" {
  bucket        = "${local.name_prefix}-pg-reports-${data.aws_caller_identity.current.account_id}"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "pg_reports" {
  bucket                  = aws_s3_bucket.pg_reports.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "pg_reports" {
  bucket = aws_s3_bucket.pg_reports.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "pg_reports" {
  bucket = aws_s3_bucket.pg_reports.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "pg_reports" {
  bucket = aws_s3_bucket.pg_reports.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_policy" "pg_reports" {
  bucket = aws_s3_bucket.pg_reports.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "RequireTLS"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.pg_reports.arn, "${aws_s3_bucket.pg_reports.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "RequireConditionalReportWrites"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:PutObject"
        Resource  = ["${aws_s3_bucket.pg_reports.arn}/raw/pg-statements/*", "${aws_s3_bucket.pg_reports.arn}/generated/operational/*"]
        Condition = { Null = { "s3:if-none-match" = "true" } }
      }
    ]
  })
}

# Legacy generated reports remain readable during migration; new writes use pg_reports/generated/operational/.
resource "aws_s3_bucket" "operational_reports" {
  lifecycle {
    prevent_destroy = true
  }
  bucket        = "${local.name_prefix}-operational-reports-${data.aws_caller_identity.current.account_id}"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "operational_reports" {
  bucket                  = aws_s3_bucket.operational_reports.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "operational_reports" {
  bucket = aws_s3_bucket.operational_reports.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "operational_reports" {
  bucket = aws_s3_bucket.operational_reports.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "operational_reports" {
  bucket = aws_s3_bucket.operational_reports.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_policy" "operational_reports" {
  bucket = aws_s3_bucket.operational_reports.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "RequireTLS", Effect = "Deny", Principal = "*", Action = "s3:*"
      Resource  = [aws_s3_bucket.operational_reports.arn, "${aws_s3_bucket.operational_reports.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
      }, {
      Sid       = "RequireConditionalReportWrites", Effect = "Deny", Principal = "*", Action = "s3:PutObject"
      Resource  = "${aws_s3_bucket.operational_reports.arn}/generated/operational/*"
      Condition = { Null = { "s3:if-none-match" = "true" } }
    }]
  })
}
