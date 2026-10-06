# ADR-0008 — The production database runs on its own server

- **Status:** Accepted
- **Date:** 2026-09-26
- **Deciders:** Roshan
- **Amends:** [ADR-0002](0002-k3s-for-always-on-eks-as-a-chapter.md) (Postgres no longer inside the k3s node),
  [ADR-0004](0004-environment-strategy-and-promotion.md) (dev's database placement differs; engine doesn't),
  [ADR-0005](0005-data-durability-and-staging-seeding.md) (backups and the staging restore now target a database server)

## Context

The original design ran Postgres + pgvector **inside the single k3s node**, on an EBS persistent
volume. It was the cheapest way to fit under a $25/month ceiling, and `CLAUDE.md` listed RDS as a
"never without an explicit conversation" item.

Roshan asked whether production would really keep the database on the same server as the app.
The project is supposed to match a real corporate setup, and in one the database has its own
server. This ADR is that conversation.

Co-locating is a real weakness, not just an aesthetic one:

- **Blast radius.** A memory spike in the app (the local model is the likely culprit on a 4 GB node)
  can get Postgres OOM-killed. The audit trail and the app then fail together.
- **Lifecycle coupling.** The app node is spot and designed to be replaced at any time. The
  database is the one thing that must not be.
- **Security boundary.** On one node, "only the app may reach the database" is a pod network
  policy. On its own server it's a security group, which is how companies draw that line.

## Options, priced

AWS Price List API, ap-south-1, 2026-09-26:

| Option | $/hr | ~$/mo always on |
|---|---|---|
| RDS `db.t4g.micro`, Single-AZ | $0.021 + gp3 $0.131/GB-mo | ~$18 |
| RDS `db.t4g.micro`, Multi-AZ | $0.042 + gp3 $0.262/GB-mo | ~$36 |
| **EC2 `t4g.small`, self-managed Postgres** | $0.0112 + gp3 $0.0912/GB-mo + IPv4 $0.005 | **~$14** |
| Stay inside the k3s node | — | $0 extra |

## Decision

**Postgres runs on its own EC2 server, self-managed. The monthly ceiling rises from $25 to $40.**

The server is built in **Phase 4** with the rest of production (`KAV-32`). Phases 1–3 keep
Postgres in a container on the dev server ([ADR-0007](0007-develop-on-an-aws-dev-server.md)).

| Aspect | Choice | Corporate practice, and the conscious trade-off |
|---|---|---|
| Machine | `t4g.small` (2 vCPU, 2 GB, Graviton), **on-demand, never spot** | A database must not be reclaimable at two minutes' notice |
| Postgres | 16 + pgvector, as the `pgvector/pgvector:pg16` container on the dedicated host. It's the same image as dev, so the engine is identical everywhere | Many companies run DB containers on dedicated hosts. The native package is the alternative; check at build time whether AL2023 packages pgvector |
| Data | Separate encrypted 20 GB gp3 data volume, `prevent_destroy`, same AZ as the app node | Replacing the instance never touches the data |
| Backups | Daily EBS snapshots via Data Lifecycle Manager (keep 7), **plus** the existing nightly `pg_dump` → S3 (ADR-0005) | Two independent mechanisms. RPO stays 24 h; WAL archiving (wal-g) for point-in-time restore is a later improvement |
| Network | Own security group: **inbound 5432 only from the app node's security group**. No SSH; admin via SSM Session Manager. Public subnet + public IPv4 for outbound; free S3 gateway endpoint for backups | A company would use a private subnet + NAT ($32/mo) or SSM interface endpoints (~$7/mo each). Isolation by security group is documented here, not hidden |
| Secrets | DB passwords in SSM Parameter Store SecureString (standard tier, free), read by services at start. Nothing in `.env` on servers | Standard managed-secret pattern |
| Access | Per-service Postgres roles (agent read-mostly, executor scoped) that mirror the privilege split | Enforced in the database, not just in the app |
| Environments | **staging** gets its own DB server from the same module, created by `make staging-up`, restored from the anonymised dump, destroyed after. **dev** keeps Postgres in a container on the dev server | Same engine and image everywhere; only *placement* differs in dev, which is the industry norm |

### Security measures

