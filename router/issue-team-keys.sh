#!/usr/bin/env bash
# Issue one virtual key per Blue Team plus White Cell, each with its own
# concurrency cap and its own log stream. Run once against the started router.
#
#   LITELLM_URL=http://localhost:4000 LITELLM_MASTER_KEY=sk-... ./issue-team-keys.sh
set -euo pipefail
: "${LITELLM_URL:?set LITELLM_URL}"; : "${LITELLM_MASTER_KEY:?set LITELLM_MASTER_KEY}"

issue() {
  local alias="$1"
  curl -fsS -X POST "$LITELLM_URL/key/generate" \
    -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "Content-Type: application/json" \
    -d "{\"key_alias\":\"$alias\",\"models\":[\"redcell-adversary\"],\"max_parallel_requests\":3,\"metadata\":{\"exercise\":\"ambermist\",\"tenant\":\"$alias\"}}" \
    | python3 -c "import sys,json;d=json.load(sys.stdin);print(f'{\"$alias\":16} {d[\"key\"]}')"
}
for i in $(seq -w 1 8); do issue "blue-team-$i"; done
issue "white-cell"
echo "Store these in your secrets manager. They are the per-team REDCELL_API_KEY values."
