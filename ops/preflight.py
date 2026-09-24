#!/usr/bin/env python3
"""
Capacity preflight for the Verda exercise infrastructure.

The deploy console showed B200, B300, GB300, L40S, A100-40GB and RTX A6000 as
"No availability". Verda checks capacity at deploy time and returns HTTP 503
against an exhausted pool, so the only question worth asking before an apply is:
can I actually get the box?

  preflight.py                 check the ladder, print a verdict
  preflight.py --json          machine-readable (CI, or a Terraform external
                               data source)
  preflight.py --watch 900     poll every 15 min, log history, shout on change

/v1/instance-availability now requires an OAuth2 bearer token (it returned
"unauthorized_request" without one as of 2026-09-19, despite older docs calling
it public). Requires VERDA_CLIENT_ID / VERDA_CLIENT_SECRET, same as the
Makefile's orphans/images targets.

Exit codes:  0 design target available | 1 only a fallback | 3 nothing available
             4 API unreachable | 5 credentials missing or rejected
"""
import argparse, datetime, json, os, sys, time, urllib.request, urllib.error

API = os.environ.get("VERDA_API", "https://api.verda.com/v1")

# sku, tensor-parallel size, VRAM GB, EUR/h, note
LADDER = [
    ("2RTXPRO6000.60V",  2, 192, 3.170, "design target, TP=2 over PCIe"),
    ("2H200.141S.88V",   2, 282, 7.456, "faster, NVLink, 2.4x the price"),
    ("4RTXPRO6000.120V", 4, 384, 6.340, "more VRAM, TP=4 PCIe scaling is worse"),
    ("1H200.141S.44V",   1, 141, 3.728, "TP=1 - Int4 or gpt-oss ONLY, FP8 will not fit"),
]

# Anything here is known-unavailable and must never be planned on.
BLOCKED = {"1B200.30V", "1B300.30V", "1GB300.32V", "1L40S.20V", "1A100.40S.22V"}


def get_token(timeout=20):
    client_id = os.environ.get("VERDA_CLIENT_ID")
    client_secret = os.environ.get("VERDA_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("VERDA_CLIENT_ID/VERDA_CLIENT_SECRET not set")
    body = json.dumps({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    req = urllib.request.Request(
        f"{API}/oauth2/token",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.load(r)
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"oauth2/token response had no access_token: {payload}")
    return token


def fetch(timeout=20):
    token = get_token(timeout=timeout)
    req = urllib.request.Request(
        f"{API}/instance-availability",
        headers={
            "Accept": "application/json",
            "User-Agent": "redcell-preflight/1.0",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def parse(raw):
    """Normalise to {location_code: set(instance_types)}."""
    by_loc = {}
    if isinstance(raw, list):
        for e in raw:
            if isinstance(e, str):                      # flat list of SKUs
                by_loc.setdefault("*", set()).add(e)
            elif isinstance(e, dict):
                loc = e.get("location_code") or e.get("location") or "*"
                types = e.get("availabilities") or e.get("instance_types") or []
                by_loc.setdefault(loc, set()).update(types)
    elif isinstance(raw, dict):
        for loc, types in raw.items():
            by_loc.setdefault(loc, set()).update(types or [])
    return by_loc


def evaluate(by_loc):
    rungs = []
    for sku, tp, vram, eur, note in LADDER:
        locs = sorted(l for l, s in by_loc.items() if sku in s)
        rungs.append({"sku": sku, "tp": tp, "vram_gb": vram, "eur_per_hour": eur,
                      "available": bool(locs), "locations": locs, "note": note})
    best = next((r for r in rungs if r["available"]), None)
    return rungs, best


def report(rungs, best, by_loc, as_json, target_sku=None, target_tp=None,
           target_location=None):
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    if target_sku:
        target_locs = sorted(l for l, types in by_loc.items() if target_sku in types)
        if target_location:
            target_locs = [l for l in target_locs if l == target_location]
        known = next((r for r in rungs if r["sku"] == target_sku), None)
        if known and target_tp and known["tp"] != target_tp:
            target_locs = []
        if as_json:
            print(json.dumps({"checked_at": ts, "target": {
                "sku": target_sku, "tp": target_tp,
                "location": target_location, "available": bool(target_locs),
                "locations": target_locs}}, indent=2))
        else:
            where = ",".join(target_locs) or "-"
            mark = "AVAILABLE" if target_locs else "UNAVAILABLE"
            print(f"=== capacity preflight {ts} ===")
            print(f"  -> requested {mark}: {target_sku} TP={target_tp or '-'} [{where}]")
        return 0 if target_locs else 3

    if as_json:
        print(json.dumps({"checked_at": ts, "best": best, "ladder": rungs,
                          "locations_seen": sorted(by_loc)}, indent=2))
    else:
        print(f"=== capacity preflight {ts} ===")
        for r in rungs:
            mark = "AVAILABLE" if r["available"] else "OUT OF STOCK"
            where = ",".join(r["locations"]) or "-"
            sku, vram, eur = r["sku"], r["vram_gb"], r["eur_per_hour"]
            print(f"  {mark:12} {sku:20} {vram:4}GB  EUR {eur:6.3f}/h  [{where}]  {r['note']}")
        blocked_seen = sorted({s for types in by_loc.values() for s in types} & BLOCKED)
        if blocked_seen:
            print(f"\n  NOTE: {', '.join(blocked_seen)} is back in stock. It was")
            print("  unavailable when this was designed - re-check before relying on it.")
        print()
        if best is None:
            print("  *** NO RUNG OF THE LADDER IS AVAILABLE ***")
            print("  Do not sit and retry. Open docs/CAPACITY-RUNBOOK.md.")
        else:
            print(f'  -> apply with: node_sku="{best["sku"]}" node_tp={best["tp"]}')
            if best is not rungs[0]:
                print("  -> THIS IS A FALLBACK RUNG, not the design target.")
                print("     See docs/CAPACITY-RUNBOOK.md for the consequences.")
    return 0 if best is rungs[0] else (1 if best else 3)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--watch", type=int, metavar="SECONDS")
    ap.add_argument("--history", default="preflight-history.log")
    ap.add_argument("--target-sku")
    ap.add_argument("--target-tp", type=int)
    ap.add_argument("--target-location")
    a = ap.parse_args()

    def once():
        try:
            by_loc = parse(fetch())
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            print("Set VERDA_CLIENT_ID/VERDA_CLIENT_SECRET (see .env.example) and re-run.",
                  file=sys.stderr)
            return 5, None, None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            print(f"ERROR: {API}/instance-availability unreachable: {e}", file=sys.stderr)
            return 4, None, None
        rungs, best = evaluate(by_loc)
        return report(rungs, best, by_loc, a.json, a.target_sku, a.target_tp,
                      a.target_location), rungs, best

    if a.watch is None:
        sys.exit(once()[0])

    print(f"watching every {a.watch}s; history -> {a.history} (Ctrl-C to stop)", file=sys.stderr)
    last = None
    while True:
        rc, rungs, best = once()
        state = tuple(r["available"] for r in rungs) if rungs else None
        with open(a.history, "a") as fh:
            fh.write(f"{datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}"
                     f" rc={rc} best={(best or {}).get('sku', 'NONE')} state={state}\n")
        if state != last:
            print(f"\n### CHANGE DETECTED (was {last}, now {state})\n", file=sys.stderr)
            last = state
        time.sleep(a.watch)


if __name__ == "__main__":
    main()
