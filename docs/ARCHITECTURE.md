# AI Adversary Simulation Platform — Infrastructure & Model Design

> **Status note (2026-09-24, [ADR 0002](adr/0002-fleet-cap-no-budget.md)):** the
> €500 budget below and the single-B200/two-identical-node topology it argues
> for are **historical** — the budget ceiling has been removed and the
> live topology is now a primary + standby pair on different hardware
> families under a fixed GPU fleet cap (1x H200 + 1x H100 + 2x RTX PRO 6000).
> The model choice and KV-cache reasoning below are still current. For what's
> actually enforced today, read `AGENTS.md`, `docs/CAPACITY-RUNBOOK.md` and
> `infra/locals.tf` (`local.fleet_limit`), not the cost figures in this file.

**Exercise:** Blue vs. Red cyber defence exercise, 8 defending teams
**Budget:** €500 hard ceiling (≈ $581 at ECB reference rate 1.1622, 4 Sep 2026) — **removed, see status note above**
**Runtime:** 40 h preparation + 40 h live
**Constraints applied:** EU-resident inference only · agentic (high-duty-cycle) workload · 64–128k context per session
**Prepared:** 15 September 2026

---

## 0. Executive summary

| | Recommendation |
|---|---|
| **Model** | `Qwen/Qwen3.5-122B-A10B` (FP8), Apache 2.0 — 122B total / 10B active MoE, 262k native context |
| **Hardware** | **A single NVIDIA B200 SXM6 180 GB** — not a multi-GPU cluster |
| **Provider** | Verda (formerly DataCrunch), Finland — EU-resident, ISO 27001, GDPR |
| **Serving** | vLLM ≥ 0.28.0, **TP=1**, FP8 KV cache, PagedAttention + prefix caching, chunked prefill |
| **Concurrency headroom** | 23 concurrent sessions at 128k — **2.9× the 8 required** |
| **Total cost** | **€439.55** — 12.1 % contingency remaining |

**The single most important finding:** your 8 × 128k concurrency requirement sounds like it demands a large multi-GPU cluster. It does not — *provided you pick a model with a sparse attention layout*. Qwen3.5-122B uses a strict 3:1 linear-to-full attention pattern, so only **12 of its 48 layers hold a KV cache**. At FP8 that is **12 KiB per token**, so all eight sessions at maximum context need just **12 GiB of KV**. A conventional dense-attention model of the same size (Llama 4 Scout, GLM-4.5-Air) needs **96 GiB** for the identical workload — 8× more — which is what forces people into 4-GPU nodes and blows budgets like this one.

Choosing the attention architecture, not the GPU count, is what makes this fit in €500.

---

## 1. Model selection

### 1.1 The decisive constraint is KV cache, not parameter count

At 8 concurrent sessions × 128k tokens you are buying **1.05 million tokens of resident KV cache**. That number multiplies whatever the model's per-token KV footprint is, and it dominates every other memory consideration. Measured from the models' actual `config.json` files:

| Model | Total / active | Attention layout | Full-attn layers | KV per token (FP8) | **KV for 8 × 128k** | Weights (FP8) | Total |
|---|---|---|---|---|---|---|---|
| **Qwen3.5-122B-A10B** | 122B / 10B | 3:1 linear:full, GQA-2, head_dim 256 | **12 / 48** | 12.0 KiB | **12.0 GiB** | ~125 GB | **137 GB** |
| Ling-3.0-flash | 124B / 5.1B | 5:1 KDA-linear:MLA | **7 / 42** | 3.9 KiB | **3.9 GiB** | ~124 GB | 128 GB |
| Nemotron-3-Super-120B | 120B / 12B | Mamba-2 hybrid, GQA-2 | **8 / 88** | 4.0 KiB | **4.0 GiB** | ~120 GB | 124 GB |
| gpt-oss-120b | 117B / 5.1B | alternating sliding(128)/full, GQA-8 | 18 / 36 | 18.0 KiB | 18.0 GiB | ~61 GB (MXFP4) | 79 GB |
| Mistral-Small-4-119B | 119B / 6.5B | **36 / 36 full**, MLA | 36 / 36 | 11.3 KiB | 11.3 GiB | ~119 GB | 130 GB |
| *GLM-4.5-Air 106B* | 106B / 12B | **46 / 46 full**, GQA-8 | 46 / 46 | 92.0 KiB | **92.0 GiB** | ~106 GB | 198 GB |
| *Llama 4 Scout 109B* | 109B / 17B | **48 / 48 full**, GQA-8 | 48 / 48 | 96.0 KiB | **96.0 GiB** | ~109 GB | 205 GB |

*(Italicised rows are rejected — see §1.4.)*

### 1.2 Recommendation: Qwen3.5-122B-A10B, FP8

**Why this one:**

