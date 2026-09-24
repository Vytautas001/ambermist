# ambermist — shared instructions for coding assistants

These instructions apply to GPT/Codex, Claude, and other coding LLMs. The latest
user request determines the task scope. A documentation review or design handoff
is not a request to implement code, provision machines, or switch a live service.

## Read first

1. [Architecture](docs/ARCHITECTURE.md): current target and constraints.
2. [Deployment specification](docs/MODEL-DEPLOYMENT-SPEC.md): artifact, profiles,
   context contract, serving behavior, and qualification.
3. [Implementation handoff](docs/IMPLEMENTATION-HANDOFF.md): ordered coding tasks,
   known implementation gaps, and acceptance criteria.
4. [Capacity runbook](docs/CAPACITY-RUNBOOK.md): required before topology work.

Status as of 2026-09-24: the Qwen3.8 target is selected; implementation and capacity
qualification are pending. Existing Qwen3.5/vLLM defaults are legacy behavior,
not the desired new model. Planned paths in the spec/handoff do not exist yet.

## Hard constraints

- **No monetary budget ceiling.** The hardware fleet below is the binding limit.
  Do not add GPUs, larger SKUs, or other families to make the design fit.
- **EU-resident inference.** Verda, Finland. No US-region or external hosted-model
  fallback. Keep exercise prompts, outputs, and audit data in the EU. Terraform
  state remains local; [ADR 0001](docs/adr/0001-provider-model-topology.md) records
  that decision.
- **Preserve held capacity.** Read the runbook before acquisition or replacement.
  Model/runtime changes must not recreate an instance or protected weights volume.
- **Orchestration only.** `harness/src/redcell/tools/` remains range-bound stubs.
  Do not add offensive tooling; recon stubs must never fabricate observations.
- **Scope is mandatory.** Reject empty `in_scope_networks`. Keep role, scope, and
  authorisation in the system prompt; do not prepend them to every user turn.
- **Do not invent evidence.** Distinguish selected design, implemented behavior,
  recorded smoke checks, and measured qualification. Report unmet requirements.

## Fleet and session policy

| GPU | Allowed SKU | Maximum fleet count | Nominal VRAM | Provisional session ceiling |
|---|---|---:|---:|---:|
| H200 | `1H200.141S.44V` | 1 | 141 GB | 4 |
| H100 | `1H100.80S.30V` | 1 | 80 GB | 1 |
| RTX PRO 6000 | `1RTXPRO6000.30V` ×2 or `2RTXPRO6000.60V` ×1 | 2 GPUs | 96 GB each | 2 per GPU; 4 total |
| **Total** | | **4 GPUs** | | **9 provisional** |

Only these families/SKUs are allowed. No B200/B300/GB300, A100, L40S, or larger
same-family allocations. Two single-GPU RTX nodes fit; two dual-GPU RTX nodes do
not. Count every held node, including experiments, other states, and replacement
overlap. `local.fleet_limit` in `infra/locals.tf` is the implemented count source;
`terraform_data.fleet_guard` checks planned roles, not the whole provider account.

Each slot reserves **131,072 tokens = 129,024 input + 2,048 output**, including
reasoning in output and template/tools/history in input. Do not truncate or shrink
context to increase concurrency. A 64k workload needs a separate explicit profile.

Start isolated qualification at one slot. Serving and router limits must agree:
`admitted_sessions = min(policy_ceiling, qualified_sessions)`. No matching
qualification means no normal traffic. Current 4/1/2 ceilings are not benchmarks;
raise them only through an explicit session-policy revision and measured evidence.
Do not exceed them in this implementation scope. Keep the hardware cap unchanged.

The target is independent replicas of the same Qwen3.8 artifact/context: H200,
H100, and two RTX replicas. A dual-RTX host may run two GPU-isolated processes
only after combined host-resource qualification. Nine ceiling slots do not
provide eight-team N+1 failover; H200 loss leaves at most five. Queue/pause excess
work without losing history. Qwen3.5/64k is explicit rollback, not automatic
fallback for a Qwen3.8/128k conversation.

## Selected model and runtime

- Repository: `windowsxp811203/Qwen3.8-Flash-Next-Abliterated-GGUF`.
- Artifact: `Qwen3.8-Flash-Next-Abliterated-Q4_K_M.gguf` at
  `c3365c410baa29bdd3d7cc8cbc2bf9bee0de2f3a`; checksum in the deployment spec.
- Runtime target: pinned llama.cpp CUDA with verified `qwen4exp` compatibility.
  Initial placement: PLE tensor on CPU, transformer layers and F16 caches on GPU.
- Preserve tokenizer/template, artifact hash, license, runtime source/image pins,
  and measured resource allocations in the resolved deployment record.
- Qwen Community License 1.0 replaces the old Apache-only rationale. Record the
  organiser's license assessment for participant service; do not assume clearance.
- Do not substitute the base model, another abliterated publisher, another quant,
  or the old Qwen3.5 primary without an explicit model decision.

## Implementation rules

Use the handoff's ordered tasks when asked to code. Keep model/runtime settings
in validated versioned profiles and generated manifests, not model-name branches
in Terraform. Preserve the Qwen3.5 service as a rollback profile during migration.
Reject unknown profile keys, missing immutable pins, unsupported combinations,
insufficient resources, and stale qualification. Keep secrets out of profiles.

Naming and configuration: public model aliases follow `redcell-<model>`:
`redcell-qwen38` (selected) and `redcell-qwen35` (explicit rollback). The legacy
`redcell-adversary` alias is retired; replace it in the harness default, router,
key issuance, ops checks, and docs. `.env` holds credentials and per-deployment
values in the order of [.env.example](.env.example); pins, context, and session
limits stay in versioned profiles. Do not rename a `VLLM_*`/`NODE_*` variable
without migrating every reader and the node's `/mnt/weights/.env` in one change.

Session enforcement belongs in the server (`--parallel` / `--max-num-seqs`) and
router admission per backend and per team. A global/per-key LiteLLM setting alone
does not enforce backend capacity. Verify the pinned router's actual semantics.
The legacy limits 16/10/12 and three requests per team are migration defects.
Add meaningful tests for any serving-slot or router path that could exceed a cap.

Do not run commands merely because they appear in an experiment document. Those
files contain historical procedures, not authorisation to interrupt the service.

## Environment and verification

Before Verda/OpenTofu commands or infra Make targets, from the repository root:

```bash
set -a
source .env
set +a
```

Never print or commit `.env`, credentials, generated state, or secret-bearing
outputs. Node credentials use the existing root-owned mode-0600 environment file
and SSH provisioning; never place keys in argv or Terraform.

For infra changes, with the root `.env` sourced:

```bash
cd infra
tofu fmt -check -recursive
tofu init -backend=false
tofu validate
```

For harness changes:

```bash
cd harness
ruff check src tests
pytest -q
```

Run relevant offline tests for new deployment/router/eval logic. These checks do
not qualify GPU capacity. Documentation-only work needs consistency, local-link,
and `git diff --check` review; it does not need a provider apply or GPU benchmark.

## Completion workflow

After requested changes are implemented and verified, commit only related work.
Never commit secrets, generated state, or unrelated user changes. Report what
changed, what was checked, and what remains unimplemented or unqualified.
