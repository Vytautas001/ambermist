# Capacity runbook

**The risk this exists for:** the Verda deploy console shows **B200, B300, GB300,
L40S, A100-40GB and RTX A6000 as "No availability"**. Capacity is checked at
deploy time and an exhausted pool returns HTTP 503. Nothing reserves a GPU for
you except occupying it.

**Hardware policy — a hard fleet cap, not a cost one:** never more than
**1x H200 + 1x H100 + 2x RTX PRO 6000 (GPUs)** running at once. Nothing above
H200 (B200, B300, GB300) and no other family (A100, L40S, ...) either, even if
it comes back into stock. There is **no monetary budget ceiling** — the fleet
cap is what's enforced. `terraform_data.fleet_guard` in `infra/instances.tf`
refuses an apply that exceeds `local.fleet_limit` in `infra/locals.tf`; see
`AGENTS.md` for the full policy.

---

## 1. The five things that actually reduce this risk

| # | Control | Why it works |
|---|---|---|
| 1 | **Never plan on an out-of-stock or out-of-policy SKU** | Only H200, H100 and RTX PRO 6000 SKUs belong in `local.node_ladder`, and only at counts that fit `local.fleet_limit`. A `lifecycle precondition` refuses to apply against any SKU marked `stock = false` in `local.catalog`, and `terraform_data.fleet_guard` refuses one that exceeds the fleet cap regardless of stock. |
| 2 | **Acquire once, hold through** | Rehearsal and live are one 46-hour phase on the same instances. There is no re-acquisition on exercise morning, which is the moment you cannot afford a 503. |
| 3 | **Primary + standby, both serving** | Losing the primary degrades to the standby rather than stopping the exercise. They're separate instances *and* separate hardware families (see §2), so a single host fault or a single family's stock-out takes only one of them. |
| 4 | **Build on the most-available SKU** | RTX PRO 6000 showed 1x/2x/4x/8x in stock and fits the checkpoint. Abundance beats peak FLOPS when the alternative is nothing. |
| 5 | **Preflight before every apply** | `make preflight` hits `/v1/instance-availability` (requires `VERDA_CLIENT_ID`/`VERDA_CLIENT_SECRET` — Verda now rejects it unauthenticated, unlike earlier docs) and walks the ladder. `make watch` polls every 15 min and logs history, so you learn the daily availability rhythm *before* it matters. |

**Do this today, it costs nothing:**

- **Fund the account.** The console shows `Project balance: €0.00`. A zero balance will stop a launch just as dead as an empty pool, and it is the more embarrassing of the two.
- Start `make watch` and leave it running until the exercise. A week of history tells you whether capacity frees up overnight, on weekends, or not at all.

---

## 2. Fleet cap and the primary/standby ladder

The fleet cap leaves no room for two same-family nodes at once, so the P4
topology is **asymmetric on purpose**: a primary that holds the full FP8
checkpoint, and a standby on a *different* family running the smaller Int4
checkpoint at halved context. That's `phases.p4` in `infra/locals.tf`
(`node_sku`/`node_tp` for the primary, `secondary_node_sku`/`secondary_node_tp`
for the standby), and `router/litellm.config.yaml` fails over between them —
it does **not** load-balance across them, because they're not equivalent
capacity. Routing teams evenly across a full-context FP8 node and a
halved-context Int4 node would silently hand some teams a worse session under
normal load, not just under failure.

### Primary ladder

`make preflight` walks this automatically and prints the highest rung in
stock. Every rung here uses its whole family's fleet allowance on its own — an
RTX PRO 6000 primary and an H200 or H100 primary can never run as two full-size
same-family nodes together; a different family covers the standby role instead.

| Rung | SKU | GPUs | VRAM | €/h/node | Fits full FP8 (8×128k)? | Consequence |
|---|---|---:|---:|---:|---|---|
| **1 (target)** | `2RTXPRO6000.60V` | 2 | 192 GB | 3.170 | Yes, 2.9× KV headroom | - |
| **2** | `1H200.141S.44V` | 1 | 141 GB | 3.728 | **No** | Int4 or gpt-oss only |
| **3** | `1H100.80S.30V` | 1 | 80 GB | 2.841 | **No** | tightest fit; Int4 or gpt-oss only, unbenchmarked |

Only rung 1 holds the full FP8 checkpoint at 8×128k. Dropping to rung 2 or 3
means switching `primary_model` to the Int4 checkpoint and accepting a smaller
`primary_max_model_len` — this is the same trade the standby node already
makes by default, just applied to the primary too.

> **Keep the code in sync.** `make preflight` walks `LADDER` in
> `ops/preflight.py`; `local.node_ladder` in `infra/locals.tf` must match.
> `ops/preflight.py`'s `OVER_FLEET_CAP` set lists SKUs that are in-stock-capable
> but exceed the fleet cap (`2H200.141S.88V`, `4RTXPRO6000.120V`,
> `2H100.80S.80V`, `4H100.80S.176V`) — do not add these to the ladder even if
> Verda shows them available.

Changing the primary rung is two variables:

