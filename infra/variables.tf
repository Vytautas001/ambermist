# ---------------------------------------------------------------------------
# Phase selection — this is the cost-control mechanism.
#
# verda_instance cannot be updated in place (the provider's Update method is a
# hard error), so every meaningful change is destroy-and-recreate anyway. Rather
# than fight that, phases are modelled as count-gated instances: applying a
# phase brings up exactly the GPUs that phase needs and tears down the rest.
# The weights volume is a separate, long-lived resource that survives all of it.
# ---------------------------------------------------------------------------

variable "phase" {
  description = <<-EOT
    Which exercise phase to stand up. Determines which GPU instances exist.

      off  - no GPUs at all (volume and keys persist).  Use between phases.
      p0   - image bake / weight pull.      1x H200 (on-demand)
      p1   - Red Cell harness development.  1x H200 (on-demand)
      p2   - model bake-off.                1x B200 (spot)
      p3   - dress rehearsal.               1x B200 (on-demand)
      p4   - LIVE exercise.                 1x B200 (on-demand) + 1x RTX PRO 6000 standby
  EOT
  type        = string
  default     = "off"

  validation {
    condition     = contains(["off", "p0", "p1", "p2", "p3", "p4"], var.phase)
    error_message = "phase must be one of: off, p0, p1, p2, p3, p4."
  }
}

variable "project" {
  description = "Short name prefixed to every resource. Lowercase, DNS-safe."
  type        = string
  default     = "redcell"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,18}$", var.project))
    error_message = "project must be 2-19 chars, lowercase alphanumeric or hyphen, starting with a letter."
  }
}

variable "location" {
  description = "Verda datacentre code. All Verda sites are in Finland (EU)."
  type        = string
  default     = "FIN-01"

  validation {
    condition     = contains(["FIN-01", "FIN-02", "FIN-03"], var.location)
    error_message = "location must be FIN-01, FIN-02 or FIN-03. Verda has no non-Finnish sites; there is no ICE-01."
  }
}

variable "image" {
  description = <<-EOT
    Base image identifier. The provider does NOT validate this — a wrong string
    fails at apply, not at plan. Confirm against your own account with
    `make images` (catalog contents and even the slug naming scheme drift over
    time — this default has already broken once). Must be a "docker" category
    image; scripts/startup.sh.tftpl requires Docker preinstalled. Use the
    `image_type` field from `make images`, not the `id`.
  EOT
  type        = string
  default     = "24.04.cuda12.9.docker"
}

variable "ssh_public_keys" {
  description = "Map of operator name => OpenSSH public key. Everyone who needs console access."
  type        = map(string)

  validation {
    condition     = length(var.ssh_public_keys) > 0
    error_message = "At least one SSH key is required; without one you cannot reach the instance."
  }

  validation {
    condition     = alltrue([for k in values(var.ssh_public_keys) : can(regex("^(ssh-ed25519|ssh-rsa|ecdsa-sha2-) ", k))])
    error_message = "Each value must be an OpenSSH public key (ssh-ed25519, ssh-rsa or ecdsa-sha2-*)."
  }
}

# ---------------------------------------------------------------------------
# Persistent model weights volume
# ---------------------------------------------------------------------------

variable "weights_volume_size_gb" {
  description = <<-EOT
    Size of the shared NVMe volume holding model weights, in GB.
    Sized for Qwen3.5-122B FP8 (~122GB) + GPTQ-Int4 (~68GB) + Ling-3.0-flash FP8
    (~124GB) + headroom. NOTE: size is ForceNew — growing this volume DESTROYS
    the data. Size it correctly the first time.
  EOT
  type        = number
  default     = 400

  validation {
    condition     = var.weights_volume_size_gb >= 250 && var.weights_volume_size_gb <= 2000
    error_message = "weights_volume_size_gb must be between 250 and 2000. Below 250 will not hold the primary plus fallback checkpoints."
  }
}

variable "os_volume_size_gb" {
  description = "Per-instance OS volume size in GB. Must fit the vLLM container image (~25GB) plus logs."
  type        = number
  default     = 100

  validation {
    condition     = var.os_volume_size_gb >= 80
    error_message = "os_volume_size_gb must be at least 80; the CUDA base image plus the vLLM container will not fit below that."
  }
}

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------

variable "primary_model" {
  description = "HF repo id served on the B200. Must already be present on the weights volume."
  type        = string
  default     = "Qwen/Qwen3.5-122B-A10B-FP8"
}

