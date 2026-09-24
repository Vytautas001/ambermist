# ambermist — context for Codex

AI adversary-simulation platform for a Blue-vs-Red cyber defence exercise:
8 defending teams, an AI red cell running agentic sessions at 64–128k context,
EU-resident inference on a fixed, capped GPU fleet.

## Hard constraints — do not silently violate these
- **No monetary budget limit.** The former €500 ceiling is removed. The binding
  limit is the hardware fleet below: never provision more machines, larger SKUs,
  or other GPU types than it lists. If a change needs more, say so.
- **EU-resident inference.** Provider is Verda (Finland). Don't introduce a US-region
  default. State state (Terraform backend) is deliberately still local — see the ADR.
- **Capacity is the live risk.** Acquire the fleet early and hold it.
  `ops/preflight.py` gates applies. Read `docs/CAPACITY-RUNBOOK.md` before
  touching topology.
- **The harness is orchestration, not weaponisation.** `harness/src/redcell/tools/`
  are range-bound stubs by design. Keep them that way — no real offensive tooling
  lands in this repo. The recon stubs must never fabricate results.
- **Scope is mandatory.** `ExerciseConfig` rejects an empty `in_scope_networks`. The
  adversary system prompt carries scope + authorisation; per-turn injects stay clean
  (per-turn authorisation measurably increases refusals).

## GPU fleet and concurrent-session limits
Only H200, H100 and RTX PRO 6000 are allowed — nothing above H200 (B200, B300,
GB300), and no other family either (A100, L40S, ...). The fleet maximum at any
one time — enforced by `terraform_data.fleet_guard` in `infra/instances.tf`
against `local.fleet_limit` in `infra/locals.tf`:

| Machine | Verda SKU | Count | VRAM | Max concurrent sessions |
|---|---|---:|---:|---:|
| H200 | `1H200.141S.44V` | 1 | 141 GB | 4 |
| H100 | `1H100.80S.30V` | 1 | 80 GB | 1 |
| RTX PRO 6000 | `1RTXPRO6000.30V` ×2, or one `2RTXPRO6000.60V` | 2 GPUs | 2 × 96 GB | 4 (2 per GPU) |
| **Fleet total** | | | | **9** |

A session is one actively generating conversation with its full context resident:
131,072 tokens per slot (129,024 input + 2,048 output), per
`docs/MODEL-DEPLOYMENT-SPEC.md`. The session-count limits are the provisional
starting targets from that spec, not qualified capacity — change them only with
results from `evals/model_capacity.py`.

Because the fleet cap leaves no room for two same-family nodes, the live P4
topology is a primary (2x RTX PRO 6000, full FP8) + standby (1x H200, Int4,
halved context) pair, not two identical nodes — see `phases.p4` in
`infra/locals.tf` and `router/litellm.config.yaml` (fails over, does not
load-balance across them, since they're not equivalent capacity).

Implemented as config, not hard-coded in application code:
- `infra/locals.tf` (`local.catalog` family/gpus fields, `local.fleet_limit`,
  `local.fleet_gpu_counts`) is the one source of truth for the fleet cap.
- `terraform_data.fleet_guard` (`infra/instances.tf`) refuses an apply that
  exceeds it — this replaced `terraform_data.budget_guard`; `budget_eur` and
  the related outputs are gone.
- `ops/preflight.py` (`LADDER`, `OVER_FLEET_CAP`) checks only fleet-fitting
  SKUs and flags anything Verda shows in stock but that's out of policy.
- Session limits belong in the serving layer (llama.cpp `--parallel`, vLLM
  `--max-num-seqs`) and the router (`general_settings.max_parallel_requests`
  per deployment in `router/litellm.config.yaml`) — never allocate more slots
  than a node's limit, and never silently truncate context to fit more in.
- Tests: `infra`'s `tofu validate` plus the fleet_guard precondition checks
  above cover the cap itself; add a harness/eval test if you add a new
  serving-slot or router config path that could silently exceed a limit.

## Environment setup
- Before running Verda/OpenTofu commands or `infra` Make targets, source the
  repository-root `.env` file so credentials are available in the environment:
  `set -a; source .env; set +a`. Never commit `.env` or print its contents.

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

## Completion workflow
- After the requested changes are implemented and verified, commit the related
  changes. Never commit `.env`, credentials, generated state, or unrelated user work.

## Model choice (why, so you don't "helpfully" swap it)
Primary `Qwen/Qwen3.5-122B-A10B` (Apache 2.0). Chosen for a 3:1 linear:full
attention layout that cuts KV cost ~8× vs a dense peer — that's what makes 8×128k
fit one small node. Newer ≠ better here: Qwen3.8-Flash-Next is 173 GB and needs a
multi-GPU tray. Full reasoning in `docs/ARCHITECTURE.md`.
