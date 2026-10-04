# Test tier weights volume. Deliberately NOT prevent_destroy: at ~16 GiB the model is
# minutes to re-download, so the volume is disposable and can follow whichever site has
# the cheapest capacity. The production volume in ../storage keeps its protection.
resource "verda_volume" "model" {
  name                = "ambermist-model-test"
  size                = var.model_volume_size_gib # ForceNew
  type                = "NVMe"                    # block device; NVMe_Shared is NFS
  location            = var.location              # ForceNew
  on_spot_discontinue = "keep_detached"           # a spot reclaim must not take the weights
}
