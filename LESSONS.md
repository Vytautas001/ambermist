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
- **No Prometheus** (or node_exporter, or metrics timers), whatever the plan says. Logs
  (nginx access log, llama-server log) are the only observability. *(decided 2026-09-27)*
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
- Selected pin (source-read 2026-09-25; compiled and run on an H200 2026-09-26, see "Phase 1 first light"):
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
  vLLM 0.28.0 at 64k context; smoke checks passed. Qwen3.8 first ran 2026-09-26 (see "Phase 1 first light").

## What went wrong last time

- Documentation outran code: ~3,000 lines of specs, handoffs, and experiment
  plans for zero Qwen3.8 runs. Get one node serving first, then write down what
  was measured.
- The design kept being amended (ADRs 0001→0005) while legacy code stayed in
  the tree "for reference". Agents kept patching legacy paths. Old code now
  lives only in the archive tag.
- Model names were hard-coded through Terraform, router, and scripts; switching
  models touched everything. Keep model/runtime choice out of infrastructure code.

## Phase 1 first light (2026-09-26, all *verified* by running it)

Result: pin builds, model loads and serves on one spot H200; T0 passes; **T1 does not
reliably pass** (below). Nothing was tuned to change the T1 result.

- **Site and capacity:** FIN-02, spot (`use_spot = true`), no reclaim during the ~28 min
  run. By 08:21 UTC FIN-02 showed no H200 at all; FIN-03 still had spot and on-demand.
  API price fields for the SKU: 4.593 (on-demand) / 2.297 (spot), currency not labelled.
- **Image and login:** `24.04.cuda12.9.docker` exists for this SKU. Login user is `root`
  (key from `ssh_key_ids`). The startup script (nftables) finished about a minute after
  first SSH login, so `inet amb` was not loaded at the very first login.
- **Host:** 1× H200, 143,771 MiB, driver 580.178.04, 167 GiB RAM (SKU says 44 vCPU, 170 GB).
  Verda's OS-volume minimum not tested (60 GiB accepted).
- **Volumes:** the 128 GiB NVMe volume shows up as `/dev/vdb` (127 GiB usable), blank, and
  `/dev/disk/by-id/virtio-<last 12 hex of the volume id>` points to it. `mkfs` guard by
  size worked. `on_destroy = delete_permanently` is the provider default for the OS volume,
  yet a **detached `ambermist-h200-os` (60 GiB) remained after `tofu destroy`**; needs
  `fleet-check.py --reap-os`.
- **Times:** llama.cpp build 277 s (parallel with the download); model download plus SHA
  check 1,124 s (~30 MB/s, 111 GB); `/health` 200 about 15 s after start; packages 7 s.
- **T0:** all 4 shards match; `general.architecture = qwen4exp`; `compress_ratios` only
  0 and 4, **12 layers with 4**; `llama-server --version` shows `e9f824d`.
