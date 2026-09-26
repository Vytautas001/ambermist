data "terraform_remote_state" "storage" {
  backend = "local"
  config  = { path = "${path.module}/../storage/terraform.tfstate" }
}

locals {
  model_volume_id = data.terraform_remote_state.storage.outputs.model_volume_id
  location        = data.terraform_remote_state.storage.outputs.location
  boot            = templatefile("${path.module}/boot.sh.tftpl", { admin_cidrs = var.admin_cidrs })
}

# is_spot and the script body aren't ForceNew: any change here means a new instance.
resource "terraform_data" "identity" {
  input = { sku = var.instance_type, spot = var.use_spot, boot = sha256(local.boot) }
}

resource "verda_ssh_key" "op" {
  for_each   = var.ssh_public_keys
  name       = "${var.project}-${each.key}"
  public_key = each.value
}

resource "verda_startup_script" "boot" {
  name   = "${var.project}-boot"
  script = local.boot # plaintext in the API and state: no secrets

  lifecycle {
    replace_triggered_by = [terraform_data.identity]
  }
}

resource "verda_instance" "node" {
  instance_type     = var.instance_type
  image             = var.image
  hostname          = "${var.project}-h200"
  description       = "project=${var.project};stack=inference;owner=${var.owner};managed-by=opentofu"
  location          = local.location
  is_spot           = var.use_spot
  ssh_key_ids       = [for k in verda_ssh_key.op : k.id]
  startup_script_id = verda_startup_script.boot.id

  os_volume = {
    name                = "${var.project}-h200-os"
    size                = var.os_volume_size_gib
    type                = "NVMe"
    on_spot_discontinue = var.use_spot ? "delete_permanently" : null
  }

  lifecycle {
    replace_triggered_by = [terraform_data.identity]
    ignore_changes       = [description]
  }
}

# The only resource that waits for the instance to be reachable.
resource "verda_volume_attachment" "model" {
  instance_id = verda_instance.node.id
  volume_id   = local.model_volume_id
}
