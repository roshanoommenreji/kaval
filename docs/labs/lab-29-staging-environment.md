# Lab 29 — the staging environment, built and proven live

**Phase:** 4 · **Time:** most of a session · **Cost:** about $0.045/hour while it is up (On-Demand
node, database server, two public IPv4 addresses, two small disks); a few cents for the whole
drill, nothing left running afterwards

`ROADMAP.md`'s next Phase 4 line was `infra/envs/staging`: a second node, its own VPC, its own k3s,
10 GB of data disk, and its own database server. [ADR-0004](../adr/0004-environment-strategy-and-promotion.md)
had decided the shape weeks earlier. Building it found that three of the shared Terraform modules
had never been written for a second environment, and that the moment a second database server
exists, several things that looked correct become ambiguous. All of it fixed; staging comes up
from nothing and is destroyed again cleanly.

---

## 1. Make the shared modules reusable

`infra/modules/network`, `node` and `iam` had the literal `"kaval-prod"` in resource names and
tags, unlike `database` and `backups`, which already took a `name_prefix`. They now take a
required `name_prefix` (no default, so a new call site cannot silently collide with prod).
The node's bootstrap script (`user_data.sh.tftpl`) used `kaval-prod` for the Kubernetes
namespace and the SSM parameter path, and `deploy/gitops/prod` for the Flux manifests; all three
now derive from the same variable (`trimprefix(name_prefix, "kaval-")` gives `prod`/`staging`).

Check that prod did not move:

```
cd infra/envs/prod && terraform plan
```

Expected: every name is byte-identical, so the only changes are the intended ones: the launch
template's bootstrap script, the ASG's pointer to the new template version, and the snapshot
policy's selector (section 2). `0 to add, 3 to change, 0 to destroy`. Nothing was applied to prod.

## 2. The finding: `Role=database` is ambiguous with two databases

