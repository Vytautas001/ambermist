#!/usr/bin/env bash
# ExecStartPre of llama-server.service (runs as llama): refuse to start on a half-provisioned node.
set -u
. /opt/ambermist/pins/model.conf
fail() { echo "preflight-serve: $*" >&2; exit 1; }
want=$(sha256sum "/opt/ambermist/pins/$MODEL_ID.sha256" | cut -d' ' -f1)
[[ "$(cat "/srv/models/$MODEL_ID/.verified" 2>/dev/null)" == "$want" ]] || fail "model not verified against the pinned manifest"
[[ -x /srv/build/current/bin/llama-server ]] || fail "llama-server is not built"
[[ -s /etc/ambermist/llama-api-keys ]] || fail "API key file is missing or empty"
nvidia-smi >/dev/null 2>&1 || fail "nvidia-smi failed"
