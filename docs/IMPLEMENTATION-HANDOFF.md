# Implementation handoff for the next coding LLM

Status: backlog, not implemented. Prepared 2026-09-24 for GPT/Codex or another
coding assistant. This file describes the coding work deferred by the architecture
review; it does not itself request a deployment or a service interruption.

Read [AGENTS.md](../AGENTS.md), [ARCHITECTURE.md](ARCHITECTURE.md), and
[MODEL-DEPLOYMENT-SPEC.md](MODEL-DEPLOYMENT-SPEC.md) first. Read the
[capacity runbook](CAPACITY-RUNBOOK.md) before topology changes. The latest user
request controls which part of this backlog to execute.

## 1. Outcome and invariants

Implement portable, reversible serving of the selected Qwen3.8 Abliterated Q4_K_M
GGUF with llama.cpp on the existing capped Verda fleet. Qualify each replica,
then route eight full-context team conversations only if measurements support it.

Keep these invariants throughout implementation:

- Fleet: at most one H200, one H100, and two RTX PRO 6000 GPUs, using the exact
  allowed SKUs. No monetary ceiling. No additional GPUs for migration overlap.
- Finland inference; EU prompt/output/audit storage; local Terraform state.
- One slot: 129,024 input + 2,048 output = 131,072 total tokens. No silent context
  shrinking, truncation, or dropping history on failure.
- Provisional ceilings: H200 4, H100 1, RTX 2 per GPU. Enforce the smaller of
  policy and matching measured qualification; unknown qualification gets no
  normal traffic. Tests above these ceilings are outside this scope.
- Preserve held machine identity, protected volumes, and explicit Qwen3.5 rollback.
- Keep tools as range-bound stubs and scope nonempty. No offensive tooling.
- Secrets never enter profiles, locks, argv, state, test fixtures, or reports.

## 2. Current implementation gaps

These findings come from repository inspection, not a live infrastructure audit.

| Area | Current behavior | Required change |
|---|---|---|
| `infra/locals.tf` | Phase map exposes at most `node_a`/`node_b`; P4 is dual-RTX FP8 + H200 Int4; `live4` sets 12 sequences | Model independent inventory/replicas; preserve cap and held identities; remove stale capacity assumptions |
| `infra/main.tf` | Templates model/runtime into immutable startup scripts; defaults to 16 or 10 sequences | Separate machine acquisition from mutable serving manifests; derive slots from qualification |
| `infra/instances.tf` | Fleet guard counts planned roles; VRAM check is nominal GB × 0.92 against Qwen3.5 estimates | Preserve role guard; also check provider-wide held/replacement totals and byte-based placement requirements |
| `infra/variables.tf`, phase/experiment tfvars | Qwen3.5 model defaults and smaller-context fallback; `p3` is accepted but has no phase map | Migrate coherently; resolve phase validation mismatch without releasing capacity |
| `infra/scripts/startup.sh.tftpl` | vLLM-specific launch and NFS-backed weights mount | Generic bootstrap and engine adapters; preserve mount/secret contracts |
| `ops/preflight.py` | Old FP8 preference ladder; explicit `--target-sku` branch checks stock but does not enforce the complete hardware policy | Validate exact allowlist/count/site for every acquisition path; never treat stock as policy approval |
| `ops/provision_node_secrets.py`, `ops/check_inference.py`, infra outputs/Makefile | Assume vLLM unit, key/alias, and at most existing node roles | Runtime-neutral service/health/secret targets for all replicas |
| `router/litellm.config.yaml`, `router/issue-team-keys.sh` | Two vLLM entries; automatic smaller-context fallback; three concurrent requests per key | Same-artifact/context replicas, per-backend admission, one active request per team initially, distinct rollback alias |
| `harness/src/redcell/client.py`, `config.py`, `loop.py` | Existing alias, timeout/output/history/retry contracts need inspection | Support explicit model identity, token contract, recovery and queue behavior; preserve stubs and audit |
| `evals/llama_two_slot_context.py` | Two fixed sessions and alias; sequential follow-ups; incomplete SLO/correctness gates | General capacity evaluator; fix exception path using unset/stale `follow` result |
| `evals/runner.py` | Sequential legacy model comparison | Keep as optional comparison; never use as concurrent-capacity proof |

