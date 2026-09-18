# =============================================================================
# Verda Red Cell infrastructure
#
#   make p0 / p1 / p2 / p3 / p4   bring up a phase
#   make off                      destroy all GPUs, keep the weights volume
#   make status                   what is running and what it costs
#   make orphans                  find volumes nobody is paying attention to
# =============================================================================
TF      ?= terraform          # set TF=tofu to use OpenTofu
PHASES  := p0 p1 p2 p4 off
VARFILE  = phases/$(word 1,$(wildcard phases/$(PHASE)-*.tfvars phases/$(PHASE).tfvars))

.PHONY: help init fmt validate $(PHASES) plan-% status cost off orphans clean

help:
	@grep -E '^[a-z0-9-]+:.*?##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | column -t -s "$$(printf '\t')"

init: ## terraform init
	$(TF) init

fmt: ## rewrite HCL to canonical format
	$(TF) fmt -recursive

validate: ## syntax + type check (no credentials needed)
	$(TF) validate

check-creds:
	@test -n "$$VERDA_CLIENT_ID"     || { echo "ERROR: VERDA_CLIENT_ID not set";     exit 1; }
	@test -n "$$VERDA_CLIENT_SECRET" || { echo "ERROR: VERDA_CLIENT_SECRET not set"; exit 1; }

# ---- phase targets ----------------------------------------------------------
p0: ## bake: pull weights to the persistent volume (3h, RTXPRO spot)
	@$(MAKE) apply PHASE=p0
p1: ## devel: Red Cell harness + prompts (21h, RTXPRO spot)
	@$(MAKE) apply PHASE=p1
p2: ## bake-off: 3 models at 64k/128k (10h, 2x RTXPRO spot)
	@$(MAKE) apply PHASE=p2
p4: ## LIVE: rehearsal + exercise, 2 nodes active-active, held 46h
	@$(MAKE) apply PHASE=p4

off: ## destroy all GPUs, keep the weights volume
	@$(MAKE) apply PHASE=off

plan-%: check-creds
	$(TF) plan -var-file=$$(ls phases/$*[-.]*.tfvars)

preflight: ## is the target SKU actually in stock right now?
	@python3 ../ops/preflight.py

watch: ## poll availability every 15 min and log history (run this from now until the exercise)
	@python3 ../ops/preflight.py --watch 900

apply: check-creds
	@echo "=== phase $(PHASE) ==="
	@echo "--- capacity preflight ---"
	@python3 ../ops/preflight.py || { \
	  echo; echo "Preflight did not return the design target."; \
	  echo "Set node_sku/node_tp to an available rung, or read docs/CAPACITY-RUNBOOK.md."; \
	  read -p "Continue anyway? [y/N] " a; [ "$$a" = "y" ] || exit 1; }
	$(TF) apply -var-file=$$(ls phases/$(PHASE)[-.]*.tfvars)
	@$(TF) output -raw burn_warning 2>/dev/null && echo

status: ## show instances and cost projection
	@$(TF) output -json instances 2>/dev/null | python3 -m json.tool || echo "no instances"
	@$(TF) output -json cost 2>/dev/null | python3 -m json.tool || true
	@$(TF) output -raw burn_warning 2>/dev/null && echo

cost: ## cost projection only
	@$(TF) output -json cost | python3 -m json.tool

# ---- safety nets ------------------------------------------------------------
orphans: ## list volumes in the account; anything not in state is billing silently
	@test -n "$$VERDA_CLIENT_ID" || { echo "set VERDA_CLIENT_ID/SECRET first"; exit 1; }
	@TOKEN=$$(curl -sS -X POST https://api.verda.com/v1/oauth2/token \
	   -H 'Content-Type: application/json' \
	   -d "{\"grant_type\":\"client_credentials\",\"client_id\":\"$$VERDA_CLIENT_ID\",\"client_secret\":\"$$VERDA_CLIENT_SECRET\"}" \
	   | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'); \
	 echo "--- all volumes in account ---"; \
	 curl -sS -H "Authorization: Bearer $$TOKEN" https://api.verda.com/v1/volumes | python3 -m json.tool; \
	 echo; echo "--- volumes known to terraform ---"; \
	 $(TF) state list 2>/dev/null | grep -E 'verda_volume\.' || echo "(none)"; \
	 echo; echo "Destroying an instance does NOT delete its OS volume. Anything above"; \
	 echo "that terraform does not know about is costing you money."

images: ## list image identifiers available to your account
	@TOKEN=$$(curl -sS -X POST https://api.verda.com/v1/oauth2/token \
	   -H 'Content-Type: application/json' \
	   -d "{\"grant_type\":\"client_credentials\",\"client_id\":\"$$VERDA_CLIENT_ID\",\"client_secret\":\"$$VERDA_CLIENT_SECRET\"}" \
	   | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'); \
	 curl -sS -H "Authorization: Bearer $$TOKEN" https://api.verda.com/v1/images | python3 -m json.tool

clean:
	rm -rf .terraform *.tfplan
