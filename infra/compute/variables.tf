variable "project" {
  type    = string
  default = "ambermist"
}

variable "owner" {
  type = string
}

variable "location" {
  type        = string
  description = "Site of the model volume to use. ops/fleet-check.py --pick-site writes it to terraform.tfvars."
  validation {
    condition     = contains(["FIN-01", "FIN-02", "FIN-03"], var.location)
    error_message = "location must be FIN-01, FIN-02 or FIN-03."
  }
}

variable "instance_type" {
  type        = string
  default     = "1H200.141S.44V"
  description = "ops/fleet-check.py --pick-site writes it to terraform.tfvars and checks the fleet cap across held nodes."
  # Keep in sync with ALLOWED in ops/fleet-check.py.
  validation {
    condition = contains([
      "1H200.141S.44V", "2RTXPRO6000.60V",
    ], var.instance_type)
    error_message = "Only SKUs within the fleet cap (1 H200, 2 RTX PRO 6000 GPUs) are allowed."
  }
}

variable "image" {
  type    = string
  default = "24.04.cuda12.9.docker"
}

variable "use_spot" {
  type        = bool
  default     = true
  description = "ops/fleet-check.py --pick-site --type spot|on-demand writes it to terraform.tfvars."
}

variable "os_volume_size_gib" {
  type    = number
  default = 60
}

variable "ssh_public_key_path" {
  type        = string
  description = "Path to the operator's SSH public key file (.pub); registered with Verda and installed on the instance."
}

variable "admin_cidrs" {
  type = list(string)
  validation {
    condition     = length(var.admin_cidrs) > 0 && !contains(var.admin_cidrs, "0.0.0.0/0")
    error_message = "admin_cidrs must list the operator's own addresses."
  }
}
