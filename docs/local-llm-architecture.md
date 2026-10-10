# Local LLM architecture (reference)

Status snapshot: 2026-10-09. Full build log/rationale: [local-llm-3060.md](local-llm-3060.md).
Measurements: `LESSONS.md`, 2026-10-08/09 sections.

## What this is

A llama.cpp server on the operator's Windows PC (WSL/Ubuntu), standing in for the Verda
`reasoner` endpoint so the CALDERA/Kali lab can run tool tests without cloud GPU spend.

## Path

```
Lab client / VS Code
       │  POST /v1/chat/completions, model="reasoner", Bearer AMB_API_KEY
       ▼
lab gateway (100.66.6.130, nginx)  ──(repointed here TEMPORARILY since 2026-10-10 — see Status)
       │  or, VS Code / local clients hit this PC directly:
       ▼
WSL (Ubuntu, this PC) :8080
       │
       ▼
llama-server (llama.cpp, CUDA) ── RTX 3060, 12 GiB VRAM
       │
       ▼
huihui-ai Qwen3-14B-abliterated, Q4_K_M GGUF
```

## Components

| Piece | Value |
|---|---|
| Server | `llama-server` (llama.cpp, pin `e9f824d8c`), built from source in WSL with CUDA |
| Model | `bartowski/huihui-ai_Qwen3-14B-abliterated-GGUF`, `Q4_K_M`, served as alias `reasoner` |
| Model file | `~/llm/models/huihui-qwen3-14b-abliterated-q4km/huihui-ai_Qwen3-14B-abliterated-Q4_K_M.gguf` |
| GPU offload | `-ngl 38` (of 41 layers), `-c 16384`, `--parallel 1`, `-fa on` |
| Sampling | `--temp 1.0 --top-k 20 --top-p 0.95 --min-p 0 --presence-penalty 1.5` |
| Auth | `--api-key-file ~/llm/keys/reasoner-api-key` — the lab's existing `AMB_API_KEY`, not a new key |
| Bind | `0.0.0.0:8080` inside WSL; gated by tailnet ACL + the API key, no separate firewall rule |
| Measured perf | ~20 tok/s generation, ~400 tok/s+ prompt processing, well inside the ≥8 tok/s / ≤60 s TTFT gate |

## Process management

- systemd unit: `/etc/systemd/system/ambermist-llama.service` (source of truth staged at
  `~/llm/ambermist-llama.service`), `enabled` + `active`, `Restart=on-failure`.
- Windows Task Scheduler boots WSL at logon (`wsl.exe -d <distro> true`) so systemd starts the
  service without anyone logging in to WSL by hand.
- Apply a config change: edit `~/llm/ambermist-llama.service`, then (operator, needs sudo):
  ```bash
  sudo cp ~/llm/ambermist-llama.service /etc/systemd/system/ambermist-llama.service
  sudo systemctl daemon-reload
  sudo systemctl restart ambermist-llama
  ```

## Status (live-checked 2026-10-09)

- ✅ Service running locally, serving the correct model, key-gated (`curl 127.0.0.1:8080` with
  no key → 401; with key → 200, correct output).
- ✅ **Tailnet up** (checked 2026-10-10): `ambermist-local` is online as `100.116.148.127`,
  tagged, and the gateway resolves it and gets `/health` 200. (It was `offline` on 2026-10-09;
  it is an ephemeral node, so it can drop again when WSL has been down for a while.)
- ✅ **Gateway repointed 2026-10-10 — TEMPORARY**: `100.66.6.130` now proxies to
  `ambermist-local.tail57998f.ts.net:8080`; `/health` through the gateway returns 200.
  Revert to the H200 with the steps in [lab-gateway.md](lab-gateway.md#temporary-upstream-is-the-local-llm-not-the-h200).
- ❓ **VS Code**: `chatLanguageModels.json` snippet drafted in local-llm-3060.md, not confirmed
  applied (that file is never read/edited directly — holds other endpoints' keys).

## To make this reachable by the lab

1. Fix the tailnet — needs the operator. Re-join with the auth key from `.env`, in WSL:
   ```bash
   cd /mnt/c/Users/vytka/OneDrive/dev/ambermist
   set -a; . ./.env; set +a          # loads TS_AUTHKEY without echoing it
   sudo tailscale up --auth-key="$TS_AUTHKEY" --hostname=ambermist-local --accept-dns=false
   tailscale status --json | grep -i -A2 tags     # must show tag:ambermist
   ```
   - Bare `tailscale up` refuses ("requires mentioning all non-default flags") — always
     restate `--hostname` and `--accept-dns=false`. Don't use `--reset`: it drops the hostname.
   - Without `--auth-key` it prints a `login.tailscale.com` URL. **Don't open it** — a browser
     login makes the node user-owned instead of `tag:ambermist`, and the gateway grant
     (`tag:ambermist-consumer` → `tag:ambermist`) stops matching.
   - The key is ephemeral, so the node is deleted after it has been offline for a while
     (e.g. WSL shut down) and has to re-join with the key. That's why it was logged out.
2. Repoint the gateway's `nginx` stage at this PC's tailnet name (step 8 of local-llm-3060.md)
   — an outward change to shared infra, needs explicit approval before running.

## Reaching it from the operator's laptop

Join the laptop as your own user (plain `tailscale up`, browser login) — **not** with
`TS_AUTHKEY`, and not tagged. Per `ops/tailnet-policy.hujson`:

| Laptop joins as | Reaches `ambermist-local` on |
|---|---|
| Your user login, listed in `group:amb-admin` | tcp/22 + tcp/8080 |
| `tag:ambermist-consumer` | tcp/8080 only (tagged devices lose user identity, so no admin grant) |
| `tag:ambermist` | nothing — it's the node tag and never a source |

The repo copy of the policy still has the `<operator login>` placeholder; check the live
policy in the admin console has the real login. Then from the laptop:

```bash
ssh vytka@ambermist-local                      # needs sshd running in WSL (unconfirmed)
curl -H "Authorization: Bearer <api key>" http://ambermist-local:8080/v1/models
```

llama binds `0.0.0.0:8080` inside WSL, alongside `tailscaled`, so there's no Windows port
proxy or firewall rule to set up.

## Rollback

Old stock model (`Qwen/Qwen3-14B-GGUF`) is still on disk at
`~/llm/models/qwen3-14b-q4km/Qwen3-14B-Q4_K_M.gguf` — revert is a one-line `--model` path edit
in the unit file plus the same `cp`/`daemon-reload`/`restart` above.
