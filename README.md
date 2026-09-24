# ambermist

AI adversary-simulation platform for an eight-team Blue-vs-Red cyber defence
exercise. EU-resident inference on Verda in Finland, a fixed fleet of one H200,
one H100, and two RTX PRO 6000 GPUs, and no monetary budget ceiling.

The selected target is
`windowsxp811203/Qwen3.8-Flash-Next-Abliterated-GGUF` (Q4_K_M) with llama.cpp.
Each full-context slot reserves 129,024 input + 2,048 output tokens. The fleet's
4+1+2+2 provisional session ceilings are **not measured capacity**.

## Design and coding entry points

- [Architecture](docs/ARCHITECTURE.md): target topology, constraints, and tradeoffs.
- [Deployment specification](docs/MODEL-DEPLOYMENT-SPEC.md): artifact pins, launch
  contract, reusable profiles, and capacity qualification.
- [Implementation handoff](docs/IMPLEMENTATION-HANDOFF.md): ordered work for the
  next coding LLM, including concrete gaps and acceptance criteria.
- [AGENTS.md](AGENTS.md): shared instructions for GPT/Codex, Claude, and other
  coding assistants. `CLAUDE.md` points to the same instructions.
- [Capacity runbook](docs/CAPACITY-RUNBOOK.md): inventory, acquisition, recovery,
  and retention of held machines.

## Current implementation

The architecture review updated documentation only. Current executable defaults
still serve Qwen3.5 with vLLM and a legacy primary/standby router. Portable
profiles, `ops/model_deploy.py`, and `evals/model_capacity.py` are planned, not
implemented. Running existing phase targets does not deploy the selected GGUF.

| Path | Current role |
|---|---|
| `infra/` | Legacy phased infrastructure, persistent storage, and planned-role fleet guard |
| `harness/` | Client, agent loop, scoped stub tools, and team-attributable audit |
| `router/` | Legacy LiteLLM configuration and team-key provisioning; admission migration needed |
| `evals/` | Sequential comparison, two-slot trial, and stub parsing clients |
| `ops/` | Availability checks, node secrets, serving smoke checks, and cleanup |
| `docs/` | Current design/spec/handoff, runbook, historical experiments, and ADRs |

Existing H200/Qwen3.5 smoke-check evidence is recorded in
[infra/README.md](infra/README.md). It does not qualify Qwen3.8 or eight concurrent
full-context conversations. No such qualification is claimed by this review.

## Local development

```bash
cd harness
pip install -e ".[dev]"
cp exercise.example.yaml exercise.yaml  # set authorised scope and endpoint
redcell -c exercise.yaml prompt
ruff check src tests
pytest -q
```

Before any Verda/OpenTofu command or infra Make target, source the repository-root
`.env` with `set -a; source .env; set +a` without printing its contents. Read the
runbook before provider work and preserve held GPU instances and protected data.

This repository contains orchestration and evaluation, with range-bound stub
tools. Model selection does not change scope validation or authorize adding real
offensive tooling.
