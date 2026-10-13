# Restore drills

One file per drill, written by `make restore-drill` ([ADR-0049](../adr/0049-backups-watched-and-restores-drilled.md)).

- `restore-YYYY-MM-DD.md` is a **passing** drill. The daily backup check reads these file names: if the newest is
  more than 35 days old, it opens an issue.
- `restore-failed-YYYY-MM-DD.md` is a drill that failed. It does not count as a drill having been done.

Each record states the data that would have been lost (the dump's age), the time each step took and the
estimated recovery time, against the targets in [disaster-recovery.md](../architecture/disaster-recovery.md).
Do not rename or copy these files to change a date.
