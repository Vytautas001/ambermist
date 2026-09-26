# Inference tier: implementation plan

Self-hosted, OpenAI-compatible LLM endpoint on one Verda H200, served by llama.cpp.

Status: **plan, 2026-09-25. None of this has been built or run.** Phase 1 is being built
from [phase-1-first-light.md](phase-1-first-light.md), which cuts its scope. For Phase 1,
that document wins wherever the two differ.
Labels: `[ASSUMPTION]` means chosen without evidence; replace it with a measurement or a
decision. `[VERIFY]` means check it before the step that depends on it. Facts marked
*verified* were checked on 2026-09-25 against the named source.

Read [LESSONS.md](../LESSONS.md) first. This plan builds on it and does not repeat it.

---

## 1. Executive summary

**Approach.** One Verda `1H200.141S.44V` instance runs `llama-server` (llama.cpp pinned at
`e9f824d8`), serving `unsloth/Qwen3.8-Flash-Next-GGUF` UD-Q4_K_XL with 4 slots of 16,384
tokens. The instance is disposable. Two OpenTofu stacks keep it (`compute`) apart from
three block volumes (`storage`: model, build, logs). Each volume has a `keep_*` flag that
decides whether teardown destroys it. A warm start reattaches the volumes, skips the
111 GB download and the build, and serves again in about 10–20 min `[ASSUMPTION]`.
Callers reach the endpoint on the node's public IP, `http://<public_ip>:8080/v1`, with a
bearer key, and only from `admin_cidrs`. Once everything else is proven, Phase 6 moves
the endpoint behind Tailscale (§2.6).

**Two requirements can't be met as written on Verda, and the plan works around both.**
Verda's instance API takes no networking parameters: no VPC, no security groups, and no
option to create an instance without a public IP (*verified*, Verda API reference). The
node therefore has a public IPv4 address. nftables drops all inbound traffic except SSH
and the API (`tcp/22`, `tcp/8080`) from the operator's `admin_cidrs`, and every other
service binds to loopback. Also, Verda's `shutdown` action keeps billing (*verified*, API
reference), so "stop" is implemented as a soft destroy.

**Cost envelope.** On-demand is €3.728/h and storage is €0.20/GiB-month (Verda console
prices, 2026-09, in LESSONS.md).

- Floor while nothing runs, with default flags: **€29.60/month** (model 128 GiB + build 20 GiB).
- 4 h/day: ~€510/month. 8 h/day on weekdays: ~€700/month. 24/7: ~€2,750/month.
- Reserved contracts save 2–25%. They beat create/destroy only above 75% duty (2-year
  term) or 92% duty (1-year term). Spot halves the hourly rate and is the biggest cost
  lever for non-critical sessions.

**Key risks.**
1. Qwen3.8 has never run on this stack, and the llama.cpp pin has never been compiled.
   Phase 1 is a go/no-go gate.
2. It is unverified whether llama.cpp parses this model's chat template into `tool_calls`
   (test T1).
3. 16,384 tokens per slot conflicts with the recorded decision of 131,072-token slots, and
   may be too small for a reasoning model that uses tools (open question Q1).
4. On-demand H200 capacity can't be reserved. After a `down`, the next `up` can fail at the
   same site, and volumes are bound to their site.

**Phases.** Each phase ends with a gate. If the gate fails, stop and report.

| Phase | Outcome | Gate | GPU time `[ASSUMPTION]` |
| :-- | :-- | :-- | :-- |
| 0 | Decisions made, account prepared | Q4 answered; fleet check clean and site picked (Phase 1 task, step 3) | 0 |
| 1 | First light: model served on loopback | T0 and T1 pass through an SSH tunnel | 1–2 h |
| 2 | Public API, allowlisted | T1 passes against the public IP from `admin_cidrs`; T6 clean | 1 h |
| 3 | nginx front, supervision, observability | T4 passes; metrics visible in Prometheus; T6 rerun with the nginx checks | 1 h |
| 4 | Lifecycle automation | T5 and T8 pass; audit clean | 1–2 h |
| 5 | Acceptance | T2, T3, T7 pass; contract numbers filled in (§2.2) | 1–2 h |
| 6 | Tailscale (§2.6), only after Phase 5 passes | T1 passes over Tailscale; public 8080 closed; T4d, T5, T6 rerun | 1 h |

Total: about 6–9 GPU-hours, or €22–34, plus storage. After each phase, write the measured
facts into LESSONS.md. §2.4 checks each fixed parameter from the brief against its source.

---

## 2. Architecture

### 2.1 Diagram

```
  OUT OF SCOPE                  :  VERDA CLOUD, one site (e.g. FIN-02)
                                :  no VPC, no security groups, no private interconnect
+---------------------------+   :  +--------------------------------------------------------+
| HOSTS INSIDE admin_cidrs  |   :  | instance ambermist-h200 (1H200.141S.44V, on-demand)    |
| (operator ISP ranges)     |   :  |                                                        |
| operator workstation:     |   :  | eth0  Verda public IPv4 (cannot be removed)            |
|  tofu state, secrets,     |   :  |       nftables inet amb: policy drop, then allow only  |
|  make; API callers        |   :  |         tcp/22, tcp/8080 from admin_cidrs              |
+-------------+-------------+   :  |                                                        |
              |                 :  |                                                        |
              +-- HTTP :8080 ---:--|--> eth0 :8080 nginx -> /health /v1/models              |
              |                 :  |         |          /v1/chat/completions only           |
              |                 :  |         |          access log, 12-connection cap       |
              +-- SSH :22 ------:--|--> eth0 :22   sshd, keys only                          |
                                :  |         v                                              |
                                :  | lo    :8081 llama-server  GPU0, 4 slots x 16,384 tok   |
                                :  |       :9090 prometheus                                 |
                                :  |       :9100 node_exporter                              |
                                :  |                                                        |
                                :  | /            OS volume        60 GiB NVMe  ephemeral   |
                                :  | /srv/models  ambermist-model 128 GiB NVMe  keep=true   |
                                :  | /srv/build   ambermist-build  20 GiB NVMe  keep=true   |
                                :  | /srv/logs    ambermist-logs   10 GiB NVMe  keep=false  |
                                :  +--------------------------------------------------------+
                                :   outbound (not restricted): huggingface.co and its CDN,
                                :   github.com, docker.io, Ubuntu mirrors
```

The diagram shows Phases 3–5. In Phases 1–2 there is no nginx: llama-server itself listens
on port 8080, on loopback in Phase 1 and on the public IP in Phase 2 (§6.1).

There is no subnet layout: Verda has no VPC and no private networking. Callers use the
node's public IP, which changes on every rebuild; `make up` prints the new base URL (the
`api_url` output). Phase 6 (§2.6) replaces this with a stable Tailscale name.

### 2.2 Interface contract (consumer-facing)

This is the only section the consumer team needs. After Phase 5, copy it into the
consumer's documentation with the measured values filled in.

| Item | Value |
| :-- | :-- |
| Base URL | `http://<public_ip>:8080/v1`, printed by `make up` as `api_url`. It changes on every rebuild. After Phase 6: `http://ambermist-h200.<tailnet>.ts.net:8080/v1`, which stays the same. |
| Protocol | HTTP/1.1 in plaintext over the internet until Phase 6 (risk 15, Q8). OpenAI Chat Completions API; streaming uses SSE. |
| Endpoints | `POST /v1/chat/completions`, `GET /v1/models` (key required), and `GET /health` (no key; `/v1/health` is the same): 200 `{"status":"ok"}` when ready, 503 `{"error":{"code":503,"message":"Loading model","type":"unavailable_error"}}` while loading. From Phase 3, every other path returns 404 at the proxy. In Phase 2, llama-server's other endpoints answer too. |
| Authentication | `Authorization: Bearer <key>`. A missing or wrong key returns 401 `{"error":{"code":401,"type":"authentication_error","message":"Invalid API Key"}}`. The server accepts a list of keys, so rotation can overlap old and new (§7.7). |
| Model name | `reasoner`. This alias stays the same when the weights change. Send `"model":"reasoner"`. |
| Network path | Direct to the node's public IP. nftables accepts `tcp/8080` only from `admin_cidrs` (§4.3), so callers elsewhere can't connect. Phase 6 moves the API onto Tailscale (§2.6). |
| Concurrency | 4 requests generate at once, one per slot. Requests 5–12 wait in llama-server's queue (first in, first out, no server-side timeout). From Phase 3, a 13th simultaneous connection gets `429` with `Retry-After: 15`; in Phase 2 nothing caps the queue. Callers should cap their own concurrency at 4. |
| Per-request budget | Prompt + reasoning + completion ≤ 16,384 tokens. A prompt of 16,384 tokens or more (after templating) returns 400 `exceed_context_size_error`. If generation reaches the limit, it ends early with `finish_reason:"length"` `[VERIFY T3]`. Always set `max_tokens`. |
| Timeouts | From Phase 3, the proxy's read timeout is 900 s. Clients: connect 10 s, total ≥ 900 s for non-streaming requests, and an idle timeout ≥ 120 s between SSE events. |
| Retries | Retry 429, 503, and connection errors with backoff. Don't retry 400 or 401. The server has no side effects, so a retry is safe, but a retried request re-runs in full. |
| Availability | Best effort, one node, no SLA. The endpoint is down whenever the tier is `down` (see §7). |

**Allowlist entries**

| Where | Entry | Purpose |
| :-- | :-- | :-- |
| Node nftables | `tcp/22` from each `admin_cidrs` entry | SSH administration (keys only) |
| Node nftables | `tcp/8080` from each `admin_cidrs` entry | API (bearer key) |

**Request (tool call, non-streaming)**

```json
{
  "model": "reasoner",
  "messages": [{"role": "user", "content": "What is the temperature at EFHK?"}],
  "tools": [{
    "type": "function",
    "function": {
      "name": "get_station_temperature",
      "description": "Current air temperature at an ICAO weather station.",
      "parameters": {
        "type": "object",
        "properties": {"station_id": {"type": "string", "enum": ["EFHK", "EFTU"]}},
        "required": ["station_id"]
      }
    }
  }],
  "tool_choice": "auto",
  "max_tokens": 4096,
  "stream": false
}
```

**Response**