variable "standby_model" {
  description = "HF repo id served on the RTX PRO 6000 standby / dev box."
  type        = string
  default     = "Qwen/Qwen3.5-122B-A10B-GPTQ-Int4"
}

variable "vllm_image" {
  description = "Pinned vLLM container image. Never use :latest for an exercise."
  type        = string
  default     = "vllm/vllm-openai:v0.28.0"
}

variable "served_model_name" {
  description = "Public model name. Identical on primary and standby so router failover is transparent."
  type        = string
  default     = "redcell-adversary"
}

variable "primary_max_model_len" {
  description = "Context window on the B200 primary."
  type        = number
  default     = 131072
}

variable "standby_max_model_len" {
  description = <<-EOT
    Context window on the Int4 standby. Lower than primary by design: Int4 weights
    (~68GB) leave only ~13.9 GiB of KV pool in 96GB, which is 9 sessions at 128k
    (too thin) but 18 at 64k. Degraded mode halves context rather than dropping teams.
  EOT
  type        = number
  default     = 65536
}

# ---------------------------------------------------------------------------
# Budget guard
# ---------------------------------------------------------------------------

variable "budget_eur" {
  description = "Hard ceiling for the whole exercise, in EUR."
  type        = number
  default     = 500
}

variable "storage_eur_per_gib_month" {
  description = "Verda block-volume price. The console quotes EUR directly, so no FX conversion is involved."
  type        = number
  default     = 0.20
}

variable "active_params_b" {
  description = "Active parameters in billions, used for the decode-throughput estimate. Qwen3.5-122B-A10B = 10, Ling-3.0-flash = 5.1, Nemotron-3-Super = 12."
  type        = number
  default     = 10
}

variable "node_sku" {
  description = <<-EOT
    Verda instance type for each serving node. Default is 2x RTX PRO 6000 (192GB,
    TP=2): the most-available SKU in the catalogue and the cheapest that holds the
    FP8 checkpoint plus its KV cache.

    If `make preflight` reports this out of stock, move DOWN local.node_ladder and
    set node_sku/node_tp together. B200 is deliberately not in the ladder.
  EOT
  type        = string
  default     = "2RTXPRO6000.60V"
}

variable "node_tp" {
  description = "--tensor-parallel-size for each node. MUST match the GPU count in node_sku."
  type        = number
  default     = 2

  validation {
    condition     = contains([1, 2, 4, 8], var.node_tp)
    error_message = "node_tp must be 1, 2, 4 or 8 - vLLM requires a power of two."
  }
}

variable "spend_to_date_eur" {
  description = <<-EOT
    EUR already spent in previous phases. Update this after each phase from the
    Verda billing console. The budget precondition uses it to refuse an apply that
    would breach the ceiling.
  EOT
  type        = number
  default     = 0
}

variable "volume_retention_days" {
  description = <<-EOT
    How long the weights volume exists across the whole engagement. Storage bills
    by wall-clock time, not GPU-hours, so this is charged in full against the
    budget on every phase rather than prorated.
  EOT
  type        = number
  default     = 21
}

variable "misc_reserve_eur" {
  description = "Flat reserve for egress, snapshots and the router VM."
  type        = number
  default     = 12.91 # ~$15
}

variable "planned_hours" {
  description = "Planned billed hours for the phase being applied. Used by the budget guard."
  type        = number
  default     = 1
}

variable "weights_footprint_gb" {
  description = "On-GPU size of the primary checkpoint. Qwen3.5-122B-A10B FP8 ~125, Ling-3.0-flash FP8 ~124, Nemotron-3-Super FP8 ~120, GPTQ-Int4 ~68."
  type        = number
  default     = 125
}

variable "kv_footprint_gb" {
  description = "KV cache needed for 8 concurrent sessions at primary_max_model_len, FP8. Qwen3.5-122B = 12, Ling = 3.9, Nemotron = 4.0."
  type        = number
  default     = 12
}

variable "standby_weights_gb" {
  description = "On-GPU size of the Int4 checkpoint used for bake and development. Qwen3.5-122B-A10B GPTQ-Int4 ~68 GB (weights + GPTQ scales + BF16 embeddings)."
  type        = number
  default     = 68
}

variable "standby_kv_gb" {
  description = "KV cache for 8 sessions at standby_max_model_len, FP8. 12 KiB/token x 64k x 8 = 6 GB."
  type        = number
  default     = 6
}
