# Model and capacity evaluation

The selected deployment is Qwen3.8 Abliterated Q4_K_M with llama.cpp. Current
clients are building blocks; they do not yet implement the qualification contract
in [MODEL-DEPLOYMENT-SPEC.md](../docs/MODEL-DEPLOYMENT-SPEC.md). Coding tasks are in
[IMPLEMENTATION-HANDOFF.md](../docs/IMPLEMENTATION-HANDOFF.md).

| File | Use | Limitation |
|---|---|---|
| `runner.py` / `compare.py` | Sequential endpoint/model comparisons | Cannot prove concurrent full-context capacity |
| `llama_two_slot_context.py` | Token-counted, two-stream long-context experiment | Fixed alias/count, sequential follow-ups, incomplete automated SLO/correctness gates |
| `check_stub_tool.py` | Structured call parsing and synthetic scope validation | Never executes the requested tool; not a capacity benchmark |
| `model_capacity.py` | Planned general qualification runner | Not implemented; scoped in [the experiment plan](../docs/QWEN38-EXPERIMENT-PLAN.md) |

The old Qwen3.5/Ling/Nemotron bake-off does not choose the new primary. Other
models require their own explicit decision and qualified deployment profile.

## Required qualification

Measure 129,024 input tokens (including template/tools/history), reserving 2,048
output tokens including reasoning: 131,072 per slot. Start N=1 and test within
policy ceilings: H200 4, H100 1, RTX PRO 6000 2 per GPU. Do not silently shorten
contexts, count queued requests as active capacity, or infer one GPU's results
from another's memory size.

Required cases include independent concurrent cold histories, concurrent warm
follow-ups, mixed arrivals, deterministic retrieval/isolation, valid stub tool
arguments, and host-loss recovery. Record usage/overlap, per-session latency and
decode rate, peak GPU/CPU memory, failures, profile hashes, and exact runtime pins.
A memory estimate or successful arithmetic answer is not qualification.

Historical trial procedures are retained in
[the H200 note](../docs/QWEN38-H200-EXPERIMENT.md) and
[the RTX note](../docs/QWEN38-RTX-PRO-EXPERIMENT.md). They do not authorize a live
service interruption or acquisition and are not evidence that a test was run.
Use the current spec for acceptance criteria and the runbook for node handling.
