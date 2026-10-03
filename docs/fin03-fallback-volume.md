# FIN-03 fallback model volume (task for the coding agent)

Written 2026-10-03. Nothing here has been built. The two results under "Checked" come from
read-only `tofu plan` runs against the real account in a scratch directory; nothing was applied.

Read [AGENTS.md](../AGENTS.md) and [LESSONS.md](../LESSONS.md) first. **This document wins
wherever it differs from [inference-tier-plan.md](inference-tier-plan.md).**

## Goal

Keep a second copy of the weights in FIN-03, so the H200 can launch there when FIN-02 has no
spot H200. `ops/fleet-check.py --pick-site` picks the site for each launch: FIN-02 first,
FIN-03 as the fallback. The FIN-02 volume and its data stay as they are.

## Where things stand

- One volume: `ambermist-model`, 140 GiB NVMe, FIN-02, ID
  `6bf9ab4e-aaa1-4190-bd81-278a9768668a`, `detached`, holding the verified
  `qwen38-uncensored-q4km` shards. It is `verda_volume.model` in
  `infra/storage/terraform.tfstate`; the local `infra/storage/terraform.tfvars` says
  `location = "FIN-02"`. `.env` sets `TF_VAR_model_volume_size_gib=140`.
- The compute stack has no location of its own. It reads `location` and `model_volume_id` from
  the storage outputs, so the instance can only go where the one volume is.
- Availability on 2026-10-03: FIN-02 no H200 at all, FIN-03 on-demand only. On 2026-09-26
  FIN-02 also ran out while FIN-03 still had spot.
- `node/` needs no change: the `disks` stage mounts by label `amb-model`, and a clone carries the
  filesystem, the label, and the `.verified` marker, so `fetch-model.sh` takes its fast path.
- The operator approved the second volume on 2026-10-03: 140 GiB at €0.20/GiB-month, about
  €28/month.

**Checked** (2026-10-03, read-only plans, nothing applied):

1. **Import drops two attributes.** `verda_volume` supports import, but the imported object has
   no `location` and no `on_spot_discontinue`. Both are ForceNew, so the plan is
   destroy-and-recreate, which `prevent_destroy` blocks. With
   `ignore_changes = [location, on_spot_discontinue]`, the same import plans
   `1 to import, 0 to add, 0 to change, 0 to destroy`.
2. **The `for_each` move is clean.** With the step 1 config and a `moved` block, a plan against
   a copy of the real state moves the FIN-02 volume to `verda_volume.model["FIN-02"]` with
   `0 to add, 0 to change, 0 to destroy`. Only the outputs change.

**From Verda's API docs, not tried:**

- `PUT /v1/volumes` with `{"action": "clone", "id", "location_code", "name", "type"}` clones
  across locations. The source must be detached. The destination shows `cloning`, then
  `detached`. A `cancel` action with the destination ID stops it. Duration and any transfer
  charge are unknown.
- The volume object has no `on_spot_discontinue` field, the create and clone bodies don't list
  one, and no action changes it later. So whether `keep_detached` protects a volume on a spot
  reclaim is unverified, for the existing FIN-02 volume as much as for the clone. Record this;
  don't try to solve it here.

## Scope

Build only these:

| File | Change |
| :-- | :-- |
| `infra/storage/variables.tf` | `location` becomes `locations` (step 1). |
| `infra/storage/main.tf` | `for_each`, `ignore_changes`, `moved` (step 1). |
| `infra/storage/outputs.tf` | `model_volume_ids` map, replacing `location` and `model_volume_id`. |
| `infra/storage/terraform.tfvars.example` | `locations = ["FIN-02", "FIN-03"]`, edited by hand. |
| `infra/compute/variables.tf`, `infra/compute/main.tf` | A `location` variable; the volume ID is looked up by it (step 2). |
| `infra/compute/terraform.tfvars.example` | New: `location = "FIN-02"`, written by `--pick-site`. |
| `ops/fleet-check.py` | `--pick-site` chooses among sites that hold a model volume (step 3). |
| `README.md`, `LESSONS.md` | Step 8. |

Don't build these: automatic failover or relaunch after a reclaim; switching to on-demand on
your own; a `--clone-to` flag or any committed clone script (the clone is a one-off); ICE-01;
build or logs volumes; keeping the two volumes in step when the model pin changes (each site
downloads on its next launch there); changes under `node/`; deleting, resizing, or renaming the
FIN-02 volume.

## Steps

