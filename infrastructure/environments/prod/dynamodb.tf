resource "aws_dynamodb_table" "metrics" {
  name             = local.metrics_table_name
  billing_mode     = "PAY_PER_REQUEST"
  hash_key         = "PK"
  range_key        = "SK"
  stream_enabled   = true
  stream_view_type = "NEW_IMAGE"

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }
}

resource "aws_dynamodb_table" "events" {
  name         = "${local.name_prefix}-events"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  # Existing items already have record_type and SK: AWS builds this index online
  # for historical records without application scans or a backfill script.
  attribute {
    name = "record_type"
    type = "S"
  }

  global_secondary_index {
    name            = "events-by-date"
    projection_type = "KEYS_ONLY"

    key_schema {
      attribute_name = "record_type"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "SK"
      key_type       = "RANGE"
    }
  }

  point_in_time_recovery {
    enabled = false
  }
}

resource "aws_dynamodb_table" "configuration" {
  name             = "${local.name_prefix}-configuration"
  billing_mode     = "PAY_PER_REQUEST"
  hash_key         = "PK"
  range_key        = "SK"
  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES"

  attribute {
    name = "PK"
    type = "S"
  }
  attribute {
    name = "SK"
    type = "S"
  }
  point_in_time_recovery {
    enabled = true
  }
}