`deploy/`, `ops/model_deploy.py`, and `evals/model_capacity.py` below are proposed
paths. Create them during coding; do not claim they already work.

## 3. Ordered work packages

### A. Profile schemas and deterministic planning

Create versioned model/runtime/hardware/workload inputs under `deploy/`, a typed
resolver, and a resolved record with input hashes, exact argv, placement, pins,
policy ceiling, and qualification reference. See spec sections 3, 6, and 8.

Use the pinned repository, full revision, file bytes, and SHA-256 in the spec.
Resolve and verify an immutable llama.cpp commit and image per required CUDA
architecture. The runtime pin is intentionally pending; do not fabricate it.
Include the Qwen3.5 GPTQ-Int4/vLLM rollback profile with its own context contract.

Acceptance:

- Offline planning rejects unknown keys, invalid units/counts, absent hashes,
  unsupported engine/artifact/GPU combinations, and secret-bearing fields.
- Generated argv is a list of arguments; profile strings are never shell code.
- A profile change invalidates old qualification. Candidate/qualified/failed
  status is explicit; candidates can run isolated tests, not normal traffic.
- A second model can be expressed without model-specific Terraform branches.

### B. Preserve inventory and acquire only allowed capacity

Refactor inventory and phase handling to describe H200, H100, and two RTX
replicas. Support two single-RTX hosts or one dual-RTX host with two isolated
processes. Preserve existing resource addresses where possible; use reviewed
state moves/imports where needed. Do not solve migration by destroying the held
H200 or weights volume. Coordinate acquisition serially against current inventory.

Acceptance:

- GPU-family totals and exact SKUs are checked on every path, including explicit
  preflight targets, experiments, unmanaged nodes, other states, and replacements.
- Tests reject two H200s, two H100s, three RTX GPUs, and forbidden families even
  when available; two single-RTX hosts and one dual-RTX host remain allowed.
- Plan evidence shows no instance/volume replacement for a serving-only change.
  A replacement needing temporary excess capacity is rejected.
- Available host RAM and storage are checked without increasing the allowed SKU.
- Current same-site NFS attachment assumptions and failure behavior are explicit.

### C. Stage, activate, and roll back serving profiles

Implement `ops/model_deploy.py` with `plan`, `stage`, `activate`, and `rollback`.
Use engine adapters for llama.cpp and legacy vLLM. Keep the protected node secret
file and SSH mechanism; adapt health/secret provisioning and outputs consistently.
Activation drains the selected replica, stops its old GPU process, starts the
candidate, and checks authentication, discovery, arithmetic, and stub parsing.
A failed candidate restores the prior service and manifest. Stage before stopping.

Acceptance:

- Checksum, sparse-attention metadata, GPU/CPU/disk shortages, unsupported flags,
  changed context/placement, and authentication failures stop activation.
- Every allocated slot is confirmed at 131,072 tokens. Server parallelism never
  exceeds the profile ceiling. CPU PLE placement is evidenced by loader output.
- The dual-RTX variant restricts each process to its assigned GPU and validates
  combined host RAM/cache pressure; neither process sees an unintended GPU.
- Failed start restores authenticated Qwen3.5 inference on the held instance.
  This is explicit rollback with the old context contract, not silent failover.
- Test ports remain localhost-only. No credential appears in command logs or locks.

### D. Capacity evaluator and immutable reports

Implement `evals/model_capacity.py` using spec section 9. Parameterize aliases,
slot count, tokenizer/template, context, resource sampling, and SLOs. Use
independent simultaneous cold prefixes, concurrent follow-ups, mixed arrivals,
retrieval/isolation assertions, and nontruncated history growth. Warm-history
cohorts must start below the input limit so follow-ups fit; cold full-context
prompts are a separate cohort. At N=1, measure arrivals through queueing rather
than allocating a second slot. Test every N
from one through the allowed ceiling or the observed failure boundary.