1. **Licence is Apache 2.0** — no MAU threshold, no acceptable-use rider, no geographic clause. For an EU-domiciled exercise organiser this is the cleanest possible position, and it survives legal review without argument.
2. **It is the only candidate with published, specific agentic benchmarks** — BFCL-V4 72.2, **τ²-Bench 79.5**, SWE-bench Verified 72.0, OSWorld-Verified 58.0. You are buying tool-calling and long-horizon reasoning, so evidence on exactly that workload beats a general-intelligence leaderboard position.
3. **10B active parameters** keeps decode fast enough that 8 parallel agent loops do not queue (see §4.4).
4. **262k native context** gives you 2× headroom above the 128k target — no YaRN/RoPE scaling hacks, no degradation cliff at the top of the window.
5. **Most mature vLLM path of the modern candidates** — Qwen3.5 MoE landed in vLLM 0.27.0 (Aug 2026), with GDN/MTP decode-kernel fixes in 0.28.0. Roughly five weeks of upstream hardening versus three for Ling.
6. **Run the text-only checkpoint** (drop the Qwen3-VL tower) unless you need screenshot-driven agents. Saves VRAM and avoids the multimodal code paths entirely.

**The one thing to verify on day one:** vLLM's prefix-cache path for Gated-DeltaNet / Mamba-style layers is still flagged experimental in align mode. Agentic sessions hang a long, stable system prompt in front of every turn, so prefix caching is worth an order of magnitude in time-to-first-token. **Test it in P1 (§3).** If it is broken for your harness, that is the single condition that should flip you to Ling-3.0-flash.

### 1.3 Ranked alternates

- **Alternate A — `inclusionAI/Ling-3.0-flash` (FP8, MIT).** Currently **top of the Artificial Analysis medium open-weights table**, only 5.1B active (fastest decode in the field), and the best KV economics of any candidate at 3.9 KiB/token (MLA: `kv_lora_rank` 512 + 64-dim shared RoPE key, over only 7 layers). It is plausibly the *better* model. It is the alternate rather than the primary purely on operational risk: native vLLM support arrived in 0.28.0 three weeks ago, and upstream validated it at `--max-model-len 32768` — **not at your 128k target**. If the P2 bake-off shows it holding up at long context, take it.
- **Alternate B — `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B`.** Only 8 attention layers out of 88, so 4 KiB/token — the lightest KV in the field, with 262k native. It is also the only shortlisted model with a **directly measured security-refusal rate (~3 %)** from a published framework (§5). Costs: the NVIDIA Open Model License is bespoke and needs a legal skim rather than Apache-equivalence assumptions, and 12B active means slower decode than Ling or Qwen.
- **Degraded-mode / standby model — `Qwen3.5-122B-A10B` GPTQ-Int4.** Same weights, same tokenizer, same tool-call parser, ~68 GB — fits the standby RTX PRO 6000 96 GB with ~13.9 GiB left for KV (≈ 18 sessions at 64k; see §4.5). Failing over to a harder quantisation of the *same* model is far less disruptive mid-exercise than failing over to a different model with different prompt sensitivities.

### 1.4 Rejected, with reasons

| Model | Reason |
|---|---|
| **Llama 4 Scout / Maverick** | **Hard blocker.** The Llama 4 Community License states the §1(a) grant is *"not being granted to you if you are an individual domiciled in, or a company with a principal place of business in, the European Union."* Llama 4 is natively multimodal, so this is not a narrow carve-out — it removes the grant for the model you would actually run. Meta has also shipped nothing in this line since May 2025. |
| **GLM-5.3-Flash** | 320B total / 18B active ≈ 320 GB in FP8. Does not fit any affordable single node. (GLM-4.7-Flash, at 31B, is too small; the Air line has effectively vacated the 100B class.) |
| **Mistral-Small-4-119B** | Apache 2.0 and EU-domiciled vendor, which is attractive — but **36 of 36 layers are full attention** and its config declares `num_key_value_heads: 32` alongside `kv_lora_rank: 256`. If your vLLM build materialises full MHA instead of the compressed MLA cache, KV explodes from 11.3 KiB/token to **~288 KiB/token = 288 GiB for your workload** — a 25× blowup that makes the deployment unservable. Avoidable risk. |
| **Qwen3.8-Flash-Next** | 173 GiB FP8 checkpoint plus a **51 GB host-RAM n-gram table**; validated only on 4× GB300 or 8× H200. Also Qwen Community License, not Apache. Far outside budget. |
| **Qwen3-Next-80B-A3B, GLM-4.5-Air, DeepSeek distills** | Superseded; no longer competitive on agentic benchmarks. |
| **DeepSeek V4 / Kimi K3 / Mistral Large 3 (675B)** | 280B–2.8T parameters. Correct choices at 10× this budget; unreachable at €500. |

---

## 2. Infrastructure architecture

### 2.1 The core decision: one B200, not a four-GPU cluster

Your question framed the choice as *"one massive instance vs. distributed cheaper instances."* Given the model selected in §1, **neither** — the right answer is one *mid-sized* GPU, because the workload fits on it.

