# LIVE4 - 4-team variant of P4: rehearsal + live on ONE 1x RTX PRO 6000, held a week.
#
# 168 h = the node is acquired days before the exercise and never released.
# Holding is affordable only because the node is ~EUR 1.6/h; 2x H200 held for
# the same week is ~EUR 1,294 and the budget guard refuses it.
#
# Int4 at the full 128k context: 68 GB weights + 6 GB KV (4 x 128k x 12 KiB).
# Single node, no redundancy. The router has no NODE_B - point NODE_B_URL at
# the same endpoint as NODE_A_URL.
phase             = "live4"
sessions          = 4
planned_hours     = 168
spend_to_date_eur = 35.80