Acceptance:

- Reports include input/profile hashes, actual hardware, launch flags, context,
  measured overlap, token counts, per-session latency/rate, resource peaks, and
  every correctness/reliability/SLO verdict.
- Parsing, transport, streaming, observability, usage mismatch, truncation,
  isolation, and SLO failures produce nonzero status and partial diagnostic output.
- An early-EOS response cannot masquerade as a sustained decode benchmark;
  reasoning and visible-answer timing are reported separately.
- At least 100 measured turns, 20 cold requests, and 30 minutes per candidate;
  repeat highest passing N after restart. A passing policy ceiling is a tested
  bound, not proof of an unrestricted physical maximum.
- H200, H100, and RTX have separate reports. A dual-RTX host is tested with both
  processes active. A failed N=1 means zero qualified slots for that profile.

### E. Router admission, session affinity, and recovery

Pin the LiteLLM version and verify its llama.cpp integration and deployment-limit
semantics. Generate eligible backends from matching qualified reports. Implement
missing admission/affinity behavior explicitly if the router cannot supply it;
do not invent a YAML option or mistake a per-key cap for a deployment semaphore.
Use `redcell-qwen38` for the selected model and a separate Qwen3.5 rollback alias.

Acceptance:

- Backend concurrent requests stay within `min(policy, qualified)` even with
  retries, multiple team keys, and White Cell traffic. Global admission cannot
  exceed the sum of healthy eligible backend limits.
- One active generation per team initially; bounded queueing/overload responses
  are explicit. Both direct backends and router enforce limits.
- Session affinity improves warm reuse without admitting work to a full/unhealthy
  replica. Full transcript replay is available when affinity is lost.
- Host loss preserves the original model/context requirement. Saturation queues
  or pauses work; it never switches to a smaller context/model automatically.
- Retry tests preserve turn/tool-call identity and avoid duplicate stub dispatch;
  do not replay partially completed actions blindly.

### F. Combined rehearsal and operational documentation

Qualify H200 first, then the other allowed hosts independently. Rehearse the
whole fleet and inject host loss. Update docs with actual results and limitations.

Acceptance:

- Eight-team readiness is claimed only after eight simultaneous full-context
  conversations pass. White Cell competes for the same bounded capacity.
- Rehearsal reports normal and degraded admission. At provisional ceilings,
  H200 loss leaves five, single-RTX loss seven, and H100 loss eight slots.
- Record shared storage/router/network outage behavior and cold replay latency;
  do not claim redundant GPUs remove these failure domains.
- Preserve license/participant-use assessment with release records. No public
  or third-party exposure is inferred from an isolated technical test.
- Keep held capacity through rehearsal/live; release only at the exercise's end
  under the operator's instruction.

## 4. Validation and completion evidence

For documentation: local links, consistency, and `git diff --check`.
For code: relevant offline tests for schemas, resource/cap guards, argv rendering,
rollback, report failure handling, admission, and recovery. Run infra/harness
checks from [AGENTS.md](../AGENTS.md) when those components change. Infra syntax
validation alone does not prove fleet/session preconditions or live feasibility.

Separate local implementation verification from provider-backed testing. A local
coding completion can report that live qualification is still outstanding;
production readiness cannot. Do not claim GPU tests ran without result artifacts.

The coding completion should contain the commit(s), changed behavior, tests,
remaining operational gates, and this evidence table filled from real reports:

| Profile | Exact runtime/artifact pins | Full-context qualified slots | Report | Status |
|---|---|---:|---|---|
| H200 | Pending | Unset | Pending | Candidate |
| H100 | Pending | Unset | Pending | Candidate |
| RTX PRO 6000 | Pending | Unset | Pending | Candidate |
| Combined eight-team fleet | Pending | Unset | Pending | Candidate |
