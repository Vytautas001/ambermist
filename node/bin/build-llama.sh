#!/usr/bin/env bash
# Build llama-server at the pinned commit in a CUDA container (plan step 20).
set -euo pipefail
. /opt/ambermist/pins/llama.cpp.conf
key="${LLAMA_SHA:0:12}-sm${CUDA_ARCH}-$(printf %s "$BUILD_IMAGE" | sha256sum | cut -c1-8)"
out=/srv/build/llama.cpp/$key
src=/srv/build/src/llama.cpp

# The binary must report the pinned commit; a build that lost its git info is a failure.
check_version() {
  local v
  v=$(LD_LIBRARY_PATH=$out/lib "$out/bin/llama-server" --version 2>&1) || return 1
  echo "$v"
  grep -q "${LLAMA_SHA:0:7}" <<< "$v"
}

if [[ -x $out/bin/llama-server ]] && check_version; then
  ln -sfn "$out" /srv/build/current
  echo "build: cache hit $key"
  exit 0
fi

mkdir -p /srv/build/src /srv/build/tmp
[[ -d $src/.git ]] || git clone --filter=blob:none https://github.com/ggml-org/llama.cpp.git "$src"
git -C "$src" fetch origin "$LLAMA_SHA"
git -C "$src" checkout --detach "$LLAMA_SHA"
git -C "$src" merge-base --is-ancestor "$QWEN4EXP_MERGE" "$LLAMA_SHA"

docker run --rm --network host -v /srv/build:/srv/build -e KEY="$key" -e CUDA_ARCH="$CUDA_ARCH" \
  "$BUILD_IMAGE" bash -euxc '
  apt-get update && apt-get install -y --no-install-recommends cmake ninja-build git ca-certificates
  git config --global --add safe.directory /srv/build/src/llama.cpp
  cmake -S /srv/build/src/llama.cpp -B /srv/build/tmp/$KEY -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=$CUDA_ARCH \
    -DGGML_NATIVE=OFF -DBUILD_SHARED_LIBS=OFF -DLLAMA_OPENSSL=OFF \
    -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF
  cmake --build /srv/build/tmp/$KEY --target llama-server -j"$(nproc)"
  mkdir -p /srv/build/llama.cpp/$KEY/bin /srv/build/llama.cpp/$KEY/lib
  cp /srv/build/tmp/$KEY/bin/llama-server /srv/build/llama.cpp/$KEY/bin/
  cp -P /usr/local/cuda/lib64/libcudart.so.12* /usr/local/cuda/lib64/libcublas.so.12* \
        /usr/local/cuda/lib64/libcublasLt.so.12* /srv/build/llama.cpp/$KEY/lib/'

! LD_LIBRARY_PATH=$out/lib ldd "$out/bin/llama-server" | grep -q 'not found' \
  || { echo "build: missing shared libraries" >&2; LD_LIBRARY_PATH=$out/lib ldd "$out/bin/llama-server" >&2; exit 1; }
check_version || { echo "build: llama-server does not report commit ${LLAMA_SHA:0:7}" >&2; exit 1; }
ln -sfn "$out" /srv/build/current
echo "build: done $key"
