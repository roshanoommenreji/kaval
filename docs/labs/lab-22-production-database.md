# Lab 22 — the production database, on its own server

**Phase:** 4 · **Time:** ~1 hr for the apply and the out-of-band Secrets, once you've read this
**Cost:** +~$14.40/mo always-on (ADR-0008); prod's steady state moves from ~$14 to ~$28/mo. The
$40 ceiling and $30/$35/$38 alert thresholds were already raised for exactly this on 2026-09-26 —
nothing to change in the budget module.

Prod has been running Postgres **in-cluster**, via the chart's own `postgres.yaml`, since `KAV-50`
— a documented stopgap. This lab moves it to its own `t4g.small` server, per
[ADR-0008](../adr/0008-production-database-on-its-own-server.md), amended 2026-10-04 with the
implementation decisions this lab reproduces.

Asked how much of `KAV-32`'s 12 acceptance criteria to build in one sitting, Roshan chose **core
server + security + backups**, deferring the rest — see "What's deferred" below.

---

## What's built

### 1. The server itself (`infra/modules/database`)

- `t4g.small`, **on-demand, never spot** — a database must not be reclaimable at two minutes'
  notice.
- Its own security group: **inbound 5432 only from the app node's security group**, no SSH —
  admin is SSM Session Manager only, same posture as `infra/modules/devbox` and
  `infra/modules/node`.
