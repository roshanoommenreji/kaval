# Bring production up, check it, park it

Production is parked between sessions (the app server group at 0, the database server stopped). This is the
procedure to bring it up with a new version, check that it is healthy, and park it again. First written from
the live run of 2026-10-14 (`KAV-83` Part 3, [Lab 50](../labs/lab-50-closing-phase-5.md)), which brought up
`sha-0904d5c` over a database two migrations behind and hit two ordering gaps (`KAV-85`). Since
[ADR-0057](../adr/0057-the-database-is-brought-up-to-date-before-a-new-version-starts.md) the migration runs before
the install and the permissions are handed out again once the release is Ready, so steps 5 and 6 are only for
when that did not work. The new order was proven on staging (Lab 54) and then ran on production on 2026-10-21 (`KAV-98`,
[Lab 61](../labs/lab-61-production-bring-up-for-phase-6.md)): the first boot applied the one waiting migration by itself,
the permissions pass ran after Ready, and neither step 5 nor step 6 was needed.

## Signals

- A new version has been promoted (the `promote.yml` pull request is merged) and production has not run it yet
- A session needs production's real numbers (memory, targets, the "still alive" alarm)
- After bring-up: the release stuck before any pod starts, with the migrate Job failing (step 5), or the gateway
  logging `slack_chatops: notify loop failed this pass` with `permission denied for table` (step 6)

## Likely causes

