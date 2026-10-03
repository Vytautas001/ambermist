output "model_volume_ids" {
  value = { for site, v in verda_volume.model : site => v.id }
}