```json
{
  "object": "chat.completion",
  "model": "reasoner",
  "choices": [{
    "index": 0,
    "finish_reason": "tool_calls",
    "message": {
      "role": "assistant",
      "content": null,
      "reasoning_content": "…",
      "tool_calls": [{
        "id": "call_…",
        "type": "function",
        "function": {"name": "get_station_temperature", "arguments": "{\"station_id\":\"EFHK\"}"}
      }]
    }
  }],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

`arguments` is a JSON **string**. `reasoning_content` appears when the template emits
thinking; llama-server defaults to `--reasoning-format auto`. Whether this model emits it
is `[VERIFY T1]`, and `content` may be `""` rather than `null` `[VERIFY T1]`.

To continue the conversation, append the assistant message unchanged, then add
`{"role":"tool","tool_call_id":"call_…","content":"{\"celsius\":17.5}"}`, and send the
request again. When streaming, `choices[0].delta.tool_calls[i].function.arguments` arrives
in fragments that must be concatenated by index, `delta.reasoning_content` arrives in
fragments too, and the stream ends with `data: [DONE]`.

**Latency expectations.** These are placeholders and acceptance thresholds, not
measurements. T7 replaces them.

| Metric | Threshold until measured | Test |
| :-- | :-- | :-- |
| Time to first token, 4k-token prompt, idle server | ≤ 5 s `[ASSUMPTION]` | T7 |
| Decode speed, 1 stream | ≥ 40 tok/s `[ASSUMPTION]` | T7 |
| Decode speed per stream, 4 streams | ≥ 15 tok/s `[ASSUMPTION]` | T7 |
| Decode at 12k tokens of context vs 1k | ≥ 0.6× the 1k rate `[ASSUMPTION]` (issue #28734) | T7 |
| Queue wait with all 4 slots busy | Not bounded by the server; the client timeout bounds it | T2 |

### 2.3 Network and security design

**Boot-time firewall.** The startup script contains no secrets; its only input is
`admin_cidrs`. The first thing it does is install this ruleset, which is the node's
whole firewall until Phase 6:

```nft
# /etc/nftables.conf, written by the startup script from admin_cidrs.
# Never "flush ruleset": that would also wipe Docker's tables.
table inet amb
delete table inet amb
table inet amb {
  set admin_pub { type ipv4_addr; flags interval; elements = { ${ADMIN_CIDRS} } }

  chain input {
    type filter hook input priority filter; policy drop;
    iif lo accept
    ct state established,related accept
    ct state invalid drop
    icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-advert } accept
    udp sport 67 udp dport 68 accept                    # DHCP replies, if the image uses DHCP
    ip saddr @admin_pub tcp dport { 22, 8080 } accept   # SSH and the API
  }
  chain forward { type filter hook forward priority filter; policy drop; }
}
```

The node never routes traffic, which is why `forward` drops everything. Docker turns on
`ip_forward`, so build containers must run with `--network host` (§5, step 22). The set
holds IPv4 only, so IPv6 connections to 22 and 8080 are dropped.

**Administrative access.**
1. The operator uses SSH to the public IP. Only `admin_cidrs` can connect, and sshd
   accepts keys only (step 26 checks this).
2. Prometheus stays on loopback. Reach it with `ssh -L 9090:127.0.0.1:9090`.
3. Break-glass: Verda has no serial console or SSM equivalent in its API `[VERIFY]`. If
   SSH is lost, run `make down && make up` (warm, ~15 min).

The public SSH port is open to the internet from boot until the startup script runs, a
window of seconds to minutes.

**Secrets**

| Secret | Generated by | Source of truth | On the node | Never in |
| :-- | :-- | :-- | :-- | :-- |
| Consumer API key(s) `amb-<64 hex>` | `ops/secrets.sh new-api-key` (`openssl rand -hex 32`) | SOPS+age file `~/.config/ambermist/secrets.sops.yaml`, outside the repo | `/etc/ambermist/llama-api-keys`, 0640 root:llama | tfvars, state, startup script, argv, git |
| Metrics key (local scrape) | same | same | appended to `llama-api-keys`; `/etc/ambermist/metrics-key`, 0640 root:prometheus | same |
| `HF_TOKEN` | Hugging Face | repo `.env` (existing convention) | `/run/ambermist/hf-auth-header`, tmpfs, 0600; cold path only | same |
| Verda client credentials | Verda console | repo `.env` | never | node, state |

The consumer's copy of its key goes through the organization's secrets manager (Q3).
Secrets reach the node only as JSON on SSH stdin, written by
`/opt/ambermist/bin/install-secrets.py`. This is the pattern from LESSONS.md
("Operations"). T6 greps state files and the stored startup script for secrets.

**Model integrity.** Each shard is downloaded to `*.part`, checked with `sha256sum`
against the pinned manifest, and renamed only if it matches. A `.verified` marker records
the manifest's own SHA-256. T0 also checks the GGUF metadata (§5, step 21).

### 2.4 Validation of the fixed starting parameters

| Parameter from the brief | Finding | Action |
| :-- | :-- | :-- |
| Verda, 1× H200 SXM, ~141 GB | SKU `1H200.141S.44V` at €3.728/h (console 2026-09). The public pricing page shows $4.59/h on-demand and $2.30/h spot. Host RAM for this SKU is unknown. | Keep. `[VERIFY]` currency/price at apply time; run `free -h`. |
| Qwen3.8-Flash-Next UD-Q4_K_XL, "~111 GiB" | The pinned revision `38bb39ee97821de2c9009abb7e93950eec396e66` has 4 shards totalling 111,334,654,784 bytes: **111.3 GB = 103.7 GiB** (archived manifest). The brief mixes up GB and GiB. | Keep. Size storage from the byte count. |
| "~119 GB resident in VRAM" | Nothing has measured this. | `[VERIFY]` from the loader log and `nvidia-smi` (T0). |
| Architecture `qwen4_exp` | At the pin, the architecture string is `qwen4exp` (`src/llama-arch.cpp`). *Verified*: merge `6c84c7d5` is an ancestor of the pin (`git merge-base --is-ancestor`). | Checks use `qwen4exp`. |
| llama.cpp "current master" | Master isn't reproducible. Pin `e9f824d8c0f011662a742c9d15d4aa18a41e32c0` (2026-09-25), which was read but never compiled. Upstream issue #28734 (decode slows as context grows) is still open. | Pin it. Bump only through §7.8. |
| `--jinja -c 65536 -np 4` | Keep all three. At the pin, `--jinja` is on by default (kept explicit). Unified KV turns on only when `-np` is auto; `--no-kv-unified` is set anyway to guarantee 16,384 tokens per slot. `--fit` defaults to on and can change unset parameters, so it is turned off. | See §6.1. |
| `--host 0.0.0.0 --port 8080` | This exposes llama-server directly, with no access log, connection cap, or path allowlist. | Kept for Phase 2, limited to `admin_cidrs` by nftables. From Phase 3, llama-server moves to `127.0.0.1:8081` and nginx takes public port 8080 (§2.5). Callers use port 8080 throughout. |
| 16,384 tokens per slot | Conflicts with the LESSONS.md decision: "Slot = 131,072 tokens… Never truncate context to fit more sessions." | Start with the brief's value. Q1 decides the acceptance target. |
| OpenAI `tool_calls` | llama-server parses tool calls using the chat template (`common/chat*.cpp`). Whether it recognizes this model's template is unverified. | T1 is a gate. |

### 2.5 Deviations from the brief

| Requirement | Plan | Why |
| :-- | :-- | :-- |
| No public IP on the instance | A public IPv4 exists and carries SSH and the API, both limited to `admin_cidrs` by nftables. Phase 6 closes the public API. | `POST /v1/instances` accepts no networking parameters (*verified*). |
| VPC and security groups | Host nftables: `tcp/22` and `tcp/8080` from `admin_cidrs` only; everything else on loopback | Verda has no cloud firewall; its docs recommend ufw on the host (*verified*). |
| Private interconnect or managed VPN | None until Phase 6, then Tailscale on the node (§2.6) | Verda offers no private networking. An IP allowlist is the smallest thing that works while serving is being proven. |
| `--host 0.0.0.0 --port 8080` | As written in Phase 2. From Phase 3: llama-server on `127.0.0.1:8081`, nginx on `0.0.0.0:8080` | From Phase 3 only nginx faces the network, and it copes with slow or idle connections that would tie up llama-server's fixed HTTP thread pool. nginx also adds the access log (llama-server logs requests only at trace level, *verified* in `server-http.cpp`), the error-rate source, a connection cap, and a path allowlist. |
| Admin access via bastion, VPN, or SSM | SSH to the public IP from `admin_cidrs`, keys only; over Tailscale after Phase 6 | Verda has neither a bastion nor an SSM equivalent. |
| Secrets manager | SOPS+age file on the operator workstation; SSH-stdin push | Verda has no secrets manager. Q3 covers the consumer handoff. |
| `stop` / deallocate | Implemented as soft destroy | `shutdown` keeps charging (*verified*). `hibernate` isn't modeled by the provider, and its billing is `[VERIFY]`. |
| `keep_snapshots` | Not implemented | The API has no snapshot operation, only clone, which costs the same as the volume. The model and build tiers can be rebuilt from their pins. |
| Tags | Name prefix plus key=value pairs in the instance `description` | The provider exposes no labels `[VERIFY]`. |
| llama.cpp "current master" | Pinned commit | Reproducibility |

### 2.6 Later: Tailscale (Phase 6)

Start this only after Phase 5 passes. Nothing in Phases 0–5 depends on it. It gives
callers a name that survives rebuilds and access by identity instead of by ISP range, and
it takes the API off the public internet.

| Item | Phases 0–5 | After Phase 6 |
| :-- | :-- | :-- |
| Base URL | `http://<public_ip>:8080/v1` (changes per rebuild) | `http://ambermist-h200.<tailnet>.ts.net:8080/v1` (`<node>`, stable) |
| Public ports | `tcp/22`, `tcp/8080` from `admin_cidrs` | `udp/41641` from any (tailscaled, silent without a valid key); `tcp/22` from `admin_cidrs` during bootstrap only |
| nginx / Prometheus bind | `0.0.0.0:8080` / `127.0.0.1:9090` (from Phase 3) | the node's tailnet address, from `/etc/ambermist/ts-ip`, with `net.ipv4.ip_nonlocal_bind=1` so they start before `tailscale0` |
| Who connects | any host in `admin_cidrs` that holds a key | whoever the tailnet policy allows |
| Admin SSH | `ssh root@<public_ip>` | `ssh root@<node>` |

**Joining.** A new `bootstrap.sh tailscale` stage installs `tailscale` from
pkgs.tailscale.com and runs:

```bash
tailscale up --auth-key=file:/run/ambermist/ts-authkey --hostname=ambermist-h200 \
  --accept-dns=false
tailscale ip -4 > /etc/ambermist/ts-ip
```

- The node's auth key is reusable, ephemeral, pre-approved, and tagged `tag:ambermist`,
  and has a 90-day expiry. It lives in SOPS with its expiry date and reaches the node on
  SSH stdin, like the other secrets, into `/run/ambermist/ts-authkey` (tmpfs, 0600).
  Tagged devices have no node-key expiry.
- `--accept-dns=false`: the node resolves only public names.
- tailscaled keeps its node key in `/var/lib/tailscale` on the OS volume, so a reboot
  rejoins as the same device with the same address `[VERIFY]`. It also adds its own
  netfilter rules. The `inet amb` table is separate, and a packet must pass both
  `[VERIFY]`.
- Each rebuild is a new device with a new 100.x address; only the name carries over.
  `make down` gains `tailscale logout` as its first step, which removes an ephemeral
  device at once `[VERIFY]`. After a spot reclaim or crash, the old device stays until
  Tailscale's ephemeral cleanup `[VERIFY how long]`, and the new node would register as
  `ambermist-h200-1`. Preflight and provisioning check the name (risk 22).

**Tailnet policy.** Keep it in `ops/tailnet-policy.hujson` and apply it by hand in the
Tailscale admin console. Tailscale denies whatever isn't granted, so the tailnet must have
no allow-all rule that covers `tag:ambermist`. If the tailnet holds other devices, merge
these entries into its policy rather than replacing it.

```hujson
{
  "groups":    { "group:amb-admin": ["<operator login>"] },
  "tagOwners": {
    "tag:ambermist":          ["group:amb-admin"],
    "tag:ambermist-consumer": ["group:amb-admin"]
  },
  "grants": [
    { "src": ["tag:ambermist-consumer"], "dst": ["tag:ambermist"], "ip": ["tcp:8080"] },
    { "src": ["group:amb-admin"], "dst": ["tag:ambermist"], "ip": ["tcp:22", "tcp:8080", "tcp:9090"] }
  ]
}
```

`tag:ambermist` is never a source, so the node can't open connections to anything on the
tailnet. Consumer hosts run Tailscale with an auth key tagged `tag:ambermist-consumer`,
which is handed over like the API key (Q3). They need MagicDNS and outbound TCP 443 and
UDP; nothing inbound. The Phase 6 open questions are in Q12.

---

## 3. Component inventory

### 3.1 Resources

