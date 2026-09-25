# ADR 0005 — Development scope: maximum sessions per node, Qwen3.5 archived

Status: accepted.
Date: 2026-09-25.
Amends: [ADR 0003](0003-qwen38-gguf-fleet-serving.md). It removes the eight-team
service target and the requirement to keep Qwen3.5 as the rollback. The hardware
cap from [ADR 0002](0002-fleet-cap-no-budget.md) stays.

## Context

The legacy Qwen3.5/vLLM infrastructure has been destroyed. The documents still
told coding agents two things that no longer fit:

- **Qwen3.5 rollback.** Keep a running Qwen3.5 service and roll back to it
  (`redcell-qwen35`, vLLM adapter, restore after a failed start). No such service
  is left to restore.
- **Eight-team target.** Route eight full-context team conversations and rehearse
  eight-team readiness across the whole fleet.

The operator runs Qwen3.8 as separate single-node attempts
([one-click plan](../QWEN38-ONE-CLICK-PLAN.md)), and qualifies nodes one at a
time ([experiment plan](../QWEN38-EXPERIMENT-PLAN.md)).

## Decisions

1. **Target: the maximum number of sessions per node.** Each allowed node serves
   the most full-context sessions it qualifies for, up to its policy ceiling:
   H200 4, H100 1, RTX PRO 6000 2 per GPU. There is no team-count target.
   Unchanged:
   - slot size: 131,072 tokens;
   - admission: `min(policy, qualified)`;
   - the fleet cap.
2. **The eight-team target is out of scope for now.** It is deferred. Do not
   build or accept work against it:
   - eight-team routing,
   - the eight-team readiness gate,
   - N+1 failover arithmetic,
   - the combined-fleet rehearsal.

   Bringing it back needs a new operator decision.
3. **Qwen3.5 is archived.** The legacy Qwen3.5/vLLM code stays in the repository,
   unchanged, as a reference:
   - `infra/scripts/startup.sh.tftpl`;
   - the phases `p0`, `p1`, `p2`, `p4` and `live4`;
   - `router/litellm.config.yaml`.

   It is not a rollback target. New work must not:
   - build a vLLM adapter, a `redcell-qwen35` alias or a Qwen3.5 rollback profile;
   - try to restore Qwen3.5.

   Deleting the archived code also needs an explicit request.
4. **Reviving Qwen3.5 later takes an explicit operator decision.** When it
   happens, Qwen3.5 comes back as its own profile with its own context contract.
   It is never an automatic fallback for a Qwen3.8 conversation.
5. **Qwen3.8 failure handling.** A failed Qwen3.8 start leaves the node stopped
   or failed, and the operator is told. Nothing falls back to another model. The
   weights volume and other staged artifacts are kept.

## Consequences

- Qualification and admission still matter. They are measured and enforced per
  node, and the evidence tables track each node rather than a combined fleet.
- A node that fails qualification serves nothing. No older model covers for it.
- When this ADR is in force, historical experiment documents that mention
  stopping or restoring `redcell-vllm` are records of past runs, not
  instructions.
