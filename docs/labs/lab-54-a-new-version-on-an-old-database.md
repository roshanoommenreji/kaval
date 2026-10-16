# Lab 54 — A new version starts by itself on an old database

**Phase:** 5 · **Story:** `KAV-85` · **Time:** about 2 hours · **Cost:** about 1.5 hours of staging (about $0.045 an hour, so about 7 cents); nothing recurring.

On 2026-10-14 production came up on a database two migrations behind and needed two fixes by hand
([Lab 50](lab-50-closing-phase-5.md)). This lab reproduces that on staging, changes the order so it does not
happen, and proves the change with the same drill. The decision is
[ADR-0057](../adr/0057-the-database-is-brought-up-to-date-before-a-new-version-starts.md).

## Prerequisites

- Staging up (`make staging-up`), a build that passed its smoke check
- `AWS_PROFILE=kaval`; the laptop's Session Manager plugin (the commands below run on staging's server through it)
- Never do this against production: the rollback below is a database downgrade

## Step 1 — The rules, on their own (no AWS)

```bash
pytest scripts/dev/test_migration_rules.py -q        # 20 tests; the two chart tests need helm
```

Read these first:

- `test_with_a_database_server_of_its_own_the_migration_runs_first`: staging's and production's rendered chart has
  the migrate Job as `pre-install,pre-upgrade`. Run it against the previous chart and it fails (checked).
- `test_with_the_charts_own_postgres_the_migration_still_waits_for_it`: local k3d keeps `post-install`.
- `test_every_migration_only_adds` and the two checkers' own tests: the negative cases (a dropped or renamed column is
  flagged; a `downgrade()` that drops is not).
- `test_every_table_a_migration_creates_is_granted_to_a_service`: a table with no `GRANT` fails the build.
- `test_permissions_are_handed_out_again_after_the_release_is_ready_on_every_boot` and
  `test_the_second_pass_is_started_last_and_does_not_hold_up_the_boot`: the bootstrap script.

## Step 2 — Make staging's database old

Staging stays up. Stop Flux from rebuilding the release, remove the release, and roll the database back two
migrations with the same image and secret the chart uses (run on staging's server over Session Manager; all `kubectl`):

```bash
kubectl patch kustomization kaval-staging -n flux-system --type merge -p '{"spec":{"suspend":true}}'
kubectl delete helmrelease kaval-staging -n flux-system --wait=true
# a Job from the gateway image: command ["sh","-c","alembic downgrade a1c4f9b0e3d2 && alembic current"],
# envFrom the secret kaval-postgres-admin. It prints a1c4f9b0e3d2.
```

## Step 3 — Reproduce the failure (the build before this story)

Let Flux install again (`suspend: false`) and watch. Seen on 2026-10-16:

| Time after resuming | What the release said |
|---|---|
| 1m40s | `Running 'install' action with timeout of 5m0s`; the executor `0/1`, restarting (3 times) |
| 5m | `InstallFailed: timeout waiting for: [Deployment/kaval-staging/kaval-staging-executor status: 'InProgress']`, then `UninstallSucceeded` and a retry |
| always | no migrate Job was ever created |

This is production's failure of 2026-10-14, reproduced. Put staging back by hand (runbook steps 5 and 6): one Job that
runs `alembic upgrade head`, after which Flux's next try succeeded (about 90 seconds).

## Step 4 — The change

Merged as PR #184: the chart's migrate hook is `pre-install` when `postgres.enabled` is false; the node's
bootstrap runs its permissions step a second time once the release is Ready, as its own background unit
(`kaval-db-grants`), on every boot. The node's script lives in the launch template, so staging needed an apply:

```bash
terraform -chdir=infra/envs/staging plan -out=<file>     # show it: 2 changes in place, 0 destroyed
terraform -chdir=infra/envs/staging apply <file>         # the launch template and the group's version
```

A running server is untouched by that apply; only a replacement uses the new script.

## Step 5 — The same drill, with the change

The chart is read from `main` and the script from the launch template, so this needs Step 4 first. Roll the database
back two migrations again (Step 2's Job), then **replace the server** so it starts from nothing on an old database,
which is exactly what a production bring-up is:

```bash
aws ec2 terminate-instances --instance-ids <staging's server>      # the group launches another
```

Seen on 2026-10-16 (clock times UTC, the old server ended at 05:46:51):

| Time | What happened |
|---|---|
| 05:47:34 | the new server is in Session Manager |
| 05:48:53 | Flux starts the install |
| 05:51:18 | the release is Ready: `Helm install succeeded` (no migrate Job left: it ran first and was removed on success) |
| 05:51:18 | the second permissions pass (`kaval-db-grants.service`) runs `GRANT` and ends; it started only after Ready |
| 05:52:39 | the first heartbeat run: the boot was not held up by the wait |
| 05:52:53 | `make staging-smoke`: 7 of 7 |

Checks, read-only, on the server: the database is at `c3e8a1d7f402` (head); `has_table_privilege('kaval_gateway',
'notification','INSERT')` and `has_column_privilege('kaval_gateway','action','slack_ts','UPDATE')` are both true; the
gateway logged no `permission denied` in ten minutes; 8 pods running, 0 restarts. Nobody touched anything between the
termination and the smoke check.

## Step 6 — Production

Nothing is done to production here. It is parked, and its chart comes from Git on every boot, so the new order applies
the first time it is brought up. The one visible difference: the Terraform plan shown at that bring-up contains a
launch-template change beside the group's desired count (see [prod-rollout.md](../runbooks/prod-rollout.md), step 2).

## Done when

- [x] The failure reproduced on staging with the previous setup (install timed out, migrate Job never created)
- [x] Chart: migration before the install with a database server of its own; local keeps its order
- [x] Node: permissions handed out again once the release is Ready, on every boot, without delaying the boot
- [x] CI: a migration only adds; every created table is granted; both have negative tests
- [x] The same drill on staging with the change: Ready by itself, database at head, permissions right, smoke 7 of 7
- [x] Runbook steps 5 and 6 reduced to "only if the automatic order did not work"
- [ ] Not seen live: production itself (parked). First proof is its next bring-up
