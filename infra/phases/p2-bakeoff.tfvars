# P2 - model bake-off + long-context validation.  10 h on 2x RTX PRO 6000 (spot).
#
# Deliberately on the SAME SKU as production, not on faster hardware: a bake-off
# that measures throughput on a box you will not use at exercise time measures
# nothing useful.
#
# Three candidates, all Apache/MIT/NVIDIA-open and all fitting 192 GB:
#   Qwen3.5-122B-A10B FP8   125 GB + 12.0 GB KV   agentic benchmarks published
#   Ling-3.0-flash    FP8   124 GB +  3.9 GB KV   top of the medium open table
#   Nemotron-3-Super  FP8   120 GB +  4.0 GB KV   ~3% measured security refusal
#
# MUST verify: prefix caching on the linear/GDN layers. It is the one finding
# that would change the primary model choice.
phase         = "p2"
planned_hours = 10
