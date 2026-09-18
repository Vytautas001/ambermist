# P0 - image bake, weight pull, smoke test.  3 h on 1x RTX PRO 6000 (spot).
# Pull every candidate checkpoint ONCE to the persistent volume. Re-downloading
# 125 GB at the start of the live phase is 20+ minutes of billed idle and a
# failure point you do not need on exercise morning.
phase             = "p0"
planned_hours     = 3
spend_to_date_eur = 0
