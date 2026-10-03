#!/usr/bin/env bash
# Download and verify the pinned model shards into /srv/models/$MODEL_ID (plan step 19).
# Resumable: partial downloads live in *.part and continue with curl --continue-at -.
set -euo pipefail
. /opt/ambermist/pins/model.conf
dest=/srv/models/$MODEL_ID
man=/opt/ambermist/pins/$MODEL_ID.sha256
authfile=/run/ambermist/hf-auth-header
mkdir -p "$dest"
want=$(sha256sum "$man" | cut -d' ' -f1)

# Every shard exists in the manifest's size.
check_sizes() {
  local sha bytes path out
  while read -r sha bytes path; do
    out=$dest/$(basename "$path")
    [[ -f $out && "$(stat -c %s "$out")" == "$bytes" ]] || return 1
  done < "$man"
}

if [[ "$(cat "$dest/.verified" 2>/dev/null)" == "$want" && "${FULL_VERIFY:-0}" != 1 ]]; then
  check_sizes && { echo "model: fast path"; exit 0; }
fi

fetch() { # sha bytes path
  local out=$dest/$(basename "$3")
  if [[ -f $out ]] && echo "$1  $out" | sha256sum -c --quiet; then
    echo "model: have $(basename "$out")"; return 0
  fi
  local auth=()
  [[ -f $authfile ]] && auth=(-H "@$authfile")
  if [[ "$(stat -c %s "$out.part" 2>/dev/null || echo 0)" != "$2" ]]; then
    curl --fail -L --retry 5 --retry-all-errors --continue-at - "${auth[@]}" \
      -o "$out.part" "https://huggingface.co/$HF_REPO/resolve/$HF_REVISION/$3"
  fi
  echo "$1  $out.part" | sha256sum -c --quiet || { rm -f "$out.part"; return 1; }
  mv "$out.part" "$out"
  echo "model: verified $(basename "$out")"
}

pids=()
while read -r sha bytes path; do
  fetch "$sha" "$bytes" "$path" &
  pids+=($!)
done < "$man"
rc=0
for p in "${pids[@]}"; do wait "$p" || rc=1; done
[[ $rc == 0 ]] || { echo "model: download or verification failed" >&2; exit 1; }
check_sizes || { echo "model: size check failed" >&2; exit 1; }
echo "$want" > "$dest/.verified"
echo "model: done"
