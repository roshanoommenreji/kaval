# Lab 26 — The start-up health check

**Phase:** 4 · **Time:** ~30 min build, one live read-only test against prod
**Cost:** $0 — SSM commands and the extra systemd unit are free; a restore (not exercised in
this lab) would create one new gp3 volume, a few cents

ADR-0008's stopping table named a third safeguard that didn't exist yet: `pg_isready` plus a
sanity query on start, restoring from a snapshot only on failure. This lab builds it. See
[ADR-0008's 2026-10-06 amendment](../adr/0008-production-database-on-its-own-server.md) for the
full reasoning, including why a failed check asks rather than restoring automatically.

---

## What's built

### Two scripts, two trigger points, neither one restores anything

```
scripts/ops/health-check-database.sh   # pg_isready + SELECT 1 over SSM; exit 0/1 only
scripts/ops/restore-snapshot.sh        # the actual restore; always a separate, deliberate command
```

`health-check-database.sh` never calls `restore-snapshot.sh` itself. On failure it prints the
exact command to run and exits 1 — enough for `resume-database.sh`'s `set -euo pipefail` to stop
`make up` before `terraform apply` runs, without the script making the restore decision for
whoever's at the keyboard.

```bash
CMD_ID=$(aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["docker exec postgres pg_isready -U kaval -d kaval","docker exec postgres psql -U kaval -d kaval -t -c \"SELECT 1\""]' \
  --query "Command.CommandId" --output text)
```

There's no network path from the operator's machine to port 5432 — only the app node's security
group can reach it (ADR-0008) — so the check has to run on the instance itself, over SSM, the
same pattern every other admin action on this server already uses.

### Wired into `resume-database.sh`, and exposed standalone

```bash
echo "Database online."

"$(dirname "$0")/health-check-database.sh"
```

One line, at the point `resume-database.sh` already knew the instance was reachable. Also
callable on its own, `make db-health-check`, for checking without going through the whole
`make up` billing-confirmation flow.

### The boot half: a systemd unit nobody has to remember to run

`make up` only runs when a human runs it. A maintenance reboot, a crash, or AWS retiring the
instance underneath it doesn't go through `resume-database.sh` at all — so `user_data.sh.tftpl`
gained a second unit, ordered after Postgres:

```bash
cat > /etc/systemd/system/kaval-db-healthcheck.service <<'UNIT'
[Unit]
Description=Kaval database start-up health check
After=kaval-postgres.service
Requires=kaval-postgres.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/kaval-db-healthcheck.sh
UNIT
```

The script it runs is the same two checks, run locally instead of over SSM, with the result
going to the journal instead of a terminal:

```bash
if docker exec postgres pg_isready -U kaval -d kaval >/dev/null 2>&1 \
  && docker exec postgres psql -U kaval -d kaval -t -c "SELECT 1" >/dev/null 2>&1; then
  logger -t kaval-db-healthcheck "OK -- pg_isready and SELECT 1 passed"
else
  logger -t kaval-db-healthcheck -p user.warning \
    "FAILED -- ... Restore only after diagnosis: make db-restore-snapshot"
fi
```

No operator is attending an unexpected reboot, so there's nobody to ask — the unattended case
resolves the same "don't restore automatically" decision the other way: it only logs loudly
(`journalctl -t kaval-db-healthcheck`), for whoever looks next.

**This half is written but not live.** `aws_instance.database` carries
`lifecycle { ignore_changes = [ami, user_data] }` on purpose, so editing the boot script can't
silently replace a running database out from under it — the same reason the TLS cert and
`pg_hba.conf` changes in earlier stories only ever took effect on a genuine instance
replacement. `terraform plan` after this change confirmed exactly that:

```
$ terraform plan
...
No changes. Your infrastructure matches the configuration.
```

The unit takes effect the next time the instance is actually recreated, not before.

### `make db-restore-snapshot`

```bash
make db-restore-snapshot                  # newest Role=database-data snapshot
make db-restore-snapshot SNAPSHOT=snap-xyz # a specific one
```

`scripts/ops/restore-snapshot.sh`, matching [the manual procedure the restore runbook already
documented](../runbooks/restore-from-backup.md) (option A, whole-volume damage): stop Postgres
over SSM, detach the current volume (tag `Reason=damaged`, keep it — `prevent_destroy` stays
true until someone verifies the restore and deletes it by hand), create a new volume from the
snapshot in the same AZ, attach and mount it, restart Postgres, re-run the health check to
confirm.

One step the runbook's original manual version didn't need: reconciling Terraform state.

```bash
terraform -chdir="$TF_PROD" state rm module.database.aws_ebs_volume.data
terraform -chdir="$TF_PROD" import module.database.aws_ebs_volume.data "$NEW_VOLUME_ID"
terraform -chdir="$TF_PROD" state rm module.database.aws_volume_attachment.data
terraform -chdir="$TF_PROD" import module.database.aws_volume_attachment.data "/dev/sdf:$NEW_VOLUME_ID:$INSTANCE_ID"
```

Without this, Terraform's state still points at the old, now-detached volume — the next
`terraform plan` would see the live attachment as drift and want to "fix" it by detaching the
new (good) volume and reattaching the damaged one. The import ID format for
`aws_volume_attachment` (`DEVICE_NAME:VOLUME_ID:INSTANCE_ID`) came from the AWS provider's own
docs, not memory — worth checking directly, since getting resource import syntax wrong fails
loudly (the `&&` chain falls through to a warning) rather than silently.

## Verification

The health check was run for real, against the actual running production database — read-only,
so no separate go-ahead was needed beyond this story's own:

```
$ make db-health-check
Database health check passed (pg_isready + SELECT 1).
```

`terraform validate` passed on the `database` module after the `user_data.sh.tftpl` change;
`terraform plan` against prod showed no changes anywhere, confirming the boot-unit edit is
correctly inert until the instance is next replaced, and that nothing else drifted.

`make db-restore-snapshot` was **not** run against prod. It stops Postgres and swaps the data
volume — real enough that it needs its own explicit go-ahead rather than riding along on this
story's, the same reasoning the pause/resume scripts' live stop-test is still waiting on. Syntax-
checked (`bash -n`) and reviewed line by line against the runbook's manual procedure instead.

## What this doesn't do

- Doesn't restore automatically on any failure, by design (ADR-0008's 2026-10-06 amendment) —
  every path stops and asks, or logs and waits.
- Doesn't exercise the actual restore path live. Next natural pause, same deferral as Lab 24's
  pause/resume stop-test.
- Doesn't add alerting beyond the instance's own journal — there's no Slack or SNS wiring for
  the boot-time failure case. Out of scope for this story; the operator already checks
  `kubectl get pods` / server state at the start of most sessions, and a loud journal entry is
  there when they do.

## Related

- [ADR-0008](../adr/0008-production-database-on-its-own-server.md) — the decision and its
  2026-10-06 amendment
- [Restore runbook](../runbooks/restore-from-backup.md) — the manual procedure this automates
- [Lab 24](lab-24-pre-stop-snapshot-and-database-pause.md) — the pre-stop snapshot this restore
  path recovers from
- `ROADMAP.md` Phase 4 — "Start-up health check" line, now ticked
