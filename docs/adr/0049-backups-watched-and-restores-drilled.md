# ADR-0049 — Backups that are watched and restores that are drilled

- **Status:** Accepted — the rules are unit-tested and the first drill ran for real against production's
  newest dump; the daily check's first live run is recorded in [Lab 49](../labs/lab-49-people-and-process.md)
- **Date:** 2026-10-13
- **Deciders:** Roshan
- **Story:** `KAV-82` (Phase 5, slice 6) · roadmap line "Alert if the nightly database dump is missing
  and a restore drill on a schedule"

## Context

[ADR-0038](0038-nightly-dump-is-taken-by-the-app-node.md) moved the nightly dump to a timer on the app
server, and the roadmap says plainly that "the timer firing is still unseen". A backup nobody watches
is a belief, and a backup nobody has restored is a hope. Two facts shape the design:

- **Production is parked most of the time**, and a parked production takes no dump and should not raise
  an alarm. "The newest dump is old" would be a false alarm most days.
- **The thing that watches must not be the thing that fails.** A check inside the cluster or the
  database server dies with them.

## Decision 1 — a daily check, run from GitHub, that knows when a dump is owed

`.github/workflows/backup-watch.yml` runs once a day (07:30 UTC) and `scripts/ops/backup_watch.py` applies
one rule: **for each 19:30 UTC in the last two days at which the app server was running, a dump must
exist from the hour after it.** "Was running" is read from the auto-scaling group's launch and
terminate history (kept for about six weeks), which also makes the exact time of the daily run
irrelevant. A server that was not up through 19:30 owes nothing.

Also checked: the newest dump is not truncated (under 10 KB) and has not shrunk to under half the usual
size, and a restore drill was recorded in the last 35 days.

Told by: one GitHub issue (opened, or commented on if one is open) and the run turning red, which
GitHub e-mails. **If the check itself cannot run, that is also reported**; silence is never "all fine".

Why GitHub and not a Lambda in AWS: it keeps working when AWS is parked or broken, needs no SNS
subscription to confirm, and the rule is plain Python in the repository with tests. Why not Slack
today: the only Slack connection lives in production's gateway, which is parked; a Slack webhook is
the upgrade when there is a place to keep it.

## Decision 2 — the check can only look

A separate role, `kaval-backup-watch` (`infra/modules/backup-watch`, free): assumable only by a job from
this repository on `main` (a scheduled run counts), and allowed to **list the dump folder** and **read
the auto-scaling history**. It cannot read a dump, write, or delete. It is not the publishing role: a
job that can push images must not be able to look at backups, and the reverse. The workflow has two
jobs with different powers, as `promote.yml` and `rollback.yml` do: `check` has AWS and cannot write to
the repository; `tell` can open an issue and has no AWS. Applying the role to production is a Terraform
apply that was **asked for, not assumed** (CLAUDE.md).

## Decision 3 — a restore drill is one command and a dated record

`make restore-drill` (`scripts/ops/restore-drill.sh`) downloads production's newest dump (read-only),
restores it **strictly** into a throwaway Postgres on the laptop, counts the rows in the tables that
matter, and writes `docs/drills/restore-<date>.md` with the data that would have been lost (the dump's
age), the time taken, and the estimated recovery time: download plus restore plus the measured cost of
replacing the server (about 5 minutes: Lab 27, `make staging-up`). Verdict rules are in
`restore_drill_record.py` and tested: a restore with errors fails; a missing table fails (a table that
a later migration added is required only of a dump from after that migration); a schema version this
repository never had fails; an estimate over the target fails. A failed drill is written as
`restore-failed-<date>.md`, so it never counts as a drill having been done.

An **empty** dump passes with a visible note rather than failing: it proves the mechanics and not that
real data survives, and production really was empty when the first drill ran. Failing it would make the
monthly drill impossible to pass until production holds data.

**Why not an unattended drill in CI:** the raw dump holds production data, which [ADR-0039](0039-staging-is-filled-from-a-copy-cleaned-on-the-laptop.md)
keeps off third-party machines, and a drill that replaces a whole server would need a CI role that can
build and destroy infrastructure. A monthly command plus an overdue alarm gives the same assurance with
neither risk.

## Decision 4 — objectives written down

`docs/architecture/disaster-recovery.md` states, for the database: **RPO 24 hours** (the nightly volume
snapshot) and **RTO 30 minutes**, with the measured pieces. The drill is judged against them.

## Found on the way

- **The dump has a hole the snapshot does not.** It is taken at 19:30 UTC only on nights production is
  running. Production used from 08:00 to 15:00 and then parked takes no dump that day; the dump on hand
  at the first drill was 63 hours old. The volume snapshots (03:00 UTC daily, kept 7, and one before every stop) are
  unaffected, so the stated RPO of 24 hours holds for the snapshots and **not** for the dump. The drill record says so
  whenever the dump is older than a day. Not changed here; an option is a dump at the moment production
  is parked (recorded in the roadmap).
- **Production's database held no rows** when the first drill ran (parked, little collected), so the
  first drill proves the mechanics only. It says so; repeat it once there is data.
- An older dump legitimately lacks later tables (the first drill's dump predated `notification`); the
  first version of the rule failed on that and was corrected, with tests for both directions.

## Rejected

- **Alert on "newest dump older than 26 h".** False alarm whenever production is parked.
- **A dead-man's-switch service (the dump pings an outside URL).** Same parked-production problem, plus
  an outside service to trust.
- **Drill by restoring into staging on a schedule.** Needs infrastructure rights in CI, and staging
  already restores a cleaned copy on every `make staging-up`; that exercises the path but not the raw
  production dump.

## Consequences

- One more thing runs every day, at zero cost (GitHub Actions on a public repository, free; a few S3
  list calls).
- The first live run of the daily check, and the production role, depend on a Terraform apply and two
  repository variables (`AWS_BACKUP_WATCH_ROLE_ARN`, `PROD_BACKUP_BUCKET`).
- Not built: a Slack message for a missing dump (production's Slack is the only one); a dump at park
  time; a drill with real data (needs real data).
