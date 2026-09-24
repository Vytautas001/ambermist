# P1 - Red Cell harness + prompt development.  21 h on 1x H200 (on-demand).
# On-demand keeps the development instance stable while the harness is exercised. Runs the
# Int4 checkpoint, which is also the emergency single-GPU fallback, so that
# config gets 21 hours of real use before anyone has to trust it.
phase         = "p1"
planned_hours = 21
