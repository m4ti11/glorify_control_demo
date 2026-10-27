locals {
  account_id  = "123456789012"
  region      = "us-east-1"
  bucket_name = "example-terraform-state-${local.account_id}"
  state_keys = [
    "bootstrap/terraform.tfstate",
    "glorify-control/prod/terraform.tfstate",
  ]
}

provider "aws" {
  region              = local.region
  allowed_account_ids = [local.account_id]

  default_tags {
    tags = {
      Project   = "glorify-control"
      Component = "terraform-state"
      ManagedBy = "terraform"
    }
  }
}

resource "aws_s3_bucket" "terraform_state" {
  bucket        = local.bucket_name
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "terraform_state_bucket" {
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.terraform_state.arn,
      "${aws_s3_bucket.terraform_state.arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  policy = data.aws_iam_policy_document.terraform_state_bucket.json
}

data "aws_iam_policy_document" "terraform_state_access" {
  statement {
    sid       = "ListStatePaths"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.terraform_state.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = concat(local.state_keys, [for key in local.state_keys : "${key}.tflock"])
    }
  }

  statement {
    sid       = "ReadWriteState"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = [for key in local.state_keys : "${aws_s3_bucket.terraform_state.arn}/${key}"]
  }

  statement {
    sid       = "ManageStateLocks"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [for key in local.state_keys : "${aws_s3_bucket.terraform_state.arn}/${key}.tflock"]
  }
}

resource "aws_iam_policy" "terraform_state_access" {
  name        = "glorify-control-terraform-state-access"
  description = "Access to Glorify Control Terraform states and their S3 locks"
  policy      = data.aws_iam_policy_document.terraform_state_access.json
}
