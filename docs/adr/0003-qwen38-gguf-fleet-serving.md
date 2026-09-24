# ADR 0003 — Qwen3.8 GGUF on independently qualified fleet replicas

Status: accepted design; implementation and capacity qualification pending.
Date: 2026-09-24.
Supersedes: ADR 0001's model selection and ADR 0002's Qwen3.5 primary/standby
serving topology and automatic smaller-context failover. Keeps ADR 0002's hardware
cap/no-budget decision and ADR 0001's Verda/Finland and local-state decisions.

## Context

The operator selected Qwen3.8-Flash-Next-Abliterated-GGUF and requested an
architecture/instructions update, leaving coding to another LLM. The deployment
spec already identified the publisher and corrected Q4_K_M artifact, while the
architecture and assistant instructions still prescribed Qwen3.5 and obsolete
hardware/capacity claims. The current code has not migrated.

The allowed fleet is one H200, one H100, and two RTX PRO 6000 GPUs. The session
policy remains provisional ceilings of 4/1/2 per GPU at 131,072 total tokens per
slot. Eight team generations need combined replica capacity; neither a four-slot
H200 nor four RTX slots alone satisfies the target. Two single-GPU RTX nodes fit
the cap, contrary to the older blanket statement about same-family nodes.

## Decisions

1. Select `windowsxp811203/Qwen3.8-Flash-Next-Abliterated-GGUF`, Q4_K_M, revision
   `c3365c410baa29bdd3d7cc8cbc2bf9bee0de2f3a`, with the hash recorded in the
   [deployment spec](../MODEL-DEPLOYMENT-SPEC.md). No silent publisher/quant swap.
2. Use llama.cpp CUDA with immutable, compatibility-tested runtime pins. Begin
   with CPU PLE placement, GPU transformer layers and F16 caches. Fit and latency
   remain hypotheses until measured on each allowed SKU.
3. Target independent H200, H100, and two RTX replicas of the same artifact and
   context. Prefer separate single-RTX hosts; a held dual-RTX host may use two
   isolated processes after combined host-resource qualification.
4. Admit at most the smaller of the policy ceiling and matching measured
   comfortable capacity. Begin trials at one slot, stop at the current ceiling,
   and report a tested bound. No unrestricted concurrency discovery in this scope.
5. Preserve 129,024 input plus 2,048 output tokens per slot, including reasoning.
   On host loss replay the full transcript to an eligible replica or queue/pause.
   Qwen3.5 at 64k remains an explicit rollback service, with a distinct alias.
6. Separate machine acquisition from mutable serving manifests. Retain held
   machines and protected volumes through model changes and rehearsal/live.
   Reconcile actual provider inventory as well as planned Terraform roles.
7. Centralize assistant instructions in `AGENTS.md`; make `CLAUDE.md` an entry
   point to that file. Provide an ordered, testable coding handoff for GPT/Codex
   or another assistant. This change implements documentation only.

## Consequences

- Nine provisional slots offer one spare slot at eight-team load, not full N+1
  protection. H200 loss leaves at most five; one RTX loss leaves seven; H100 loss
  leaves eight. A dual-RTX host loss removes four slots together.
- GGUF CPU offload makes host RAM, memory bandwidth, and storage page residency
  part of capacity qualification. Aggregate fleet VRAM is not pooled memory.
- The inherited Qwen Community License replaces the old Apache-only rationale;
  participant-service applicability must be assessed and recorded.
- Current Terraform/router/server defaults are migration work, not an executable
  representation of this decision. See [the handoff](../IMPLEMENTATION-HANDOFF.md).
- Model feasibility may fail on one or more permitted GPUs. Report reduced
  concurrency explicitly; neither extra hardware nor a context cut is implied.
