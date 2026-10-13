# ADR-0050 — Monitoring and patching the database server

- **Status:** Accepted — built and validated (Terraform, chart, promtool, unit tests); the live staging
  proof is recorded in [Lab 49](../labs/lab-49-people-and-process.md). Production gets it with Prometheus
  itself (`KAV-83`).
- **Date:** 2026-10-13
- **Deciders:** Roshan
- **Story:** `KAV-82` (Phase 5, slice 6) · roadmap line "Database server monitoring" · completes what
  [ADR-0008](0008-production-database-on-its-own-server.md) deferred ("postgres_exporter + node_exporter
  -> Prometheus, disk-space alert; SSM Patch Manager schedule; connection and failed-auth logging")

## Context

The database is the one store that matters and it sits on its own server
([ADR-0008](0008-production-database-on-its-own-server.md)). Kaval watched the app server's disk and
its own services, but nothing watched this server: not its disk (a full data disk stops Postgres and
corrupts nothing but halts everything), not Postgres itself, not its patches, and nobody could see who
had connected or failed to. Three constraints decide how:

- **`ignore_changes = [user_data]`** on the instance: an edited boot script never reaches a server that
  already exists, and production's already does.
- **The app node is 4 GB and Prometheus is already tight** there; whatever is added must be small.
- **The server is parked most of the time**, so "it did not answer" must not page anyone while it is
  meant to be stopped.

## Decision 1 — two small exporters, read by the Prometheus that already exists

`node_exporter` (machine: disk space, memory, load; four collectors only) and `postgres_exporter`
(Postgres' own statistics), each in a container capped at 64 MiB, listening on the server's private
address, pinned by digest from quay.io. Prometheus on the app node scrapes them. The server's address
is not known when the chart is rendered, so an init container writes it into two target files from the
`POSTGRES_HOST` every service already receives (refusing anything that is not a plain host name or
address). The security group gains two inbound ports, 9100 and 9187, from the app node's group and
nothing else. About 30 MiB on the database server; Prometheus gains two targets.

Rejected: **CloudWatch agent** (a second metrics stack, and a custom metric per disk costs money);
**a script that checks `df` and posts** (a one-off nobody would extend); **Alertmanager** (still
rejected, [ADR-0043](0043-alertmanager-not-adopted-yet.md)).

## Decision 2 — the disk alert reuses the disk path built in the last slice

The poller asks Prometheus for the fullest real disk (root and data volume) and writes a
`disk_near_full` signal whose subject is `database-disk` (the app server's is `node-disk`). Everything
downstream is the existing machinery of [ADR-0047](0047-incidents-that-are-for-people-disk-full-and-slowness.md):
an incident named `disk_pressure`, for a person, no automatic proposal, a hand-filed restart classed
`never`, a Slack message (worded "the database server's disk"), the same runbook, and the 2-hour
escalation of [ADR-0048](0048-approvals-nobody-answers-expiry-and-escalation.md). Line: 85 %, as for the app
server. Two more rules, `KavalDatabaseDown` (Postgres unreachable or `pg_up` is 0, for five minutes) and
`KavalDatabaseMonitorDown` (the machine monitor silent: the disk is then not being watched), go through
the existing "Kaval's own health" path. Neither has an `absent()` term, on purpose: a parked server has
no series and must not page.

## Decision 3 — put it on the server with SSM State Manager, not boot-time user_data

A Command document holds `db-monitoring.sh` and an association runs it on every server named
`<env>-database`: when it is created or changed, daily, and **whenever a stopped server comes back
online after missing a run** (State Manager's documented behaviour, which is exactly this parked
pattern). It is idempotent. One mechanism reaches staging (rebuilt each time) and production (never
rebuilt) alike, and an edit to the script is a Terraform change that is visible in a plan. Free.

What the script does: creates a Postgres role `kaval_monitor` that is only a member of the built-in
`pg_monitor` (statistics, no table data, no writes) with a generated password in Parameter Store that
only this server's role can read (it is deliberately not in the list the app node may read); starts
the exporters as systemd services; turns on connection, disconnection and client-address logging;
and caps Docker's default container log size so that logging cannot fill the disk being watched
(applied at the next daemon start, because restarting the daemon would stop Postgres unclean).

## Decision 4 — patching: weekly, installed, never rebooted by the patcher

`AWS-RunPatchBaseline` with the default Amazon Linux 2023 baseline, `Install`, `NoReboot`, Sunday
19:00 UTC (a stopped server is patched when it next comes online). The patcher never restarts
Postgres in the middle of a working session; the nightly stop/start is a boot, so a kernel update
takes effect at the next start. Compliance is visible in Patch Manager.

## Found on the way

- A production `terraform plan` shows **existing drift unrelated to this change**: the app node's launch
  template (a newer machine image and boot script from earlier merged changes, among them the nightly
  dump timer) has never been applied. It only takes effect when the node is next created. It should be
  applied on purpose, with this change, and noted.
- A security group's description cannot be edited in place (it forces replacement), so the group's
  text still says "Inbound 5432 only" although it now also admits the two exporter ports. Left as is,
  and recorded here, rather than replace the database server's security group.

## Consequences

- `postgres_exporter` and `node_exporter` run un-audited upstream code on the most sensitive server.
  Mitigations: pinned by digest, 64 MiB cap, no inbound except from the app node, a read-only role with
  no data access, a password only this server can read.
- Production does not get the exporters until the Terraform apply (asked for, not assumed), and its
  Prometheus does not exist yet (`KAV-83`), so the production disk alert is unobservable until then.
- Not built: shipping Postgres logs off the server (CloudWatch would be a cost and a second stack); a
  query-level view (`pg_stat_statements`); alerting on connections near the limit.