1. **Storage stack.** Replace `location` in `variables.tf`:

   ```hcl
   variable "locations" {
     type = set(string)
     validation {
       condition     = length(var.locations) > 0 && alltrue([for l in var.locations : contains(["FIN-01", "FIN-02", "FIN-03"], l)])
       error_message = "locations must list FIN-01, FIN-02 or FIN-03."
     }
   }
   ```

   `main.tf`:

   ```hcl
   resource "verda_volume" "model" {
     for_each            = var.locations
     name                = "ambermist-model"
     size                = var.model_volume_size_gib # ForceNew
     type                = "NVMe"                    # block device; NVMe_Shared is NFS
     location            = each.key                  # ForceNew
     on_spot_discontinue = "keep_detached"           # a spot reclaim must not take the weights

     lifecycle {
       prevent_destroy = true
       # Import leaves both null and both are ForceNew; neither can change in place anyway.
       ignore_changes = [location, on_spot_discontinue]
     }
   }

   moved {
     from = verda_volume.model
     to   = verda_volume.model["FIN-02"]
   }
   ```

   `outputs.tf`: only

   ```hcl
   output "model_volume_ids" {
     value = { for site, v in verda_volume.model : site => v.id }
   }
   ```

   Key the map by `each.key`, never `v.location`: that is null on an imported volume.

   Set the local `infra/storage/terraform.tfvars` to `locations = ["FIN-02"]` (FIN-03 comes in
   step 5). `tofu -chdir=infra/storage plan` must match Checked 2: the move, `0 to add,
   0 to change, 0 to destroy`, output changes only. Anything else: stop. Then apply. The compute
   stack reads the old outputs until step 2 is done, so don't run it in between.

2. **Compute stack.** Add to `variables.tf`:

   ```hcl
   variable "location" {
     type        = string
     description = "Site of the model volume to use. ops/fleet-check.py --pick-site writes it to terraform.tfvars."
     validation {
       condition     = contains(["FIN-01", "FIN-02", "FIN-03"], var.location)
       error_message = "location must be FIN-01, FIN-02 or FIN-03."
     }
   }
   ```

   In `main.tf`, drop `local.location`, set `location = var.location` on the instance, and use
   `model_volume_id = data.terraform_remote_state.storage.outputs.model_volume_ids[var.location]`.
   A site with no volume fails at plan with "Invalid index". That is the guard; add nothing else.

   `location` is ForceNew: changing it while an instance exists replaces the instance. Precedence
   is `-var` over `terraform.tfvars` over `TF_VAR_location`, so don't put it in `.env`.

3. **`fleet-check.py --pick-site`.**
   - `TFVARS` becomes `infra/compute/terraform.tfvars`.
   - If `ambermist-h200` exists, print `site: <its location> (ambermist-h200 is running there;
     nothing changed)` and write nothing. This keeps a pick from replacing a running node.
   - Candidate sites: the locations of volumes named exactly `ambermist-model` with status
     `detached`. Volumes in any other status (for example `cloning`) are listed but not candidates.
   - No candidates: say so and exit 2.
   - Otherwise take the first `SITE_ORDER` site that is a candidate and has a spot H200, write
     `location = "<site>"`, and print `site: <site> (written to infra/compute/terraform.tfvars)`,
     adding `fallback` when it isn't FIN-02.
   - No candidate has a spot H200: print on-demand availability at the candidate sites and
     exit 2. The operator decides on `use_spot=false`.
   - Drop the old branch that wrote `infra/storage/terraform.tfvars` for a first volume;
     `locations` is edited by hand. Update the docstring. Exit codes keep their meanings.

4. **Clone FIN-02 to FIN-03** (one-off, not committed). First, `python3 ops/fleet-check.py`
   must show no instances and `ambermist-model` in FIN-02 as `detached`. Launch nothing until
   the clone is `detached`.

   ```bash
   set -a; source .env; set +a
   python3 - <<'PY'
   import importlib, sys, time
   sys.path.insert(0, "ops")
   fc = importlib.import_module("fleet-check")   # reuse its token and request helpers
   SRC = "6bf9ab4e-aaa1-4190-bd81-278a9768668a"  # ambermist-model, FIN-02
   res = fc.request("PUT", "/volumes", fc.get_token(), {
       "action": "clone", "id": SRC, "location_code": "FIN-03", "type": "NVMe", "name": "ambermist-model"})
   print("clone response:", res, flush=True)
   dst = res["id"] if isinstance(res, dict) else res
   t0 = time.time()
   while True:
       v = fc.request("GET", f"/volumes/{dst}", fc.get_token())
       print(f"{int(time.time() - t0)}s {v.get('status')}", flush=True)
       if v.get("status") == "detached" or time.time() - t0 > 3 * 3600:
           break
       time.sleep(60)
   PY
   ```

   If the response is neither an ID string nor `{"id": ...}`, stop and report it. Record the
   destination ID, start and end time, and every status seen.

