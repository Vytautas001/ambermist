# infra — Terraform / OpenTofu

The exercise's inference infrastructure on Verda: a primary + standby vLLM node
pair (different hardware families, so failover — not equal load-balancing — see
`../router/litellm.config.yaml`), a persistent weights volume, and a phased
lifecycle that holds capacity through rehearsal into the live exercise, all
capped at a fixed GPU fleet: never more than 1x H200 + 1x H100 + 2x RTX PRO 6000
(GPUs). There is no monetary budget ceiling.

See the repo root for the full picture:
- `../docs/ARCHITECTURE.md` — the design and the reasoning
- `../docs/CAPACITY-RUNBOOK.md` — what to do when the SKU is out of stock
- `../CLAUDE.md` — constraints that must not be silently violated

## Use

```bash
cp terraform.tfvars.example terraform.tfvars   # your operators, region
export VERDA_CLIENT_ID=... VERDA_CLIENT_SECRET=...

make preflight     # is the target SKU in stock right now?
make p0            # pull weights (once)
make p2            # bake-off nodes
make p4            # rehearsal + live, held
make live4         # 4 teams: one RTX PRO 6000 Int4 at 128k, held a week
make off           # only after the exercise
make orphans       # OS volumes survive instance deletion — check for strays
```

Provider gotchas, the phase model and the fleet guard are documented in
`../docs/` and enforced by preconditions in `instances.tf`.

## P1 serving checks

After each `make p0`, `make p1`, `make p2`, `make p4`, or `make live4`, the
apply target reads `HF_TOKEN` and `VLLM_API_KEY` from the exported repository
root `.env` and sends them over SSH to every active serving node. It atomically
writes `/mnt/weights/.env` as root with mode `0600`, then restarts vLLM if the
credentials changed or the service is inactive. The credentials travel through
SSH stdin and do not enter Terraform inputs, state, command arguments, or
startup-script content. Ensure the operator SSH key is available to `ssh` (for
example through `ssh-agent`) and source the repository root `.env` before
running infra Make targets.

The weights volume is persistent across phase changes, so the installed file
survives node replacement. Each serving node is provisioned after apply, before
the Make target returns. To resync credentials later, run `make provision-secrets`
from `infra/` with the root `.env` sourced.

Both the bake step and serving container use `HF_HUB_CACHE=/weights/hf-cache`.
With vLLM v0.28.0, omit the removed `--swap-space` flag and use `qwen3_coder`
for Qwen3.5 tool calls (see the
[vLLM recipe](https://github.com/vllm-project/recipes/blob/main/Qwen/Qwen3.5.md)).

After `/health` is ready, run this from the repository root, substituting the
node IP and operator key:

```bash
ssh -i /path/to/operator-key root@NODE_IP \
  'set -a; . /mnt/weights/.env; set +a; python3 -' < ops/check_inference.py
```

This checks that unauthenticated discovery returns 401, the authenticated model
list contains `redcell-adversary`, an arithmetic completion returns `42`, and
an automatic tool call parses correctly. It does not execute the requested tool.
Credentials stay on the node, and the script prints only validation results.
These are serving smoke checks, not the P2 long-context or concurrency bake-off.

Startup scripts run only at initial provisioning. Template changes must also be
installed into the existing node's systemd service and followed by
`systemctl daemon-reload` and `systemctl restart redcell-vllm`. Keep the acquired
P1 instance: applying a changed immutable startup script is not an in-place
service repair. The original service can be backed up before replacing it.

Validated on 2026-09-24: the existing FIN-02 H200 (TP=1) served
`Qwen/Qwen3.5-122B-A10B-GPTQ-Int4` with vLLM v0.28.0 at a 65,536-token
configured context limit. All four smoke checks passed; the arithmetic request
used 26 prompt tokens and 3 completion tokens and completed in 0.29 seconds.
The weights snapshot was `30cd92cba9707a9aba09d1e490ed4b66b78e9606`.
Long-context performance and multi-team concurrency remain untested.
