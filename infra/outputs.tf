output "phase" {
  description = "Phase currently applied."
  value       = var.phase
}

output "project" {
  description = "Project prefix used for the node's vLLM systemd service."
  value       = var.project
}

output "instances" {
  description = "Per-role instance facts. ip can be null immediately after create; re-run terraform refresh."
  value = {
    for k, v in verda_instance.gpu : k => {
      role            = local.roles[k].role
      id              = v.id
      hostname        = v.hostname
      instance_type   = v.instance_type
      ip              = v.ip
      status          = v.status
      is_spot         = v.is_spot
      tensor_parallel = local.roles[k].tp
      price_per_hour  = v.price_per_hour
      gpu             = try(v.gpu.description, null)
      gpu_memory      = try(v.gpu_memory.size_in_gigabytes, null)
      ssh             = v.ip != null ? "ssh ubuntu@${v.ip}" : "(pending — run: terraform refresh)"
      endpoint        = v.ip != null && local.roles[k].serve ? "http://${v.ip}:8000/v1" : null
    }
  }
}

output "weights_volume" {
  description = "The long-lived shared NVMe volume. Survives every phase transition."
  value = {
    id       = verda_volume.weights.id
    name     = verda_volume.weights.name
    size_gb  = verda_volume.weights.size
    type     = verda_volume.weights.type
    status   = verda_volume.weights.status
    location = verda_volume.weights.location
  }
}

output "mount_commands" {
  description = "Commands Verda returns for mounting the shared volume, per instance. The startup script does this automatically; these are for manual recovery."
  value = {
    for k, v in verda_volume_attachment.weights : k => {
      create_directory = v.create_directory_command
      mount            = v.mount_command
      fstab            = v.filesystem_to_fstab_command
    }
  }
}

output "cost" {
  description = "Cost projection for the applied phase. Informational only - there is no budget ceiling; nothing here gates an apply. See the `fleet` output for the constraint that does."
  value = {
    hourly_eur              = format("%.4f", local.hourly_eur)
    planned_hours           = var.planned_hours
    phase_compute_eur       = format("%.2f", local.phase_compute_eur)
    phase_os_storage_eur    = format("%.2f", local.storage_os_eur)
    phase_total_eur         = format("%.2f", local.phase_total_eur)
    weights_storage_eur     = format("%.2f", local.storage_weights_eur)
    provisioned_storage_gib = local.weights_gib + local.os_gib
    orphan_risk_eur         = format("%.2f", local.orphan_risk_eur)
    est_tok_s_per_session   = format("%.0f", local.est_tok_s_per_session)
    nodes_serving           = local.nodes_serving
  }
}

output "fleet" {
  description = "GPU fleet cap accounting for the applied phase. This is the binding constraint (see terraform_data.fleet_guard); there is no budget ceiling."
  value = {
    gpu_counts_by_family = local.fleet_gpu_counts
    fleet_limit          = local.fleet_limit
  }
}

output "burn_warning" {
  description = "Plain-language reminder of what is currently costing money."
  value       = length(local.roles) == 0 ? "No GPUs running. Only the ${var.weights_volume_size_gb} GB weights volume is billing (~EUR ${format("%.2f", local.weights_gib * var.storage_eur_per_gib_month)}/month)." : "BURNING EUR ${format("%.2f", local.hourly_eur)}/hour across ${length(local.roles)} GPU instance(s). Run 'make off' when the phase ends — instances bill until destroyed."
}