5. **Bring the clone under OpenTofu.** Set the local `infra/storage/terraform.tfvars` to
   `locations = ["FIN-02", "FIN-03"]` and add a temporary `infra/storage/import.tf`:

   ```hcl
   import {
     to = verda_volume.model["FIN-03"]
     id = "<destination ID from step 4>"
   }
   ```

   The plan must be `1 to import, 0 to add, 0 to change, 0 to destroy`. **If it plans an add for
   FIN-03, the import block is missing or wrong; applying would create a second, blank FIN-03
   volume.** Apply, delete `import.tf`, and plan again: no changes, both IDs in `model_volume_ids`.

6. **Check without GPU time:**
   - `python3 ops/fleet-check.py` lists two `ambermist-model` volumes, FIN-02 and FIN-03, both
     140 GiB and `detached`.
   - `--pick-site` writes the site that the availability it prints implies.
   - `tofu -chdir=infra/compute plan -var location=FIN-03`: instance `location = "FIN-03"`,
     attachment `volume_id` = the FIN-03 ID. Same check with FIN-02. Plan only.
   - `tofu -chdir=infra/compute plan -var location=FIN-01` fails with "Invalid index".

7. **FIN-03 test launch, only with the operator's OK for this run.** It costs about 2.3/h on
   spot or 4.6/h on-demand. Put `location = "FIN-03"` in `infra/compute/terraform.tfvars`
   (`--pick-site` writes it when FIN-02 has no spot H200; otherwise write it by hand), then:

   ```bash
   tofu -chdir=infra/compute apply           # -var use_spot=false only if the operator approved on-demand
   tofu -chdir=infra/compute apply -refresh-only
   ip=$(tofu -chdir=infra/compute output -raw public_ip)
   ops/provision.sh "$ip" packages tailscale disks
   ssh -i "$SSH_KEY" root@"$ip" 'FULL_VERIFY=1 /opt/ambermist/bootstrap.sh model'   # hash every cloned shard once
   ops/provision.sh "$ip" build t0 serve nginx
   ```

   Then from a tailnet host: `/health` 200 and `ops/accept/t1.py` against
   `http://ambermist-h200:8080/v1`. Tear down as in README.md (`tailscale logout`, `destroy`,
   `fleet-check.py` for an orphaned OS volume). Without the operator's OK, skip this step and
   report that no node has served from FIN-03 yet.

8. **Record and document.**
   - LESSONS.md, dated and marked: Checked 1 and 2 (*verified*); the clone's response shape,
     duration, statuses, whether the duplicate name was accepted, and any charge on the billing
     page (*verified*); that the volume API has no `on_spot_discontinue` (*from the docs*,
     behaviour unverified); if step 7 ran, the `FULL_VERIFY` time, provision times, and the T0
     and T1 results.
   - README.md: one `ambermist-model` per site in `locations` (FIN-02 primary, FIN-03 fallback);
     `infra/storage/terraform.tfvars` holds `locations`, edited by hand; the compute stack takes
     `location` from `infra/compute/terraform.tfvars`, written by `--pick-site`, or `-var`.
     Replace "the site can't change while the volume holds the weights" with the fallback rule.
     Never change `location` while an instance exists. To retire a site: `tofu state rm` that
     entry, then delete the volume by hand; removing it from `locations` alone is blocked by
     `prevent_destroy`. Storage now costs about €56/month.

## Stop and report; don't work around

- Any storage plan with a destroy, a replace, or an add you didn't expect.
- An instance exists, or the FIN-02 volume isn't `detached`, when you are about to clone.
- The clone is refused, shows a status other than `cloning` or `detached`, or runs past 3 h.
  Don't retry under another name, and don't `cancel` without the operator.
- Another H200 exists.
- In step 7, `FULL_VERIFY` prints anything other than `model: have …` for a shard: the clone
  wasn't intact. Let it finish (it re-downloads that shard) and report it.

## Done when

- Storage state holds `verda_volume.model["FIN-02"]` (ID unchanged) and `["FIN-03"]`, and a plan
  shows no changes.
- `--pick-site` picks FIN-02 when it has a spot H200 and falls back to FIN-03 otherwise; compute
  plans land in the chosen site with that site's volume.
- Step 7 either ran with T0 and T1 passing in FIN-03, or the report says it didn't.
- README.md and LESSONS.md are updated as in step 8.
