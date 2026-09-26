variable "location" {
  type = string
  validation {
    condition     = contains(["FIN-01", "FIN-02", "FIN-03"], var.location)
    error_message = "location must be FIN-01, FIN-02 or FIN-03."
  }
}

variable "model_volume_size_gib" {
  type    = number
  default = 128
  validation {
    condition     = var.model_volume_size_gib >= 120
    error_message = "The model volume must hold 111 GB of weights plus headroom."
  }
}
