locals {
  # ---------------------------------------------------------------------------
  # Instance type identifiers, verified against GET /v1/instance-availability
  # and the Verda deploy console. The provider ships NO data sources, so these
  # must be literals.
  #
  # `stock` records what the console showed when this was written. It is a
  # PLANNING HINT, NOT A GUARANTEE — run `make preflight` before every apply.
  #
  # `family`/`gpus` drive the fleet cap (see local.fleet_limit below): only
  # h200, h100 and rtxpro are permitted families at all. Everything else is
  # tagged "blocked" (fleet_limit.blocked = 0), whether or not Verda has it in
  # stock — this is a hardware-allowlist policy, not just an availability check.
  # ---------------------------------------------------------------------------
  catalog = {
    # sku                    vram  eur/gpu/h  stock   nvlink  hbm_tb_s  family    gpus
    "1GB300.32V"       = { vram = 288, eur = 7.733, stock = false, nvlink = true, bw = 8.00, family = "blocked", gpus = 1 }
    "1B300.30V"        = { vram = 268, eur = 6.724, stock = false, nvlink = true, bw = 8.00, family = "blocked", gpus = 1 }
    "1B200.30V"        = { vram = 180, eur = 5.532, stock = false, nvlink = true, bw = 8.00, family = "blocked", gpus = 1 }
    "1H200.141S.44V"   = { vram = 141, eur = 3.728, stock = true, nvlink = true, bw = 4.80, family = "h200", gpus = 1 }
    "2H200.141S.88V"   = { vram = 282, eur = 7.456, stock = true, nvlink = true, bw = 9.60, family = "h200", gpus = 2 }
    "1H100.80S.30V"    = { vram = 80, eur = 2.841, stock = true, nvlink = true, bw = 3.35, family = "h100", gpus = 1 }
    "2H100.80S.80V"    = { vram = 160, eur = 5.682, stock = true, nvlink = true, bw = 6.70, family = "h100", gpus = 2 }
    "4H100.80S.176V"   = { vram = 320, eur = 11.364, stock = true, nvlink = true, bw = 13.40, family = "h100", gpus = 4 }
    "1RTXPRO6000.30V"  = { vram = 96, eur = 1.585, stock = true, nvlink = false, bw = 1.79, family = "rtxpro", gpus = 1 }
    "2RTXPRO6000.60V"  = { vram = 192, eur = 3.170, stock = true, nvlink = false, bw = 3.58, family = "rtxpro", gpus = 2 }
    "4RTXPRO6000.120V" = { vram = 384, eur = 6.340, stock = true, nvlink = false, bw = 7.16, family = "rtxpro", gpus = 4 }
    "1A100.22V"        = { vram = 80, eur = 1.472, stock = true, nvlink = true, bw = 2.04, family = "blocked", gpus = 1 }
    "4A100.88V"        = { vram = 320, eur = 5.888, stock = true, nvlink = true, bw = 8.16, family = "blocked", gpus = 4 }
    "1L40S.20V"        = { vram = 48, eur = 1.240, stock = false, nvlink = false, bw = 0.86, family = "blocked", gpus = 1 }
  }

  sku = {
    rtxpro_1 = "1RTXPRO6000.30V"
    rtxpro_2 = "2RTXPRO6000.60V"
    rtxpro_4 = "4RTXPRO6000.120V"
    h200_1   = "1H200.141S.44V"
    h200_2   = "2H200.141S.88V"
    h100_1   = "1H100.80S.30V"
  }

  # ---------------------------------------------------------------------------
  # Hard fleet cap: never more than this many GPUs of each family running at
  # once, regardless of cost. 1x H200 + 1x H100 + 2x RTX PRO 6000 GPUs, total.
  # Enforced by terraform_data.fleet_guard below against every role currently
  # planned (local.roles), summed across ALL nodes in the phase together — so
  # e.g. node_sku=2RTXPRO6000.60V + secondary_node_sku=2RTXPRO6000.60V would
  # use 4 rtxpro GPUs and get refused, even though each SKU alone is in stock.
  # See AGENTS.md for the policy this encodes.
  # ---------------------------------------------------------------------------
  fleet_limit = {
    h200    = 1
    h100    = 1
    rtxpro  = 2
    blocked = 0
  }

  # ---------------------------------------------------------------------------
  # Fallback ladder for the PRIMARY node, best first. `make preflight` walks
  # this and reports the highest rung actually in stock. Only SKUs that fit
  # local.fleet_limit on their own belong here — a rung that already needs the
  # whole family's cap (rtxpro_2, h200_1, h100_1) leaves no room for a
  # same-family secondary node; see phases.p4 below for how the secondary
  # covers that with a different family instead of a second same-size node.
  # B200/B300/GB300 are deliberately ABSENT: out of policy AND out of stock.
  # ---------------------------------------------------------------------------
  node_ladder = [
    local.sku.rtxpro_2, # 192GB, TP=2 PCIe  - the design target, holds full FP8
    local.sku.h200_1,   # 141GB, TP=1       - Int4 or gpt-oss only, see runbook
    local.sku.h100_1,   # 80GB,  TP=1       - tightest fit, Int4/gpt-oss only
  ]

  spot_multiplier = 0.5 # Verda spot is exactly half on-demand

  # Isolated Qwen3.8 attempt profiles. The launch-time selectors map to
  # versioned YAML; artifact and runtime pins are not duplicated in HCL.
  qwen38_model_profile = yamldecode(file("${path.module}/../deploy/models/${var.qwen38_model == "abliterated" ? "qwen38-abliterated-q4" : "qwen38-ud-q4kxl"}.yaml"))
  qwen38_runtime       = yamldecode(file("${path.module}/../deploy/runtimes/llamacpp-cuda.yaml"))
  qwen38_hardware      = yamldecode(file("${path.module}/../deploy/hardware/${var.qwen38_gpu == "rtx" ? "rtxpro6000" : "h200"}.yaml"))
  qwen38_alias         = "qwen38-${local.qwen38_model_profile.id}"
  qwen38_argv = concat(
    [
      "--model", "${local.weights_mount}/qwen38/${local.qwen38_model_profile.id}/${local.qwen38_model_profile.entry_file}",
      "--alias", local.qwen38_alias, "--host", "127.0.0.1", "--port", "8001",
    ],
    local.qwen38_hardware.placement_args,
    [
      "--ctx-size", tostring(131072 * var.qwen38_sessions), "--parallel", tostring(var.qwen38_sessions),
      "--no-kv-unified", "--no-context-shift", "--fit", "off",
      "--flash-attn", "on", "--cache-type-k", "f16", "--cache-type-v", "f16",
      "--batch-size", "512", "--ubatch-size", "128", "--ctx-checkpoints", "2",
      "--cache-ram", "8192", "--jinja", "--metrics", "--slots",
    ]
  )

  # ---------------------------------------------------------------------------
  # Phases.
  #
  # p3 no longer exists as a separate phase. The dress rehearsal is the first
  # hours of p4, on the SAME instances that carry the live exercise, so capacity
  # is acquired ONCE and never re-acquired. Re-acquisition is the failure mode
  # that actually stops an exercise: releasing a node you cannot get back.
  # ---------------------------------------------------------------------------
  phases = {
    off = {}

    qwen38 = {
      primary = {
        role     = "qwen38", sku = local.qwen38_hardware.sku, tp = 1,
        spot     = false, runtime = "llamacpp", model_id = local.qwen38_model_profile.id,
        sessions = var.qwen38_sessions, alias = local.qwen38_alias, serve = true
      }
    }

    p0 = {
      # p0 and p1 intentionally share this identity. Select the SKU, TP and
      # spot mode at launch; changing them later is blocked while it is running.
      primary = { role = "devel", sku = local.phase_node_sku, tp = local.phase_node_tp, spot = var.phase_node_spot, model = var.standby_model, ctx = var.standby_max_model_len, serve = true, wt_gb = var.standby_weights_gb, kv_gb = var.standby_kv_gb }
    }

    p1 = {
      primary = { role = "devel", sku = local.phase_node_sku, tp = local.phase_node_tp, spot = var.phase_node_spot, model = var.standby_model, ctx = var.standby_max_model_len, serve = true, wt_gb = var.standby_weights_gb, kv_gb = var.standby_kv_gb }
    }

    p2 = {
      primary = { role = "bakeoff", sku = var.node_sku, tp = var.node_tp, spot = true, model = var.primary_model, ctx = var.primary_max_model_len, serve = true, wt_gb = var.weights_footprint_gb, kv_gb = var.kv_footprint_gb }
    }

    # Active-active. Both nodes serve; the router load-balances across them.
    # Primary and secondary are DIFFERENT families on purpose: the fleet cap
    # (1x H200 + 1x H100 + 2x RTX PRO 6000 GPUs) leaves no room for two
    # same-family nodes at once, so redundancy comes from mixing families
    # instead of doubling one. Default is node_sku=2x RTX PRO 6000 (full FP8,
    # primary_max_model_len) + secondary_node_sku=1x H200 (standby_model at
    # standby_max_model_len - a single H200 cannot hold full FP8 at full
    # context). Losing the primary degrades to the standby's lower context and
    # Int4 quality rather than stopping; see docs/CAPACITY-RUNBOOK.md.
    p4 = {
      primary   = { role = "live-a", sku = var.node_sku, tp = var.node_tp, spot = false, model = var.primary_model, ctx = var.primary_max_model_len, serve = true, wt_gb = var.weights_footprint_gb, kv_gb = var.kv_footprint_gb }
      secondary = { role = "live-b", sku = var.secondary_node_sku, tp = var.secondary_node_tp, spot = false, model = var.standby_model, ctx = var.standby_max_model_len, serve = true, wt_gb = var.standby_weights_gb, kv_gb = var.standby_kv_gb }
    }

    # Reduced live exercise: 4 teams on ONE RTX PRO 6000 running Int4 at the FULL
    # 128k context. 4 x 128k x 12 KiB = 6 GiB KV against a ~13.9 GiB pool (2.3x
    # headroom) - the same card that could only do 64k for 8 teams. Cheap enough
    # (~EUR 1.6/h) to hold for a whole week, which is the point: capacity is
    # acquired once, days early. No redundancy - a host fault stops the exercise
    # until a replacement is found. max_seqs = 4 teams x router max_parallel_requests 3.
    live4 = {
      primary = { role = "live-a", sku = local.sku.rtxpro_1, tp = 1, spot = false, model = var.standby_model, ctx = var.primary_max_model_len, serve = true, wt_gb = var.standby_weights_gb, kv_gb = var.kv_footprint_gb * var.sessions / 8, max_seqs = 12 }
    }
  }

  active = local.phases[var.phase]

  phase_node_sku = var.phase_node_sku != "" ? var.phase_node_sku : var.node_sku
  phase_node_tp  = var.phase_node_tp != 0 ? var.phase_node_tp : var.node_tp

  roles = merge(
    try(local.active.primary, null) != null ? { node_a = local.active.primary } : {},
    try(local.active.secondary, null) != null ? { node_b = local.active.secondary } : {},
  )

  # ---------------------------------------------------------------------------
  # Fleet cap accounting. Sums GPUs per family across every role in the phase
  # being applied, for terraform_data.fleet_guard to check against fleet_limit.
  # ---------------------------------------------------------------------------
  fleet_gpu_counts = {
    for fam in keys(local.fleet_limit) : fam => sum(concat([0], [
      for k, v in local.roles : local.catalog[v.sku].gpus
      if local.catalog[v.sku].family == fam
    ]))
  }

  # ---------------------------------------------------------------------------
  # Cost. Informational only - there is no monetary ceiling (see
  # variables.tf). Nothing here gates an apply; the binding check is
  # terraform_data.fleet_guard against local.fleet_gpu_counts.
  # ---------------------------------------------------------------------------
  rate       = { for k, v in local.roles : k => local.catalog[v.sku].eur * (v.spot ? local.spot_multiplier : 1) }
  hourly_eur = sum(concat([0], values(local.rate)))

  os_gib      = length(local.roles) * var.os_volume_size_gb
  weights_gib = var.weights_volume_size_gb

  # Storage bills wall-clock, not GPU-hours. The weights volume exists for the
  # whole engagement including while phase="off", so it is charged in full on
  # every phase rather than prorated. OS volumes live only as long as their
  # instance, so they prorate.
  storage_weights_eur = local.weights_gib * var.storage_eur_per_gib_month * (var.volume_retention_days / 30.44)
  storage_os_eur      = local.os_gib * var.storage_eur_per_gib_month * (var.planned_hours / 730)

  # Destroying an instance does NOT delete its OS volume. Orphans silently turn
  # the prorated figure into the full retention figure. Run `make orphans`.
  orphan_risk_eur = local.os_gib * var.storage_eur_per_gib_month * (var.volume_retention_days / 30.44)

  phase_compute_eur = local.hourly_eur * var.planned_hours
  phase_total_eur   = local.phase_compute_eur + local.storage_os_eur

  # Throughput estimate, memory-bandwidth-bound decode.
  agg_bw_tb_s           = sum(concat([0], [for k, v in local.roles : local.catalog[v.sku].bw]))
  nodes_serving         = length([for k, v in local.roles : k if v.serve])
  est_tok_s_total       = local.agg_bw_tb_s * 1e12 * 0.50 / (var.active_params_b * 1e9) * 3.5
  est_tok_s_per_session = local.nodes_serving > 0 ? local.est_tok_s_total / var.sessions : 0

  weights_mount = "/mnt/weights"

  # Keep the instance description stable across p0 -> p1. Phase is tracked in
  # terraform_data.fleet_guard and must not force a running node replacement.
  common_tags_description = "cyber-defence-exercise/${var.project} managed-by=terraform"
}
