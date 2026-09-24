# ambermist

AI adversary-simulation platform for a Blue-vs-Red cyber defence exercise.
8 Blue Teams · agentic red cell at 64–128k context · EU-resident inference ·
fixed GPU fleet cap (1x H200 + 1x H100 + 2x RTX PRO 6000), no budget ceiling.

> Full design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) ·
> Capacity procedure: [`docs/CAPACITY-RUNBOOK.md`](docs/CAPACITY-RUNBOOK.md)

Portable model deployment specification:
[`docs/MODEL-DEPLOYMENT-SPEC.md`](docs/MODEL-DEPLOYMENT-SPEC.md) covers Qwen3.8
Abliterated on H200, H100, and RTX PRO 6000, reusable deployment profiles, and
measuring comfortable concurrency at 126k input. Capacity figures are provisional.

## Layout

| Path | What |
|---|---|
| `infra/` | Terraform/OpenTofu — primary + standby vLLM nodes (different families), weights volume, phased lifecycle, fleet cap guard |
| `harness/` | `redcell` Python package — the AI adversary: client, agent loop, tools, audit log |
| `router/` | LiteLLM — per-team keys, per-key concurrency caps, failover across both nodes |
| `evals/` | P2 model bake-off — Qwen3.5 vs Ling vs Nemotron at 64k and 128k |
| `ops/` | `preflight.py` — capacity checker against Verda's public availability API |
| `docs/` | architecture, capacity runbook, ADRs |

## Quickstart

```bash
cp .env.example .env            # fill in Verda + HF credentials
cd infra && make preflight      # is the target GPU in stock right now?
```

Exercise phases (see `infra/` and the runbook):

```
make p0   # pull weights to the persistent volume (once)
make p1   # 21h  Red Cell builds the harness (this repo's harness/)
make p2   # 10h  model bake-off (evals/)
make p4   # 46h  rehearsal + live, primary + standby held continuously
make off  # only after the exercise; also removes detached redcell OS volumes
```

Harness:

```bash
cd harness
pip install -e ".[dev]"
cp exercise.example.yaml exercise.yaml     # set scope + authorisation
redcell -c exercise.yaml prompt            # inspect the rendered adversary prompt
redcell -c exercise.yaml health
redcell -c exercise.yaml run --team blue-team-01 --objective "..."
pytest -q
```

## Status

Scaffold. `infra/`, `ops/preflight.py` and the docs are complete and verified.
`harness/`, `router/` and `evals/` are working skeletons with passing tests —
the tool implementations (`harness/src/redcell/tools/`) are deliberately
range-bound stubs for the Red Cell to bind during phase P1.

## Security posture

This repository is an orchestration and evaluation layer. It contains no
offensive tooling. Effects on the exercise range are produced by the Red Cell's
own sanctioned tools, in-scope, under the recorded rules of engagement. See
`CLAUDE.md` and `harness/src/redcell/tools/__init__.py`.
