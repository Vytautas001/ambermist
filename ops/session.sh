#!/usr/bin/env bash
# Usage: ops/session.sh <command> [options]
#
#   check                       read-only: does compute state hold an instance that no longer
#                               exists (spot reclaim, zero balance)? Exit 3 if so.
#   clean                       check, then drop that instance from compute state.
#   up [--yes] [--wait[=EVERY]] [--site SITE] [--type TYPE] [--instance SKU]
#                               clean, apply storage (a no-op unless the model volumes were
#                               deleted), pick a site, apply compute, wait for the IP, provision.
#                               --yes passes -auto-approve to tofu: GPU billing starts unasked.
#                               --wait polls every EVERY (default 30s) until a candidate site has
#                               the SKU as TYPE, then applies at once. EVERY is seconds or a
#                               duration: 45s, 5m, 2h, 1h30m. Use it with --yes, or the claim
#                               waits at tofu's prompt.
#                               --site FIN-02|FIN-03 uses only that site's model volume
#                               (default: FIN-02, then FIN-03).
#                               --type spot (default) or on-demand (about twice the price,
#                               never reclaimed).
#                               --instance 1H200.141S.44V (default) or 2RTXPRO6000.60V; refused
#                               if the fleet cap has no room. Only the H200 is qualified to serve.
#   down compute|volumes|all [--yes]
#                               compute: tailscale logout, destroy the compute stack, reap
#                               detached ambermist-*-os volumes. volumes: delete every model
#                               volume in storage state (the weights are lost). all: both.
#                               Asks before each part unless --yes.
#
# Never switches between spot and on-demand by itself. Loads .env itself.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
set -a; . "$root/.env"; set +a

TF=(tofu -chdir="$root/infra/compute")
SSH=(ssh -i "${SSH_KEY:?set SSH_KEY in .env}" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new
     -o BatchMode=yes -o ConnectTimeout=10)

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit "${1:-0}"; }
fleet() { python3 "$root/ops/fleet-check.py" "$@"; }
# Captured first: grep -q exiting early would SIGPIPE tofu under pipefail.
managed() { grep -v '^data\.' <<<"$("${TF[@]}" state list)"; }
has_instance() { grep -qx verda_instance.node <<<"$(managed)"; }

# Whole seconds, or h/m/s parts in any mix: 45s, 5m, 2h, 1h30m. Prints seconds; fails on 0.
parse_duration() {
  local spec=${1,,} total=0 n unit
  [[ $spec =~ ^([0-9]+[smh]?)+$ ]] || return 1
  while [[ $spec =~ ^([0-9]+)([smh]?) ]]; do
    n=${BASH_REMATCH[1]} unit=${BASH_REMATCH[2]}
    case $unit in
      h) total=$((total + n * 3600)) ;;
      m) total=$((total + n * 60)) ;;
      *) total=$((total + n)) ;;
    esac
    spec=${spec#"${BASH_REMATCH[0]}"}
  done
  ((total > 0)) && printf '%s\n' "$total"
}

# 3750 -> 1h2m30s, 300 -> 5m, 45 -> 45s. For the poll messages, so a long wait reads as one.
human_duration() {
  local s=$1 out=
  ((s >= 3600)) && { out+="$((s / 3600))h"; s=$((s % 3600)); }
  ((s >= 60)) && { out+="$((s / 60))m"; s=$((s % 60)); }
  if ((s)) || [[ -z $out ]]; then out+="${s}s"; fi
  printf '%s\n' "$out"
}

confirm() {
  ((yes)) && return 0
  local answer
  read -r -p "$* Type yes to continue: " answer
  [[ $answer == yes ]] || { echo "aborted" >&2; exit 1; }
}

# Poll until --pick-site writes a site where the SKU is available. Without --wait, one try.
claim_site() {
  local out rc start=$SECONDS tries=0
  while :; do
    out=$(fleet --pick-site "${sel[@]}" 2>&1) && rc=0 || rc=$?
    if ((rc == 0)); then printf '%s\n' "$out"; return; fi
    # Keep polling only for missing spot capacity or a failed API call, never for the fleet cap.
    if [[ -n $wait && ( $out == *"no candidate site has "* || $out == *"ERROR: fleet check failed"* ) ]]; then
      echo "$(date -u +%H:%M:%S) $(tail -n 1 <<<"$out"); try $((++tries)) after" \
           "$(human_duration $((SECONDS - start))), retrying in $(human_duration "$wait")"
      sleep "$wait"
      continue
    fi
    printf '%s\n' "$out"
    exit "$rc"
  done
}