| Resource | Stack | Purpose | Persistence | Cost |
| :-- | :-- | :-- | :-- | :-- |
| `verda_instance.node` `ambermist-h200` (`1H200.141S.44V`) | compute | GPU inference | ephemeral | €3.728/h on-demand; €1.864/h spot |
| OS volume `ambermist-h200-os`, NVMe 60 GiB (`os_volume` attribute) | compute | OS, Docker, packages, secrets | ephemeral, **but not deleted when the instance is destroyed**; `audit.py --reap-os` removes it | €0.0164/h; €12/month if orphaned |
| `verda_volume.tier["model"]` `ambermist-model`, NVMe 128 GiB | storage | GGUF shards and `.verified` marker | `keep_model_volume = true` | €25.60/month |
| `verda_volume.tier["build"]` `ambermist-build`, NVMe 20 GiB | storage | llama.cpp source, builds, bundled CUDA libraries | `keep_build_volume = true` | €4.00/month |
| `verda_volume.tier["logs"]` `ambermist-logs`, NVMe 10 GiB | storage | Logs, Prometheus TSDB, test output | `keep_logs_volume = false` | €2.00/month while it exists |
| `terraform_data.model_guard` | storage | Blocks model-volume destruction unless confirmed | follows the model volume | €0 |
| `verda_volume_attachment.tier[*]` ×3 | compute | Attaches the volumes, and waits for the instance IP | ephemeral | €0 |
| `verda_startup_script.boot` `ambermist-boot` | compute | Boot-time firewall | ephemeral | €0 |
| `verda_ssh_key.op[*]` `ambermist-<operator>` | compute | Bootstrap SSH | ephemeral | €0 |
| `terraform_data.identity` | compute | Forces replacement on non-ForceNew changes (LESSONS.md) | ephemeral | €0 |
| nftables, nginx, Prometheus, node_exporter, llama-server | on the node | §2.3, §6 | OS volume and `/srv/build` | €0 |
| Secrets file and tofu state | operator workstation | §2.3, §4.2 | local, backed up | €0 |
| Transfer | — | Model download (~111 GB inbound per cold start), logs, API traffic | — | Not on Verda's price list `[VERIFY]`; Hugging Face doesn't charge |

### 3.2 Storage tiers

| Tier | Mount | Size | Contents and growth | Snapshot / backup |
| :-- | :-- | :-- | :-- | :-- |
| OS | `/` | 60 GiB `[VERIFY minimum]` | Image (~20 GB), CUDA devel build image (~9 GB), apt cache. Doesn't grow. | None |
| Model | `/srv/models` | 128 GiB | 103.7 GiB of shards plus ~24 GiB free. Only a different model or pin changes it. To replace a bad shard, delete it first, then download, so no extra headroom is needed. | None. The source of truth is the Hugging Face revision plus the hashes. The retained volume is the hedge against the revision disappearing. |
| Build | `/srv/build` | 20 GiB | Blobless source clone (~0.5 GB), build tree (3–5 GB), output of 2 builds (current and previous) at ~1.5 GB each. Growth is bounded by pruning to 2 builds. | None. Rebuilt from the pin. |
| Logs | `/srv/logs` | 10 GiB | Rotated logs (≤3.5 GiB worst case, §6.5), Prometheus (capped at 2 GB), test output. | `ops/logs-pull.sh` copies it to `~/ambermist-runs/<instance>-<date>/` before every `down`; keep 90 days locally `[ASSUMPTION]`. |
| Tofu state | workstation | small | — | Each `make` target first copies `infra/*/terraform.tfstate` to `~/.local/share/ambermist/state-backups/` and keeps the last 20. |

The volume sizes must stay distinct: 128, 20, and 10 GiB. Size is the fallback identity
when a blank disk is formatted (step 16).

**Re-applying when a volume already exists.** The storage stack plans no changes.
`plan-guard.sh` aborts `make up` if the plan would create, delete, or replace any
`verda_volume` (step 11). The compute stack attaches volumes by ID. Bootstrap mounts them
by filesystem label, and **never runs `mkfs` on a disk that already has a filesystem or
partition table**. The model tier takes the fast path (marker plus sizes). The build tier
gets a cache hit on `llama.cpp/<key>`.

**When a volume is missing.** `make up` applies the storage stack, which creates the tier
as a blank volume. Bootstrap formats it (`mkfs.ext4 -m 0 -T largefile4 -L amb-<tier>`),
then takes the cold path for that tier: download and verify (model), build (build), or
start empty (logs). Cold and warm paths are chosen per tier, never all at once.

**When the volume exists in Verda but not in state** (for example, the state file was
lost). Preflight runs `audit.py`, finds a name collision, and aborts with an import
command: `tofu -chdir=storage import 'verda_volume.tier["model"]' <id>`. Provider support
for import is `[VERIFY]`.

**Forcing destruction of a retained volume.** Use `make drop TIER=build` for build or
logs. For the model volume, use `make drop TIER=model CONFIRM=<volume-id>`. To remove
everything, use `make destroy-hard CONFIRM=<model-volume-id>` (§7). Afterwards,
`audit.py --purge-trash` deletes the volume permanently. Deleted volumes otherwise sit in
Verda's trash for 96 h and are still charged (LESSONS.md).

**Growing a volume.** Don't edit `*_volume_size_gib` on its own: size is ForceNew in the
provider, and the edit **destroys the data**. The procedure is:
1. Resize through the API (`PUT /v1/volumes`, `size`) `[VERIFY: works while attached]`.
2. Set the variable to the new size.
3. Run `tofu apply -refresh-only`.
4. Confirm the plan shows no changes.
5. Run `resize2fs` on the node.

**Tagging.** Verda resources have names but no labels.
- Names follow `${project}-<role>`. `project = "ambermist"` makes the prefix
  `ambermist-`.
- The instance `description` field (which is required) holds
  `project=ambermist;stack=inference;owner=<operator>;managed-by=opentofu`.
- For cost allocation, sum the API listing by prefix.
- Orphan detection flags three things: any `ambermist-*` resource that isn't in either
  state file, any detached `*-os` volume, and anything in the trash.
- `redcell-*` resources from the earlier attempt are **reported but never touched**.
  AGENTS.md forbids destroying held capacity without an explicit request.

### 3.3 Cost model

Rates: on-demand €3.728/h, spot €1.864/h (Verda spot is half price, per the archived
catalog and the public page), storage €0.20/GiB-month, 730 h/month. Billing granularity
is `[VERIFY]`; it matters for short sessions.

**Monthly cost.** Defaults: model and build volumes kept (€29.60), plus one warm start per
session at about 0.25 GPU-h each `[ASSUMPTION]`.

| Duty cycle | GPU hours | GPU cost (on-demand) | Start overhead | Storage | **Total (on-demand)** | **Total (spot)** |
| :-- | --: | --: | --: | --: | --: | --: |
| 4 h/day, every day | 121.7 | €453.60 | €28.30 | €29.60 | **€511** | **€271** |
| 8 h/day, weekdays (21.7 d) | 173.8 | €647.90 | €20.20 | €29.60 | **€698** | **€364** |
| 8 h/day, every day | 243.3 | €907.10 | €28.30 | €29.60 | **€965** | **€497** |
| 24/7 | 730 | €2,721.40 | €0 | €31.60 | **€2,753** | **€1,392**¹ |

¹ Before the cost of preemptions. Each reclaim costs one warm start plus the interrupted work.

**Storage per volume, per month:** model €25.60, build €4.00, logs €2.00, OS
€12.00-equivalent (charged only while it exists, or if orphaned).

**Transfer.** Each cold model download is ~111 GB inbound. Hugging Face doesn't charge,
and Verda's price list shows no transfer charges `[VERIFY]`. Pulling logs moves MBs. API
traffic is JSON text: under 1 GB/day even at full load. Transfer is not a material cost.

**Break-even points**
- **Reserved vs on-demand.** A reservation bills 730 h/month at a discount `d`. On-demand
  at duty `u` bills `730·u`. They cross at `u = 1 − d`. Verda's published discounts are:

  | Term | Discount | Break-even duty |
  | :-- | --: | --: |
  | 1 month | 2% | 98% |
  | 3 months | 3% | 97% |
  | 6 months | 4% | 96% |
  | 1 year | 8% | 92% (22 h/day) |
  | 2 years | 25% | 75% (18 h/day) |

  **Below 18 h/day, create/destroy on-demand is always cheaper.** At 24/7, a 1-year term
  costs €2,503.70/month and a 2-year term €2,041.10/month. Whether the discounts apply to
  the H200 in EUR is `[VERIFY]`.
- **Spot vs on-demand.** Spot is always 50% cheaper per hour. It loses only when
  preemptions cost more than half the session in rework.
- **Staying up vs going down during an idle gap.** A down/up cycle costs about 0.25
  GPU-h (€0.93) plus 10–20 min of waiting. **If the next request is more than ~15–30 min
  away, run `make down`.**
- **Keeping the model volume.** It costs €25.60/month. Re-downloading and verifying on
  each cold start costs 10–25 GPU-min `[ASSUMPTION]`, or €0.62–1.55, so the money breaks
  even at about 17–41 starts/month. The `true` default is justified by start latency and
  by protection against the pinned revision disappearing, not by cost.
- **Keeping the build volume.** It costs €4/month. A rebuild costs 10–20 GPU-min
  `[ASSUMPTION]`, or €0.62–1.24, so it breaks even at 3–7 starts/month. Keep it.

**How to spend less when use is intermittent**
1. Run `make down` whenever the next use is more than 30 min away. Never use Verda
   `shutdown`: it keeps billing.
2. Use `use_spot = true` for the proving phases (1–5) and for benchmarking and
   exploratory sessions. Keep on-demand for sessions a consumer depends on, and run T5
   once on on-demand, because its OS-volume settings differ. On spot, every data volume
   must have `on_spot_discontinue = "keep_detached"`. Spot saves about €11–17 over the
   6–9 GPU-hours of Phases 1–5.
3. Run `audit.py --reap-os` after every destroy. Each forgotten OS volume costs €12/month.
4. Right-size volumes. The earlier attempt's `redcell-weights` volume (400 GB
   NVMe_Shared, ~€80/month) should be retired **once the user approves** (Q4).
5. Check the account balance in preflight. A zero balance lets Verda discontinue
   instances and move volumes to the trash (LESSONS.md).
6. Optional, after Phase 5: a workstation-side idle watcher that runs `make down` after
   60 min with `rate(llamacpp:requests_processing)` at zero. Verda credentials never go
   on the node, so the node can't tear itself down.

---

## 4. IaC structure

### 4.1 Layout

```
infra/
  Makefile                  # the operator's only entry point (§7)
  storage/                  # stack 1: volumes. Its own state. Rarely changes.
    versions.tf  main.tf  variables.tf  outputs.tf  terraform.tfvars(.example)
  compute/                  # stack 2: instance, attachments, startup script, SSH keys
    versions.tf  main.tf  variables.tf  outputs.tf  boot.sh.tftpl  terraform.tfvars(.example)
node/                       # copied to /opt/ambermist; independent of the provider
  bootstrap.sh              # idempotent; stages: packages disks secrets-check net model build serve
  bin/install-secrets.py  bin/healthcheck.sh  bin/metrics-gpu.sh  bin/metrics-http.sh
  bin/fetch-model.sh  bin/build-llama.sh  bin/preflight-serve.sh
  pins/llama.cpp.conf       # LLAMA_SHA, QWEN4EXP_MERGE, BUILD_IMAGE, CUDA_ARCH
  pins/model.conf           # MODEL_ID, HF_REPO, HF_REVISION, ENTRY_FILE
  pins/qwen38-ud-q4kxl.sha256  # "<sha256>  <bytes>  <repo path>" x4 (from archive/attempt-1)
  serving.conf              # llama-server settings (§6.1); non-secret
  etc/                      # systemd units, nginx, logrotate, prometheus
ops/
  preflight.sh  provision.sh  plan-guard.sh  wait-ip.sh  logs-pull.sh  secrets.sh
  audit.py                  # orphan audit / reap / purge (adapt archive ops/cleanup_orphans.py)
  accept/                   # T0–T8 (Python standard library only)
```

