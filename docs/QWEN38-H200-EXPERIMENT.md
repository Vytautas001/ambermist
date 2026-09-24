# Qwen3.8 GGUF experiment on the existing H200

This is a temporary, reversible capacity and performance experiment for the
existing FIN-02 H200. It does not change Terraform, the production model
defaults, the router, or the current systemd unit. The production service is
recorded as Qwen3.5 GPTQ-Int4 on vLLM with a 65,536-token configured context;
this experiment compares a corrected Qwen3.8 GGUF under llama.cpp at two
131,072-token slots. Keep the acquired instance and its persistent weights
volume. Do not resize the volume or run `tofu apply` for this experiment.

The selected checkpoint is an abliterated community conversion that removes
refusals. Treat this as a private technical evaluation artifact, not a
production model recommendation. Its model card says it uses Qwen Community
License 1.0 and that third-party Model-as-a-Service use requires a separate
license. Do not expose it to exercise participants or use it in production
without resolving that license. The repository's production choice and
licensing rationale remain in
[`ARCHITECTURE.md`](ARCHITECTURE.md).

Before starting, confirm the **actual Verda project balance is positive** in
the console. Terraform's projected remaining budget is separate from provider
account funds; with a zero balance Verda can discontinue the instance and move
attached volumes to trash. Trashed volumes are recoverable for 96 hours, and
restoring them charges the project balance ([Verda storage recovery rules](https://docs.verda.com/storage/deleting-storage/)).

## 1. Check the node before making changes

Connect to the existing node and capture a baseline:

```bash
nvidia-smi
free -h
df -h /mnt/weights /
nvcc --version
systemctl is-active redcell-vllm
```

The 119 GB checkpoint needs about 140 GB free disk space. Require roughly
80 GiB free host RAM before trying the CPU-resident embedding table, then
confirm actual memory use after load. If CUDA development tools are absent,
install a toolkit compatible with the installed driver before building.

Do not stop vLLM until the checkpoint hash is verified and a copy of the
existing unit configuration is saved. This trial causes a service interruption;
it is not suitable during an active exercise.

## 2. Download and verify the checkpoint

```bash
mkdir -p /mnt/weights/qwen38-test
cd /mnt/weights/qwen38-test

curl --fail --location --retry 5 --continue-at - \
  --output Qwen3.8-Flash-Next-Abliterated-Q4_K_M.gguf \
  'https://huggingface.co/windowsxp811203/Qwen3.8-Flash-Next-Abliterated-GGUF/resolve/c3365c4/Qwen3.8-Flash-Next-Abliterated-Q4_K_M.gguf'

sha256sum Qwen3.8-Flash-Next-Abliterated-Q4_K_M.gguf
```

Expected SHA-256:

```text
324c85132e04654480ac93923f444b760b2950eb8c84a346dd0ec70e680ecde2
```

Stop if the hash differs. The revision in the URL pins the corrected GGUF
metadata; the earlier file had incorrect sparse-attention metadata. Keep the
checkpoint and checksum record until results are reviewed.

## 3. Build llama.cpp and record its exact revision

```bash
apt-get update
apt-get install -y build-essential cmake git libcurl4-openssl-dev

cd /mnt/weights/qwen38-test
git clone https://github.com/ggml-org/llama.cpp.git
cd llama.cpp
git fetch origin pull/27742/head:pr27742
git switch --detach pr27742
git rev-parse HEAD > ../llama.cpp-revision.txt

cmake -S . -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES=90

cmake --build build --config Release -j 8 --target llama-server
```

This GGUF uses the experimental `qwen4_exp` architecture, which is not in a
released llama.cpp version. The checkpoint publisher says it requires the
support from [upstream PR #27742](https://github.com/ggml-org/llama.cpp/pull/27742)
and will not load on stock llama.cpp. The fetched PR head is detached and its
exact commit is written to `llama.cpp-revision.txt`; preserve that file with
the measurements. The GGUF format may change before the PR is merged.

## 4. Stop vLLM and start the temporary server

Use a persistent terminal such as `tmux`. Stop the service only after the
preflight checks above pass. Keep the original systemd unit available for
restoration.

```bash
systemctl stop redcell-vllm
nvidia-smi

set -a
source /mnt/weights/.env
set +a
export LLAMA_API_KEY="${VLLM_API_KEY:?Missing API key}"

cd /mnt/weights/qwen38-test
set -o pipefail

./llama.cpp/build/bin/llama-server \
  --model ./Qwen3.8-Flash-Next-Abliterated-Q4_K_M.gguf \
  --alias qwen38-test \
  --host 127.0.0.1 \
  --port 8001 \
  --n-gpu-layers all \
  --override-tensor 'per_layer_token_embd=CPU' \
  --ctx-size 262144 \
  --parallel 2 \
  --no-kv-unified \
  --no-context-shift \
  --fit off \
  --flash-attn on \
  --cache-type-k f16 \
  --cache-type-v f16 \
  --batch-size 512 \
  --ubatch-size 128 \
  --jinja \
  --metrics \
  --slots \
  2>&1 | tee server.log
```

The intended allocation is 262,144 total context tokens, split into two slots
of 131,072 tokens each. Before sending test traffic, inspect `server.log` and
confirm the actual per-slot context, CPU placement for the embedding tensor,
GPU placement for transformer layers, and absence of automatic context
reduction. Stop if the loaded placement or context differs. The llama.cpp
server's [`--parallel`, KV cache, and slot options](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
are version-sensitive, so record the source revision with the startup log.

From a workstation, forward the local test port:

```bash
ssh -N -L 8001:127.0.0.1:8001 root@NODE_IP
```

The test endpoint is `http://127.0.0.1:8001/v1`, model `qwen38-test`, with the
same API key. Keep the listener bound to localhost; use the SSH tunnel rather
than exposing port 8001 publicly.

## 5. Measure two concurrent long-context sessions

Run the dedicated client from the repository root (or copy it to the
workstation). It uses `/apply-template` and `/tokenize` to size each prompt,
then streams two independent requests concurrently. It records prepared and
reported input tokens, time to first token, total duration, completion usage,
and each answer. The default is 129,000 input tokens and 1,024 output tokens
per session, within a 131,072-token slot.

```bash
set -a
source .env
set +a

LLAMA_BASE_URL=http://127.0.0.1:8001 \
LLAMA_API_KEY="${VLLM_API_KEY:?Missing API key}" \
python3 evals/llama_two_slot_context.py \
  --out evals/results/qwen38-two-slot-129k.json
```

Each prompt carries different synthetic markers near its beginning, middle,
and end. Confirm the report has matching tokenizer and request usage counts,
both streams overlap in time, both answers identify only their own markers,
and the `/slots` endpoint reports both slots processing. Monitor `nvidia-smi`,
`free -h`, swap activity, and disk faults during prefill and decode. Save the
server log and resource observations with the JSON report.

The client then replays each full conversation and its first answer, asks for
the markers again, and token-counts that follow-up before sending it with a
128-token output limit. It records a follow-up error if the replay would exceed
the per-slot context. The follow-up is a continuity check; the concurrent long
prompts are the actual 128k-capacity measurement. The existing
`evals/runner.py` is sequential and does not measure this requirement.

After the memory test succeeds, check automatic tool-call formatting against
the harness's registered `enumerate_hosts` schema and synthetic scope. The
client validates that the requested CIDR is within `192.0.2.0/24` and never
dispatches the stub:

```bash
set -a
source .env
set +a

LLAMA_BASE_URL=http://127.0.0.1:8001 \
LLAMA_API_KEY="${VLLM_API_KEY:?Missing API key}" \
python3 evals/check_stub_tool.py
```

Keep harness tools range-bound stubs. This check exercises parsing only; it
does not wire the model to real actions.

## 6. Tune only after recording the baseline

Keep two slots at 131,072 tokens for all comparisons:

| Observation | Next change |
|---|---|
| GPU out of memory during prefill | Reduce `--ubatch-size` from 128 to 64, then repeat the same test. |
| Still insufficient GPU memory | Try Q8 cache only if supported for this architecture and build; repeat the correctness check. |
| CPU swapping or heavy disk faults | Resolve RAM pressure before interpreting speed. |
| Plenty of GPU headroom | Trial GPU placement of the embedding table and compare. |
| Long-context decode is very slow | Compare shorter-context measurements and inspect runtime behavior. |

There is an [upstream report of CUDA decode slowdown with context](https://github.com/ggml-org/llama.cpp/issues/28734)
for this architecture. A successful load alone does not establish that the
model is usable at the required concurrency.

## 7. Restore vLLM and record the outcome

Stop `llama-server` with Ctrl-C in its terminal, then restore and verify the
existing service:

```bash
systemctl start redcell-vllm
curl --fail http://127.0.0.1:8000/health
```

Record the checkpoint hash, llama.cpp revision, exact launch flags, measured
prompt and output token counts, TTFT, duration, generation rate, errors, and
peak GPU and host RAM use. Keep the result files free of credentials.

The estimated H200 rate is €3.728/hour. Four additional hours cost about
€14.91 before storage. Check remaining funds before extending the test; do not
change or bypass the repository's €500 budget guard. The running instance
continues to incur charges during the experiment.
