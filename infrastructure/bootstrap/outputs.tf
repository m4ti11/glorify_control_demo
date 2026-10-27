output "state_bucket_name" {
  description = "S3 bucket shared by the bootstrap and application backends."
  value       = aws_s3_bucket.terraform_state.id
}

output "state_access_policy_arn" {
  description = "Attach through IAM or an IAM Identity Center permission set; resource deployment permissions are separate."
  value       = aws_iam_policy.terraform_state_access.arn
}
