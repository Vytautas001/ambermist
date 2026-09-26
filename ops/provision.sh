#!/usr/bin/env bash
# Usage: ops/provision.sh <ip> [stage...]
# Copies node/ to the node, pushes secrets on stdin, and runs the bootstrap stages
# (default: packages disks model+build t0 serve). Safe to rerun.
set -euo pipefail
ip=${1:?usage: provision.sh <ip> [stage...]}
shift
root=$(cd "$(dirname "$0")/.." && pwd)
if [[ -z ${AMB_API_KEY:-} || -z ${HF_TOKEN:-} ]]; then
  set -a; . "$root/.env"; set +a
fi
: "${AMB_API_KEY:?}" "${HF_TOKEN:?}"

SSH=(ssh -i "${SSH_KEY:-$HOME/.ssh/verda}" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new
     -o ServerAliveInterval=30 -o ConnectTimeout=10 "root@$ip")
stages=("$@")
[[ ${#stages[@]} -gt 0 ]] || stages=(packages disks model+build t0 serve)

echo "waiting for ssh on $ip"
for ((i = 0; i < 90; i++)); do
  "${SSH[@]}" true 2>/dev/null && break
  sleep 10
done
"${SSH[@]}" true || { echo "ssh to $ip never came up" >&2; exit 1; }
"${SSH[@]}" 'whoami; nft list tables'

tar -C "$root/node" -cz . | "${SSH[@]}" 'mkdir -p /opt/ambermist && tar -C /opt/ambermist -xz && chmod +x /opt/ambermist/bootstrap.sh /opt/ambermist/bin/*.sh'

# Secrets travel on stdin only (printf is a shell builtin, so nothing appears in argv).
"${SSH[@]}" 'id llama >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin llama'
printf '%s\n' "$AMB_API_KEY" | "${SSH[@]}" \
  'install -d -m 0755 /etc/ambermist && umask 027 && cat > /etc/ambermist/llama-api-keys && chown root:llama /etc/ambermist/llama-api-keys && chmod 0640 /etc/ambermist/llama-api-keys'
printf 'Authorization: Bearer %s\n' "$HF_TOKEN" | "${SSH[@]}" \
  'install -d -m 0700 /run/ambermist && umask 077 && cat > /run/ambermist/hf-auth-header'

run_stage() { "${SSH[@]}" "/opt/ambermist/bootstrap.sh $1"; }

for st in "${stages[@]}"; do
  if [[ $st == model+build ]]; then
    run_stage model & pm=$!
    run_stage build & pb=$!
    rc=0
    wait "$pm" || rc=1
    wait "$pb" || rc=1
    [[ $rc == 0 ]] || { echo "model or build failed" >&2; exit 1; }
  else
    run_stage "$st"
  fi
done
echo "provision: done. Tunnel: ssh -i ~/.ssh/verda -N -L 8080:127.0.0.1:8080 root@$ip"
