SHELL := /bin/bash
.DEFAULT_GOAL := help

-include .env
export

AWS_PROFILE ?= kaval
AWS_REGION  ?= ap-south-1
TF_PROD     := infra/envs/prod
TF_LAB      := infra/envs/lab-eks
TF_DEV      := infra/envs/dev
# Keep only a real instance id: with no state, terraform prints its "No outputs found"
# warning on stdout, which would otherwise end up in the variable.
DEVBOX       = $(shell terraform -chdir=$(TF_DEV) output -raw instance_id 2>/dev/null | grep -oE '^i-[0-9a-f]+$$')
unexport DEVBOX   # else the bare `export` above runs terraform for every target

# ─────────────────────────────────────────────────────────────
##@ Help

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nKaval — make targets\n\n"} \
	  /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 } \
	  /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) }' $(MAKEFILE_LIST)
	@echo ""

# ─────────────────────────────────────────────────────────────
##@ Local development  (the stack runs on the AWS dev server — ADR-0007, KAV-22)

# Docker commands go to the dev server's daemon over SSH, not to the laptop.
DEV_CONTEXT ?= kaval-devbox
COMPOSE     := docker --context $(DEV_CONTEXT) compose

.PHONY: dev
dev: devbox-up ## Start the stack on the dev server (Ollama, Postgres, migrations, gateway)
	$(COMPOSE) up -d --build --wait
	@echo ""
	@echo "Running on the dev server. From the laptop: make dev-tunnel (in its own terminal), then"
	@echo "  gateway   http://localhost:8000/healthz"
	@echo "  ollama    http://localhost:11435   (not 11434: a laptop Ollama app often holds that port)"
	@echo "  postgres  localhost:5432"

.PHONY: dev-down
dev-down: ## Stop the stack (data and models stay in their volumes)
	$(COMPOSE) down

.PHONY: dev-tunnel
dev-tunnel: ## Forward the stack's ports to the laptop (runs until Ctrl-C)
	ssh -N -o ExitOnForwardFailure=yes -L 8000:127.0.0.1:8000 -L 5432:127.0.0.1:5432 -L 11435:127.0.0.1:11434 kaval-devbox

.PHONY: logs
logs: ## Tail the stack's logs
	$(COMPOSE) logs -f --tail=100

.PHONY: signals
signals: ## Write one fake incident's signals: SCENARIO=oom-crashloop [SEED=42]; no SCENARIO lists them
	$(COMPOSE) run --rm --build signals $(if $(SCENARIO),$(SCENARIO) $(if $(SEED),--seed $(SEED)),--list)

.PHONY: correlate
correlate: ## Group the signals not yet in an incident into incidents, one pass (KAV-39)
	$(COMPOSE) run --rm --build agent

.PHONY: bench
bench: ## Measure the local model shortlist on the dev server (KAV-22, ~20 min)
	python scripts/dev/bench_models.py

.PHONY: test
test: ## Unit tests + policy tests
	pytest services/ scripts/dev/ scripts/tracking/ scripts/release/ evals/ infra/modules/idle-stop/lambda/ -q
	@command -v opa >/dev/null 2>&1 && opa test policy/ -v || echo "opa not installed — skipping policy tests"

.PHONY: lint
lint: ## Lint and type-check
	ruff check infra/modules/idle-stop/lambda scripts/release services/ migrations/ evals/ scripts/tracking/atlassian.py scripts/tracking/jira-sync.py scripts/tracking/jira_adf.py scripts/tracking/test_jira_adf.py scripts/tracking/jira-dashboards.py scripts/dev/bench_models.py scripts/dev/check_commits.py scripts/dev/test_check_commits.py scripts/ops/approve.py
	mypy infra/modules/idle-stop/lambda/idle_stop.py scripts/release/pin_staging.py scripts/release/staging_smoke.py scripts/release/promote.py scripts/release/rollback.py scripts/release/versions.py scripts/release/change_record.py services/ evals/ scripts/dev/check_commits.py scripts/tracking/jira_adf.py

.PHONY: lock
lock: ## Re-resolve uv.lock after editing pyproject.toml's dependencies (then commit both)
	uv lock

.PHONY: sync
sync: ## Install exactly what uv.lock says into .venv (what CI and the images use)
	uv sync --locked --all-extras

.PHONY: migrate
migrate: ## Apply database migrations (reads POSTGRES_* from .env)
	alembic upgrade head