Config files are named `*.conf`, not `*.env`, because `.gitignore` ignores `*.env`. Remove
`.terraform.lock.hcl` from `.gitignore` and commit the lock files, so provider versions are
reproducible.

Keep the model and runtime out of the IaC (LESSONS.md, "What went wrong"). OpenTofu knows
volume sizes and the SKU, and nothing about Qwen or llama.cpp. Swapping the model means
editing `node/pins/` and `node/serving.conf`, not the `.tf` files.

**Provider boundary.** Everything specific to Verda lives in the two stacks and in
`ops/audit.py`/`preflight.sh`. The rest of the system depends only on the stack outputs:
`volumes` (tier → id, size), `public_ip`, and `instance_id`. Substituting another H200
provider means rewriting about 150 lines of HCL and the audit script. `node/` and the
contract don't change. No multi-cloud module layer is planned.

### 4.2 State backend

Use the **local** backend: one state file per stack at `infra/<stack>/terraform.tfstate`
(already gitignored), with the backups from §3.2. By design the state holds no secrets
(T6 checks this). Move to an S3-compatible remote backend with locking only if a second
operator appears. The compute stack reads the storage outputs through
`terraform_remote_state` (local).

Tooling: OpenTofu ≥ 1.8 and `verda-cloud/verda ~> 1.1` (1.1.3 was the latest on
2026-09-12, per the archive). If the provider binary exists under
`.terraform/providers`, run `tofu init -plugin-dir=.terraform/providers`.

### 4.3 Variables

`infra/storage/variables.tf`

| Variable | Type | Default | Notes |
| :-- | :-- | :-- | :-- |
| `project` | string | `"ambermist"` | Name prefix. Validation: `^[a-z][a-z0-9-]{1,18}$`. |
| `location` | string | none | Picked from H200 availability until the model volume exists, then fixed to its site (Q5; Phase 1 task, step 3). Volumes are site-bound. Validation: one of `FIN-01`, `FIN-02`, `FIN-03`. |
| `model_volume_size_gib` | number | `128` | ForceNew; see §3.2 "Growing a volume". Validation: `>= 120`. |
| `build_volume_size_gib` | number | `20` | |
| `logs_volume_size_gib` | number | `10` | |
| `keep_model_volume` | bool | `true` | Teardown keeps the model volume. |
| `keep_build_volume` | bool | `true` | Teardown keeps the build volume. |
| `keep_logs_volume` | bool | `false` | Teardown destroys the logs volume; `make down` pulls the logs first. |
| `teardown` | bool | `false` | Set only by `make down` and `make drop`. Destroys the tiers whose `keep_*` is false. |

The three sizes must be distinct, because step 16 relies on size as a fallback identity:

```hcl
variable "logs_volume_size_gib" {
  type    = number
  default = 10
  validation {
    condition = length(distinct([var.model_volume_size_gib, var.build_volume_size_gib,
                                 var.logs_volume_size_gib])) == 3
    error_message = "Volume sizes must be distinct; bootstrap uses size to identify blank disks."
  }
}
```

Whether the installed OpenTofu accepts cross-variable validation is `[VERIFY]`. If it
doesn't, use a `terraform_data` precondition instead.

`infra/compute/variables.tf`

| Variable | Type | Default | Notes |
| :-- | :-- | :-- | :-- |
| `project` | string | `"ambermist"` | Must match the storage stack. |
| `owner` | string | none | Goes into the instance `description`. |
| `instance_type` | string | `"1H200.141S.44V"` | Validation enforces the fleet cap (below). |
| `image` | string | `"24.04.cuda12.9.docker"` | `[VERIFY]` with `make images`; the provider doesn't validate it. |
| `use_spot` | bool | `false` | |
| `os_volume_size_gib` | number | `60` | `[VERIFY]` Verda's minimum. |
| `ssh_public_keys` | map(string) | none | operator ⇒ OpenSSH public key. |
| `admin_cidrs` | list(string) | none | Sources allowed to reach SSH and the API (`tcp/22`, `tcp/8080`). Operator value (2026-09-26): `["193.219.12.0/23", "188.69.0.0/17", "88.118.128.0/17", "85.206.0.0/18", "78.56.0.0/14"]`. These are whole ISP ranges (~345k addresses), not /32s: any host in them reaches sshd (keys only) and the API (bearer key) (risk 20). |

```hcl
variable "instance_type" {
  type    = string
  default = "1H200.141S.44V"
  validation {                                   # fleet cap, AGENTS.md
    condition     = contains(["1H200.141S.44V"], var.instance_type)
    error_message = "Only one H200 is allowed for this tier."
  }
}

variable "admin_cidrs" {
  type = list(string)
  validation {
    condition     = length(var.admin_cidrs) > 0 && !contains(var.admin_cidrs, "0.0.0.0/0")
    error_message = "admin_cidrs must list the operator's own addresses."
  }
}
```

`admin_cidrs` feeds only the startup script. Changing it changes the script body, which
replaces the instance (a warm `make up`, risk 19).

### 4.4 Key resources (sketch)

`storage/main.tf`

```hcl
locals {
  tiers = {
    model = { size = var.model_volume_size_gib, keep = var.keep_model_volume }
    build = { size = var.build_volume_size_gib, keep = var.keep_build_volume }
    logs  = { size = var.logs_volume_size_gib,  keep = var.keep_logs_volume }
  }
  present = { for k, v in local.tiers : k => v if !(var.teardown && !v.keep) }
}

resource "verda_volume" "tier" {
  for_each            = local.present
  name                = "${var.project}-${each.key}"
  size                = each.value.size          # ForceNew
  type                = "NVMe"                   # block device; NVMe_Shared is NFS (LESSONS)
  location            = var.location
  on_spot_discontinue = "keep_detached"          # a spot reclaim must not take data volumes
}

# prevent_destroy can't depend on a variable. This guard depends on the model volume, so
# OpenTofu destroys it first; its destroy-time check fails unless the operator names the
# volume ID, and the run aborts before the volume is touched. [VERIFY with T8]
resource "terraform_data" "model_guard" {
  for_each = { for k, v in verda_volume.tier : k => v.id if k == "model" }
  input    = each.value
  provisioner "local-exec" {
    when    = destroy
    command = "test \"$${AMB_CONFIRM_DESTROY:-}\" = \"${self.input}\" || { echo 'Refusing to destroy model volume ${self.input}; set AMB_CONFIRM_DESTROY=${self.input}' >&2; exit 1; }"
  }
}
```

`compute/main.tf`

```hcl
data "terraform_remote_state" "storage" {
  backend = "local"
  config  = { path = "${path.module}/../storage/terraform.tfstate" }
}
locals {
  vols = data.terraform_remote_state.storage.outputs.volumes    # tier => { id, name, size_gib }
  boot = templatefile("${path.module}/boot.sh.tftpl", { admin_cidrs = var.admin_cidrs })
}

resource "terraform_data" "identity" {                           # is_spot and script body aren't ForceNew
  input = { sku = var.instance_type, spot = var.use_spot, boot = sha256(local.boot) }
}

resource "verda_ssh_key" "op" {
  for_each   = var.ssh_public_keys
  name       = "${var.project}-${each.key}"
  public_key = each.value
}

resource "verda_startup_script" "boot" {
  name      = "${var.project}-boot"
  script    = local.boot                                         # plaintext in the API and state: no secrets
  lifecycle { replace_triggered_by = [terraform_data.identity] }
}

resource "verda_instance" "node" {
  instance_type     = var.instance_type
  image             = var.image
  hostname          = "${var.project}-h200"
  description       = "project=${var.project};stack=inference;owner=${var.owner};managed-by=opentofu"
  location          = data.terraform_remote_state.storage.outputs.location
  is_spot           = var.use_spot
  ssh_key_ids       = [for k in verda_ssh_key.op : k.id]
  startup_script_id = verda_startup_script.boot.id
  os_volume = {                                                  # an attribute, not a block
    name                = "${var.project}-h200-os"
    size                = var.os_volume_size_gib
    type                = "NVMe"
    on_spot_discontinue = var.use_spot ? "delete_permanently" : null
  }
  lifecycle {
    replace_triggered_by = [terraform_data.identity]
    ignore_changes       = [description]
  }
}

resource "verda_volume_attachment" "tier" {                      # the only resource that waits for an IP
  for_each    = local.vols
  instance_id = verda_instance.node.id
  volume_id   = each.value.id
}
```

`compute/boot.sh.tftpl` does one thing: it writes and loads the §2.3 nftables ruleset and
runs `systemctl enable nftables`. Everything else
happens in `node/bootstrap.sh`. It can be rerun without replacing the instance, and
instances are immutable (LESSONS.md).

### 4.5 Outputs

| Stack | Output | Value |
| :-- | :-- | :-- |
| storage | `volumes` | `{ model = { id, name, size_gib }, build = {…}, logs = {…} }` for the tiers that exist |
| storage | `location` | site code; compute uses it, so the stacks can't disagree |
| storage | `monthly_storage_eur` | `sum(size) * 0.20` (informational) |
| compute | `instance_id`, `public_ip` | `public_ip` may be `null` right after create; `ops/wait-ip.sh` runs `tofu apply -refresh-only` until it is set |
| compute | `hourly_eur` | `3.728` or `1.864` (informational; literal catalog, since the provider has no data sources) |
| compute | `api_url` | `http://<public_ip>:8080/v1`; hand it to callers after every `up` (§2.2) |
| compute | `ssh` | `ssh root@<public_ip>` |
| compute | `burn_warning` | Plain-language reminder that the GPU is billing |

---

## 5. Bootstrap sequence

Steps are numbered across all phases. Where a script is named, the engineer writes it to
the behavior stated. Where a command is given, use it as-is.

**Phase 0: from a bare account to ready-to-apply (no GPU).**

1. Install on the workstation: `opentofu>=1.8`, `sops`, `age`, `jq`, `python3`, `rsync`,
   `curl`, `make`, and `nmap` (for T6).
2. In the Verda console, create API client credentials and add them to `.env` as
   `VERDA_CLIENT_ID` and `VERDA_CLIENT_SECRET`. Add `HF_TOKEN`. Load the file with
   `set -a; source .env; set +a` (AGENTS.md).
3. Inventory what already exists (read-only): `python3 ops/audit.py --report-all`. List
   every instance and volume. **If a `redcell-*` H200 is running, stop here.** A second
   H200 breaks the fleet cap. Ask the user (Q4).
4. Create the secrets store:
   ```bash
   age-keygen -o ~/.config/ambermist/age.key
   ops/secrets.sh init                  # creates ~/.config/ambermist/secrets.sops.yaml
   ops/secrets.sh new-api-key consumer  # amb-<64 hex>
   ops/secrets.sh new-api-key metrics
   ```
5. Confirm that every API caller is inside `admin_cidrs` (Q2), and send them §2.2. The
   base URL is known only after `make up` (`api_url`).
6. Fill in `infra/storage/terraform.tfvars` (location) and
   `infra/compute/terraform.tfvars` (owner, SSH keys, `admin_cidrs`).
7. Copy the model manifest from the archive into `node/pins/`:
   `git show archive/attempt-1:deploy/models/qwen38-ud-q4kxl.yaml` → `qwen38-ud-q4kxl.sha256`.
   Copy the runtime pin from LESSONS.md into `node/pins/llama.cpp.conf`.
8. `cd infra && make init && make preflight`. Preflight checks:
   - credentials, and that the balance is above €50 `[ASSUMPTION threshold]`;
   - no other H200 running in the account (fleet cap);
   - `1H200.141S.44V` available at `location` (not a reservation);
   - the image slug exists;
   - the workstation's egress IP is inside `admin_cidrs`;
   - the secrets decrypt;
   - no name collisions between the API and state.

**Phase 1: first light, the model served on loopback.**

