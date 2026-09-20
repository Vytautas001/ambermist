# Capacity runbook

**The risk this exists for:** the Verda deploy console shows **B200, B300, GB300,
L40S, A100-40GB and RTX A6000 as "No availability"**. Capacity is checked at
deploy time and an exhausted pool returns HTTP 503. Nothing reserves a GPU for
you except occupying it.

---

## 1. The five things that actually reduce this risk

| # | Control | Why it works |
|---|---|---|
| 1 | **Never plan on an out-of-stock SKU** | B200 is absent from `local.node_ladder` on purpose. A `lifecycle precondition` refuses to apply against any SKU marked `stock = false` in `local.catalog`. |
| 2 | **Acquire once, hold through** | Rehearsal and live are one 46-hour phase on the same instances. There is no re-acquisition on exercise morning, which is the moment you cannot afford a 503. |
| 3 | **Two independent nodes, both serving** | Losing one degrades throughput; it does not stop the exercise. They are separate instances, so a single host fault takes one. |
| 4 | **Build on the most-available SKU** | RTX PRO 6000 showed 1x/2x/4x/8x in stock and is the cheapest that fits the checkpoint. Abundance beats peak FLOPS when the alternative is nothing. |
| 5 | **Preflight before every apply** | `make preflight` hits `/v1/instance-availability` (requires `VERDA_CLIENT_ID`/`VERDA_CLIENT_SECRET` — Verda now rejects it unauthenticated, unlike earlier docs) and walks the ladder. `make watch` polls every 15 min and logs history, so you learn the daily availability rhythm *before* it matters. |

**Do this today, it costs nothing:**

- Click **"Notify me"** on B200, B300 and GB300 in the console.
- **Fund the account.** The console shows `Project balance: €0.00`. A zero balance will stop a launch just as dead as an empty pool, and it is the more embarrassing of the two.
- Start `make watch` and leave it running until the exercise. A week of history tells you whether capacity frees up overnight, on weekends, or not at all.

---

## 2. Fallback ladder

`make preflight` walks this automatically and prints the highest rung in stock.

| Rung | SKU | GPUs | VRAM | €/h/node | Nodes affordable | Total € | Headroom € | Throughput | Consequence |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| **1 (target)** | `2RTXPRO6000.60V` | 2 | 192 GB | 3.170 | 2 | 410.55 | +89.45 | ~78 tok/s/node, ~156 with both | - |
| **2** | `2H200.141S.88V` | 2 | 282 GB | 7.456 | 1 | 461.89 | +38.11 | ~210 tok/s, NVLink | **single node - no redundancy** |
| **3** | `4RTXPRO6000.120V` | 4 | 384 GB | 6.340 | 1 | 410.55 | +89.45 | ~157 tok/s (PCIe TP=4, optimistic) | single node; same cost as rung 1 but no redundancy |
| **4** | `1H200.141S.44V` | 1 | 141 GB | 3.728 | 2 | 461.89 | +38.11 | ~65 tok/s/node | **FP8 will not fit** - Int4 or gpt-oss only |

**Verified by `tofu plan`:** two nodes at rung 2 projects **€792.37** and the budget precondition refuses the apply. Rung 2 means **one** node — you trade redundancy for speed. Rung 3 at one node costs the same as rung 1 at two nodes, so only choose it if you actually need 384 GB.

Changing rung is two variables:

```bash
terraform apply -var-file=phases/p4-live.tfvars \
  -var='node_sku=2H200.141S.88V' -var='node_tp=2'
```

The preconditions will refuse the apply if the SKU is marked out of stock or if
the VRAM cannot hold `weights_footprint_gb + kv_footprint_gb`. If you drop to
rung 4, set `weights_footprint_gb=68`, `kv_footprint_gb=12` and
`primary_model=Qwen/Qwen3.5-122B-A10B-GPTQ-Int4` in the same apply.

---

## 3. If nothing on the ladder is available

Work down this list. Do **not** sit in a retry loop — capacity does not arrive
because you asked twice.

