# Example only: no remote state is connected in this repository.
# Review the account, bucket policy and separate state key before enabling.
# terraform {
#   backend "s3" {
#     bucket       = "example-terraform-state-123456789012"
#     key          = "bootstrap/terraform.tfstate"
#     region       = "us-east-1"
#     encrypt      = true
#     use_lockfile = true
#   }
# }