9. The first run of `make up` does the following, in order.
10. It backs up state (§3.2).
11. `tofu -chdir=storage plan -out=up.tfplan`, then `ops/plan-guard.sh`. The guard fails
    if any `verda_volume` action is `delete`. On the first run, `create` is expected.
    Then `tofu -chdir=storage apply up.tfplan`.
12. It plans and applies the compute stack in the same way. Before applying, the guard
    confirms that the plan replaces nothing that already exists.
13. `ops/wait-ip.sh` loops `tofu -chdir=compute apply -refresh-only -auto-approve` until
    `public_ip` isn't null, then waits for TCP/22 (with a timeout of 15 min).
14. `ops/provision.sh <public_ip>` runs the remaining steps over SSH.
15. It copies `node/` to the node:
    `tar -C node -cz . | ssh root@IP 'mkdir -p /opt/ambermist && tar -C /opt/ambermist -xz'`.
    Then it runs `bootstrap.sh packages`, which does:
    ```bash
    apt-get update
    apt-get install -y --no-install-recommends nftables jq \
      prometheus prometheus-node-exporter python3-venv libgomp1 git rsync
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
    # must show 1x H200, driver >= 570 (CUDA 12.8 runtime)
    free -g   # record host RAM in LESSONS
    ```
16. `bootstrap.sh disks`. For each tier label (`amb-model` 128 GiB, `amb-build` 20 GiB,
    `amb-logs` 10 GiB):
    - If `blkid -L amb-<tier>` finds the disk, mount it.
    - Otherwise, pick a disk that has no filesystem, no partitions, and exactly the
      tier's size in bytes. There must be **exactly one** such disk; if not, abort
      without formatting.
    - Format it with `mkfs.ext4 -m 0 -T largefile4 -L amb-<tier>`.
    - Add it to fstab as
      `LABEL=amb-<tier> /srv/<tier> ext4 defaults,nofail,x-systemd.device-timeout=60s 0 2`.

    Two points are `[VERIFY]`: that NVMe volumes attach as block devices, and whether
    `/dev/disk/by-id` exposes the volume ID. If it does, match on the ID instead of the
    size.
17. `ops/provision.sh` pushes secrets on stdin:
    `sops -d --output-type json … | jq '{api_keys, metrics_key, hf_token}' | ssh root@IP /opt/ambermist/bin/install-secrets.py`.
    `install-secrets.py` writes each file with its mode and ownership (§2.3) atomically,
    through a temp file, `fsync`, and rename. It writes `hf-auth-header` to `/run` only.
18. `bootstrap.sh model` and `bootstrap.sh build` run **in parallel**. Both log to
    `/srv/logs/bootstrap/<ts>-<stage>.log` and record their duration in
    `/srv/logs/bootstrap/timeline.json`.
19. `fetch-model.sh`:
    ```bash
    . /opt/ambermist/pins/model.conf            # MODEL_ID HF_REPO HF_REVISION
    dest=/srv/models/$MODEL_ID; man=/opt/ambermist/pins/$MODEL_ID.sha256
    want=$(sha256sum "$man" | cut -d' ' -f1)
    if [[ "$(cat "$dest/.verified" 2>/dev/null)" == "$want" && "${FULL_VERIFY:-0}" != 1 ]]; then
      check_sizes "$man" "$dest" && { echo "model: fast path"; exit 0; }
    fi
    fetch() {  # sha bytes path
      out="$dest/$(basename "$3")"
      [[ -f $out ]] && echo "$1  $out" | sha256sum -c --quiet && return 0
      curl --fail -L --retry 5 --retry-all-errors --continue-at - \
           -H @/run/ambermist/hf-auth-header -o "$out.part" \
           "https://huggingface.co/$HF_REPO/resolve/$HF_REVISION/$3"
      echo "$1  $out.part" | sha256sum -c --quiet || { rm -f "$out.part"; return 1; }
      mv "$out.part" "$out"
    }
    # run fetch for all 4 manifest lines in parallel; any failure -> exit 1 (no marker)
    echo "$want" > "$dest/.verified"
    ```
    Shards keep their original file names in one directory, because llama.cpp loads split
    GGUFs from the first shard. The header file keeps the token out of `ps` output.
    Whether the repo is gated, and so whether it needs the token, is `[VERIFY]`.
20. `build-llama.sh`:
    ```bash
    . /opt/ambermist/pins/llama.cpp.conf   # LLAMA_SHA=e9f824d8c0f011662a742c9d15d4aa18a41e32c0
                                           # QWEN4EXP_MERGE=6c84c7d5d8833c6e0df69628f75a0f599797934e
                                           # BUILD_IMAGE=nvidia/cuda:12.8.1-devel-ubuntu24.04@sha256:4b9ed5fa8361736996499f64ecebf25d4ec37ff56e4d11323ccde10aa36e0c43
                                           # CUDA_ARCH=90
    key="${LLAMA_SHA:0:12}-sm${CUDA_ARCH}-$(printf %s "$BUILD_IMAGE" | sha256sum | cut -c1-8)"
    out=/srv/build/llama.cpp/$key; src=/srv/build/src/llama.cpp
    if LD_LIBRARY_PATH=$out/lib "$out/bin/llama-server" --version 2>/dev/null; then
      ln -sfn "$out" /srv/build/current; echo "build: cache hit $key"; exit 0
    fi
    [[ -d $src/.git ]] || git clone --filter=blob:none https://github.com/ggml-org/llama.cpp.git "$src"
    git -C "$src" fetch origin "$LLAMA_SHA" && git -C "$src" checkout --detach "$LLAMA_SHA"
    git -C "$src" merge-base --is-ancestor "$QWEN4EXP_MERGE" "$LLAMA_SHA"
    docker run --rm --network host -v /srv/build:/srv/build -e KEY="$key" -e CUDA_ARCH="$CUDA_ARCH" \
        "$BUILD_IMAGE" bash -euxc '
      apt-get update && apt-get install -y --no-install-recommends cmake ninja-build git ca-certificates
      cmake -S /srv/build/src/llama.cpp -B /srv/build/tmp/$KEY -G Ninja \
        -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=$CUDA_ARCH \
        -DGGML_NATIVE=OFF -DBUILD_SHARED_LIBS=OFF -DLLAMA_OPENSSL=OFF \
        -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF
      cmake --build /srv/build/tmp/$KEY --target llama-server -j"$(nproc)"
      mkdir -p /srv/build/llama.cpp/$KEY/bin /srv/build/llama.cpp/$KEY/lib
      cp /srv/build/tmp/$KEY/bin/llama-server /srv/build/llama.cpp/$KEY/bin/
      cp -P /usr/local/cuda/lib64/libcudart.so.12* /usr/local/cuda/lib64/libcublas.so.12* \
            /usr/local/cuda/lib64/libcublasLt.so.12* /srv/build/llama.cpp/$KEY/lib/'
    ! LD_LIBRARY_PATH=$out/lib ldd "$out/bin/llama-server" | grep -q 'not found'
    ln -sfn "$out" /srv/build/current      # keep current + previous; prune older keys
    ```
    Notes:
    - `--network host` is needed because the node's `forward` chain drops bridged traffic.
    - The binary runs **on the host**, not in a container. The host needs only the NVIDIA
      driver and the bundled libraries. This means no Docker networking in the serving
      path.
    - The binary is built on Ubuntu 24.04 and runs on the same release, so glibc matches.
    - `[VERIFY]`: the flag set configures at the pin. If the build fails with Ubuntu's
      default gcc-13, use gcc-14 as upstream `.devops/cuda.Dockerfile` does.
    - `[VERIFY]`: the server builds without UI assets (`LLAMA_BUILD_UI` defaults to OFF
      at the pin).
21. Once both steps have finished, check the metadata (T0):
    ```bash
    python3 -m venv /srv/build/gguf-venv
    /srv/build/gguf-venv/bin/pip install -q /srv/build/src/llama.cpp/gguf-py
    /srv/build/gguf-venv/bin/gguf-dump --json --json-array --no-tensors \
      /srv/models/qwen38-ud-q4kxl/Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf \
      > /srv/logs/bootstrap/gguf-meta.json
    jq -e '.metadata["general.architecture"].value == "qwen4exp"' /srv/logs/bootstrap/gguf-meta.json
    jq -e '[.metadata["qwen4exp.attention.compress_ratios"].value[]] | all(. == 0 or . == 4)
           and (map(select(. == 4)) | length) == 12' /srv/logs/bootstrap/gguf-meta.json
    ```
    At the pin, `gguf-py` needs numpy ≥ 2.2.6, tqdm, pyyaml, and requests
    (`gguf-py/pyproject.toml`). Ubuntu 24.04's `python3-numpy` is older, hence the venv.
    Without `--json-array`, array fields have no `value` in the JSON output, and the
    `compress_ratios` check would always fail (*verified*, `gguf_dump.py` at the pin).
    The expected count of 12
    full-attention layers comes from the LESSONS capacity math `[VERIFY]` against the
    model config.
22. `bootstrap.sh serve` installs `llama-server.service` (§6.2) with `llama` as a system
    user, creates `/srv/logs/{llama,nginx,prometheus,bootstrap,bench}`, and starts the
    service with `LLAMA_HOST=127.0.0.1` and `LLAMA_PORT=8080` (§6.1). It then polls
    `curl -s 127.0.0.1:8080/health` until it returns 200, with a timeout of 20 min.
23. From the workstation, open `ssh -N -L 8080:127.0.0.1:8080 root@IP` and run T0 and T1
    against `http://127.0.0.1:8080`. **Gate:** if either fails, stop, run `make down`,
    and record the failure in LESSONS.md.

**Phase 2: the public API, allowlisted.**

24. `bootstrap.sh net` sets `LLAMA_HOST=0.0.0.0` in `serving.conf` and restarts
    llama-server, which now answers on public port 8080. The firewall from the startup
    script (§2.3) already admits only `admin_cidrs`. There is no nginx yet (Phase 3).
25. From the workstation, `curl -s http://<public_ip>:8080/health` returns 200, and
    `POST /v1/chat/completions` without a key returns 401.
26. Confirm sshd accepts keys only: `sshd -T` on the node shows
    `passwordauthentication no` and `kbdinteractiveauthentication no`. If the image
    doesn't set this `[VERIFY]`, `bootstrap.sh net` adds a drop-in under
    `/etc/ssh/sshd_config.d/` and reloads sshd.
27. **Gate:** from a host inside `admin_cidrs`, T1 passes against
    `http://<public_ip>:8080/v1`, and T6 is clean.

**Phase 3: supervision and observability.**

28. Put nginx in front of llama-server (§6.3):
    - `apt-get install nginx`, remove the default site, and install the `ambermist` site.
    - Set `LLAMA_HOST=127.0.0.1` and `LLAMA_PORT=8081` in `serving.conf`, restart
      llama-server, then start nginx on port 8080. Callers keep the same URL.

    Then install the healthcheck timer, the logrotate rules and hourly timer, Prometheus,
    node_exporter, and the two textfile metric timers (§6.4–§6.6). The healthcheck and
    Prometheus use llama-server on `127.0.0.1:8081`; the error-rate metric reads nginx's
    access log.
29. **Gate:** T4 passes, the §6.6 queries return data, and T6 passes again with the
    nginx checks.

**Phase 4: lifecycle automation.**

30. Implement the `make` targets and `ops/audit.py` (§7).
31. Run T8 on a throwaway stack (`project=ambtest`, volumes of 1, 2, and 3 GiB, no
    instance).
32. **Gate:** T5 passes, and `audit.py` reports no orphans after `make down`.

**Phase 5: acceptance.**

33. Run T2, T3, and T7. Fill in §2.2 with the measured values. Write the measurements
    (VRAM, host RAM, load time, cold/warm timelines, throughput) into LESSONS.md.

**Phase 6: Tailscale (only after Phase 5 passes; design in §2.6).**

