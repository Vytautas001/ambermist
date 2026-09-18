terraform {
  required_version = ">= 1.9.0"

  required_providers {
    verda = {
      source  = "verda-cloud/verda"
      version = "~> 1.1" # latest at time of writing: 1.1.3 (2026-09-12)
    }
  }

  # Remote state is strongly recommended once more than one person can apply.
  # Verda has no state backend of its own; any S3-compatible EU bucket works.
  #
  # backend "s3" {
  #   bucket                      = "redcell-tfstate"
  #   key                         = "exercise/terraform.tfstate"
  #   region                      = "eu-north-1"
  #   encrypt                     = true
  #   use_lockfile                = true
  # }
}

# The provider reads VERDA_CLIENT_ID / VERDA_CLIENT_SECRET from the environment.
# Do not put credentials in HCL — values set here land in the plan file.
provider "verda" {}
