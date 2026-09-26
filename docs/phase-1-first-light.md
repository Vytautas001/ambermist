# Phase 1: first light (task for the coding agent)

Written 2026-09-26. Nothing here has been built or run.

Read [AGENTS.md](../AGENTS.md) and [LESSONS.md](../LESSONS.md) first. The full design is in
[inference-tier-plan.md](inference-tier-plan.md) ("the plan"); read only the sections this
document points to. **For Phase 1, this document wins wherever the two differ.**

## Goal

Serve `unsloth/Qwen3.8-Flash-Next-GGUF` UD-Q4_K_XL with llama.cpp at the pinned commit on
one Verda H200, on loopback only, and pass T0 and T1 through an SSH tunnel. Then write the
measured facts into LESSONS.md and destroy the instance, keeping the model volume.

This is a go/no-go test: the pin has never been compiled and the model has never run.
Write the least code that answers the question. Budget: about 2–3 GPU-hours on spot
(≈ €4–6).

## Before you start

The operator provides:

- **`.env`** with `VERDA_CLIENT_ID`, `VERDA_CLIENT_SECRET`, `HF_TOKEN`, and `AMB_API_KEY`.
  `AMB_API_KEY` is the bearer key that clients send to llama-server
  (`Authorization: Bearer …`); it is `amb-` followed by `openssl rand -hex 32`. Load the
  file with `set -a; source .env; set +a`. Never print these values.

The site is not chosen in advance (Q5). Step 3 picks it from current H200 availability,
until the model volume exists. From then on the site is fixed to the volume's location.
- **Q4** (is an old H200 still running?) is answered by the fleet check in step 3.

## Scope

Build only these:

| File | Purpose |
| :-- | :-- |
| `.gitignore` | Remove `.terraform.lock.hcl` so lock files get committed. Add `infra/*/terraform.tfvars`. |
| `infra/storage/`: `versions.tf`, `main.tf`, `variables.tf`, `outputs.tf`, `terraform.tfvars.example` | The model volume (step 4). |
| `infra/compute/`: the same five files plus `boot.sh.tftpl` | Instance, SSH keys, firewall startup script, volume attachment (step 5). |
| `node/pins/llama.cpp.conf`, `node/pins/model.conf`, `node/pins/qwen38-ud-q4kxl.sha256`, `node/serving.conf` | Pins and settings (step 2). |
| `node/bootstrap.sh` | Stages `packages`, `disks`, `model`, `build`, `t0`, `serve` (step 7). |
| `node/bin/fetch-model.sh`, `node/bin/build-llama.sh` | Plan §5 steps 19 and 20. |
| `ops/fleet-check.py` | Read-only account check, site pick, and an opt-in OS-volume reaper (step 3). |
| `ops/provision.sh` | Copies `node/`, pushes secrets, runs the stages (step 6). |
| `ops/accept/t1.py` | T1 (step 8). Python standard library only. |

Don't build these yet (the phase that adds each is in brackets):

- build and logs volumes, `keep_*` flags, `teardown` variable, `model_guard`, `plan-guard.sh` [4]
- Makefile, `audit.py`, `wait-ip.sh`, `logs-pull.sh`, `preflight.sh` [4]
- SOPS/age and `secrets.sh` (neither tool is installed) [later]
- public API on port 8080 [2]
- nginx, the llama-server systemd unit, healthcheck, logrotate, Prometheus, node_exporter [3]
- T2–T8 [2–5]; Tailscale [6]

No tests for code that doesn't exist, and no helpers "for later".

## How Phase 1 differs from the plan

| Item | Phase 1 | Plan (later phases) |
| :-- | :-- | :-- |
| Volumes | One: `ambermist-model`, 128 GiB NVMe, `prevent_destroy = true`. `/srv/build` and `/srv/logs` are plain directories on the OS disk, at the same paths, so later phases don't change the scripts. | Three volumes with `keep_*` flags (§3.2) |
| Capacity | Spot: `use_spot = true` | On-demand for consumer sessions |
| Secrets | From `.env`, pushed over SSH stdin | SOPS+age (§2.3) |
| llama-server | Transient unit (`systemd-run`) on `127.0.0.1:8080` | systemd unit (§6.2) behind nginx |
| Entry point | The commands in this document and `ops/provision.sh` | `make` targets (§7) |

## Spot rules

1. The model volume has `on_spot_discontinue = "keep_detached"`. Without it, a reclaim can
   delete the 111 GB download. This is the one setting that must not be missed.
