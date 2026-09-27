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
| `infra/compute/` | Registers your public key with Verda, the boot script, the spot H200 instance, and the volume attachment. Created and destroyed every session. |
| `node/` | Copied to `/opt/ambermist` on the node: pins, `serving.conf`, `bootstrap.sh`, `bin/fetch-model.sh`, `bin/build-llama.sh`. Knows nothing about Verda. |
| `ops/fleet-check.py` | Read-only account check, fleet cap, orphan volumes, H200 availability, site pick. |
| `ops/provision.sh` | Runs from your workstation: copies `node/`, pushes secrets, runs the stages over SSH. |
| `ops/accept/t1.py` | T1 tool-call round trip through the tunnel. Standard library only. |

The `.tf` files know volume sizes and the instance SKU, nothing about Qwen or llama.cpp. To
change the model or runtime, edit `node/pins/` and `node/serving.conf`.

## Prerequisites

- OpenTofu ≥ 1.8, `python3`, `ssh`, `curl`, `jq`.
- One config file, `.env` in the repo root (gitignored). Copy `.env.example` to `.env` and fill it in.
  It holds the credentials (`VERDA_CLIENT_ID`, `VERDA_CLIENT_SECRET`, `HF_TOKEN`, `AMB_API_KEY`), the
  path to your SSH private key (`SSH_KEY`), and the compute stack settings as `TF_VAR_*` variables
  (`TF_VAR_owner`, `TF_VAR_admin_cidrs`, `TF_VAR_ssh_public_key_path`). Load it before running anything:
  `set -a; source .env; set +a`. Never print or commit it.
  - `AMB_API_KEY` is the bearer key clients send to llama-server (`amb-` + `openssl rand -hex 32`).
  - `admin_cidrs` is the only set of addresses the node's firewall lets reach ports 22 and 8080. If
    your public IP is outside it, you lock yourself out.
  - `SSH_KEY` is the path to your **private** key file (used to log in). `TF_VAR_ssh_public_key_path` is
    the path to the matching **public** key file (`.pub`). OpenTofu reads that file itself, so you never
    paste key text anywhere.
  - The one exception is `infra/storage/terraform.tfvars` (just `location = "<site>"`). `fleet-check.py
    --pick-site` writes it for you; don't edit it by hand.

## The compute stack in detail

`infra/compute/` is the disposable half: apply it to get a GPU node, destroy it to stop billing.
It never creates or deletes the model volume; it only reads that volume's ID and location from the
storage stack's local state (`infra/storage/terraform.tfstate`, via `terraform_remote_state`). Because
the location comes from that state, the instance always lands in the same site as the weights.

**Prerequisite: the storage stack must have been applied from this same checkout.** That apply
(step 2 under "Spin up" below) is what creates `infra/storage/terraform.tfstate`. The file is
gitignored, so a fresh clone or a different machine doesn't have it, and `tofu plan` in
`infra/compute` fails because it can't read the volume ID and location. If the volume already exists
in Verda but the state file is missing, copy the state file over from the machine that created the
volume. Don't re-apply the storage stack blindly: it would try to create a second `ambermist-model`.

Resources it manages:

| Resource | Purpose |
| :-- | :-- |
| `verda_ssh_key.op` | Uploads your public key (the file at `ssh_public_key_path`) to Verda as `ambermist-operator` and installs it on the instance so you can log in as `root`. No new key is generated; the entry is removed on `destroy`. |
| `verda_startup_script.boot` | Renders `boot.sh.tftpl` (nftables firewall only, no secrets). Stored in plaintext in the Verda API and in state. |
| `verda_instance.node` | The single `1H200.141S.44V` instance, hostname `ambermist-h200`, in the model volume's site. Gets a 60 GiB NVMe OS volume `ambermist-h200-os`. |
| `verda_volume_attachment.model` | Attaches the existing `ambermist-model` volume. The only resource that waits until the instance is reachable. |
| `terraform_data.identity` | Records SKU, spot flag, and the hash of the boot script. Any change to them **replaces** the instance (see below). |

Variables (`infra/compute/variables.tf`). Set them in `.env` as `TF_VAR_<name>` (see `.env.example`). A
`terraform.tfvars` file in `infra/compute/`, if you still have one, takes precedence over the environment,
so delete it once you have moved its values to `.env`:

| Variable | Default | Notes |
| :-- | :-- | :-- |
| `owner` | none, required | Goes in the instance description. |
| `ssh_public_key_path` | none, required | Path to your public key file (`.pub`). |
| `admin_cidrs` | none, required | Firewall allow-list for ports 22 and 8080. Must not contain `0.0.0.0/0`. |
| `instance_type` | `1H200.141S.44V` | Validation rejects anything else: exactly one H200 is allowed. |
| `use_spot` | `true` | Spot is about half the on-demand price and can be reclaimed. Set `-var use_spot=false` only after asking. |
| `image` | `24.04.cuda12.9.docker` | Ubuntu 24.04 with CUDA 12.9 and Docker. |
| `os_volume_size_gib` | `60` | |

Behaviours worth knowing:

- **Spot vs on-demand is fixed at creation.** `is_spot` and the boot script can't be updated in
  place, so `terraform_data.identity` forces a replacement whenever the SKU, the spot flag, or the
  script body changes. Expect `plan` to show "must be replaced", not "updated in place".
- **The OS disk is disposable.** On spot, `on_spot_discontinue = "delete_permanently"` deletes the OS
  volume on reclaim, so `/srv/build` and `/srv/logs` are lost. The model volume is `keep_detached`
  (set in the storage stack) and survives.
- **`public_ip` may be `null`** right after create. Run `apply -refresh-only` until it appears.
- **Billing runs from the moment `apply` starts the instance until `destroy` finishes.** The `ssh`
  and `burn_warning` outputs are reminders of that.
- **Outputs:** `instance_id`, `public_ip`, `ssh`, `burn_warning`.
- **Firewall:** the boot script takes about a minute after the instance is reachable. Only
  `admin_cidrs` reach ports 22 and 8080; if your IP isn't listed you can't log in.
- **State is local** (`infra/compute/terraform.tfstate`, gitignored). Run tofu from the same checkout
  each time, or it won't know about the running instance.

### Relaunching with an existing model volume

Use this path when `fleet-check.py --pick-site` reports `ambermist-model` as `detached` and prints
`site: <site> (location of existing ambermist-model; nothing changed)`. Don't touch `infra/storage`.

```bash
set -a; source .env; set +a
python3 ops/fleet-check.py --pick-site            # expect: model volume detached, spot=True at its site
tofu -chdir=infra/compute init                    # only the first time on this checkout
tofu -chdir=infra/compute plan                    # expect: 5 to add (public key upload, boot script, instance, attachment, identity marker)
tofu -chdir=infra/compute apply                   # GPU billing starts
tofu -chdir=infra/compute apply -refresh-only     # repeat until public_ip is set
ops/provision.sh "$(tofu -chdir=infra/compute output -raw public_ip)"
```

If `--pick-site` shows `spot=False` for the volume's site, the apply will fail. Retry later or ask
before using on-demand: the site can't move while the volume holds the weights. When you finish,
`tofu -chdir=infra/compute destroy`.

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
ssh -i "${SSH_KEY:-$HOME/.ssh/verda}" -N -L 8080:127.0.0.1:8080 root@<public_ip> &
python3 ops/accept/t1.py     # uses AMB_API_KEY from the sourced .env; exits 0 only if T1 passes
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
