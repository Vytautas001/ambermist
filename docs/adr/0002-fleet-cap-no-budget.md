# ADR 0002 — Fleet cap replaces the budget ceiling

Status: accepted · Date: 2026-09-24
Supersedes: [ADR 0001](0001-provider-model-topology.md) decision 3 and the
budget parts of its context/consequences.

## Context
The €500 hard ceiling from ADR 0001 is removed at the operator's direction.
In its place: a hard ceiling on hardware, not cost. Never run more than
**1x H200 + 1x H100 + 2x RTX PRO 6000 (GPUs)** at once, and nothing outside
those three families (no B200/B300/GB300, no A100, no L40S), regardless of
price or availability.

That cap makes ADR 0001's topology decision (two identical 2× RTX PRO 6000
nodes = 4 RTX GPUs) impossible: the fleet only ever has 2 RTX PRO 6000 GPUs
available at once. Two same-family nodes of any kind no longer fit.

## Decisions
1. **No monetary budget ceiling.** `terraform_data.budget_guard`, `budget_eur`,
   `spend_to_date_eur` and `misc_reserve_eur` are removed. The `cost` output
   stays as pure information; nothing gates on it any more.
2. **Fleet cap is the new gate.** `local.fleet_limit` (`infra/locals.tf`) and
   `terraform_data.fleet_guard` (`infra/instances.tf`) refuse an apply that
   would run more GPUs of a family than the cap, or any family outside the
   allowlist. `ops/preflight.py`'s `LADDER`/`OVER_FLEET_CAP` mirror it.
3. **P4 topology becomes primary + standby, not two identical nodes.** Primary
   (`node_sku`/`node_tp`, default 2x RTX PRO 6000) runs the full FP8 checkpoint
   at full context; standby (`secondary_node_sku`/`secondary_node_tp`, default
   1x H200) runs the Int4 checkpoint at halved context. This restores what
   ADR 0001's own `phase` variable description already said ("1x B200 +
   1x RTX PRO 6000 standby") but the actual `phases.p4` local block had drifted
   away from — the two had gone out of sync before this ADR.
4. **Router fails over, does not load-balance, between primary and standby.**
   `router/litellm.config.yaml` moved from a duplicate `model_name` (least-busy
   across both) to a `fallbacks` list, because the two nodes are no longer
   equivalent capacity — routing teams evenly across them would silently hand
   some a smaller context and lower-quality completion under normal load, not
   only during a failure.

## Consequences
- No monetary ceiling means a runaway `planned_hours` or an on-demand rate
  increase no longer blocks an apply. Nothing enforces cost discipline any
  more except operator attention to the `cost`/`burn_warning` outputs.
- Redundancy no longer means "the same throughput, halved" — it means a real
  capability downgrade (Int4, 65,536 vs 131,072 context) on failover. White
  Cell messaging in `docs/CAPACITY-RUNBOOK.md` §4 reflects this.
- The primary ladder (`local.node_ladder`) shrank to 3 rungs, all single-family
  allocations, because a same-family second node is no longer an option for
  the fallback path either — see `docs/CAPACITY-RUNBOOK.md` §2.
- `docs/ARCHITECTURE.md`'s narrative (single-B200, €500-budgeted design) is
  now historical background, not the current design; it carries a status note
  pointing here and at `docs/CAPACITY-RUNBOOK.md` / `AGENTS.md` for what's
  actually enforced.
