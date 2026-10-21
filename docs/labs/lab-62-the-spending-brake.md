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

*To be filled in as it is done.*

## Part 3 — The live proof on staging

*To be filled in as it is done.* Staging up, the real alert path used (never a hand-edited copy of the function), then:
staging's group goes to zero, staging's database is snapshotted and stopped, production (parked) is untouched.

## Part 4 — The real lowered-limit test (separate go)

*To be filled in if it is done.* The real budget's stop line is lowered briefly so AWS Budgets itself sets the brake off,
then restored to $48 straight after (a plan shows it back to "No changes").

## What this does not do

It does not stop a restart after the brake fired (the brake fires once per month at the crossing; the nightly 02:00 IST
run is the only repeat while the system is parked between sessions). It does not delete anything. A repeating spend
check for an always-on system is a recorded follow-up, not built.
