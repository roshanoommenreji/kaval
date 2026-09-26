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
| Health check on start | `make up` and a boot unit run `pg_isready` plus a sanity query. **Only on failure** is the latest pre-stop snapshot restored ([restore runbook](../runbooks/restore-from-backup.md)). Restoring on every start would slow start-up and throw away good data |

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
