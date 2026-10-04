# ambermist

A self-hosted llama.cpp endpoint (Qwen3.8-Flash-Next Uncensored, Q4_K_M) on one Verda H200.
The model volume is replicated per configured site: FIN-02 is primary and FIN-03 is the fallback.
This README describes how spin-up works **as of Phase 6**: llama-server runs as a systemd unit on
`127.0.0.1:8081`, nginx serves the API on port 8080, reachable only over Tailscale at
`http://ambermist-h200:8080/v1` (public 8080 is closed), a timer restarts a hung server. The Phase 6
code has not been run yet. The design is in [docs/inference-tier-plan.md](docs/inference-tier-plan.md),
the Phase 1 task in [docs/phase-1-first-light.md](docs/phase-1-first-light.md), the Phase 6 task in
[docs/phase-6-tailscale.md](docs/phase-6-tailscale.md), and measured
facts and gotchas in [LESSONS.md](LESSONS.md). Rules for coding assistants: [AGENTS.md](AGENTS.md).

Lab clients do not join the tailnet themselves: one gateway host carries Tailscale and fronts
the API on the lab network, because the lab's addresses collide with the range Tailscale claims.
See [docs/lab-gateway.md](docs/lab-gateway.md) and
[ADR 0006](docs/adr/0006-lab-gateway-for-llm-access.md). For a CALDERA client, follow
[CALDERA with a remote LLM through the lab gateway](docs/caldera-kali.md).

## How it fits together

Two OpenTofu stacks, one node-side bootstrap, and a few scripts:

