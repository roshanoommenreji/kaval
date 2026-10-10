# ADR-0037: Staging is seeded from the production dump, and the scrub fails closed

**Status:** Accepted for the scrub and the restore script (built and proven locally). **Open** for the wiring
(how staging gets at the dump, and in what order things start); see "Not decided here".
**Date:** 2026-10-10
**Related:** [ADR-0005](0005-data-durability-and-staging-seeding.md) (decided that staging is seeded from a
sanitised production snapshot; this builds it),
[ADR-0004](0004-environment-strategy-and-promotion.md) (staging is a real second cluster),
[ADR-0008](0008-production-database-on-its-own-server.md) (the database servers),
[ADR-0029](0029-staging-parks-itself-when-idle.md) (`make staging-up`), `KAV-72`, Lab 39

## Context

ADR-0005 decided in September that staging would be restored from last night's production dump, with personal
and secret data scrubbed first. It was never built. `scripts/ops/restore.sh` and `scripts/ops/anonymise.sql`
were written in Phase 0 and have not run once. Until that is done every release has been tried against an
empty database, so nothing has shown how a new version behaves with realistic data, or how its migration
behaves over rows that already exist.

Reading the old scrub script against the schema that exists now found it would have failed on first contact:

- `signal.value` is a `jsonb` column; the script treated it as text (`left(value, 500)`, `value ~ '...'`), which
  Postgres rejects.
- It never touched `action.params`, `action.target`, `signal.target`, `incident.fingerprint` or
  `decision.reason`, all of which carry free text.
- Its checks ran **after** `COMMIT`. A failed check stopped the script, but the already-committed, partly
  scrubbed data stayed in the database.
- `COMMENT ON DATABASE CURRENT_DATABASE IS ...` is not valid SQL (the comment command takes a name, not an
  expression).

None of this was visible because nothing had run it. Also found: the S3 backup bucket is **empty**. The
nightly dump has never run (the chart job needed an `aws-creds` Secret holding a live key; [ADR-0038](0038-nightly-dump-is-taken-by-the-app-node.md)
replaces it with a timer on the app node that needs no new credential, built but not yet run on AWS).
There is no production dump to restore yet.

## Decision

**1. The scrub is rewritten against the real schema, as one transaction.** `anonymise.sql` has no `BEGIN` or
`COMMIT`; `restore.sh` runs it with `psql --single-transaction`. Either the whole scrub and its final check
commit, or none of it does.

**2. The final check is "scrubbing a second time changes nothing".** Instead of a hand-written list of things
that must not remain (which only catches what someone thought of), the check runs the same scrubbing functions
over every scrubbed column again and fails if any value would change. Every replacement is chosen so that it
is itself unchanged by another pass. This catches a value the first pass missed for any reason, including a
newly-shaped log line.

**3. jsonb is walked, not cast to text and back.** Temporary functions (`pg_temp`, so they leave nothing in
the staging database) scrub every string value and key at any depth. Casting a whole document to text, running
a regular expression and casting back can produce invalid JSON (a replacement eating a backslash) or damage a
number. Numbers are deliberately left alone: a 12-digit JSON number is as likely a byte count as an account
id, and account ids with a leading zero arrive as text anyway.

**4. What is scrubbed:** account numbers (12 digits not part of a longer number), ARNs, access key ids, JWTs,
PEM private keys, Slack tokens, email addresses and EC2 host names. Strings in `signal.value` are cut to 500
characters and `execution` fields to 2000, **after** scrubbing (cutting first could slice a secret in half
into a fragment no pattern recognises). `decision.actor` becomes a stable pseudonym (the same person stays the
same person across rows); `decision.reason` is replaced outright, because no pattern finds a person's name.

**5. A guard against forgetting.** ADR-0005 listed "forgetting is silent" as the cost of this design. A test
(`services/shared/tests/test_anonymise.py`) reads the live schema and fails if any text or jsonb column is
neither scrubbed nor on a short list of columns that hold no free text, with the reason. Adding a column to
a migration without deciding about it fails CI.

**6. `restore.sh` fails closed and cannot be aimed at production.** If the scrub fails, the restored tables
are dropped, the exit code is non-zero and `.build/last-restore.json` says `"status":"failed"`. In staging mode
it refuses to run when `STAGING_POSTGRES_HOST` equals `POSTGRES_HOST`, because `pg_restore --clean` drops
tables.

**7. Run it while no service is connected.** `pg_restore` puts the unscrubbed copy into the database and the
scrub runs after. ADR-0005's "anonymise before anything can read it" is only true if nothing is reading in
between. The restore must therefore happen before the application starts (see below).

## Alternatives rejected

- **Scrub in prod's dump, upload only the scrubbed copy.** Cleaner trust-wise (unscrubbed data never leaves
  prod's side) but it makes the production backup job do two things and hide the real dump. Revisit if
  unscrubbed data landing on the staging database host is ever judged unacceptable.
- **Generate synthetic data instead.** ADR-0005 already names this as the fallback if the scrub is ever found
  to miss something. It would not restore-test the real backup, which is half the reason for seeding.
- **A blocklist check after the scrub** (the old design). Replaced by the second-pass check, which needs no list.

## Consequences

**Easier.** A scrub that is tested against a real database in CI, whose coverage cannot silently fall behind the
schema. A restore that cannot leave an unscrubbed copy behind.

**Harder.** Every new free-text column needs a statement in `anonymise.sql` and a line in the test.

**Cost.** Nothing recurring. This decision added no AWS resources.

**Proven so far** (Lab 39): the scrub against the repository's real migrations on a real Postgres 16 with
pgvector, 15 tests; `restore.sh` end to end against a real `pg_dump` with a stand-in for S3 on the success
path, the failed-scrub path (0 tables left, exit 1) and the refuse-production path.

**Not proven.** Anything that touches AWS or the staging cluster. No production dump exists, and the restore has
never run on staging.

## Not decided here (needs a live run and Roshan's go-ahead; story `KAV-73`)

1. **How staging reaches the dump.** The staging database server's role can read only its own SSM parameters; it
   has no S3 access, and the staging backup bucket is separate from prod's. Either staging's role gets
   read-only access to prod's bucket (a cross-environment permission), or the dump is copied across by the
   release workflow. This is a security choice.
2. **Order of events.** The restore must run after the database exists and before the application connects,
   while the Helm migration job (run on every release) must then move the restored data forward from prod's
   schema version to the new one. That is the point of seeding, and also the thing most likely to go wrong.
3. **Permissions after the restore.** `db-roles.sql` grants each service its own database role access. A
   restored table either carries the grants in the dump or loses them, depending on the order in (2).
4. **A dump has to exist.** The timer that takes it is built (ADR-0038, `KAV-74`) but takes effect only when
   production is next switched on, and "nightly" then means nightly while it is running. Until a real dump
   exists, a drill can only use a synthetic dump made from a scratch database (staging now installs the same
   timer, so a dump of staging's own database into staging's own bucket is a free dress rehearsal), which tests
   the restore and the scrub but not real production data.