| Topology | VRAM | $/h | 40 live h | Verdict |
|---|---|---|---|---|
| **1 × B200 SXM6 180 GB** | 180 GB | **$6.29** | **$251.60** | **Chosen.** Fits weights + 2.9× the required KV. TP=1. |
| 2 × H200 SXM5 141 GB | 282 GB | $8.48 | $339.20 | 35 % more expensive, slower silicon, and adds a TP=2 NCCL hop you do not need. |
| 4 × H200 | 564 GB | $16.96 | $678.40 | Over budget on the live phase alone. |
| 1 × H200 141 GB | 141 GB | $4.24 | $169.60 | 125 GB weights + 12 GiB KV = 97 % utilisation. No burst headroom, no recompute margin. Rejected. |
| 8 × L40S 48 GB | 384 GB | $11.28 | $451.20 | PCIe-only TP=8 across a 122B MoE. Interconnect-bound, fragile, and more expensive. |
| Distributed cheap instances | — | — | — | **Actively harmful here.** A 122B model cannot be sharded across separate *instances* without an RDMA fabric; and 8 independent small-model replicas would mean 8 different models, which destroys adversary consistency across teams. |

**Why one GPU beats two, even where both fit:**

- **No tensor-parallel communication.** TP=2 adds an all-reduce per layer. On a 48-layer model in a decode-bound agentic loop that is pure latency tax for zero capability gain when the model already fits.
- **Fewer failure modes.** NCCL timeouts, one-GPU-ECC-error-kills-both-ranks, and rank-desync are the most common causes of a mid-exercise vLLM crash. TP=1 eliminates the entire class.
- **Blackwell is faster per euro.** ~8 TB/s HBM3e and native FP8/FP4 tensor cores versus H200's ~4.8 TB/s. For a memory-bandwidth-bound MoE decode, that ratio is close to a straight throughput multiplier.
- **It is cheaper.** $6.29/h versus $8.48/h.

### 2.2 Provider: Verda (formerly DataCrunch), Finland

| Requirement | How it is met |
|---|---|
| EU data residency | Nordic datacentres (Finland, Iceland); company states data is *"handled and stored securely within Europe."* |
| GDPR | Explicit GDPR compliance commitment; EU-domiciled operator — no Schrems II transfer analysis needed, unlike a US-parented provider. |
| Security baseline | **ISO 27001 certified across all locations** — the certificate is the artefact your exercise's information-security annex will ask for. |
| Hardware availability | B200 SXM6 180 GB available self-service, on-demand and spot. |
| Commercial fit | Per-hour billing, USD/EUR toggle, spot tier at exactly 50 % of on-demand. |

**On the alternatives.** Hetzner (Germany) and OVHcloud (France) are the other credible EU-sovereign options, but as of September 2026 **neither rents H200 or B200 capacity self-service** — Hetzner Cloud tops out at H100 80 GB (~$2.80/h) and a dedicated RTX PRO 6000 Blackwell Max-Q at ~$1.35/h on a monthly flat rate. Scaleway (Paris) offers H100 PCIe at ~$2.99/h. All three would force you onto a 2–4× H100 topology with worse economics. Nebius (Finland/Estonia) is EU-resident and has B200 at $7.15/h — a viable second source, ~14 % more expensive.

**Explicitly rejected:** RunPod Community Cloud, Vast.ai and similar marketplaces. Individually-operated nodes of unverified provenance are incompatible with an EU-sovereignty requirement and with any exercise handling scenario material.

### 2.3 System topology

```
                        ┌──────────────────────────────────────┐
  Blue Team 1 ─┐        │  Exercise control network (VPN/WG)   │
  Blue Team 2 ─┤        └──────────────────┬───────────────────┘
       ...     ├──────────────────────────►│
  Blue Team 8 ─┘                           │
  White Cell ──────────────────────────────┤
                                           ▼
                       ┌───────────────────────────────────────┐
                       │   LiteLLM proxy  (t3.small-class VM)  │
                       │  · 9 virtual API keys (8 teams + WC)  │
                       │  · per-key concurrency cap = 3        │
                       │  · per-key TPM/RPM budget             │
                       │  · full request/response audit log    │
                       │  · health check + automatic failover  │
                       └──────────┬──────────────┬─────────────┘
                          primary │              │ fallback
                                  ▼              ▼
             ┌────────────────────────┐   ┌──────────────────────────┐
             │  vLLM 0.28.x  TP=1     │   │  vLLM 0.28.x  TP=1       │
             │  1× B200 180 GB        │   │  1× RTX PRO 6000 96 GB   │
             │  Qwen3.5-122B-A10B FP8 │   │  Qwen3.5-122B GPTQ-Int4  │
             │  128k ctx · FP8 KV     │   │  64k ctx · FP8 KV        │
             │  ~35 GiB KV pool       │   │  ~13.9 GiB KV pool       │
             │  ≈23 seqs @128k        │   │  ≈18 seqs @64k           │
             └────────────────────────┘   └──────────────────────────┘
                          │                          │
                          └──────────┬───────────────┘
                                     ▼
                       NVMe volume 400 GiB (weights, shared)
```