| Area | Measure |
|---|---|
| Network | Inbound 5432 only from the app node's SG. No SSH port; SSM Session Manager |
| Encryption | Encrypted gp3 at rest; Postgres TLS (`ssl = on`, `sslmode=require`) in transit |
| Secrets | SSM Parameter Store SecureString, never on disk or in Git |
| Least privilege | Per-service Postgres roles; the agent can insert proposals but can't alter audit tables |
| Accident protection | `disable_api_termination = true` on the instance; `prevent_destroy` on the data volume |
| Backups | DLM daily snapshots (keep 7) + nightly `pg_dump` → S3, restore-tested by every staging release |
| Patching | SSM Patch Manager on a schedule |
| Audit | `log_connections`, `log_disconnections`, failed-auth logging |
| Monitoring | `postgres_exporter` + `node_exporter` → Prometheus, disk-space alert (Phase 6) |

### Stopping: deliberate or a brake, never idle

The dev server stops itself after 60 idle minutes, because its users are people. The database's
user is the app, all the time. An idle stop would either never fire or take production down under
the app, so the database has none. It stops only in these ways:

| Stop | When |
|---|---|
| `make down` / `make up` | Pausing and resuming production. The app node and the database stop and start together |
| Nightly auto-stop | Only in the paused posture (Phases 4–6): an EventBridge Scheduler rule stops `Project=kaval` servers at 02:00 IST if `make down` was forgotten. A Terraform variable switches it off for the always-on Phases 7–9. ≈ $0 |
| $38 hard-stop Lambda | Month-to-date spend ≥ $38. It already covers this server: tagged `Project=kaval` and outside an ASG (`KAV-30`) |

### Stopping and starting without losing data

Stopping an EC2 instance keeps its EBS volumes, so Postgres resumes from the same disk and there is
normally nothing to restore. The risks are an unclean stop and a damaged disk:

| Safeguard | How |
|---|---|
| Graceful shutdown | The container gets `stop_grace_period: 60s`, and the Docker unit stops before the instance halts. EC2 stop → ACPI shutdown → systemd → Docker → SIGTERM → Postgres fast shutdown with a checkpoint. WAL crash recovery covers the worst case |
| Snapshot before every stop | `make down`, the nightly auto-stop and the hard-stop Lambda snapshot the data volume *before* `StopInstances`, tagged `Reason=pre-stop`. The Lambda gains `ec2:CreateSnapshot` scoped to `Project=kaval` volumes |
| Health check on start | `make up` and a boot unit run `pg_isready` plus a sanity query. **On failure, neither restores on its own** — both print the diagnosis-then-restore procedure and stop, so a flaky check can't trigger a destructive-ish volume swap unattended ([restore runbook](../runbooks/restore-from-backup.md), amendment below) |

The limit, stated honestly: while paused, the whole system is down, the app and the database
together. These safeguards guarantee **no data loss and a clean resume**, not uninterrupted
service. Continuous service is the Phase 7–9 always-on posture.

## Why not the alternatives

- **RDS Single-AZ (~$18/mo).** Managed backups and patching are real value. But it costs more than
  the self-managed server for no extra availability, and it hides the operations (patching,
  backups, restore, TLS, roles) this project exists to learn and demonstrate.
- **RDS Multi-AZ (~$36/mo).** Automatic failover is the real corporate answer for a critical
  database. But it would nearly double the whole project's monthly cost, for a single-user system
  whose outcome table can tolerate a 24 h RPO (ADR-0005).
- **Stay inside the node.** Cheapest, and still the right call for dev. For production it couples
  the database's life to a spot node and to the model's memory use, which is the failure this ADR
  exists to remove.

## Consequences

- **Cost.** ~$14.40/mo always-on (compute $8.18, root $0.73, data $1.82, IPv4 $3.65), plus small
  incremental snapshots. Prod steady state goes from ~$14 to **~$28/mo**. The project projection
  goes from ~$67 to **~$110**, still inside the $140 AWS credit, so out-of-pocket stays ≈ $0.
- **Ceiling $40**, alerts at **$30 / $35**, hard stop at **$38**, forecast alert at $40. $30 was chosen
  over a lower first alert because steady state is ~$28, and an alert that fires on normal spend
  trains people to ignore it. Applied to the prod budget on 2026-09-26.
- **The app node gets back ~250 MB**, which helps the local model fit. So `KAV-22` measures model
  memory **without** Postgres, because prod won't host it there.
- **Documented gaps.** There's no private subnet + NAT, so the security group does the isolating,
  and no Multi-AZ failover. Both are written down, not hidden.
