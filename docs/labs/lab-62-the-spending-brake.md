# Lab 62 — The spending brake: naming every server group and watching it fire

**Phase:** 6 · **Story:** `KAV-96` · **Time:** about 3 hours · **Cost:** about $0.05 (an hour of staging) and cents of
snapshots; everything else $0

At **$48** of the month's $50 ceiling a small function stops the compute. It was built in Phase 0 and armed on production
in `KAV-50`, but it was never tested as code, it knew only production's server group, and nobody had seen the real
alert path (AWS Budgets, then the alert topic, then the function) set it off. This lab fixes the first two and proves the
third. The decision is [ADR-0064](../adr/0064-the-spending-brake-names-every-server-group-and-is-proven-on-staging.md).

## Prerequisites

- `AWS_PROFILE=kaval`, Terraform connected to production (`scripts/ops/tf-init.sh prod`)
- For Part 3: staging up (`make staging-up`, run by the owner, about 5 minutes, about $0.045 an hour)

## Part 1 — The rules, on their own (no AWS)

```bash
pytest infra/modules/budget/lambda scripts/dev/test_budget_wiring.py -q     # 18 tests
```

Read these first:

- `test_every_named_group_is_scaled_to_zero` and `test_dry_run_changes_nothing`
- `test_a_group_that_does_not_exist_is_not_an_error`: staging is destroyed between uses, and the nightly run must not
  fail every night because its group is absent
- `test_servers_inside_a_group_are_skipped_because_the_group_replaces_them`: why a staging app server was never stopped
  before this change (the gap, ADR-0064)
- `test_one_group_failing_does_not_stop_the_others_or_the_servers` and
  `test_a_failure_in_the_server_step_still_lets_the_groups_stop`: each step is tried; the function raises at the end so
  the alert topic retries
- `test_a_failed_copy_never_blocks_the_stop`: stopping the bill is its one job
- `test_the_modules_defaults_equal_productions_values` and the two that read `CLAUDE.md` and the budget plan: change
  the ceiling in one place and these fail

## Part 2 — Apply to production (plan shown first, on the owner's go)

The function's code and one setting change: `ASG_NAMES = kaval-prod,kaval-staging`. Production is parked, so the apply
cannot stop anything real.

```bash
terraform -chdir=infra/envs/prod plan -out=<file>   # expect: the function's code and its settings change, nothing else
terraform -chdir=infra/envs/prod apply <file>       # only when told
aws lambda get-function-configuration --function-name kaval-budget-hard-stop --query Environment.Variables
```

2026-10-21, on the owner's go: the plan showed two small changes (the function's code and its group list, and the nightly
permission rewritten to the same value; the second turned out to be nothing: 1 changed). Afterwards the live function
read `ASG_NAMES=kaval-prod,kaval-staging`, `DRY_RUN=false`, `STOP_INSTANCES=true`; a plan said "No changes"; both groups at 0
and no Kaval server running.

## Part 3 — The live proof on staging

Staging up (`make staging-up`: it asks "Continue?"; a run that has already been agreed sets `ASSUME_YES=1`, a switch of the
script's own). Do not set `MSYS_NO_PATHCONV=1` for it on Windows: Terraform then cannot find the staging folder.

Before: `kaval-staging` group at 1, staging database running, production's group at 0 and its database stopped.

```bash
aws sns publish --topic-arn <the kaval-budget-alarm topic> --subject "KAV-96 proof: simulated budget hard stop"   --message "Test of the spending brake path on staging. Not a real budget breach."
aws logs tail /aws/lambda/kaval-budget-hard-stop --since 6m
```

Seen (15:45 to 15:46 UTC): the function started in 0.3 s; `kaval-prod` "already at zero" (left alone); `kaval-staging`
"scaled 1 -> 0"; staging's database stopped; production's database and the dev server untouched; 6 seconds in all.

**The proof found a real defect.** The snapshot before the stop was refused: "not authorized to perform
ec2:CreateSnapshot on resource snapshot/*". `CreateSnapshot` is checked against the disk **and** the snapshot it makes, and the
brake's policy allowed only the disk. The stop went ahead (a failed copy never blocks it, by design) but without its extra
copy, and the same is true of the nightly 02:00 IST run, which is the same function. The staging data disk still had 19
daily copies from the backup schedule, so nothing was at risk. The two other stopping functions already had the grant; only
this module lacked it. Fixed in the change that follows this lab (a statement allowing the snapshot, only when the request
labels it as the project's, and a test that fails for any module that grants the disk without the snapshot).

### Part 3b — The fix, applied and re-tested (2026-10-22)

The permission was applied to production on its own (`-target=module.budget.aws_iam_role_policy.hard_stop`, one resource),
because the full plan also wanted a newer server image published the night before that no staging boot had tried yet; that
change waits for the next bring-up. Then only staging's database was started
(`DB_NAME_PREFIX=kaval-staging bash scripts/ops/resume-database.sh`) and the same message was sent again.

Seen (05:35 UTC): both groups "already at zero"; `Snapshotted vol-... as snap-07c7f4fa81aabecb7 before stopping i-...`;
`HARD STOP APPLIED. Stopped i-...`; 5.5 seconds in all. The snapshot completed with `Project=kaval`, `Env=staging`,
`ManagedBy=kaval-lambda`, `Reason=pre-stop` and `Role=database-data`. Production's and the dev server's state did not change.

## Part 4 — The real lowered-limit test (separate go)

*To be filled in if it is done.* (The owner has not yet decided; the snapshot fix and a re-test come first.) The real budget's stop line is lowered briefly so AWS Budgets itself sets the brake off,
then restored to $48 straight after (a plan shows it back to "No changes").

## What this does not do

It does not stop a restart after the brake fired (the brake fires once per month at the crossing; the nightly 02:00 IST
run is the only repeat while the system is parked between sessions). It does not delete anything. A repeating spend
check for an always-on system is a recorded follow-up, not built.
