variable "project" {
  type    = string
  default = "ambermist"
}

variable "owner" {
  type = string
}

variable "instance_type" {
  type    = string
  default = "1H200.141S.44V"
  validation {
    condition     = contains(["1H200.141S.44V"], var.instance_type)
    error_message = "Only one H200 is allowed for this tier."
  }
}

variable "image" {
  type    = string
  default = "24.04.cuda12.9.docker"
}

variable "use_spot" {
  type    = bool
  default = true
}

variable "os_volume_size_gib" {
  type    = number
  default = 60
}

variable "ssh_public_keys" {
  type = map(string)
}

variable "admin_cidrs" {
  type = list(string)
  validation {
    condition     = length(var.admin_cidrs) > 0 && !contains(var.admin_cidrs, "0.0.0.0/0")
    error_message = "admin_cidrs must list the operator's own addresses."
  }
}
