# A restore drill is due or has failed

The backup check ([ADR-0049](../adr/0049-backups-watched-and-restores-drilled.md)) says no restore drill was
recorded in the last 35 days, or you ran `make restore-drill` and it failed.

## Signals

- GitHub issue "Backup check needs a person": "The last restore drill was N days ago" or "No restore drill has ever been recorded"
- `make restore-drill` ending `FAIL: ...`, and a file `docs/drills/restore-failed-<date>.md`

## Likely causes

1. **It is simply due.** Once a month, by hand; nothing broke
2. **Docker is not running** on the laptop, or AWS access has expired (`aws sso login --profile kaval`)
3. **The dump did not restore cleanly.** The strict restore stops at the first error; the message names it
4. **A table is missing** after the restore: the dump is from a broken or very different schema
5. **The schema version is unknown** to this repository: the dump came from a build this repo never had
6. **The estimated recovery time is over 30 minutes:** the data has grown; the target or the method needs a decision

## Diagnosis

```bash
make restore-drill                      # about a minute, read-only on AWS, needs Docker
cat docs/drills/restore-failed-*.md     # what was wrong, in words, with the row counts
```

An **empty** production is not a failure: the record passes with a note that it proves the mechanics only.

## Remediation

- **Due:** run `make restore-drill`, then commit the new `docs/drills/restore-<date>.md` (`docs: KAV-82 restore drill <date>`)
- **Failed on the dump:** take a fresh dump (`make backup`) and drill again; if it fails again the dump process is
  broken, see [backup-missing](backup-missing.md). Keep the `restore-failed-*` record as evidence
- **Over the recovery target:** that is a finding: write it in the record, then decide (faster method, or a
  new target in `docs/architecture/disaster-recovery.md` with the reason)

## Do not

- Do not copy an old record to a new date to satisfy the check; the check reads the file name, and a drill that did
  not happen is worse than an honest overdue alert
- Do not restore a production dump into staging to "test it better"; staging only ever receives the cleaned copy
  ([ADR-0039](../adr/0039-staging-is-filled-from-a-copy-cleaned-on-the-laptop.md))