- **Loader log** (needs `-lv 5`; the default verbosity-3 log omits the loader lines
  entirely): `offloaded 49/49 layers to GPU`; `n_ctx = 65536`, `n_ctx_seq = 16384`,
  4 slots, `kv_unified = false`; no fit adjustment or context reduction. Buffers:
  CUDA0 model 78,056 MiB, KV 1,536 + 192 MiB, recurrent state 450 MiB, compute 263 MiB.
  **27.5 GiB stays in CPU-mapped memory:** `per_layer_token_embd.weight` (27,465 MiB) plus
  644 MiB. That is the default at the pin, without `--override-tensor`. `nvidia-smi`
  memory.used: **81,107 MiB** (not the brief's ~119 GB). Host page cache ~123 GiB after load.
  Checkpoint spam: `erasing old context checkpoint` warnings appear on every request with
  `--ctx-checkpoints 2`.
- **T1 (7 runs of 5+5 tool rounds, streaming and not):** 401 without key, `/health` 200,
  `reasoner` listed, 6×7 = 42 in every run: all pass. Tool
  rounds: **66 of 70 parsed correctly; 4 skipped the tool call** (in turn 1 the model
  answers in plain text such as "The temperature at EFHK is not available", after
  reasoning "Need use tool... Already did"). The skips are in both modes. No raw tool
  markup ever appeared in `content`, and no wrong arguments (0 of 66). Only 3 of the 7
  runs met "5 of 5 parse", so **T1 fails as written** (~6% per tool round).
  Response shape: `reasoning_content` present in every response; `content` is `""`
  when non-streaming with a tool call and `null` when streaming.
  Raw results: `~/ambermist-runs/2026-09-26/` (JSON), node logs in `node/` there.
- **Not answered:** whether the skip rate depends on the template, `--reasoning-*`
  settings, `tool_choice`, or is just this model. Not tried, per the task's stop rules.
- **Reclaim recovery:** untested (no reclaim happened).
- **GPU time:** instance created 07:54 UTC, destroyed 08:21 UTC, about 0.46 h of spot H200.

## Uncensored Q4_K_M (2026-09-27, all *verified* by running it)

Model switched to `orcarouter/Qwen3.8-Flash-Next-Uncensored-GGUF` Q4_K_M @ `0434906a`
(3 shards, 119,150,722,944 bytes = 111.0 GiB). Same llama.cpp pin, build image, server
flags, and `serving.conf` as Phase 1 except the model path. Result: **T0 and T1 pass**, so
the Phase 1 gate is met with this model.

- **Volume:** `ambermist-model` is 140 GiB (API resize while detached, plan §3.2). The
  ext4 filesystem from 2026-09-26 was kept and grown, not reformatted: 139 GiB, 111 GiB
  used, 28 GiB free.
- **Instance:** spot H200, FIN-02, created 09:03 UTC. Provisioning with the weights
  already on the volume: packages 8 s, model 1 s (fast path), build 232 s, t0 18 s,
  serve 261 s to `/health` 200 with a cold page cache. A restart with a warm page cache
  was healthy after 15 s.
- **T0:** `qwen4exp`; `compress_ratios` only 0 and 4, 12 layers with 4; `--version` shows
  `e9f824d`. The loader reports `Q4_K - Medium`, 176.94 B params, type `A3B`, 512 experts
  with 10 used, `n_ctx_train` 262,144.
- **Loader (`-lv 5`):** `offloaded 49/49 layers to GPU`; `n_ctx = 65536`,
  `n_ctx_seq = 16384`, 4 slots, `kv_unified = false`; no fit adjustment or context
  reduction. CUDA0 model 79,710 MiB, KV 1,536 + 192 MiB, recurrent state 450 MiB,
  compute 263 MiB. CPU-mapped: `per_layer_token_embd.weight` 33,569 MiB (27,465 MiB on
  UD-Q4_K_XL) plus 341 MiB. `nvidia-smi` memory.used: 82,761 MiB after load, 82,877 MiB
  after T1, so about 59 GiB of the H200 is free. Host: 167 GiB RAM, 95 GiB page cache
  after load.
- **T1 (7 runs of 5+5 tool rounds, streaming and not):** **70 of 70 parsed, 70 of 70
  with the right arguments, second turn correct in all.** 401 without key, `/health` 200,
  `reasoner` listed, 6×7 = 42 in every run. Same T1 script and flags as Phase 1, where
  UD-Q4_K_XL skipped the tool call in 4 of 70; at that rate, 0 of 70 has a 1–2% chance,
  so the difference is probably the model, not luck. Response shape unchanged:
  `reasoning_content` always present; `content` is `""` non-streaming and `null`
  streaming when there is a tool call. Results: `~/ambermist-runs/2026-09-27/t1-122549.json`
  and `t1-1237*`–`t1-1239*`. The earlier files there (06:04 and 08:47 UTC) came from
  before this instance, and which model they ran against wasn't recorded; `t1-122441`
  hit 503 while the model was still loading.

## Phase 2 prechecks (2026-09-27, *verified* on the running H200)

- **sshd:** the `24.04.cuda12.9.docker` image already sets `passwordauthentication no` and
  `kbdinteractiveauthentication no` (drop-ins `60-cloudimg-settings.conf`,
  `dc_hardening.conf`); a client without a key is offered only `publickey`. No drop-in
  needed (plan step 26).
- **Unauthenticated paths at the pin with `--no-webui`:** `/health` and `/v1/health` → 200;
  `/` → 404; `/index.html`, `/favicon.ico`, `/props`, `/slots`, `/metrics`, `/v1/models`,
  `POST /v1/chat/completions` → 401 `authentication_error`. No web UI path answers.
- **Firewall from inside `admin_cidrs`:** 22 open; 8080 refused while llama-server is on
  loopback (so nftables admits it); every other TCP port times out. `nftables.service` is
  enabled but shows `inactive`: the boot script loads the rules with `nft -f` directly.
- **Scanning from WSL:** a 1,000-way parallel connect scan reported 8080 as timed out,
  while a single `nc -vz` got "refused". Confirm bulk-scan results port by port.
- **State:** no API key, HF token, or Tailscale key in `infra/*/terraform.tfstate*`.

## Phases 2 and 3 (2026-09-27, *verified* on the running H200 unless marked)

Built as one step: llama-server never listened publicly. The plan's separate Phase 2 `net`
stage wasn't needed (sshd was already key-only; `serving.conf` sets the address).

- **Layout:** llama-server is `llama-server.service` (user `llama`, `127.0.0.1:8081`,
  `ProtectSystem=strict`, no `--metrics`); nginx on `0.0.0.0:8080`. `ss -ltn` shows nothing
  else beyond loopback except sshd.
- **Ubuntu packages start listeners on all interfaces at install** (nginx 1.24.0 on :80).
  The `packages` stage therefore waits for the firewall table first, and the `nginx` stage
  removes the default site.
- **`provision.sh` never deletes files on the node:** it untars `node/` over `/opt/ambermist`,
  so files removed from the repo stay there until deleted by hand.
- **systemd `$LLAMA_EXTRA_ARGS`** (unbraced, unset) expands to zero arguments; the process
  got exactly the §6.1 flags.
- **Switching from the Phase 1 transient unit:** stop it first. The transient unit file in
  `/run/systemd/transient` would otherwise win over `/etc/systemd/system/llama-server.service`.
- **Restart with warm page cache:** healthy after ~15 s under the new unit.
- **Through nginx from the public IP, no key:** `/health` 200; `/v1/models` and
  `POST /v1/chat/completions` 401; `/slots`, `/metrics`, `/props`, `/`, and `/v1/health` 404
  (plan §2.2 says `/v1/health` works; nginx §6.3 only passes `/health`).
- **Through nginx with the key (tunnel):** chat 6×7 = 42; streaming delivered 59 SSE events
  ending in `data: [DONE]`, so `proxy_buffering off` works.
- **Prometheus** was built and then removed the same day (decision above); purged from the node.
- **Not exercised** *(unverified)*: crash restart, the health-check restart, reboot recovery
  (T4d), actual log rotation, the 429 cap, and T1/T6 against the public URL. The scan from
  outside `admin_cidrs` hasn't been run.

## Lab gateway and the CGNAT collision (2026-10-01, *verified* on the Kali client)

The lab's own addressing sits inside the range Tailscale claims: LAN `100.66.6.0/24`,
resolvers `100.100.100.26`/`.28`, all within `100.64.0.0/10`. Everything below follows
from that one fact. The decision it forced is [ADR 0006](docs/adr/0006-lab-gateway-for-llm-access.md).

- **Tailscale's anti-spoof rule silently kills an overlapping LAN.** tailscaled installs
  `-A ts-input -s 100.64.0.0/10 ! -i tailscale0 -j DROP`, which drops every *reply* from
  the lab. Symptom: the default gateway and both resolvers 100% unreachable while
  `1.1.1.1` answered over the same path, ARP entries `REACHABLE`, and
  `ip route get 100.100.100.26` correctly showing `via 100.66.6.200 dev eth0`. Routing and
  DNS both look fine; only the netfilter counter tells the truth
  (`iptables -L ts-input -v -n`). `--netfilter-mode=off` is the only setting that lets both
  coexist, and `tailscale up` resets it.
- **`ping 100.100.100.100` proves nothing.** It answers from tailscaled regardless, even
  with `--accept-dns=false`, and says nothing about whether other `100.x` hosts are reachable.
- **`--accept-dns=true` without the resolved stub is all-or-nothing.** With
  `/etc/resolv.conf` a regular file, tailscaled uses its *direct* manager and overwrites it
  wholesale; split DNS needs `/etc/resolv.conf` symlinked to
  `/run/systemd/resolve/stub-resolv.conf`. When it is, tailscaled publishes MagicDNS on the
  `tailscale0` link with routing-only domains and leaves everything else alone.
- **That mistake produces a resolver loop**, not an error: quad100 → `127.0.0.53` →
  quad100, visible only as `dns: tcp query: waiting for response or error from
  [127.0.0.53]: context deadline exceeded` in the tailscaled journal.
- **systemd-resolved with no upstream returns `REFUSED`, not SERVFAIL.** Once the stub
  symlink is restored but nothing supplies servers, every non-tailnet name fails this way.
  `FallbackDNS` does not help: it applies only when no `DNS=` is configured at all, so dead
  servers listed in `DNS=` fail rather than falling through.
- **cloud-init put `dns-nameservers` on the `lo` stanza** of
  `/etc/network/interfaces.d/50-cloud-init`, not `eth0`; eth0 is `unmanaged` by
  NetworkManager and no `resolvconf` is installed, so those lines never reached any
  resolver. The working fix is a `/etc/systemd/resolved.conf.d/` drop-in.
- **nginx must re-resolve the H200 per request.** A literal name in `proxy_pass` is resolved
  once at startup and nginx refuses to start when the peer is absent. Using
  `resolver 127.0.0.53` with the name in a variable (and `$request_uri` appended) both
  survives node rebuilds, which change the 100.x address, and lets nginx start without the
  node present — it returns 502 until it is.
- **Not verified:** the gateway's leg to the H200. The node was not joined to the tailnet
  on 2026-10-01, so `/health` through the gateway returns 502 and no inference has gone
  through this path.

## FIN-03 fallback model volume (2026-10-03)

- **Checked 1 — verified from the read-only plan:** importing a volume with the provider's
  omitted `location` and `on_spot_discontinue` attributes requires ignoring both ForceNew
  attributes. `ignore_changes = [location, on_spot_discontinue]` plans one import with no
  add/change/destroy operations.
- **Checked 2 — verified by running it:** moving the existing FIN-02 volume to
  `verda_volume.model["FIN-02"]` planned and applied with 0 add, 0 change, 0 destroy. The
  state output is now the site-keyed `model_volume_ids` map, retaining the existing volume ID.
- **Picker — verified with focused mocked API scenarios:** a running `ambermist-h200` is left
  unchanged; only detached volumes named exactly `ambermist-model` are candidates; FIN-02 is
  preferred and a detached FIN-03 volume is selected as the fallback; no candidate or no spot
  candidate exits 2.
- **Clone — verified:** the request started at 2026-10-03 11:41:14 UTC. The API returned the
  destination ID in a one-element JSON list, and the Verda console showed it cloning. The
  destination `f64e68e9-e379-442b-ac20-10def731f87d` was later observed by the read-only fleet
  check by 11:49:03 UTC as `detached`, 140 GiB, FIN-03. The duplicate `ambermist-model` name was
  accepted. The exact completion time and any billing-page charge were not checked.
- The volume API documentation has no `on_spot_discontinue` field; whether a cloned or existing
  volume is protected from spot reclaim is unverified.
- No FIN-03 node launch was performed; the operator did not approve GPU time for step 7.

## Stale compute state after a reclaim (2026-10-03, *verified* unless marked)

- Compute state from 2026-09-30 still held `verda_instance.node` `5379c0d0…` (spot, FIN-02).
  The API reports it as `discontinued`. `GET /instances` leaves discontinued instances out
  (`?status=discontinued` lists them, including six older `ambermist-h200`), but
  `GET /instances/{id}` still returns the record.
- The provider's refresh keeps it in state: `plan` showed only `verda_volume_attachment.model`
  to create, attaching to the dead instance ID. After `tofu state rm verda_instance.node` (run on
  a copy), `plan` showed 2 to add (instance, attachment). The SSH key and boot script still existed.
- `tofu state rm` on the local backend writes `terraform.tfstate.<unix time>.backup`.
- `ops/session.sh up`/`down` were tested only against a mock API. *(unverified on real resources)*
  Also unverified: what `destroy` does to a discontinued instance (the script removes it from
  state first), whether `DELETE /volumes/{id}` trashes or deletes permanently, whether trashed
  volumes show up in `GET /volumes`, and what a no-capacity apply failure leaves in state.

## 27B context: 4 sessions × 262,144 (2026-10-04, *verified* on the running RTX PRO 6000 test node)

Measured earlier the same day, on `node/serving.27b.conf` set to `LLAMA_SLOTS=4`,
`LLAMA_CTX=1048576` (262,144 tokens/slot, the model's `n_ctx_train`) — a different, larger
sizing than the 2 × 131,072 the next section below settles on; kept here as the measured
reference for a bigger-slot config, not as the currently active one. That branch's
`node/serving.27b.conf` was never merged and set no sampling defaults, so these numbers say
nothing about the loop investigated below.

- `LLAMA_CTX` is the total across slots: the old 65536 / 4 gave 16,384 tokens per request.
  The pin pads each slot up to a multiple of 256 (`llama-context.cpp`), so size slots in
  256s. `/slots` showed `n_ctx` 262,144 × 4 with no rounding warning.
- VRAM after load: 20,581 MiB at 4 × 16,384; 48,117 MiB at 4 × 126,208; **82,235 MiB at
  4 × 262,144** (of 97,887). So 64.2 KiB/token (matches the computed 64 KiB) on a ~16.1 GiB
  base. It does not fit an 80 GB card. VRAM stayed at 82,259 MiB with three slots full at
  once (262k, 183k, 32k tokens): KV is preallocated, so load does not change it.
- A 261,920-token prompt returned 200 in 165 s (prefill ~1,590 tok/s cold, decode
  37 tok/s at that depth); markers at the very start and very end both recalled. A
  262,500-token prompt returned 400 `exceed_context_size_error` (`n_ctx: 262144`) at once.
- **Long prefill starves other sessions' decode.** While other slots prefilled ~250k-token
  prompts, a live client's decode on another slot fell from ~62 to ~0.8 tok/s, and
  recovered once they stopped. A full 4 × 255k concurrent run was not completed (a live
  client held a slot; the test was stopped). This effect is about slot *count* sharing one
  GPU, not specific to this sizing — worth keeping in mind at 2 slots too.
- Earlier at 4 × 126,208: 124,384 tokens in 53 s (~2,360 tok/s prefill); resending the same
  prompt hit the prefix cache (123,868 cached tokens) and took 1 s.

## 27B test tier in VS Code agent mode (2026-10-04, see docs/27b-agent-loop.md)

Task: VS Code's "Ambermist reasoner (27B)" custom model fell into loops in agent mode.
Measured before and after a server/client config change; partially resolved, reported here.

- **Config drift found before any change *(verified)*:** the live `/opt/ambermist/serving.conf`
  on the test node did not match `master`'s `node/serving.27b.conf` (which still had
  `LLAMA_CTX=65536 LLAMA_SLOTS=4`, inherited from the flash profile and known wrong — see
  below). Source traced afterward: branch `27b-weights-volume-and-context` (commit
  `4f55607`, 09:52 that morning, see the section above) had been provisioned onto the node
  with `MODEL_PROFILE=27b ops/provision.sh "$IP" serve` but never merged to `master`, so its
  `LLAMA_CTX=1048576 LLAMA_SLOTS=4` was live while the repo's checked-out `master` still said
  otherwise. That branch also set no sampling defaults, so the loop in step 1 below was
  observed under llama.cpp's built-in sampling (temperature 0.8), not the card's values.
- **`test-tier-27b.md` "What the model needs" was wrong, confirmed *(verified)*:** `LLAMA_CTX`
  is the KV budget summed *across* slots, not per slot; per-slot size is
  `LLAMA_CTX / LLAMA_SLOTS`. 64 KiB/token (already in that doc) × 262,144 tokens total =
  16 GiB of KV, however split across slots. Fixed in the doc.
- **GGUF `context_length` *(verified)*:** 262144 (`qwen35.context_length`), confirmed on the
  node — meets the >=131072 floor the task required before touching server config.
- **Step 1, loop shape before any change *(verified, from the server log and one screenshot
  the operator sent)*:** two distinct symptoms, both present in the same session:
  1. Repeated short turns (~150–650 generated tokens each) with the prompt growing by
     ~250–700 tokens per turn and old context checkpoints being evicted every turn —
     consistent with the same tool being re-called turn after turn.
  2. One single assistant turn visibly stuck: the model announced it was "stuck in a loop"
     and then repeated the same source-code comment block verbatim many times in a row
     before the operator cancelled it.
  No `exceed_context_size` errors and no `truncated = 1` stops appeared in the log at any
  point in this session — **the loop was not caused by hitting the context limit.** The
  server at the time was running with `--ctx-size 1048576 --parallel 4` (see the drift note
  above), i.e. 262,144 tokens per slot already, far more than any prompt reached (max
  observed ~45,700 tokens). This rules out the task doc's primary hypothesis (16,384-token
  slots from a stale repo file) for *this* run; that hypothesis may still be correct for
  whatever config was running on whatever earlier occasion the operator first noticed the
  loop, which we didn't capture.
