# ambermist — context for Claude Code

AI adversary-simulation platform for a Blue-vs-Red cyber defence exercise:
8 defending teams, an AI red cell running agentic sessions at 64–128k context,
EU-resident inference, hard €500 budget.

## Hard constraints — do not silently violate these
- **Budget: €500.** `infra/` has a `terraform_data.budget_guard` precondition that
  refuses an over-budget apply. Don't route around it; if a change needs more, say so.
- **EU-resident inference.** Provider is Verda (Finland). Don't introduce a US-region
  default. State state (Terraform backend) is deliberately still local — see the ADR.
- **Capacity is the live risk.** B200 was out of stock, so the design runs on the
  most-available SKU (2× RTX PRO 6000) and holds it. `ops/preflight.py` gates applies.
  Read `docs/CAPACITY-RUNBOOK.md` before touching topology.
- **The harness is orchestration, not weaponisation.** `harness/src/redcell/tools/`
  are range-bound stubs by design. Keep them that way — no real offensive tooling
  lands in this repo. The recon stubs must never fabricate results.
- **Scope is mandatory.** `ExerciseConfig` rejects an empty `in_scope_networks`. The
  adversary system prompt carries scope + authorisation; per-turn injects stay clean
  (per-turn authorisation measurably increases refusals).

## Layout
- `infra/` — Terraform/OpenTofu root module. `make preflight`, `make p0..p4`, `make off`.
- `harness/` — the `redcell` Python package: client, agent loop, tools, audit. `pytest`.
- `router/` — LiteLLM config (per-team keys, failover across the two nodes).
- `evals/` — the P2 bake-off runner (Qwen3.5 vs Ling vs Nemotron at 64k/128k).
- `ops/preflight.py` — capacity checker against the public availability API.
- `docs/` — ARCHITECTURE.md (full design), CAPACITY-RUNBOOK.md, ADRs.

## Verify before committing
- infra: `cd infra && tofu fmt -check -recursive && tofu init -backend=false && tofu validate`
- harness: `cd harness && ruff check src tests && pytest -q`

## Model choice (why, so you don't "helpfully" swap it)
Primary `Qwen/Qwen3.5-122B-A10B` (Apache 2.0). Chosen for a 3:1 linear:full
attention layout that cuts KV cost ~8× vs a dense peer — that's what makes 8×128k
fit one small node. Newer ≠ better here: Qwen3.8-Flash-Next is 173 GB and needs a
multi-GPU tray. Full reasoning in `docs/ARCHITECTURE.md`.