34. Answer Q12. In the Tailscale admin console: apply `ops/tailnet-policy.hujson`, turn
    on MagicDNS, join the workstation to `group:amb-admin`, and create the node and
    consumer auth keys. Store the node key with `ops/secrets.sh set-ts-authkey`, which
    also records its expiry date.
35. Add the `bootstrap.sh tailscale` stage and push `ts_authkey` with the other secrets
    (step 17). Re-render nginx and Prometheus to bind to the tailnet address.
36. On the node, `tailscale status --json | jq -r .Self.DNSName` must start with
    `ambermist-h200.`. From the workstation, `ssh root@<node> true` succeeds, and
    `tailscale ping ambermist-h200` shows whether the path is direct or through DERP.
37. Switch the ruleset to the Phase 6 column of §2.6. Public `tcp/8080` is removed, and
    public `tcp/22` becomes bootstrap-only (`provision.sh --close-public-ssh` once SSH over
    Tailscale works). Add `tailscale logout` to `make down`. Add preflight checks for
    key expiry and for a stale `ambermist-h200` device.
38. **Gate:** from a consumer-tagged host, T1 passes against `http://<node>:8080/v1`.
    T4d and T5 pass with the same name after a reboot and after a rebuild. T6 passes
    with the Phase 6 checks.

## 6. Serving configuration

### 6.1 llama-server invocation

```bash
/srv/build/current/bin/llama-server \
  --model /srv/models/qwen38-ud-q4kxl/Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf \
  --alias reasoner \
  --host 127.0.0.1 --port 8081 \
  --api-key-file /etc/ambermist/llama-api-keys \
  --jinja \
  --ctx-size 65536 --parallel 4 --no-kv-unified --no-context-shift \
  --n-gpu-layers all --fit off \
  --flash-attn on \
  --ctx-checkpoints 2 \
  --metrics --slots --no-webui \
  --log-timestamps --log-prefix
```