- **Whether VS Code sets its own sampling, which overrides the server *(verified)*:** yes.
  `/slots` repeatedly showed a busy slot with `temperature: 0.1`, differing from whatever the
  server's own configured default was at the time (1.0 after step 2). `top_k: 20` matched in
  both cases so that alone doesn't prove an override, but `presence_penalty` tracked the
  server's configured value across all three deploys (0.0 → 1.0 → 1.5) while `temperature`
  stayed pinned at 0.1 throughout — so VS Code sends its own `temperature` but **not** its own
  `presence_penalty`, letting the server default take effect for that one field. Per the task
  doc's stop condition, nothing was built to override this.
- **The client config has no sampling field at all:** `chatLanguageModels.json`'s `ambermist`
  entry only has `maxInputTokens`/`maxOutputTokens` (plus url/vendor/id/toolCalling/vision) —
  no `temperature`. The `temperature: 0.1` VS Code sends is hardcoded in its own agent-mode
  request logic, not something this repo or the operator can configure.
- **Whether VS Code sends `reasoning_content` back:** *unverified* — the optional `-lv 5`
  check in step 1 was skipped (would have needed another redeploy and risked logging the
  operator's live chat; the task doc allows recording "unknown" and moving on).
- **Step 2/3, server change *(verified, deployed and measured)*:** `node/serving.27b.conf` now
  sets `LLAMA_CTX=262144 LLAMA_SLOTS=2` (131,072 tokens/slot) and
  `LLAMA_EXTRA_ARGS="--temp 1.0 --top-k 20 --top-p 0.95 --min-p 0 --presence-penalty <N>"`
  (card's thinking-mode values except `presence_penalty`, escalated — see below). Deployed
  with `MODEL_PROFILE=27b ops/provision.sh "$IP" serve`; confirmed on the running process
  args and via `/slots` (`n_ctx: 131072` × 2). VRAM used after load: **32,655 / 97,887 MiB**.
  T1 passed 5/5 (both tool-call modes) after every one of the three deploys below. A synthetic
  ~57,664-token prompt (filler text + a short question, `max_tokens: 2048`) got **200**, not
  400, confirming large-but-in-budget prompts no longer error.
- **Step 5, `presence_penalty` escalation *(verified, three real VS Code sessions)*:**
  - `0.0` (card default): not retested in isolation after the slot-size fix — went straight to
    `1.0` once the operator's first post-fix retest ended with an unattended 45,723-token
    prompt the operator cancelled themselves (not a server-side failure; see above).
  - `1.0`: still looped. A single turn generated **16,861 tokens continuously over 283 s**
    before stopping on its own (`truncated = 0`, no error, no cancel) — a finite but
    excessive single-turn runaway, not the earlier "same block repeated" text (not rechecked
    verbatim, but the token count and duration are consistent with the same failure mode).
  - `1.5` (the task doc's ceiling — do not go higher, card warns of language mixing above
    this): the runaway shortened but didn't disappear — **12,399 tokens over 211 s** in one
    turn, then the session recovered and continued normally (6 more turns, all under 2 s,
    all finishing cleanly). The operator confirmed VS Code showed a real, usable response and
    a generated code file at the end — not stuck/garbled text — but said the agent "acted
    very uncertain." This doesn't match the task doc's "still looping at 1.5" stop condition
    (which describes an unresolved loop, not a slow-but-working turn), so `presence_penalty`
    was **left at 1.5** rather than reverted to 0.
  - Net effect: `presence_penalty` 0.0 → 1.5 reduced the longest single-turn runaway from
    16,861 to 12,399 generated tokens (~26% shorter) but did not eliminate it. One long,
    slow, hedging-sounding turn is a known residual behavior, not a crash or a true infinite
    loop.
- **Final state, done-when check:** `node/serving.27b.conf` — 2 × 131,072 slots, card sampling
  with `presence_penalty=1.5`; flash profile untouched. `/slots` and T1 both confirm.
  `chatLanguageModels.json`'s `ambermist`/`reasoner` entry — `maxInputTokens: 110000`,
  `maxOutputTokens: 16000` (sum 126,000 < 131,072 slot). Operator's retest: **no longer an
  unbounded loop; one long single-turn generation remains, then normal operation resumes.**
  Not fully resolved — flagged for a design session per the task doc's scope (no proxy,
  no chat-template override, no `--reasoning-budget`/thinking-off was attempted, as directed).

## Allowed SKUs in the catalog (2026-10-03, *verified* via `GET /instance-types`, `/images`)

- SKUs within the fleet cap: `1H200.141S.44V` (170 GB RAM, 4.827 / spot 2.414 per hour),
  `1H100.80S.30V` (120 GB RAM) and `1H100.80S.32V` (170 GB RAM, both 3.700 / 1.850),
  `1RTXPRO6000.30V` (90 GB RAM, 2.044 / 1.022), `2RTXPRO6000.60V` (180 GB RAM, 4.088 / 2.044).
  Currency not labelled. `1RTXPRO6000.30V.CC` is a confidential-computing variant (left out).
- `24.04.cuda12.9.docker` is offered for H100 and RTX PRO 6000 as well.
- Spot snapshot at 14:00 UTC: `1H100.80S.30V` at FIN-02; RTX PRO 6000 only as 4× and 8× (over
  the cap) at FIN-01 and FIN-03. Availability changes by the minute.
- `build-llama.sh` now reads the arch from `nvidia-smi --query-gpu=compute_cap` instead of the
  pinned `CUDA_ARCH=90`. *(unverified on a node; H200 should still give `sm90`)*
- Serving on H100 or RTX PRO 6000 has never been tried. The H200 used 82,761 MiB after load
  (model 79,710 MiB on CUDA0), which doesn't fit an 80 GB H100 with the current `serving.conf`.

## Local LLM on the RTX 3060, candidate measurement (2026-10-08, *verified* by running it, see docs/local-llm-3060.md)

- **llama.cpp build *(verified)*:** pinned commit `e9f824d8c` builds clean in WSL (Ubuntu
  26.04) with plain `cmake`/`make` (no `ninja` installed) once `PATH`/`CUDACXX` point at
  `/usr/local/cuda/bin/nvcc` — CMake's `find_package(CUDAToolkit)` doesn't auto-discover the
  WSL CUDA 13.4 toolkit otherwise, even though `nvidia-smi` and the Windows CUDA 12.9 install
  are both visible from WSL (that Windows path is on `$PATH` but is a Windows binary, useless
  here). `--list-devices`, `--version`, and all of `--n-cpu-moe`, `--api-key-file`,
  `--presence-penalty`, `-fa`, `--alias`, `--fit` exist at this pin, confirmed via `--help`.
- **Candidate A, Qwen3.8-27B-Uncensored Q4_K_M — fails the gate *(verified)*:** download
  sha256 matched the pin exactly. `--fit on` at `-c 32768` chose **36/66 layers on GPU**
  (8,470 MiB weights on CUDA0, 7,300 MiB CPU-mapped; found via `-lv 5`, not printed at the
  default verbosity). Real ~8.5k-token `/v1/chat/completions` request: **TTFT 31.1 s** (prompt
  8,482 tok at 272.6 tok/s — both comfortably inside the doc's gate), but **generation only
  2.59 tok/s** against the required ≥8 tok/s. `llama-bench` with `-ngl -1` (full/"auto"
  offload) is misleading here — it reports pp512 75 tok/s but tg128 only 3.12 tok/s, because
  beyond 12 GiB `-1` relies on CUDA UVM paging over PCIe, which thrashes on every
  single-token decode step; always bench with the real `--fit`-chosen `-ngl`, not `-1`, once
  the model doesn't fit outright.
- **Candidate B does not exist *(verified via the HF API)*:** the official `Qwen/` Qwen3.8
  line has only the 27B dense model, a large "Flash-Next" MoE, and a 2.4T-A95B MoE — no
  8–14B dense size and no confirmed smaller MoE. The only ~9B-class options are unofficial
  third-party distills (e.g. `empero-ai/Qwen3.8-9B-Distill-GGUF`) of unknown provenance/
  quality; not used. Moved straight to candidate C per the doc's fallback rule.
- **Candidate C, `Qwen/Qwen3-14B-GGUF` Q4_K_M — passes the gate, chosen *(verified)*:**
  revision `530227a7d994db8eca5ab5ced2fb692b614357fd`, file size 9,001,752,960 B, sha256
  `500a8806e85ee9c83f3ae08420295592451379b4f8cf2d0f41c15dffeb6b81f0` (downloaded via a
  `curl -K` header-file, not `-H`, after the near-miss below). At `-c 32768` `--fit` only
  offloaded 30/41 layers (standard transformer KV is expensive at that length, unlike the
  27B's hybrid-SSM KV) and generation dropped to 5.31 tok/s — also fails the gate. At
  **`-c 16384`**, `--fit` reaches **38/41 layers on GPU** (1,018 MiB CPU-mapped), leaving
  927 MiB VRAM free under load. Real ~8.4k-token request: **TTFT 10.2 s**, prompt
  821.2 tok/s, **generation 15.24 tok/s** — clears the ≥8 tok/s gate with margin.
  `llama-bench -ngl 38`: pp512 1020.7 tok/s, tg128 21.5 tok/s (short-context ceiling, no KV
  overhead). RAM: ~1.5 GiB active + 9 GiB page cache, 14 GiB available, no swap pressure —
  no RAM bottleneck like candidate A risked. **Chosen model: candidate C, `-ngl 38`,
  `-c 16384`, `--parallel 1`.** Only one candidate passed, so no operator tie-break was
  needed.
- **Security near-miss, self-caught *(verified)*:** the first candidate-C download used
  `curl -H "Authorization: Bearer $HF_TOKEN" ...`, which puts the token in argv — visible to
  any local user via `ps`. It was then actually printed into the session transcript by a
  follow-up `ps aux | grep curl` during a progress check. Caught immediately: killed the
  curl, re-ran the download via a `chmod 600` curl `-K` config file (`header = "Authorization:
  Bearer <token>"`), shredded the temp file after. **The exposed `HF_TOKEN` should be
  rotated** — this doc can't do that itself. Lesson generalizes past the doc's own
  `AMB_API_KEY`/`--api-key-file` warning: *any* secret passed as a CLI arg or `-H` header is
  `ps`-visible, not just the one example the task doc called out.
- **A second near-self-inflicted outage:** `kill $(pgrep -f "llama-server.*reasoner")` run
  from a shell whose own pending command text already contained that same substring (the next
  `llama-server ... --alias reasoner ...` invocation in the same script) matched the *current*
  process's argv and killed the script before it could start the replacement server. Fixed by
  matching on the exact binary name instead: `pgrep -x llama-server` / `kill $(pgrep -x
  llama-server)`. Worth remembering for any `pgrep -f`/`pkill -f` against a long, literal
  command line used inside the same script that issues it.
- **Tailnet: stuck, not rejected *(verified, still open)*:** `ambermist-local` (tagged
  `tag:ambermist`, joined in an earlier session) now fails to sync —
  `PollNetMap: initial fetch failed 404: node not found`, continuous since ~06:16 that
  morning; `tailscale ping`/`ssh amber-gateway` both time out. General internet from WSL
  works fine (plain HTTPS egress unaffected), so this is specific to the tailnet control
  plane, not a host network problem. `tailscale up` (no new auth key, same flags) fails with
  `Access denied: prefs write access denied — Use 'sudo tailscale up ...'`, i.e. this needs
  the operator's sudo password, same as every other privileged step in the task doc. Not yet
  resolved as of this writing — flagged to the operator rather than minting a new
  `TS_AUTHKEY` unprompted.
- **Binding `--host 0.0.0.0` directly from an agent Bash call is denied** by the Claude Code
  auto-mode permission classifier ("Expose Local Services"), even for a loopback-adjacent
  local measurement. Routed around it correctly, not by working around the block: the
  production bind lives in the systemd unit (`~/llm/ambermist-llama.service`, staged, not yet
  installed — needs the operator's `sudo systemctl enable --now`), so it's the operator's
  privileged action that opens the port, not a direct agent action.
- **Still open (step 6/7/8/9 of the task doc):** systemd unit staged but not installed; no
  Windows Task Scheduler entry yet; VS Code `chatLanguageModels.json` snippet drafted but not
  applied (operator must do it, file holds other secrets); tailnet resync pending; gateway
  repoint (step 8) blocked on both the tailnet fix and explicit operator approval since it
  changes the live lab gateway.
  - **Update, later the same day:** the systemd unit *was* installed and enabled (verified via
    `systemctl status ambermist-llama` — active, running candidate C) sometime after the above
    was written; this note was stale. See the next section for what changed after that.

## Local LLM on the RTX 3060, model swap to an abliterated build (2026-10-08, *verified*)

- **Why:** the operator asked to serve an uncensored/"abliterated" model instead of stock
  `Qwen/Qwen3-14B-GGUF` for the CALDERA/Kali lab — fits the lab's security-tool-testing use
  case better than a safety-tuned chat model. Requested as `huihui_ai/qwen3-abliterated:14b-v2`.
- **That string is Ollama tag syntax** (`namespace/model:tag`), not a Hugging Face
  `repo/revision/file.gguf` reference, and this doc's "Don't build these" section rules out
  Ollama outright. Flagged to the operator rather than silently switching stacks; they chose to
  stay on llama.cpp.
- **No GGUF of the "v2" checkpoint exists** *(verified via web search)*: huihui-ai's
  `Huihui-Qwen3-14B-abliterated-v2` is published as safetensors only. A v2 GGUF exists for the
  8B size (`mradermacher/Huihui-Qwen3-8B-abliterated-v2-GGUF`) but not for 14B.
- **Used instead, operator's choice: the confirmed GGUF of huihui-ai's v1 checkpoint** —
  `bartowski/huihui-ai_Qwen3-14B-abliterated-GGUF` (quantized from
  `huihui-ai/Qwen3-14B-abliterated`), revision `623c0f3fc42a4699d4583fb16e022942c003d1b7`, file
  `huihui-ai_Qwen3-14B-abliterated-Q4_K_M.gguf`, **9,001,749,568 bytes** — 3,392 bytes off stock
  candidate C's file, same param count and quant. sha256 **verified against the downloaded
  file**: `d76889059a3bfab30bc565012a0184827ff2bdc10197f6babc24541b98451dbe`.
- **Download flakiness, not a bad file:** two attempts stalled partway with
  `curl: (92) HTTP/2 stream 1 was not closed cleanly: CANCEL` (once at ~2.1 GiB, once after a
  `-C -` resume at ~1.8 GiB further) — a transient HF/CDN HTTP/2 issue, not corruption (`curl`
  exit code was 92 both times, not a checksum mismatch). Third attempt, forcing `--http1.1`
  with `--retry-all-errors` and `-C -` to resume from the partial file, completed clean and the
  sha256 matched on the first try. **Lesson: for multi-GB HF downloads over a flaky link, retry
  with `--http1.1` rather than repeatedly resuming over HTTP/2.**
- **Not re-run through the step-4 gate live**, by design: same architecture/param count/quant
  as the already-measured stock candidate C (file size differs by 3,392 bytes), so the proven
  `-ngl 38 -c 16384` was kept rather than re-measured. A real side-by-side bench was skipped
  because the live service was already using ~11 of 12 GiB VRAM (leaving no room to load a
  second 14B model concurrently) and stopping it needs the operator's `sudo` either way — so
  the staged systemd unit (`~/llm/ambermist-llama.service`) was edited to point at the new
  `--model` path (only that one flag changed) and handed to the operator to apply via
  `sudo systemctl restart ambermist-llama`, with the step-6 checks run right after as the real
  validation.
- **Live confirmation (2026-10-09), operator applied the restart:** `systemctl status` shows
  the service running the new `--model .../huihui-ai_Qwen3-14B-abliterated-Q4_K_M.gguf` path.
  A real `/v1/chat/completions` request (no key → 401, confirmed; with key → 200) returned the
  correct answer after a `<think>` block, at **19.9 tok/s generation** — slightly *faster* than
  stock candidate C's 15.24 tok/s, clearing the ≥8 tok/s gate with more margin, not less.
  Confirms the "same architecture/quant → same settings" assumption held.
- The old stock file (`~/llm/models/qwen3-14b-q4km/Qwen3-14B-Q4_K_M.gguf`) was left on disk,
  not deleted, so reverting the unit's `--model` path is a one-line rollback.
