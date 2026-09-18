# infra — Terraform / OpenTofu

The exercise's inference infrastructure on Verda: two active-active vLLM nodes,
a persistent weights volume, and a phased lifecycle that holds capacity through
rehearsal into the live exercise.

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
make off           # only after the exercise
make orphans       # OS volumes survive instance deletion — check for strays
```

Provider gotchas, the phase model and the budget guard are documented in
`../docs/` and enforced by preconditions in `instances.tf`.
