# ADR-0087: Production takes a database dump as it is parked

**Status:** Accepted on 2026-11-12 (Roshan: "go", taking `KAV-134` as the next item). Built, and **proven live on production the same night** (see "Proven live").
**Date:** 2026-11-12
**Related:** [ADR-0005](0005-data-durability-and-staging-seeding.md) (RPO 24 h, the nightly dump),
[ADR-0038](0038-nightly-dump-is-taken-by-the-app-node.md) (the app server takes the dump),
[ADR-0049](0049-backups-watched-and-restores-drilled.md) (the hole this closes, the restore drill),
[Lab 89](../labs/lab-89-a-database-dump-at-the-moment-production-is-parked.md), `KAV-134`

## Context

The nightly dump runs at 19:30 UTC from a timer on the app server, and only on nights the app server is running. Production is parked most of the time. On 2026-11-08 the monthly restore drill
restored the newest dump cleanly in one second, yet had to be recorded as **failed**, because that dump was 12 days old ([record](../drills/restore-failed-2026-11-08.md)): a failed drill does not count, and
a dump that old is not a backup of what production holds today. ADR-0049 named the hole and left an option open: a dump at the moment production is parked. The volume snapshots are not affected
(one is taken before every stop), so the data was never at risk; the dump is the copy that can be restored on its own, and the one staging is seeded from.

## Options (what real companies do)

1. **Leave it; run the drill only after a night production ran.** Costs nothing; the drill keeps depending on luck, and "last backup was 12 days ago" stays true on most days.
2. **Dump from the laptop (or a Lambda) against the database server.** Needs the database password and network reach from outside; a new credential in a new place.
3. **Ask the app server's own dump unit to run once, as the first step of parking.** The server already holds the tools, the instance role, the Postgres 16 client and the checked, shared `backup.sh`.
   The usual pattern: "take a final backup before shutdown", reusing the one backup path so there is one thing to trust.

## Decision

**Option 3.** `scripts/ops/dump-before-park.sh` finds the running app server (`Name=kaval-prod`), starts `kaval-db-backup.service` over Session Manager, waits, and reads the unit's own log from the last
five minutes. It counts the dump as taken only if the log shows the finished upload (`backup.sh`'s last line) and no refusal. `make down` runs it **first**: before the park marker is written,
before the app server group is scaled to 0 (the server is what holds the tools) and before the database is stopped (it must be running to be dumped).

- **A failed dump stops the park.** Nothing has been scaled down, the marker file is untouched, and the message says how to continue. A park that quietly skipped the dump is exactly the hole being closed.
  Cost of this choice: a broken dump keeps the app server billing (about 4.5 cents an hour) until it is fixed or skipped.
- **`SKIP_DUMP=1 make down` skips it on purpose** (for example the database itself is the problem and stopping is the point). It prints that the newest dump may be old and a drill on it will be recorded as failed.
- **Already parked is not an error:** no running app server means there is nothing to dump from, and the script says so and exits 0.
- **No new permission, key or copy of the logic.** The unit, the role, the S3 write and the upload check (`pg_restore --list` before upload, ADR-0038) already exist. The bucket's name is hidden from what the script prints.

## Not covered (on purpose)

The two automatic stops, the nightly 02:00 IST rule and the budget hard-stop Lambda, do not run this: they have no way to ask the app server and are meant to act without anyone. They are covered by the
19:30 UTC timer on a night production was running (the common case) and by the pre-stop volume snapshot. Only a deliberate park (`make down`, the runbook's step 8) gets the extra dump.

## Proven live

Production's park of 2026-11-12 (23:48 IST, the first live run; staging has no park, so it could not rehearse this): the script found the running app server, started the dump unit, read `done.` in its log and printed "Dump taken and uploaded. Safe to park." (4 seconds of work, 396K, `kaval-20261112T181813Z.dump`). The bucket listing then showed that dump, `LATEST` pointed at it, and the same-day restore drill passed on it with the data that could have been lost at 5 min 44 s ([record](../drills/restore-2026-11-12.md)). Before the park the newest dump was 2 days old (10 Nov). The negative case (a failed dump stops the park) is covered by the tests, not by breaking the real unit.

## Cost

$0 recurring. One dump of a small database is a few kilobytes in the bucket, which already keeps 14 days.
