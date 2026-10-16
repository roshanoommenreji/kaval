# ADR-0057 — The database is brought up to date before a new version starts, and the permissions are handed out after

- **Status:** Accepted; reproduced and fixed on staging 2026-10-16 (see "Measured"); reaches production when it is next brought up
- **Date:** 2026-10-16
- **Deciders:** Roshan
- **Story:** `KAV-85` (Phase 5) · follows [ADR-0039](0039-staging-is-filled-from-a-copy-cleaned-on-the-laptop.md) (the order on a seeded staging: restore, migrate, permissions twice), [ADR-0051](0051-closing-phase-5-readiness-checks-prod-prometheus-and-the-disk-trend.md) (promote before production comes up) and the live run of 2026-10-14 ([Lab 50](../labs/lab-50-closing-phase-5.md))

## Context

Production is parked between sessions and rebuilt from Git at every boot, so **every bring-up is a fresh install of the
newest version over a database that stayed behind**. On 2026-10-14 that database was two migrations behind and two
things went wrong, both by hand-fixed ordering, neither visible on staging (whose database had been migrated already):

1. **The install waited on a migration that waited on the install.** The chart ran the migration as a Helm
   `post-install` hook. Helm waits for every Deployment to be ready before it runs a post-install hook, and the new
   executor cannot be ready without the new column. The install timed out (`InstallFailed: timeout waiting for
   Deployment ... executor`) and the hook never ran.
2. **A table the migration added had no permissions.** The node's bootstrap ends its first `db-roles.sql` run as
   soon as the schema *exists*, which on an old database is at once. The gateway then logged `permission denied for
   table notification` every 30 seconds until the script was started by hand.

[ADR-0039](0039-staging-is-filled-from-a-copy-cleaned-on-the-laptop.md) had already met the second one for a seeded
staging and added a second permissions run, but only there. The first one affects staging too, whenever it is seeded
from an older production copy; it did not show on 2026-10-10 only because the migrations in between added nothing the
new services needed.

## What real companies do

| Pattern | One line | Fits here? |
|---|---|---|
| Migrate first, then roll out | A pre-deploy step brings the schema forward; no new code starts against an old schema | **Yes.** Helm's `pre-install`/`pre-upgrade` hook is exactly this, with no new machinery |
| A separate migration stage in the deploy pipeline | The pipeline runs it, then deploys | Sound for a team with a pipeline server and a deploy host. We have neither; Flux pulls from Git on the node |
| Every service migrates itself on start | The first pod to start wins a lock | Races between two gateway copies and widens each service's database rights to the schema owner |

**Chosen:** the first. Its price is a rule, below: the old version keeps running while the migration runs.

## Decision

1. **The migration is a `pre-install` hook when the database is a server of its own** (`postgres.enabled: false`:
   staging and production). It stays `post-install` for the chart's own Postgres (local k3d and dev), where there is no
   database until the release creates one. Both keep `pre-upgrade`. The hook's prerequisites (the namespace, `ecr-cred`,
   the admin Secret) are created by the node's bootstrap before Flux is told about the release.
2. **The permissions are handed out a second time, once the release is Ready, on every boot** (ADR-0039 did it only for
   a seeded staging). The first run stays: it creates the service logins, without which the pods cannot start. The
   second run starts as its own background unit (`kaval-db-grants`, `systemd-run --no-block`) and is started last, so it
   neither holds up the rest of the boot (the heartbeat that feeds the "still alive" alarm) nor shares the temporary
   file with the first.
3. **A migration only adds** (the expand step of expand/contract). While the pre-upgrade hook runs, the old version is
   still serving; a migration that drops or renames something would break it. A check in CI (`scripts/dev/test_migration_rules.py`)
   fails a migration whose `upgrade()` drops a table or column, renames a table or column. A deliberate exception writes
   `# contract: <why the old version is already gone>` on a line of its own, which also puts the reason in the review.
4. **Every table a migration creates must appear in a `GRANT` in `scripts/ops/db-roles.sql`**, checked in CI. Three
   live failures (`incident_signal`, `outcome`, `notification`) were each a table with no grant.

## Security

- No new permission. The migration already ran as the admin role with the admin Secret; only *when* changes.
- The second permissions run reads the same stored passwords the first reads, from the same place, with the node's own
  role, and never prints them (the script still has no `set -x`).
- A hook that runs before the install runs before any NetworkPolicy of the release exists; it needs only the database,
  as before.

## Failure and rollback

- **The migration fails:** the install fails before any service starts. On an upgrade the old pods are untouched (the
  hook runs first). Flux retries three times, then reports the release failed. The "still alive" alarm rings on a
  production that never produces its heartbeat. The runbook says where to read the Job's log.
- **The second permissions run fails or the release never becomes Ready:** the script says so in the journal and the
  existing 6-hourly timer repeats it. Nothing worse than before.
- **Rollback:** revert the chart line and the script block. A migration that has run is undone only from the snapshot the
  runbook takes (`downgrade()` exists for the ones written so far).
- **Not covered:** a migration that is slow enough to time out the hook (5 minutes). The data here is small; the
  expectation is seconds. Noted so it is a known number, not a surprise.

## Observability

The Job's own log (`kubectl logs job/<release>-migrate-<n>`), `journalctl -u kaval-db-grants`, the HelmRelease
condition, and the existing "still alive" alarm. No new alert: a stuck start already shows as a missing heartbeat.

## Cost and capacity

No AWS cost, no new pod, nothing recurring. The same Job runs earlier. The live proof used about an hour of staging,
about 5 cents.

## Testing

Unit (`scripts/dev/test_migration_rules.py`, 20 tests): the hook is `pre-install,pre-upgrade` for staging and production
values and `post-install,pre-upgrade` with the chart's own Postgres (and the first two fail on the old chart); a
migration that drops or renames is flagged and a `downgrade()` that drops is not; a table only named in a comment is not
"granted"; every real migration only adds and every created table is granted; the bootstrap script is valid shell, the
second run follows the wait for Ready on every boot, and is started last. Live: see "Measured".

## People and process

Runbook [prod-rollout.md](../runbooks/prod-rollout.md): steps 5 and 6 become "only if the automatic order did not
work"; the plan shown at production's next bring-up will contain one extra change, the new server start-up script
(Terraform replaces the launch template; a running server is untouched). Anyone writing a migration reads rule 3.

## What this does not do

- It does not make a **breaking** schema change safe; it makes it visible (the `# contract:` marker) and leaves the
  expand/contract steps to the author.
- It does not test migrations against a copy of production's data before production. Staging's seeded copy is the
  nearest thing; a restore drill that also migrates is a later, separate item.
- It does not change production today. Production is parked; the new order is live there the first time it comes up.

## Measured

Staging, 2026-10-16, database rolled back two versions (`c3e8a1d7f402` to `a1c4f9b0e3d2`, the same distance as
production on 2026-10-14), Flux resumed so the release is installed afresh:

| | Before (setup as of `sha-06b64ba`) | After |
|---|---|---|
| Install result | `InstallFailed: timeout waiting for ... executor`, uninstalled, retried | *(filled in after the live drill, Lab 54)* |
| Migration Job | never created | |
| Hands needed | the migration run by hand | |
