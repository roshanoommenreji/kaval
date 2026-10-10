# Lab 39 — Scrubbing a copy of the production database, and proving it before staging is touched

**Phase:** 4 · **Story:** `KAV-72` · **Cost:** $0. Nothing in this lab starts an AWS resource.
Decisions: [ADR-0037](../adr/0037-staging-seeding-from-the-production-dump.md), building on
[ADR-0005](../adr/0005-data-durability-and-staging-seeding.md).

Staging is meant to be filled with last night's production data, with anything personal or secret removed
first. The script that removes it (`scripts/ops/anonymise.sql`) and the script that does the restore
(`scripts/ops/restore.sh`) were written in Phase 0 and had never been run. This lab runs both against a real
Postgres on your own machine, so they are proven before staging (which costs money) is switched on.

**Result:** the old scrub script would have failed on its first run (it treated a JSON column as text). The
rewrite passes 15 tests against the repository's real migrations; `restore.sh` was run end to end against a real
`pg_dump` for three cases: it works, it refuses to touch the production host, and when the scrub fails it
deletes the unscrubbed copy and exits with an error.

**Not covered:** anything on AWS. No production dump exists yet (the backup bucket is empty), and the restore
has not run on staging. See ADR-0037, "Not decided here".

## 0. What you need

A Postgres 16 with the `vector` extension, on `localhost`, that you do not mind filling with test rows. Either:

```bash
# Option A (Docker Desktop must be running). The same image the project uses everywhere.
docker run -d --name scrubtest -p 5432:5432 -e POSTGRES_PASSWORD=x pgvector/pgvector:pg16

# Option B (no Docker): an embedded Postgres in a throwaway virtual environment OUTSIDE the repository.
python -m venv ~/pgvenv && ~/pgvenv/bin/pip install pgserver   # Windows: Scripts\ instead of bin/
```

(Option B was used for the run described here, because Docker Desktop was off. `pgserver` starts a real
PostgreSQL 16.2 with pgvector 0.6.2 and ships `psql`, `pg_dump` and `pg_restore`.)

Point the project at it, then create the real tables from the real migrations:

```bash
export POSTGRES_USER=postgres POSTGRES_PASSWORD=x POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 \
       POSTGRES_DB=postgres POSTGRES_SSLMODE=disable KAVAL_REQUIRE_DB=1
alembic upgrade head
```

## 1. Run the scrub tests

```bash
python -m pytest services/shared/tests/test_anonymise.py -v
```

Expect `15 passed`. They run the script inside a transaction that is rolled back, so nothing stays in the
database. What they check:

| Test | Promise |
|---|---|
| `test_nothing_sensitive_survives` | An account number, ARN, access key, JWT, email, EC2 host name, Slack token and PEM private key planted in every free-text column are all gone. |
| `test_what_staging_needs_survives` | Pod names, restart counts, 13-digit timestamps and JSON numbers are untouched; strings are cut at 500 / 2000 characters. |
| `test_same_person_keeps_the_same_pseudonym` | Two approvals by one person stay one person; two people stay two. A name typed into a free-text reason is gone. |
| `test_running_it_twice_changes_nothing` | A second run changes no row (this is also what the script's own final check asserts). |
| `test_every_text_column_is_classified` | The guard: a new text or jsonb column that nobody decided about fails the build. |
| `test_scrub_text_is_idempotent[...]` | Edge strings: empty, 11 digits, 13 digits, two ARNs in a row, an email followed by a full stop. |

**Prove the tests can fail.** Break one rule on purpose (for example change the email replacement to `'\0'`),
rerun, and watch tests fail; then restore the file. A test that has never failed has proved nothing. The first
run of these tests failed twice for a real reason: a planted 12-digit JSON *number* survived. That is the
decision recorded in ADR-0037 (numbers are left alone), and the test was changed to say so rather than the
script changed to corrupt real figures.

## 2. Run `restore.sh` end to end

You need a "production" database with dirty rows, a dump of it, and something that answers `aws s3 cp` in place
of S3.

```bash
# a source database with the real schema and a few dirty rows
psql -d postgres -c "CREATE DATABASE kavalsrc" -c "CREATE DATABASE kavalstage"
POSTGRES_DB=kavalsrc alembic upgrade head
ACCT="123456""789012"   # a made-up account number, in two halves so the repo's secret check does not trip on it
psql -d kavalsrc <<SQL
INSERT INTO signal(id,source,kind,target,value,observed_at) VALUES
  (gen_random_uuid(),'k8s_event','oom_killed','ip-10-60-2-54.ap-south-1.compute.internal/kaval-demo/checkout',
   '{"restart_count":4,"msg":"role arn:aws:iam::${ACCT}:role/x used AKIAABCDEFGHIJKLMNOP mail a.b@corp.example"}', now());
SQL
pg_dump -d kavalsrc -Fc -f prod.dump

# a stand-in for `aws s3 cp`, first on PATH: it answers for the pointer file and for the dump
mkdir fakebin && cat > fakebin/aws <<'EOF'
#!/usr/bin/env bash
src="$3"; dst="$4"
if [[ "$src" == */postgres/LATEST ]]; then echo "postgres/kaval-20261010T000000Z.dump"; else cp prod.dump "$dst"; fi
EOF
chmod +x fakebin/aws
```

On Git Bash for Windows put the stand-in on `PATH` in the `/c/Users/...` form. A `C:/...` entry contains a
colon, which splits `PATH` in the wrong place, and the real `aws` is found instead (this happened).

**2a. It works.**

```bash
PATH="$PWD/fakebin:$PATH" BACKUP_BUCKET=fake STAGING_POSTGRES_HOST=127.0.0.1 STAGING_POSTGRES_DB=kavalstage \
  STAGING_POSTGRES_USER=postgres STAGING_POSTGRES_PASSWORD=x bash scripts/ops/restore.sh staging
psql -d kavalstage -At -c "select value::text from signal"
```

Expected: `sanitised - the final check passed`, exit 0, and the row reads
`{"msg": "role arn:aws:REDACTED used AKIAREDACTED mail user@example.com", "restart_count": 4}`.
`.build/last-restore.json` records `"status":"ok"` and the seconds taken.

**2b. It refuses production.** Add `POSTGRES_HOST=127.0.0.1` (the same host) and rerun. Expected:
`refusing: STAGING_POSTGRES_HOST is the same host as POSTGRES_HOST (production)`, exit 1, nothing dropped.

**2c. It fails closed.** Copy `restore.sh` and `anonymise.sql` into a scratch folder shaped `scripts/ops/`, append a
statement that always fails to the copy of `anonymise.sql` (`DO $f$ BEGIN RAISE EXCEPTION 'deliberate'; END $f$;`),
and run that copy of `restore.sh` against an empty database. Expected: `sanitising FAILED - dropping the restored
tables`, exit 1, `"status":"failed"`, and `select count(*) from pg_tables where schemaname='public'` returns `0`.

## 3. Clean up

```bash
docker rm -f scrubtest      # Option A
# Option B: stop the Python process that started pgserver, then delete the virtual environment
```

## What to take from this

- **A script nobody has run is a draft.** Both files had sat in the repository for a month, described as
  "written, not yet run" in the repository guide, and the scrub would have failed on its first line that touched
  the JSON column.
- **Check after, and roll back if the check fails.** The old script committed first and checked second.
- **Prove a restore, not just a backup.** The nightly dump had never run, so there was no real copy to restore.
  The piece that takes it is now built without any new credential ([Lab 40](lab-40-the-nightly-dump-from-the-app-node.md)),
  but it still has to run once on AWS before a restore into staging can be tried.
