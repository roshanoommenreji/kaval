# Lab 89 — A database dump at the moment production is parked

**Phase:** 8 (hardening) · **Story:** `KAV-134` · **Time:** about 30 minutes for Parts 1 to 3; Part 4 happens at production's next park · **Cost:** $0 for Parts 1 to 3 (your laptop only); a few cents for the production minutes in Part 4

The nightly database dump only happens on nights production is running, so a parked production leaves the newest dump days old, and a restore drill on it must be recorded as failed. This lab adds a fresh
dump as the first step of parking ([ADR-0087](../adr/0087-production-takes-a-database-dump-as-it-is-parked.md); the hole is described in [ADR-0049](../adr/0049-backups-watched-and-restores-drilled.md)).

## Prerequisites

- The repository checked out, `make sync`, bash available (Git Bash on Windows)
- For Part 4 only: AWS access (`AWS_PROFILE=kaval AWS_REGION=ap-south-1`) and production running

## Part 1 — See the gap

```bash
make restore-drill        # read-only; writes docs/drills/restore-<date>.md
```

If the newest dump is more than a day old, the record says FAILED even though the restore itself was clean. That is the 2026-11-08 situation ([record](../drills/restore-failed-2026-11-08.md)).

## Part 2 — What was built

`scripts/ops/dump-before-park.sh`:

1. Finds the running app server by its `Name` tag (`kaval-prod`). None running: prints "already parked" and exits 0.
2. Sends one Session Manager command that runs `set -e`, `systemctl start kaval-db-backup.service` (a one-shot unit, so this waits until the dump finishes) and prints the unit's log from the last five minutes. The parameters go in a temporary JSON file because the log line contains quotes.
3. Waits up to four minutes for the command, prints the log (bucket name replaced by `<bucket>`), and counts the dump as taken only when the log contains `backup.sh`'s finished line and no "NOT uploading".
4. Failure: exit 1, "Nothing has been scaled down", and the hint `SKIP_DUMP=1 make down`.

`make down` runs it first. The same step is in [prod-rollout.md](../runbooks/prod-rollout.md), section 8.

## Part 3 — Prove the decisions without AWS

```bash
python -m pytest scripts/dev/test_dump_before_park.py -q      # 10 passed
```

The tests check: no syntax errors and nothing runs on `source`; a finished upload counts and an empty log, a half log and a refusal do not; the bucket name is hidden; `SKIP_DUMP=1` is loud and needs no AWS;
and in the Makefile the order is dump, then the marker, then `terraform apply`, then `pause-database.sh`. To see one guard bite, move the `@bash scripts/ops/dump-before-park.sh` line in `down:` below `terraform apply`: the order test fails. Put it back.

## Part 4 — The live proof (at production's next park)

Do these on production's next start-up, with his go. They are not done yet.

1. Before parking, note the newest dump: `aws s3 ls s3://<backup bucket>/postgres/ | tail -2` (the bucket name comes from `terraform output backup_bucket_name`; do not write it into git).
2. Park with `make down` (or the runbook's step 8 with `bash scripts/ops/dump-before-park.sh` first). Expect the script to print the unit's log ending in `done.` and "Dump taken and uploaded. Safe to park."
3. List the bucket again: a new `kaval-<timestamp>.dump` from today, and `postgres/LATEST` pointing at it.
4. Run `make restore-drill` the same day. Expect PASS with a dump a few minutes old, and no need for a hand-made dump.
5. The negative case: with production up, `SKIP_DUMP=1` prints the warning and does not touch the app server. (Do not break the real unit to test the failure path; the tests cover the refusal logic.)

## What can go wrong

- **"The dump did NOT finish"** with status `Failed`: read the stderr it prints. Common causes: the database is not up yet, the node's Postgres client is older than the server (`pg_dump` refuses, ADR-0038), or the instance role lost its write to the bucket.
- **Status `TimedOut` or no output:** Session Manager could not reach the server; check it is registered (`aws ssm describe-instance-information`).
- **A park that must happen now and the dump is broken:** `SKIP_DUMP=1 make down`. The pre-stop volume snapshot is still taken.

## Done when

- [x] The script, the `make down` step and the runbook step exist; 10 tests pass
- [ ] The first live run at production's next park is recorded (new dump listed, `LATEST` moved, a same-day restore drill passing)
