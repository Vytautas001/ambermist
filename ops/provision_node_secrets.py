#!/usr/bin/env python3
"""Install inference credentials on every active serving node over SSH.

Credentials are read from the caller's environment and sent only through
SSH stdin. They are never passed in a command argument or Terraform input.
"""

from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
import sys
import time


REMOTE_INSTALL = r'''import json, os, pathlib, sys, time, subprocess
data = json.load(sys.stdin)
mount = pathlib.Path("/mnt/weights")
unit = pathlib.Path(sys.argv[1])
deadline = time.monotonic() + 1200
while time.monotonic() < deadline:
    if mount.is_mount() and unit.is_file():
        break
    time.sleep(5)
else:
    raise SystemExit("weights mount or vLLM unit did not become ready")
target = mount / ".env"
temporary = mount / (".env.tmp." + str(os.getpid()))
content = "HF_TOKEN=" + data["HF_TOKEN"] + "\nVLLM_API_KEY=" + data["VLLM_API_KEY"] + "\n"
changed = not target.is_file() or target.read_text() != content
if changed:
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(temporary, 0o600)
    os.chown(temporary, 0, 0)
    os.replace(temporary, target)
    os.chmod(target, 0o600)
    os.chown(target, 0, 0)
subprocess.run(["systemctl", "daemon-reload"], check=True)
active = subprocess.run(["systemctl", "is-active", "--quiet", sys.argv[2]]).returncode == 0
if changed or not active:
    subprocess.run(["systemctl", "restart", sys.argv[2]], check=True)
'''


def terraform_output(terraform: str, name: str, *, raw: bool = False) -> object:
    args = [terraform, "output", "-raw" if raw else "-json", name]
    try:
        result = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() if raw else json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read Terraform output {name!r}") from exc


def main() -> int:
    terraform = os.environ.get("TF", "tofu")
    ssh_user = os.environ.get("VLLM_SSH_USER", "root")
    ssh_key = os.environ.get("VLLM_SSH_KEY")
    try:
        nodes = terraform_output(terraform, "instances")
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    serving = {
        role: details
        for role, details in nodes.items()
        if details.get("endpoint") and details.get("ip")
    }
    if not serving:
        print("No active serving nodes; credentials were not sent.")
        return 0

    hf_token = os.environ.get("HF_TOKEN", "")
    api_key = os.environ.get("VLLM_API_KEY", "")
    if not hf_token or not api_key:
        print("ERROR: source the repository .env with HF_TOKEN and VLLM_API_KEY first", file=sys.stderr)
        return 2
    try:
        project = terraform_output(terraform, "project", raw=True)
    except RuntimeError as exc:
        # Older state may predate the project output. The provider output
        # already includes each hostname as <project>-<role>, so recover the
        # prefix when all serving nodes agree instead of leaving credentials
        # unsynchronized on an otherwise healthy node.
        inferred_projects = {
            str(details.get("hostname", "")).removesuffix("-" + str(details.get("role", "")))
            for details in serving.values()
        }
        if len(inferred_projects) != 1 or not next(iter(inferred_projects), ""):
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        project = next(iter(inferred_projects))
    unit_path = f"/etc/systemd/system/{project}-vllm.service"
    service_name = f"{project}-vllm"
    encoded_installer = base64.b64encode(REMOTE_INSTALL.encode()).decode("ascii")
    remote_command = (
        "python3 -c 'import base64; exec(base64.b64decode(\""
        + encoded_installer
        + "\"))' "
        + shlex.quote(unit_path)
        + " "
        + shlex.quote(service_name)
    )

    payload = json.dumps({"HF_TOKEN": hf_token, "VLLM_API_KEY": api_key}).encode()
    for role, details in serving.items():
        ip = str(details["ip"])
        target = f"{ssh_user}@{ip}"
        command = [
            "ssh",
            *(["-i", ssh_key] if ssh_key else []),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            "-o",
            "StrictHostKeyChecking=accept-new",
            target,
            remote_command,
        ]
        deadline = time.monotonic() + 1200
        while True:
            try:
                subprocess.run(
                    command,
                    input=payload,
                    stdout=subprocess.DEVNULL,
                    check=True,
                    timeout=1250,
                )
                print(f"Credentials synced on {role} ({ip}); vLLM is ready to use them.")
                break
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                if time.monotonic() >= deadline:
                    print(f"ERROR: could not provision {role} ({ip}) over SSH", file=sys.stderr)
                    return 1
                time.sleep(10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