2. The spot OS volume has `on_spot_discontinue = "delete_permanently"` (on-demand: `null`;
   the provider rejects the policy there). A reclaim loses `/srv/build` and `/srv/logs`;
   rerunning `provision.sh` rebuilds in about 10–20 min.
3. Downloads resume. `fetch-model.sh` writes `.part` files on the model volume and uses
   `curl --continue-at -`. Keep it that way.
4. Spot or on-demand is fixed when the instance is created. `is_spot` isn't ForceNew in the
   provider, so changing it plans an update that fails at apply. Keep
   `terraform_data.identity` with `replace_triggered_by` (plan §4.4), so a change always
   means a new instance.
5. After a reclaim, state still lists the instance. Run `tofu -chdir=infra/compute apply`,
   which refreshes first. How the provider handles a vanished instance is unverified; if it
   errors, `tofu state rm` the instance and the attachment, and record what happened.
6. If no spot H200 is available at the site, ask the operator before switching to
   on-demand (`use_spot = false`). Never run a second H200.
7. Check the spot price at apply time. €1.864/h (half of on-demand) comes from the
   archived catalog.

## Steps

1. **`.gitignore`**: as in the scope table.

2. **Pins and settings.** Create these files with exactly this content.

   `node/pins/llama.cpp.conf`
   ```
   LLAMA_SHA=e9f824d8c0f011662a742c9d15d4aa18a41e32c0
   QWEN4EXP_MERGE=6c84c7d5d8833c6e0df69628f75a0f599797934e
   BUILD_IMAGE=nvidia/cuda:12.8.1-devel-ubuntu24.04@sha256:4b9ed5fa8361736996499f64ecebf25d4ec37ff56e4d11323ccde10aa36e0c43
   CUDA_ARCH=90
   ```

   `node/pins/model.conf`
   ```
   MODEL_ID=qwen38-ud-q4kxl
   HF_REPO=unsloth/Qwen3.8-Flash-Next-GGUF
   HF_REVISION=38bb39ee97821de2c9009abb7e93950eec396e66
   ENTRY_FILE=Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf
   ```

   `node/pins/qwen38-ud-q4kxl.sha256`: `<sha256>  <bytes>  <repo path>`, copied from
   `archive/attempt-1:deploy/models/qwen38-ud-q4kxl.yaml`. Total 111,334,654,784 bytes.
   ```
   4448186216b3af4cc558bbce2c3213f01608f8f8b2e5267a9767971dd3ec8082  10946624  UD-Q4_K_XL/Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf
   3f342f1c1580473f1ee94ddd5b28206e8c07a70fa1a366f59d1d6c922919a6c9  49859583136  UD-Q4_K_XL/Qwen3.8-Flash-Next-UD-Q4_K_XL-00002-of-00004.gguf
   56758f40269cad5cd9b0d3d6fbae0f40f6d5be6de49e4ab392dbe83157d9cbd3  49376141504  UD-Q4_K_XL/Qwen3.8-Flash-Next-UD-Q4_K_XL-00003-of-00004.gguf
   753bda48b98ba4f1636134a90a967de1b2d3908a236c026e464777342e53510a  12087983520  UD-Q4_K_XL/Qwen3.8-Flash-Next-UD-Q4_K_XL-00004-of-00004.gguf
   ```

   `node/serving.conf`
   ```
   MODEL_PATH=/srv/models/qwen38-ud-q4kxl/Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf
   LLAMA_ALIAS=reasoner
   LLAMA_CTX=65536
   LLAMA_SLOTS=4
   LLAMA_HOST=127.0.0.1
   LLAMA_PORT=8080
   ```

3. **Fleet check.** Write `ops/fleet-check.py` (standard library only). Take the API
   client from `archive/attempt-1:ops/cleanup_orphans.py`: base
   `https://api.verda.com/v1`, OAuth2 client credentials. It must:
   - list instances and volumes, one line each: id, name, type, status, location, size.
     Never print credentials or tokens.
   - exit 1 if any H200 instance other than `ambermist-h200` exists, whatever its status
     (fleet cap). Report `redcell-*` resources; never touch them.
   - flag detached volumes named `ambermist-*-os`: they are orphans at €12/month each.
     `--reap-os` deletes those volumes and nothing else, after listing them. Run it only
     when the operator says so.
   - print whether `1H200.141S.44V` is available at each site, spot and on-demand
     (`GET /instance-availability`; the spot filter parameter is `[VERIFY]`). "Available"
     is not a reservation.
   - pick the site with `--pick-site`:
     - If a volume named `ambermist-model` exists, the site is that volume's location. Print
       it and change nothing. The site is fixed once this volume exists.
     - Otherwise, pick the first site with a spot H200 available, in the order `FIN-02`,
       `FIN-01`, `FIN-03`. FIN-02 comes first because it hosted the only recorded H200 run.
       Write `location = "<site>"` to `infra/storage/terraform.tfvars`.
     - If no site has a spot H200, list the sites with an on-demand H200 and exit non-zero.
       The operator decides (spot rule 6).

   Run it. **If another H200 exists, stop and ask the operator (Q4).** Also check that the
   workstation's public IPv4 is inside `admin_cidrs`; otherwise the firewall locks you out.

