# ADR-0078 — Backup copies are found by their labels, never by their names

- **Status:** Accepted (plan shown to Roshan 2026-11-06, who said "go"). Built and tested 2026-11-06 (`KAV-127`, `KAV-128`). Merged as #322 (2026-11-06). **Not yet proved across a night:** the best proof is a staging left up through a night, so the nightly stop makes its real copy and the schedule's own 03:00 UTC run happens (about 15 cents; [Lab 78](../labs/lab-78-backup-copies-that-belong-to-the-right-environment.md) Part 4a), and it needs Roshan's go for the build and again for the morning teardown. Production's schedule was changed on 2026-11-07, at its bring-up (decision 6)
- **Date:** 2026-11-06
- **Deciders:** Roshan
- **Story:** `KAV-127` (the staging teardown leaves backup copies behind) and `KAV-128` (a snapshot schedule covers the other environment's disk); evidence in [Lab 78](../labs/lab-78-backup-copies-that-belong-to-the-right-environment.md); amends the 2026-11-05 amendment of [ADR-0008](0008-production-database-on-its-own-server.md)

## Context

Four jobs make backup copies (EBS snapshots) of a database disk, and each names its copy differently:

| Maker | Name it gives | `Env` label it gives |
|---|---|---|
| The daily schedule (Data Lifecycle Manager, `infra/modules/database`) | the disk's own `Name`, copied (`kaval-staging-database-data`) | the disk's, copied |
| The idle stop (Lambda, staging only) | `kaval-staging-database-pre-stop` | `staging` |
| The nightly 02:00 stop and the budget brake (`infra/modules/budget/lambda/hard_stop.py`) | **`kaval-hard-stop-pre-stop`, for every environment** | the disk's, or `unknown` if the disk has none |
| `scripts/ops/pause-database.sh` | `<prefix>-database-pre-stop` | from the prefix |

`make staging-down` looked for copies **by name**: `kaval-staging-database*`. The nightly stop's copy of staging's disk is named `kaval-hard-stop-pre-stop`, so it was never found. Three 10 GB copies were left behind (22 Oct, 27 Oct, 4 Nov), found by a sweep on 2026-11-04 and deleted by hand on 2026-11-05 with Roshan's go. At most about $1.50 a month; the point is that a teardown which says "nothing left" must be true.

The second finding came from the same look at the list. AWS's Data Lifecycle Manager covers a disk that has **any one** of a schedule's target tags, not all of them. The module targeted `Role = database-data` and the disk's `Name`; every environment's disk carries that same `Role`, so staging's schedule also copied production's disk (2026-11-04, 03:15 UTC). The 2026-11-05 amendment of ADR-0008 records it.

## Decision

1. **`staging-down` finds copies by what they are: `Project = kaval` and `Env = staging`.** Every maker above sets both labels, either copied from the disk or set explicitly, and a copy labelled `prod` or `unknown` cannot match. The old name check is dropped, not kept beside it: the name was the cause, and one rule is easier to state and to test. Measured on 2026-11-06 (read-only): the account holds 20 snapshots, all labelled `Env=prod`; none lacks the label, so nothing is lost by dropping the name rule. The project's tag policy ([ADR-0061](0061-every-resource-carries-three-tags-and-the-bill-is-split-by-them.md), `make tag-scan`) is what keeps that true.
2. **A circuit breaker.** The teardown refuses to delete more than 30 copies in one go and says so. Staging holds a handful; more would mean the search found something it should not, and a refused teardown is cheap while a deleted backup is not.
3. **A failed listing stops the teardown.** An expired login used to read as "no copies" because the listing sat inside a loop's word list, where a failure is ignored. It is now its own step: on failure the script says `nothing was deleted` and exits non-zero.
4. **The closing line counts copies.** `Left tagged Env=staging: 0 instances, 0 volumes, 0 VPCs, 0 snapshots`: anything left shows up where the person running it is already looking.
5. **Each schedule targets the `Name` tag alone** (`KAV-128`). Only one environment's disk carries `<name_prefix>-database-data`. A test reads the real Terraform of every environment that builds the module, works out the tags its disk carries (the module's own plus the environment's default tags) and applies AWS's rule both ways: the schedule must cover its own disk (else there would be no backups at all, a worse failure than the bug) and no other environment's.
6. **Production's schedule changes at its next bring-up, with the changes already waiting.** ADR-0008's amendment said production's plan would show one in-place change. It does not: on 2026-11-06 production's plan already held five changes from earlier stories (an approver-token parameter and its random password to create, a permission policy, and the node's launch template and group to update, `Plan: 2 to add, 3 to change`). With this change it reads `Plan: 2 to add, 4 to change, 0 to destroy`, the extra one being the schedule's target tags losing `Role`. Applying that one alone would need `-target`, which hides the other five; applying all six now would be a production apply for a few cents of exposure. So it rides with the bring-up, where the full plan is read and approved anyway. The exposure until then is small and one-sided: staging's schedule copies production's disk only if staging exists at 03:00 UTC (and staging gets the fixed schedule the next time it is built), and production's schedule copies staging's disk, which the new teardown deletes. Roshan can overrule and ask for an earlier apply. **Done 2026-11-07:** he asked for production's bring-up that day, so all six changes were applied together from the plan he had seen (`2 to add, 4 to change, 0 to destroy`); afterwards `get-lifecycle-policy` printed only `Name   kaval-prod-database-data` and production's plan read `No changes`. The night run that shows it working is Lab 78 Part 4a.

## Alternatives considered

- **Keep the name check as well as the labels.** Catches an unlabelled copy that does not exist today, and keeps a rule that was the cause of the miss. Rejected for simplicity.
- **Give the nightly stop's copy a per-environment name** (`hard_stop.py` is in the budget module, which production runs). It would fix the symptom at its source, but it is a Lambda change and a production apply, and the label already says which environment a copy belongs to. A possible later tidy-up, not needed.
- **Select by the disk's volume id.** Exact, but the volume is deleted before the sweep, and a re-run after a half-finished teardown has no id to ask for. Labels survive both.
- **Apply the schedule change to production alone now, with `-target`.** Rejected: it would skip five other waiting changes silently, and this project applies full plans it has read.
- **Add a target tag for `Env`.** The database module does not know the environment's `Env` (it comes from the provider's default tags); it does own `name_prefix`, which `Name` is built from.

## Testing

- `scripts/dev/test_staging_snapshots.py` (8 tests) runs the real `staging.sh down` against a pretend AWS that holds both environments' copies and applies filters the way AWS does. Staging's four kinds of copy must go; production's three, a copy of unknown environment and someone else's must stay; a second run deletes nothing and succeeds; 31 staging copies are refused; a failing listing stops it; the closing line counts. The stand-ins come first on the PATH and the AWS settings point at nothing, so a mistake in the test fails rather than deletes.
- `scripts/dev/test_snapshot_schedule_targets.py` (6 tests) as in decision 5, including a control that the same rule catches the old `Role` plus `Name` pair.
- **Seen failing first:** the teardown deleted three of staging's four copies and left the nightly stop's (`snap-0a1`), with no count on its closing line; both environments' schedules were reported as covering the other's disk. **Seen failing the other way:** with the `Env` filter deliberately removed, four tests failed (production's copies would have been deleted); the file was put back and checked identical.
- A read-only run of the exact search against the real account on 2026-11-06: 0 staging-labelled copies, 20 production-labelled (the control), 0 without an `Env` label.

## Noticed, not changed

- The stray copy of **production's disk made by staging's schedule on 2026-11-04** is still in the account (it carries production's labels, copied, so it looks like one of production's own). Nothing will expire it, because the schedule that made it is gone. It can be told apart: every copy a schedule makes carries an `aws:dlm:lifecycle-policy-id` tag, and on 2026-11-06 this was the only one of production's schedule-made copies whose policy id was not production's own. A few cents a month; keeping it costs nothing to decide, and deleting it needs Roshan's go. **Decided 2026-11-07: keep it.** Roshan agreed to the advice to keep it; nothing is deleted, the policy-id tag still tells it apart, and a later deletion would again need his go.
- `restore-snapshot.sh` and the other scripts that look for a copy by name are unchanged: they restore, they do not delete, and they need the environment's own name on purpose.
