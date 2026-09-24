# ADR 0004 — Relax the hardware allow-list for the Qwen3.8 GGUF primary

Status: **Rejected** (2026-09-24, operator — B200 too expensive). The
[ADR 0002](0002-fleet-cap-no-budget.md) / [ADR 0003](0003-qwen38-gguf-fleet-serving.md)
allow-list stands unchanged: no B200, no relaxation. B200 stays
`family = "blocked"` in `local.catalog`. This ADR is retained as the record of the
decision so it is not re-litigated.

## Resolution

The operator considered relaxing the allow-list to add a larger primary and
**declined on cost**: `1B200.30V` (€5.532/gpu/h) is ~48% more than the held
`1H200.141S.44V` (€3.728/gpu/h), and the only in-family alternative,
`2H200.141S.88V`, is more expensive still and exceeds the one-H200 cap. There is
no cheaper way to add single-GPU VRAM above 141 GB in the Verda/EU catalog.

Consequence: the **111 GiB Q4_K_M artifact is served on the existing capped fleet**
as already designed — on-GPU on the H200, and via CPU-PLE offload on the H100 and
RTX replicas (marginal; qualified per node). No fleet table, `local.catalog`,
`local.fleet_limit`, or `terraform_data.fleet_guard` change is required. The rest
of the corpus is already consistent with this outcome.

## Context

The selected artifact `windowsxp811203/Qwen3.8-Flash-Next-Abliterated-GGUF`
(`Q4_K_M`) is **111 GiB** of weights. Under the current allow-list (H200/H100/RTX
PRO 6000 only), that alone exceeds H100 (80 GB) and RTX (96 GB) single-GPU VRAM:
only the H200 (141 GB) holds it on-GPU, and the H100/RTX replicas depend on CPU-PLE
offload and are marginal (the deployment spec's own memory sensitivity puts the
H100 at 0–1 slots). See [MODEL-DEPLOYMENT-SPEC.md](../MODEL-DEPLOYMENT-SPEC.md) §4
and [ARCHITECTURE.md](../ARCHITECTURE.md) §2/§4.

The operator briefly considered relaxing the hardware allow-list so the primary
would run with real long-context KV headroom rather than marginally, then declined
on cost (see Resolution above). This ADR records that the option was weighed and
rejected; no `local.catalog`, `local.fleet_limit`, or `terraform_data.fleet_guard`
change follows from it.

## Options considered

| Option | VRAM | Cost (Verda) | Verdict |
|---|---:|---|---|
| Keep the cap; offload on H100/RTX | H200 141 GB single-GPU; H100/RTX via CPU-PLE | held H200 €3.728/gpu/h | **Chosen** — no new spend, already designed for |
| Add `1B200.30V` (B200 SXM6) primary | 180 GB single-GPU, no offload | €5.532/gpu/h (~48% more than H200) | Rejected — too expensive |
| `2H200.141S.88V` (2×H200, TP=2) | 282 GB | €7.456/gpu/h | Rejected — more expensive still, and exceeds the one-H200 cap |

Had a relaxation been accepted it would have required a coordinated change to
`local.catalog`, `local.fleet_limit`, `terraform_data.fleet_guard`,
`ops/preflight.py`, `local.node_ladder`, and the fleet tables across AGENTS.md,
README, the spec, and the runbook. None of that is needed, because the cap is
retained. Capacity remains a qualification gate regardless of hardware
(ADR 0003 decision 4); the H100 and RTX replicas still qualify per node with the
offload profile in [MODEL-DEPLOYMENT-SPEC.md](../MODEL-DEPLOYMENT-SPEC.md) §4.
