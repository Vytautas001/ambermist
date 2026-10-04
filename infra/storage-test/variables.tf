variable "location" {
  type = string
  validation {
    condition     = contains(["FIN-01", "FIN-02", "FIN-03"], var.location)
    error_message = "location must be FIN-01, FIN-02 or FIN-03."
  }
}

variable "model_volume_size_gib" {
  type    = number
  default = 50
  validation {
    condition     = var.model_volume_size_gib >= 50
    error_message = "Verda's NVMe block-volume minimum is 50 GiB; below it the API rejects the create with 'Specified storage size is too low'. ~16 GiB of weights fits easily."
  }
}