wait_ip() {
  local ip i
  for ((i = 0; i < 30; i++)); do
    ip=$("${TF[@]}" output -raw public_ip 2>/dev/null) || ip=
    [[ -n $ip && $ip != null ]] && { echo "$ip"; return; }
    sleep 10
    "${TF[@]}" apply -refresh-only -auto-approve >/dev/null
  done
  echo "public_ip never appeared; the instance is billing. Run: ops/session.sh down compute" >&2
  exit 1
}

cmd_up() {
  local ip
  [[ -z $instance || $instance == 1H200.141S.44V ]] ||
    echo "warning: $instance is not qualified to serve the model: it took ~81 GiB of VRAM on the H200," \
         "more than an H100 has; RTX PRO 6000 is untested. Provisioning may fail at the serve stage." >&2
  fleet --clean-state "${sel[@]}"
  tofu -chdir="$root/infra/storage" apply "${approve[@]}"
  while :; do
    claim_site
    "${TF[@]}" apply "${approve[@]}" && break
    [[ -n $wait ]] || exit 1
    # Retry only if no instance survived the failure; a live one may be billing.
    fleet --clean-state "${sel[@]}" >/dev/null
    if has_instance; then
      echo "apply failed and compute state holds an instance; not retrying" >&2
      exit 1
    fi
    echo "apply failed before an instance existed (capacity gone?); polling again in $(human_duration "$wait")"
    sleep "$wait"
  done
  ip=$(wait_ip)
  ssh-keygen -R "$ip" >/dev/null 2>&1 || true  # spot IPs are reused; the host key is new
  "$root/ops/provision.sh" "$ip" || {
    echo "provision failed; the instance is still billing. Rerun stages with ops/provision.sh $ip <stage...>," \
         "or stop it with ops/session.sh down compute" >&2
    exit 1
  }
}

down_compute() {
  local ip
  fleet --clean-state || echo "warning: fleet check reported a problem (above); tearing down anyway" >&2
  if [[ -z $(managed) ]]; then
    echo "compute state is empty; nothing to destroy"
  else
    if has_instance; then
      confirm "Destroy the GPU instance? Billing stops; /srv/logs on its OS disk is lost."
      ip=$("${TF[@]}" output -raw public_ip 2>/dev/null) || ip=
      if [[ -n $ip && $ip != null ]]; then
        # Frees the name ambermist-h200 for the next node.
        "${SSH[@]}" "root@$ip" tailscale logout ||
          echo "tailscale logout failed; remove ambermist-h200 in the Tailscale admin console" >&2
      fi
    else
      confirm "Destroy the compute stack (no instance; the uploaded SSH key and boot script)?"
    fi
    "${TF[@]}" destroy -auto-approve
  fi
  fleet --reap-os
}

down_volumes() {
  if has_instance; then
    echo "compute state still holds an instance: run ops/session.sh down compute first" >&2
    exit 1
  fi
  confirm "Delete every ambermist-model volume in infra/storage state? The weights are lost;" \
          "the next up creates blank volumes and downloads again."
  fleet --delete-model-volumes
}

cmd=${1:-}
[[ -n $cmd ]] || usage 1
shift
yes=0 wait= target= site= type= instance=
while (($#)); do
  case $1 in
    -y|--yes) yes=1 ;;
    --wait) wait=30s ;;
    --wait=*) wait=${1#--wait=} ;;
    --site|--type|--instance) [[ $# -ge 2 ]] || { echo "$1 needs a value" >&2; exit 1; }; printf -v "${1#--}" %s "$2"; shift ;;
    --site=*) site=${1#--site=} ;;
    --type=*) type=${1#--type=} ;;
    --instance=*) instance=${1#--instance=} ;;
    compute|volumes|all) target=$1 ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage 1 ;;
  esac
  shift
done
if [[ -n $wait ]]; then
  wait=$(parse_duration "$wait") ||
    { echo "--wait takes seconds or a duration: 30, 45s, 5m, 2h, 1h30m" >&2; exit 1; }
fi
approve=()
((yes)) && approve=(-auto-approve)
sel=()  # fleet-check validates both
[[ -z $site ]] || sel+=(--site "$site")
[[ -z $type ]] || sel+=(--type "$type")
[[ -z $instance ]] || sel+=(--instance "$instance")

case $cmd in
  check) fleet --check-state ;;
  clean) fleet --clean-state ;;
  up) cmd_up ;;
  down)
    case $target in
      compute) down_compute ;;
      volumes) down_volumes ;;
      all) down_compute; down_volumes ;;
      *) echo "down needs compute, volumes or all" >&2; usage 1 ;;
    esac ;;
  -h|--help) usage ;;
  *) echo "unknown command: $cmd" >&2; usage 1 ;;
esac
