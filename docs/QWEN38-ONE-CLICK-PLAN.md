# Qwen3.8 one-click attempt node — plan for the coding agent

Status: ready for offline implementation by the coding agent. Prepared
2026-09-25. Nothing in this plan has been built or run yet.

Read first: [AGENTS.md](../AGENTS.md), [QWEN38-EXPERIMENT-PLAN.md](QWEN38-EXPERIMENT-PLAN.md)
§1.1, §4.1, §4.2, §4.4 and §9, and [MODEL-DEPLOYMENT-SPEC.md](MODEL-DEPLOYMENT-SPEC.md)
§8. The profile files and the llama.cpp pin are shared with the experiment plan.
Build them once, to the schema that plan defines. If this plan and those documents
disagree, the stricter rule wins; report the conflict.

## 1. Objective

The operator runs one command. It provisions one Verda GPU node of the family the
operator chose and serves a pinned Qwen3.8-Flash-Next GGUF through `llama-server`.
The command then checks that the endpoint works:

```bash
cd infra
make qwen38 GPU=h200 MODEL=base
make qwen38 GPU=rtx  MODEL=abliterated CONFIRM_ABLITERATED=yes SESSIONS=2
```

Each run is a separate **experimental attempt**:

- It is not qualification, it is not production, and no team traffic reaches it.
- It stands alongside the existing provisioning code without replacing it.
- The legacy Qwen3.5/vLLM infrastructure is already destroyed. Its code paths
  (`p0`, `p1`, `p2`, `p4`, `live4`, `scripts/startup.sh.tftpl`) stay exactly as
  they are.

Operator decisions behind this plan:

| Topic | Decision |
|---|---|
| GPU | The operator chooses `GPU=h200` or `GPU=rtx`. There is no automatic fallback. If that SKU is out of stock, the command fails. |
| Model | `MODEL=base` is the default model. `MODEL=abliterated` also requires `CONFIRM_ABLITERATED=yes`. |
| End state | Node running, weights verified, `llama-server` healthy, and one test chat answered. The LiteLLM router is not touched. |
| Abliterated gate | For that single run, the confirm flag counts as the operator's authorization. It does not perform the §9 switch in the experiment plan, and it does not change AGENTS.md, the spec or any ADR. |

## 2. Operator interface

All targets live in `infra/Makefile`.

```text
make qwen38 GPU=h200|rtx MODEL=base|abliterated [SESSIONS=1] [location=FIN-01] [CONFIRM_ABLITERATED=yes]
make qwen38-status    # health, loaded alias, per-slot context, tunnel command
make off              # existing target: destroys the GPU node, keeps the weights volume
```

`make qwen38` runs these steps in order. It never prompts. The first failure
stops the run with a clear message.

1. **Validate arguments.**
   - `GPU` and `MODEL` are required.
   - `SESSIONS` defaults to 1 and must be an integer from 1 to the hardware
     `session_ceiling` (H200 4, RTX 2).
   - `MODEL=abliterated` without `CONFIRM_ABLITERATED=yes` is refused.
   - The run is refused while the llama.cpp commit in
     `deploy/runtimes/llamacpp-cuda.yaml` is still empty.
2. **Check credentials.** Run the existing `check-creds`, then require
   `HF_TOKEN` and `VLLM_API_KEY`, as `apply` already does.