4. **Storage stack** (`infra/storage/`). Start from the plan §4.4 sketch, reduced to one
   resource, `verda_volume.model`:
   - name `ambermist-model`, size 128, type `NVMe` (a block device; `NVMe_Shared` is NFS),
     `location = var.location`, `on_spot_discontinue = "keep_detached"`, and
     `lifecycle { prevent_destroy = true }`.
   - outputs `model_volume_id` and `location`.

   `var.location` has no default; it comes from the `terraform.tfvars` that step 3 wrote.
   Location is ForceNew, so if it ever changes, the plan shows a replacement and
   `prevent_destroy` stops the apply. That is the intended guard.

   Use provider `verda-cloud/verda ~> 1.1` and OpenTofu ≥ 1.8 (1.12.6 is installed), with
   local state. Credentials come from the environment, never from HCL. No model or
   llama.cpp names go in `.tf` files. Run `tofu plan`, show the operator, then apply.

   The first time, apply step 5 right after this one: availability can change within
   minutes. If the compute apply can't get an H200 at the chosen site, retry for up to
   30 min. If it still fails and the volume holds no weights yet, ask the operator whether
   to delete the empty volume and pick again. Deleting it means lifting `prevent_destroy`,
   so it is the operator's call.

5. **Compute stack** (`infra/compute/`). Start from the plan §4.4 `compute/main.tf`, with
   these changes:
   - read `model_volume_id` and `location` from the storage state;
   - use one `verda_volume_attachment.model`;
   - `terraform_data.identity` input: SKU, spot, and `sha256` of the boot script.

   Variables as in plan §4.3: `owner`; `instance_type` with the fleet-cap validation;
   `image` (`24.04.cuda12.9.docker`, `[VERIFY]` the slug); `use_spot`, default **`true`**;
   `os_volume_size_gib` 60 (`[VERIFY]` Verda's minimum); `ssh_public_keys`; and
   `admin_cidrs`, with its validation and the operator's value from §4.3.

   `boot.sh.tftpl` does four things and holds no secrets:
   1. installs `nftables` if `nft` is missing;
   2. writes the plan §2.3 ruleset to `/etc/nftables.conf`, with `admin_cidrs` joined by
      `, `;
   3. loads the ruleset;
   4. runs `systemctl enable nftables`.

   Escape shell `${…}` as `$${…}` in the template.

   Outputs: `instance_id`, `ssh`, a line saying the GPU is billing, and `public_ip`
   (`verda_instance.node.ip`). `public_ip` can be `null` right after create; if it is, run
   `tofu apply -refresh-only` until it's set.

   Tell the operator before you apply: billing starts then.

6. **Provisioning** (`ops/provision.sh <ip>`):
   - Log in as `root`, per LESSONS.md. The archived outputs used `ubuntu`, so `[VERIFY]` on
     the first login. Use `-o StrictHostKeyChecking=accept-new`.
   - Copy `node/` to `/opt/ambermist` with `tar | ssh` (plan step 15).
   - Create the `llama` system user. Push secrets **on stdin only**:
     - `AMB_API_KEY` goes to `/etc/ambermist/llama-api-keys`, `root:llama`, mode 0640;
     - `Authorization: Bearer $HF_TOKEN` goes to `/run/ambermist/hf-auth-header`, `root`,
       mode 0600.

     Never pass secrets through argv, tfvars, state, or the startup script.
   - Run the stages in this order:
     1. `packages`
     2. `disks`
     3. `model` and `build` in parallel; both must succeed
     4. `t0`
     5. `serve`

     Each stage logs to `/srv/logs/bootstrap/<stage>.log` and appends its start and end
     times to `/srv/logs/bootstrap/timeline.log`. Every stage must be safe to rerun.

7. **Stages** (`node/bootstrap.sh <stage>`):
   - `packages`:
     - run `apt-get install -y --no-install-recommends nftables jq python3-venv libgomp1 git rsync`;
     - print `nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv` (expect
       one H200 and driver ≥ 570);
     - print `free -g`.
   - `disks`: plan step 16, for the model volume only (label `amb-model`, mounted at
     `/srv/models`).
     - Run `mkfs` only if exactly one disk is blank (no filesystem, no partitions) and
       exactly 128 GiB. Otherwise abort.
     - Run `mkdir -p /srv/build /srv/logs`.
     - `[VERIFY]` the device name, and whether `/dev/disk/by-id` shows the volume ID.
   - `model`: `fetch-model.sh`, plan step 19. That script calls `check_sizes` but never
     defines it: it must compare each file's size with the manifest. Shards land flat in
     `/srv/models/qwen38-ud-q4kxl/`.
   - `build`: `build-llama.sh`, plan step 20. If `llama-server --version` doesn't show
     commit `e9f824d`, the build lost its git information. Fix that; don't skip the check.
   - `t0`: plan step 21 (a venv with `gguf-py`, and `--json-array`).
     - `general.architecture` must be `qwen4exp`.
     - `compress_ratios` must contain only 0 and 4. Any other value is the symptom of the
       known bad upload: stop.
     - Record how many values are 4. 12 is expected; this is unverified.
   - `serve`:
     - Stop any existing `llama-server` unit.
     - Start llama-server with `systemd-run --unit=llama-server`: user `llama`,
       `LD_LIBRARY_PATH=/srv/build/current/lib`, `LimitMEMLOCK=infinity`, stdout and stderr
       appended to `/srv/logs/llama/server.log`.
     - Use the plan §6.1 flags exactly as written, but take `--host`, `--port`, and the
       other values from `serving.conf`.
     - Poll `http://127.0.0.1:8080/health` until it returns 200 (timeout 20 min).

8. **T0 and T1.**
   - **T0 (on the node):** the `t0` stage results, plus these facts from the loader log:
     - arch `qwen4exp`;
     - 4 slots of 16,384 tokens each;
     - all layers on the GPU;
     - no automatic fit or context reduction (any reduction is a failure);
     - the CUDA model, KV, and compute buffer sizes, plus `nvidia-smi` `memory.used`.

     The first time, read the log yourself and record these values. Automate the checks
     only after you've seen the real log wording.
   - **T1** (`ops/accept/t1.py`): run it from the workstation through
     `ssh -N -L 8080:127.0.0.1:8080 root@<ip>`. It covers all of plan §8 T1:
     - 401 without a key;
     - `/health` returns 200;
     - `reasoner` is listed in `/v1/models`;
     - 6×7;
     - the §2.2 tool call, non-streaming and streaming;
     - the second turn.

     Run each 5 times. Read the key from `AMB_API_KEY` and never print it. Write results
     as JSON under `~/ambermist-runs/<date>/`. For reference, see
     `archive/attempt-1:ops/check_inference.py` and
     `archive/attempt-1:evals/check_stub_tool.py`.

9. **Record** in LESSONS.md, marked *verified* and dated:
   - the site; spot or on-demand; the image slug; the login user;
   - the driver version and host RAM;
   - how the volume appeared in the guest;
   - download, build, and load times;
   - VRAM use (loader buffers and `nvidia-smi`);
   - the `compress_ratios` count;
   - T0 and T1 results, including whether `reasoning_content` appears and whether
     `content` is `null` or `""`;
   - any reclaim and how recovery went;
   - GPU-hours used.

   Replace each `[VERIFY]` you resolved.

10. **Tear down.** Run `tofu -chdir=infra/compute destroy`. The model volume stays,
    because it's in the other stack. Then run `ops/fleet-check.py`: the model volume
    should be detached, and any detached `ambermist-h200-os` volume is reported to the
    operator for `--reap-os`. Destroy the compute stack at the end of every working
    session unless the operator says otherwise. Never leave the GPU billing unattended.

## Stop and report; don't work around

- Another H200 exists in the account.
- The build fails at the pin. Don't change the pin: a pin bump is the operator's call
  (plan §7.8).
- Loading runs out of memory. You may try the fallback profile once
  (`--override-tensor 'per_layer_token_embd=CPU'`, plan §6.1; it needs ≥ 80 GiB of free
  host RAM). Record it as a separate profile. If that fails too, stop.
- The loader reduces context or moves layers off the GPU on its own.
- Tool calls come back as text in `content` instead of `tool_calls`. Don't write a parser
  or a template override.
- `compress_ratios` contains values other than 0 and 4.

## Done when

- T0 and T1 pass (T1's pass criteria are in plan §8), and their output is saved.
- LESSONS.md has the facts from step 9.
- The compute stack is destroyed, the model volume is detached and intact, and
  `fleet-check.py` reports no orphans, or the operator has been told about them.