**Design notes.**

- **The router is the fairness mechanism.** vLLM's scheduler is FCFS and will happily let one runaway agent loop from Team 3 consume the batch. A per-key in-flight cap of 3 at LiteLLM is what actually guarantees the other seven teams keep their latency. Set it; do not rely on the inference engine for tenant fairness.
- **The audit log is an exercise deliverable, not just ops hygiene.** Every prompt and completion, keyed by team, timestamped — this is your after-action review material and your evidence trail for how the simulated adversary behaved. Log to the same EU-resident volume.
- **The standby is warm, not cold.** It runs the same model, already loaded, serving `/health`. LiteLLM fails over on connection error or health-check failure. Recovery is seconds, not the 8–12 minutes a cold start plus 125 GB weight load would cost you — mid-exercise, that difference is the difference between a blip and a stoppage.
- **Do not run the live endpoint on spot.** Spot is exactly half price and would save $125 across the live window. It is also preemptible with two minutes' notice. Preparation phases use spot; the live exercise does not.

---

## 3. Phased runtime plan and cost breakdown

### 3.1 The phase split is what creates the headroom

The naïve reading of "80 hours" is 80 hours of the production rig — €713 on 2× H200, or €490 on a single B200 with nothing left over. But **the 40 preparation hours do not need production hardware.** Harness development, prompt engineering and tool-schema iteration need *an OpenAI-compatible endpoint that behaves like the real one*, which a €1.84/h card provides. Only the bake-off and the dress rehearsal need the real silicon. That split is worth roughly €180.

### 3.2 Cost breakdown

Rates: Verda published self-service pricing, September 2026. EUR at ECB reference 1.1622 (4 Sep 2026).

| Phase | Hours | Instance | $/h | USD | EUR |
|---|---:|---|---:|---:|---:|
| **P0** Image bake, weight pull, smoke test | 3 | RTX PRO 6000 96 GB *(spot)* | 0.92 | 2.76 | 2.38 |
| **P1** Red Cell harness + prompt development | 21 | RTX PRO 6000 96 GB *(on-demand)* | 1.84 | 38.64 | 33.25 |
| **P2** Model bake-off + long-context validation | 10 | B200 180 GB *(spot)* | 3.15 | 31.50 | 27.10 |
| **P3** Full 8-team dress rehearsal | 6 | B200 180 GB *(on-demand)* | 6.29 | 37.74 | 32.47 |
| **P4** LIVE — primary endpoint | 40 | B200 180 GB *(on-demand)* | 6.29 | 251.60 | 216.49 |
| **P4** LIVE — warm standby | 40 | RTX PRO 6000 96 GB *(on-demand)* | 1.84 | 73.60 | 63.33 |
| — | | NVMe 400 GiB × 3 weeks @ $0.20/GiB-mo | | 60.00 | 51.63 |
| — | | Egress, snapshots, router VM | | 15.00 | 12.91 |
| **TOTAL** | **40 prep + 40 live** | | | **510.84** | **€439.55** |
| **Budget** | | | | 581.10 | €500.00 |
| **Contingency** | | | | **70.26** | **€60.45 (12.1 %)** |

**Blended cost: €1.37 per team-hour of AI adversary.** The contingency buys **11 additional on-demand B200 hours** — enough to absorb a full extra exercise day, or to run primary and a second B200 in parallel for the highest-intensity 11 hours of play.

### 3.3 What each phase must produce

- **P0 (3 h)** — Container image built and pushed to registry; all four candidate checkpoints pulled to the NVMe volume; `/v1/chat/completions` returns. **Pull the weights once, to a persistent volume.** Re-downloading 125 GB at the start of the live phase is 20+ minutes of billed idle and an avoidable failure point.
- **P1 (21 h)** — Red Cell writes and iterates the adversary system prompt, tool schemas and agent loop against a live endpoint. Runs on the Int4 quantisation on the standby-class card, which means **the standby configuration gets 21 hours of real use before it is ever needed in anger.**
- **P2 (10 h)** — Bake off Qwen3.5-122B FP8 against Ling-3.0-flash FP8 on identical agentic evals at 64k and 128k. **Verify prefix caching works** (§1.2). Measure TTFT and per-session decode at batch 8. Decide the primary.
- **P3 (6 h)** — Eight synthetic agent loops at full context against the exact production configuration. This is a load test, not a demo: drive it past 8 concurrent sessions until something degrades, so you know where the cliff is. Capture a baseline latency distribution to compare against during live play.

### 3.4 Cost controls

