#!/usr/bin/env bash
# Usage: gateway.sh [stage...]  (default: netfilter dns nginx verify)
# Makes this host the lab's gateway to the ambermist LLM: Tailscale faces the tailnet,
# nginx faces the lab. Lab clients then need no Tailscale of their own. Every stage is
# safe to rerun, and `netfilter` and `dns` must be rerun after `tailscale up`, which
# resets the prefs they set.
set -euo pipefail

LAB_IP=${LAB_IP:-100.66.6.130}
LAB_CIDR=${LAB_CIDR:-100.66.6.0/24}
LAB_DNS=${LAB_DNS:-100.100.100.26 100.100.100.28}
LAB_DOMAIN=${LAB_DOMAIN:-ambermist.lt}
H200=${H200:-ambermist-h200.tail57998f.ts.net}
PORT=${PORT:-8080}

[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }

stage_netfilter() {
  # The lab runs inside 100.64.0.0/10, the range Tailscale claims. Its anti-spoof rule
  # (-s 100.64.0.0/10 ! -i tailscale0 -j DROP) therefore drops every reply from the lab
  # LAN and the lab resolvers. Off is the only setting that lets both coexist; see
  # docs/adr/0006-lab-gateway-for-llm-access.md for what that costs.
  tailscale set --netfilter-mode=off
  echo "netfilter: off (lab LAN overlaps the tailnet range)"
}

stage_dns() {
  # eth0 is unmanaged and 50-cloud-init puts dns-nameservers on the `lo` stanza, so
  # nothing ever handed the lab resolvers to systemd-resolved. Do it here instead.
  install -d -m 0755 /etc/systemd/resolved.conf.d
  umask 022
  cat > /etc/systemd/resolved.conf.d/ambermist-lab.conf <<CONF
[Resolve]
DNS=${LAB_DNS}
Domains=${LAB_DOMAIN}
CONF
  # The stub is what makes tailscaled pick the systemd-resolved manager and publish
  # MagicDNS as a routing-only domain on tailscale0, instead of overwriting this file.
  ln -sf /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
  systemctl restart systemd-resolved
  tailscale set --accept-dns=true
  echo "dns: lab resolvers global, MagicDNS scoped to the tailnet"
}

stage_nginx() {
  install -d -m 0755 /srv/logs/nginx
  umask 022
  cat > /etc/nginx/sites-available/ambermist-gateway <<CONF
log_format gw_json escape=json '{"ts":\$msec,"client":"\$remote_addr","method":"\$request_method",'
  '"uri":"\$uri","status":\$status,"req_bytes":\$request_length,"resp_bytes":\$bytes_sent,'
  '"rt":\$request_time,"urt":"\$upstream_response_time","ustatus":"\$upstream_status"}';

server {
  listen ${LAB_IP}:${PORT};      # lab side only: the tailnet side stays outbound
  listen 127.0.0.1:${PORT};
  allow ${LAB_CIDR};
  allow 127.0.0.1;
  deny all;

  access_log /srv/logs/nginx/gateway.access.json.log gw_json;  # no bodies, so no prompts
  error_log  /srv/logs/nginx/gateway.error.log warn;
  client_max_body_size 8m;

  # Re-resolved per request: the H200's 100.x address does not survive a rebuild, the
  # MagicDNS name does. 127.0.0.53 is the resolved stub, which routes *.ts.net to MagicDNS.
  resolver 127.0.0.53 valid=30s;
  set \$h200 ${H200};

  # No credential is injected here. Clients send their own Authorization header and the
  # H200 checks it, so this host stores no key; see the ADR.
  location = /health {
    proxy_pass http://\$h200:${PORT}\$request_uri;
    access_log off;
  }

  location ~ ^/v1/(chat/completions|models)\$ {
    proxy_pass http://\$h200:${PORT}\$request_uri;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;          # SSE streaming
    proxy_read_timeout 900s;      # matches the H200; concurrency is capped there, not here
    proxy_send_timeout 900s;
  }

  location / { return 404; }
}
CONF
  ln -sf /etc/nginx/sites-available/ambermist-gateway /etc/nginx/sites-enabled/ambermist-gateway
  rm -f /etc/nginx/sites-enabled/default
  nginx -t
  systemctl enable nginx
  systemctl reload-or-restart nginx
  echo "nginx: gateway listening on ${LAB_IP}:${PORT}"
}

stage_verify() {
  local fail=0
  resolvectl query "${H200}" >/dev/null 2>&1 \
    || { echo "verify: ${H200} does not resolve - is the node joined to the tailnet?" >&2; fail=1; }
  getent hosts "${LAB_DOMAIN}" >/dev/null 2>&1 \
    || { echo "verify: ${LAB_DOMAIN} does not resolve - lab DNS unreachable" >&2; fail=1; }
  ss -tln | grep -q "${LAB_IP}:${PORT}" \
    || { echo "verify: nothing listening on ${LAB_IP}:${PORT}" >&2; fail=1; }
  if curl -sf -o /dev/null --max-time 15 "http://${LAB_IP}:${PORT}/health"; then
    echo "verify: /health ok through the gateway"
  else
    echo "verify: /health failed through the gateway (502 means the H200 is unreachable)" >&2
    fail=1
  fi
  return "$fail"
}

stages=("$@")
[[ ${#stages[@]} -gt 0 ]] || stages=(netfilter dns nginx verify)
for stage in "${stages[@]}"; do
  case "$stage" in
    netfilter | dns | nginx) "stage_$stage" ;;
    verify) stage_verify || echo "gateway configured, but verification did not pass - see above" >&2 ;;
    *) echo "unknown stage: $stage" >&2; exit 1 ;;
  esac
done
