resource "verda_volume" "model" {
  for_each            = var.locations
  name                = "ambermist-model"
  size                = var.model_volume_size_gib # ForceNew
  type                = "NVMe"                    # block device; NVMe_Shared is NFS
  location            = each.key                  # ForceNew
  on_spot_discontinue = "keep_detached"           # a spot reclaim must not take the weights

  lifecycle {
    prevent_destroy = true
    # Import leaves both null and both are ForceNew; neither can change in place anyway.
    ignore_changes = [location, on_spot_discontinue]
  }
}

moved {
  from = verda_volume.model
  to   = verda_volume.model["FIN-02"]
}
