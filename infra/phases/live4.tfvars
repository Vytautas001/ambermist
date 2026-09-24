# LIVE4 - 4-team variant of P4: rehearsal + live on ONE 1x RTX PRO 6000, held a week.
#
# 168 h = the node is acquired days before the exercise and never released.
# It uses only 1 of the fleet's 2 RTX PRO 6000 GPUs (see local.fleet_limit /
# AGENTS.md), leaving the H200 and H100 free for other phases in parallel.
#
# Int4 at the full 128k context: 68 GB weights + 6 GB KV (4 x 128k x 12 KiB).
# Single node, no redundancy. The router has no NODE_B - point NODE_B_URL at
# the same endpoint as NODE_A_URL.
phase         = "live4"
sessions      = 4
planned_hours = 168