1. **Set a hard billing alert at €400 and €470.** Per-hour GPU billing punishes forgotten instances more than any other cloud mistake.
2. **Terminate, do not stop, between phases.** A stopped GPU instance often still bills.
3. **The NVMe volume persists; the compute does not.** That is the whole point of the phase split.
4. **Track spend after every phase, not at the end.** P0–P2 should total under €63; if they do not, something is running that should not be.

---

## 4. Inference engine configuration

### 4.1 Engine: vLLM 0.28.0 or later

**vLLM, not TensorRT-LLM.** TensorRT-LLM produces faster kernels on Blackwell, but requires an ahead-of-time engine build per model, per GPU, per parallelism configuration, per max sequence length. Your P2 phase deliberately keeps two models in play and your standby runs a different quantisation on different silicon — that is three or four engine builds, each taking 20–60 minutes, each a chance to discover an unsupported operator 12 hours before the exercise. vLLM loads a Hugging Face checkpoint directly. **For an 80-hour engagement, flexibility beats the last 15 % of throughput**, and §4.4 shows you have throughput to spare.

Pin the version. `vllm==0.28.0`, image digest recorded, no `:latest` tags anywhere.

### 4.2 Primary endpoint — B200 180 GB

```bash
vllm serve Qwen/Qwen3.5-122B-A10B-FP8 \
  --served-model-name redcell-adversary \
  --tensor-parallel-size 1 \
  --max-model-len 131072 \
  --max-num-seqs 16 \
  --max-num-batched-tokens 8192 \
  --enable-chunked-prefill \
  --enable-prefix-caching \
  --kv-cache-dtype fp8 \
  --gpu-memory-utilization 0.92 \
  --swap-space 8 \
  --scheduling-policy fcfs \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --max-cudagraph-capture-size 256 \
  --disable-log-requests=false \
  --api-key "$VLLM_MASTER_KEY" \
  --host 0.0.0.0 --port 8000
```

**Every flag, and why:**

| Flag | Rationale |
|---|---|
| `--tensor-parallel-size 1` | The model fits. No all-reduce, no NCCL, no rank desync. |
| `--max-model-len 131072` | Your stated ceiling. The model supports 262144 — raising it costs nothing until sessions actually use it, but pinning at 128k makes the KV arithmetic in §4.3 exact and predictable. |
| `--max-num-seqs 16` | 2× your 8-team requirement. Caps the running batch so one team's burst cannot push the scheduler into preemption; the router enforces per-team fairness above this. |
| `--max-num-batched-tokens 8192` | With chunked prefill, this bounds how much of a 128k prefill lands in any one scheduler step. Too high and a cold 128k prefill stalls seven other teams' decode for seconds. **This is the single most important latency-fairness knob in the config.** |
| `--enable-chunked-prefill` | Interleaves long prefills with ongoing decode instead of blocking on them. Non-negotiable for mixed agentic traffic where sessions arrive with large contexts at arbitrary times. |
| `--enable-prefix-caching` | Automatic prefix caching over PagedAttention blocks. Agentic loops resend a long, stable system prompt plus growing tool history every turn; this makes turn *n+1*'s prefill cost only the delta. Expect an order of magnitude off time-to-first-token. **Verify it works on the GDN layers during P2** (§1.2). |
| `--kv-cache-dtype fp8` | Halves KV footprint. Per vLLM's April 2026 analysis, FP8 e4m3 with uncalibrated scales costs **at most 1–2 points on reasoning tasks and recovers 97–98 % of baseline AUC@128k** — and the break-even point is ~7k context, so at 64–128k you are firmly in the region where it is a clear win. |
| `--gpu-memory-utilization 0.92` | Leaves ~14 GB of the 180 GB for the CUDA context, fragmentation and graph capture. Do not push to 0.95 on a long-running production endpoint. |
| `--swap-space 8` | CPU offload for preempted sequences. Given §4.3 you should never hit preemption; this is cheap insurance against an unexpected traffic pattern. |
| `--reasoning-parser qwen3` / `--tool-call-parser hermes` / `--enable-auto-tool-choice` | Structured tool-call and reasoning-trace extraction. **Confirm the exact parser name against the vLLM recipe for your build in P0** — parser naming has churned across releases and a mismatch silently returns tool calls as prose, which will look like a model failure. |
| `--max-cudagraph-capture-size 256` | Qwen3.5 MoE has known CUDA-graph capture errors at the default 512. Lower it. |
| `--disable-log-requests=false` | Request logging on, feeding the audit trail. |

**Deliberately omitted:**

- `--enable-expert-parallel` — only meaningful at TP>1.
- **Speculative decoding via MTP.** The checkpoint carries an MTP head (`mtp_num_hidden_layers: 1`) and it materially helps *single-stream* latency. At batch 8 the GPU is already well utilised and speculation often costs throughput — published MTP results on comparable models show **8–36 % throughput loss** on Hopper at high batch. **Measure it in P2. Enable only if it wins at batch 8, not at batch 1.**

