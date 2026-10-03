#!/usr/bin/env bash
# Run by llama-healthcheck.timer every 30 s: restart llama-server after 3 failed /health
# checks in a row. 503 (loading) counts as healthy for the first 20 min after a start.
. /opt/ambermist/serving.conf
systemctl is-active --quiet llama-server || exit 0      # systemd handles the failed state
code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:$LLAMA_PORT/health" || true)
since=$(( $(date +%s) - $(date -d "$(systemctl show -p ActiveEnterTimestamp --value llama-server)" +%s) ))
f=/run/ambermist/health-fails
n=$(cat "$f" 2>/dev/null || echo 0)
if [[ $code == 200 || ( $code == 503 && $since -lt 1200 ) ]]; then echo 0 > "$f"; exit 0; fi
n=$((n + 1)); echo "$n" > "$f"
if (( n >= 3 )); then
  logger -t amb-health "restart after $n failed checks (last=$code)"
  echo 0 > "$f"
  systemctl restart llama-server
fi
