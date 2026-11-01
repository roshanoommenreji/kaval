# Lab 69 — Production runs the locked-down pods, the two health pages and the notifier lock

**Phase:** 7 follow-up · **Stories:** `KAV-105`, `KAV-106` (closed here), `KAV-100` (lock seen working; stays open) · **Time:** about 15 minutes of production, plus the staging run that came before · **Cost:** about 5 cents for production, about $0.50 for the staging run

[Lab 67](lab-67-two-health-pages-and-pods-that-pass-restricted.md) built the locked-down pods and the two health pages and passed them on staging. [Lab 68](lab-68-only-one-gateway-copy-notifies-at-a-time.md) built the notifier lock. Both were waiting for production, because production is parked to save money. This lab is the bring-up that carried them there, following [the production runbook](../runbooks/prod-rollout.md). Each live step below had the user's own go, with the exact command shown first.

## Prerequisites

- The build passed staging and was promoted: `make staging-up`, `make staging-smoke` (sha-1d38be1, 7 of 7, PR #285), `make staging-down`, `promote.yml` (PR #286, merged)
- The laptop connected to Terraform's record (`make tf-init ENV=prod`) and the `kaval` AWS profile

## Part 1 — Read-only checks first

```bash
python scripts/release/promote.py preflight          # all four images tagged sha-1d38be1 exist in the registry
terraform -chdir=infra/envs/prod plan                # while parked
```

The parked plan showed four in-place changes and nothing created or destroyed: a newer base image in the app server's launch template (and the group's version of it), and new code for the two waste functions. No database migration had been added since production last ran (the newest is from 19 October, and production started on 21 October), so no pre-migration snapshot was needed.

## Part 2 — Start the database, show the plan, apply

```bash
bash scripts/ops/resume-database.sh                                                  # healthy in about a minute
terraform -chdir=infra/envs/prod plan -var app_node_desired_capacity=1 -out=<file>   # the same four changes, group 0 -> 1
terraform -chdir=infra/envs/prod apply <file>
```

## Part 3 — Check it

`make prod-smoke` was run every 90 seconds. The first two tries failed because the services were still starting; the third, **six minutes after the apply**, passed all nine checks:

| Check | Result |
|---|---|
| Pods running and ready | 10 running (two gateway copies) |
| No crash loop | 0 restarts |
| Every service runs the promoted tag | ok |
| Running digests equal the registry's | all four match |
| Gateway reaches the database, migrated | to the current head |
| API | `/v1/incidents` and `/v1/signals` both HTTP 200 |
| These exact images passed staging | sha-1d38be1, recorded 2026-10-31 |
| The server's timers | backup at 19:30 UTC, all three active |

The new base image booted without trouble. The model server showed "not ready" for the first minutes while it loaded; the smoke test notes this and does not gate on it.

## Part 4 — The notifier lock, in the logs

`kubectl logs` on each gateway pod (read-only; nothing was run inside a service): both copies connected to Slack at the same moment, and one of them logged `another gateway copy is notifying, skipping this round` while the other did not. No `permission denied` appeared.

What this does **not** show: no approval request was open, so there was nothing to send twice. The lock is seen taking turns; the original double alert was not re-created. `KAV-100` stays open until a real open approval has been through a production session (`make approvals-report` is staging-only for now).

## Part 5 — Park it

```bash
terraform -chdir=infra/envs/prod plan -out=<file>     # one change: group 1 -> 0
terraform -chdir=infra/envs/prod apply <file>
bash scripts/ops/pause-database.sh                    # snapshots the data volume, then stops the database
```

Confirmed: the group at 0, the database stopped, the "still alive" alarm `OK`, nothing else running.

## Done when

- `make prod-smoke` passes on the promoted build (it did)
- Production is parked again with a snapshot taken (it is)

## Cost

Both production servers for about 13 minutes, plus a snapshot of the data volume: well under 10 cents. Month-to-date spend stays far under the $50 ceiling.