| Flag | Why |
| :-- | :-- |
| `--alias reasoner` | A stable contract name, independent of the model (LESSONS: don't hard-code model names). |
| `--host 127.0.0.1 --port 8081` | From Phase 3; only nginx reaches it (§2.5). Set by `LLAMA_HOST`/`LLAMA_PORT`: `127.0.0.1:8080` in Phase 1 (SSH tunnel), `0.0.0.0:8080` in Phase 2 (public, allowlisted). |
| `--api-key-file` | Keys stay out of argv and the environment. One key per line, `#` for comments (*verified*, `common/arg.cpp` at the pin), which allows rotation with overlap. |
| `--jinja` | Uses the model's template for tool calls. On by default at the pin; kept explicit. |
| `--ctx-size 65536 --parallel 4` | From the brief: 4 × 16,384. |
| `--no-kv-unified` | Guarantees each slot its own 16,384 tokens. Unified KV would pool them, and one long request could starve the others. At the pin, unified KV is on only when `-np` is auto. |
| `--no-context-shift` | Past the limit, the server rejects or stops (T3) instead of silently dropping context. This is the default at the pin; kept explicit. |
| `--n-gpu-layers all --fit off` | Everything goes on the GPU, and `--fit` (on by default) can't change unset parameters on its own. Any automatic reduction counts as a failure (LESSONS). |
| `--flash-attn on` | Makes memory use deterministic (the default is `auto`). If the architecture doesn't support it, the server fails loudly instead of falling back. |
| `--ctx-checkpoints 2` | From the recorded baseline in LESSONS. The default of 32 costs memory per slot. |
| `--metrics --slots` | `/metrics` for Prometheus (off by default); `/slots` for T2. Both need a key, and from Phase 3 nginx doesn't expose them. |
| `--no-webui` | Less exposed surface. The web UI is on by default at the pin, and its assets are public paths. |

Placement: the starting profile keeps everything on the GPU. At 4 × 16,384 tokens, KV
memory is small, and 103.7 GiB of weights leaves about 30 GiB of the H200's ~140 GiB
free `[VERIFY T0]`. This departs from the archived H200 profile
(`--override-tensor per_layer_token_embd=CPU`), which was sized for 131,072-token slots.
Fall back to that profile if the load runs out of memory; it needs ≥ 80 GiB of free host
RAM (LESSONS). Each fallback is a new profile, and T0–T3 must be rerun.

`node/serving.conf` holds `MODEL_PATH`, `LLAMA_ALIAS`, `LLAMA_CTX`, `LLAMA_SLOTS`,
`LLAMA_HOST`, and `LLAMA_PORT`. The unit expands them, so changing the model, the profile,
or the listen address doesn't touch the unit or the IaC.

### 6.2 systemd unit

`/etc/systemd/system/llama-server.service`

```ini
[Unit]
Description=llama-server (ambermist inference tier)
After=network-online.target
Wants=network-online.target
RequiresMountsFor=/srv/models /srv/build /srv/logs
StartLimitIntervalSec=900
StartLimitBurst=5

[Service]
Type=simple
User=llama
Group=llama
EnvironmentFile=/opt/ambermist/serving.conf
Environment=LD_LIBRARY_PATH=/srv/build/current/lib
ExecStartPre=/opt/ambermist/bin/preflight-serve.sh
ExecStart=/srv/build/current/bin/llama-server --model ${MODEL_PATH} --alias ${LLAMA_ALIAS} \
  --host ${LLAMA_HOST} --port ${LLAMA_PORT} --api-key-file /etc/ambermist/llama-api-keys --jinja \
  --ctx-size ${LLAMA_CTX} --parallel ${LLAMA_SLOTS} --no-kv-unified --no-context-shift \
  --n-gpu-layers all --fit off --flash-attn on --ctx-checkpoints 2 \
  --metrics --slots --no-webui --log-timestamps --log-prefix
Restart=on-failure
RestartSec=10
TimeoutStopSec=30
LimitMEMLOCK=infinity
LimitNOFILE=65536
StandardOutput=append:/srv/logs/llama/server.log
StandardError=append:/srv/logs/llama/server.log
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/srv/logs/llama

[Install]
WantedBy=multi-user.target
```

`preflight-serve.sh` exits non-zero, with a clear message, unless all of the following
hold:
- `.verified` in the model directory matches the pinned manifest hash;
- `/srv/build/current/bin/llama-server` exists;
- the API key file is non-empty;
- `nvidia-smi` succeeds.

After 5 failed starts in 15 min, the unit stays `failed`. That stops a crash loop, for
example an out-of-memory error at load, and needs an operator.

### 6.3 nginx (API front, access log, connection cap; from Phase 3)

`/etc/nginx/sites-enabled/ambermist` (remove the default site)

```nginx
limit_conn_zone $server_addr zone=llm_total:1m;
log_format llm_json escape=json '{"ts":$msec,"client":"$remote_addr","method":"$request_method",'
  '"uri":"$uri","status":$status,"req_bytes":$request_length,"resp_bytes":$bytes_sent,'
  '"rt":$request_time,"urt":"$upstream_response_time","ustatus":"$upstream_status"}';

server {
  listen 0.0.0.0:8080;           # public; nftables admits only admin_cidrs (§2.3)
  access_log /srv/logs/nginx/access.json.log llm_json;   # no bodies, so no prompt content
  error_log  /srv/logs/nginx/error.log warn;
  client_max_body_size 8m;

  location = /health { proxy_pass http://127.0.0.1:8081; access_log off; }

  location ~ ^/v1/(chat/completions|models)$ {
    limit_conn llm_total 12;              # 4 running + 8 queued in llama-server
    limit_conn_status 429;
    error_page 429 = @busy;
    proxy_pass http://127.0.0.1:8081;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;                  # SSE streaming
    proxy_read_timeout 900s;
    proxy_send_timeout 900s;
  }

  location @busy {
    add_header Retry-After 15 always;
    default_type application/json;
    return 429 '{"error":{"code":429,"type":"rate_limit_error","message":"inference tier at capacity"}}';
  }

  location / { return 404; }
}
```

### 6.4 Health check and restart behavior

- **Endpoint.** `GET /health` has no authentication. It returns 200 `{"status":"ok"}`
  when ready, and 503 `{"error":{"code":503,"message":"Loading model","type":"unavailable_error"}}`
  while loading (*verified* in the README and `server-http.cpp` at the pin). Consumers
  and the healthcheck both use it.
- **Crashes.** `Restart=on-failure` restarts the service after 10 s. The start limit is
  in §6.2.
- **Hangs.** `llama-healthcheck.timer` (`OnUnitActiveSec=30s`) runs `healthcheck.sh`:
  ```bash
  systemctl is-active --quiet llama-server || exit 0      # systemd handles failed state
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:8081/health || true)
  since=$(( $(date +%s) - $(date -d "$(systemctl show -p ActiveEnterTimestamp --value llama-server)" +%s) ))
  f=/run/ambermist/health-fails; n=$(cat $f 2>/dev/null || echo 0)
  if [[ $code == 200 || ( $code == 503 && $since -lt 1200 ) ]]; then echo 0 > $f; exit 0; fi
  n=$((n+1)); echo $n > $f
  if (( n >= 3 )); then
    logger -t amb-health "restart after $n failed checks (last=$code)"
    echo 0 > $f
    systemctl restart llama-server
  fi
  ```
  A process that is up but not responding is restarted within about 2 min. A load that
  takes longer than 20 min is treated as a hang. `/run` is emptied at every boot, so
  `/etc/tmpfiles.d/ambermist.conf` must contain `d /run/ambermist 0700 root root -`.
  Without it, the script can't write its counter after a reboot (T4d).
- **On reboot**, the order is: nftables, then the fstab mounts
  (`nofail`), then `llama-server` (which requires the mounts), then nginx. No operator
  action is needed (T4d).
- **Not handled automatically:** GPU Xid errors and a GPU that falls off the bus. The
  `metrics-gpu.sh` timer exports `amb_gpu_up 0`, and the fix is a rebuild with
  `make down && make up`.

### 6.5 Log rotation

`/etc/logrotate.d/ambermist`, with `logrotate.timer` overridden to `OnCalendar=hourly` so
that `maxsize` takes effect:

```
/srv/logs/llama/*.log {
  daily
  maxsize 256M
  rotate 7
  compress
  delaycompress
  missingok
  notifempty
  copytruncate          # systemd holds the file open (StandardOutput=append:)
}
/srv/logs/nginx/*.log {
  daily
  maxsize 256M
  rotate 7
  compress
  delaycompress
  missingok
  notifempty
  sharedscripts
  postrotate
    [ -s /run/nginx.pid ] && kill -USR1 "$(cat /run/nginx.pid)"
  endscript
}
```

Worst case this uses about 3.5 GiB, plus Prometheus's 2 GB cap, on the 10 GiB logs
volume.

### 6.6 Observability

| Signal | Source | Where it goes |
| :-- | :-- | :-- |
| Request log: time, client, path, status, latency, bytes | nginx JSON access log | `/srv/logs/nginx/access.json.log` |
| Per-request tokens and timing (`prompt eval time`, `eval time`, logged at info level; *verified* in `server-context.cpp`) | llama-server log | `/srv/logs/llama/server.log` |
| Token throughput and queue depth | `/metrics`: `llamacpp:prompt_tokens_total`, `llamacpp:tokens_predicted_total`, `llamacpp:predicted_tokens_seconds`, `llamacpp:requests_processing`, `llamacpp:requests_deferred`, `llamacpp:n_busy_slots_per_decode` | Prometheus |
| GPU utilization, memory, temperature, power, up/down | `metrics-gpu.sh` every 15 s (`nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw --format=csv,noheader,nounits`) → node_exporter textfile `amb_gpu_*` | Prometheus |
| Error rate | `metrics-http.sh` every 60 s: requests in the last 60 s by status code, from the access log → `amb_http_requests_last_minute{code="…"}` | Prometheus |
| Host (CPU, RAM, disk, network) | node_exporter on `127.0.0.1:9100` | Prometheus |

Prometheus uses the Ubuntu package. `/etc/default/prometheus`:

```
ARGS="--web.listen-address=127.0.0.1:9090 --storage.tsdb.path=/srv/logs/prometheus \
      --storage.tsdb.retention.time=15d --storage.tsdb.retention.size=2GB"
```

`/etc/default/prometheus-node-exporter` (the package default listens on all interfaces):

```
ARGS="--web.listen-address=127.0.0.1:9100 \
      --collector.textfile.directory=/var/lib/prometheus/node-exporter"
```

`metrics-gpu.sh` and `metrics-http.sh` write `*.prom` files into that directory. Each
writes a temp file and renames it, so node_exporter never reads a partial file.

`/etc/prometheus/prometheus.yml`:

```yaml
global: { scrape_interval: 15s }
scrape_configs:
  - job_name: llama
    authorization: { type: Bearer, credentials_file: /etc/ambermist/metrics-key }
    static_configs: [{ targets: ["127.0.0.1:8081"] }]
  - job_name: node
    static_configs: [{ targets: ["127.0.0.1:9100"] }]
```

`make status` runs these queries over SSH, against `http://127.0.0.1:9090/api/v1/query` on
the node:

```
rate(llamacpp:tokens_predicted_total[5m])            # aggregate generation tok/s
rate(llamacpp:prompt_tokens_total[5m])               # aggregate prefill tok/s
llamacpp:requests_processing
llamacpp:requests_deferred                           # > 0 means the queue is in use
amb_gpu_utilization_percent
amb_gpu_memory_used_mib
sum(amb_http_requests_last_minute{code=~"5.."}) / clamp_min(sum(amb_http_requests_last_minute), 1)
```

No alerting or Grafana is planned: the tier runs only while someone is using it.

---

## 7. Lifecycle runbook

Run everything from `infra/` after `set -a; source ../.env; set +a`. Every target first
backs up state (§3.2). Timings are `[ASSUMPTION]` until T5 measures them.

The `make` targets are `init`, `images`, `preflight`, `up`, `smoke`, `status`, `down`,
`drop TIER=… [CONFIRM=…]`, `destroy-hard CONFIRM=…`, `audit`, `ssh`, `logs-pull`,
`provision [STAGE=…]`, and `provision-secrets`. There is no other supported way to change
infrastructure.

| Operation | Command | Wall clock | GPU billed | Cost |
| :-- | :-- | :-- | :-- | :-- |
| Preflight | `make preflight` | < 1 min | 0 | €0 |
| Apply, cold | `make up` | 35–60 min | same | €2.20–3.70 |
| Apply, warm | `make up` | 10–20 min | same | €0.60–1.20 |
| Stop / deallocate | `make down` | 3–8 min | until the instance is deleted | €0.20–0.50; storage continues at €29.60/month |
| Destroy (soft) | `make down` | 3–8 min | same | same |
| Destroy (hard) | `make destroy-hard CONFIRM=<model-vol-id>` | 5–10 min | same | €0.30–0.60, then €0/month |
| Orphan audit | `make audit` | < 1 min | 0 | €0 |

### 7.1 Apply from cold

```bash
make preflight
make up            # storage creates 3 volumes → compute → provision: format, download, build, serve
make smoke         # T1 from the workstation against api_url
make status
```

Expect `bootstrap/timeline.json` to show `disks: formatted` for all three tiers,
`model: downloaded+verified`, and `build: built <key>`.

### 7.2 Apply warm

```bash
make preflight
make up            # storage: no changes (the plan guard enforces it) → compute → provision
make smoke
```

Expect `disks: mounted existing`, `model: fast path`, and `build: cache hit <key>`. The
public IP is new, so callers need the new `api_url`. After Phase 6 the name stays the
same.

### 7.3 Stop / deallocate

This is the same as §7.4. Don't use the Verda console's "shut down", which keeps billing,
or "hibernate", which is `[VERIFY]` and not modeled by the provider, so it drifts from
state.

### 7.4 Destroy (soft)

```bash
make down
# = ../ops/logs-pull.sh                               # only when keep_logs_volume = false; best effort
#   tofu -chdir=compute destroy -auto-approve
#   tofu -chdir=storage apply -auto-approve -var teardown=true   # destroys tiers with keep_* = false
#   ../ops/audit.py --reap-os                         # permanently deletes detached ambermist-*-os
#   ../ops/audit.py                                   # exit 1 if anything is unexpected
```

With the defaults, `ambermist-model` and `ambermist-build` remain, detached.

To drop one retained tier: `make drop TIER=build`, which is
`apply -var teardown=true -var keep_build_volume=false`. For the model volume, add
`CONFIRM=<id>`.

### 7.5 Destroy (hard)

```bash
make audit                                  # note the model volume ID
make destroy-hard CONFIRM=<model-volume-id>
# = logs-pull; compute destroy;
#   AMB_CONFIRM_DESTROY=<id> tofu -chdir=storage destroy -auto-approve;
#   audit.py --reap-os --purge-trash --prefix ambermist-; audit.py --expect-empty
```

Afterwards the account holds no `ambermist-*` resource, and nothing is in the trash.
Whether the provider's delete is permanent or goes to the trash is `[VERIFY]`; the purge
step covers both cases.

### 7.6 Orphan audit

```bash
make audit          # python3 ../ops/audit.py
```

The audit lists instances, volumes (including the trash `[VERIFY endpoint]`), startup
scripts, and SSH keys through the API. It compares them with
`tofu -chdir={storage,compute} show -json` and prints one line per resource:
`status (managed | ORPHAN | detached-os | trash | foreign) id name size €/month`. It
exits 1 if it finds any `ORPHAN`, `detached-os`, or `trash`. `foreign` covers
non-`ambermist-` resources, such as `redcell-*`; they are reported and never deleted.

### 7.7 API key rotation

```bash
ops/secrets.sh rotate-api-key consumer   # new key becomes current; old one kept as previous
make provision-secrets                   # rewrites llama-api-keys with both, restarts llama-server
# hand the new key to the consumer; once it has switched:
ops/secrets.sh retire-previous consumer && make provision-secrets
```

llama-server reads the keys only at startup, so each rotation step costs one restart:
downtime equal to the load time, 1–3 min `[VERIFY T4]`. Schedule rotations.

### 7.8 llama.cpp pin bump

1. Edit `node/pins/llama.cpp.conf`.
2. Run `make provision STAGE=build`. The new key builds alongside the old one, and
   `current` switches to it.
3. `systemctl restart llama-server`.
4. Run T0, T1, and T3.
5. If anything fails: `ln -sfn /srv/build/llama.cpp/<previous> /srv/build/current` and
   restart.
6. Record the new pin in LESSONS.md.

---

## 8. Acceptance tests

The tests live in `ops/accept/`, use only the Python standard library, and write JSON
results to `/srv/logs/bench/` or `~/ambermist-runs/`. "Direct" means llama-server itself
through an SSH tunnel: `http://127.0.0.1:8080` in Phases 1–2, `http://127.0.0.1:8081` from
Phase 3. "Contract" means
`http://<public_ip>:8080/v1` from a host inside `admin_cidrs` (after Phase 6,
`http://<node>:8080/v1` from a tailnet device).

**T0: model and runtime verification** (Phase 1, direct)
- All 4 shard hashes match; `.verified` is written. The metadata checks in step 21 pass.
- `llama-server --version` reports commit `e9f824d`.
- The loader log shows arch `qwen4exp`; per-slot context 16,384 × 4 `[VERIFY log
  wording]`; all layers offloaded; no fit adjustment.
- Record the CUDA model, KV, and compute buffer sizes, plus `nvidia-smi` `memory.used`.
  Compare them with the brief's "~119 GB".
- **Pass:** all of the above.

**T1: tool-call round trip** (Phase 1 direct; Phase 2 contract)
- No key on `POST /v1/chat/completions` → 401 `authentication_error`.
- `GET /health` without a key → 200. `GET /v1/models` with a key → contains `reasoner`.
- "What is 6×7? Reply with the number only." → the content contains `42`.
- Non-streaming tool call with the §2.2 request:
  - `finish_reason == "tool_calls"`, and there is exactly one tool call;
  - its name is `get_station_temperature`;
  - `json.loads(arguments) == {"station_id": "EFHK"}`, and `id` is not empty;
  - `content` contains no raw template markup such as `<tool_call>` or `<think>`.
- Second turn: add the assistant message and a `tool` message
  `{"celsius": 17.5}`. Expect `finish_reason == "stop"`, content containing `17.5`, and
  no `tool_calls`.
- Repeat both turns with `stream: true`. The fragments reassembled by index must give the
  same results, and the stream ends with `data: [DONE]`.
- Record whether `reasoning_content` is present.
- **Pass:** 5 of 5 runs parse correctly (a parse failure is a server or template problem),
  and the arguments are correct in at least 4 of 5 (a wrong argument is the model's
  choice).

**T2: 4 concurrent streams, slot isolation** (Phase 5, direct and contract)
- Build 4 prompts of 10,000 tokens each, sized exactly with `/apply-template` and
  `/tokenize`. Each has unique markers `M<i>-<8 random chars>` at the start, middle, and
  end. Task: "List every marker beginning with M, exactly as written." Use
  `max_tokens: 4000` and `stream: true`. Release all 4 from a `threading.Barrier`
  (the design is in LESSONS.md, "Operations").
- While they run, poll `/slots` every second. At some moment all 4 slots must show
  `is_processing: true`, each with `n_ctx == 16384`. `llamacpp:requests_deferred` must
  stay at 0.
- **Pass:** all 4 return 200 with `finish_reason: "stop"`. Each response contains its own
  3 markers and **none** of the other 9.
- **Degradation, part 1:** send 6 at once. 4 run and 2 are deferred (metrics), and all 6
  finish with 200.
- **Degradation, part 2:** send 14 at once through the contract URL. 12 are accepted and
  2 get 429 with `Retry-After: 15`. `/health` stays at 200 throughout.

**T3: context limit and graceful rejection** (Phase 5, direct)
- (a) A prompt of exactly 16,384 tokens after templating returns 400
  `exceed_context_size_error`, and the message names 16384. The pin rejects when
  `n_tokens >= n_ctx` (*verified*, `server-context.cpp`).
- (b) A 16,000-token prompt with `max_tokens: 2000` returns 200 with
  `finish_reason: "length"` `[VERIFY exact value]`, and
  `usage.prompt_tokens + usage.completion_tokens ≤ 16,384`.
- (c) Run (a) while a T2 stream is active on another slot. The stream finishes
  unaffected.
- (d) Afterwards, `/health` returns 200 and `systemctl show -p NRestarts llama-server`
  is unchanged.
- **Pass:** all four.

**T4: restart recovery** (Phase 3)
- (a) Crash: during a stream, run `systemctl kill -s SIGKILL llama-server`. The client
  sees an error. `NRestarts` goes up by 1 within 15 s, `/health` returns 503 and then 200,
  and T1 passes. Record the time to 200. **Pass:** ≤ 5 min `[ASSUMPTION]`.
- (b) Hang: `kill -STOP $(pidof llama-server)`. The journal shows `amb-health: restart`,
  and service is back within 3 min.
- (c) Crash loop: set `MODEL_PATH` to a missing file. The unit is `failed` after 5
  attempts. Restore the path, run `systemctl reset-failed`, and start the service.
- (d) Reboot: `systemctl reboot`, with no operator action afterwards. The volumes are
  mounted by label, the nftables ruleset is loaded, llama-server is active, and T1
  passes over the contract URL. Record boot-to-ready time.

**T5: warm start after a soft destroy** (Phase 4, needs T0–T4 passed)
- `time make down`. The instance is gone. The audit shows `ambermist-model` and
  `ambermist-build` detached and nothing orphaned. The logs volume is deleted, and the
  logs were pulled to the workstation first.
- `time make up`. The timeline shows `mounted existing` for model and build, `model: fast
  path`, and `build: cache hit`. The node makes no request to huggingface.co and starts
  no `docker run`.
- T1 passes over the new contract URL (`api_url`).
- Run T5 at least once with `use_spot = false`. That is the path consumer sessions use, and
  its OS volume has no `on_spot_discontinue` policy.
- **Pass:** ≤ 20 min from `make up` to T1 passing `[ASSUMPTION]`. Record the time for
  each stage and the GPU minutes billed in LESSONS.md.

**T6: security posture** (Phase 2; again in Phases 3 and 6)
- From an internet host outside `admin_cidrs`: `nmap -Pn -p- <public_ip>` shows every
  TCP port filtered, including 22 and 8080.
- From a host inside `admin_cidrs`: the same scan shows only 22 and 8080 open.
- On the node, `ss -ltnup` shows only sshd (22) and the API listener on 8080 beyond
  127.0.0.1. The API listener is llama-server in Phase 2 and nginx from Phase 3, when
  8081, 9090, and 9100 appear on 127.0.0.1 only.
- sshd refuses password logins: `ssh -o PreferredAuthentications=password,keyboard-interactive root@<public_ip>`
  fails without a password prompt.
- Phase 2: without a key, `POST /v1/chat/completions`, `/slots`, and `/metrics` return
  401. At the pin, only `/health`, `/v1/health`, and the web UI paths skip the key check
  (*verified* by reading `server-http.cpp`). Whether `--no-webui` leaves any UI path
  answering is `[VERIFY]`.
- From Phase 3 (nginx checks): through the contract URL, `/slots`, `/metrics`, `/props`,
  and `/` return 404. Without a key, `/v1/models` returns 401.
- `grep -rEl 'amb-[0-9a-f]{64}|hf_[A-Za-z0-9]{20,}|tskey-[A-Za-z0-9-]{10,}' infra/*/terraform.tfstate`
  finds nothing. The startup script body fetched from the API contains no secret.
- **Phase 6 checks, replacing the first three:**
  - From anywhere on the internet, every TCP port on the public IP is filtered after
    provisioning, and `nmap -sU -p 41641` shows `open|filtered` (tailscaled is silent).
  - On the node, 8080 and 9090 listen on the tailnet address only.
  - A consumer-tagged device reaches `<node>:8080` but not 22 or 9090. A tailnet device
    that is neither admin nor consumer reaches none of the three.
- **Pass:** all of the above.

**T7: performance baseline** (Phase 5, contract)
- Single stream: prompts of 1k, 4k, and 12k tokens, `max_tokens: 512`, 3 runs each.
  Record time to first token and decode tok/s, from the response `timings` and the
  server log.
- 4 concurrent streams with 4k-token prompts: decode tok/s per stream and in total.
- Decode tok/s at 1k vs 12k tokens of context (issue #28734).
- **Pass:** meets the §2.2 thresholds, or the user agrees to revised thresholds. Put the
  measured numbers into §2.2.

**T8: destroy guard** (Phase 4, throwaway stack `project=ambtest`)
- `keep_model_volume=false` plus a teardown, without `AMB_CONFIRM_DESTROY`: tofu exits
  non-zero, and the volume still exists.
- With the correct ID: the volume is destroyed, and the audit finds it in the trash
  (then purge it).
- Changing `model_volume_size_gib` without confirmation: the plan shows a replace, the
  guard blocks it, and `plan-guard.sh` would already have refused it.

---

## 9. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
| :-- | :-- | :-- | :-- | :-- |
| 1 | The llama.cpp pin fails to build or load `qwen4exp` (never compiled) | M | H | Phase 1 gate. Pin bump procedure (§7.8). The build runs in parallel with the download, so failure shows up early. |
| 2 | Tool calls come back as plain text because the template isn't recognized | M | H | T1 gate. `[VERIFY]` a `--chat-template-file` override, or a pin that supports the template. |
| 3 | 16,384 tokens is too small for reasoning plus tool schemas, causing frequent 400 or `length` responses | H | M | Q1. Measure the consumer's prompt sizes. Alternative profiles: 2 × 32k, or 4 × 131k (LESSONS); each needs requalification. |
| 4 | No H200 available at the site at `up` time; volumes are bound to the site | M | H | Preflight availability check. Wait and retry. Spot. Don't change site without accepting a cold start. No other SKU (fleet cap). |
| 5 | The public IP is reachable because Verda has no cloud firewall | Certain | H | nftables default-drop from first boot; only 22 and 8080, and only from `admin_cidrs`; everything else on loopback; T6. |
| 6 | Secrets leak through the startup script, state, or argv | L | H | Nothing secret in HCL. Push over SSH stdin. `--api-key-file`. A header file for the Hugging Face token. The T6 grep. |
| 7 | The model volume is destroyed, replaced, or formatted by mistake | L | H | Destroy guard (T8), plan guard, `mkfs` only on blank disks of the exact size, and re-download as the recovery path. |
| 8 | Orphaned OS volumes or trash keep billing | H | L | `--reap-os` in every `down`; `make audit` exits non-zero. |
| 9 | Zero balance: Verda discontinues the instance and trashes volumes | L | H | Balance check in preflight. |
| 10 | The pinned Hugging Face revision is deleted or gated | L | H | Keep the model volume. Optionally, keep an offline copy (Q7). |
| 11 | Provider quirks (no update path, ForceNew, `is_spot`, null IP) trigger an unplanned replacement | M | M | `replace_triggered_by`, plan guard, `wait-ip.sh`, and reviewing every compute plan. |
| 12 | Slow requests plus an unbounded queue starve the other callers | M | M | Consumer caps at 4 concurrent; `max_tokens` is required; from Phase 3, nginx caps connections at 12. |
| 13 | Spot reclaim mid-session | M (spot only) | M | Data volumes use `keep_detached`, then a warm re-apply. On-demand for sessions that matter. |
| 14 | Driver and CUDA 12.8 runtime mismatch, or the image slug changes | L | H | Preflight checks the image slug. Step 15 checks the driver version. The build image is pinned by digest. |
| 15 | Until Phase 6, the bearer key, prompts, and completions cross the internet in plaintext HTTP. Anyone on the path can read them and reuse the key from inside `admin_cidrs`. | Certain | M | Use test prompts only until Phase 6. From the workstation, `ssh -L 8080:127.0.0.1:8080` keeps traffic inside SSH. Rotate the API key when Phase 6 lands (§7.7). If this isn't acceptable for the proving phases, add TLS at nginx now (Q8). |
| 16 | Decode slows as context grows (issue #28734) | M | M | T7 measures it. Less severe at 16k than at 131k. A fixing commit means a pin bump. |
| 17 | The wrong disk is identified at format time | L | H | Label first, then exact size, then no existing filesystem or partitions, then exactly one candidate; otherwise abort. |
| 18 | The model license (Qwen Community License 1.0) doesn't allow the intended use | L | H | Q9. The assessment was pending in LESSONS. |
| 19 | The operator's egress IP changes, locking them out during bootstrap | L | L | `admin_cidrs` lists the operator's ISP ranges, so a new IP inside them still works. Preflight checks the IP. For an IP outside the ranges, the fix is a `down`/`up` with updated `admin_cidrs` (cheap). |
| 20 | `admin_cidrs` covers whole ISP ranges (~345k addresses), so strangers on those ISPs can reach sshd and the API. In Phase 2 the API is llama-server's own HTTP server, and a few idle connections can tie up its fixed thread pool without a key. | Certain | M | sshd takes keys only (step 26). The API needs a bearer key on every path except `/health`. Phase 2 is short: Phase 3 puts nginx in front (connection cap, three paths only, slow-client handling). Phase 6 closes the public API. |
| 21 | Phase 6: Tailscale is a third-party dependency. A control-plane outage, an account problem, or a plan limit stops a new node from joining. The control plane sees device metadata (not traffic), and DERP relays may carry encrypted traffic. | L | M | Existing connections keep working during a control-plane outage `[VERIFY]`. Public SSH from `admin_cidrs` stays open during bootstrap, so a failed join doesn't lock the operator out. If the metadata or relay exposure isn't acceptable, self-host the control plane (Headscale); that's outside this plan. |
| 22 | Phase 6: a stale `ambermist-h200` device (after a spot reclaim or crash without `tailscale logout`) pushes the new node to `ambermist-h200-1`, which breaks the callers' URL | M | M | `make down` logs out first. Preflight and step 36 check the name. Remove the stale device in the admin console. |
| 23 | Phase 6: the node's reusable auth key leaks. Someone joins as `tag:ambermist` and, while the node is down, takes its name and receives consumer requests with their bearer keys. | L | H | The key lives only in SOPS and `/run`; 90-day expiry; revoke it in the admin console. Preflight's name check catches a squatter before `up`. |

---

## 10. Open questions

Q4 blocks Phase 1. Q2 and Q8 block Phase 2 (the public API). Q1, Q3, and Q9 block
consumer use (after Phase 5).

1. **Slot profile.** The brief's 4 × 16,384 tokens conflicts with the recorded decision
   of 131,072-token slots ("Never truncate context to fit more sessions"). Which one is
   the acceptance target? Phase 1 starts with the brief's value either way.
2. **Callers.** Until Phase 6, the API is reachable only from `admin_cidrs`. Is every
   caller inside those ranges, and can they take a new base URL after each rebuild?
3. **Secrets handoff.** Which organizational secrets manager delivers the consumer key?
   Should `.env` (Verda and Hugging Face credentials) also move into SOPS?
4. **Held resources.** Is an H200 from the earlier attempt still running? Does the
   `redcell-weights` volume still exist? Retiring either needs your explicit approval
   (AGENTS.md). A running `redcell` H200 blocks `make up` under the fleet cap.
5. **Site.** Answered 2026-09-26: chosen automatically where an H200 is available, until
   the model volume exists. From then on it is fixed to that volume's site. Preference
   order when several sites qualify: FIN-02 (the only recorded H200 run), FIN-01, FIN-03.
6. **Admin addresses.** Answered 2026-09-26: `admin_cidrs` (§4.3).
7. Should an offline copy of the pinned weights (111 GB) be kept outside Verda as a
   hedge against the revision disappearing? The default is no.
8. **TLS.** Until Phase 6, API traffic is plaintext HTTP over the internet (risk 15). Is
   that acceptable for the proving phases? After Phase 6, is plaintext inside Tailscale
   acceptable? If TLS is needed then, `tailscale cert` can issue a publicly trusted
   certificate for `<node>` once HTTPS is turned on in the tailnet `[VERIFY]`.
9. **Model license.** Has the Qwen Community License 1.0 been assessed for this
   consumer's use?
10. Are the placeholder latency thresholds in §2.2 acceptable as pass/fail criteria until
    T7 replaces them?
11. **Verda facts to verify before Phase 4** (read the API docs or ask support):
    - whether `hibernate` stops GPU billing;
    - billing granularity;
    - whether any private networking exists that isn't in the public API (for example,
      through instant clusters);
    - a serial or web console for break-glass access;
    - the endpoint for listing the trash;
    - resizing a volume while it is attached;
    - how block devices are identified inside the guest;
    - `tofu import` support for `verda_volume`;
    - whether reserved discounts apply to the H200 in EUR.
12. **Before Phase 6 (Tailscale):**
    - Can every consumer host run Tailscale and join the operator's tailnet as
      `tag:ambermist-consumer`? If not, which hosts need a gateway, and who operates it?
      A gateway must masquerade those hosts into the tailnet and resolve `<node>` for
      them `[VERIFY]`. Sharing the node into the consumer's own tailnet doesn't survive a
      rebuild, because each rebuild is a new device.
    - Does the consumer's edge allow outbound TCP 443 and UDP to Tailscale?
    - Is a third-party control plane acceptable (risk 21)?
    - The operator's Tailscale login for `group:amb-admin`, and which Tailscale plan the
      tailnet is on (`[VERIFY]` whether the free plan allows this use).
