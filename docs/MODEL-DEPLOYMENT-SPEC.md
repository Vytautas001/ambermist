# Portable model deployment: Qwen3.8 Flash Next Abliterated

Status: implementation specification; capacity estimates are unverified.
Date: 2026-09-24.

## 1. Outcome and scope

Deploy `windowsxp811203/Qwen3.8-Flash-Next-Abliterated-GGUF` on one H200,
H100, or RTX PRO 6000 Blackwell, and discover how many simultaneous long-context
sessions each configuration can serve comfortably. Reuse the deployment mechanism
for subsequent models through explicit model, runtime, hardware, and workload
profiles.

This specification has **no monetary ceiling and no fixed session limit**.
Concurrency is an output of capacity testing. Cost reporting is optional metadata,
not a criterion for the hardware recommendations below. Existing infrastructure
policy is a separate concern: this documentation change does not edit or bypass
the current Terraform budget guard.

Inference remains on Verda in Finland. Keep exercise scope in the system prompt,
require nonempty authorized networks, and keep harness tools as range-bound stubs.
This spec defines deployment and evaluation, including a reversible transition
from the existing service; it does not claim a deployment has been performed.

### Context contract

Interpret **126k as 126 × 1024 = 129,024 input tokens**, including the rendered
system prompt, tools, history, and chat delimiters. Reserve **2,048 output tokens**,
including reasoning, giving **131,072 tokens per active slot**. This is also large
enough if the intended input was decimal 126,000 tokens.

A session means an actively generating conversation with its full context
resident. Registered users, queued requests, and conversations saved to disk do
not count as simultaneous capacity. History that exceeds the input allowance must
receive an explicit error or a separately configured user-visible compaction
policy; capacity testing must never silently truncate it.

## 2. Hardware and initial capacity targets

SKU and nominal VRAM values come from [the repository catalog](../infra/locals.tf).
Actual availability, VRAM, host RAM, driver, CPU allocation, and storage must be
queried for each deployment. The suffix in a SKU is not a host-memory guarantee.

| Hardware | Verda SKU | Nominal VRAM | CUDA build target | First concurrency target | Next candidates if successful |
|---|---|---:|---:|---:|---|
| H200 | `1H200.141S.44V` | 141 GB | 90 | 4 | 6, 8, then higher |
| H100 | `1H100.80S.30V` | 80 GB | 90 | 1 | 2, then higher if memory permits |
| RTX PRO 6000 Blackwell | `1RTXPRO6000.30V` | 96 GB | 120 | 2 | 3, 4, then higher |

**These are provisional starting targets, not measured comfortable capacities.**
Start every hardware qualification at one session before advancing. The H100 is
the tightest fit and may require additional offload even for one slot. A failed
single-session latency test can make any candidate unsuitable. There is no
evidence yet to promise 4, 1, or 2 comfortable sessions respectively.

