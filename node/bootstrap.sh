#!/usr/bin/env bash
# Usage: bootstrap.sh <packages|disks|model|build|t0|serve|nginx|observe>. Every stage is safe to rerun.
set -euo pipefail
stage=${1:?usage: bootstrap.sh <packages|disks|model|build|t0|serve|nginx|observe>}
ROOT=/opt/ambermist
MODEL_SIZE_BYTES=$((140 * 1024 * 1024 * 1024))
MODEL_DIR=/srv/models/qwen38-uncensored-q4km

mkdir -p /srv/build /srv/logs/bootstrap /srv/logs/llama

# Copy files from node/etc to the same path under /etc.
install_etc() { local p; for p; do install -D -m 0644 "$ROOT/etc/$p" "/etc/$p"; done; }

# The boot script loads the firewall about a minute after first login. Packages start
# listeners on all interfaces, and nginx is the public API: neither may run before it.
wait_firewall() {
  local i
  for ((i = 0; i < 60; i++)); do
    nft list table inet amb >/dev/null 2>&1 && return 0
    sleep 5
  done
  echo "$stage: firewall table inet amb not loaded after 5 min; refusing to continue" >&2
  return 1
}

stage_packages() {
  wait_firewall
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    nftables jq python3-venv libgomp1 git rsync nginx prometheus prometheus-node-exporter
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
  free -g
}