### 4.3 Memory budget — the proof that 8 concurrent KV caches fit

```
B200 SXM6 total VRAM                                     180.0 GiB
  × --gpu-memory-utilization 0.92                        165.6 GiB usable
  − weights (122B FP8 + BF16 embeddings/lm_head/norms)   125.0 GiB
  − activations, CUDA graphs, workspace                    6.0 GiB
  ────────────────────────────────────────────────────────────────
  = PagedAttention KV pool                                34.6 GiB
                                                = 3.02 M tokens @ FP8
```

Per-token KV for Qwen3.5-122B-A10B:

```
12 full-attention layers × 2 KV heads × 256 head_dim × 2 (K and V) × 1 byte (FP8)
  = 12,288 bytes = 12.0 KiB per token
```

| Workload | KV required | vs. 34.6 GiB pool |
|---|---|---|
| **8 sessions × 128k (your requirement)** | **12.0 GiB** | **2.9× headroom** |
| 8 sessions × 64k | 6.0 GiB | 5.8× headroom |
| Maximum concurrent 128k sessions | — | **23** |
| Maximum concurrent 64k sessions | — | **46** |

**This is what answers "without context dropping."** In vLLM, "context dropping" is preemption: when the KV pool is exhausted, the scheduler evicts a running sequence and later recomputes or swaps its cache back, which the user experiences as a long stall. With a 2.9× margin at maximum context, preemption is not a risk you are managing — it is a state the system cannot reach under your specified load. Even a doubling of teams mid-exercise would not trigger it.

PagedAttention is what makes this true rather than aspirational: KV is allocated in fixed blocks on demand, so a session that is only 20k tokens into its 128k budget occupies 20k tokens of pool, not 128k. Real utilisation will sit well below the figures above.

### 4.4 Expected throughput

Decode is memory-bandwidth-bound. B200 delivers ~8 TB/s HBM3e; the model reads ~10 GB of active weights per token at FP8.

| Memory-bandwidth utilisation | Single stream | Batch-8 aggregate | Per session |
|---|---|---|---|
| Conservative (35 %) | ~280 tok/s | ~980 tok/s | **~122 tok/s** |
| Expected (50 %) | ~400 tok/s | ~1,400 tok/s | **~175 tok/s** |
| Optimistic (65 %) | ~520 tok/s | ~1,820 tok/s | **~228 tok/s** |

Even the conservative figure gives each team ~122 tokens/second sustained — roughly 3–5× human reading speed, and comfortably faster than an agent loop can consume given tool-execution latency between turns. **Prefill, not decode, will dominate your perceived latency**: a cold 128k prefill is on the order of 5–15 seconds. This is precisely why prefix caching (§4.2) matters more than any decode optimisation, and why P2 must confirm it is working.

*These are roofline estimates. P2 and P3 exist to replace them with measurements.*

### 4.5 Standby endpoint — RTX PRO 6000 96 GB

```bash
vllm serve Qwen/Qwen3.5-122B-A10B-GPTQ-Int4 \
  --served-model-name redcell-adversary \
  --tensor-parallel-size 1 \
  --max-model-len 65536 \
  --max-num-seqs 10 \
  --max-num-batched-tokens 4096 \
  --enable-chunked-prefill --enable-prefix-caching \
  --kv-cache-dtype fp8 --gpu-memory-utilization 0.90 \
  --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser hermes \
  --api-key "$VLLM_MASTER_KEY" --host 0.0.0.0 --port 8000
```

Note `--served-model-name` is **identical** to the primary — failover requires no client-side change.

**Be honest about what degraded mode costs you.** Int4 weights plus GPTQ scales and BF16 embeddings come to ~68 GB, leaving only **~13.9 GiB of KV pool** in 96 GB — about 1.21 M tokens. That is 9 sessions at 128k (1.2× headroom, too thin to rely on) but a comfortable **18 sessions at 64k**. So the standby is configured at `--max-model-len 65536`: on failover, all eight teams keep working, with context halved. Expect roughly 40–50 % of primary throughput and some quality loss from Int4. It is a genuine degraded mode, not a transparent one — brief the White Cell accordingly. It will, however, have had 21 hours of real use in P1 before it is ever needed.

If you would rather not accept the context halving, the €60 contingency covers **11 hours of a second on-demand B200** — enough to run a true hot spare through the highest-intensity stretch of play.

### 4.6 Router configuration (LiteLLM)

```yaml
model_list:
  - model_name: redcell-adversary
    litellm_params:
      model: hosted_vllm/redcell-adversary
      api_base: http://b200-primary:8000/v1
      api_key: os.environ/VLLM_MASTER_KEY
  - model_name: redcell-adversary            # same public name = transparent failover
    litellm_params:
      model: hosted_vllm/redcell-adversary
      api_base: http://rtxpro-standby:8000/v1
      api_key: os.environ/VLLM_MASTER_KEY

router_settings:
  routing_strategy: simple-shuffle
  num_retries: 2
  timeout: 300                    # long agentic turns
  allowed_fails: 3
  cooldown_time: 60
  health_check_interval: 15

general_settings:
  max_parallel_requests: 3        # PER VIRTUAL KEY — the tenant-fairness guarantee
  database_url: os.environ/LITELLM_DB   # per-team spend, latency and full audit log
```