H200 is the preferred first qualification machine because it is already held and
has the most memory. RTX has more memory than this H100 SKU, so it may hold more
sessions despite different decode performance. Memory alone cannot rank latency.
NVIDIA lists H100/H200 at compute capability 9.0 and RTX PRO Blackwell at 12.0;
use a toolkit and driver supporting the selected target, or publish separate
runtime images per architecture. [NVIDIA hardware reference](https://developer.nvidia.com/cuda/gpus)

For each host, provision at least 128 GiB system RAM as an initial design target,
prefer 192–256 GiB for larger concurrency trials, and require at least 80 GiB
available before the initial CPU-embedding experiment. These are provisional
reservations, not verified model requirements. Increase them if measured peak
memory, checkpoint copies, or page residency requires it. Allow 140 GB free on the
weights volume for this artifact, plus runtime/build space and the retained
rollback model. Evaluate disk capacity in bytes before downloading.

## 3. Artifact and runtime

Use the same Q4_K_M artifact across all three machines:

```yaml
repository: windowsxp811203/Qwen3.8-Flash-Next-Abliterated-GGUF
revision_reference: c3365c4
file: Qwen3.8-Flash-Next-Abliterated-Q4_K_M.gguf
sha256: 324c85132e04654480ac93923f444b760b2950eb8c84a346dd0ec70e680ecde2
format: gguf
quantization: Q4_K_M
```

Resolve the revision reference to its full commit in the deployment lock file.
The publisher lists approximately 119 GB / 111 GiB for this artifact and describes
a corrected sparse-attention metadata upload. Its weights alone exceed the H100
and RTX VRAM. Q4 conversion quality and this exact artifact's long-context
behaviour need evaluation. Record the inherited license with the artifact.
[Checkpoint source](https://huggingface.co/windowsxp811203/Qwen3.8-Flash-Next-Abliterated-GGUF)

Use llama.cpp with CUDA as the initial runtime. Architecture support was merged
upstream; the older experiment documents' assertion that only an open PR can load
it is stale. Select an immutable upstream commit containing that support and the
required flags, build it, then lock the container digest, CUDA version, compiler,
and source commit. Do not launch from a moving PR head or `latest` tag. Actual
loading of the pinned GGUF remains a qualification gate.
[Upstream support](https://github.com/ggml-org/llama.cpp/pull/27742)

Baseline placement: PLE embedding table on CPU; transformer layers on GPU;
attention caches on GPU; F16 cache; text-only; no speculative draft model.
Inspect the GGUF tensor directory to obtain exact CPU and GPU weight sizes.
Verify placement in loader logs rather than subtracting an assumed table size
from the download size.

## 4. Memory model and limits of the session estimates

The base configuration has 48 layers, every fourth using full attention, two KV
heads, and 256 dimensions per head. At a 131,072-token allocation, ordinary F16 KV
therefore costs:

```text
12 layers × 2 (K and V) × 2 heads × 256 dimensions × 2 bytes × 131072
= 3 GiB per session
```

Confirm these dimensions in the selected GGUF. They are derived from the
[base model configuration](https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/config.json),
not measurements of this conversion.

There is also an indexer cache. The inspected implementation stores a single
128-dimensional key per full-attention layer and context cell; at F16 that adds
approximately 0.375 GiB per slot. Sparse selection does not remove the full
attention KV allocation in this model. Recurrent state, checkpoints, alignment,
and temporary allocations add further overhead.
[Runtime cache implementation](https://raw.githubusercontent.com/ggml-org/llama.cpp/master/src/llama-memory-hybrid-idx.cpp)

For **sensitivity analysis only**, assume 60–65 GiB GPU weights after CPU PLE
placement and 4–6 GiB incremental memory per full slot, with a total runtime and
free-memory reserve `R = max(8 GiB, 0.10 × V)`:

```text
N_memory = max(0, floor((V - W_gpu - R) / S_slot))
```

Using nominal GB converted conservatively to GiB gives:

| Hardware | Illustrative memory-only slot range | Interpretation |
|---|---:|---|
| H200 | 8–14 | Latency may become limiting much earlier; begin qualification at 4 |
| H100 | 0–1 | Fit depends strongly on actual placement; begin at 1 only after memory checks |
| RTX PRO 6000 | 2–5 | Begin at 2; validate decode and prefill contention before raising |

These ranges are calculated scenarios, not model benchmarks or hard maximums.
The assumed weight split and per-slot allowance must be replaced with measured
values. GPU-reported memory may differ from the nominal conversion. Context
checkpoint growth can exceed this allowance. Measure at N=1 and N=2, then at
each next N; use the worst measured incremental allocation and prefill peak.

H100 fallbacks, in order: reduce prefill microbatch; evaluate Q8 cache if the
pinned implementation supports it; move explicitly selected additional tensors
to CPU. Each change creates a distinct configuration requiring qualification.
Q8 reduces cache bytes, not weight bytes, and does not imply a twofold increase
in comfortable sessions. Expert offload can hurt latency significantly.

For H200, also compare keeping the PLE table on GPU if measured capacity allows;
that is a different capacity profile. For higher demand, add independently
qualified replicas and route sessions consistently. Do not assume linear scaling
or equivalent cache behaviour from tensor parallelism across multiple GPUs.

## 5. Definition of comfortable service

The following are proposed acceptance targets for agentic work, chosen for this
specification rather than claimed hardware performance. Workload profiles may
override them before testing; reports must show the targets used.

| Measure | Acceptance target |
|---|---|
| Generation at full context | At least 10 tokens/s for at least 95% of measured turns, evaluated per session |
| Warm follow-up TTFT | p95 at most 10 seconds, including admission/queue delay |
| Cold 126k input TTFT | p95 at most 180 seconds; report separately from warm work |
| Correctness | Every deterministic retrieval/isolation case passes; structured stub tool calls parse correctly |
| Reliability | No OOM, crash, context truncation, or failed request in the qualification run |
| Resource margin | At least 10% GPU memory free at measured peak; no sustained swap activity |

Count reasoning and answer tokens in generation throughput; also report time to
first answer content and total time to final answer. A stream producing only
reasoning does not pass correctness. Short early-EOS answers cannot establish
steady generation speed: include tasks producing at least 512 completion tokens.

`N_comfortable` is the largest tested N passing all targets at 126k input,
including concurrent prefill and repeated follow-ups. Report `N_memory`,
`N_comfortable`, and tested failures separately. The router's admission limit
comes from `N_comfortable`; a memory estimate cannot promote a profile to ready.
Results can be zero if even one session fails these targets.

## 6. Reusable deployment profiles

Implement four versioned inputs and one generated lock/qualification record:

| Input | Required information |
|---|---|
| Model | Artifact revision/hash, format, tokenizer/template provenance, context capability, tools/reasoning support, license |
| Runtime | Engine, immutable image digest/source commit, GPU architecture compatibility, launch adapter, health/metrics adapters |
| Hardware | Provider SKU/site, GPU count and memory, host memory, driver/toolkit constraints, storage/mounts |
| Workload | Input/output allowance, latency targets, concurrency discovery policy, checkpoint/cache policy |
| Resolved record | Exact four input hashes, generated argv, placement, measurements, qualification status and artifact links |

Suggested future layout (not implemented by this spec):

```text
deploy/models/qwen38-abliterated-q4.yaml
deploy/models/qwen35-gptq-int4.yaml
deploy/runtimes/llamacpp-cuda.yaml
deploy/runtimes/vllm-cuda.yaml
deploy/hardware/{h200,h100,rtxpro6000}.yaml
deploy/workloads/agentic-126k.yaml
ops/model_deploy.py
evals/model_capacity.py
```

Illustrative workload schema:

```yaml
schema_version: 1
id: agentic-126k
input_tokens: 129024
output_tokens: 2048
context_per_session: 131072
concurrency:
  mode: discover
  start: 1
  growth: adaptive
  qualified_sessions: null
targets:
  decode_tokens_per_second_min: 10
  warm_ttft_p95_seconds: 10
  cold_ttft_p95_seconds: 180
  gpu_free_fraction_min: 0.10
```

Use typed, engine-specific adapters to generate argv arrays; never evaluate
profile content as shell commands. Refuse unknown keys and unsupported engine /
artifact / GPU combinations. Runtime capabilities must be probed from the pinned
binary and demonstrated by a smoke test. Keep tokenizer and template tied to the
checkpoint; changing either invalidates capacity and correctness qualification.

The resolver must distinguish `candidate`, `qualified`, and `failed`. Null runtime
pins or unknown memory requirements are allowed in a draft spec, but never in a
resolved launch record. A candidate may run isolated qualification after resource
inspection; it must not receive normal user traffic. Further models need a profile
and qualification, without adding model-name branches to Terraform.

## 7. Infrastructure integration

Separate immutable machine acquisition from reversible serving configuration:

1. Terraform owns the GPU node, network access, keys, and persistent volume.
   Hardware/site changes use the normal reviewed replacement path. Select the
   exact SKU and Finnish site in capacity preflight. Existing p0/p1 identity and
   volume destroy protections apply; do not release held capacity to switch a model.
2. A generic bootstrap installs the runtime prerequisites and service launcher.
   Model selection, quantization, context, and serving flags live in a deployment
   manifest applied over SSH. Serving changes must not replace the GPU instance.
3. `model_deploy.py plan` resolves profiles and reports missing pins/resources.
   `stage` downloads and verifies artifacts without stopping the active service.
   `activate` drains requests, stops the old runtime, starts the candidate locally,
   and performs authenticated checks. `rollback` restores the previous manifest,
   runtime, and service. These are proposed commands, not existing entrypoints.
4. Keep inference credentials in the existing root-owned mode-0600 node secret
   file. Pass API keys through the process environment, never argv, Terraform
   state, manifests, logs, or reports. Restrict local evaluation ports to SSH
   tunnelling. Route qualified traffic through the authenticated private endpoint.
5. Replace the Qwen3.5-specific VRAM comparison with byte-based, placement-aware
   checks: GPU weights + session state + measured runtime peak + safety margin;
   independently check CPU resident memory and disk space. Unknown sizes block
   normal activation. Do not lower `weights_footprint_gb` merely to pass a guard.
6. Add runtime-neutral status, health, secret provisioning, and rollback handling.
   The current startup template, `provision_node_secrets.py`, and outputs assume
   vLLM; both runtime adapters must satisfy these contracts. Preserve the existing
   Qwen3.5 deployment as an explicit profile and rollback target.
7. Router configuration uses the actual backend engine, a model-specific alias,
   qualified concurrency, and measured timeouts. Verify LiteLLM compatibility
   with llama.cpp instead of assuming `hosted_vllm` settings transfer. Pin sessions
   to replicas for cache reuse; retain full history in the client for recovery.
   Returning sessions to a different model requires an explicit model change.

Before provider/OpenTofu commands, source the repository-root `.env` without
printing it. Site availability and sufficient provider funds are operational
inputs; no cost-based cap is introduced by this specification.

## 8. Launch contract for llama.cpp

After staging, the adapter renders the following baseline; `MODEL_FILE` and
`LLAMA_SERVER` refer to verified artifacts, and `TRIAL_SESSIONS` is the current
qualification candidate. The key is supplied as `LLAMA_API_KEY` in the protected
service environment. This is a launch template, not an instruction to switch the
running service during specification work.

```bash
"${LLAMA_SERVER:?}" \
  --model "${MODEL_FILE:?}" --alias qwen38-test \
  --host 127.0.0.1 --port 8001 \
  --n-gpu-layers all --override-tensor 'per_layer_token_embd=CPU' \
  --ctx-size "$((131072 * ${TRIAL_SESSIONS:?}))" \
  --parallel "${TRIAL_SESSIONS}" \
  --no-kv-unified --no-context-shift --fit off \
  --flash-attn on --cache-type-k f16 --cache-type-v f16 \
  --batch-size 512 --ubatch-size 128 \
  --ctx-checkpoints 2 --cache-ram 8192 \
  --jinja --metrics --slots
```

Validate a positive integer session count before rendering. Require loader/slot
evidence of 131,072 tokens per slot and the intended tensor placement. Explicitly
record checkpoint and host-cache settings because they affect both memory and
warm latency. Unsupported flags or implicit context changes are failures.
[Server option reference](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

## 9. Capacity qualification

Extend the existing [two-slot client](../evals/llama_two_slot_context.py) into
`model_capacity.py`. It currently fixes the session count and model alias,
performs sequential follow-ups, and does not enforce all correctness/SLO gates.
The sequential [bake-off runner](../evals/runner.py) is insufficient for this test.

Required procedure:

1. Inspect exact checkpoint tensors and GPU/CPU resources. Record immutable
   revisions, driver, CPU topology, GPU identity, and startup allocations.
2. Run arithmetic, authenticated API discovery, tokenizer/template consistency,
   and the [stub tool parsing check](../evals/check_stub_tool.py). Execute no tool.
3. Generate N different realistic synthetic histories of 129,024 input tokens
   (up to 32 tokens below target, never above); count the final rendered messages
   with the server tokenizer. Include scope, synthetic tool history, and unique
   facts at the beginning, middle, and end. Use randomized filler as well as a
   repeatable corpus; repeated padding alone is insufficient evidence.
4. Release all N requests from a barrier with independent prefixes and cold
   caches. Sample slots and timestamps to prove active overlap. Record prepared
   versus reported token usage; mismatch or missing usage fails qualification.
5. Repeat concurrent follow-ups with normal growing histories. For sustained
   steady-state testing, start new histories before exceeding the context contract.
   Do not silently trim accumulated replies. Measure cache reuse/recomputation and
   include reasoning consistently in replay according to the model template.
6. Mix a cold arrival with warm active sessions to expose prefill interference.
   Collect TTFT, first-answer latency, generation duration/rate, final-answer time,
   queue time, sampled peak VRAM/RSS, page faults, swap, and per-session errors.
7. At each candidate N, collect at least 100 measured turns including at least
   20 cold full-context requests, with a minimum 30-minute soak. Report sample
   counts with p95 values; repeat the highest passing N after a clean restart.
8. Increase N through the candidates in section 2, then adaptively until a memory,
   quality, or latency gate fails. Test intermediate counts to resolve the largest
   passing N. An operational time limit may stop discovery: label the result a
   tested lower bound rather than claiming a maximum.
9. Repeat after any change in model, quantization, runtime, GPU SKU, offload,
   checkpoint/cache settings, or workload. Never infer an RTX/H100 qualification
   solely from H200 memory use.

Correctness must automatically verify all session markers, absence of another
session's markers, follow-up continuity, and valid stub tool arguments. Handle
stream/observability errors as failures, return nonzero, and save partial results.
Correct the existing client's exception path that can reference an unset or stale
`follow` result. Separate reasoning tokens from visible-answer metrics and use
actual first/last token timestamps for decode rate.

There is an upstream long-context CUDA slowdown report for this architecture.
Treat its status as something to check against the pinned build; it is not a
benchmark for any of these hosts.
[Issue #28734](https://github.com/ggml-org/llama.cpp/issues/28734)

## 10. Implementation milestones and acceptance

1. Define profile schemas, resolver, and immutable lock format. Reject bad units,
   missing hashes, unsupported combinations, and secrets in profile output.
2. Implement runtime adapters and staging/activation/rollback. Prove a serving
   profile change produces no instance/volume replacement and rollback restores
   authenticated Qwen3.5 inference after a failed candidate start.
3. Implement workload-driven capacity evaluation and machine-readable reports.
   Router admission limits must use only a matching qualified report.
4. Qualify H200 first, then independently qualify H100 and RTX. Publish the largest
   passing concurrency (or tested lower bound), resource measurements, latency
   distributions, and failure boundary for each configuration.
5. Add another model profile through the same resolver without changing Terraform
   model-specific logic. Verify its own tokenizer, tools, memory, and runtime.

Implementation checks must cover invalid manifests, checksum failure, insufficient
CPU/GPU/disk memory, failed startup rollback, incorrect context allocation,
concurrency/isolation failures, and SLO failure reporting. Run the repository's
infra and harness checks when those components are changed. This specification
alone requires documentation/link review, not a provider apply or GPU benchmark.

Completion evidence is a table of measured comfortable session counts for all
three GPUs with reproducible manifests. Until those runs exist, use section 2
only to plan trials; leave `qualified_sessions` unset.
