# Lessons from earlier attempts

Everything before the restart is preserved at git tag `archive/attempt-1`.
Read an old file with `git show archive/attempt-1:<path>`. Old code is
reference material, not a template: re-add something only on purpose, with a test.

Status of each fact: **verified** (checked against a source or run), **decided**
(operator decision), or **unverified** (plan or estimate, never measured).

## Decisions (still in force)

- **Scope: maximum qualified sessions per node**, not an eight-team target
  (deferred). Admitted sessions = `min(policy ceiling, measured capacity)`.
  Provisional ceilings: H200 4, H100 1, RTX 2 per GPU. *(decided)*
- **Harness tools are range-bound stubs.** No real offensive tooling; empty
  `in_scope_networks` must be rejected; scope lives in the system prompt. *(decided)*
- **Model license:** Qwen Community License 1.0 (not Apache). Applicability to
  participant service must be assessed and recorded. *(decided, assessment pending)*

## Model artifact (verified against publisher metadata 2026-09-24)

- Pin the revision: an earlier upload had wrong sparse-attention metadata. Check
  `qwen4exp.attention.compress_ratios` = 4 on full-attention layers, 0 elsewhere.
- Weights alone exceed H100 (80 GB) and RTX (96 GB) VRAM; only H200 holds them
  on-GPU. H100/RTX need CPU offload and are marginal.
- Alternate candidate with pinned shards: `unsloth/Qwen3.8-Flash-Next-GGUF`
  UD-Q4_K_XL @ `38bb39ee97821de2c9009abb7e93950eec396e66` (4 shards; hashes in
  `archive/attempt-1:deploy/models/qwen38-ud-q4kxl.yaml`).
- Download: `curl --fail -L --retry 5 --continue-at - https://huggingface.co/<repo>/resolve/<revision>/<file>`.
  Needs ~140 GB free disk.

## Runtime: llama.cpp CUDA

- `qwen4exp` support merged upstream in PR #27742 (merge commit
  `6c84c7d5d8833c6e0df69628f75a0f599797934e`). The publisher's "needs open PR"
  note is stale.
- Selected pin (source-read 2026-09-25, **never compiled or run on a GPU**):
  `e9f824d8c0f011662a742c9d15d4aa18a41e32c0`. Build image
  `nvidia/cuda:12.8.1-devel-ubuntu24.04@sha256:4b9ed5fa8361736996499f64ecebf25d4ec37ff56e4d11323ccde10aa36e0c43`.
- CUDA arch: `90` for H100/H200, `120` for RTX PRO 6000 Blackwell. Build per node arch.
- Open issue #28734: decode cost grows with context for this architecture on
  CUDA (unresolved at the pin). Measure decode at depth; a fixing commit is a new pin.
- `--n-cpu-moe` is accepted by the parser, but nobody checked that it matches the
  `qwen4exp` tensor names. *(unverified)*

Baseline launch (unverified on hardware; `N` = sessions):

```bash
llama-server --model "$MODEL_FILE" --alias qwen38 \
  --host 127.0.0.1 --port 8001 \
  --n-gpu-layers all --override-tensor 'per_layer_token_embd=CPU' \
  --ctx-size $((131072 * N)) --parallel N \
  --no-kv-unified --no-context-shift --fit off \
  --flash-attn on --cache-type-k f16 --cache-type-v f16 \
  --batch-size 512 --ubatch-size 128 \
  --ctx-checkpoints 2 --cache-ram 8192 \
  --jinja --metrics --slots
```

API key goes in env `LLAMA_API_KEY`, never argv. Confirm per-slot context and
tensor placement in the loader log; any automatic context reduction is a failure.
If OOM at prefill: lower `--ubatch-size` to 64, then try Q8 cache, then move more
tensors to CPU. Each change is a new profile that needs requalification.

## Capacity math (estimates, not measurements)

- Slot = 131,072 tokens = 129,024 input + 2,048 output (reasoning included).
  Never truncate context to fit more sessions.