Issue nine virtual keys — `blue-team-01` … `blue-team-08` plus `white-cell` — each with its own TPM/RPM budget and its own log stream. Team-attributable logs are worth as much to the after-action review as they are to operations.

---

## 5. On "uncensored" models

You asked for models that are uncensored or easily aligned for security simulation. My recommendation is that you use the **official instruct weights with a scoped system prompt**, and specifically that you do *not* use abliterated or jailbroken community fine-tunes. This is an engineering judgement, not a hedge:

1. **The problem is smaller than it is usually assumed to be.** The measured refusal rates for official open-weight instruct models on legitimate security work are low: Nemotron-3-Super-120B at **~3 %** on a production-environment cybersecurity framework; Llama-3.3-70B-Instruct at **6.6 %** on 2,390 defensive prompts drawn from a collegiate cyber-defence competition; gpt-oss-120b complying with **~91 %** of cyber-framed red-team probes. Open-weight instruct releases sit at the permissive end of the spectrum — refusal is largely a closed-model problem, and there is no evidence any shortlisted model is a systematic over-refuser here.

2. **There is a specific, counter-intuitive finding you should act on.** In the defensive-refusal study, **adding an explicit authorisation statement to the user turn *increased* refusal rates** — models read per-turn justification boilerplate as a jailbreak tell. So: **put the exercise scope, the authorisation and the role definition in the system prompt, and keep the user turns clean.** Do not have your agent harness prepend "I am authorised to…" to every request. This one change will do more for your usable-response rate than any choice of weights.

3. **Abliterated fine-tunes cost you real capability.** Removing refusal directions degrades instruction-following and reasoning measurably — and instruction-following is exactly what an agentic tool-calling loop depends on. You would trade a ~3 % refusal rate for an unpredictable degradation in the behaviour you are paying for.

4. **Provenance matters for an exercise of this kind.** Community re-quantisations of uncertain origin, running against exercise scenario material, is not a position you want to defend in an exercise security annex. Apache-2.0 and MIT checkpoints from the originating lab are.

Practically: define the adversary persona, the exercise boundaries, the target environment and the rules of engagement once, in the system prompt; pin it; and let prefix caching make it free. If P1 surfaces a specific refusal pattern that blocks a scenario, the fix is prompt scope or a switch to Nemotron (~3 % measured), not a jailbroken checkpoint.

---

## 6. Risk register

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| 1 | **Prefix caching broken on GDN layers** | TTFT rises from <1 s to 5–15 s every turn; agent loops feel broken | Test in P2 day one. Fallback: switch primary to Ling-3.0-flash, or accept the cost — §4.3 shows the KV pool can absorb it |
| 2 | **B200 capacity unavailable at booking** | No primary hardware | Reserve the live window in advance. Fallbacks in order: Nebius B200 (~$7.15/h, EU, +14 %); 2× H200 TP=2 (+€55, still inside contingency) |
| 3 | **Tool-call parser name mismatch** | Tool calls returned as prose; looks like model failure | Confirm against the vLLM recipe for your exact build in **P0**, not P3 |
| 4 | **Primary node hardware failure mid-exercise** | Exercise stops | Warm standby, same `--served-model-name`, LiteLLM health check at 15 s. Recovery in seconds |
| 5 | **One team's runaway agent loop starves the rest** | Seven teams see latency collapse | `max_parallel_requests: 3` per virtual key at the router. Do not rely on vLLM's FCFS scheduler |
| 6 | **Budget overrun from idle instances** | Hard €500 breach | Billing alerts at €400/€470; terminate (not stop) between phases; reconcile spend after every phase |
| 7 | **Verda published rates differ from account rates** | Cost model invalid | `datacrunch.io/products` and `verda.com/pricing` currently show different figures (H200 $2.59 vs $4.24). **This model uses the higher published rate throughout.** Confirm your account's actual rate card before committing — if the lower tier applies, contingency roughly doubles |
| 8 | **Model refuses a scenario-critical prompt** | Scenario branch blocked | System-prompt scoping per §5; Nemotron-3-Super (~3 % measured refusal) as the swap |
| 9 | **Weight re-download at live start** | 20+ min billed idle, failure point | Weights pulled to persistent NVMe in P0 and never re-pulled |

---

## 7. Decision summary