3. **Strict preflight.** Run `python3 ../ops/preflight.py --target-sku <sku> --target-tp 1 --target-location <location>`.
   That path already exits 0 when the SKU is available and 3 when it is not
   ([preflight.py:123-143](../ops/preflight.py#L123-L143)).
   - On any exit other than 0, stop. **Do not** reuse the "Continue anyway?"
     prompt from `apply`.
   - On exit 3, print the locations that have the SKU in stock. Also say that
     the weights volume is tied to one location (§8).
4. **Apply.** Run `$(TF) apply -auto-approve -var-file=phases/qwen38.tfvars -var qwen38_gpu=… -var qwen38_model=… -var qwen38_sessions=… $(TF_VAR_ARGS)`.
5. **Secrets.** Run `ops/provision_node_secrets.py`, which then targets the llama
   unit (§5).
6. **Wait and verify.** Run `ops/qwen38_wait.py` (§6). The overall timeout is
   90 minutes, enough for the first download and build.

`make qwen38-status` runs the read-only part of `ops/qwen38_wait.py` once.

The coding agent writes and tests this code offline. It never runs `make qwen38`,
`tofu apply` or anything else that contacts Verda. Only the operator runs those.

## 3. Profiles

Create the files from experiment plan §4.2 using its schema. For this plan, only
the subset below is needed. If the experiment plan's version of a file already
exists, reuse it rather than creating a duplicate.

```text
deploy/models/qwen38-ud-q4kxl.yaml          # MODEL=base. Pins exactly as experiment plan §1.1
deploy/models/qwen38-abliterated-q4.yaml    # MODEL=abliterated. Pins exactly as experiment plan §9
deploy/runtimes/llamacpp-cuda.yaml          # commit (per §4.1 criteria), build_image + digest, cmake flags
deploy/hardware/h200.yaml                   # sku 1H200.141S.44V, cuda_arch 90,  session_ceiling 4, placement_args
deploy/hardware/rtxpro6000.yaml             # sku 1RTXPRO6000.30V, cuda_arch 120, session_ceiling 2, placement_args
```

Rules:

- **Model mapping.** `MODEL=base` maps to `qwen38-ud-q4kxl` and
  `MODEL=abliterated` maps to `qwen38-abliterated-q4`. `GPU=h200` maps to
  `h200.yaml` and `GPU=rtx` maps to `rtxpro6000.yaml`.
- **Copy the pins, don't retype them.** Take repository, revision, `entry_file`
  and the per-shard `size_bytes` and `sha256` verbatim from the experiment plan.
  A test compares them against those blocks.
- **llama.cpp commit.** Use the experiment plan §4.1 criteria: include PR #27742,
  check against issue #28734, and verify the spec §8 flags. Do not guess the
  commit. If none qualifies, leave the field empty and report it; step 1 in §2
  then blocks the run.
- **Build container.** `build_image` is a CUDA 12.8+ `devel` image pinned by
  digest. It must be able to compile for both sm_90 and sm_120.
- **H200 `placement_args`:** `--n-gpu-layers all --override-tensor 'per_layer_token_embd=CPU'`.
- **RTX `placement_args`:** the same, plus an extra CPU offload for part of the
  weights. One RTX PRO 6000 has 96 GB, and the weights are 104–111 GiB.
  - Use the §4.4 GGUF inspection to choose the tensors, for example
    `--n-cpu-moe <k>` or a regex for `--override-tensor`. Pick whichever the
    pinned commit supports for `qwen4exp`.
  - Mark the value `provisional: true` in the YAML.
  - Do not use `--fit on`. Automatic placement may shrink the context, and
    spec §8 requires `--fit off`.
- **Workload.** For these attempts, no workload profile is needed. `SESSIONS`
  is the only workload input.

OpenTofu reads the YAML with `yamldecode(file(...))`, so every pin exists in one
place only. If `ops/model_deploy.py plan` from the experiment plan also exists,
a test checks that its argv equals the argv rendered by OpenTofu.

## 4. OpenTofu changes

Only add code. Leave the vLLM path unchanged.

| File | Change |
|---|---|
| `infra/variables.tf` | Add `"qwen38"` to the `phase` validation. Add `qwen38_gpu` (`h200`/`rtx`, default `""`), `qwen38_model` (`base`/`abliterated`, default `""`) and `qwen38_sessions` (number, default 1), each with a validation. |
| `infra/locals.tf` | Load the YAML profiles. Add `phases.qwen38.primary = { role = "qwen38", sku = <hardware yaml sku>, tp = 1, spot = false, runtime = "llamacpp", model_id, sessions, serve = true, ... }`. Leave `fleet_limit`, `fleet_gpu_counts` and the other phases unchanged. |
| `infra/main.tf` | In `verda_startup_script.role`, render `scripts/startup-llamacpp.sh.tftpl` when `runtime == "llamacpp"`. Otherwise keep the existing template call byte for byte. Pass only non-secret values: pins, commit, image, arch, argv list, sessions. |
| `infra/instances.tf` | Add `model_id` and `sessions` to `terraform_data.instance_identity`, so changing the model or session count replaces the node cleanly. Skip the VRAM precondition when `runtime == "llamacpp"`: it models vLLM, and a partial CPU offload is intended on RTX. The fit check becomes the loader-log check in §6. Leave `fleet_guard` unchanged. |
| `infra/outputs.tf` | Expose each role's `runtime` and its llama alias so the scripts don't hardcode them. |
| `infra/phases/qwen38.tfvars` | `phase = "qwen38"` and `planned_hours`. No SKU; the SKU comes from `qwen38_gpu`. |

Reuse the following unchanged:

- `verda_ssh_key.operators`.
- `verda_volume.weights`: 400 GB `NVMe_Shared`, with `prevent_destroy`. Both
  models (111 GB and 119 GB) and the builds fit on it together, and it survives
  `make off`.
- `verda_volume_attachment.weights`.
- `terraform_data.fleet_guard`. A single GPU always fits the cap. The guard still
  checks the cap and must stay in place.

Resources are keyed by role. So `qwen38` replaces whatever node the previous
phase left, and it never runs alongside another phase.

## 5. Node bootstrap

### 5.1 `infra/scripts/startup-llamacpp.sh.tftpl`

This is a new file. Copy sections 1–3 of `scripts/startup.sh.tftpl` (NFS mount
discovery, loading `.env`, GPU sanity check) instead of refactoring the vLLM
template. Then add these steps.

1. **GPU family check.** Check the GPU count (must be 1). Check that
   `nvidia-smi` reports a name matching the chosen family (`H200` or
   `RTX PRO 6000`). A mismatch is fatal.
2. **Stage the weights,** skipping this step if already verified:
   - If `/mnt/weights/qwen38/<model_id>/.verified` exists and lists exactly the
     pinned hashes, skip the step.
   - Otherwise download only the pinned shard files:
     `hf download <repo> --revision <sha> --include <file>... --local-dir /mnt/weights/qwen38/<model_id>`.
   - Never download `MTP/`, the vision projector or other quants.
   - Before downloading, require free space of at least the total `size_bytes`
     plus 20 GiB.
   - Check every shard's size and SHA-256, then write `.verified`.
   - A mismatch is fatal. Never delete or overwrite the other model's directory.
   - `HF_TOKEN` is used if `.env` provides it; both repositories are public.
3. **Build llama.cpp,** skipping this step if the build is cached:
   - Target directory:
     `/mnt/weights/qwen38/llama.cpp-<commit>-sm<arch>/`.
   - If the directory has a `build.json` matching the commit, image digest and
     arch, reuse it.
   - Otherwise, inside `build_image`, run
     `git fetch` of the exact commit, then `cmake -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=<arch>`,
     then `--target llama-server`, and write `build.json`.
4. **GGUF inspection.** Run the §4.4 inspection if it exists. A metadata
   mismatch is fatal. If the inspection does not exist yet, log that it was
   skipped.
5. **Systemd unit.** Write `/etc/systemd/system/redcell-llama.service` using the
   same pattern as the vLLM unit:
   - `EnvironmentFile=/mnt/weights/.env`.
   - `ExecStartPre` fails when `VLLM_API_KEY` is empty.
   - `ExecStart` runs `docker run --rm --gpus all --network host` with
     `build_image`, mounts `/mnt/weights`, passes `-e LLAMA_API_KEY=$VLLM_API_KEY`,
     and runs the built `llama-server` with the spec §8 argv:
     - `--model <staged entry_file>`
     - `--alias qwen38-<model_id>`
     - `--host 127.0.0.1 --port 8001`
     - `--ctx-size $((131072 * SESSIONS))`, `--parallel SESSIONS`
     - the hardware `placement_args`
     - the remaining spec §8 flags, unchanged
   - Pass the argv as a rendered list, never through `eval`.
   - `Restart=on-failure`.

   Then run `systemctl enable --now`. Before `.env` exists, the unit fails its
   `ExecStartPre` and waits. The secrets step restarts it.
6. **Attempt record.** Write `/mnt/weights/qwen38/attempts/<utc-ts>.json` with:
   - SKU, GPU name and driver
   - the model id and pins
   - the llama.cpp commit and `build.json`
   - the argv list

   It holds no secrets.

Keep the listener on `127.0.0.1`. The operator reaches it through an SSH tunnel.

### 5.2 `ops/provision_node_secrets.py`

It currently hardcodes `{project}-vllm` for the unit path and service name
([provision_node_secrets.py:104-105](../ops/provision_node_secrets.py#L104-L105)).
Take them from the role's `runtime` output instead:

- `llamacpp` uses `redcell-llama`.
- Anything else keeps `redcell-vllm`.

Also make the remote error message name the actual unit. Leave the
secret-handling path unchanged: stdin only, atomic 0600 write.

## 6. `ops/qwen38_wait.py`

Everything it needs comes from `tofu output`: node IP, runtime, alias and
sessions. Every node command goes over SSH to `127.0.0.1:8001`, so the port is
never exposed.

1. Poll `/health` until it succeeds or the timeout expires. While waiting, show
   progress from the tail of `/var/log/redcell-startup.log`.
2. Call the authenticated `/v1/models` and check that the alias matches. Read
   the key on the node from `.env`; never put it in argv on the workstation.
3. Read `/slots`, or the loader log. Check that there are `SESSIONS` slots, each
   with `n_ctx == 131072`, and that nothing was shrunk. If the context is
   smaller, the attempt fails.
4. Send one short chat completion with `max_tokens` 64 and record its latency.
5. Copy the node's attempt JSON to `attempts/qwen38/<utc-ts>-<model>-<gpu>.json`
   in the repository. Add the results of steps 1–4 and `"status": "unqualified"`.
6. Print the alias, the base URL through the tunnel and the tunnel command:
   `ssh -N -L 8001:127.0.0.1:8001 root@<ip>`.

On failure, it prints the last 100 lines of
`journalctl -u redcell-llama` and of the startup log, then exits with a nonzero
code. It leaves the node running, so the operator can inspect it and then run
`make off`.

Add `attempts/` to `.gitignore`.

## 7. Tests (offline)

- **`deploy/tests/`** (pytest):
  - The profiles parse.
  - Shard sizes add up to `size_bytes`.
  - `entry_file` is one of the shards.
  - The pins equal the YAML blocks in the experiment plan's §1.1 and §9.
  - The ceilings are 4 and 2.
  - No field name matches `key|token|secret|password`.
- **Rendered argv:**
  - For H200 with N from 1 to 4 and RTX with N from 1 to 2, it equals the spec
    §8 argv plus that hardware's `placement_args`.
  - Rendering is refused for N=0, for H200 with N=5 and for RTX with N=3.
- **Makefile:** a dry-run test (`make -n` or a shell test with a stubbed `tofu`)
  checks that:
  - a missing `GPU` or `MODEL` is refused;
  - `MODEL=abliterated` without the flag is refused;
  - a blank commit is refused;
  - preflight exit 3 stops the run before `apply`.
- **OpenTofu:**
  - `tofu fmt -check` and `tofu validate` pass.
  - `tofu plan -refresh=false` with dummy credentials, or a mocked provider,
    gives exactly one GPU in `fleet_gpu_counts`: `h200` for
    `qwen38`+`h200` and `rtxpro` for `qwen38`+`rtx`.
  - The rendered startup script contains no `.env` values.
- **vLLM path unchanged:** the rendered `startup.sh.tftpl` and the planned
  resources for `p0` and `live4` are identical before and after this change.
- **`ops/`:** unit tests for `qwen38_wait.py`, using fixtures from a stubbed SSH
  runner, and for the runtime-based unit name in `provision_node_secrets.py`.

Run `ruff` and `pytest` for every Python path you touch. Add `deploy/tests` and
the `ops` tests to CI, following experiment plan §4.5.

## 8. Known risks

State each of these in the final report. Never work around one silently.

- **RTX fit.** One RTX PRO 6000 has 96 GB, so part of the weights must live in
  host RAM. Host RAM on `1RTXPRO6000.30V` is not guaranteed, and decode will be
  slower. The first RTX attempt is the measurement. If the loader reduces the
  context or runs out of memory, the attempt fails; do not lower the context.
- **Volume location.** The weights volume is tied to one location. If the chosen
  SKU is in stock only somewhere else, changing `location` would replace the
  volume and lose the staged weights. `make qwen38` must stop and explain this.
  It must never apply a location change on its own.
- **Runtime pin.** The one-click run stays blocked until the llama.cpp commit is
  pinned (§3).
- **First-run time.** The first attempt for each model downloads 111–119 GB and
  builds llama.cpp, which takes roughly 30–60 minutes. Later attempts reuse the
  volume. Every attempt runs the build once per GPU architecture.
- **Abliterated provenance.** The abliterated artifact comes from an anonymous
  publisher. Size and SHA-256 checks against the §9 pins run on every stage; a
  mismatch is fatal.

## 9. Non-goals

- LiteLLM router, team keys, harness alias changes (handoff E).
- Automatic GPU fallback, H100, multi-GPU or multi-node attempts.
- Capacity qualification. Use the experiment plan for that. An attempt record is
  not evidence of capacity.
- Changes to `scripts/startup.sh.tftpl`, the legacy phases or `fleet_limit`.
- Deleting weights, volumes or other staged models.
- Any SKU or family outside the fleet cap in AGENTS.md.

## 10. Handback

Report back with:

- the commits;
- test output;
- the llama.cpp commit and how it was verified, or why the pin is still blank;
- the RTX `placement_args` and their basis;
- the exact operator commands for the first H200 and RTX attempts.
