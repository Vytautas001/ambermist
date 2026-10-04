#!/usr/bin/env python3
"""Verda account check: fleet cap, orphans, H200 availability, site pick, state drift.

Credentials come from VERDA_CLIENT_ID / VERDA_CLIENT_SECRET (never printed).
Read-only unless --clean-state, --reap-os or --delete-model-volumes is given.

  fleet-check.py                         list everything; exit 1 if a foreign H200 exists
  fleet-check.py --pick-site             also choose the site (see below)
  fleet-check.py --reap-os               also delete detached ambermist-*-os volumes
  fleet-check.py --check-state           compare the local state files with the account
  fleet-check.py --clean-state           ... and drop a dead instance from compute state
  fleet-check.py --delete-model-volumes  delete the model volumes in storage state

--instance picks the SKU (default 1H200.141S.44V; only SKUs that fit the fleet
cap of 1 H200 and 2 RTX PRO 6000 GPUs). Exit 1 if the instances already
held, other than ambermist-h200, leave no room for it. --type is spot (default)
or on-demand.

--pick-site: if ambermist-h200 exists, leave the compute selection unchanged
(exit 1 if --site, --instance or --type asks for something else). Otherwise
choose the first detached ambermist-model volume in FIN-02, FIN-01, FIN-03 (or
only at --site) where the SKU is available as that type, and write location,
instance_type and use_spot to infra/compute/terraform.tfvars. If no eligible
volume or site exists, exit 2.

--check-state: after a spot reclaim or a zero balance the API keeps the instance
as "discontinued", so tofu keeps it in state and plans to attach the volume to
it. Exit 3 if compute state holds such an instance; exit 1 if the account has an
ambermist-h200 or ambermist-model the state doesn't know (applying would create
a duplicate). --clean-state removes the dead instance and its attachment with
`tofu state rm`; it never touches storage state.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

API = os.environ.get("VERDA_API", "https://api.verda.com/v1")
DEFAULT_SKU = "1H200.141S.44V"
# SKU -> (GPU family, GPUs). Keep in sync with instance_type in infra/compute/variables.tf.
ALLOWED = {
    "1H200.141S.44V": ("H200", 1),
    "2RTXPRO6000.60V": ("RTXPRO6000", 2),
}
CAP = {"H200": 1, "RTXPRO6000": 2}  # GPUs per family across every held node
OWN_INSTANCE = "ambermist-h200"
MODEL_VOLUME = "ambermist-model"
SITE_ORDER = ["FIN-02", "FIN-01", "FIN-03"]
DEAD = {"discontinued"}  # status the API reports for a reclaimed or zero-balance instance
INFRA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "infra")
TFVARS = os.path.join(INFRA, "compute", "terraform.tfvars")
# --pick-cheapest serves the 27B test tier only and never writes the production tfvars.
TEST_STORAGE_TFVARS = os.path.join(INFRA, "storage-test", "terraform.tfvars")
TEST_COMPUTE_TFVARS = os.path.join(INFRA, "compute-test", "terraform.tfvars")
TEST_IMAGE = "24.04.cuda12.9.docker"
TEST_VOLUME_GIB = 50  # Verda's NVMe block-volume minimum; 32 was rejected as "too low"


def request(method, path, token=None, body=None):
    headers = {"Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = resp.read()
        return json.loads(payload) if payload else None


def get_token():
    cid = os.environ.get("VERDA_CLIENT_ID")
    secret = os.environ.get("VERDA_CLIENT_SECRET")
    if not cid or not secret:
        raise RuntimeError("VERDA_CLIENT_ID/VERDA_CLIENT_SECRET not set")
    payload = request("POST", "/oauth2/token", body={
        "grant_type": "client_credentials", "client_id": cid, "client_secret": secret})
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not token:
        raise RuntimeError("oauth2/token response had no access_token")
    return token


def availability(token, spot, sku):
    path = "/instance-availability" + ("?is_spot=true" if spot else "")
    return {row["location_code"]: sku in row.get("availabilities", []) for row in request("GET", path, token) or []}


def availability_all(token, spot):
    """{location_code: set(instance_type)} -- every SKU on offer, not just one."""
    path = "/instance-availability" + ("?is_spot=true" if spot else "")
    return {row["location_code"]: set(row.get("availabilities") or [])
            for row in request("GET", path, token) or []}


def fitting_types(token, min_vram_gib):
    """Single-GPU SKUs with >= min_vram_gib VRAM that support TEST_IMAGE.

    Catalog prices are USD; the Verda console quotes EUR, which is why the two have
    never matched (see LESSONS.md).
    """
    out = {}
    for r in request("GET", "/instance-types", token) or []:
        if (r.get("gpu") or {}).get("number_of_gpus") != 1:
            continue
        vram = (r.get("gpu_memory") or {}).get("size_in_gigabytes") or 0
        if vram < min_vram_gib:
            continue
        names = [o if isinstance(o, str) else (o.get("name") or o.get("id") or "")
                 for o in (r.get("supported_os") or [])]
        if TEST_IMAGE not in names:
            continue
        out[r["instance_type"]] = {
            "model": r.get("model"), "vram": vram, "currency": r.get("currency"),
            "on_demand": float(r["price_per_hour"]) if r.get("price_per_hour") else None,
            "spot": float(r["spot_price"]) if r.get("spot_price") else None,
        }
    return out


def pick_cheapest(token, min_vram_gib, apply_it, prefer=None, kind=None):
    """Rank every available single-GPU SKU that fits the test model, cheapest first."""
    types = fitting_types(token, min_vram_gib)
    if prefer:
        missing = [p for p in prefer if p not in types]
        types = {k: v for k, v in types.items() if k in prefer}
        if missing:
            print("not in the catalog, or below the VRAM floor: %s" % ", ".join(missing))
    offers = []
    for is_spot in (True, False):
        if kind == "spot" and not is_spot:
            continue
        if kind == "on-demand" and is_spot:
            continue
        for site, skus in availability_all(token, is_spot).items():
            for sku in skus & types.keys():
                price = types[sku]["spot" if is_spot else "on_demand"]
                if price is not None:
                    offers.append((price, sku, site, is_spot))
    offers.sort()
    if not offers:
        print("nothing available matching: >= %d GB VRAM, %s%s%s"
              % (min_vram_gib, TEST_IMAGE,
                 ", sku in {%s}" % ",".join(prefer) if prefer else "",
                 ", %s only" % kind if kind else ""))
        return 2
    print("--- test-tier SKUs (>= %d GB VRAM, %s), cheapest first ---"
          % (min_vram_gib, TEST_IMAGE))
    for price, sku, site, is_spot in offers:
        t = types[sku]
        print("  %8.4f %s/h  %-22s%-14s%4dG  %s  %s"
              % (price, t["currency"], sku, str(t["model"]), t["vram"], site,
                 "spot" if is_spot else "on-demand"))
    price, sku, site, is_spot = offers[0]
    print("cheapest: %s at %s (%s), %s %s/h"
          % (sku, site, "spot" if is_spot else "on-demand", price, types[sku]["currency"]))
    if not apply_it:
        print("  (pass --apply to write the *-test tfvars)")
        return 0
    for path, body in (
        # storage-test takes a single location (one disposable volume, one site);
        # compute-test reads that location back out of storage-test's remote state.
        (TEST_STORAGE_TFVARS,
         'location              = "%s"\nmodel_volume_size_gib = %d\n' % (site, TEST_VOLUME_GIB)),
        (TEST_COMPUTE_TFVARS,
         'instance_type = "%s"\nuse_spot      = %s\n' % (sku, str(is_spot).lower())),
    ):
        if not os.path.isdir(os.path.dirname(path)):
            print("  SKIP %s: stack directory does not exist" % path)
            continue
        with open(path, "w") as f:
            f.write(body)
        print("  wrote %s" % os.path.relpath(path, INFRA + "/.."))
    return 0


def gpus(instance_type, family):
    """GPUs of `family` in an instance type such as 2RTXPRO6000.60V (the leading digits)."""
    m = re.match(r"(\d+)" + family, str(instance_type))
    return int(m.group(1)) if m else 0


def state_ids(stack):
    """Map each managed resource address in infra/<stack>/terraform.tfstate to its ID."""
    try:
        with open(os.path.join(INFRA, stack, "terraform.tfstate")) as f:
            state = json.load(f)
    except FileNotFoundError:
        return {}
    ids = {}
    for res in state.get("resources", []):
        if res.get("mode") != "managed":
            continue
        for inst in res.get("instances", []):
            key = inst.get("index_key")
            addr = f"{res['type']}.{res['name']}" + (f'["{key}"]' if key is not None else "")
            ids[addr] = inst.get("attributes", {}).get("id")
    return ids


def state_rm(stack, addr):
    sys.stdout.flush()  # keep our lines ahead of tofu's when piped
    subprocess.run(["tofu", f"-chdir={os.path.join(INFRA, stack)}", "state", "rm", addr], check=True)


def instance_status(token, iid):
    try:
        return request("GET", f"/instances/{iid}", token).get("status")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def check_state(token, instances, volumes, clean):
    """Returns 1 if the account has resources the state doesn't know, 3 if state is stale, else 0."""
    compute = state_ids("compute")
    iid = compute.get("verda_instance.node")
    stale = []
    if iid:
        status = instance_status(token, iid)
        if status is None or status in DEAD:
            stale = [a for a in ("verda_volume_attachment.model", "verda_instance.node") if a in compute]
            print(f"STALE STATE: verda_instance.node {iid} is {status or 'gone'} (spot reclaim or zero balance)")
        else:
            print(f"state: verda_instance.node {iid} is {status}")
    else:
        print("state: no instance in infra/compute state")

    model = {vid for addr, vid in state_ids("storage").items() if addr.startswith("verda_volume.model[")}
    untracked = [i for i in instances if i.get("hostname") == OWN_INSTANCE and i.get("id") != iid]
    untracked += [v for v in volumes if v.get("name") == MODEL_VOLUME and v.get("id") not in model]
    for x in untracked:
        print(f"UNTRACKED: {x.get('hostname') or x.get('name')} {x.get('id')} ({x.get('status')}) is not in "
              "the local state; another checkout or a lost state file? Applying would create a duplicate.")
    for vid in sorted(model - {v.get("id") for v in volumes}):
        print(f"WARNING: model volume {vid} in infra/storage state is not in the account "
              "(trashed after a zero balance?). Not changed; fix by hand.")

    if untracked:
        return 1
    if stale and clean:
        for addr in stale:
            state_rm("compute", addr)
        print("removed the dead instance from infra/compute state. A reclaimed node never logged out of "
              f"Tailscale: if {OWN_INSTANCE} is still listed there, remove it or the tailscale stage fails.")
        return 0
    return 3 if stale else 0


