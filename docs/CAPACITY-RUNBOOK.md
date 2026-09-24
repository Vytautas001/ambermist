# Capacity runbook

Status: operating requirements for the selected Qwen3.8 design; implementation
migration and qualification pending. Updated 2026-09-24. Read
[ARCHITECTURE.md](ARCHITECTURE.md) and [the handoff](IMPLEMENTATION-HANDOFF.md).

## 1. Constraints and current state

There is no monetary budget ceiling. Never hold more than **1x H200 + 1x H100 +
2x RTX PRO 6000 GPUs** at once, including experiments, other states, and replacement
overlap. Only the exact SKUs in [AGENTS.md](../AGENTS.md) are allowed. Availability
of B200/B300/GB300, A100, L40S, or a larger same-family allocation changes nothing.

The target is Qwen3.8 Abliterated Q4_K_M on independently qualified llama.cpp
replicas, with 131,072 tokens per slot. Provisional ceilings are H200 4, H100 1,
and RTX 2 per GPU. No ceiling is a capacity result. GPU stock, model feasibility,
and comfortable serving capacity are three separate checks.

Current `make p4` still requests the legacy Qwen3.5 dual-RTX primary/H200 standby.
`make live4` still configures a legacy single-RTX service with excessive sequence
limits relative to the new policy. Neither target implements this architecture.
Do not run them to obtain a Qwen3.8 deployment. The old smaller-context automatic
fallback is not the desired recovery procedure.

## 2. Inventory and preflight before acquisition

From the repository root, load credentials without printing them:

```bash
set -a
source .env
set +a
```

Before any provider apply:

1. Check the actual project balance and provider account inventory. Record held
   GPU instances, SKU, site, ownership/state, and service role. Do not assume an
   old document's balance or availability is current.
2. Reconcile the desired plan against **all** held GPUs and replacement overlap.
   `terraform_data.fleet_guard` checks selected roles, not unmanaged resources or
   other states. No plan may temporarily exceed the cap. Coordinate acquisitions
   through one operator; local state alone cannot serialize another applier.
3. Query the exact allowed SKU and Finnish site. `ops/preflight.py` uses Verda's
   authenticated availability API; an available result is not a reservation.
   Its current default ladder and model-fit messages are legacy. Its explicit
   target branch does not yet enforce the complete allowlist/count policy, so
   independently check those constraints until the handoff fix is implemented.
4. Inspect plan changes for instance/volume replacement. Keep the held H200 and
   protected weights volume. A model switch must not require reacquisition.
5. Confirm actual host RAM, GPU memory, disk space, CUDA compatibility, and storage
   attachments. Missing RAM does not authorize a larger SKU. Keep live capacity
   on-demand and held continuously through rehearsal and the exercise.

A current read-only availability query, after the root `.env` is sourced:

```bash
python3 ops/preflight.py \
  --target-sku 1H200.141S.44V --target-tp 1 --target-location FIN-02
```

Substitute only an allowed SKU and intended Finnish site. For the desired
single-RTX nodes use `1RTXPRO6000.30V`, TP=1; for H100 use `1H100.80S.30V`, TP=1.
A held dual-RTX allocation consumes **both** RTX allowances. The existing TP
argument describes acquisition metadata; it does not configure the planned two
independent llama.cpp processes on that host.

`make watch` can record availability history, and `make fleet` shows Terraform's
current phase counts. Neither proves complete account inventory or reserves GPUs.

## 3. Acquisition and qualification order

| Step | Allowed allocation | Qualification task |
|---|---|---|
| 1 | Reuse the recorded H200 if still held | Confirm N=1, then 2, 3, 4 within policy |
| 2 | Up to two single-RTX nodes | Confirm N=1 then 2 on each actual deployment |
| 3 | One H100 | Confirm fit and comfortable service at N=1 |
| Alternative for step 2 | One dual-RTX host | Two isolated replicas; test both under combined host load |

This order prioritizes technical qualification. Acquire scarce allowed capacity
early when available; do not release it merely because its trial is later.
Choose sites compatible with the held storage and network. Do not destroy a held
dual-RTX node merely to obtain the preferred two-host failure layout.