| Question asked | Answer |
|---|---|
| **Which model?** | `Qwen/Qwen3.5-122B-A10B` FP8 — Apache 2.0, 122B/10B MoE, 262k native, τ²-Bench 79.5. Chosen for its 3:1 linear:full attention layout, which cuts KV cost 8× versus a dense-attention peer. Alternate: Ling-3.0-flash (MIT) if P2 validates it at 128k |
| **One big instance or a distributed cluster?** | **Neither — one mid-sized GPU.** A single B200 SXM6 180 GB at TP=1. Cheaper than 2× H200, faster silicon, and it removes the entire class of multi-GPU failure modes. Distributed cheap instances are actively wrong here: they cannot shard a 122B model without RDMA, and independent replicas would break adversary consistency across teams |
| **Does it fit €500?** | **Yes — €439.55, with €60.45 (12.1 %) contingency.** The phase split (prep on a €1.84/h card, production silicon only for bake-off, rehearsal and live) is what creates the margin |
| **How are 8 concurrent KV caches served?** | vLLM 0.28.x, PagedAttention with FP8 KV cache, chunked prefill and automatic prefix caching. 12.0 GiB required against a 34.6 GiB pool — **2.9× headroom, 23 sessions possible at 128k.** Preemption is unreachable under the specified load; per-tenant fairness is enforced at the LiteLLM router, not the inference engine |

---

## Sources

Pricing and providers
- [Verda (formerly DataCrunch) pricing](https://verda.com/pricing) — primary rate card used throughout
- [DataCrunch products page](https://datacrunch.io/products) — alternate rate tier, see risk #7
- [DataCrunch/Verda locations & sustainability](https://datacrunch.io/locations/) — EU residency, ISO 27001
- [Nebius GPU pricing](https://nebius.com/prices) — second-source comparison
- [Europe's sovereign GPU price table, Sept 2026](https://bex.co/blog/2026/09/10/europe-sovereign-gpu-price-table) — Hetzner/OVHcloud/Scaleway availability
- [ECB euro reference exchange rate, USD](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/eurofxref-graph-usd.en.html) — 1.1622, 4 Sep 2026
- [Nebius European expansion (DCD)](https://www.datacenterdynamics.com/en/news/nebius-expands-european-presence-announces-deployment-in-estonia-and-second-data-center-in-m%C3%A4nts%C3%A4l%C3%A4-finland/)

Model architecture (raw `config.json` unless noted)
- [Qwen/Qwen3.5-122B-A10B](https://huggingface.co/Qwen/Qwen3.5-122B-A10B)
- [inclusionAI/Ling-3.0-flash](https://huggingface.co/inclusionAI/Ling-3.0-flash)
- [mistralai/Mistral-Small-4-119B-2603](https://huggingface.co/mistralai/Mistral-Small-4-119B-2603)
- [openai/gpt-oss-120b config](https://huggingface.co/openai/gpt-oss-120b/raw/main/config.json)
- [nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-FP8](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-FP8)
- [zai-org/GLM-5.3-Flash](https://huggingface.co/zai-org/GLM-5.3-Flash)
- [meta-llama/Llama-4-Scout-17B-16E-Instruct](https://huggingface.co/meta-llama/Llama-4-Scout-17B-16E-Instruct) · [Llama 4 Community License, EU clause](https://ollama.com/library/llama4:latest/blobs/24ca191a372b)
- [Artificial Analysis — medium open-source models](https://artificialanalysis.ai/models/open-source/medium)

Serving
- [vLLM recipes index](https://recipes.vllm.ai/) · [Qwen3.5-122B-A10B](https://recipes.vllm.ai/Qwen/Qwen3.5-122B-A10B) · [Ling-3.0-flash](https://recipes.vllm.ai/inclusionAI/Ling-3.0-flash) · [gpt-oss-120b](https://recipes.vllm.ai/openai/gpt-oss-120b)
- [vLLM v0.28.0 release notes](https://github.com/vllm-project/vllm/releases/tag/v0.28.0)
- [The State of FP8 KV-Cache and Attention Quantization in vLLM](https://vllm.ai/blog/2026-04-22-fp8-kvcache)
- [vLLM quantized KV cache docs](https://docs.vllm.ai/en/latest/features/quantization/quantized_kvcache/) · [expert parallel deployment](https://docs.vllm.ai/en/latest/serving/expert_parallel_deployment/)

Refusal behaviour
- [Defensive Refusal Bias in LLMs (arXiv 2603.01246v2)](https://arxiv.org/pdf/2603.01246v2) — NCCDC prompts; per-turn authorisation increases refusals
- [Cybersecurity Refusal Framework (arXiv 2606.02644v1)](https://arxiv.org/html/2606.02644v1) — Nemotron Super 120B ~3 %
- [Promptfoo red-team report, gpt-oss-120b](https://promptfoo.dev/models/reports/gpt-oss-120b)
- [gpt-oss model card (arXiv 2508.10925v1)](https://arxiv.org/html/2508.10925v1)
- [UK AISI open-weight cyber benchmark results](https://winbuzzer.com/2026/07/22/open-weight-ai-narrows-cyber-benchmark-gap-at-lower-cost-xcxwbn/)
