resource "verda_volume" "model" {
  name                = "ambermist-model"
  size                = var.model_volume_size_gib # ForceNew
  type                = "NVMe"                    # block device; NVMe_Shared is NFS
  location            = var.location              # ForceNew
  on_spot_discontinue = "keep_detached"           # a spot reclaim must not take the weights

  lifecycle {
    prevent_destroy = true
  }
}