If a requested allocation is unavailable, check the other permitted Finnish
sites and the alternative RTX packaging, accounting for storage location and
failure domains. Keep existing capacity. Do not substitute another family,
provider, model, or context silently. If the allowed fleet cannot be acquired or
qualified, report the shortfall and use a declared queued/reduced-concurrency
exercise plan or a scenario hold. There is no hardware expansion escape hatch.

## 4. Model changes on held nodes

The new `model_deploy.py` commands are planned, not available yet. Follow the
implementation handoff before treating them as operational commands.

The required lifecycle is:

1. Snapshot the current service manifest/runtime identity and keep its rollback
   artifacts and credentials available. Inspect remaining storage in bytes.
2. Stage the pinned GGUF and runtime; verify hashes, tensor metadata, host RAM,
   GPU placement plan, and disk margin before draining the current replica.
3. Drain and stop only the selected replica's old GPU process. Start the candidate
   on a localhost test listener using the protected environment file.
4. Check authentication, discovery, template/tokenizer, arithmetic, and stub tool
   parsing. Confirm exact context per slot and tensor placement in loader logs.
5. Qualify the exact profile; only then admit normal traffic within measured and
   policy limits. On failure restore the previous manifest and service.

Switching a full H200 from Qwen3.5 to Qwen3.8 may require a service interruption;
there is no spare second H200 for simultaneous migration. Coordinate the drain
with the exercise operator. Do not fit both runtimes by shrinking the context.

The existing weights volume uses NFS (`NVMe_Shared`). Measure concurrent load and
CPU-PLE residency; do not assume local-NVMe performance or cross-site attachment.
Retain the protected volume and avoid concurrent cache writes. Any separate
storage migration needs a reviewed copy/verification plan.

## 5. Failure handling during rehearsal/live

First remove the unhealthy replica from admission and compute the remaining
**qualified** capacity. Notify White Cell of queued or paused generations.

| Event | Ceiling capacity remaining | Required response |
|---|---:|---|
| H100 lost | 8 | Recover affected transcript to eligible capacity; no spare slot |
| One single-RTX host lost | 7 | Queue/pause at least one of eight team generations |
| H200 lost | 5 | Queue/pause at least three team generations |
| Dual-RTX host lost | 5 | Both RTX replicas are gone; queue/pause accordingly |
| Model unhealthy or unqualified | Subtract its measured slots | Drain, diagnose, and explicitly restore a verified profile |
| Router/shared storage/control network lost | Potentially all | Scenario hold; recover the shared dependency |

The table assumes all provisional ceilings qualified beforehand. Actual remaining
capacity may be lower. Replay full transcripts only to healthy same-artifact,
same-context replicas with admission space; replay has a cold-prefill cost. Do
not blindly retry partial completions or completed tool actions.

Never send a 128k conversation to the old Qwen3.5/64k service as automatic
failover. If the operator selects that rollback service, announce the model and
context change and use a separate workload/new session or explicit compaction
policy. Record the decision in the audit trail.

A 10–15 minute recovery objective is a planning target to rehearse, not a measured
promise. Recheck inventory and preflight before replacing a failed machine; do
not assume its GPU allocation has been released or exceed the cap during overlap.

## 6. Readiness and shutdown

Before rehearsal/live:

- [ ] Actual inventory fits the hardware cap; balance and per-site stock checked
- [ ] Selected runtime, artifact, and profile identities are immutable and verified
- [ ] Exact per-replica reports support the desired full-context concurrency
- [ ] Combined eight-team test passes, or reduced concurrency is explicitly agreed
- [ ] Router/server/team limits agree; White Cell consumes the same capacity pool
- [ ] Host-loss and shared-dependency recovery rehearsed with complete transcripts
- [ ] Scope/stub behavior, authenticated endpoints, EU audit retention verified
- [ ] Model license applicability to the participant arrangement recorded
- [ ] Rollback artifacts remain accessible; held machines survive serving changes

Keep acquired machines through rehearsal and live. Do not use `make off` between
those stages. After the operator ends the exercise, release intended instances
and inspect detached OS volumes (`make orphans`) without deleting the protected
weights or required audit records. Confirm cleanup against real inventory.
