output "instance_id" {
  value = verda_instance.node.id
}

# May be null right after create: run `tofu apply -refresh-only` until it is set.
output "public_ip" {
  value      = verda_instance.node.ip
  depends_on = [verda_volume_attachment.model]
}

output "ssh" {
  value = "ssh root@${coalesce(verda_instance.node.ip, "<ip pending>")}"
}

output "burn_warning" {
  value = "The H200 is billing until you run: tofu -chdir=infra/compute destroy"
}
