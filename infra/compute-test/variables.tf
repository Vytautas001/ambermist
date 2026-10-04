variable "project" {
  type    = string
  default = "ambermist"
}

variable "node_name" {
  type        = string
  default     = "ambermist-test"
  description = "Hostname and resource-name prefix. Kept distinct from the production node so both stacks coexist in one Verda account."
}

variable "owner" {
  type = string
}

# The test tier floats: ops/fleet-check.py --pick-cheapest writes whichever of these is
# available most cheaply. All of them are single-GPU, hold the ~16 GiB test model, and
# support var.image (verified against /instance-types on 2026-10-03). The production
# fleet cap in AGENTS.md governs ../compute, not this stack.
variable "instance_type" {
  type    = string
  default = "1A6000.10V"
  validation {
    condition = contains([
      "1A6000.10V",      # RTX A6000     48G  sm_86
      "1L40S.20V",       # L40S          48G  sm_89
      "1A100.22V",       # A100 80GB     80G  sm_80
      "1RTXPRO6000.30V", # RTX PRO 6000  96G  sm_120
      "1H100.80S.30V",   # H100          80G  sm_90
      "1H200.141S.44V",  # H200         141G  sm_90
    ], var.instance_type)
    error_message = "instance_type must be a single-GPU SKU known to fit the test model."
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
  type        = number
  default     = 60
  description = "Holds /srv/build, so it must fit a llama.cpp build tree."
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
