# P4 - dress rehearsal AND live exercise, on the SAME instances.
#
# 46 h = 6 h rehearsal + 40 h live, held continuously. Capacity is acquired ONCE.
# This is the whole point: releasing a node between rehearsal and live means
# re-acquiring it on exercise morning, and the Verda console has already shown
# that "no availability" is a real state for real SKUs.
#
# Two nodes, both serving, router load-balancing across them:
#   normal      ~156 tok/s per session across 8 teams
#   one node lost ~78 tok/s per session - degraded, not stopped
#
# Spot is forbidden here and a lifecycle precondition enforces it: both nodes
# sit in the same pool and would likely be reclaimed together.
phase             = "p4"
planned_hours     = 46
spend_to_date_eur = 35.80