- F16 KV per slot ≈ 3 GiB (12 full-attention layers × 2 × 2 heads × 256 dim × 2 B
  × 131072) + ~0.375 GiB indexer cache + overhead. Plan 4–6 GiB per slot.
- Illustrative memory-only range: H200 8–14, H100 0–1, RTX 2–5 slots. These are
  *not* capacity; they don't override the ceilings.
- Host RAM: ≥128 GiB target (192–256 preferred), ≥80 GiB free for CPU-resident
  embeddings. The SKU suffix is not a RAM guarantee: check `free -h`.

## Verda provider / OpenTofu gotchas (verified in code)

- `verda_instance` has **no update path** (Update is an error stub), and nearly
  every argument is ForceNew. Treat instances as immutable.
- `is_spot` is *not* ForceNew in the schema; changing it plans an update that
  fails at apply. Force replacement via `replace_triggered_by`.
- `description` is **required** on `verda_instance`.
- `os_volume` is a nested *attribute* (`os_volume = { ... }`), not a block.
- `on_spot_discontinue = "delete_permanently"` only on spot OS volumes (rejected
  on on-demand); `"keep_detached"` on the weights volume, or a spot reclaim can take it.
- `verda_instance` does not wait for an IP (can return `ip = null`). Only
  `verda_volume_attachment` waits (3 min). Depend on the attachment.
- Startup scripts cannot be updated in place, and their bodies are stored in
  plaintext in the Verda API and in state. Never put secrets in them.
- Destroying an instance **does not delete its OS volume**. Orphans keep billing;
  check for them after every teardown.
- Volume `size` is ForceNew: growing it destroys the data. Protect the weights
  volume with `prevent_destroy`.
- `NVMe_Shared` is NFS, not local disk; measure load speed and page residency.
  Volumes are site-bound.
- No data sources in the provider: the SKU catalog must be literals.
- Availability API: an "available" answer is not a reservation.
- Zero project balance → Verda can discontinue instances and trash volumes
  (recoverable for 96 h, charged). Check the balance before long runs.
- Offline init: if the provider binary is in `infra/.terraform/providers`, use
  `tofu init -plugin-dir=.terraform/providers`.
- Prices seen 2026-09: H200 €3.728/GPU/h, RTX PRO 6000 €1.585/GPU/h.

## Operations

- Node secrets: write `/mnt/weights/.env` over SSH stdin as root, mode 0600.
  Never pass keys through Terraform inputs, state, argv, or startup scripts.
- Keep inference ports on localhost; test through `ssh -N -L 8001:127.0.0.1:8001 root@NODE_IP`.
- Smoke check worth keeping: unauthenticated `/v1/models` → 401; authenticated
  list has the alias; "6×7" → `42`; a stub tool call parses (never executed).
  See `archive/attempt-1:ops/check_inference.py`, `evals/check_stub_tool.py`.
- Long-context test design: use `/apply-template` + `/tokenize` to size prompts
  exactly; put unique markers at the start, middle, and end of each session;
  release sessions from a barrier; check `/slots` for overlap and that no
  session sees another's markers. See `archive/attempt-1:evals/llama_two_slot_context.py`.
- Only recorded run: 2026-09-24, FIN-02 H200 served Qwen3.5-122B GPTQ-Int4 on
  vLLM 0.28.0 at 64k context; smoke checks passed. **Qwen3.8 has never been run.**

## What went wrong last time

- Documentation outran code: ~3,000 lines of specs, handoffs, and experiment
  plans for zero Qwen3.8 runs. Get one node serving first, then write down what
  was measured.
- The design kept being amended (ADRs 0001→0005) while legacy code stayed in
  the tree "for reference". Agents kept patching legacy paths. Old code now
  lives only in the archive tag.
- Model names were hard-coded through Terraform, router, and scripts; switching
  models touched everything. Keep model/runtime choice out of infrastructure code.
