terraform {
  required_version = ">= 1.8"
  required_providers {
    verda = {
      source  = "verda-cloud/verda"
      version = "~> 1.1"
    }
  }
}

provider "verda" {}
