# Local LLM on the workstation RTX 3060 (task for the coding agent)

Written 2026-10-07. Nothing here has been built or run. Facts below are marked with where
they come from; "unverified" means nobody has looked yet.

Read [AGENTS.md](../AGENTS.md) and [LESSONS.md](../LESSONS.md) first. This is a **local**
setup on the operator's Windows PC. It touches no cloud node, no tofu stack, no fleet
capacity, and none of `node/`. Don't change those.

## Goal

Make this PC a **drop-in replacement for the Verda `reasoner` endpoint** so the
CALDERA/Kali lab ([docs/caldera-kali.md](caldera-kali.md)) can keep running tool tests
with **no cloud GPU spend**. The lab reaches the Verda node over Tailscale as
`http://100.66.6.130:8080/v1` (gateway) → `ambermist-h200:8080` → llama.cpp, model alias
`reasoner`, shared bearer key `AMB_API_KEY`. The only change a lab client should need is the
base URL: point `MCP_LLM_API_BASE` at this PC's tailnet address. Everything else — the key,
the `reasoner` model id, the OpenAI `/v1` shape — must match.

So the local llama.cpp endpoint must be:

1. on the tailnet, reachable from the lab gateway / CALDERA host the same way `ambermist-h200` is,
2. authenticated with the **existing** `AMB_API_KEY` (not a new key), returning 401 without it,
3. serving model alias **`reasoner`**, so `openai/reasoner` in the lab's LiteLLM config resolves,
4. also reachable from VS Code chat on this PC (secondary),
5. started automatically so it's up whenever the lab runs.

**Host decision (2026-10-07): everything runs in WSL (Ubuntu 26.04), not native Windows.**
Tailscale must sit on the same host that serves llama so the gateway can route to it in one
network namespace; the operator works in WSL; and WSL here has working GPU passthrough
(`nvidia-smi` lists the 3060), systemd enabled, and `/dev/net/tun`. So `tailscaled` and
llama.cpp both run in WSL. The 15 GiB WSL RAM cap is not a blocker: with ~10.5 GiB of the
27B on the GPU only ~5 GiB of weights spill to RAM. Raise the cap later via `.wslconfig`
(`[wsl2]` `memory=26GB`) only if a bigger model needs it.

Measure it and record what you find. Pick the model from measurements (step 4), not from
this doc.

This replaces the cloud node for **test sessions only**. It doesn't touch `infra/`, the
Verda node, or `ops/session.sh`; the operator still runs `tofu -chdir=infra/compute destroy`
to stop paying for the cloud node, and brings it back for anything the 3060 can't serve.

## Where things stand

**Hardware** (read 2026-10-07 with `nvidia-smi`, `free`, `lscpu`, and Win32 CIM):

- GPU: NVIDIA GeForce RTX 3060, **12,288 MiB**, ~1,232 MiB already used by the Windows
  desktop. Driver 591.86. Plan on **~10.5 GiB** for weights + KV + compute buffers.
- CPU: AMD Ryzen 5 5600, 6 cores / 12 threads, AVX2.
- RAM: **32 GB** on the host; WSL sees 15 GiB (default cap) + 4 GiB swap.
- Disk: WSL root has 955 GB free.
- WSL (the host, see above): Ubuntu 26.04, systemd on, `/dev/net/tun` present, GPU visible.
  `sudo` **requires a password** here — the agent can't run privileged commands unattended in a
  background session, so the install/`up`/`apt` steps are handed to the operator to run in the
  interactive prompt with the `! ` prefix (noted inline).

**Candidate models:**