- A **separate 20 GB encrypted gp3 data volume**, `prevent_destroy`, found via its stable
  `/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_<volume-id>` symlink rather than a guessed
  NVMe device name (Nitro instances don't honour the requested `device_name`).
- `disable_api_termination = true`, IMDSv2 only, tagged `Role=database` (instance) /
  `Role=database-data` (volume) — the tags `docs/runbooks/restore-from-backup.md` was already
  written to expect.
- **TLS**, enforced not merely available: a self-signed certificate generated once on first boot
  (lives on the data volume, so it survives a stop/start), and a custom `pg_hba.conf` with
  `hostssl ... scram-sha-256` plus a `hostnossl ... reject` catch-all. Honest limitation: this
  encrypts the wire, it doesn't authenticate the server's identity — a real CA is out of scope
  for a single-operator project. The actual network boundary is still the security group above.
- **Graceful shutdown**: Postgres runs under a `systemd` unit (`kaval-postgres.service`) whose
  `ExecStop` is `docker stop -t 60`, so an EC2 stop becomes a clean checkpoint, not a kill.
- **Five passwords, one per Postgres role**, generated once by Terraform (`random_password`) and
  stored in SSM Parameter Store as `SecureString`s under `/kaval/<name_prefix>/db/<role>-password`
  — read by the instance itself only for the admin role (first-boot init), and by a human,
  off the instance, for the four service roles.

### 2. Per-service Postgres roles, enforced in the database (`scripts/ops/db-roles.sql`)

`kaval_gateway`, `kaval_agent`, `kaval_executor`, `kaval_collector` are real login roles with
real, narrow `GRANT`s — the same read-only/scoped-write split `CLAUDE.md` constraint 3 already
enforces one layer up, at the Kubernetes RBAC layer (ADR-0021, `KAV-47`). `kaval_collector` can
insert into `signal` and nothing else; `kaval_gateway` can write `decision` but not `execution`.

### 3. The chart no longer assumes Postgres lives in the cluster

- `postgres.yaml` (the in-cluster Deployment/PVC/Service) now wraps in `{{- if
  .Values.postgres.enabled }}`. Local/dev/CI keep it `true` — nothing changes for them.
- Every consumer (`gateway`, `agent`, `executor`, `collector`, `migrate-job`) reads its
  `POSTGRES_*` env entirely from `envFrom` now, never a hardcoded `POSTGRES_HOST` line — a new
  `kaval.postgresSecretName` helper (`_helpers.tpl`) resolves to the chart's own Secret when
  `postgres.secretNames.<service>` is empty, or to a real out-of-band Secret name when it isn't.
  Prod sets all five (`kaval-postgres-gateway`, `-agent`, `-executor`, `-collector`, `-admin`) —
  same posture as `global.imagePullSecretName`/`ecr-cred` and `gateway.slackSecretName`: only the
  *name* is a repo fact, never a password.

### 4. Two independent backup mechanisms (ADR-0008 / ADR-0005)

- **DLM daily EBS snapshots of the data volume, keep 7** — pure AWS-to-AWS, no cluster
  involvement, live the moment `terraform apply` runs.
- **Nightly `pg_dump` → S3** (`scripts/ops/backup.sh`, now actually runnable: it was missing
  `PGPASSWORD` entirely until this lab, a real, previously-unexercised gap — `restore.sh` had
  the same gap, also fixed) via a new Helm `CronJob` (`backup-cronjob.yaml`, `backup.enabled`)
  and a new minimal utility image, `services/backup/` (`postgresql-client` + `awscli` on the
  same base as the gateway image). **Not yet enabled in prod** — see the blocker below.
- `infra/modules/backups`: the S3 bucket (private, SSE-S3, blocked public access) and the free
  S3 gateway VPC endpoint, so the dump never crosses a metered path.

### 5. `infra/modules/iam` gained one thing

The app node's own role — the only compute that can reach the database server's security
group on 5432 — gets scoped `s3:PutObject`/`GetObject`/`ListBucket`/`DeleteObject` on the new
bucket, nothing broader.

---

## What's deferred (named, not silently dropped)

- Pre-stop snapshots wired into `make down`, the nightly paused-posture auto-stop, and the
  hard-stop Lambda.
- The nightly auto-stop schedule itself (EventBridge Scheduler + a Terraform variable).
- The automatic boot-time health-check-and-restore-on-failure. `make db-restore-snapshot` stays
  a manual, operator-invoked step for now — `docs/runbooks/restore-from-backup.md` already
  documents it that way.
- SSM Patch Manager; `log_connections`/`log_disconnections`/failed-auth logging.
- The staging database server — blocked on `infra/envs/staging` (the second node) not existing
  yet. The module is already written to be reused for it (`name_prefix`, `subnet_id` are both
  inputs, nothing prod-specific is hardcoded).

## What's blocked, and needs you directly

Two pieces of this story need to **materialize a live AWS credential somewhere a Kubernetes
Secret can read it**, and this environment's own safety classifier refused to let an assistant
write either one — correctly cautious, since "write code that pulls a real credential and
stores it" is exactly the kind of action that should get a second look:

1. **`aws-creds`, a generic AWS credentials Secret**, refreshed hourly from the app node's own
   instance role (`infra/modules/iam`'s `kaval-prod-node` role already has the S3 permissions
   it would use). This is the *same pattern* `infra/modules/node/user_data.sh.tftpl` already
   runs for `ecr-cred` — a host-level script queries instance metadata, then builds the Secret
   via `kubectl apply -f -` on stdin, never a `kubectl create secret --from-literal` argument
   (which would sit in `ps aux`). Needed because pods on this k3s node, running in Flannel's
   own network namespace, can't reach `169.254.169.254` themselves — the default IMDS hop
   limit of 1 stops one network hop short of a pod, the same well-known gap EKS users hit and
   solve by raising `http-put-response-hop-limit` to 2, except here the fix is "ask the host,
   not the pod" instead, matching the ECR precedent already in this repo.
2. **`db-roles-job`, the Helm hook Job that runs `db-roles.sql`.** It has to read five different
   Secrets' `POSTGRES_PASSWORD` keys (the admin role plus the four service roles) into five
   distinctly-named env vars (`envFrom`-ing more than one Secret with the same key name would
   have each overwrite the last) and hand them to `psql -v gateway_password=... -f
   db-roles.sql`. The combination of several secretKeyRefs plus a `psql`/`sh -c` invocation
   tripped the classifier too.

**Do this yourself**, following the design above and in the ADR-0008 amendment, or review and
explicitly approve an assistant writing it in a follow-up turn — either way, never paste a
decrypted SSM parameter value into a chat with an assistant (same discipline as the Slack
tokens, ADR-0026).

---

## Reproducing from zero

```bash
cd infra/envs/prod
terraform init
terraform plan        # review: new module.database, module.backups, one new IAM statement —
                       # nothing else should drift
terraform apply       # ~5 min for the instance; cloud-init finishes Postgres setup after that
```

Then, reading each value yourself — never pasting a decrypted one into a chat:

```bash
terraform output database_private_ip
terraform output database_ssm_parameter_paths
# for each role in gateway, agent, executor, collector, admin:
aws ssm get-parameter --with-decryption --name <path-from-above> \
  --query Parameter.Value --output text --profile kaval

kubectl create secret generic kaval-postgres-admin -n kaval-prod \
  --from-literal=POSTGRES_HOST=<database_private_ip> --from-literal=POSTGRES_PORT=5432 \
  --from-literal=POSTGRES_DB=kaval --from-literal=POSTGRES_USER=kaval \
  --from-literal=POSTGRES_PASSWORD=<admin password> --from-literal=POSTGRES_SSLMODE=require
# repeat for kaval-postgres-gateway/kaval_gateway, -agent/kaval_agent, -executor/kaval_executor,
# -collector/kaval_collector — role names only change the --from-literal=POSTGRES_USER value
```

```bash
helm upgrade kaval-prod deploy/charts/kaval -n kaval-prod -f deploy/environments/prod/values.yaml
kubectl get pods -n kaval-prod   # migrate-job's init container should connect within a few
                                 # seconds; no postgres Deployment should exist at all now
```

Build and push the per-service role grants are still a manual step until the blocked
`db-roles-job` lands — run `scripts/ops/db-roles.sql` by hand against the admin connection in
the meantime:

```bash
PGPASSWORD=<admin password> psql -h <database_private_ip> -U kaval -d kaval \
  -v gateway_password="<gateway password>" -v agent_password="<agent password>" \
  -v executor_password="<executor password>" -v collector_password="<collector password>" \
  -f scripts/ops/db-roles.sql
```

## Verification

- `aws ec2 describe-security-groups` on the database's SG shows exactly one ingress rule,
  5432 from the app node's SG; no SSH rule exists anywhere on it.
- `aws ec2 describe-instances` on the database shows `DisableApiTermination: true`.
- From the app node: `psql "host=<ip> sslmode=require ..."` connects; `sslmode=disable` is
  rejected.
- `\du` on the database lists the five roles; `kaval_collector` can `INSERT INTO signal` but
  gets a permission error on `INSERT INTO decision`.
- `kubectl get pods -n kaval-prod` — every pod healthy against the new host; no `postgres-*`
  pod anywhere in the namespace.
- `aws dlm get-lifecycle-policies` shows the policy `ENABLED`.
- `helm lint`/`helm template` against every environment's `values.yaml` stay clean — the real
  regression check, since five chart templates changed and local/dev must render identically
  to before.
- `make test`, `make lint` green.

## Related

- [ADR-0008](../adr/0008-production-database-on-its-own-server.md) and its 2026-10-04 amendment
- [docs/runbooks/restore-from-backup.md](../runbooks/restore-from-backup.md)
- [Lab 21](lab-21-slack-chatops.md) — the same "Roshan runs the credential commands himself"
  discipline, there for Slack tokens, here for SSM parameters
