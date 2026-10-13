# The nightly database dump is missing or looks wrong

The daily backup check ([ADR-0049](../adr/0049-backups-watched-and-restores-drilled.md)) opened a GitHub issue
"Backup check needs a person". It says one of: no dump was taken for a night the app server was running; the
newest dump is far smaller than usual; or the check itself could not run.

## Signals

- A GitHub issue labelled `backup-watch`, and a red `backup-watch` run
- Text such as "The app server was running at 2026-10-13 19:30 UTC, when the nightly dump is due, but no dump was taken"
- Or "the newest dump is only N bytes" / "under half the usual"
- Or "The daily backup check could not run" (then nobody knows; treat as a problem)

## Likely causes

1. **The timer did not fire or failed.** The dump is taken by a systemd timer on the app server at 19:30 UTC,
   using the server's own role (ADR-0038). It has never been seen firing on its own
2. **The database server was not running** when the timer fired (it was stopped, or still starting)
3. **The dump script failed** (a client/server version mismatch, a password that changed, the bucket unreachable)
4. **The check is wrong:** the server's launch history is read from the auto-scaling group; a launch that
   finished after 19:10 UTC, or a stop before 19:40, is deliberately not counted
5. **The check itself broke:** the role or the two repository variables (`AWS_BACKUP_WATCH_ROLE_ARN`,
   `PROD_BACKUP_BUCKET`) are missing or wrong

## Diagnosis

On the app server (`aws ssm start-session --target <instance-id> --profile kaval`), if it is running:

```bash
systemctl list-timers kaval-db-backup.timer          # is it scheduled? when did it last fire?
sudo journalctl -u kaval-db-backup.service --since "2 days ago" | tail -40
```

From the laptop, read-only:

```bash
aws s3 ls s3://$PROD_BACKUP_BUCKET/postgres/ --profile kaval | tail
aws autoscaling describe-scaling-activities --auto-scaling-group-name kaval-prod --max-items 10 --profile kaval
```

If the server is not running, a missing dump is expected and the check should not have fired: compare the launch
and terminate times with 19:30 UTC and report a wrong rule.

## Remediation

- **Take a dump now, by hand:** `make backup` (it checks the dump reads back before uploading). This closes the gap
  but not the cause
- **Timer or script failed:** fix what the journal shows; the next 19:30 run proves it. Do not run production
  longer than needed just to see it
- **A dump that is too small:** run `make restore-drill`; its record shows the row counts. A real, empty database is
  possible (production is parked and collects little)
- **Close the issue** only when a dump exists for the night, or you have written down why none was owed

## Do not

- Do not delete the dump folder or the `LATEST` pointer to make the check pass
- Do not widen the role (list is all it needs) or add `s3:GetObject` to it
- Do not silence the workflow instead of fixing the cause; a silent backup failure is the thing this exists to find