.PHONY: build
build: ## Build arm64 images (Graviton — amd64 will NOT run on the node)
	# Context is the repo root: images need services/shared and pyproject.toml, and the
	# root .dockerignore allowlist is what keeps .env out.
	docker buildx build --platform linux/arm64 -f services/gateway/Dockerfile   -t kaval/gateway:dev   .
	docker buildx build --platform linux/arm64 -f services/collector/Dockerfile -t kaval/collector:dev .
	docker buildx build --platform linux/arm64 -f services/agent/Dockerfile     -t kaval/agent:dev     .
	docker buildx build --platform linux/arm64 -f services/executor/Dockerfile  -t kaval/executor:dev  .

# ─────────────────────────────────────────────────────────────
##@ AWS  (starts and stops billing — read docs/cost/budget-plan.md)

.PHONY: guardrails
guardrails: ## Provision ONLY the budget alarms. Run this before anything else, ever.
	cd $(TF_PROD) && terraform init && terraform apply -target=module.budget

.PHONY: plan
plan: ## Show what would change in prod (never applies)
	cd $(TF_PROD) && terraform plan

.PHONY: up
up: ## Resume the database, provision the spot node and reconcile from Git (~5 min, STARTS BILLING)
	@python scripts/release/promote.py preflight
	@echo "This starts billing at roughly \$$0.0126/hr (app node) plus the database server (~\$$14.40/mo while running)."
	@read -p "Continue? [y/N] " ok && [ "$$ok" = "y" ]
	@bash scripts/ops/resume-database.sh
	rm -f $(TF_PROD)/node.auto.tfvars
	cd $(TF_PROD) && terraform apply
	@echo "Waiting for Flux to reconcile..."
	@echo "Check with: kubectl get pods -A"

.PHONY: down
down: ## Scale the node's ASG to 0, pause the database (snapshot first), keep EBS/ECR/S3 state (~\$2/mo parked)
	# node.auto.tfvars persists the pause so it isn't silently undone by some *other*,
	# unrelated `terraform apply` run later that doesn't think to pass this on the command
	# line (found live, KAV-32 Lab 28: exactly that happened, mid-session, to the person who
	# wrote this Makefile target).
	echo 'app_node_desired_capacity = 0' > $(TF_PROD)/node.auto.tfvars
	cd $(TF_PROD) && terraform apply
	@bash scripts/ops/pause-database.sh
	@echo "Node scaled to 0, database paused. 'make up' restores both in a few minutes."

.PHONY: nuke
nuke: ## Destroy EVERYTHING in prod including state. Irreversible.
	@echo "This destroys all persistent data including the incident history."
	@read -p "Type 'nuke' to confirm: " ok && [ "$$ok" = "nuke" ]
	cd $(TF_PROD) && terraform destroy

.PHONY: db-health-check
db-health-check: ## Run the database's start-up health check by hand (pg_isready + sanity query)
	@bash scripts/ops/health-check-database.sh

.PHONY: db-restore-snapshot
db-restore-snapshot: ## Restore the database's data volume from an EBS snapshot (SNAPSHOT=<id>, else the newest). See docs/runbooks/restore-from-backup.md
	@SNAPSHOT=$(SNAPSHOT) TF_PROD=$(TF_PROD) bash scripts/ops/restore-snapshot.sh

# ─────────────────────────────────────────────────────────────
##@ Staging  (ADR-0004, ADR-0029 — ~\$0.045/hr while up, parks itself after 4 idle hours)

.PHONY: staging-up
staging-up: ## Build or resume staging and wait until its services answer (~5 min, STARTS BILLING)
	@bash scripts/ops/staging.sh up

.PHONY: staging-status
staging-status: ## Is staging running? Database state and the pods, read over Session Manager
	@bash scripts/ops/staging.sh status

.PHONY: staging-smoke
staging-smoke: ## Smoke-test the tag staging runs (digest vs ECR, pods, database, API) and record a pass. Needs staging up (TAG=, NO_RECORD=1)
	@python scripts/release/staging_smoke.py $(if $(TAG),--tag $(TAG)) $(if $(NO_RECORD),--no-record)

.PHONY: version-plan
version-plan: ## What the next component and product versions would be, from the commits since each tag. Writes nothing
	@python scripts/release/versions.py plan

.PHONY: staging-down
staging-down: ## Destroy staging completely, data volume included (it also parks itself when idle)
	@bash scripts/ops/staging.sh down

