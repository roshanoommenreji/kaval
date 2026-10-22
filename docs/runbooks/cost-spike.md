# The AWS bill jumped (a cost spike)

An incident named `cost_spike:aws:…` means one AWS service cost much more on one day than it usually does. Kaval
works it out from the daily bill the cost reader pulls ([ADR-0060](../adr/0060-the-cost-reader-the-server-asks-aws-and-the-cluster-never-holds-the-login.md)):
a service's day is a spike when it is **more than three times the median of the seven days before it** and **at least
25 cents above the usual day**, with at least three earlier days to compare with. This is **for a person**: Kaval
proposes no fix and the policy refuses to attach one, because the cause is almost always something that was
started and not stopped, and only a person knows whether it is still wanted. Spend is the point: the month's ceiling
is $50, with alerts at $42 and $46 and the brake at $48 ([budget-plan.md](../cost/budget-plan.md)).

## Signals

- An incident `cost_spike:aws:…`, severity medium, with the service name (for example
  `Amazon Elastic Compute Cloud - Compute`) and the day's amount
- Slack: the usual incident message; the budget's own emails at $42 / $46 / $48 are separate and come from AWS
- The bill is about **a day late**, so a spike is seen the next day at the earliest. A server left on over a weekend
  is found on Monday, not on Saturday. That delay is why the daily waste scan and the 12-hour server-overstay finding
  exist: they look at what is running now ([waste-found.md](waste-found.md))

## Likely causes, most common first

| Service in the spike | Usually | First look |
|---|---|---|
| **EC2 - Compute** | Staging or production was brought up and not parked; staging's 4-hour idle stop did not run; the dev server was left on | the running servers (below) |
| **EC2 - Other** (disks, snapshots, addresses, data transfer) | A loose disk or old backup copies; a restore drill not cleaned up; an address attached to nothing | `make tag-scan`; [waste-found.md](waste-found.md); `make savings-report` for what was already approved |
| **Amazon EKS** | The `lab-eks` environment was brought up and not taken down (a control plane bills about $0.10 an hour) | `aws eks list-clusters`; `make lab-down` |
| **NAT Gateway / Load Balancing** | Something that must never exist here was created by hand ([CLAUDE.md](../../CLAUDE.md) lists them) | `aws ec2 describe-nat-gateways`; `aws elbv2 describe-load-balancers` |
| **CloudWatch** | A log group that grew, or a new alarm count | the largest log groups |
| **Cost Explorer** (the bill's own line) | The cost reader pulling more than its 60 calls a month, or someone running reports by hand (each call costs 1 cent) | [cost-reader-not-pulling.md](cost-reader-not-pulling.md) |
| **Tax / support / refunds** | Not a resource; the month's invoice lines | ignore, and note it |

## Diagnosis

Read-only, in this order. Stop at the first thing that explains the day.

```bash
# 1. What is billing right now
aws ec2 describe-instances --filters Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].[InstanceId,InstanceType,LaunchTime,Tags[?Key==`Name`]|[0].Value]' --output text
aws autoscaling describe-auto-scaling-groups --query 'AutoScalingGroups[].[AutoScalingGroupName,DesiredCapacity]' --output text
aws rds describe-db-instances --query 'DBInstances[].DBInstanceIdentifier' --output text    # expect none (Kaval has no RDS)
aws eks list-clusters --output text                                                          # expect none

# 2. The month so far, and who it belongs to
make cost-report                       # the month's spend so far
make cost-showback MONTH=2026-10       # by AWS service and by environment (docs/cost/showback-<month>.md, costs 1 cent)

# 3. Anything unlabelled or forgotten
make tag-scan                          # Kaval resources missing their labels
make savings-report                    # what Kaval already proposed to delete, and what was confirmed
```

The cost showback splits the bill by `Env`, so a spike that sits under `staging` and not `prod` points straight at
the practice copy. "No Env label" is mostly lines that never carry a label (support, tax, data transfer); a *large*
unlabelled share on EC2 means something was started without its labels (see [untagged-resources.md](untagged-resources.md)).

For the day itself: AWS console, Billing and Cost Management, Cost Explorer, daily view, group by service, then by
"usage type" for the spiking service. This is where "four `t4g.medium` hours more than usual" becomes visible.

## Remediation

Nothing here is automatic. Each step is yours to take. Reversible means it can be undone; the blast radius is how
much it touches.

- **A server or group left up** (reversible: it starts again; blast radius: that environment): park it deliberately: `make staging-down` (staging, destroys it), or for production the
  parking steps in [prod-rollout.md](prod-rollout.md) (scale the group to 0, then `scripts/ops/pause-database.sh`).
  Never terminate production's database disk
- **A loose disk or old backup copy** (the delete is NOT reversible; blast radius: that one resource): let Kaval propose it ([waste-removal.md](waste-removal.md): a backup, a wait, then
  the delete, each with your approval) rather than deleting by hand
- **A forgotten lab or hand-made resource** (a lab is rebuilt by `make lab-up`; a hand-made resource may not be): `make lab-down`, or delete it after checking who made it
  (`aws cloudtrail lookup-events --lookup-attributes AttributeKey=ResourceName,AttributeValue=<id>`)
- **The month is heading for the ceiling anyway:** the brake at $48 will stop the compute by itself
  ([budget-hard-stop-fired.md](budget-hard-stop-fired.md)). Do not wait for it when a spike already shows the cause

## Do not

- Do not delete or terminate to "see if the spike stops". A spike is a day that already happened; deleting a database
  server or disk because of it can turn a $2 problem into data loss
- Do not turn the brake's switches off to stop it interfering
- Do not treat a spike from the first days of a bring-up as an error: a server that has just been started costs more
  than the parked days before it, and the rule compares with the week before. If it matches a bring-up you did, note
  it in the journal and move on
- Do not count credits as proof the spike does not matter. Credits hide the invoice, not the budget's view of usage

## Escalation and ownership

- Owner: Roshan. The budget's own emails (AWS Budgets, $42 / $46 / $48) are the escalation; the brake is the backstop
- If the cause is unknown after the diagnosis above, leave everything as it is, write down the service and the day
  in the journal, and look again after the next day's bill arrives: the cost reader brings in one more settled day each
  morning

## When it is fixed

The next day's bill is back near the usual day and no new `cost_spike` incident opens. The incident closes by itself
48 hours after the last spiking row. Record the cause and what stopped it in the journal; if it was a gap in what Kaval
watches, add a line to [ROADMAP.md](../../ROADMAP.md).