```bash
terraform apply -var-file=phases/p4-live.tfvars \
  -var='node_sku=1H200.141S.44V' -var='node_tp=1' \
  -var='primary_model=Qwen/Qwen3.5-122B-A10B-GPTQ-Int4' \
  -var='weights_footprint_gb=68' -var='primary_max_model_len=65536'
```

The preconditions refuse the apply if the SKU is out of stock, if the fleet
cap is exceeded, or if the VRAM cannot hold `weights_footprint_gb + kv_footprint_gb`.

### Standby node

Default `secondary_node_sku=1H200.141S.44V`, running `standby_model` (Int4) at
`standby_max_model_len` (65,536) — deliberately smaller than the primary's
context, not a matched pair. If you change the primary's family, pick a
*different* family for the standby so the fleet cap still allows both at once
(e.g. primary on RTX PRO 6000 + standby on H200, or primary on H200 + standby
on H100 — never two RTX PRO 6000 nodes or two H200 nodes together).

### 4-team variant: `make live4`

With 4 teams instead of 8, KV drops from 12 GiB to **6 GiB** (4 × 128k × 12 KiB).
One `1RTXPRO6000.30V` (68 GB Int4 weights, ~13.9 GiB KV pool) serves all four
teams at the **full 128k** with 2.3× headroom, using only 1 of the fleet's 2
RTX PRO 6000 GPUs — the H200 and H100 stay free for other work in parallel.

`live4` is a single one-GPU allocation, so it is the easiest setup to acquire
days early and **hold for the whole week**. The trade: Int4 quality becomes the
primary, and there is no second node. Point the router's `NODE_B_URL` at the
same endpoint as `NODE_A_URL`.

---

## 3. If nothing on the ladder is available

Work down this list. Do **not** sit in a retry loop — capacity does not arrive
because you asked twice.

1. **Check per-location.** The console filter defaults to *All locations*, but
   query `/v1/instance-availability` per site: Verda has `FIN-01`, `FIN-02` and
   `FIN-03`. Stock differs between them and the aggregate view can hide a site
   that has what you need. `make preflight` prints the locations per rung.
2. **Take fewer GPUs per node, more nodes.** `1RTXPRO6000.30V` (TP=1, 96 GB)
   running the **Int4** checkpoint behind the router is a smaller allocation
   than `2RTXPRO6000.60V` — easier to satisfy from a fragmented pool, at the
   cost of Int4 quality. Still counts against the 2-GPU RTX PRO 6000 cap.
3. **Second provider.** Nebius (Finland, and Hüüru near Tallinn) is EU-resident
   and lists H100/H200 — the same hardware policy and fleet cap apply there.
   **Create and fund that account NOW**, before you need it — signup plus
   funding plus quota on exercise morning is not a plan. Only the Terraform is
   Verda-specific; the model, vLLM config and KV arithmetic port unchanged.
4. **Shrink the model, keep the exercise.** `gpt-oss-120b` in MXFP4 is 61 GB of
   weights and 18 GB of KV for 8×128k — **79 GB**, which fits a *single*
   `1RTXPRO6000.30V` at 96 GB. It is the most-available card in the catalogue in
   its smallest unit. Weaker adversary, but the exercise runs.
5. **Long-term contract.** The console offers 1 month (−2%) through 2 years
   (−25%), and a commitment does hold capacity. One month of `2RTXPRO6000.60V`
   is 730 h × €3.170 × 0.98 ≈ **€2,268** per node. It is the only option that
   guarantees capacity, so book it well ahead if availability is volatile.

---

## 4. Degraded-mode decision tree (White Cell)

Stated RTO: **10–15 minutes is survivable.** Everything below fits inside that.

```
Endpoint failing?
├─ Primary down, standby healthy
│    Router fails over automatically (health check every 15s).
│    Context and quality drop to the standby: standby_max_model_len (Int4),
│    not just slower - a real capability downgrade, not only throughput.
│    ACTION: tell teams to expect a smaller context window and weaker
│    completions. Do not pause.
│    Then: make preflight && terraform apply -var-file=phases/p4-live.tfvars
│           to rebuild the lost primary. ~10 min (weights already on the volume).
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
- [ ] Nebius account created **and funded** as the escape hatch
- [ ] All candidate checkpoints on the weights volume (`make p0` done)

**T-1 day**

- [ ] `make preflight` returns rung 1 for the primary (or a rung 2/3 you have load-tested)
- [ ] `make p4` applied — **primary and standby up and held from here on**
- [ ] Dress rehearsal run on those exact instances, 8 synthetic agent loops
- [ ] Baseline latency distribution captured for comparison during live play
- [ ] Router failover tested by killing the primary *for real*

**T-0**

- [ ] Both nodes healthy, `make status` clean (check the `fleet` output too — should read `rtxpro: 2, h200: 1, h100: 0, blocked: 0`)
- [ ] **Do not `make off`.** The instances stay up until the exercise ends.
- [ ] Runbook open, White Cell briefed on degraded mode

**After**

- [ ] `make off`
- [ ] `make orphans` — destroying an instance does **not** delete its OS volume
