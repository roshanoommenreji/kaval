# ADR-0005 — Data durability, and seeding staging from production

- **Status:** Accepted
- **Date:** 2026-09-09
- **Deciders:** Roshan
- **Amends:** [ADR-0004](0004-environment-strategy-and-promotion.md) — supersedes its "accepted limitation" on staging data
- **Amended by:** [ADR-0008](0008-production-database-on-its-own-server.md) (2026-09-26): the data volume, the nightly dump and the staging restore now target a dedicated database server. Daily EBS snapshots (DLM, keep 7) and a snapshot before every stop are added alongside the dump. RPO stays 24 h.
- **Amended by:** [ADR-0038](0038-nightly-dump-is-taken-by-the-app-node.md) (2026-10-10): the nightly dump is taken by a systemd timer on the app node (19:30 UTC, using the node's own role), not by a chart CronJob. RPO is 24 h while production is running.
- **Amended by:** [ADR-0037](0037-staging-seeding-from-the-production-dump.md) (2026-10-10): the scrub is rewritten against the real schema as one transaction whose final check is "a second pass changes nothing", and the restore script fails closed. The table above lists what is sanitised; ADR-0037 has the current treatment.

## Context

ADR-0004 recorded an accepted limitation: staging is created fresh per release, so it holds no
accumulated data and cannot catch problems that only appear after months of production. Roshan
questioned it — *why can't the data live in a database or S3 so nothing is lost?*

The limitation did not survive the question. It was a design that had not been done, not a
constraint. Examining it exposed two problems that had been conflated.

**Production's recovery point objective was never decided.** The plan said "nightly `pg_dump` to
S3" and stopped. Nobody had asked what that implies: a volume failure at 23:00 loses a full day.

That matters more here than in a typical project. The `outcome` table records, for every proposal,
whether the action worked. It is the evidence base for promoting an action class from `ask` to
`auto` — the progressive-autonomy argument that is the point of the whole system. It accumulates
slowly, over months, and **it cannot be regenerated**. Losing a day of it is losing evidence.

**Staging was empty for no reason.** The nightly snapshot already exists in S3. Restoring it is a
handful of lines.

Worth separating what was already safe: production data lives on a **separate EBS volume**, not
the instance root. Spot reclamation — the *likely* failure — does not lose data; the volume
reattaches to the replacement node. The exposure is volume corruption, accidental deletion, AZ
loss, and the gap between dumps.

## Decision

### Production RPO stays at 24 hours — as a decision, not a default

Nightly `pg_dump` to S3. Options costed and rejected:

| Option | RPO | Extra RAM | Cost | Rejected because |
|---|---|---|---|---|
| Hourly dump + EBS snapshot | 1 h | 0 | ~$0.15/mo | Reasonable, but the failure it guards against is rare |
| Continuous WAL archiving | seconds | ~100 MB | ~$0.20/mo | ~100 MB of 1.2 GB headroom, plus a sidecar that can fail silently — archiving must then itself be monitored |
| **Nightly dump** | **24 h** | **0** | **~$0.05/mo** | **Chosen** |

The justification: the likely failure mode is spot reclamation, and the EBS volume already
survives it. Volume loss and AZ failure are genuinely rare. For a personal project, 24 hours of
lost incident history is annoying rather than serious, and the complexity budget is better spent
elsewhere.

**Revisit when** the `outcome` table holds enough rows that losing a day would materially weaken
the autonomy argument, or after the first near-miss. Both are cheap to act on — the dump schedule
is one line, and WAL archiving is a session's work.

### Staging is seeded from the latest sanitised production snapshot

`make staging-up` pulls the most recent dump, runs an anonymisation step **before restoring**, and
records how long the restore took. That duration is the measured RTO and goes into the next change
record.

This closes most of the parity gap: staging gets real data shape and real volume, so slow queries,
index behaviour and pagination bugs surface before production sees them.

It also pays a second dividend. The Phase 4 concept page asserts that *a backup is not verified
until you have restored one*. Restoring on every release makes that true continuously and
automatically, several times a month, rather than being an annual fire drill nobody schedules.

> **Update 2026-10-10 ([ADR-0039](0039-staging-is-filled-from-a-copy-cleaned-on-the-laptop.md)):** as built, the
> restore is not automatic on every release. Staging is filled from a copy cleaned on the laptop by `make seed-refresh`,
> so it is as fresh as the last refresh, and what the restore verifies is the restore path and the cleaner.

Cost: **$0**. The dump exists already; staging's EBS is already budgeted.

### What is sanitised, and why

| Field | Holds | Treatment |
|---|---|---|
| `decision.actor` | Cognito subject / email | Stable pseudonym |
| `signal.value` | Log excerpts — arbitrary application output | Truncated, pattern-redacted |
| `proposal.root_cause` | LLM text quoting those log lines | Same redaction |
| `execution.stdout` | `kubectl` command output | Redacted — see below |
| Anywhere | Account IDs, ARNs, hostnames | Placeholders |

This project's data is not personal data in any meaningful sense — it is cluster events and model
output. The anonymisation step exists anyway, because copying production data into a lower
environment unsanitised is the practice auditors flag, and a project citing ISO 42001 in its
governance story should not be doing the thing the standard exists to prevent. The *pattern* is
what gets asked about.

### Consequence found while designing this: redact `execution.stdout` at write time

The executor records command output so that actions are auditable. But `kubectl` output can
contain secret values — `kubectl get secret -o yaml` is the obvious case, and there are subtler
ones where a pod spec echoes an environment variable.

If redaction happens only when copying to staging, then **production itself stores secrets in a
table** — one the gateway reads and the mobile app renders.

So the executor redacts before it inserts. The staging anonymisation becomes a second line of
defence rather than the only one. This is a change to Phase 3's executor work, and it came out of
a question about data loss rather than a security review, which is worth noting.

## Consequences

**Easier.** Staging catches data-shaped problems. The backup is restore-tested several times a
month rather than never. The RTO is a measured number. The `execution.stdout` exposure is closed
before it exists rather than found in a Phase 9 audit.

**Harder.** An anonymisation script that must be maintained as the schema grows — a new column
holding something sensitive is a new line in `anonymise.sql`, and forgetting is silent. Worth a
test that asserts no account-ID-shaped string survives into staging.

**Cost.** Zero.

**Accepted risk.** Up to 24 hours of production data can still be lost to a volume failure. Stated
plainly, with a revisit trigger, rather than left as an unexamined default.

**Revisit if:** the outcome history becomes valuable enough to warrant continuous archiving, or if
the anonymisation script is ever found to have missed something — the second would argue for
generating staging data synthetically instead.
