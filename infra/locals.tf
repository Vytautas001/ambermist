locals {
  # ---------------------------------------------------------------------------
  # Instance type identifiers, verified against GET /v1/instance-availability
  # and the Verda deploy console. The provider ships NO data sources, so these
  # must be literals.
  #
  # `stock` records what the console showed when this was written. It is a
  # PLANNING HINT, NOT A GUARANTEE — run `make preflight` before every apply.
  # ---------------------------------------------------------------------------
  catalog = {
    # sku                    vram  eur/gpu/h  stock   nvlink  hbm_tb_s
    "1GB300.32V"       = { vram = 288, eur = 7.733, stock = false, nvlink = true, bw = 8.00 }
    "1B300.30V"        = { vram = 268, eur = 6.724, stock = false, nvlink = true, bw = 8.00 }
    "1B200.30V"        = { vram = 180, eur = 5.532, stock = false, nvlink = true, bw = 8.00 }
    "1H200.141S.44V"   = { vram = 141, eur = 3.728, stock = true, nvlink = true, bw = 4.80 }
    "2H200.141S.88V"   = { vram = 282, eur = 7.456, stock = true, nvlink = true, bw = 9.60 }
    "1H100.80S.30V"    = { vram = 80, eur = 2.841, stock = true, nvlink = true, bw = 3.35 }
    "2H100.80S.80V"    = { vram = 160, eur = 5.682, stock = true, nvlink = true, bw = 6.70 }
    "4H100.80S.176V"   = { vram = 320, eur = 11.364, stock = true, nvlink = true, bw = 13.40 }
    "1RTXPRO6000.30V"  = { vram = 96, eur = 1.585, stock = true, nvlink = false, bw = 1.79 }
    "2RTXPRO6000.60V"  = { vram = 192, eur = 3.170, stock = true, nvlink = false, bw = 3.58 }
    "4RTXPRO6000.120V" = { vram = 384, eur = 6.340, stock = true, nvlink = false, bw = 7.16 }
    "1A100.22V"        = { vram = 80, eur = 1.472, stock = true, nvlink = true, bw = 2.04 }
    "4A100.88V"        = { vram = 320, eur = 5.888, stock = true, nvlink = true, bw = 8.16 }
    "1L40S.20V"        = { vram = 48, eur = 1.240, stock = false, nvlink = false, bw = 0.86 }
  }

  sku = {
    rtxpro_1 = "1RTXPRO6000.30V"
    rtxpro_2 = "2RTXPRO6000.60V"
    rtxpro_4 = "4RTXPRO6000.120V"
    h200_1   = "1H200.141S.44V"
    h200_2   = "2H200.141S.88V"
  }

  # ---------------------------------------------------------------------------
  # Fallback ladder, best first. `make preflight` walks this and reports the
  # highest rung actually in stock. B200 is deliberately ABSENT: it showed no
  # availability, and an exercise must not be planned on hardware you cannot get.
  # ---------------------------------------------------------------------------
  node_ladder = [
    local.sku.rtxpro_2, # 192GB, TP=2 PCIe  - the design target
    local.sku.h200_2,   # 282GB, TP=2 NVLink - faster, ~2.4x the price
    local.sku.rtxpro_4, # 384GB, TP=4 PCIe   - more VRAM, worse scaling
    local.sku.h200_1,   # 141GB, TP=1        - Int4 or gpt-oss only, see runbook
  ]

  spot_multiplier = 0.5 # Verda spot is exactly half on-demand

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

    p0 = {
      primary = { role = "bake", sku = local.sku.rtxpro_1, tp = 1, spot = true, model = var.standby_model, ctx = var.standby_max_model_len, serve = false, wt_gb = var.standby_weights_gb, kv_gb = var.standby_kv_gb }
    }

    p1 = {
      primary = { role = "devel", sku = local.sku.rtxpro_1, tp = 1, spot = true, model = var.standby_model, ctx = var.standby_max_model_len, serve = true, wt_gb = var.standby_weights_gb, kv_gb = var.standby_kv_gb }
    }

    p2 = {
      primary = { role = "bakeoff", sku = var.node_sku, tp = var.node_tp, spot = true, model = var.primary_model, ctx = var.primary_max_model_len, serve = true, wt_gb = var.weights_footprint_gb, kv_gb = var.kv_footprint_gb }
    }

    # Active-active. Both nodes serve; the router load-balances across them.
    # Normal operation gives ~2x the per-session throughput of a single node;
    # losing one degrades to single-node throughput rather than stopping.
    p4 = {
      primary   = { role = "live-a", sku = var.node_sku, tp = var.node_tp, spot = false, model = var.primary_model, ctx = var.primary_max_model_len, serve = true, wt_gb = var.weights_footprint_gb, kv_gb = var.kv_footprint_gb }
      secondary = { role = "live-b", sku = var.node_sku, tp = var.node_tp, spot = false, model = var.primary_model, ctx = var.primary_max_model_len, serve = true, wt_gb = var.weights_footprint_gb, kv_gb = var.kv_footprint_gb }
    }
  }

  active = local.phases[var.phase]

  roles = merge(
    try(local.active.primary, null) != null ? { node_a = local.active.primary } : {},
    try(local.active.secondary, null) != null ? { node_b = local.active.secondary } : {},
  )

  # ---------------------------------------------------------------------------
  # Cost
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
  projected_eur     = var.spend_to_date_eur + local.phase_total_eur + local.storage_weights_eur + var.misc_reserve_eur

  # Throughput estimate, memory-bandwidth-bound decode.
  agg_bw_tb_s           = sum(concat([0], [for k, v in local.roles : local.catalog[v.sku].bw]))
  nodes_serving         = length([for k, v in local.roles : k if v.serve])
  est_tok_s_total       = local.agg_bw_tb_s * 1e12 * 0.50 / (var.active_params_b * 1e9) * 3.5
  est_tok_s_per_session = local.nodes_serving > 0 ? local.est_tok_s_total / 8 : 0

  weights_mount = "/mnt/weights"

  common_tags_description = "cyber-defence-exercise/${var.project} phase=${var.phase} managed-by=terraform"
}
