# Model bake-off (phase P2)

The 10 hours of P2 exist to answer one question with data, not vibes: **which of
the three candidates should carry the live exercise**, and does it hold up at
128k context.

Candidates (all fit 2× RTX PRO 6000, all Apache/MIT/NVIDIA-open):

| model | active | KV @8×128k | note |
|---|---|---|---|
| `Qwen/Qwen3.5-122B-A10B-FP8` | 10B | 12.0 GB | published agentic benchmarks |
| `inclusionAI/Ling-3.0-flash-FP8` | 5.1B | 3.9 GB | fastest decode; vLLM support only weeks old |
| `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-FP8` | 12B | 4.0 GB | ~3% measured security-refusal rate |

**The one thing that can change the answer:** prefix caching on the linear/GDN
layers. Agentic loops resend a long system prompt every turn; if prefix caching
is broken, time-to-first-token goes from <1s to 5–15s. `runner.py` measures it.

## Run

```bash
# against each candidate's endpoint in turn (bring it up, point BASE_URL at it):
BASE_URL=http://node:8000/v1 API_KEY=... MODEL=redcell-adversary \
  python runner.py --context 65536  --out results/qwen-64k.json
BASE_URL=... python runner.py --context 131072 --out results/qwen-128k.json
```

Then `python compare.py results/*.json` for the side-by-side.

The sequential bake-off runner does not establish concurrent full-context
capacity. For the temporary two-slot llama.cpp trial on the existing H200, use
[`../docs/QWEN38-H200-EXPERIMENT.md`](../docs/QWEN38-H200-EXPERIMENT.md) and
`llama_two_slot_context.py`; it measures the actual rendered prompt with the
server tokenizer before submitting both streams concurrently.