| Part | What it does |
| :-- | :-- |
| `infra/storage/` | The 140 GiB NVMe `ambermist-model` volumes, one per site in `locations` (`prevent_destroy`, kept when a spot node is reclaimed). FIN-02 is primary; FIN-03 is the fallback. |
| `infra/compute/` | Registers your public key with Verda, the boot script, the spot H200 instance in the selected site, and that site's volume attachment. Created and destroyed every session. |
| `node/` | Copied to `/opt/ambermist` on the node: pins, `serving.conf`, `bootstrap.sh`, and `bin/` (download, build, preflight, health check). `node/etc/` mirrors the files installed under `/etc`: systemd units and timers, the nginx site, logrotate. Knows nothing about Verda. |
| `ops/fleet-check.py` | Account check, fleet cap, orphan volumes, H200 availability, site pick, local state vs. account. Read-only unless given a delete or clean flag. |
| `ops/provision.sh` | Runs from your workstation: copies `node/`, pushes secrets, runs the stages over SSH. |
| `ops/session.sh` | One command per session step: `check`/`clean` stale compute state, `up` (claim a spot H200, apply, provision), `down compute\|volumes\|all`. |
| `ops/tailnet-policy.hujson` | The tailnet policy (who reaches the node's ports 22 and 8080). Applied by hand in the Tailscale admin console. |
| `ops/lab/gateway.sh` | Configures the lab gateway: Tailscale on the tailnet side, nginx on the lab side. Staged and safe to rerun. |
| `ops/accept/t1.py` | T1 tool-call round trip through the tunnel. Standard library only. |

The `.tf` files know volume sizes and the instance SKU, nothing about Qwen or llama.cpp. To
change the model or runtime, edit `node/pins/` and `node/serving.conf`.

## Prerequisites

- OpenTofu ≥ 1.8, `python3`, `ssh`, `curl`, `jq`.
- One config file, `.env` in the repo root (gitignored). Copy `.env.example` to `.env` and fill it in.
  It holds the credentials (`VERDA_CLIENT_ID`, `VERDA_CLIENT_SECRET`, `HF_TOKEN`, `AMB_API_KEY`, `TS_AUTHKEY`), the
  path to your SSH private key (`SSH_KEY`), and the compute stack settings as `TF_VAR_*` variables
  (`TF_VAR_owner`, `TF_VAR_admin_cidrs`, `TF_VAR_ssh_public_key_path`). Load it before running anything:
  `set -a; source .env; set +a`. Never print or commit it.
  - `AMB_API_KEY` is the bearer key clients send to llama-server (`amb-` + `openssl rand -hex 32`).
  - `TS_AUTHKEY` is the node's Tailscale auth key: reusable, ephemeral, pre-approved, tagged
    `tag:ambermist`, 90-day expiry. Put its expiry date in a comment next to it.
  - `admin_cidrs` is the only set of addresses the node's firewall lets reach SSH (port 22). If
    your public IP is outside it, you lock yourself out. The API is not reachable from the internet.
  - `SSH_KEY` is the path to your **private** key file (used to log in). `TF_VAR_ssh_public_key_path` is
    the path to the matching **public** key file (`.pub`). OpenTofu reads that file itself, so you never
    paste key text anywhere.
  - `infra/storage/terraform.tfvars` holds the `locations` set and is edited by hand. Do not put
    `location` in `.env`: `infra/compute/terraform.tfvars` is written by `fleet-check.py --pick-site`,
    or set `location` with `-var` for a one-off plan/apply.

Tailnet (set up once, by hand):

- A tailnet with MagicDNS on. Put your login in `group:amb-admin` in
  [ops/tailnet-policy.hujson](ops/tailnet-policy.hujson) and apply it in the admin console (Access
  controls). The default policy allows everything, so replace or narrow it; if the tailnet has other
  devices, merge the entries instead.
- A node auth key with the properties above, in `.env` as `TS_AUTHKEY`.
- One lab gateway runs Tailscale tagged `tag:ambermist-consumer` and holds no key; it needs outbound
  TCP 443 and UDP, nothing inbound. Every other lab client reaches the API through it over plain
  HTTP and holds the API key itself. [ADR 0006](docs/adr/0006-lab-gateway-for-llm-access.md) says why.

## The compute stack in detail

`infra/compute/` is the disposable half: apply it to get a GPU node, destroy it to stop billing.
It never creates or deletes model volumes; it reads the selected site's volume ID from the storage
stack's local state (`infra/storage/terraform.tfstate`, via `terraform_remote_state`). The instance
location and attachment are both selected by the compute `location` variable.

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
| `verda_instance.node` | The single `instance_type` instance (default `1H200.141S.44V`), hostname `ambermist-h200` whatever the SKU, in `location`. Gets a 60 GiB NVMe OS volume `ambermist-h200-os`. |
| `verda_volume_attachment.model` | Attaches `model_volume_ids[location]`. The only resource that waits until the instance is reachable. |
| `terraform_data.identity` | Records SKU, spot flag, and the hash of the boot script. Any change to them **replaces** the instance (see below). |

Variables (`infra/compute/variables.tf`). Set them in `.env` as `TF_VAR_<name>` (see `.env.example`),
except for `location`, `instance_type` and `use_spot`, which are normally written to `infra/compute/terraform.tfvars` by the picker.
Precedence is `-var` over `terraform.tfvars` over `TF_VAR_*`:

| Variable | Default | Notes |
| :-- | :-- | :-- |
| `owner` | none, required | Goes in the instance description. |
| `location` | none, required | Model-volume site. `fleet-check.py --pick-site` writes `infra/compute/terraform.tfvars`; `-var location=...` also works. |
| `ssh_public_key_path` | none, required | Path to your public key file (`.pub`). |
| `admin_cidrs` | none, required | Firewall allow-list for SSH (port 22). Must not contain `0.0.0.0/0`. |
| `instance_type` | `1H200.141S.44V` | Written by `--pick-site --instance`. Validation allows only SKUs within the fleet cap: `1H200.141S.44V`, `2RTXPRO6000.60V`. Only the H200 is qualified to serve the model. |
| `use_spot` | `true` | Written by `--pick-site --type spot\|on-demand`. Spot is about half the on-demand price and can be reclaimed. |
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
  `admin_cidrs` reach port 22 (if your IP isn't listed you can't log in); ports 22 and 8080 are
  also open on `tailscale0`, where the tailnet policy decides who connects; `udp/41641` is open to
  all for tailscaled. Changing the rules changes the boot script, which replaces the instance.
- **State is local** (`infra/compute/terraform.tfstate`, gitignored). Run tofu from the same checkout
  each time, or it won't know about the running instance.

### Relaunching with an existing model volume

Use this path when `fleet-check.py --pick-site` reports a detached `ambermist-model` and writes
`location` to `infra/compute/terraform.tfvars`. If `ambermist-h200` is already running, the picker
leaves the current selection unchanged. Don't touch `infra/storage` for a relaunch.

```bash
set -a; source .env; set +a
python3 ops/fleet-check.py --pick-site            # choose FIN-02, or detached FIN-03 as fallback
tofu -chdir=infra/compute init                    # only the first time on this checkout
tofu -chdir=infra/compute plan                    # expect: 5 to add (public key upload, boot script, instance, attachment, identity marker)
tofu -chdir=infra/compute apply                   # GPU billing starts
tofu -chdir=infra/compute apply -refresh-only     # repeat until public_ip is set
ops/provision.sh "$(tofu -chdir=infra/compute output -raw public_ip)"
```

If no candidate site has a spot H200, `--pick-site` prints on-demand availability at the candidate
sites; pass `--type on-demand` to pick an on-demand site instead. It never switches by itself and
never relaunches automatically.
Never change `location` while an instance exists. When you finish,
`tofu -chdir=infra/compute destroy`.

## The short path: `ops/session.sh`

`ops/session.sh` runs the steps in "Spin up" and "Tear down" below. It loads `.env` itself.

```bash
ops/session.sh check                 # read-only; exit 3 = compute state holds a dead instance
ops/session.sh clean                 # drop that instance from compute state
ops/session.sh up --yes --wait       # claim a spot H200 as soon as one appears, apply, provision
ops/session.sh up --yes --wait=10s   # the same hunt, checking every 10 seconds
ops/session.sh up --yes --wait=5m    # ... or every 5 minutes, for a hunt left running for hours
ops/session.sh up --type on-demand                      # on-demand instead of spot
ops/session.sh up --site FIN-03 --instance 1H200.141S.44V  # choose the site and/or the SKU
ops/session.sh down compute          # tailscale logout, destroy, reap detached OS volumes
ops/session.sh down volumes          # delete the model volumes: the weights are lost
ops/session.sh down all              # both
```

- **Stale state.** After a spot reclaim or a zero balance, Verda keeps the instance with status
  `discontinued`. `GET /instances` hides it, but the provider still reads it by ID, so it stays in
  state and `plan` tries to attach the volume to it. `check` reports that (or a 404); `clean`,
  `up` and `down` remove the instance and its attachment with `tofu state rm`. All of them stop
  if the account has an `ambermist-h200` or `ambermist-model` the local state doesn't know about,
  because applying would create a duplicate.
- **`up`** cleans, applies `infra/storage`, runs `--pick-site`, applies `infra/compute`, waits for
  `public_ip`, clears that IP's old host key, and runs `provision.sh`. The storage apply changes
  nothing unless `down volumes` ran; then it creates blank volumes and provisioning downloads the
  weights again. `--yes` passes `-auto-approve`, so GPU billing starts without a prompt.
  `--wait[=EVERY]` polls every 30 s (or EVERY) while no candidate site has the SKU as the chosen
  type, and retries an apply that failed before any instance existed. EVERY is seconds or a
  duration — `10s`, `45s`, `5m`, `2h`, `1h30m` — so a tight hunt can check every ten seconds and a
  long one can run for hours between polls. Each try logs the time, the try number and how long the
  wait has run.
  It never switches between spot and on-demand by itself, never waits out the fleet cap, and stops
  if a failed apply left an instance in state.
- **`--type`** is `spot` (default) or `on-demand`: about twice the price, never reclaimed.
- **`--site`** limits `up` to one model-volume site (only FIN-02 and FIN-03 have one).
  **`--instance`** picks the SKU (default `1H200.141S.44V`) from those in the `instance_type` row
  above. The picker
  counts the GPUs of that family already held by other instances and refuses the SKU if the cap
  (1 H200, 1 H100, 2 RTX PRO 6000) has no room. With `ambermist-h200` running, asking for a
  different site, type or SKU is refused; `down compute` first. The node keeps the name
  `ambermist-h200`, so the gateway and API URL don't change. `build-llama.sh` builds for the GPU
  it finds (sm90 on H100/H200, sm120 on RTX PRO 6000). Only the H200 is qualified: the model used
  about 81 GiB of VRAM there, more than an H100 has, and RTX PRO 6000 is untested, so
  provisioning another SKU may fail at the `serve` stage.
- **`down`** asks before each part unless `--yes`. `volumes` refuses while compute state holds an
  instance. `prevent_destroy` blocks `tofu destroy`, so it deletes each volume through the API and
  then removes it from storage state.
- `check` and `clean` have run against the real account (`clean` on a copy of the state).
  `up` and `down` have only run against a mock API with `tofu apply`/`destroy` intercepted.

## Spin up

```bash
set -a; source .env; set +a

# 1. Check the account and pick a detached model-volume site. Exit 1 = the fleet cap has no room: stop.
python3 ops/fleet-check.py --pick-site
```

`--pick-site` preserves a running `ambermist-h200`. Otherwise it takes the first site in
FIN-02, FIN-01, FIN-03 that has a detached `ambermist-model` and a spot H200, then writes
`infra/compute/terraform.tfvars`. With the configured FIN-02 primary and FIN-03 fallback, it
uses FIN-03 when FIN-02 has no spot H200. It never edits `infra/storage/terraform.tfvars`.
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
until it is set. If a spot H200 isn't available at the selected site, run `--pick-site` again so it
can choose the detached fallback volume. If no candidate has spot capacity, ask before switching to
on-demand (`--pick-site --type on-demand`). Never change `location` while an instance exists.

The instance boots with a startup script that only installs the nftables firewall (SSH from
`admin_cidrs`, SSH and 8080 from `tailscale0`, `udp/41641` for tailscaled, everything else
dropped). It takes about a minute after the instance is reachable, so the first SSH login can see
no `inet amb` table yet. The script holds no secrets.

```bash
# 4. Provision the node (about 25 min on a cold volume).
ops/provision.sh <public_ip>
```

`provision.sh` waits for SSH (as `root`, `accept-new` host keys), copies `node/` to
`/opt/ambermist`, creates the `llama` user, and pushes secrets **on stdin only**:
`AMB_API_KEY` → `/etc/ambermist/llama-api-keys` (root:llama, 0640) and the Hugging Face header →
`/run/ambermist/hf-auth-header` and `TS_AUTHKEY` → `/run/ambermist/ts-authkey` (both 0600, tmpfs).
It then runs `node/bootstrap.sh` stages in order:

| Stage | What it does |
| :-- | :-- |
| `packages` | Waits until the boot script's firewall is loaded, then installs apt packages (including nginx); prints GPU, driver, and RAM. |
| `tailscale` | Installs `tailscale` from pkgs.tailscale.com (noble repo) and, unless already running, joins with `tailscale up --auth-key=file:/run/ambermist/ts-authkey --hostname=ambermist-h200 --accept-dns=false`, then deletes the key file. Fails if the device name isn't `ambermist-h200` (a stale device holds it: remove that in the admin console and rerun). Prints the DNS name and tailnet IPv4. |
| `disks` | Mounts the volume by label `amb-model` at `/srv/models`. Formats only if exactly one blank 140 GiB disk exists; otherwise aborts. |
| `model` + `build` | Run in parallel. `model`: resumable download of 3 shards, SHA-256 checked against `node/pins/qwen38-uncensored-q4km.sha256`. `build`: llama.cpp at the pinned commit in a CUDA container; fails if `--version` doesn't show the pin. |
| `t0` | Checks GGUF metadata (`qwen4exp`, `compress_ratios` only 0 and 4) and the server version. |
| `serve` | Installs `llama-server.service` (on `127.0.0.1:8081`, restarts on failure, `preflight-serve.sh` checks model, build, key file and GPU first) and `llama-healthcheck.timer` (restarts the server after 3 failed `/health` checks 30 s apart; 503 is fine for the first 20 min), and hourly logrotate for `/srv/logs/llama` and `/srv/logs/nginx`. Waits for `/health`. |
| `nginx` | Refuses to run unless the firewall is loaded. Installs the `ambermist` site on `0.0.0.0:8080` (the firewall admits 8080 only on `tailscale0`): `/health`, `/v1/chat/completions`, `/v1/models` go to llama-server; everything else is 404; more than 12 concurrent API connections get 429. JSON access log (no bodies) in `/srv/logs/nginx/`. |

Every stage is safe to rerun. To run only some: `ops/provision.sh <ip> serve nginx`. Logs are in
`/srv/logs/bootstrap/<stage>.log` with start and end times in `timeline.log`. `/srv/build` and
`/srv/logs` are on the spot OS disk and are lost if the instance is reclaimed or destroyed.
A warm model volume skips the download (`fetch-model.sh` fast path), so a rerun takes a few minutes
plus the build.

## Use and test

The API is `http://ambermist-h200:8080/v1` on the tailnet (full name
`http://ambermist-h200.<tailnet>.ts.net:8080/v1`, printed by `provision.sh`) with
`Authorization: Bearer $AMB_API_KEY`. The name survives rebuilds; the node's 100.x address doesn't.
Tailscale encrypts the traffic. Public 8080 is closed. The lab gateway gets 8080 only (not SSH); the
tailnet policy decides who connects. Lab clients use the gateway's address instead
([docs/lab-gateway.md](docs/lab-gateway.md)). From a host that isn't on the tailnet, use an SSH tunnel; it
reaches the same nginx:

```bash
ssh -i "${SSH_KEY:-$HOME/.ssh/verda}" -N -L 8080:127.0.0.1:8080 root@<public_ip> &
python3 ops/accept/t1.py     # T1 against http://127.0.0.1:8080; add --base http://ambermist-h200:8080 from a tailnet host
```

Results go to `~/ambermist-runs/<date>/t1-<time>.json`.

Logs: requests in `/srv/logs/nginx/access.json.log` (no bodies), server and per-request token
timings in `/srv/logs/llama/server.log`.

The default log verbosity hides the loader lines (offload count, buffer sizes). To see them, on
the node run `echo 'LLAMA_EXTRA_ARGS="-lv 5"' >> /opt/ambermist/serving.conf && systemctl restart llama-server`,
then read `/srv/logs/llama/server.log`. The next `provision.sh` resets `serving.conf`.

## Tear down

```bash
ssh -i "${SSH_KEY:-$HOME/.ssh/verda}" root@<public_ip> tailscale logout   # first: frees the name ambermist-h200 at once
tofu -chdir=infra/compute destroy       # stops GPU billing; the model volumes stay
python3 ops/fleet-check.py              # expect: no instances, both model volumes detached
python3 ops/fleet-check.py --reap-os    # only when you want detached ambermist-*-os volumes deleted
```

Log out first: the node is an ephemeral device, and logging out should remove it from the tailnet
at once (unverified), so the next node gets the name `ambermist-h200` instead of `ambermist-h200-1`. Destroy at the
end of every session. A destroyed instance can leave a detached
`ambermist-h200-os` volume that keeps billing (about €12/month); `fleet-check.py` lists it.
Copy anything you want to keep from `/srv/logs` first. Never destroy the storage stack; the
`prevent_destroy` on the model volumes is deliberate. To retire one site, first remove its entry
from state, for example `tofu -chdir=infra/storage state rm 'verda_volume.model["FIN-03"]'`, then
delete that volume by hand. Removing it from `locations` alone is blocked by `prevent_destroy`.
The two 140 GiB model volumes cost about €56/month in storage.

## Spot reclaim

The selected site's model volume is `keep_detached`, so its data survives. The OS disk is deleted.
Compute state still holds the reclaimed instance (status `discontinued`), and `apply` would try
to attach the volume to it: run `ops/session.sh clean` first (`up` does it too).
After a reclaim, run `fleet-check.py --pick-site` and relaunch at the first detached site with spot
capacity (`tofu -chdir=infra/compute apply` refreshes and recreates the instance; `is_spot` isn't
updatable in place, so any change to SKU, spot, or the boot script replaces it), then rerun
`ops/provision.sh`. A reclaimed node never logged out, so its device still holds `ambermist-h200`:
remove it in the Tailscale admin console (Machines) before re-provisioning, or the `tailscale`
stage fails. This path has not been exercised yet.

## Not built yet

TLS (the API is plain HTTP inside the tailnet), `make` targets, build and logs volumes, SOPS secrets, and tests T2–T8. Restart-on-crash, the health-check
restart, reboot recovery, log rotation and the 429 cap are configured but have not been exercised.
See the phases in the plan.
