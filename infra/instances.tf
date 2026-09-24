# ===========================================================================
# PHASE-SCOPED GPU INSTANCES
#
# verda_instance has no Update path in the provider — the Update method is a
# hard error stub — and nearly every argument is ForceNew. So instances are
# treated as strictly immutable: change anything, get a new instance.
# ===========================================================================

resource "verda_instance" "gpu" {
  for_each = local.roles

  instance_type = each.value.sku
  image         = var.image
  hostname      = "${var.project}-${each.value.role}"

  # description is REQUIRED by the provider, not optional. Easy to miss.
  description = local.common_tags_description

  location = var.location
  is_spot  = each.value.spot

  ssh_key_ids       = [for k in verda_ssh_key.operators : k.id]
  startup_script_id = verda_startup_script.role[each.key].id

  # os_volume is a nested ATTRIBUTE in the provider schema (SingleNestedAttribute),
  # not a block — it takes '=' and an object. Writing it as a bare block fails.
  os_volume = {
    name = "${var.project}-${each.value.role}-os"
    size = var.os_volume_size_gb
    type = "NVMe"

    # Spot only; the provider rejects this policy on on-demand contracts.
    # Without it on spot, an OS volume orphaned by a reclaim keeps billing until
    # someone notices it in the console.
    on_spot_discontinue = each.value.spot ? "delete_permanently" : null
  }

  lifecycle {
    # is_spot is NOT marked ForceNew in the provider schema, so Terraform will
    # happily plan an in-place update for it — which then fails at apply with
    # "Update Not Supported" and leaves you stuck. Force a replace instead.
    replace_triggered_by = [terraform_data.instance_identity[each.key]]

    # p0 and p1 intentionally share one immutable development node. If a
    # future edit accidentally changes its identity, fail before destroying a
    # running machine; p2/p4/live4 remain explicitly replaceable transitions.
    prevent_destroy = contains(["p0", "p1"], var.phase)

    # Older nodes include the phase in their provider description. Do not
    # replace a running node just to normalize that historical metadata.
    ignore_changes = [description]

    precondition {
      condition     = !(startswith(each.value.role, "live") && each.value.spot)
      error_message = "Live nodes must never run on spot. Spot is preemptible with two minutes' notice, and both nodes would likely be reclaimed together."
    }

    precondition {
      condition     = local.catalog[each.value.sku].stock
      error_message = "Instance type ${each.value.sku} was out of stock when this config was written. Run `make preflight` and pick a rung from local.node_ladder that is actually available."
    }

    precondition {
      condition     = local.catalog[each.value.sku].vram * 0.92 >= each.value.wt_gb + each.value.kv_gb
      error_message = "${each.value.sku} has ${local.catalog[each.value.sku].vram} GB VRAM (${format("%.1f", local.catalog[each.value.sku].vram * 0.92)} usable), too little for ${each.value.model}: ${each.value.wt_gb} GB weights + ${each.value.kv_gb} GB KV for ${var.sessions} sessions at ${each.value.ctx}. Move up local.node_ladder, or switch this role to the Int4 checkpoint."
    }
  }
}

# Tracks the fields that ought to be ForceNew but are not, so that changing one
# triggers a replacement rather than an unappliable update.
resource "terraform_data" "instance_identity" {
  for_each = local.roles

  input = {
    sku  = each.value.sku
    spot = each.value.spot
    role = each.value.role
    tp   = each.value.tp
  }
}

# ---------------------------------------------------------------------------
# Attach the shared weights volume to every instance in the phase.
#
# This is also the only resource in the provider that waits for the instance to
# have an IP (waitForInstanceIP, 3-minute timeout) — verda_instance itself does
# no polling and can return with ip = null. So anything that depends on the
# instance actually being reachable should depend on the attachment, not the
# instance.
# ---------------------------------------------------------------------------
resource "verda_volume_attachment" "weights" {
  for_each = local.roles

  instance_id = verda_instance.gpu[each.key].id
  volume_id   = verda_volume.weights.id
}

# ---------------------------------------------------------------------------
# Fleet guard. There is no monetary budget ceiling. Instead this refuses an
# apply that would run more GPUs of a family than the fleet allows - never
# more than 1x H200 + 1x H100 + 2x RTX PRO 6000 (GPUs) at once, and never any
# family outside that allowlist (B200/B300/GB300/A100/L40S all count as
# "blocked", limit 0). See local.fleet_limit and AGENTS.md.
# ---------------------------------------------------------------------------
resource "terraform_data" "fleet_guard" {
  input = {
    phase       = var.phase
    gpu_counts  = local.fleet_gpu_counts
    fleet_limit = local.fleet_limit
  }

  lifecycle {
    precondition {
      condition = alltrue([
        for fam, limit in local.fleet_limit : local.fleet_gpu_counts[fam] <= limit
      ])
      error_message = <<-EOT
        FLEET CAP EXCEEDED — apply refused.

        Phase ${var.phase} would run ${jsonencode(local.fleet_gpu_counts)} GPUs
        against the hard fleet ceiling ${jsonencode(local.fleet_limit)} (1x H200 +
        1x H100 + 2x RTX PRO 6000 GPUs, nothing else). This is a hardware policy,
        not a cost one - there is no budget override for it.

        Reduce node_sku/node_tp or secondary_node_sku/secondary_node_tp to a
        combination that fits, or drop a node. See docs/CAPACITY-RUNBOOK.md.
      EOT
    }
  }
}