1. **Check per-location.** The console filter defaults to *All locations*, but
   query `/v1/instance-availability` per site: Verda has `FIN-01`, `FIN-02` and
   `FIN-03`. Stock differs between them and the aggregate view can hide a site
   that has what you need. `make preflight` prints the locations per rung.
2. **Take fewer GPUs per node, more nodes.** Four `1RTXPRO6000.30V` (TP=1, 96 GB
   each) running the **Int4** checkpoint behind the router is four small
   allocations instead of two large ones — materially easier to satisfy from a
   fragmented pool, at the cost of Int4 quality.
3. **Second provider.** Nebius (Finland, and Hüüru near Tallinn) is EU-resident
   and had B200 listed. **Create and fund that account NOW**, before you need it —
   signup plus funding plus quota on exercise morning is not a plan. Only the
   Terraform is Verda-specific; the model, vLLM config and KV arithmetic port
   unchanged.
4. **Shrink the model, keep the exercise.** `gpt-oss-120b` in MXFP4 is 61 GB of
   weights and 18 GB of KV for 8×128k — **79 GB**, which fits a *single*
   `1RTXPRO6000.30V` at 96 GB. It is the most-available card in the catalogue in
   its smallest unit. Weaker adversary, but the exercise runs.
5. **Long-term contract.** The console offers 1 month (−2%) through 2 years
   (−25%), and a commitment does hold capacity. Costed out it is not viable here:
   one month of `2RTXPRO6000.60V` is 730 h × €3.170 × 0.98 ≈ **€2,268**, against a
   €500 ceiling. Listed for completeness so nobody re-derives it under pressure.

---

## 4. Degraded-mode decision tree (White Cell)

Stated RTO: **10–15 minutes is survivable.** Everything below fits inside that.

```
Endpoint failing?
├─ One node down, other healthy
│    Router fails over automatically (health check every 15s).
│    Throughput halves: ~156 -> ~78 tok/s per session.
│    ACTION: tell teams to expect slower responses. Do not pause.
│    Then: make preflight && terraform apply -var-file=phases/p4-live.tfvars
│           to rebuild the lost node. ~10 min (weights already on the volume).
│
├─ Both nodes down, capacity available
│    ACTION: tactical pause. terraform apply. ~10-15 min.
│    Weights are on the shared NVMe volume, so this is a boot plus model load,
│    not a 125 GB download.
│
├─ Both nodes down, NO capacity on any rung
│    ACTION: pause. Work section 3 in order. Announce a scenario hold.
│    If step 4 (gpt-oss-120b on one small card) works, resume degraded and tell
│    White Cell the adversary is weaker - injects may need manual reinforcement.
│
└─ Model serving but refusing / looping / producing nonsense
     NOT a capacity problem. Check the system prompt first (per-turn
     authorisation boilerplate measurably increases refusals - keep it in the
     system prompt only), then journalctl -u redcell-vllm.service.
```

---

## 5. Pre-exercise checklist

**T-7 days**

- [ ] `make watch` running, history accumulating
- [ ] Account funded — balance is **not** €0.00
- [ ] "Notify me" set on B200/B300/GB300
- [ ] Nebius account created **and funded** as the escape hatch
- [ ] All candidate checkpoints on the weights volume (`make p0` done)

**T-1 day**

- [ ] `make preflight` returns rung 1
- [ ] `make p4` applied — **both nodes up and held from here on**
- [ ] Dress rehearsal run on those exact instances, 8 synthetic agent loops
- [ ] Baseline latency distribution captured for comparison during live play
- [ ] Router failover tested by killing node B *for real*

**T-0**

- [ ] Both nodes healthy, `make status` clean
- [ ] **Do not `make off`.** The instances stay up until the exercise ends.
- [ ] Runbook open, White Cell briefed on degraded mode

**After**

- [ ] `make off`
- [ ] `make orphans` — destroying an instance does **not** delete its OS volume
- [ ] Reconcile actual spend against the projection