- **Revisit** if the data outgrows 20 GB, if RPO under 24 h becomes a requirement (add wal-g
  first, then consider RDS), or if the account's credit runs out before v1.

## Amendment, 2026-10-04 — implementation (`KAV-32`)

Building the module surfaced decisions this ADR's original table didn't specify. Recorded
here rather than a new ADR, since none of them change the decision above, only how it's built.

- **Scope, chosen when asked.** This slice covers the server, its security group and data
  volume, TLS, per-service Postgres roles, and the two backup mechanisms (DLM snapshots, S3
  dump). Deferred to a follow-up story: pre-stop snapshots wired into `make down`/the nightly
  paused-posture auto-stop/the hard-stop Lambda, the nightly auto-stop schedule itself, the
  automatic boot-time health-check-and-restore (the runbook's manual `make db-restore-snapshot`
  path covers this for now), SSM Patch Manager, connection/failed-auth logging, and the staging
  database (blocked on `infra/envs/staging` — the second node — not existing yet).
- **TLS is a self-signed certificate, `sslmode=require` from services, not `verify-full`.**
  Encrypts the wire; doesn't authenticate the server's identity. A real CA is out of scope for
  a single-operator project. `pg_hba.conf` enforces it (`hostssl ... scram-sha-256` +
  `hostnossl ... reject`), so it's required, not merely available — the real network boundary
  underneath it is still the security group, per the table above.
- **The database's AZ is pinned to the network module's first subnet**, not derived from
  wherever the app node's spot capacity happens to land. The app node's AZ moves on
  reclamation; the database's must not. Accepted trade-off: a spot replacement into a
  different AZ costs one small cross-AZ data-transfer charge between app and database, not an
  outage.
- **Per-service Postgres roles are real, not just documented.** Each of `kaval_gateway`,
  `kaval_agent`, `kaval_executor`, `kaval_collector` is its own login role with its own
  Terraform-generated password (SSM Parameter Store SecureString) and its own `GRANT`s
  (`scripts/ops/db-roles.sql`), matching the read-only/scoped-write split `CLAUDE.md`
  constraint 3 already enforces at the Kubernetes RBAC layer (ADR-0021). Each service's own
  Kubernetes Secret carries its own role's credentials — one Secret per service, not one
  shared Secret, so a compromised `kaval_collector` credential can't read a `decision` row.
- **The chart's own in-cluster Postgres (`postgres.yaml`) gets a `postgres.enabled` switch**
  rather than being deleted outright — local/dev/CI keep using it unchanged; only an
  environment with its own database server (prod now, staging later) sets it `false`.
