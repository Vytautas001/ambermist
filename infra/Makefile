# =============================================================================
# Verda Red Cell infrastructure
#
#   make p0 / p1 / p2 / p3 / p4   bring up a phase
#   make live4                    4-team variant of p4, one node held a week
#   make off                      destroy all GPUs and project OS-volume orphans
#   make status                   what is running and what it costs
#   make orphans                  find volumes nobody is paying attention to
# =============================================================================
TF      ?= tofu               # set TF=terraform to use HashiCorp Terraform instead
PHASES  := p0 p1 p2 p4 live4 off
VARFILE  = phases/$(word 1,$(wildcard phases/$(PHASE)-*.tfvars phases/$(PHASE).tfvars))
TF_ARGS ?=

# Pass launch-time machine/location choices as make variables, e.g.:
#   make p1 location=FIN-02 phase_node_sku=1H200.141S.44V phase_node_tp=1
TF_VAR_ARGS = $(if $(location),-var=location=$(location)) \
              $(if $(phase_node_sku),-var=phase_node_sku=$(phase_node_sku)) \
              $(if $(phase_node_tp),-var=phase_node_tp=$(phase_node_tp)) \
              $(if $(phase_node_spot),-var=phase_node_spot=$(phase_node_spot)) \
              $(if $(node_sku),-var=node_sku=$(node_sku)) \
              $(if $(node_tp),-var=node_tp=$(node_tp)) \
              $(if $(secondary_node_sku),-var=secondary_node_sku=$(secondary_node_sku)) \
              $(if $(secondary_node_tp),-var=secondary_node_tp=$(secondary_node_tp))
PREFLIGHT_TARGET_SKU = $(if $(phase_node_sku),$(phase_node_sku),$(node_sku))
PREFLIGHT_TARGET_TP  = $(if $(phase_node_tp),$(phase_node_tp),$(node_tp))
PREFLIGHT_ARGS = $(if $(PREFLIGHT_TARGET_SKU),--target-sku $(PREFLIGHT_TARGET_SKU)) \
                 $(if $(PREFLIGHT_TARGET_TP),--target-tp $(PREFLIGHT_TARGET_TP)) \
                 $(if $(location),--target-location $(location))

.PHONY: help init fmt validate $(PHASES) plan-% status fleet cost off orphans reap-orphans clean provision-secrets

help:
	@grep -E '^[a-z0-9-]+:.*?##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | column -t -s "$$(printf '\t')"

init: ## terraform init
	@if find .terraform/providers -type f -name 'terraform-provider-verda*' -print -quit 2>/dev/null | grep -q .; then \
		echo "Using locally installed Verda provider"; \
		$(TF) init -plugin-dir=.terraform/providers $(TF_ARGS); \
	else \
		$(TF) init $(TF_ARGS); \
	fi

fmt: ## rewrite HCL to canonical format
	$(TF) fmt -recursive

validate: ## syntax + type check (no credentials needed)
	$(TF) validate

check-creds:
	@test -n "$$VERDA_CLIENT_ID"     || { echo "ERROR: VERDA_CLIENT_ID not set";     exit 1; }
	@test -n "$$VERDA_CLIENT_SECRET" || { echo "ERROR: VERDA_CLIENT_SECRET not set"; exit 1; }

# ---- phase targets ----------------------------------------------------------
p0: ## bake: pull weights to the persistent volume (launch-time GPU)
	@$(MAKE) apply PHASE=p0
p1: ## devel: Red Cell harness + prompts (launch-time GPU)
	@$(MAKE) apply PHASE=p1
p2: ## bake-off: 3 models at 64k/128k (10h, 2x RTXPRO spot)
	@$(MAKE) apply PHASE=p2
p4: ## LIVE: rehearsal + exercise, 2 nodes active-active, held 46h
	@$(MAKE) apply PHASE=p4
live4: ## LIVE, 4 teams: 1x RTXPRO Int4 at 128k, held a week (168h)
	@$(MAKE) apply PHASE=live4

off: ## destroy all GPUs, keep the weights volume
	@$(MAKE) apply PHASE=off
	@$(MAKE) reap-orphans

plan-%: check-creds
	$(TF) plan -var-file=$$(ls phases/$**.tfvars) $(TF_VAR_ARGS) $(TF_ARGS)

preflight: check-creds ## is the target SKU actually in stock right now?
	@python3 ../ops/preflight.py $(PREFLIGHT_ARGS)

watch: check-creds ## poll availability every 15 min and log history (run this from now until the exercise)
	@python3 ../ops/preflight.py --watch 900

apply: check-creds
	@if [ "$(PHASE)" != "off" ]; then \
	  test -n "$$HF_TOKEN" || { echo "ERROR: HF_TOKEN not set; source ../.env first"; exit 1; }; \
	  test -n "$$VLLM_API_KEY" || { echo "ERROR: VLLM_API_KEY not set; source ../.env first"; exit 1; }; \
	fi
	@echo "=== phase $(PHASE) ==="
	@echo "--- capacity preflight ---"
	@python3 ../ops/preflight.py $(PREFLIGHT_ARGS) || { \
	  echo; echo "Preflight did not return the design target."; \
	  echo "Set phase_node_sku/phase_node_tp/location to an available target, or read docs/CAPACITY-RUNBOOK.md."; \
	  read -p "Continue anyway? [y/N] " a; [ "$$a" = "y" ] || exit 1; }
	$(TF) apply -var-file=$$(ls phases/$(PHASE)*.tfvars) $(TF_VAR_ARGS) $(TF_ARGS)
	@TF="$(TF)" python3 ../ops/provision_node_secrets.py
	@$(TF) output -raw burn_warning 2>/dev/null && echo

provision-secrets: ## securely copy root .env inference credentials to serving nodes
	@TF="$(TF)" python3 ../ops/provision_node_secrets.py

status: ## show instances, fleet cap usage and cost projection
	@$(TF) output -json instances 2>/dev/null | python3 -m json.tool || echo "no instances"
	@$(TF) output -json fleet 2>/dev/null | python3 -m json.tool || true
	@$(TF) output -json cost 2>/dev/null | python3 -m json.tool || true
	@$(TF) output -raw burn_warning 2>/dev/null && echo

fleet: ## GPU fleet cap usage only (the binding constraint - no budget ceiling)
	@$(TF) output -json fleet | python3 -m json.tool

cost: ## cost projection only (informational - not a gate)
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

reap-orphans: check-creds ## delete detached redcell OS volumes left by destroyed instances
	@python3 ../ops/cleanup_orphans.py --project redcell --yes

images: ## list image identifiers available to your account
	@TOKEN=$$(curl -sS -X POST https://api.verda.com/v1/oauth2/token \
	   -H 'Content-Type: application/json' \
	   -d "{\"grant_type\":\"client_credentials\",\"client_id\":\"$$VERDA_CLIENT_ID\",\"client_secret\":\"$$VERDA_CLIENT_SECRET\"}" \
	   | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'); \
	 curl -sS -H "Authorization: Bearer $$TOKEN" https://api.verda.com/v1/images | python3 -m json.tool

clean:
	rm -rf .terraform *.tfplan
