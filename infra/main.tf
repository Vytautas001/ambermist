# ===========================================================================
# LONG-LIVED RESOURCES
# These survive every phase transition. Applying phase="off" destroys all GPUs
# but leaves these intact, which is what makes the phase split cheap.
# ===========================================================================

resource "verda_ssh_key" "operators" {
  for_each = var.ssh_public_keys

  name       = "${var.project}-${each.key}"
  public_key = each.value
}

# ---------------------------------------------------------------------------
# Shared NVMe volume holding the model weights.
#
# Type is NVMe_Shared, not NVMe: during phase p4 the RTX PRO 6000 primary and
# the H200 standby are both up and both need the same checkpoints. A plain
# NVMe volume attaches to one instance at a time.
#
# size is ForceNew. Growing this volume DESTROYS THE DATA and means re-pulling
# ~300GB of weights. prevent_destroy is deliberate: it turns an accidental
# `terraform destroy` into a loud error instead of a silent 40-minute setback.
# ---------------------------------------------------------------------------
resource "verda_volume" "weights" {
  name     = "${var.project}-weights"
  size     = var.weights_volume_size_gb
  type     = "NVMe_Shared"
  location = var.location

  # Undocumented in the registry docs but present in the provider schema.
  # Without it, a spot reclaim can take the volume with it.
  on_spot_discontinue = "keep_detached"

  lifecycle {
    prevent_destroy = true
  }
}

# ---------------------------------------------------------------------------
# Startup scripts.
#
# SECURITY: script bodies are stored in the Verda API in plaintext and appear
# in Terraform state. Nothing secret goes in here. The scripts read secrets
# from /mnt/weights/.env, which an operator writes once over SSH during p0
# and which lives only on the encrypted volume.
# ---------------------------------------------------------------------------
resource "verda_startup_script" "role" {
  for_each = local.roles

  lifecycle {
    # Verda does not support updating startup scripts. Phase changes alter the
    # role, model, location, or serving settings, so replace the script with
    # the corresponding immutable instance identity.
    replace_triggered_by = [terraform_data.instance_identity[each.key]]
  }

  name = "${var.project}-${each.value.role}"
  script = try(each.value.runtime, "vllm") == "llamacpp" ? templatefile("${path.module}/scripts/startup-llamacpp.sh.tftpl", {
    model_id                 = local.qwen38_model_profile.id
    model_repository         = local.qwen38_model_profile.repository
    model_revision           = local.qwen38_model_profile.revision
    model_entry_file         = local.qwen38_model_profile.entry_file
    model_shards             = local.qwen38_model_profile.shards
    model_size_bytes         = local.qwen38_model_profile.size_bytes
    runtime_commit           = local.qwen38_runtime.source_commit
    build_image              = local.qwen38_runtime.build_image
    build_digest             = local.qwen38_runtime.build_image_digest
    cuda_arch                = local.qwen38_hardware.cuda_arch
    cmake_args               = local.qwen38_runtime.cmake_args_by_arch[tostring(local.qwen38_hardware.cuda_arch)]
    sku                      = local.qwen38_hardware.sku
    argv                     = local.qwen38_argv
    sessions                 = each.value.sessions
    alias                    = each.value.alias
    weights_mount            = local.weights_mount
    placement_is_provisional = local.qwen38_hardware.provisional
    }) : templatefile("${path.module}/scripts/startup.sh.tftpl", {
    project           = var.project
    role              = each.value.role
    location          = var.location
    weights_mount     = local.weights_mount
    vllm_image        = var.vllm_image
    served_model_name = var.served_model_name
    model             = each.value.model
    max_model_len     = each.value.ctx
    serve             = each.value.serve
    tensor_parallel   = each.value.tp
    # Batch sizing differs by role: the primary/live node (var.node_sku, e.g.
    # 2x RTX PRO 6000) has the larger KV pool; the bake/devel standby (a single
    # RTX PRO 6000 running Int4) has only ~13.9 GiB. A role may pin its own.
    max_num_seqs       = try(each.value.max_seqs, each.value.sku == var.node_sku ? 16 : 10)
    max_batched_tokens = each.value.sku == var.node_sku ? 8192 : 4096
    gpu_memory_util    = each.value.sku == var.node_sku ? "0.92" : "0.90"
  })
}