- **Known gap, found while building this, not yet closed:** the two pieces that actually
  materialize a live AWS credential into the cluster — a host-level refresh of temporary
  credentials into an `aws-creds` Secret (so the nightly backup CronJob can call `aws s3 cp`
  without pods reaching instance metadata directly, which the default IMDS hop limit blocks
  anyway) and the Helm hook Job that runs `db-roles.sql` (which has to combine five different
  Secrets' passwords into one `psql` invocation) — were both blocked by this environment's own
  safety classifier rather than written. The design for both is recorded here and in
  `docs/labs/lab-22-production-database.md`; Roshan builds or approves those two pieces
  directly rather than an assistant writing them unsupervised.

## Amendment, 2026-10-05 — pre-stop snapshot, and `make down` actually pausing the database (`KAV-32`)

The table above always said `make down` / `make up` pause the app node and the database
**together**. In practice `make down` only ever ran `terraform destroy -target=module.node` —
the database kept running, un-paused, billing the full ~$14.40/mo regardless of whether anyone
was using the system, ever since the 2026-10-04 apply. Closed now, alongside the pre-stop
snapshot this story was actually scoped to build:

- **`scripts/ops/pause-database.sh` / `resume-database.sh`**, called from `make down` / `make up`.
  Stop and start the database instance (never terminate — `disable_api_termination` and
  `prevent_destroy` still apply), idempotent either way. `pause-database.sh` snapshots the data
  volume first, tagged `Reason=pre-stop` plus `Project=kaval`/`Role=database-data` so it's
  findable by the exact query `docs/runbooks/restore-from-backup.md` already used.
- **The hard-stop Lambda** (`infra/modules/budget/lambda/hard_stop.py`) does the same
  snapshot-then-stop for every instance it stops, not just ones started by the scripts above —
  it already covered the database at the `$38` threshold (`stop_tagged_instances`, KAV-30); it
  just never snapshotted first. New IAM statements, necessarily broad the same way
  `ec2:DescribeInstances` was in ADR-0027: `ec2:DescribeVolumes` (no resource-level scoping
  exists), `ec2:CreateSnapshot` scoped to `Project=kaval`-tagged volumes, `ec2:CreateTags`
  scoped to `ec2:CreateAction=CreateSnapshot` (the snapshot doesn't exist to tag-scope against
  until the same call creates it). A snapshot failure is logged and swallowed, never blocks the
  actual stop — stopping the bill is this function's one job.
- **The third stop path, the nightly auto-stop schedule itself, doesn't exist yet** — it's the
  next unchecked `ROADMAP.md` line. The snapshot step above is written as a reusable script
  rather than inlined into `make down`, specifically so wiring it into that schedule later is
  one call, not a rewrite.
- **Found live, not guessed:** the DLM daily-snapshot policy (`aws_dlm_lifecycle_policy.database`)
  had been in AWS's `ERROR` state since the very first apply — a `terraform plan` run for this
  story was the first time anyone looked at its actual state in AWS rather than its Terraform
  config. Cause: `copy_tags = true` already copies the source volume's `Name` tag onto every
  snapshot; `tags_to_add` also set a `Name` tag; DLM rejects the resulting duplicate key outright
  and never retries on its own. No daily snapshot had ever been taken in the month this server
  has existed — the pre-stop snapshot this story adds was, until today, the *only* EBS-level
  backup this database actually had. Fixed by dropping `tags_to_add` (`copy_tags` alone already
  gives every snapshot `Name`/`Role`/`Project` from the volume); `terraform plan` confirmed
  `ERROR` → `ENABLED` as the only change to that resource.
- **Cost:** $0 change. EBS snapshots bill only for changed blocks since the last one, already
  priced into ADR-0008's "small incremental snapshots" line — the actual change here is that
  the daily ones are finally happening, not that anything new costs money. The database pausing
  with `make down` going forward is itself a cost *saving* against the gap just closed, not a
  new cost.

## Amendment, 2026-10-05 — nightly auto-stop, the third stop path (`KAV-32`, Lab 25)

Built the stop path the table above already named but that didn't exist yet: an EventBridge
Scheduler rule, 02:00 IST, as a brake for a forgotten `make down`.

- **Reuses the hard-stop Lambda rather than duplicating it.** At 02:00 IST the desired behaviour
  is identical to what `infra/modules/budget/lambda/hard_stop.py` already does at the $38
  threshold — scale the ASG to zero, snapshot-then-stop every `Project=kaval` instance outside an
  ASG. Rather than writing that logic twice, the new `aws_scheduler_schedule` targets the same
  Lambda. Both call paths already no-op correctly when there's nothing to do (ASG already at
  desired=0, instances already stopped), so a nightly firing when `make down` was *not* forgotten
  is silent and harmless, not a redundant action worth guarding against separately.
- **New resources, in `infra/modules/budget`:** `aws_scheduler_schedule.nightly_auto_stop`
  (`cron(0 2 * * ? *)`, `schedule_expression_timezone = "Asia/Kolkata"` — EventBridge Scheduler
  takes an IANA timezone directly, so "02:00" means 02:00 IST without a manual UTC-offset
  conversion to get wrong), a narrowly-scoped IAM role for `scheduler.amazonaws.com` that can
  `lambda:InvokeFunction` on exactly the hard-stop function and nothing else, and the matching
  `aws_lambda_permission`.
- **New Terraform variable, `nightly_auto_stop_enabled`** (default `true`), set explicitly `true`
  in `infra/envs/prod`. This is the variable the original table already promised: flip it to
  `false` once Phase 7 makes the system always-on, since stopping things nightly would then be
  wrong, not redundant.
- **Cost:** $0. EventBridge Scheduler's invocation volume here (one firing a night) is nowhere
  near its free tier, and the Lambda it calls was already billed for at the $38 threshold path —
  this just gives it a second trigger, not a second cost.

## Amendment, 2026-10-06 — the start-up health check (`KAV-32`, Lab 26)

Built the last of the three stopping-table safeguards: `pg_isready` plus a sanity query on
start, restoring from a snapshot only on failure.