# ─────────────────────────────────────────────────────────────
##@ Dev server  (ADR-0007 — \$0.0224/hr while running, stops itself after 1 h idle)

.PHONY: devbox-create
devbox-create: ## Create the dev server. Shows the plan and asks before applying
	cd $(TF_DEV) && terraform init -input=false && terraform apply

.PHONY: devbox-exists
devbox-exists:
	@test -n "$(DEVBOX)" || (echo "no dev server yet - run: make devbox-create" && exit 1)

.PHONY: devbox-up
devbox-up: devbox-exists ## Start the dev server and wait until you can connect (~1 min)
	@aws ec2 start-instances --instance-ids $(DEVBOX) --query 'StartingInstances[0].CurrentState.Name' --output text
	@aws ec2 wait instance-running --instance-ids $(DEVBOX)
	@echo "running - waiting for Session Manager..."
	@until [ "$$(aws ssm describe-instance-information --filters Key=InstanceIds,Values=$(DEVBOX) \
	    --query 'InstanceInformationList[0].PingStatus' --output text)" = "Online" ]; do sleep 5; done
	@# "Online" isn't proof: right after a start it can still be the status from before the stop,
	@# and a docker command that connects too early hangs for good (its ssh times out, but the
	@# orphaned SSM plugin keeps the pipe open). So ready means one real SSH login worked.
	@echo "online - waiting for SSH..."
	@for i in $$(seq 1 24); do ssh -o ConnectTimeout=20 -o BatchMode=yes kaval-devbox true 2>/dev/null && break; \
	  [ $$i -eq 24 ] && { echo "SSH still failing after 2 min: see docs/labs/lab-03-aws-dev-server.md"; exit 1; }; sleep 5; done
	@echo "ready: make devbox-ssh, or docker --context kaval-devbox ..."

.PHONY: devbox-down
devbox-down: devbox-exists ## Stop the dev server now (it also stops itself after 1 h idle)
	@aws ec2 stop-instances --instance-ids $(DEVBOX) --query 'StoppingInstances[0].CurrentState.Name' --output text

.PHONY: devbox-status
devbox-status: devbox-exists ## Is the dev server running, and since when?
	@aws ec2 describe-instances --instance-ids $(DEVBOX) \
	  --query 'Reservations[0].Instances[0].[State.Name,InstanceType,LaunchTime]' --output text

.PHONY: devbox-ssh
devbox-ssh: ## Open a terminal on the dev server (SSH through Session Manager)
	ssh kaval-devbox

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
	@bash scripts/ops/backup.sh

.PHONY: seed-refresh
seed-refresh: ## Clean production's newest dump on THIS machine for staging to be filled from (needs Docker; ADR-0039)
	@bash scripts/ops/seed-refresh.sh

.PHONY: restore-staging
restore-staging: ## By hand: restore the latest dump in BACKUP_BUCKET into staging, scrubbing it (the staging node does this itself at boot, ADR-0039)
	@bash scripts/ops/restore.sh staging

.PHONY: restore-prod
restore-prod: ## DISASTER RECOVERY. Replaces the live database. Prompts.
	@bash scripts/ops/restore.sh prod

# ─────────────────────────────────────────────────────────────
##@ Cost

.PHONY: cost-report
cost-report: ## Month-to-date AWS spend against the \$25 ceiling
	@bash scripts/ops/cost-report.sh

.PHONY: cost-check
cost-check: ## Fail loudly if MTD spend exceeds the ceiling
	@bash scripts/ops/cost-report.sh --assert

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

# ─────────────────────────────────────────────────────────────
##@ Housekeeping

.PHONY: dashboard
dashboard: ## Regenerate docs/dashboard.html from ROADMAP.md, ADRs, labs, journal and git
	@python scripts/tracking/dashboard.py

.PHONY: docs-sync
docs-sync: dashboard ## Regenerate the dashboard and republish Confluence (Definition of Done item 9)
	@python scripts/tracking/publish-confluence.py
	@echo "Now republish docs/dashboard.html to the dashboard artifact."

.PHONY: jira
jira: ## Show an epic's stories. Usage: make jira EPIC=KAV-6
	@python scripts/tracking/jira-sync.py show $(or $(EPIC),KAV-6)

.PHONY: pull-embed-model
pull-embed-model: ## Ensure EMBED_MODEL is pulled into Ollama (KAV-40); a no-op after the first run
	$(COMPOSE) run --rm model-pull-embed

