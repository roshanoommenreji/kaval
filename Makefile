SHELL := /bin/bash
.DEFAULT_GOAL := help

-include .env
export

AWS_PROFILE ?= kaval
AWS_REGION  ?= ap-south-1
TF_PROD     := infra/envs/prod
TF_LAB      := infra/envs/lab

# ─────────────────────────────────────────────────────────────
##@ Help

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nKaval — make targets\n\n"} \
	  /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 } \
	  /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) }' $(MAKEFILE_LIST)
	@echo ""

# ─────────────────────────────────────────────────────────────
##@ Local development  (costs nothing)

.PHONY: dev
dev: ## Start the local stack (Ollama + Gemma, Postgres, gateway)
	docker compose up -d --build
	@echo "gateway    http://localhost:8000/docs"
	@echo "postgres   localhost:5432"

.PHONY: dev-down
dev-down: ## Stop the local stack
	docker compose down

.PHONY: logs
logs: ## Tail local stack logs
	docker compose logs -f --tail=100

.PHONY: test
test: ## Unit tests + policy tests
	pytest services/ -q
	@command -v opa >/dev/null 2>&1 && opa test policy/ -v || echo "opa not installed — skipping policy tests"

.PHONY: lint
lint: ## Lint and type-check
	ruff check services/
	mypy services/

.PHONY: build
build: ## Build arm64 images (Graviton — amd64 will NOT run on the node)
	docker buildx build --platform linux/arm64 -t kaval/gateway:dev  services/gateway
	docker buildx build --platform linux/arm64 -t kaval/agent:dev    services/agent
	docker buildx build --platform linux/arm64 -t kaval/executor:dev services/executor
	docker buildx build --platform linux/arm64 -t kaval/collector:dev services/collector

# ─────────────────────────────────────────────────────────────
##@ AWS  (starts and stops billing — read docs/cost/budget-plan.md)

.PHONY: guardrails
guardrails: ## Provision ONLY the budget alarms. Run this before anything else, ever.
	cd $(TF_PROD) && terraform init && terraform apply -target=module.budget

.PHONY: plan
plan: ## Show what would change in prod (never applies)
	cd $(TF_PROD) && terraform plan

.PHONY: up
up: ## Provision the spot node and reconcile from Git (~5 min, STARTS BILLING)
	@echo "This starts billing at roughly \$$0.0126/hr plus storage."
	@read -p "Continue? [y/N] " ok && [ "$$ok" = "y" ]
	cd $(TF_PROD) && terraform apply
	@echo "Waiting for Flux to reconcile..."
	@echo "Check with: kubectl get pods -A"

.PHONY: down
down: ## Destroy the node, keep EBS/ECR/S3 state (~\$2/mo parked)
	cd $(TF_PROD) && terraform destroy -target=module.node
	@echo "Node destroyed. State preserved. 'make up' restores in ~5 min."

.PHONY: nuke
nuke: ## Destroy EVERYTHING in prod including state. Irreversible.
	@echo "This destroys all persistent data including the incident history."
	@read -p "Type 'nuke' to confirm: " ok && [ "$$ok" = "nuke" ]
	cd $(TF_PROD) && terraform destroy

# ─────────────────────────────────────────────────────────────
##@ EKS lab  (Phase 8 — ephemeral, ~\$4/session)

.PHONY: lab-up
lab-up: ## Stand up real EKS to prove chart portability
	cd $(TF_LAB) && terraform init && terraform apply

.PHONY: lab-down
lab-down: ## Destroy the EKS lab. ALWAYS run this. Verify with cost-report tomorrow.
	cd $(TF_LAB) && terraform destroy

# ─────────────────────────────────────────────────────────────
##@ Data

.PHONY: backup
backup: ## Dump the prod database to S3 (RPO 24h — see ADR-0005)
	@bash scripts/backup.sh

.PHONY: restore-staging
restore-staging: ## Seed staging from the latest sanitised prod snapshot
	@bash scripts/restore.sh staging

.PHONY: restore-prod
restore-prod: ## DISASTER RECOVERY. Replaces the live database. Prompts.
	@bash scripts/restore.sh prod

# ─────────────────────────────────────────────────────────────
##@ Cost

.PHONY: cost-report
cost-report: ## Month-to-date AWS spend against the \$25 ceiling
	@bash scripts/cost-report.sh

.PHONY: cost-check
cost-check: ## Fail loudly if MTD spend exceeds the ceiling
	@bash scripts/cost-report.sh --assert

# ─────────────────────────────────────────────────────────────
##@ Chaos and verification

.PHONY: chaos-run
chaos-run: ## Inject a failure. Usage: make chaos-run EXPERIMENT=oom-kill
	@test -n "$(EXPERIMENT)" || (echo "set EXPERIMENT= (see chaos/)" && exit 1)
	kubectl apply -f chaos/$(EXPERIMENT).yaml

.PHONY: verify-outcome
verify-outcome: ## Assert an incident resolved within SLO. Usage: make verify-outcome INCIDENT=<id>
	@test -n "$(INCIDENT)" || (echo "set INCIDENT=<id>" && exit 1)
	@bash scripts/verify-outcome.sh $(INCIDENT)

.PHONY: evals
evals: ## Run the LLM eval harness against the golden incident set
	pytest evals/ -q

# ─────────────────────────────────────────────────────────────
##@ Housekeeping

.PHONY: dashboard
dashboard: ## Regenerate docs/dashboard.html from ROADMAP.md, ADRs, labs, journal and git
	@python scripts/dashboard.py

.PHONY: lab
lab: ## Scaffold a new lab doc + journal entry. Usage: make lab NAME=setup-flux
	@test -n "$(NAME)" || (echo "set NAME=<slug>" && exit 1)
	@bash scripts/new-lab.sh $(NAME)

.PHONY: secrets-scan
secrets-scan: ## Scan the full git history for leaked secrets
	@command -v gitleaks >/dev/null 2>&1 || (echo "install gitleaks first" && exit 1)
	gitleaks detect --source . --verbose