| Candidate | Source | Size | Measured on this card (2026-10-08) |
|---|---|---|---|
| A. Qwen3.8-27B Q4_K_M (the test-tier model) | `node/pins/model.27b.conf`: `orcarouter/Qwen3.8-27B-Uncensored-GGUF` @ `fc437a33…`, `Qwen3.8-27B-Uncensored-Q4_K_M.gguf`, 16,810,714,496 bytes, sha256 in `node/pins/qwen38-27b-uncensored-q4km.sha256` | 15.7 GiB | **Fails the gate.** `--fit` offloads 36/66 layers at `-c 32768`. Real 8.5k-token request: TTFT 31.1 s (passes), but generation **2.59 tok/s** — well under the ≥8 tok/s floor. The "4–8 tok/s" guess was too optimistic. |
| B. Smaller Qwen3.8 (~8–14B dense, or a ~30B-A3B MoE) | **Confirmed absent.** HF API search on the `Qwen/` org shows only the 27B dense, a large "Flash-Next" MoE, and a 2.4T-A95B MoE — no 8–14B dense size. Only unofficial community distills exist at ~9B (e.g. `empero-ai/`); not used, provenance unverified. | n/a | Doesn't exist as planned. Skipped straight to C. |
| C. Fallback: Qwen3-14B Q4_K_M | `Qwen/Qwen3-14B-GGUF` @ `530227a7d9…`, `Qwen3-14B-Q4_K_M.gguf`, 9,001,752,960 bytes, sha256 `500a8806e85ee9c83f3ae08420295592451379b4f8cf2d0f41c15dffeb6b81f0` | 8.38 GiB | **Chosen.** At `-c 32768` only 30/41 layers fit (standard attention KV is pricier than the 27B's hybrid-SSM KV) and generation fell to 5.31 tok/s — also fails. At **`-c 16384`**, `--fit` reaches 38/41 layers on GPU (927 MiB VRAM free under load). Real 8.4k-token request: TTFT 10.2 s, prompt 821 tok/s, **generation 15.24 tok/s** — passes with margin. `llama-bench -ngl 38`: pp512 1020.7, tg128 21.5 tok/s. |

Qwen3-30B-A3B Q4_K_M (`unsloth/Qwen3-30B-A3B-GGUF`, confirmed to exist, ~18 GiB) was not tried — C already cleared the gate, so the doc's "if several models pass, ask the operator" branch didn't apply.

**Model swap (2026-10-08): candidate C's weights replaced with an abliterated (uncensored)
build, same architecture/quant.** The operator asked to serve `huihui_ai/qwen3-abliterated:14b-v2`
for the CALDERA/Kali lab instead of stock `Qwen/Qwen3-14B-GGUF`. That exact string is Ollama
tag syntax, which this doc's "Don't build these" section rules out, and no GGUF of huihui-ai's
**v2** checkpoint (`huihui-ai/Huihui-Qwen3-14B-abliterated-v2`, safetensors only) was found on
HF. The operator chose instead (see conversation, 2026-10-08) the confirmed GGUF of huihui-ai's
**v1** checkpoint:

- Repo: `bartowski/huihui-ai_Qwen3-14B-abliterated-GGUF` (quantized from
  `huihui-ai/Qwen3-14B-abliterated`), revision `623c0f3fc42a4699d4583fb16e022942c003d1b7`.
- File: `huihui-ai_Qwen3-14B-abliterated-Q4_K_M.gguf`, 9,001,749,568 bytes — within 3,392 bytes
  of stock candidate C's file size (same param count, same quant).
- sha256 (verified against the downloaded file): `d76889059a3bfab30bc565012a0184827ff2bdc10197f6babc24541b98451dbe`.
- Downloaded to `~/llm/models/huihui-qwen3-14b-abliterated-q4km/`. The first two download
  attempts stalled partway (`curl: (92) HTTP/2 stream ... CANCEL`, a flaky-connection issue
  unrelated to the file itself); the third attempt, forcing `--http1.1` with
  `--retry-all-errors` and `-C -` to resume, completed and the checksum matched.
- Because this is the same base architecture, parameter count, and quant as the already-measured
  stock candidate C, the existing `-ngl 38 -c 16384` settings were kept rather than re-run
  through the full step-4 gate. The staged systemd unit
  (`~/llm/ambermist-llama.service`) was updated to point at the new file; **not yet re-measured
  live** because the unit is already installed and running the stock model (see step 7) — doing
  a side-by-side bench would have needed ~9 GiB more VRAM than the idle card had free, or
  stopping the live service, which needs the operator's `sudo systemctl restart` either way. The
  operator's restart when applying this change doubles as the real-world check; run the step-6
  checks (`/health`, `/v1/models`, a real `/v1/chat/completions` request) right after.
- The old stock file (`~/llm/models/qwen3-14b-q4km/Qwen3-14B-Q4_K_M.gguf`) was left in place
  (not deleted) in case of rollback.

The 27B's KV cache is cheap: it's a hybrid SSM/attention model and pays KV only on the 16
full-attention layers, **64 KiB/token** at f16 (`docs/test-tier-27b.md`, from the GGUF
header). 32,768 tokens of context costs 2 GiB. So VRAM goes almost entirely to weights.

**llama.cpp:** the repo pin is `LLAMA_SHA=e9f824d8` (`node/pins/llama.cpp.conf`) and supports
`qwen35`. The upstream Windows CUDA release zips are built per tag (`bNNNN`). Which tag
contains `e9f824d8` is unverified.

**The lab client** (`docs/caldera-kali.md`): CALDERA's MCP plugin reads `MCP_LLM_API_BASE`
and `MCP_LLM_API_KEY` from its environment and calls `{base}/v1/chat/completions` with
`Authorization: Bearer <key>`, model `reasoner`. It verifies `reasoner` is in `GET /models`
and that a wrong key gives 401 (its step 7). `AMB_API_KEY` is "the same key every lab client
holds." So the local server must accept that exact key and advertise `reasoner` — then only
the base URL changes.

**VS Code client:** `C:\Users\vytka\AppData\Roaming\Code\User\chatLanguageModels.json` holds
the `ambermist` entry (model id `reasoner`, url `http://ambermist-h200:8080/v1`) and **API
keys for other endpoints. Never print it, `cat` it, or commit it** (`docs/27b-agent-loop.md`).
Don't edit it either. Because the local server also answers as `reasoner`, pointing VS Code at
it is just a url change the operator makes; step 7 gives them the snippet.

**`AMB_API_KEY`:** the shared lab key. The operator has it (it's in the lab's env / their
secrets, not committed). The agent must get its **value** from the operator or from an
existing env load, **never print it**, and write it to the key file (step 5). Don't mint a
new key — that would force every lab client to change.

**The tailnet and the gateway** (`docs/lab-gateway.md`, `ops/lab/gateway.sh`, read 2026-10-07):

- Path: `lab client → gateway :8080 → Tailscale → ambermist-h200:8080 → llama.cpp`. The
  gateway is **kali-caldera at `100.66.6.130`**, tagged `tag:ambermist-consumer`, running
  nginx. It binds `100.66.6.130:8080` and `proxy_pass`es to upstream `$h200`, where
  `H200` defaults to `ambermist-h200.tail57998f.ts.net`, **re-resolved per request** via
  MagicDNS. The tailnet domain is `tail57998f.ts.net`.
- `H200` is an **overridable env var** and the `nginx` stage is "safe to rerun." So pointing
  `100.66.6.130` at this PC is: join this PC to the tailnet, then rerun the gateway's `nginx`
  stage with `H200=<this-pc>.tail57998f.ts.net`. Revert by rerunning with no override. **Lab
  clients never change** — they keep calling `http://100.66.6.130:8080/v1`.
- The gateway holds no key; it passes `Authorization: Bearer` straight through and the backend
  checks it (`lab-gateway.md`, the ADR). So this PC's llama.cpp still enforces `AMB_API_KEY`.

**Joining the tailnet:** use **`TS_AUTHKEY` from `.env`** (it's present; load with
`set -a; . .env; set +a`, never print it). The cloud node joins with
`tailscale up --auth-key=file:<path> --hostname=ambermist-h200 --accept-dns=false`
(`node/bootstrap.sh`). This PC joins the same way but with its own hostname (see step 2a), so
it doesn't collide with the cloud node's MagicDNS name. `--accept-dns=false` keeps the PC's
own DNS intact. Tailscale on Windows is **unverified** — `tailscale` isn't on the Windows
PATH as seen from WSL (2026-10-07). If it isn't installed, stop and ask; don't install it
unattended.

## Don't build these

No Docker, Ollama, LM Studio, or nginx. No changes to `node/`, `infra/`, `ops/`, or the
tailnet ACL (`ops/tailnet-policy.hujson`) without asking. Don't bind to `0.0.0.0` on an open
LAN without the firewall rule from step 6. Don't put the API key in the repo, in argv
visible to other users, or in a log.

## Steps

Everything runs **in WSL (Ubuntu 26.04)**. Root is `~/llm`: `~/llm/bin` (llama.cpp),
`~/llm/models/<model-id>/`, `~/llm/keys`, `~/llm/logs`. Steps that need `sudo` are marked
*(operator runs)* — in a background session the agent can't sudo, so the operator runs those
in the prompt with the `! ` prefix.

### 1. Preflight

- `df -h ~` — WSL root has ~955 GB free (checked); candidate A needs ~17 GB.
- `nvidia-smi` in WSL already lists the 3060 (driver 591.86 on the host, CUDA 12.x). Good.
- Build deps *(operator runs)*: `! sudo apt-get update && sudo apt-get install -y build-essential cmake git libcurl4-openssl-dev`.
  CUDA toolkit: if `nvcc` is absent, install the CUDA toolkit for WSL-Ubuntu (NVIDIA's
  `cuda-toolkit` repo) — record the version. GPU *driver* stays on Windows; only the toolkit
  goes in WSL.

### 2a. Join WSL to the tailnet  *(operator runs — needs sudo)*

Tailscale is **not installed** anywhere yet (2026-10-07). Install and join in WSL. The ACL
(`ops/tailnet-policy.hujson`) only lets `tag:ambermist-consumer` (the gateway) reach
`tag:ambermist` on tcp/8080, so the node **must come up tagged `tag:ambermist`** — which the
shared `TS_AUTHKEY` assigns (the cloud node uses the same key). `TS_AUTHKEY` is current (len
61, not expired — operator confirmed 2026-10-07).

Operator runs in the prompt (each line prefixed with `! ` so it runs in this session):

```bash
curl -fsSL https://tailscale.com/install.sh | sh          # official installer, adds the apt repo
set -a; . .env; set +a                                     # loads TS_AUTHKEY; don't echo it
sudo tailscale up --auth-key="$TS_AUTHKEY" \
     --hostname=ambermist-local --accept-dns=false
tailscale status                                           # expect this node up with a 100.x addr
tailscale status --json | grep -i -A2 tags                 # must show tag:ambermist
```

Hostname **`ambermist-local`** avoids colliding with the cloud node's `ambermist-h200`
MagicDNS name, so the cloud node stays registered as the fallback. `--accept-dns=false` keeps
WSL's own DNS. The full name becomes `ambermist-local.tail57998f.ts.net`. **Stop and ask** if
the key is rejected or the node is not tagged `tag:ambermist` (don't mint a key or edit ACLs).

**Re-joining later:** the key is ephemeral, so after WSL has been down for a while the node
is gone and `tailscale up` prints a login URL. Don't open it: a browser login makes the node
user-owned, not `tag:ambermist`. Re-run the `tailscale up --auth-key=...` line above with the
same flags (a bare `tailscale up` is refused because the flags aren't defaults). See
local-llm-architecture.md, "To make this reachable by the lab".

### 2b. SSH access to the gateway  *(done 2026-10-07)*

This WSL had no SSH key (`.env`'s `SSH_KEY` → `/home/vytka/.ssh/verda`, missing here). The
agent generated a dedicated key and host alias — no sudo needed, no secret exposed:

- Key: `~/.ssh/ambermist-gateway` (ed25519). Public key:
  `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINe7atGt9L+FOB3rhRwZRszyX1YLr4Ep7FSWSsginu+O ambermist-local-wsl`
- `~/.ssh/config` alias `amber-gateway` → `amber@100.66.6.130`, `IdentityFile ~/.ssh/ambermist-gateway`.

Register the public key on the gateway (one-time; needs the `amber` password **once**):

```bash
ssh-copy-id -i ~/.ssh/ambermist-gateway.pub amber@100.66.6.130
# or hand the operator the pub key to append to the gateway's ~amber/.ssh/authorized_keys
ssh amber-gateway true && echo "key auth works"          # verify: no password prompt
```

The gateway is only reachable once this WSL is on the tailnet (step 2a) — `100.66.6.130` is a
tailnet address. Do step 2a first.

### 2. Build llama.cpp (Linux/CUDA, in WSL)

The repo already builds llama.cpp from source at a pin; there are no official Linux binary
releases, so build it. Match the repo pin `LLAMA_SHA=e9f824d8` (`node/pins/llama.cpp.conf`),
which already supports `qwen35` — see `node/build-llama.sh` for the exact flags the repo uses
and mirror them.

- `git clone https://github.com/ggml-org/llama.cpp ~/llm/src && cd ~/llm/src && git checkout e9f824d8`
- `cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86` (86 = Ampere / RTX 3060),
  then `cmake --build build -j` and copy `llama-server`, `llama-cli`, `llama-bench` into
  `~/llm/bin`.
- Verify: `~/llm/bin/llama-server --version` and `--list-devices` shows the RTX 3060 (CUDA0).
- Confirm the flags used below exist at this pin (`--help`): `--n-cpu-moe`, `--api-key-file`,
  `--presence-penalty`, `-fa`, `--alias`. (`--fit` is newer; if absent, bisect `-ngl` in
  step 4.)

### 3. Download candidate A

- `~/llm/models/qwen38-27b-uncensored-q4km/`, exact repo/revision/file from
  `node/pins/model.27b.conf` (`https://huggingface.co/<repo>/resolve/<revision>/<file>`,
  `Authorization: Bearer $HF_TOKEN` from `.env`; licence already accepted on that account).
- Check the byte count (16,810,714,496) and `sha256sum` against
  `node/pins/qwen38-27b-uncensored-q4km.sha256`. On a mismatch, delete the file and stop.

### 4. Measure and choose the model

Start each run bound to `127.0.0.1` only, with no key yet, and **alias `reasoner`**
(`--alias reasoner`) so later checks match the lab. Use one slot (`--parallel 1`), `-c 32768`,
`-fa on`, and the card's thinking-mode sampling, the same as `node/serving.27b.conf`:
`--temp 1.0 --top-k 20 --top-p 0.95 --min-p 0 --presence-penalty 1.5`.

**Candidate A, 27B:**

- If `--fit` exists, start with it and read back what it chose. Otherwise bisect `-ngl`
  (start at 40 of 65 blocks), and pick the highest value that loads with ≥ 300 MiB VRAM free
  after a 16k-token prompt.
- Measure with `llama-bench` (pp512, tg128) and a real request through `/v1/chat/completions`
  with a ~8k-token prompt. Record prompt tok/s, generation tok/s, VRAM used, RAM used,
  and time to first token.

**Gate:** keep A if **generation ≥ 8 tok/s and an 8k-token prompt takes ≤ 60 s to the first
token**. Otherwise, search for candidate B (state what you found and where, with revision
SHAs), download the best match the same way (size + sha256 from the HF API), and measure it
too. If B doesn't exist, use C. If several models pass, **ask the operator** which to keep
and present the numbers. Don't decide on quality without them.

**Outcome (2026-10-08):** A failed (2.59 tok/s). B doesn't exist (confirmed via the HF API).
C passes at `-c 16384`/`-ngl 38` (15.24 tok/s, 10.2 s TTFT) — only one candidate passed, so no
operator tie-break was needed. **Chosen: candidate C**, `Qwen/Qwen3-14B-GGUF` Q4_K_M. Full
numbers in [LESSONS.md](../LESSONS.md)'s 2026-10-08 section. Note `-c` had to drop from
32768 to 16384 to get full-ish GPU offload — standard transformer KV at this length doesn't
fit alongside the weights the way the 27B's cheap hybrid-SSM KV did.

### 5. API key — reuse the lab's `AMB_API_KEY`

- **Don't mint a new key.** The lab's clients all hold `AMB_API_KEY`; the goal is that only
  the base URL changes. Get its value from the operator (hidden prompt) or from an env the
  operator has already loaded. Never echo it, never write it to the run log or to git.
- Write it to `~/llm/keys/reasoner-api-key`, `chmod 600`, owned by the operator.
- Pass it with `--api-key-file` (not `--api-key`, which puts the key in argv where other
  users can read it via `ps`). If the pin lacks `--api-key-file`, ask before falling back.
- The server accepts a list of keys, so you can add VS Code's own key later without dropping
  the lab's.

### 6. Make it reachable

- **Bind:** `--host 0.0.0.0 --port 8080` inside WSL. Because `tailscaled` runs in the same
  WSL namespace, the node's `100.x` address is a local interface here, so the gateway can
  reach `ambermist-local:8080` directly. Access is gated by the tailnet ACL (only
  `tag:ambermist-consumer` reaches tcp/8080) and by `--api-key-file`, so no host firewall rule
  is needed. (If llama ever binds only to the Windows side, revisit this — not the case here.)
- **Checks, all from the run log** (these mirror `caldera-kali.md` step 7, so passing them
  means the lab will work):
  - WSL local: `curl http://127.0.0.1:8080/health` → 200; `GET /v1/models` with no key → 401;
    with the key → a list containing `reasoner`.
  - Over the tailnet, **from the gateway `100.66.6.130`** (operator runs it, or over SSH):
    `curl http://ambermist-local.tail57998f.ts.net:8080/v1/models -H "Authorization: Bearer
    $AMB_API_KEY"` lists `reasoner`; without the header → 401; and a `POST /v1/chat/completions`
    with `"model": "reasoner"` returns an answer. This is both the shape the gateway proxies and
    the shape `caldera-kali.md`'s step-7 script sends.
  - From a non-tailnet LAN address, if one is available: connection refused or timeout.

### 7. Start automatically (systemd in WSL), and the VS Code entry

WSL has systemd enabled, so run llama as a service. WSL only runs while something holds it
open, so two parts: a system service, and a Windows logon task that boots the distro.

- Write a unit `/etc/systemd/system/ambermist-llama.service` *(operator installs with sudo)*
  that runs the final `~/llm/bin/llama-server …` command line (model path, `-ngl`/`--fit`,
  context, sampling, `--alias reasoner`, `--api-key-file ~/llm/keys/reasoner-api-key`,
  `--host 0.0.0.0 --port 8080`) as the operator's user, `Restart=on-failure`,
  `RestartSec=5`, journald for logs. `! sudo systemctl enable --now ambermist-llama`.

  **Installed and running (2026-10-08).** The unit at `~/llm/ambermist-llama.service` was
  installed to `/etc/systemd/system/` and enabled by the operator, serving candidate C (`--model
  ~/llm/models/qwen3-14b-q4km/Qwen3-14B-Q4_K_M.gguf -ngl 38 -c 16384 --parallel 1 -fa on`, the
  card sampling, `--api-key-file`). Binding `--host 0.0.0.0` directly from an agent shell is
  blocked by Claude Code's own "Expose Local Services" permission classifier even for local
  testing — this is why the unit (installed by the operator's own `sudo`) is the thing that
  opens the port, not an agent-run command.

  **Update (2026-10-08, staged, not yet applied):** the unit file was edited in place to swap
  the model to the huihui-ai abliterated build described above — same `-ngl`/`-c`/sampling,
  only the `--model` path changed. The live service is still running the old stock model until
  the operator re-applies the unit. Since the service is already enabled and running the old
  model, `enable --now` won't pick up the new `ExecStart` on its own — use `restart`:
  ```bash
  sudo cp ~/llm/ambermist-llama.service /etc/systemd/system/ambermist-llama.service
  sudo systemctl daemon-reload
  sudo systemctl restart ambermist-llama
  ```
- So the distro (and the service) comes up at Windows logon, register **one Windows** Task
  Scheduler task that runs `wsl.exe -d <distro> true` at logon — that boots WSL, and systemd
  then starts the enabled service. (`wsl -l -v` gives the distro name.) Hand the operator the
  `Register-ScheduledTask` one-liner; it's the only Windows-side piece.
- Verify: `wsl --terminate <distro>` then `wsl.exe -d <distro> true`, wait, and
  `curl http://127.0.0.1:8080/health` from WSL → 200 without anyone starting llama by hand.
- Give the operator this snippet for `chatLanguageModels.json`, with the measured values filled
  in. **Don't open or edit that file yourself.** `maxInputTokens` + `maxOutputTokens` must be
  ≤ `-c`, leaving at least 1,024 tokens for template overhead. Reasoning counts as output
  (`docs/27b-agent-loop.md`).

  Because the model id stays `reasoner`, this is just a url change on the existing `ambermist`
  entry — from `http://ambermist-h200:8080/v1` to the PC's own address — and the lower
  token limits that match the local `-c`:

  ```json
  {
    "name": "Ambermist local",
    "vendor": "customendpoint",
    "apiType": "chat-completions",
    "url": "http://localhost:8080/v1",
    "models": [{ "id": "reasoner", "name": "Ambermist local (14B abliterated)", "toolCalling": true,
                 "maxInputTokens": 12000, "maxOutputTokens": 3000 }]
  }
  ```

  Values match the chosen candidate C's `-c 16384` (sum 15,000 ≤ 16,384, leaving 1,384 tokens
  for template overhead). Model name updated from "(27B)" to "(14B)" since candidate C
  (`Qwen3-14B`), not the 27B, is what's actually being served locally.

  Copy the field names from the existing `ambermist` entry's shape (ask the operator to
  confirm it; you can't read the file). Don't assume the snippet's keys match exactly.

### 8. Point `100.66.6.130` at the PC (repoint the gateway)

This is what makes testing free, and it needs **no change to any lab client** — they keep
calling `http://100.66.6.130:8080/v1`. Only the gateway's upstream moves from the cloud node
to this PC.

- On the gateway, via the `amber-gateway` SSH alias set up in step 2b, rerun only the nginx
  stage of `ops/lab/gateway.sh` with the upstream overridden to this PC:

  ```bash
  scp ops/lab/gateway.sh amber-gateway:/tmp/gateway.sh
  ssh amber-gateway 'sudo H200=ambermist-local.tail57998f.ts.net bash /tmp/gateway.sh nginx verify'
  ```

  The script dispatches per stage (`netfilter dns nginx verify`, confirmed 2026-10-07), so
  `nginx verify` rewrites just the site and then runs the built-in `/health`-through-the-gateway
  check with the new upstream. nginx re-resolves the name per request, so once this PC is up the
  next request lands on it.
- **This is an outward change to the live lab gateway.** Show the operator the exact command
  and get approval before running it; or hand it to them to run. Don't touch the cloud node's
  tailnet registration — leaving `ambermist-h200` registered is what lets you revert.
- **Revert** (back to the cloud node): rerun the same stage with no `H200` override
  (`sudo bash /tmp/gateway.sh nginx`), which restores the default `ambermist-h200.tail…`.
- Verify through the gateway, exactly as a lab client sees it:
  `curl http://100.66.6.130:8080/health` → 200, and `caldera-kali.md`'s step-7 script against
  `http://100.66.6.130:8080/v1` — `reasoner` present, 401 without the key, an answer
  containing `42`. That proves the lab is now served by the PC.
- Record that the cloud node can now be destroyed for test sessions
  (`tofu -chdir=infra/compute destroy`) to stop paying, and that it stays the fallback — bring
  it back and revert the gateway for anything the 3060 is too small or slow for.

### 9. Record

Append a dated section to [LESSONS.md](../LESSONS.md) covering: the llama.cpp tag and why;
each model measured (repo, revision, file, sha256, `-ngl`, VRAM/RAM used, pp/tg tok/s,
time to first token at 8k); the model chosen and who chose it; the reachability results from
step 6; and anything in this doc that turned out wrong. Fix this doc where it was wrong.
Keep raw output in `~/ambermist-runs/2026-10-07-local/`, not in the repo.

## Done when

- This PC is on the tailnet as `ambermist-local`, and `/health` answers after a fresh logon
  without anyone starting the server.
- With the gateway repointed, the lab's `caldera-kali.md` step-7 check passes **through the
  unchanged gateway** `http://100.66.6.130:8080/v1` with `AMB_API_KEY`: `reasoner` listed,
  401 without the key, answer contains `42`. No lab client was edited.
- VS Code chat answers a tool-calling request with "Ambermist local" (operator confirms).
- The firewall rule blocks non-tailnet sources; no key → 401 locally and over the tailnet.
- Revert is one command (gateway `nginx` stage with no `H200`); the cloud node is still
  registered so it works.
- LESSONS.md has the measurements. Nothing secret is in git (`git diff` checked), and
  neither `AMB_API_KEY` nor `TS_AUTHKEY` was printed or written to a tracked file.

## Stop and ask if

- Tailscale isn't installed on Windows, or `TS_AUTHKEY` is rejected (expired/consumed/wrong
  tags) — don't install Tailscale unattended or mint a new auth key / edit ACLs alone.
- The operator can't provide `AMB_API_KEY` (don't mint a replacement without agreement — it
  breaks every other lab client).
- The gateway's `gateway.sh` can't run a single `nginx` stage, or the override doesn't take —
  don't hand-edit the live nginx config; report what the script actually supports.
- No model passes the step 4 gate. A test tier that's far slower than the H200 may still be
  fine for tool-wiring tests, but that's the operator's call — present the numbers.
- Any step needs admin rights (firewall, scheduled task with elevation).
- The sha256 doesn't match, or the HF revision is gone.