.PHONY: index-runbooks
index-runbooks: pull-embed-model ## Sync docs/runbooks/*.md into runbook_chunk (KAV-40). Needs make dev-tunnel.
	@python -m kaval_agent.index_runbooks

.PHONY: context
context: pull-embed-model ## Print the context built for one incident. Usage: make context INCIDENT=<uuid>
	@test -n "$(INCIDENT)" || (echo "set INCIDENT=<uuid>" && exit 1)
	@python -m kaval_agent.context $(INCIDENT) --with-changes

.PHONY: diagnose
diagnose: pull-embed-model ## Diagnose one incident with the local model and write a proposal (KAV-41). Usage: make diagnose INCIDENT=<uuid> [DRY_RUN=1] [ESCALATE=1]
	@test -n "$(INCIDENT)" || (echo "set INCIDENT=<uuid>" && exit 1)
	@python -m kaval_agent.diagnose $(INCIDENT) --with-changes $(if $(DRY_RUN),--dry-run,) $(if $(ESCALATE),--escalate,)

.PHONY: escalate
escalate: ## Force one incident straight to Bedrock (KAV-44), bypassing the local-first decision. Usage: make escalate INCIDENT=<uuid> [DRY_RUN=1]. Needs BEDROCK_MODEL_ID and AWS_PROFILE=kaval.
	@test -n "$(INCIDENT)" || (echo "set INCIDENT=<uuid>" && exit 1)
	@test -n "$(BEDROCK_MODEL_ID)" || (echo "set BEDROCK_MODEL_ID=<model or inference-profile id>" && exit 1)
	@python -m kaval_agent.escalate $(INCIDENT) $(if $(DRY_RUN),--dry-run,)

.PHONY: evals
evals: pull-embed-model ## Run the 20 golden incidents through the real pipeline and score them (KAV-43). Usage: make evals [ONLY=name] [JSON=path]
	@python -m evals.run $(if $(ONLY),--only $(ONLY),) $(if $(JSON),--json $(JSON),)

.PHONY: policy-check
policy-check: ## Classify one hypothetical action against policy/ (KAV-42), no database. Usage: make policy-check TYPE=restart_pod BLAST_RADIUS=pod CONFIDENCE=0.95 [REVERSIBLE=1]
	@test -n "$(TYPE)" && test -n "$(BLAST_RADIUS)" && test -n "$(CONFIDENCE)" || \
		(echo "set TYPE= BLAST_RADIUS= CONFIDENCE=" && exit 1)
	@python -m kaval_agent.policy --type $(TYPE) --blast-radius $(BLAST_RADIUS) \
		--confidence $(CONFIDENCE) $(if $(REVERSIBLE),--reversible,)

.PHONY: approve
approve: ## Approve or deny one proposed action via the gateway (KAV-47, Phase-3 stand-in for the mobile app). Usage: make approve ACTION=<uuid> VERDICT=approved|denied [ACTOR=you] [REASON="..."]
	@test -n "$(ACTION)" && test -n "$(VERDICT)" || (echo "set ACTION=<uuid> VERDICT=approved|denied" && exit 1)
	@python scripts/ops/approve.py $(ACTION) $(VERDICT) $(if $(ACTOR),--actor "$(ACTOR)",) $(if $(REASON),--reason "$(REASON)",)

.PHONY: execute
execute: ## Run the executor one pass against the database in .env (KAV-47). Needs a reachable Kubernetes cluster — make dev-tunnel does not provide one; run this on the devbox or against k3d.
	@python -m kaval_executor.executor

.PHONY: watch-events
watch-events: ## Poll real Kubernetes events into signal rows, one pass (KAV-48). Same cluster requirement as `make execute`.
	@python -m kaval_collector.k8s_events $(if $(NAMESPACE),--namespace $(NAMESPACE),)

.PHONY: jira-dashboards
jira-dashboards: ## Create or update the Jira dashboards from scripts/tracking/jira-dashboards.toml
	@python scripts/tracking/jira-dashboards.py

.PHONY: lab
lab: ## Scaffold a new lab doc + journal entry. Usage: make lab NAME=setup-flux
	@test -n "$(NAME)" || (echo "set NAME=<slug>" && exit 1)
	@bash scripts/dev/new-lab.sh $(NAME)

.PHONY: secrets-scan
secrets-scan: ## Scan the full git history for leaked secrets
	@command -v gitleaks >/dev/null 2>&1 || (echo "install gitleaks first" && exit 1)
	gitleaks detect --source . --verbose
