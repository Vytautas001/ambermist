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
      p2   - model bake-off.                1x 2xRTX PRO 6000 (spot)
      p3   - dress rehearsal.               same instances as p4 (see below)
      p4   - LIVE exercise.                 1x 2xRTX PRO 6000 primary (on-demand)
                                             + 1x H200 standby (on-demand)
      live4 - LIVE, 4 teams, held a week.   1x RTX PRO 6000 Int4 at 128k (on-demand)
  EOT
  type        = string
  default     = "off"

  validation {
    condition     = contains(["off", "p0", "p1", "p2", "p3", "p4", "live4", "qwen38"], var.phase)
    error_message = "phase must be one of: off, p0, p1, p2, p3, p4, live4, qwen38."
  }
}

variable "qwen38_gpu" {
  description = "GPU family for an isolated Qwen3.8 attempt. Required for phase qwen38."
  type        = string
  default     = ""

  validation {
    condition     = contains(["", "h200", "rtx"], var.qwen38_gpu)
    error_message = "qwen38_gpu must be h200 or rtx (empty outside the qwen38 phase)."
  }
}

variable "qwen38_model" {
  description = "Model selection for an isolated Qwen3.8 attempt. Required for phase qwen38."
  type        = string
  default     = ""

  validation {
    condition     = contains(["", "base", "abliterated"], var.qwen38_model)
    error_message = "qwen38_model must be base or abliterated (empty outside the qwen38 phase)."
  }
}

variable "qwen38_sessions" {
  description = "Full 131072-token slots for an isolated Qwen3.8 attempt."
  type        = number
  default     = 1

  validation {
    condition     = var.qwen38_sessions >= 1 && var.qwen38_sessions <= 4 && floor(var.qwen38_sessions) == var.qwen38_sessions
    error_message = "qwen38_sessions must be an integer between 1 and 4; the hardware ceiling is checked per node."
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
  description = "HF repo id served on the primary node. Must already be present on the weights volume."
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
  description = "Context window on the primary node."
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
# There is no monetary budget ceiling. The binding constraint is the GPU fleet
# cap below (see local.fleet_limit / terraform_data.fleet_guard) — never more
# than 1x H200 + 1x H100 + 2x RTX PRO 6000 (GPUs) running at once.
# ---------------------------------------------------------------------------

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
    Verda instance type for the primary serving node. Default is 2x RTX PRO 6000
    (192GB, TP=2): the most-available SKU in the catalogue and the one that holds
    the FP8 checkpoint plus its KV cache with the most headroom.

    Must be a SKU whose family/gpus fit within local.fleet_limit (1x H200, 1x H100,
    2x RTX PRO 6000 GPUs total, across node_sku + secondary_node_sku combined) -
    terraform_data.fleet_guard refuses an apply that doesn't. If `make preflight`
    reports this out of stock, move DOWN local.node_ladder and set node_sku/node_tp
    together. B200/B300/GB300 are deliberately not in the ladder - see AGENTS.md.
  EOT
  type        = string
  default     = "2RTXPRO6000.60V"
}

variable "node_tp" {
  description = "--tensor-parallel-size for the primary node. MUST match the GPU count in node_sku."
  type        = number
  default     = 2

  validation {
    condition     = contains([1, 2, 4, 8], var.node_tp)
    error_message = "node_tp must be 1, 2, 4 or 8 - vLLM requires a power of two."
  }
}

variable "secondary_node_sku" {
  description = <<-EOT
    Verda instance type for p4's second (standby) node. Default is 1x H200: with
    node_sku's default of 2x RTX PRO 6000, that's 2 RTX GPUs + 1 H200 GPU, which
    fits local.fleet_limit exactly and leaves the H100 free for p0/p1/live4/degraded
    mode. Runs standby_model/standby_max_model_len, not the primary FP8 checkpoint -
    a single H200 (141GB) cannot hold 125GB FP8 weights plus KV at full context.
  EOT
  type        = string
  default     = "1H200.141S.44V"
}

variable "secondary_node_tp" {
  description = "--tensor-parallel-size for the secondary node. MUST match the GPU count in secondary_node_sku."
  type        = number
  default     = 1

  validation {
    condition     = contains([1, 2, 4, 8], var.secondary_node_tp)
    error_message = "secondary_node_tp must be 1, 2, 4 or 8 - vLLM requires a power of two."
  }
}

variable "phase_node_sku" {
  description = "Launch-time instance type override for the shared p0/p1 development node."
  type        = string
  default     = ""
}

variable "phase_node_tp" {
  description = "Launch-time tensor parallelism override for the shared p0/p1 development node."
  type        = number
  default     = 0

  validation {
    condition     = contains([0, 1, 2, 4, 8], var.phase_node_tp)
    error_message = "phase_node_tp must be 0 (use node_tp) or 1, 2, 4 or 8."
  }
}

variable "phase_node_spot" {
  description = "Whether the shared p0/p1 development node is spot; keep false for a stable phase transition."
  type        = bool
  default     = false
}

variable "volume_retention_days" {
  description = <<-EOT
    How long the weights volume exists across the whole engagement. Storage bills
    by wall-clock time, not GPU-hours, so the informational cost output charges it
    in full on every phase rather than prorating.
  EOT
  type        = number
  default     = 21
}

variable "planned_hours" {
  description = "Planned billed hours for the phase being applied. Informational only - feeds the `cost` output, not a gate."
  type        = number
  default     = 1
}

variable "weights_footprint_gb" {
  description = "On-GPU size of the primary checkpoint. Qwen3.5-122B-A10B FP8 ~125, Ling-3.0-flash FP8 ~124, Nemotron-3-Super FP8 ~120, GPTQ-Int4 ~68."
  type        = number
  default     = 125
}

variable "sessions" {
  description = <<-EOT
    Concurrent adversary sessions (one per defending team). Sizes the KV cache of
    the live4 phase and divides the per-session throughput estimate. kv_footprint_gb
    stays quoted for 8 sessions; live4 scales it by sessions/8.
  EOT
  type        = number
  default     = 8

  validation {
    condition     = var.sessions >= 1 && var.sessions <= 16
    error_message = "sessions must be between 1 and 16."
  }
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
