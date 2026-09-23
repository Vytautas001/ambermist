#!/usr/bin/env python3
"""Delete detached project OS volumes left behind by destroyed instances.

Only detached OS volumes whose names belong to the selected project are eligible.
The weights volume is not an OS volume and is therefore never selected.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request


API = os.environ.get("VERDA_API", "https://api.verda.com/v1")


def request(method, path, auth_token, body=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json", "Authorization": f"Bearer {auth_token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{API}{path}", data=data, headers=headers, method=method
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = response.read()
        return json.loads(payload) if payload else None


def get_token():
    client_id = os.environ.get("VERDA_CLIENT_ID")
    client_secret = os.environ.get("VERDA_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("VERDA_CLIENT_ID/VERDA_CLIENT_SECRET not set")
    payload = request(
        "POST",
        "/oauth2/token",
        "",
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    access_token = payload.get("access_token") if isinstance(payload, dict) else None
    if not access_token:
        raise RuntimeError("oauth2/token response had no access_token")
    return access_token


def candidates(volumes, project):
    prefix = f"{project}-"
    return [
        volume
        for volume in volumes
        if volume.get("is_os_volume") is True
        and volume.get("status") == "detached"
        and isinstance(volume.get("name"), str)
        and volume["name"].startswith(prefix)
        and volume["name"].endswith("-os")
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="redcell")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="delete matching detached OS volumes after listing them",
    )
    args = parser.parse_args()

    try:
        auth_token = get_token()
        volumes = request("GET", "/volumes", auth_token) or []
        matches = candidates(volumes, args.project)
        if not matches:
            print(f"No detached {args.project}-* OS volumes found.")
            return 0

        print("--- detached project OS volumes ---")
        for volume in matches:
            print(
                f"{volume.get('id')}  {volume.get('name')}  "
                f"{volume.get('size')} GB  {volume.get('location')}"
            )

        if not args.yes:
            print("Dry run: pass --yes to delete these volumes.")
            return 0

        for volume in matches:
            volume_id = volume["id"]
            request("DELETE", f"/volumes/{volume_id}", auth_token)
            print(f"deleted {volume_id} ({volume.get('name')})")
        return 0
    except (RuntimeError, urllib.error.URLError, urllib.error.HTTPError, KeyError) as exc:
        print(f"ERROR: orphan cleanup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
