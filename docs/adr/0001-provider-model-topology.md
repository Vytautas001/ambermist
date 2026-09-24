# ADR 0001 — Provider, model and topology

Status: accepted · Date: 2026-09-18

> **Superseded in part by [ADR 0002](0002-fleet-cap-no-budget.md) (2026-09-24):**
> the €500 budget ceiling (decision context, consequence 1) is removed, and the
> topology (decision 3) is no longer two identical RTX PRO 6000 nodes — it's a
> primary + standby pair on different hardware families under a fixed fleet cap.
> Kept as-is below for the historical record; do not treat the budget or
> two-identical-nodes topology as current.

## Context
8 Blue Teams, agentic red cell at 64–128k context, EU-resident inference, €500 cap,
short-lived exercise. Live capacity is scarce: Verda showed B200 out of stock.

## Decisions
1. **Provider: Verda (Finland).** EU-resident, ISO 27001, cheapest B200 on the
   market when available; AWS's sovereign region has no GPUs and its GPU regions
   are US-parented. See ARCHITECTURE.md §2.
2. **Model: Qwen3.5-122B-A10B (Apache 2.0), FP8.** 3:1 linear:full attention →
   ~8× less KV than a dense peer → 8×128k fits one small node. Ling-3.0-flash and
   Nemotron-3-Super are P2 bake-off alternates.
3. **Topology: two active-active 2× RTX PRO 6000 nodes**, not one B200. The
   most-available SKU; losing one node degrades throughput rather than stopping.
4. **Capacity held, not re-acquired.** Rehearsal and live are one phase on the
   same instances. `ops/preflight.py` + `local.catalog[].stock` preconditions.

## Consequences
- Fits €500 with ~€100 headroom; ~156 tok/s/session normal, ~78 degraded.
- Terraform state is local for now (single applier) — revisit when the team grows.
- If the SKU is gone at T-0, walk the fallback ladder in CAPACITY-RUNBOOK.md.
