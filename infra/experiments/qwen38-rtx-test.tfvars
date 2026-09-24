# WARNING: this file only sizes the node (1x RTX PRO 6000, p0). With
# phases/p0-bake.tfvars the node boots the legacy Qwen3.5 standby model on vLLM.
# Qwen3.8 is started manually per docs/QWEN38-RTX-PRO-EXPERIMENT.md; it is not
# the target infrastructure path (docs/IMPLEMENTATION-HANDOFF.md).
# Qwen3.8 temporary test target. Combine after phases/p0-bake.tfvars.
# No budget ceiling to supply - `make fleet` / the `fleet` output shows
# whether this fits the fleet cap alongside whatever else is running.
phase           = "p0"
phase_node_sku  = "1RTXPRO6000.30V"
phase_node_tp   = 1
phase_node_spot = false
planned_hours   = 4