- **Asks before restoring, rather than restoring automatically.** The original table (above)
  said "only on failure is the latest pre-stop snapshot restored," reading as fully automatic.
  Asked directly, Roshan chose to have a failed check stop and print the diagnosis-then-restore
  procedure instead: `make up` is already an attended, interactive command (it has its own
  billing confirmation prompt), and a destructive-ish volume swap — detach, tag damaged, attach
  a new one from a snapshot — fits that same attended pattern rather than running unattended off
  a single SSM round-trip that could itself be the thing that's flaky. Nothing in this story
  auto-restores; `make db-restore-snapshot` is always a deliberate, separate command.
- **Two trigger points, both read-only until a human decides to restore:**
  - `scripts/ops/health-check-database.sh`, run from `resume-database.sh` (so every `make up`
    gets one) and standalone as `make db-health-check`. Runs over SSM — there's no network path
    to 5432 from the operator's machine, only the app node's security group can reach it. On
    failure it exits non-zero, which stops `make up` before `terraform apply` runs, and prints
    the exact restore command.
  - A new systemd oneshot unit, `kaval-db-healthcheck.service` (`After=kaval-postgres.service`),
    added to `user_data.sh.tftpl`. Covers a reboot nobody ran `make up` for — AWS maintenance,
    a crash, instance retirement. There's no attended operator at boot to ask, so an unattended
    failure only `logger`s a warning (`journalctl -t kaval-db-healthcheck`) rather than
    restoring — consistent with the no-auto-restore decision above, just resolved the other way
    when asking isn't possible at all.
- **The boot unit is written, not yet live.** The instance resource carries
  `lifecycle { ignore_changes = [ami, user_data] }` deliberately (so an edited script can't
  silently replace a running database) — the same reason the TLS cert and `pg_hba.conf` changes
  in earlier amendments also only take effect on a genuine instance replacement. `terraform
  plan` after this change confirmed exactly that: no changes to apply. The `make up` health
  check has no such gap — it's a plain script, live immediately — and was run live against the
  running production database for this story (`make db-health-check`, read-only, passed).
- **`make db-restore-snapshot`** (`scripts/ops/restore-snapshot.sh`), the command both failure
  paths point to: stops Postgres over SSM, detaches the current data volume (tags it
  `Reason=damaged`, never deletes it — same `prevent_destroy` posture as the original), creates
  a new volume from a snapshot in the same AZ (the newest `Role=database-data` one if `SNAPSHOT`
  isn't given), attaches and mounts it, restarts Postgres, then re-runs the health check to
  confirm. Also reconciles Terraform state (`state rm` + `import` on `aws_ebs_volume.data` and
  `aws_volume_attachment.data`) so the next `terraform plan` doesn't try to detach the new
  volume and reattach the damaged one — without this step the module's Terraform state would
  silently point at a volume that no longer exists in the running configuration. **Not live-
  tested against prod** — an actual restore stops Postgres and swaps the data volume, which
  needs its own explicit go-ahead rather than piggybacking on this story's go-ahead for the
  (non-destructive) health check. Deferred to the next natural pause, same as the pause/resume
  scripts' own real stop-test in the 2026-10-05 amendment.
- **Cost:** $0. SSM commands and the extra systemd unit cost nothing; a restore creates one new
  gp3 volume sized the same as the one it replaces, a few cents, and only happens on deliberate
  command.

## Amendment, 2026-10-06 — the restore drill, run for real (`KAV-32`, Lab 27)

The previous amendment deferred the live restore test. This one is that test: a real,
confirmed, timed run of `make db-restore-snapshot` against prod, from a real DLM snapshot.

- **RTO ~3m51s**, confirmation to verified-healthy. Full writeup, including the two script bugs
  the drill found (Git Bash's path mangling on `--device /dev/sdf`, and the AWS CLI shorthand
  parser choking on a nested quote) and fixed on the spot, in [Lab 27](../labs/lab-27-restore-drill.md).
- **No data was actually at risk** — prod's `signal`/`incident` tables were empty before and
  after, since the event pipeline hasn't run continuously against prod yet. The drill proved the
  *mechanism*, not data survival; worth repeating once prod has real incident history.
- The old, pre-restore volume was reviewed and deleted by hand after the restored one was
  confirmed healthy and Terraform-tracked — `prevent_destroy` stops Terraform from destroying the
  module's managed volume, but it never applied to the detached one once it was no longer the
  resource Terraform was managing.
- No design change from the previous amendment — ask-before-restore stands. This amendment exists
  because the restore path is now a verified fact, not an untested script.
