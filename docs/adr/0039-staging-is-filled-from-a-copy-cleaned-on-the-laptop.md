# ADR-0039: Staging is filled from a copy cleaned on the laptop, and the order is restore → upgrade → permissions

**Status:** Accepted. Built and proven locally (Lab 41); **the live run on AWS is still to do** (see "Not proven").
**Date:** 2026-10-10
**Related:** [ADR-0037](0037-staging-seeding-from-the-production-dump.md) (the scrub and `restore.sh`; its
"Not decided here" is settled by this ADR), [ADR-0038](0038-nightly-dump-is-taken-by-the-app-node.md) (the dump
this starts from), [ADR-0005](0005-data-durability-and-staging-seeding.md) (staging is seeded from a sanitised
production copy), [ADR-0004](0004-environment-strategy-and-promotion.md) (staging is a real second cluster),
`KAV-73`, Lab 41

## Context

ADR-0037 built the scrub and left four things open. This decides the first three; the fourth (a dump has to
exist) was closed by ADR-0038 and the first real dump on 2026-10-10.

1. **How staging reaches the data.** Staging's database server has no S3 access, and staging has its own backup
   bucket, separate from production's. Something has to carry a dump from one to the other.
2. **Order of events.** The restore must finish before any service connects; the Helm migration then has to move
   the restored data from production's schema version to the release being tested.
3. **Permissions.** `db-roles.sql` hands each service its own database role access to its tables.

## Decision

**1. Clean on the laptop first; only the cleaned file crosses (`make seed-refresh`).** A person runs
`scripts/ops/seed-refresh.sh` on their own machine when they want staging to have fresher data. It reads
production's newest dump (read-only), restores it into a throwaway Postgres in Docker, runs `anonymise.sql`
there, checks the result a second way, and writes `.build/seed/clean.dump`. The raw copy is deleted when the
script ends. `make staging-up` then uploads the cleaned file to **staging's own bucket** (`seed/`), and the
staging node fills its database from it.

What follows from that:
- **No environment holds a key to the other.** Staging never reads production's bucket; production's data
  never reaches staging in raw form (not in staging's bucket, not in staging's database).
- **The laptop is needed only to refresh the file**, not to run anything nightly. Production's own timer takes
  the dump by itself (ADR-0038); `staging-up` is run from the laptop anyway.
- **Staging still scrubs on arrival.** `restore.sh` runs `anonymise.sql` again. The scrub is idempotent and its
  last step proves a second pass changes nothing, so this costs nothing and catches a file that was not cleaned
  (for example one put in the bucket by hand).

**2. The second check is independent of the scrub.** After scrubbing, `seed-refresh.sh` reads the cleaned dump
as plain text and refuses to keep it if anything still looks like an access key, an AWS account ARN, a JWT, a
private key, a Slack token or an e-mail address (the scrub's own `user@example.com` is allowed; 12-digit numbers
are deliberately not checked, as in ADR-0037 point 3). Only line numbers are printed, never the matched text.
The scrub proves itself with its own code; this shares none of it.

**3. The order is: restore (and scrub) → Flux → migration → permissions.**
- `kaval-db-seed` (a script on the staging node) runs inside `kaval-gitops-bootstrap` **before** the lines that
  tell Flux about the release. No service exists yet, so none is connected during the restore.
- The node seeds only a database **with no tables** and only if the bucket holds `seed/LATEST`. A resumed
  staging keeps its data and skips; so does every later 6-hour run of the bootstrap.
- It waits up to 10 minutes for `seed/LATEST` or `seed/NONE`, because `staging-up` uploads one of the two just
  after Terraform finishes, about when the node boots. `NONE` means "no cleaned copy on this laptop": staging
  starts empty, as before, and says so.
- Flux installs the release; the migration Job (`alembic upgrade head`) moves the restored data to this
  release's schema.
- **Permissions run twice on a seeded boot.** The existing wait ("until the `signal` table exists") ends at once on
  a restored database, before the migration has run, so a table the migration adds would get no grants. The first
  run still has to happen early: it creates the service logins, without which the pods cannot start. Then the
  script waits for the HelmRelease to be Ready (the migration is part of that) and runs `db-roles.sql` again.
  It is idempotent.

**4. The dump's own permissions are ignored on staging.** `restore.sh` passes `--no-privileges` for the staging
target. The dump's GRANTs name production's service roles, which do not exist on a fresh staging server, and each
would be an error (shown in Lab 41: `errors ignored on restore: 1`, exit 1). `db-roles.sql` stays the only place
that hands out permissions. The production target (disaster recovery) keeps them.

