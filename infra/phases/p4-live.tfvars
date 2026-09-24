# P4 - dress rehearsal AND live exercise, on the SAME instances.
#
# 46 h = 6 h rehearsal + 40 h live, held continuously. Capacity is acquired ONCE.
# This is the whole point: releasing a node between rehearsal and live means
# re-acquiring it on exercise morning, and the Verda console has already shown
# that "no availability" is a real state for real SKUs.
#
# Two nodes, DIFFERENT families (fleet cap leaves no room for two of the same -
# see local.fleet_limit / AGENTS.md): 2x RTX PRO 6000 primary at full FP8/128k,
# 1x H200 standby at Int4/64k. The router fails over, it does not load-balance
# across them (see router/litellm.config.yaml) - normal operation is full
# quality on the primary; losing it degrades to the standby's lower context
# and Int4 quality rather than stopping.
#
# Spot is forbidden here and a lifecycle precondition enforces it.
phase         = "p4"
planned_hours = 46