def delete_model_volumes(token, instances):
    if any(i.get("hostname") == OWN_INSTANCE for i in instances):
        print(f"{OWN_INSTANCE} exists: destroy the compute stack first")
        return 1
    for addr, vid in state_ids("storage").items():
        if not addr.startswith("verda_volume.model["):
            continue
        try:
            request("DELETE", f"/volumes/{vid}", token)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
        state_rm("storage", addr)  # prevent_destroy blocks `tofu destroy`, so delete by API and forget it
        print(f"deleted {vid} ({addr})")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pick-site", action="store_true")
    ap.add_argument("--reap-os", action="store_true")
    ap.add_argument("--check-state", action="store_true")
    ap.add_argument("--clean-state", action="store_true")
    ap.add_argument("--delete-model-volumes", action="store_true")
    ap.add_argument("--site", choices=SITE_ORDER)
    ap.add_argument("--instance", choices=ALLOWED)
    ap.add_argument("--type", choices=["spot", "on-demand"])
    ap.add_argument("--pick-cheapest", action="store_true",
                    help="rank available single-GPU SKUs that fit the 27B test model")
    ap.add_argument("--min-vram", type=int, default=96, metavar="GIB",
                    help="VRAM floor for --pick-cheapest (default 96: 82,235 MiB measured "
                         "at 4 slots x 262144; 80 GB cards do not fit)")
    ap.add_argument("--apply", action="store_true",
                    help="with --pick-cheapest, write the chosen SKU/site to the *-test tfvars")
    ap.add_argument("--prefer", action="append", metavar="SKU",
                    help="restrict --pick-cheapest to these SKUs (repeatable), e.g. "
                         "--prefer 1RTXPRO6000.30V")
    ap.add_argument("--kind", choices=["spot", "on-demand"],
                    help="restrict --pick-cheapest to spot or on-demand only")
    args = ap.parse_args()
    sku = args.instance or DEFAULT_SKU
    kind = args.type or "spot"
    family, want = ALLOWED[sku]

    token = get_token()
    instances = request("GET", "/instances", token) or []
    volumes = request("GET", "/volumes", token) or []

    print("--- instances ---")
    for i in instances:
        print(f"{i.get('id')}  {i.get('hostname')}  {i.get('instance_type')}  {i.get('status')}  "
              f"{i.get('location')}  spot={i.get('is_spot')}")
    print("--- volumes ---")
    for v in volumes:
        print(f"{v.get('id')}  {v.get('name')}  {v.get('type')}  {v.get('status')}  "
              f"{v.get('location')}  {v.get('size')} GiB")

    rc = 0
    held = [i for i in instances if i.get("hostname") != OWN_INSTANCE and gpus(i.get("instance_type"), family)]
    if sum(gpus(i.get("instance_type"), family) for i in held) + want > CAP[family]:
        print(f"FLEET CAP: {sku} would exceed {CAP[family]}x {family}; already held: " + ", ".join(
            f"{i.get('hostname')} ({i.get('instance_type')}, {i.get('status')})" for i in held))
        rc = 1
    red = [x.get("hostname") for x in instances if str(x.get("hostname")).startswith("redcell-")]
    red += [x.get("name") for x in volumes if str(x.get("name")).startswith("redcell-")]
    if red:
        print("redcell-* resources (not touched): " + ", ".join(map(str, red)))

    orphans = [v for v in volumes if v.get("is_os_volume") and v.get("status") == "detached"
               and str(v.get("name")).startswith("ambermist-") and str(v.get("name")).endswith("-os")]
    if orphans:
        print("ORPHAN OS VOLUMES (about EUR 12/month each):")
        for v in orphans:
            print(f"  {v.get('id')}  {v.get('name')}  {v.get('location')}  {v.get('size')} GiB")
        if args.reap_os:
            for v in orphans:
                request("DELETE", f"/volumes/{v['id']}", token)
                print(f"deleted {v['id']} ({v['name']})")
        else:
            print("  (pass --reap-os to delete these)")

    if args.check_state or args.clean_state:
        drift = check_state(token, instances, volumes, args.clean_state)
        rc = rc or drift
    if args.delete_model_volumes:
        rc = delete_model_volumes(token, instances) or rc

    spot = availability(token, True, sku)
    ondemand = availability(token, False, sku)
    print(f"--- {sku} availability (not a reservation) ---")
    for site in sorted(set(spot) | set(ondemand)):
        print(f"{site}  spot={spot.get(site, False)}  on-demand={ondemand.get(site, False)}")

    if args.pick_site:
        own = next((i for i in instances if i.get("hostname") == OWN_INSTANCE), None)
        if own:
            where, what = own.get("location"), own.get("instance_type")
            how = "spot" if own.get("is_spot") else "on-demand"
            if args.site not in (None, where) or args.instance not in (None, what) or args.type not in (None, how):
                print(f"{OWN_INSTANCE} is running at {where} as {how} {what}; destroy the compute stack before "
                      f"switching to {args.site or where} / {args.type or how} {args.instance or what}")
                return 1
            print(f"site: {where} ({OWN_INSTANCE} is running there as {how} {what}; nothing changed)")
        else:
            candidates = {
                v.get("location") for v in volumes
                if v.get("name") == MODEL_VOLUME and v.get("status") == "detached"
            }
            candidates &= {args.site} if args.site else set(SITE_ORDER)
            if not candidates:
                print(f"no detached {MODEL_VOLUME} volume is available at {args.site or 'a candidate site'}")
                return 2

            avail, other, other_kind = (spot, ondemand, "on-demand") if kind == "spot" else (ondemand, spot, "spot")
            pick = next((s for s in SITE_ORDER if s in candidates and avail.get(s)), None)
            if pick is None:
                sites = ", ".join(
                    f"{site}={other.get(site, False)}"
                    for site in SITE_ORDER if site in candidates
                )
                print(f"no candidate site has {kind} {sku}; {other_kind} availability: {sites}")
                return 2

            with open(TFVARS, "w") as f:
                f.write(f'location      = "{pick}"\ninstance_type = "{sku}"\n'
                        f'use_spot      = {"true" if kind == "spot" else "false"}\n')
            fallback = "; fallback" if pick != "FIN-02" and not args.site else ""
            print(f"site: {pick}, {kind} {sku} (written to infra/compute/terraform.tfvars{fallback})")
    if args.pick_cheapest:
        rc = pick_cheapest(token, args.min_vram, args.apply, args.prefer, args.kind) or rc
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, urllib.error.URLError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: fleet check failed: {exc}", file=sys.stderr)
        sys.exit(1)