**5. Failure leaves staging empty and says so; it never leaves unclean data.** If the cleaned copy is missing,
unreadable or the scrub fails, the node writes `/var/lib/kaval/seed-status.json` (`skipped` or `failed`), the
scrub's own fail-closed step has dropped any restored tables, and the release comes up on an empty database
exactly as every release did before. `staging-up` reads that file and prints `Seed status: ...`, with a warning
unless it says `ok`.

**6. Staging only.** A new node-module switch `seed_database` (default false, set only in `infra/envs/staging`)
installs the script and the bootstrap calls. Production's boot script has no seed call. It is not byte-identical
to before, though: the permissions step in the shared bootstrap is now a function (a no-behaviour-change
refactor needed by point 3), so production's launch template shows an update at its next plan and it takes
effect on the next `make up`, after staging has run the same text.

**7. Staging's bucket may be destroyed with files in it.** The backups module gets `force_destroy`, true for
staging only. Without it `make staging-down` would stop on `BucketNotEmpty` the first time the bucket held the
seed (or one of staging's own dumps, ADR-0038). Production keeps false: its dumps are the recovery point.

## Alternatives rejected

- **Staging reads production's bucket directly (a read-only key).** Simplest, and automatic. Rejected: it is a
  permanent door from the less protected environment to production's real data, and staging runs release
  candidates that have not yet been proven.
- **The release workflow copies the dump.** Rejected: it would give the GitHub publishing role (public
  repository, today allowed only to push images) a way to read production's data.
- **Carry the raw dump and scrub it only on staging** (ADR-0037's design as written). Rejected in favour of
  cleaning first: raw data would sit in staging's bucket and, between `pg_restore` and the scrub, in its database.
- **No real data at all (generated data).** What banks and hospitals do. Kept as the fallback ADR-0005 already
  names if the scrub is ever found to miss something; it would not restore-test a real backup.

## Consequences

**Easier.** Staging rehearses releases against realistic data, and the migration is tried over existing rows.
The restore is timed on every fresh staging, and that figure is the measured RTO for the change record.

**Harder / accepted.**
- **The cleaned file is as fresh as the last `make seed-refresh`.** Production is parked most days and its data
  changes only when it runs, so this is rarely stale. If a fully automatic refresh is wanted later, the next step
  is to run the same script on a small always-reachable machine (the dev server) instead of the laptop; it is
  not built.
- **A staging that comes up empty and is then resumed stays empty** (its first migration created tables, so the
  node skips). To seed it, `make staging-down` and `make staging-up` again.
- Docker must be running on the laptop for a few minutes when refreshing.
- The node fetches `restore.sh` and `anonymise.sql` from `main` at boot, the same trust it already gives
  `backup.sh` and `db-roles.sql` (ADR-0038). Merging this is what makes the live run possible.
- Changing `restore.sh` is a change to the `backup` image's inputs, so merging starts a normal release build.

**Cost.** No new resource and no recurring spend. The live run is a staging session of one to two hours at
about $0.045/hour (roughly 10 cents), plus pennies of S3 for a 20 KB file.

## Proven so far (Lab 41)

Against a real Postgres 16 with pgvector and the repository's real migrations, with stand-ins only for AWS:
- `seed-refresh.sh` on a deliberately dirty dump (an account ARN, an access key id, an e-mail): the cleaned file
  holds none of them and is readable. With the scrub replaced by one that does nothing, the independent check
  refuses and no file is kept; with a scrub that errors, nothing is kept; with no `LATEST`, it stops with a
  message. No throwaway container is left behind.
- The node's `kaval-db-seed`, extracted from the script Terraform renders, run in a Linux container against an
  empty database: no seed yet (fails after the wait, empty, status `failed`); `seed/NONE` (exit 10, `skipped`);
  `seed/LATEST` (exit 0, 10 tables, the scrubbed row, `alembic_version` at head, status `ok`); a second run
  (exit 10, nothing restored over the data); a broken scrub (exit 1, **0 tables left**, `failed`); a seed that
  still carries production's GRANTs (restored cleanly with no grants; without `--no-privileges`, exit 1).
- `staging.sh`: the seed upload (first time, already-done, no cleaned copy) and the status report (`ok`,
  `skipped`, `failed`, nothing recorded).
- The rendered boot script is valid shell for staging, production and no-bucket; zipped it is about 11,000
  characters for staging (limit 16,384).
- `terraform validate` and `fmt` clean; read-only plans: production `0 to add, 2 to change, 0 to destroy`,
  staging creates only its own resources.

## Not proven

- **Everything on AWS**: the node waiting for and finding `seed/LATEST`, the real TLS restore, the migration over
  restored data, the second permissions run after the release is Ready, the restore time on the real server.
  That is the live drill (staging, about 10 cents).
- Whether the real production dump (not the local stand-in) cleans without tripping the independent check.
- `make staging-down` with a populated bucket (`force_destroy`).
