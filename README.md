# ambermist

A self-hosted llama.cpp endpoint (Qwen3.8-Flash-Next, UD-Q4_K_XL) on one Verda H200.
This README describes how spin-up works **as of Phase 1** (first light: serve on loopback,
test through an SSH tunnel). The design is in [docs/inference-tier-plan.md](docs/inference-tier-plan.md),
the Phase 1 task in [docs/phase-1-first-light.md](docs/phase-1-first-light.md), and measured
facts and gotchas in [LESSONS.md](LESSONS.md). Rules for coding assistants: [AGENTS.md](AGENTS.md).

## How it fits together

Two OpenTofu stacks, one node-side bootstrap, and a few scripts:

| Part | What it does |
| :-- | :-- |
| `infra/storage/` | The 128 GiB NVMe model volume `ambermist-model` (`prevent_destroy`, kept when a spot node is reclaimed). Rarely changes. |
| `infra/compute/` | SSH key, boot script, the spot H200 instance, and the volume attachment. Created and destroyed every session. |
| `node/` | Copied to `/opt/ambermist` on the node: pins, `serving.conf`, `bootstrap.sh`, `bin/fetch-model.sh`, `bin/build-llama.sh`. Knows nothing about Verda. |
| `ops/fleet-check.py` | Read-only account check, fleet cap, orphan volumes, H200 availability, site pick. |
| `ops/provision.sh` | Runs from your workstation: copies `node/`, pushes secrets, runs the stages over SSH. |
| `ops/accept/t1.py` | T1 tool-call round trip through the tunnel. Standard library only. |

The `.tf` files know volume sizes and the instance SKU, nothing about Qwen or llama.cpp. To
change the model or runtime, edit `node/pins/` and `node/serving.conf`.

## Prerequisites

- OpenTofu ≥ 1.8, `python3`, `ssh`, `curl`, `jq`.
- A `.env` in the repo root (gitignored) with `VERDA_CLIENT_ID`, `VERDA_CLIENT_SECRET`,
  `HF_TOKEN`, and `AMB_API_KEY` (`amb-` + `openssl rand -hex 32`; the bearer key clients send
  to llama-server). Load it with `set -a; source .env; set +a`. Never print or commit it.
- `infra/compute/terraform.tfvars` (gitignored; copy `terraform.tfvars.example`): `owner`,
  `ssh_public_keys`, and `admin_cidrs`. `admin_cidrs` is the only set of addresses the node's
  firewall lets reach ports 22 and 8080. If your public IP is outside it, you lock yourself out.
- An SSH key at `~/.ssh/verda` (override with `SSH_KEY=`), whose public half is in `ssh_public_keys`.

## Spin up

```bash
set -a; source .env; set +a

# 1. Check the account and pick a site. Exit 1 = another H200 exists (fleet cap): stop.
python3 ops/fleet-check.py --pick-site
```

`--pick-site` uses the site of `ambermist-model` if it exists. Otherwise it takes the first
site with a spot H200 (FIN-02, FIN-01, FIN-03) and writes `infra/storage/terraform.tfvars`.
Availability is not a reservation.

```bash
# 2. Storage stack (first time only; afterwards it already exists).
tofu -chdir=infra/storage init
tofu -chdir=infra/storage apply

# 3. Compute stack. Billing for the GPU starts here (spot, about 2.3/h).
tofu -chdir=infra/compute init
tofu -chdir=infra/compute apply
tofu -chdir=infra/compute output public_ip
```

`public_ip` can be `null` right after create. Run `tofu -chdir=infra/compute apply -refresh-only`
until it is set. If a spot H200 isn't available at the volume's site, the apply fails: retry, or ask
before switching to on-demand (`-var use_spot=false`). The site can't change while the volume
holds the weights.

The instance boots with a startup script that only installs the nftables firewall (SSH and
8080 from `admin_cidrs`, everything else dropped). It takes about a minute after the instance is
reachable, so the first SSH login can see no `inet amb` table yet. The script holds no secrets.

```bash
# 4. Provision the node (about 25 min on a cold volume).
ops/provision.sh <public_ip>
```

`provision.sh` waits for SSH (as `root`, `accept-new` host keys), copies `node/` to
`/opt/ambermist`, creates the `llama` user, and pushes secrets **on stdin only**:
`AMB_API_KEY` → `/etc/ambermist/llama-api-keys` (root:llama, 0640) and the Hugging Face header →
`/run/ambermist/hf-auth-header` (0600, tmpfs). It then runs `node/bootstrap.sh` stages in order:

| Stage | What it does |
| :-- | :-- |
| `packages` | apt packages; prints GPU, driver, and RAM. |
| `disks` | Mounts the volume by label `amb-model` at `/srv/models`. Formats only if exactly one blank 128 GiB disk exists; otherwise aborts. |
| `model` + `build` | Run in parallel. `model`: resumable download of 4 shards, SHA-256 checked against `node/pins/qwen38-ud-q4kxl.sha256`. `build`: llama.cpp at the pinned commit in a CUDA container; fails if `--version` doesn't show the pin. |
| `t0` | Checks GGUF metadata (`qwen4exp`, `compress_ratios` only 0 and 4) and the server version. |
| `serve` | Starts llama-server as the transient unit `llama-server` on `127.0.0.1:8080`, waits for `/health`. |

Every stage is safe to rerun. To run only some: `ops/provision.sh <ip> t0 serve`. Logs are in
`/srv/logs/bootstrap/<stage>.log` with start and end times in `timeline.log`. `/srv/build` and
`/srv/logs` are on the spot OS disk and are lost if the instance is reclaimed or destroyed.
A warm model volume skips the download (`fetch-model.sh` fast path), so a rerun takes a few minutes
plus the build.

## Test

The API listens on loopback only. Open a tunnel and run T1:

```bash
ssh -i ~/.ssh/verda -N -L 8080:127.0.0.1:8080 root@<public_ip> &
AMB_API_KEY=... python3 ops/accept/t1.py     # exits 0 only if T1 passes
```

Results go to `~/ambermist-runs/<date>/t1-<time>.json`. On the node, the default log verbosity
hides the loader lines (offload count, buffer sizes). To see them, run
`LLAMA_EXTRA_ARGS="-lv 5" /opt/ambermist/bootstrap.sh serve` on the node.

## Tear down

```bash
tofu -chdir=infra/compute destroy       # stops GPU billing; the model volume stays
python3 ops/fleet-check.py              # expect: no instances, model volume detached
python3 ops/fleet-check.py --reap-os    # only when you want detached ambermist-*-os volumes deleted
```

Destroy at the end of every session. A destroyed instance can leave a detached
`ambermist-h200-os` volume that keeps billing (about €12/month); `fleet-check.py` lists it.
Copy anything you want to keep from `/srv/logs` first. Never destroy the storage stack; the
`prevent_destroy` on the model volume is deliberate.

## Spot reclaim

The model volume is `keep_detached`, so its data survives. The OS disk is deleted. After a
reclaim, `tofu -chdir=infra/compute apply` refreshes and recreates the instance (`is_spot` isn't
updatable in place, so any change to SKU, spot, or the boot script replaces it), then rerun
`ops/provision.sh`. This path has not been exercised yet.

## Not built yet

Public API on 8080, nginx, systemd unit for llama-server, health checks, Prometheus, Tailscale,
`make` targets, build and logs volumes, SOPS secrets, and tests T2–T8. See the phases in the plan.