Everything that located "the database" did so by tag `Role=database` and took the first match.
With prod and staging each running a server, that is a coin flip: `make down`, `make up`, the
health check, the restore script (including its "newest snapshot" default, which was newest
*account-wide*), the node bootstrap (it could have pointed staging's services at **prod's**
database), and the daily snapshot policy (each environment's would snapshot both volumes).

All now match the environment's exact `Name` tag as well. The ops scripts take `DB_NAME_PREFIX`
(default `kaval-prod`). Pre-stop snapshots are named `<prefix>-database-pre-stop`; the restore
script still recognises the old unprefixed name, for prod only. Detail in the
[ADR-0008 amendment](../adr/0008-production-database-on-its-own-server.md) of 2026-10-08.

## 3. The new environment

`infra/envs/staging/`: own VPC (`10.61.0.0/16`, distinct from prod's `10.60.0.0/16`), node,
database server (10 GB data volume), backups bucket, IAM. It deliberately has no ECR module (images
are shared; staging computes the four repository ARNs and only pulls), no budget module (the
account-wide hard stop already covers it) and no Slack (placeholder SSM parameters keep the
bootstrap script happy; the chart's `slackSecretName` is unset). Plus `deploy/gitops/staging/`
(five Flux manifests) and a rewritten `deploy/environments/staging/values.yaml`, which was still
the Phase 3 k3d placeholder with an in-cluster Postgres and a dev password.

```
cp infra/envs/staging/terraform.tfvars.example infra/envs/staging/terraform.tfvars  # add your ssh_public_key
cd infra/envs/staging
terraform init
terraform plan        # 48 to add, 0 to change, 0 to destroy; every name carries kaval-staging
terraform apply
```

**Order matters:** the node downloads `deploy/gitops/staging/*` from GitHub's `main` branch at
boot, so those files must be merged before the first apply.

## 4. Spot capacity again, and a new default

The first apply created everything except the node: `We currently do not have sufficient
t4g.medium capacity` in all three AZs, the same shortage that stalled Lab 28 on 2026-10-06.
Switched the node to On-Demand and it launched immediately. Staging now defaults to On-Demand
(`node_spot = false`): it is up for hours per release, so the premium (~$0.012/hr) is pennies,
and it is the environment that must come up when asked. Prod's purchase option is a separate,
cost-ceiling decision.

## 5. The finding: services locked out of a fresh database

With the node up, the migrate job completed, but the agent and executor crash-looped
(`password authentication failed for user "kaval_agent"`) and the gateway's `/healthz` was 503.
Cause: the bootstrap script applied `db-roles.sql` (per-service roles and grants) *before* seeding
Flux, and the schema is created by the migrate job, which only runs after Flux is seeded. On a
fresh database the GRANTs always failed (`relation "signal" does not exist`) and the services
waited for the next 6-hour timer. This was not the "race" the script's comment called it; it
could not win. A long-lived prod database never showed it.

Fix: apply the roles last, after waiting (up to ~10 minutes, non-fatal) for the schema. Verified
by destroying staging and building it fresh: the agent and executor each restarted once in the
first minute, then ran stable with no intervention.

## 6. The finding: the gateway could not read what it serves

Even then `/healthz` returned `{"postgres":{"ok":false,"detail":"ProgrammingError"},...}`. It runs
`SELECT version_num FROM alembic_version`, and `kaval_gateway` had no grant on that table, nor on
`signal` or `incident_signal` (the `/v1/signals` and `/v1/incidents` endpoints read them).
`scripts/ops/db-roles.sql` now grants all three, read-only. This is a latent bug on prod too,
hidden because `/healthz` also returns 503 there for want of a served model.

## 7. Verify (read-only, over SSM)

```
aws ssm send-command --instance-ids <node> --document-name AWS-RunShellScript \
  --parameters file://verify.json     # commands: export KUBECONFIG=/etc/rancher/k3s/k3s.yaml, then
                                      # kubectl get nodes; flux get kustomizations,helmreleases -A;
                                      # kubectl get pods,secrets -n kaval-staging
```

Seen: node `Ready`; Flux `kaval-staging` kustomization and Helm release both `True`; agent,
collector, executor and gateway `Running 1/1`; the five `kaval-postgres-*` Secrets and `kaval-slack`
created automatically from SSM; `POSTGRES_HOST` is staging's own database
(`10.61.x.x`, not prod's `10.60.x.x`). The `ecr-cred` Secret and the registry ConfigMap came from
the instance's own identity, with no account ID in Git.

## 8. Tear down (by hand until `make staging-down` exists)

The database instance has no termination protection in staging, but its data volume keeps
`prevent_destroy` (its address is wired into `restore-snapshot.sh`). Releasing it is the
deliberate, separate step:

```
cd infra/envs/staging
VOL=$(terraform output -raw database_data_volume_id)
aws ec2 stop-instances --instance-ids "$(terraform output -raw database_instance_id)" && \
  aws ec2 wait instance-stopped --instance-ids "$(terraform output -raw database_instance_id)"
terraform state rm module.database.aws_ebs_volume.data
terraform plan -destroy -out=destroy.tfplan     # read it: 47 to destroy, all kaval-staging
terraform apply destroy.tfplan
# the volume is now detached and unmanaged; confirm it is staging's before deleting it
aws ec2 describe-volumes --volume-ids "$VOL" --query 'Volumes[0].[State,Tags[?Key==`Name`]|[0].Value]'
aws ec2 delete-volume --volume-id "$VOL"
```

Verified afterwards: no instances, volumes or VPCs tagged `Env=staging`, and prod's database
instance, data volume and ASG exactly as before. The stop-first step matters: detaching a
mounted volume from a running instance is the failure mode to avoid.

## What this does not do

- No `make staging-up`/`staging-down`, and no idle self-destruct. Until they exist staging is
  destroyed by hand as above, which is why it is not left running.
- Staging comes up with an **empty** database. Seeding from a sanitised prod snapshot
  (`restore.sh`, `anonymise.sql`) is its own line.
- The gateway's `/healthz` stays 503 on AWS until something serves a model; that is a Phase 6+
  concern and the same on prod.
- Nothing was applied to prod. Its plan shows three in-place updates (section 1) waiting for
  approval, and the db-roles fix reaches prod's database on its next bootstrap run.

## Related

- [ADR-0004](../adr/0004-environment-strategy-and-promotion.md) (amended 2026-10-08),
  [ADR-0008](../adr/0008-production-database-on-its-own-server.md) (amended),
  [ADR-0027](../adr/0027-automatic-secret-and-role-recreation.md) (amended)
- [Lab 28](lab-28-make-up-down-drill.md) — the Spot shortage it first hit, and the same "a drill
  finds what a review can't" pattern