stage_disks() {
  mkdir -p /srv/models
  if ! blkid -L amb-model >/dev/null 2>&1; then
    local -a blank=()
    local name size
    while read -r name size; do
      [[ -z "$(blkid -o value -s TYPE "/dev/$name" 2>/dev/null)" ]] || continue
      [[ "$(lsblk -rno NAME "/dev/$name" | wc -l)" == 1 ]] || continue   # no partitions
      [[ $size == "$MODEL_SIZE_BYTES" ]] && blank+=("/dev/$name")
    done < <(lsblk -dbnro NAME,SIZE,TYPE | awk '$3=="disk"{print $1, $2}')
    if [[ ${#blank[@]} != 1 ]]; then
      echo "disks: expected exactly one blank $((MODEL_SIZE_BYTES >> 30)) GiB disk, found ${#blank[@]}; not formatting" >&2
      lsblk -o NAME,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINT >&2
      exit 1
    fi
    echo "disks: formatting ${blank[0]}"
    mkfs.ext4 -m 0 -T largefile4 -L amb-model "${blank[0]}"
  fi
  grep -q 'LABEL=amb-model' /etc/fstab || \
    echo 'LABEL=amb-model /srv/models ext4 defaults,nofail,x-systemd.device-timeout=60s 0 2' >> /etc/fstab
  systemctl daemon-reload
  mountpoint -q /srv/models || mount /srv/models
  df -h /srv/models
  lsblk -o NAME,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINT
  ls -l /dev/disk/by-id/ || true
}

stage_model() { "$ROOT/bin/fetch-model.sh"; }
stage_build() { "$ROOT/bin/build-llama.sh"; }

stage_t0() {
  local meta=/srv/logs/bootstrap/gguf-meta.json venv=/srv/build/gguf-venv
  [[ -x $venv/bin/gguf-dump ]] || {
    python3 -m venv "$venv"
    "$venv/bin/pip" install -q /srv/build/src/llama.cpp/gguf-py
  }
  . "$ROOT/pins/model.conf"
  "$venv/bin/gguf-dump" --json --json-array --no-tensors "$MODEL_DIR/$ENTRY_FILE" > "$meta"
  jq -e '.metadata["general.architecture"].value == "qwen4exp"' "$meta" \
    || { echo "t0: architecture is not qwen4exp" >&2; exit 1; }
  jq -e '[.metadata["qwen4exp.attention.compress_ratios"].value[]] | all(. == 0 or . == 4)' "$meta" \
    || { echo "t0: compress_ratios has values other than 0 and 4 (the known bad upload)" >&2; exit 1; }
  echo "t0: compress_ratios = 4 on $(jq '[.metadata["qwen4exp.attention.compress_ratios"].value[] | select(. == 4)] | length' "$meta") layers (12 expected, unverified)"
  . /opt/ambermist/pins/llama.cpp.conf
  local v
  v=$(LD_LIBRARY_PATH=/srv/build/current/lib /srv/build/current/bin/llama-server --version 2>&1)
  echo "$v"
  grep -q "${LLAMA_SHA:0:7}" <<< "$v" || { echo "t0: version does not show ${LLAMA_SHA:0:7}" >&2; exit 1; }
  echo "t0: ok"
}

stage_serve() {
  . "$ROOT/serving.conf"
  # Key for the local Prometheus scrape. Generated here, never leaves the node; provision.sh
  # keeps it in llama-api-keys when it rewrites that file.
  local mk=/etc/ambermist/metrics-key
  [[ -s $mk ]] || (umask 077; echo "amb-$(openssl rand -hex 32)" > "$mk")
  chown root:prometheus "$mk"; chmod 0640 "$mk"
  grep -qxFf "$mk" /etc/ambermist/llama-api-keys || cat "$mk" >> /etc/ambermist/llama-api-keys

  install_etc tmpfiles.d/ambermist.conf systemd/system/llama-server.service \
    systemd/system/llama-healthcheck.service systemd/system/llama-healthcheck.timer
  systemd-tmpfiles --create /etc/tmpfiles.d/ambermist.conf
  systemctl stop llama-server 2>/dev/null || true   # also drops a Phase 1 transient unit
  systemctl reset-failed llama-server 2>/dev/null || true
  systemctl daemon-reload
  systemctl enable --now llama-server llama-healthcheck.timer
  local i code
  for ((i = 0; i < 240; i++)); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$LLAMA_PORT/health" || true)
    [[ $code == 200 ]] && { echo "serve: healthy after ~$((i * 5))s"; return 0; }
    systemctl is-active --quiet llama-server || { echo "serve: llama-server exited" >&2; tail -n 60 /srv/logs/llama/server.log >&2; return 1; }
    sleep 5
  done
  echo "serve: not healthy after 20 min" >&2
  return 1
}

stage_nginx() {
  wait_firewall
  mkdir -p /srv/logs/nginx
  install_etc nginx/sites-available/ambermist
  ln -sf /etc/nginx/sites-available/ambermist /etc/nginx/sites-enabled/ambermist
  rm -f /etc/nginx/sites-enabled/default
  nginx -t
  systemctl enable nginx
  systemctl reload-or-restart nginx
  curl -sf -o /dev/null http://127.0.0.1:8080/health && echo "nginx: /health ok on :8080"
}

stage_observe() {
  install -d -o prometheus -g prometheus /srv/logs/prometheus
  install_etc default/prometheus default/prometheus-node-exporter prometheus/prometheus.yml \
    logrotate.d/ambermist systemd/system/logrotate.timer.d/hourly.conf \
    systemd/system/amb-metrics.service systemd/system/amb-metrics.timer
  systemctl daemon-reload
  systemctl restart prometheus prometheus-node-exporter logrotate.timer
  systemctl enable --now amb-metrics.timer
  ss -ltn | grep -E ':(9090|9100) '
}

# The outer call logs and times the stage; the inner call runs it under set -e.
if [[ -z ${AMB_STAGE_INNER:-} ]]; then
  log=/srv/logs/bootstrap/$stage.log
  timeline=/srv/logs/bootstrap/timeline.log
  echo "$(date -u +%FT%TZ) $stage start" >> "$timeline"
  start=$SECONDS
  set +e
  AMB_STAGE_INNER=1 "$0" "$stage" 2>&1 | tee -a "$log"
  rc=${PIPESTATUS[0]}
  echo "$(date -u +%FT%TZ) $stage end rc=$rc duration=$((SECONDS - start))s" >> "$timeline"
  exit "$rc"
fi
"stage_$stage"