1. The migration itself failed (it now runs first, so a failure stops the install before any service starts;
   read the Job's log, step 5)
2. A table the migration added has no per-service permissions yet (step 6; the second permissions pass should
   have run, `journalctl -u kaval-db-grants`)
3. The app server's base image or boot script changed since the last boot (watch the first boot)

## Diagnosis

Read the order rule first ([ADR-0051](../adr/0051-closing-phase-5-readiness-checks-prod-prometheus-and-the-disk-trend.md)): **the new version is
promoted through `promote.yml` before production comes up**, so production boots straight into it and Flux never
has to move it later. That needs a `make staging-smoke` pass recorded for the exact tag
(`deploy/promotion/passed-staging.json`) and the promotion pull request merged.

Cost while up: the app server plus the database server, about 4.5 cents an hour together (including the two public
addresses). A 1-hour session is about 5 cents.

## Remediation

### 1. Before anything starts (read-only)

```bash
AWS_PROFILE=kaval python scripts/release/promote.py preflight      # prod's pinned images exist in ECR
AWS_PROFILE=kaval terraform -chdir=infra/envs/prod plan            # expect "No changes" while parked (or only the launch template and the group's version, if a start-up script change has merged since the last apply). New laptop: `make tf-init ENV=prod` first, see terraform-state.md
```

If the new version has a migration, first run the old-database drill on staging with that exact version (roll
staging's database back, replace its server, see it come up by itself: [Lab 54](../labs/lab-54-a-new-version-on-an-old-database.md),
steps 2 and 5). Staging's database is normally already at head, so this is the only way staging shows the case.
Then take a snapshot of the database's data volume while the database is still
stopped (reversible only with it; blast radius: the database). The data volume's Name tag is
`kaval-prod-database-data`:

```bash
aws ec2 create-snapshot --volume-id <the volume> --description "kaval-prod pre-migration backup before <tag>, DB stopped"
aws ec2 wait snapshot-completed --snapshot-ids <the snapshot>
```

### 2. Start the database, then show the plan

```bash
bash scripts/ops/resume-database.sh                                # starts it, waits for SSM and a health check
mv infra/envs/prod/node.auto.tfvars <somewhere>                    # the parked setting (app_node_desired_capacity = 0)
terraform -chdir=infra/envs/prod plan -out=<file>                  # show it: desired 0 -> 1, min 0 -> 1
```

The first bring-up after `KAV-85` also shows the server's start-up script changing (the launch template and the
group's version of it, both in place, nothing destroyed). That is the new permissions step reaching production; no
running server is touched because there is none.

Never apply to production without showing this plan and being told to ([CLAUDE.md](../../CLAUDE.md)).

### 3. Apply, and wait for the server

```bash
terraform -chdir=infra/envs/prod apply <file>
```

The group's server is `InService` within a minute or two; k3s, Flux and the services follow. Timings on
2026-10-14: the first "still alive" datapoint about 4 minutes after the apply, every service `Running` about
17 minutes in (this run: held back by step 5).

### 4. Check it (read-only), from the server over Session Manager

First, from the laptop, `make prod-smoke` ([ADR-0066](../adr/0066-production-gets-one-real-planted-finding-and-a-read-only-smoke-test.md)):
it checks the tag, that production runs the digests that passed staging, the pods, the database, the API and the
server's timers, changes nothing, and exits 2 if production is parked. Then, from the server:

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl get pods -n kaval-prod                       # agent, collector, collector-prometheus, executor, 2 x gateway, prometheus: all 1/1
kubectl get kustomization,helmrelease -A             # kaval-prod kustomization True
kubectl exec -n kaval-prod deploy/<release>-gateway -- alembic current   # the head: c3e8a1d7f402 on 2026-10-14
systemctl list-timers | grep kaval                   # heartbeat (2 min), gitops-bootstrap (6 h), db-backup (19:30 UTC)
free -m; kubectl top nodes; kubectl top pods -n kaval-prod
kubectl get --raw /api/v1/namespaces/kaval-prod/services/<release>-prometheus:9090/proxy/api/v1/targets | grep -oE 'health.{3}[a-z]+' | sort | uniq -c
```

Seen on 2026-10-14: 10 of 10 Prometheus targets up (the six services, Prometheus itself, the kubelet's cAdvisor
and the database server's `node_exporter` on 9100 and `postgres_exporter` on 9187); node memory 1,576 MB used and
2,062 MB available of 3,829 MB (52 percent); services 45 to 81 MiB each. The "still alive" alarm
(`kaval-prod-still-alive`) stayed `OK` with 2 to 3 datapoints in every 5-minute reading
([still-alive-alarm.md](still-alive-alarm.md)).

### 5. Only if the migration did not run by itself: run it by hand (reversible only from the snapshot; blast radius: the production database)

Since `KAV-85` the migrate Job is a `pre-install` hook, so the database is brought to the new version before any
service starts. If it did not (the release shows failed and no service is up, or an executor crash-loops on a
missing column), first read why: `kubectl get jobs,pods -n kaval-prod`, then `kubectl logs job/<release>-migrate-<n>`.
On 2026-10-14, before the change, the cause was the old `post-install` order: the install waited for an executor
that needed the migration. If the Job failed for its own reason, fix that; the manual run below is the same command.

Run the same command from the same image the chart uses, as one Job. Fill in the account's registry; the secret
is the one the chart's `postgres.secretNames.migrate` names (`kaval-postgres-admin`):

```yaml
apiVersion: batch/v1
kind: Job
metadata: {name: kaval-prod-migrate-manual, namespace: kaval-prod}
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      imagePullSecrets: [{name: ecr-cred}]
      containers:
        - name: migrate
          image: <account>.dkr.ecr.ap-south-1.amazonaws.com/kaval/gateway:<tag>
          command: ["alembic", "upgrade", "head"]
          envFrom: [{secretRef: {name: kaval-postgres-admin}}]
```

```bash
kubectl apply -f migrate-manual.yaml
kubectl wait --for=condition=complete job/kaval-prod-migrate-manual -n kaval-prod --timeout=120s
kubectl logs job/kaval-prod-migrate-manual -n kaval-prod | tail
kubectl exec -n kaval-prod deploy/<release>-gateway -- alembic current        # must print the new head
```

The executor recovers on its next restart (kubelet's back-off is at most 5 minutes). The release stays recorded
as failed in Flux, which does not matter: production's cluster is rebuilt from Git at every boot, so the next
boot installs against a database that is already at head.

### 6. Only if the gateway logs `permission denied for table` after a migration: apply the permissions again (reversible; blast radius: database permissions)

Since `KAV-85` the bootstrap runs `db-roles.sql` a second time once the release is Ready, in the background
(`journalctl -u kaval-db-grants`). If that did not happen (the release never became Ready within 10 minutes, or
the server was started from an older script), start it by hand; a table the migration added has no `GRANT` until then.

```bash
systemctl start kaval-gitops-bootstrap.service    # idempotent; it re-applies scripts/ops/db-roles.sql
journalctl -u kaval-gitops-bootstrap.service -n 20
kubectl logs -n kaval-prod deploy/<release>-gateway --since=2m | grep -c "notify loop failed"   # 0 within a minute
```

### 7. A fresh dump of the migrated database

```bash
systemctl start kaval-db-backup.service           # the node's own dump to the backup bucket, checked with pg_restore --list
aws s3 ls s3://<prod backup bucket>/postgres/ | tail -3
```

### 8. Park it

```bash
echo 'app_node_desired_capacity = 0' > infra/envs/prod/node.auto.tfvars
terraform -chdir=infra/envs/prod plan -out=<file>      # show it: ONE change, desired 1 -> 0, min 1 -> 0
terraform -chdir=infra/envs/prod apply <file>
bash scripts/ops/pause-database.sh                     # snapshots the data volume, then stops the database
```

Confirm: `aws autoscaling describe-auto-scaling-groups` shows `kaval-prod` at 0; the database is `stopped`; the
alarm is `OK` (a parked group wants 0, so the alarm cannot ring). The nightly 02:00 IST rule would also stop it,
but do not leave it to that.

### Open Prometheus from a laptop (optional, while it runs)

Prometheus has no public address by design. Tunnel to its private address through Session Manager:

```bash
aws ssm start-session --profile kaval --region ap-south-1 --target <the server's id> \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters '{"host":["<the prometheus service IP>"],"portNumber":["9090"],"localPortNumber":["9090"]}'
```

Then open `http://localhost:9090`. The service IP is set at each boot (`kubectl get svc -n kaval-prod`).

## Do not

- Do not bring production up before promoting: Flux would then have to move a running production to the new
  version under a database that may not match it.
- Do not apply to production without showing the plan first, or without being told to.
- Do not run step 5 without a snapshot of the data volume taken while the database was stopped.
- Do not create a pretend incident in production's database to see a Slack post. A real post waits for a real
  finding: a real incident, or one real, tiny, clearly named resource planted for the real scan to find (a 1 GiB
  disk tagged `Project=kaval`, `Env=prod`, `ManagedBy=manual`, deleted by hand afterwards), as
  [ADR-0066](../adr/0066-production-gets-one-real-planted-finding-and-a-read-only-smoke-test.md) allows.
- Do not leave production up overnight by accident: the group bills while it wants a server, and the alarm is
  the only thing that notices if it goes quiet.
