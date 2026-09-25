# Qwen3.8 H200 qualification experiment — plan for the coding agent

Status: ready for execution by the coding agent (GPT Luna). Prepared 2026-09-25.
Nothing in this plan has run yet. It authorizes **offline coding only**. Every
live step waits for an explicit operator message naming its gate (for example
`GO G1`). See [Gates](#3-gates-and-authority).

Read first, in order: [AGENTS.md](../AGENTS.md),
[MODEL-DEPLOYMENT-SPEC.md](MODEL-DEPLOYMENT-SPEC.md) sections 1–5, 8–9,
[CAPACITY-RUNBOOK.md](CAPACITY-RUNBOOK.md) sections 2–4, and
[IMPLEMENTATION-HANDOFF.md](IMPLEMENTATION-HANDOFF.md) packages A, C, D. If this
plan and those documents disagree, the stricter rule wins; report the conflict.

## 1. Objective

Answer one question with measured evidence: **how many 131,072-token sessions
does the pinned Qwen3.8 Abliterated Q4_K_M GGUF serve comfortably on one H200
under a pinned llama.cpp build, from N=1 up to the policy ceiling of 4?**

The experiment produces:

1. An immutable llama.cpp runtime pin, verified by building it and loading the GGUF.
2. A minimal profile set and resolver that render the exact launch argv.
3. `evals/model_capacity.py`, which implements spec section 9 for one replica.
4. One qualification report per tested N, plus a summary with
   `N_memory`, `N_comfortable`, the policy ceiling (4), and the failure boundary.
5. The H200 profile marked `qualified` (with `qualified_sessions = N_comfortable`)
   or `failed`, never "ready for traffic".

The H200 goes first because it is the only allowed GPU that fits the transformer
weights with KV headroom (spec §2). The RTX and H100 experiments reuse the same
tooling later under their own gates. They are **out of scope** here.

### Non-goals (do not touch)

- Router/LiteLLM config, team keys, harness alias migration (handoff E and the
  alias row). The experiment uses a localhost trial alias `qwen38-test`.
- Terraform refactors, inventory model, phase maps, `fleet_guard`, `locals.tf`
  catalog (handoff B). No new tfvars is needed: `p0` already defaults to one
  on-demand `1H200.141S.44V`.
- Any normal/participant traffic, any context below 131,072 per slot, any
  session count above 4, any other model/quant/publisher, any other GPU family.
- Offensive tooling. Harness tools stay range-bound stubs; the tool check only
  parses calls and never dispatches them.

## 2. Known starting state (verify, don't trust)

- No `infra/terraform.tfstate` exists in the working copies. Only a stale
  `.backup` exists. **Treat held inventory as unknown** until G0 reconciles it
  against the Verda account.
- `infra/terraform.tfvars` sets `weights_volume_size_gb = 400`. The GGUF needs
  ~140 GB free plus build space, alongside the Qwen3.5 rollback weights.
- `make p0` boots the legacy Qwen3.5 GPTQ-Int4 on vLLM (`redcell-vllm` unit) with
  the weights volume on NFS (`NVMe_Shared`). That service is the rollback target
  for this experiment. It is **not** production. Interrupting it on a p0 node
  still needs operator approval at G3.
- The node secret file `/mnt/weights/.env` (root, 0600) holds `VLLM_API_KEY`.
  Reuse it. Do not create a second key or rename the variable (AGENTS.md).
- `evals/results/` is gitignored. Raw reports stay out of git (§6).
- `.github/workflows/*.yml` trigger on `main`, but the branch is `master`.
- `evals/llama_two_slot_context.py` has the stale-`follow` exception path noted in
  spec §9. Fix it, or mark the script superseded by `model_capacity.py`.

## 3. Gates and authority

| Gate | What it unlocks | Luna may do before it |
|---|---|---|
| — | Offline coding, tests, commits (§4) | Everything in §4 |
| **G0** | Read-only account inventory + `ops/preflight.py` availability query | Prepare commands; nothing sent to Verda |
| **G1** | Acquisition: operator runs or approves `tofu apply` for p0 H200 | Show `tofu plan` output for review |
| **G2** | SSH to node; download/verify GGUF; build llama.cpp; inspect GGUF. No service stop | — |
| **G3** | Stop `redcell-vllm`; run trials N=1→4; restore vLLM afterwards | — |
| **G4** | Keep or release the node (operator decides; default **keep**) | Report cost-to-date |

At each gate, stop and send the operator: what you will run, what it changes,
the expected duration, and how to undo it. Proceed only on `GO Gn`. A gate
approval covers only that gate. If anything unexpected happens, stop and
restore (§5.6), then report.

## 4. Offline work packages (no gate needed)

Commit each package separately with tests. Run `ruff` and `pytest` for every
Python path you touch. Add the new test directories to CI (see 4.5).

### 4.1 Runtime pin (handoff A, runtime part)

Choose a full upstream `ggml-org/llama.cpp` commit that:

- contains the merged `qwen4exp` support ([PR #27742](https://github.com/ggml-org/llama.cpp/pull/27742));
- accepts every flag in spec §8: `--override-tensor`, `--no-kv-unified`,
  `--no-context-shift`, `--fit`, `--flash-attn on`, `--cache-type-k/v`,
  `--ctx-checkpoints`, `--cache-ram`, `--jinja`, `--metrics`, `--slots`;
- is checked against [issue #28734](https://github.com/ggml-org/llama.cpp/issues/28734).
  Record whether the long-context CUDA decode slowdown is fixed at that commit.

Verify by reading the source at that commit (server arg parser, model arch
table). Do not guess or take a PR head or tag. Record the commit SHA, the date,
and how you verified it in `deploy/runtimes/llamacpp-cuda.yaml`. Leave
`image_digest: null` for this experiment: we build from source on the node, and
the build record (§5.3) captures compiler, CUDA, driver and `cmake` flags. If no
commit satisfies all flags, stop and report which flags are missing. Do not drop
a flag silently.

### 4.2 Minimal profiles and resolver (handoff A, subset)

Create the layout from spec §6, limited to what this experiment needs:

```text
deploy/models/qwen38-abliterated-q4.yaml   # repo, revision, file, size_bytes, sha256, license, template provenance
deploy/runtimes/llamacpp-cuda.yaml         # commit, cuda_arch [90, 120], build flags, supported flags list
deploy/hardware/h200.yaml                  # SKU 1H200.141S.44V, FIN site, policy_ceiling: 4, cuda_arch 90
deploy/workloads/agentic-126k.yaml         # spec §6 schema + SLO targets from spec §5 + warm-cohort params (§4.3)
ops/model_deploy.py                        # subcommands below
deploy/tests/                              # resolver tests
```

`ops/model_deploy.py` subcommands for this experiment:

- `plan --model --runtime --hardware --workload --sessions N` writes a resolved
  record to JSON. It holds the four input SHA-256s, the argv **list**,
  `ctx_size = 131072 × N`, `parallel = N`, placement, and status `candidate`.
  It rejects unknown keys, missing hashes/pins, N < 1, N > policy ceiling,
  engine/arch mismatches, and any key matching `key|token|secret|password`.
- `stage` (G2) runs over SSH. It downloads to `/mnt/weights/qwen38/` with resume,
  checks size in bytes and SHA-256, and runs the GGUF inspection (4.4). It never
  stops a service. It refuses to start if free disk is below `size_bytes` + 20 GiB.
- `trial start --record R` (G3) copies the record to the node and starts
  `llama-server` under a transient unit (`systemd-run --unit=redcell-llama-trial`).
  A small wrapper reads `/mnt/weights/.env`, exports
  `LLAMA_API_KEY=$VLLM_API_KEY`, and `exec`s the argv from the JSON. It never
  evaluates profile strings as shell. It binds `127.0.0.1:8001` only, and it
  refuses to start while `redcell-vllm` is active (the operator stops it at G3).
- `trial stop` stops the transient unit and collects the server log.
- `restore` starts `redcell-vllm` and confirms `/health`. It also confirms an
  authenticated `/v1/models`.

Keep `activate`/`rollback` (handoff C) for later. Structure the code so they
can reuse `stage` and the wrapper.

Tests: bad keys, bad N, secret-shaped fields, deterministic hashes, and argv
equality with spec §8 for N=1..4. Also verify that changing any input changes
the record hash.

### 4.3 Capacity evaluator (handoff D, single replica)

Implement `evals/model_capacity.py` following spec §9 steps 2–8 and §5 targets.
Use the stdlib plus whatever `evals/` already uses. Configurable: base URL,
alias, API key via env only, N, the workload profile path, the resolved-record
path, the output dir, and the soak duration.

Required behaviour:

- **Smoke phase**: authenticated `/v1/models`, a wrong-key request that must
  fail, arithmetic, and `/apply-template` + `/tokenize` consistency with reported
  usage. Then run `check_stub_tool.py` logic in-process (parse only, CIDR within
  the synthetic scope, no dispatch).
- **Prompt corpus**: realistic synthetic exercise histories. Each has a system
  prompt with scope, synthetic stub-tool call/result turns, and randomized filler
  from a seeded generator plus a repeatable corpus. Each session gets unique
  markers at its start, middle and end. Size every prompt with the server
  tokenizer to between 129,024−32 and 129,024 rendered tokens.
- **Cold cohort**: N independent full-size prompts released from a barrier.
  Prove the overlap from sampled `/slots` plus per-stream first/last token
  timestamps.
- **Warm cohort**: growing conversations that start at a
  `warm_start_tokens` value set in the workload profile (default 100,000). Each
  turn appends a user message and the model reply (reasoning included as the
  template requires) and replays the full history with no trimming. Retire a
  conversation before `input + 2048 > 131072` and start a fresh one. Never
  truncate.
- **Mixed arrivals**: at N>1, one cold arrival joins N−1 active warm sessions.
  At N=1, a second arrival queues; record the queue delay and do not allocate
  a slot for it.
- **Decode tasks**: at least some turns must require ≥512 completion tokens
  (for example, a structured incident summary), so early EOS cannot pass as a
  decode benchmark.
- **Metrics per turn**: queue time, TTFT, time to first visible answer token,
  reasoning and answer tokens separately, decode rate from real first/last
  token timestamps, total time, prepared vs reported prompt tokens, and
  cache-reuse fields from `/slots` if exposed.
- **Correctness per turn**: all own markers present, no other session's
  markers, follow-up continuity, and valid stub-call arguments where requested.
  A stream that finishes with only reasoning counts as a failure. If reasoning
  exhausts the 2,048-token output reserve, that also counts as a failure. Report
  it; do **not** raise `output_tokens`.
- **Volume**: at least 100 measured turns, at least 20 cold, and a soak of at
  least 30 minutes per N.
- **Verdicts**: a pass/fail for each spec §5 target, computed per session where
  the spec says so. Any transport, stream, parse, usage-mismatch, truncation,
  isolation, OOM or SLO failure gives a nonzero exit and a partial report on
  disk.

Resource sampling: add `evals/node_sampler.py`. It is a stdlib-only script run on
the node over SSH that writes JSONL every second. Each line has `nvidia-smi`
memory used/total and utilization, `/proc/meminfo`, the `pswpin`/`pswpout`/
`pgmajfault` counters from `/proc/vmstat`, and `llama-server` RSS. The evaluator
fetches it at the end and computes peaks. It also checks the GPU-free ≥10% margin
and the no-sustained-swap rule.

Report schema (JSON, one file per run): profile/record hashes, runtime commit,
GPU identity, driver, host RAM, argv, N, workload targets, sample counts, p50/p95
per metric, resource peaks, every verdict, errors, and start/end timestamps.
Credentials must never appear. Add a test that scans a sample report for the
API key value.

Tests with a fake llama-server (threaded `http.server`) must cover: overlap
detection, usage mismatch → fail, a reasoning-only stream → fail, a
cross-session marker → fail, a mid-stream disconnect → nonzero plus a partial
report, warm-cohort retirement before overflow, and no request above 129,024
input tokens.

### 4.4 GGUF inspection

Add `evals/inspect_gguf.py`. Use the `gguf-py` package from the **pinned**
llama.cpp commit. It checks `qwen4exp.attention.compress_ratios` (4 at each
full-attention layer, 0 elsewhere). It checks layer count, KV heads, head dim and
full-attention layer count against spec §4. It also totals the tensor bytes that
go to CPU (the `per_layer_token_embd` match) and to GPU. Write JSON. A metadata
mismatch fails `stage`.

Using the measured `W_gpu` from the inspection, print the spec §4 `N_memory`
estimate. Label it an estimate. It never changes admission.

### 4.5 Housekeeping

- Change CI branch filters from `main` to `master`. Add a job (or extend
  `harness.yml`) that runs `ruff` and `pytest` on `deploy/`, `evals/` and
  `ops/model_deploy.py`.
- Fix or retire `evals/llama_two_slot_context.py` (§2). Update the table in
  [evals/README.md](../evals/README.md).
- Do not edit `infra/*.tf`. If something there blocks the experiment, stop and
  report it.

**Offline exit criteria:** all tests pass locally and in CI, and
`model_deploy.py plan` renders records for N=1..4. Then post the G0 request.

## 5. Live execution (gated)

Source the repository-root `.env` without printing it before every provider or
OpenTofu command (AGENTS.md). Use the existing SSH key mechanism from
`ops/provision_node_secrets.py`. Keep every test port on localhost behind
`ssh -N -L 8001:127.0.0.1:8001`.

### 5.1 G0: inventory and availability (read-only)

1. The operator confirms a positive Verda balance (console).
2. List every GPU instance on the account: SKU, site, state, role. Check that
   adding one `1H200.141S.44V` stays within 1×H200 / 1×H100 / 2×RTX. **If an H200
   is already held, reuse it and skip G1.** If holding one would exceed the cap,
   stop.
3. Run `python3 ops/preflight.py --target-sku 1H200.141S.44V --target-tp 1 --target-location FIN-02`.
   Try other Finnish sites only when the weights volume can attach there
   (runbook §4). An available result is not a reservation.
4. Report the inventory table and availability.

### 5.2 G1: acquisition

From `infra/`, run `tofu plan -var-file=phases/p0-bake.tfvars` and show the plan.
It must create or keep exactly one H200 and keep the weights volume
(`prevent_destroy`). It must not replace any existing resource. The **operator**
applies. Afterwards, run `make fleet` and confirm `redcell-vllm` is healthy on
the node (`ops/check_inference.py`).

### 5.3 G2: stage (no interruption)

On the node, record the baseline: `nvidia-smi`, `free -h`, `df -B1 /mnt/weights /`,
`nvcc --version`, driver, CPU model/cores, and NUMA layout. Stop and report if
any of these hold:

- available RAM is below 80 GiB, or total RAM is below 128 GiB (spec §2);
- free space on the weights volume is below the GGUF size plus 20 GiB;
- the CUDA toolkit does not support sm_90. Install one that matches the driver,
  and record its version.

Then:

1. `model_deploy.py stage` downloads and verifies the GGUF, then runs the inspection.
2. Build the pinned llama.cpp with `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=90`,
   target `llama-server`, under `/mnt/weights/qwen38/llama.cpp-<sha>`. Save
   `git rev-parse HEAD`, compiler, CUDA, and cmake cache flags in `build.json`.
3. Run `llama-server --help` from the built binary and diff it against the
   required flags list. Any missing flag stops the experiment.

### 5.4 G3: trials

Tell the operator that `redcell-vllm` will be down for the whole trial window
(estimate in §7). After `GO G3`:

1. Save the current `redcell-vllm` unit and env hashes, then stop the unit.
   Confirm GPU memory is released.
2. For **N = 1, 2, 3, 4** in order:
   1. `model_deploy.py plan --sessions N`, then `trial start`.
   2. Before any traffic, check the loader log. It must show
      `n_ctx_seq = 131072` for every slot, `per_layer_token_embd` on CPU, all
      layers on GPU, F16 K/V, and no automatic context or fit reduction. Any
      deviation fails this N.
   3. Start `node_sampler.py`, then run `model_capacity.py` with N and the soak
      of at least 30 minutes.
   4. `trial stop`. Archive the server log, sampler JSONL, record and report
      together (§6).
   5. On fail, stop the ladder. `N_comfortable` = the last passing N (possibly 0).
3. Repeat the highest passing N once after a clean restart. It must pass again,
   or `N_comfortable` drops to the next lower N that passed.
4. **Tuning (only if the baseline fails and time allows; ask first).** Try these
   one at a time, each as a *new* profile with its own record and full run, per
   spec §4 and the old H200 note §6: `--ubatch-size 64`; Q8 cache if the pinned
   build supports it for this arch; PLE on GPU as a separate comparison profile.
   Never fold tuned results into the baseline profile.
5. `model_deploy.py restore` brings back `redcell-vllm`. Verify `/health` and
   authenticated inference with `ops/check_inference.py`.

### 5.5 G4: node disposition

Report elapsed GPU hours and the rate (€3.728/h listed). Default is to **keep**
the node (runbook: retain acquired capacity). Release it only on an explicit
operator instruction, via the normal Terraform path, and never destroy the
weights volume.

### 5.6 Abort and restore rule

Abort and run `trial stop` → `restore` in any of these cases:

- CUDA OOM, crash, swap storm, or disk full;
- a checksum or metadata mismatch;
- a context or placement mismatch;
- any sign that a credential was logged;
- the operator asks.

A failed restore is the highest-priority report. Include the unit status and the
last 100 journal lines, with secrets redacted.

## 6. Evidence and write-up

- Raw bundles (report JSON, server log, sampler JSONL, resolved record, build.json,
  GGUF inspection) go to `evals/results/qwen38-h200/<UTC timestamp>-N<n>/`
  (gitignored), plus a copy on the node under `/mnt/weights/qwen38/results/`.
  Both are EU-resident. Do not upload them anywhere else.
- Commit a small, secret-free summary as
  `docs/qualification/qwen38-h200-<YYYY-MM-DD>.md`. It holds the pins, hardware
  facts, a per-N table (sample counts, p95 TTFT warm/cold, p5 decode rate,
  peak VRAM/RSS, verdicts), `N_memory` vs `N_comfortable`, failures, and the
  #28734 observation. Link the raw bundle paths.
- Set `qualified_sessions` and status in the H200 hardware/workload profile
  **only** from a passing report, with the report hash.
- Update the H200 row of the evidence table in
  [IMPLEMENTATION-HANDOFF.md §4](IMPLEMENTATION-HANDOFF.md#4-validation-and-completion-evidence).
  Do not touch other rows.
- Do not claim router readiness, eight-team readiness, or anything about RTX/H100.

## 7. Time and cost estimate

These numbers are planning estimates, not measurements. Staging (download
119 GB, build) takes 1–2 h. Each N takes about 1–1.5 h: the load from NFS plus
a soak of at least 30 min, dominated by 20+ cold ~129k prefills. With four N
values and the repeat run, that is 6–8 h. Expect roughly **8–11 H200 hours
(€30–41)**, plus retries. There is no budget ceiling, but report any overrun
before it happens.

## 8. Handback to the operator

Finish with:

1. the commits (one per package in §4, plus the results summary);
2. the tests run and their results;
3. the gates passed and exactly what ran on the node;
4. the evidence row: runtime pin | GGUF sha | `N_comfortable` / policy 4 |
   report path | `qualified` or `failed`;
5. open issues: missing flags, #28734 status, reasoning-budget exhaustion,
   RAM/NFS observations, and a recommendation for the next node (RTX or H100)
   or for tuning profiles.
