#!/usr/bin/env python3
"""Read-only Verda account check: fleet cap, orphans, H200 availability, site pick.

Credentials come from VERDA_CLIENT_ID / VERDA_CLIENT_SECRET (never printed).

  fleet-check.py               list everything; exit 1 if a foreign H200 exists
  fleet-check.py --pick-site   also choose the site (see below)
  fleet-check.py --reap-os     also delete detached ambermist-*-os volumes

--pick-site: if the volume ambermist-model exists, its location is the site.
Otherwise the first of FIN-02, FIN-01, FIN-03 with a spot H200 is written to
infra/storage/terraform.tfvars. If no site has a spot H200, exit 2.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("VERDA_API", "https://api.verda.com/v1")
SKU = "1H200.141S.44V"
OWN_INSTANCE = "ambermist-h200"
MODEL_VOLUME = "ambermist-model"
SITE_ORDER = ["FIN-02", "FIN-01", "FIN-03"]
TFVARS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "infra", "storage", "terraform.tfvars")


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


def availability(token, spot):
    path = "/instance-availability" + ("?is_spot=true" if spot else "")
    return {row["location_code"]: SKU in row.get("availabilities", []) for row in request("GET", path, token) or []}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pick-site", action="store_true")
    ap.add_argument("--reap-os", action="store_true")
    args = ap.parse_args()

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
    foreign = [i for i in instances if "H200" in str(i.get("instance_type")) and i.get("hostname") != OWN_INSTANCE]
    if foreign:
        print("FLEET CAP: another H200 exists: " + ", ".join(f"{i.get('hostname')} ({i.get('status')})" for i in foreign))
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

    spot = availability(token, True)
    ondemand = availability(token, False)
    print(f"--- {SKU} availability (not a reservation) ---")
    for site in sorted(set(spot) | set(ondemand)):
        print(f"{site}  spot={spot.get(site, False)}  on-demand={ondemand.get(site, False)}")

    if args.pick_site:
        model = [v for v in volumes if v.get("name") == MODEL_VOLUME]
        if model:
            print(f"site: {model[0].get('location')} (location of existing {MODEL_VOLUME}; nothing changed)")
        else:
            pick = next((s for s in SITE_ORDER if spot.get(s)), None)
            if pick is None:
                print("no site has a spot H200. On-demand H200 at: "
                      + (", ".join(s for s, ok in sorted(ondemand.items()) if ok) or "none"))
                return 2
            with open(TFVARS, "w") as f:
                f.write(f'location = "{pick}"\n')
            print(f"site: {pick} (written to infra/storage/terraform.tfvars)")
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, urllib.error.URLError, KeyError) as exc:
        print(f"ERROR: fleet check failed: {exc}", file=sys.stderr)
        sys.exit(1)
