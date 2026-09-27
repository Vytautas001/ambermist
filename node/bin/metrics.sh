#!/usr/bin/env bash
# Run by amb-metrics.timer every 15 s: GPU state, and nginx requests in the last 60 s by
# status code, as a node_exporter textfile. Written to a temp file and renamed, so
# node_exporter never reads a partial file.
set -uo pipefail
dir=/var/lib/prometheus/node-exporter
tmp=$(mktemp "$dir/.amb.XXXXXX")
if q=$(nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw \
         --format=csv,noheader,nounits 2>/dev/null); then
  IFS=', ' read -r util used total temp power <<< "$q"
  printf 'amb_gpu_up 1\namb_gpu_utilization_percent %s\namb_gpu_memory_used_mib %s\namb_gpu_memory_total_mib %s\namb_gpu_temperature_celsius %s\namb_gpu_power_watts %s\n' \
    "$util" "$used" "$total" "$temp" "$power" >> "$tmp"
else
  echo 'amb_gpu_up 0' >> "$tmp"
fi
log=/srv/logs/nginx/access.json.log
if [[ -f $log ]]; then
  tail -n 50000 "$log" | jq -r --argjson since "$(( $(date +%s) - 60 ))" 'select(.ts >= $since) | .status' \
    | sort | uniq -c | awk '{printf "amb_http_requests_last_minute{code=\"%s\"} %s\n", $2, $1}' >> "$tmp"
fi
chmod 0644 "$tmp"
mv "$tmp" "$dir/amb.prom"
