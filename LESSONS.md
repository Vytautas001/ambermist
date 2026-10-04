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
