# The spending brake fired

At **$48** of this month's usage (the ceiling is $50) Kaval's spending brake stops the compute: it scales production's
and staging's Auto Scaling Groups to zero and stops every other running `Project=kaval` server (the databases), taking
a snapshot of each data disk first. It never deletes anything. See [ADR-0064](../adr/0064-the-spending-brake-names-every-server-group-and-is-proven-on-staging.md)
and [the budget plan](../cost/budget-plan.md). This is **for a person**: the brake is the last line of defence, so by
the time it ran something already went wrong, and Kaval itself is switched off with the servers.

The same function also runs every night at 02:00 IST as a brake for a forgotten `make down`. Seeing it stop a server
at that hour is not a spending event; look at the time and at the bill first.

## Signals

- An email from AWS Budgets: "actual spend greater than $48" (it goes to the project's alert address)
- Production or staging unreachable; `kaval-prod-still-alive` stays quiet because a group at zero cannot ring it
- The function's log has `HARD STOP APPLIED` lines: `/aws/lambda/kaval-budget-hard-stop`

## Find out what happened

```bash
export AWS_PROFILE=kaval AWS_DEFAULT_REGION=ap-south-1
aws logs tail /aws/lambda/kaval-budget-hard-stop --since 2d          # which groups and servers it stopped, and why
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names kaval-prod kaval-staging \
  --query "AutoScalingGroups[].[AutoScalingGroupName,DesiredCapacity,MinSize]" --output text
aws ec2 describe-instances --filters "Name=tag:Project,Values=kaval" \
  --query "Reservations[].Instances[].[Tags[?Key=='Name']|[0].Value,State.Name]" --output text
make cost-report                                                      # month to date, by service
make cost-showback                                                    # by environment (one cent)
```

Then find **what spent the money** before turning anything back on. A server left up is the usual cause (an app server
is about $0.05 an hour with its database); the second usual cause is a service outside Kaval in the shared account
(the budget counts the whole account, and another project, `stock-trader`, shares it).

## Likely causes

1. A server left running for days (check launch times and `make cost-showback`)
2. Another project in the same account spending (the showback splits by the `Env` label; unlabelled is mostly the other project)
3. A one-off charge (a large snapshot set, data transfer): `make cost-report` shows it by service
4. The line is simply too low for the posture (always-on is about $39 a month, ADR-0059): that is a budget decision,
   not a fault

## Remediation

- **Do not just switch it back on.** The brake fires once per month when the line is crossed. A restart afterwards is
  not stopped again until the next night's 02:00 IST run, so restarting without understanding the cause can overspend.
- To restart: `bash scripts/ops/resume-database.sh` and the normal bring-up (`docs/runbooks/prod-rollout.md`), only after
  the cause is understood and, if the month's bill is close to $50, only for the short session that is needed.
  Blast radius: the bill.
- To change the line itself: that is a decision, written as an ADR. Edit `infra/envs/prod/variables.tf` (and your local `terraform.tfvars`, which is not in git),
  `docs/cost/budget-plan.md` and `CLAUDE.md` together (`scripts/dev/test_budget_wiring.py` fails if one disagrees), with
  the plan shown first.
- The snapshots the brake took are tagged `Reason=pre-stop`; they age out like the others.

## After

Record the cause in the journal, and in `docs/cost/budget-plan.md` if the plan was wrong.
