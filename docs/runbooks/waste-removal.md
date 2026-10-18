# Kaval asks to remove waste (and what to do when a removal fails or stalls)

Kaval can remove two kinds of waste the daily scan finds: **a disk attached to nothing** (incident `idle_volume`) and
**a backup copy past its retention** (incident `snapshot_past_retention`). It never does it by itself. A person
approves every risky step ([ADR-0063](../adr/0063-waste-is-removed-in-steps-by-a-separate-worker-and-only-with-approval.md)),
and the proposal is made by plain code, never by the model. This page covers three situations: you are asked to
approve a step, a removal failed or was refused (`removal_failed`), and an approved order was never answered
(`KavalRemovalOrderUnanswered`).

## You are asked to approve a step

The approval message names the step, the AWS id, what will happen, and how to undo it.

| Step | What it does | Safe to approve when |
|---|---|---|
| `backup_and_mark_volume` | Takes a backup copy of the disk and labels the disk `KavalRemoval=<time>`. **Nothing is deleted.** | You are fine with a one-off copy costing about $0.05 per GB a month for 30 days. It is almost always safe |
| `delete_idle_volume` | Deletes the disk. The backup copy is kept for 30 days | The disk has been marked for the whole grace period (7 days in production), its backup is finished, and nobody has said they need it |
| `delete_old_snapshot` | Deletes a backup copy past its retention | It is not the only copy of anything you still want. The worker already refuses the newest copy of a disk that exists |

Before approving a delete, look: `aws ec2 describe-volumes --volume-ids <id>` (State `available`, no attachments,
the `KavalRemoval` label) and `aws ec2 describe-snapshots --snapshot-ids <backup id>` (State `completed`). Approve
within the hour: an approval is permission for now, and an old one is refused.

**Deny** if you recognise the disk and want it. A "no" is not asked again for a week.

## To undo a deleted disk

The backup copy is the way back, for 30 days:

```bash
aws ec2 create-volume --snapshot-id <backup id> --availability-zone <zone> \
  --tag-specifications 'ResourceType=volume,Tags=[{Key=Project,Value=kaval},{Key=Env,Value=<env>},{Key=ManagedBy,Value=manual}]'
```

Then attach it where it is needed. Do not skip the three labels (an unlabelled disk is reported as waste in turn).

## A removal failed or was refused (incident `removal_failed`)

The incident names the step and one **fixed word**; the removal worker never passes AWS's own error text on. The word
says why the worker did not act:

| Word | Meaning | What to do |
|---|---|---|
| `attached` | The disk is attached to something now (or got attached during the wait) | Nothing: that is the safety working. The removal stops by itself |
| `not_found` | The disk or copy is already gone | Nothing; check no one else deleted it by mistake |
| `grace_not_over` | The delete order came before the grace period ended | Check the chart's `removal.graceHours` equals the worker's `grace_hours` (a test keeps them equal); otherwise a clock or label problem |
| `not_marked`, `already_marked` | The disk's `KavalRemoval` label is missing or already there | Look at the disk's labels; do not add one by hand |
| `no_backup`, `backup_unfinished` | There is no finished backup copy of this disk | Wait for the copy to finish (`describe-snapshots`), then the next scan files the step again |
| `not_kaval`, `other_env`, `terraform_owned`, `not_labelled` | The thing is not Kaval's to remove, belongs to another environment, to Terraform, or is not labelled well enough | A person decides. **Never** remove a Terraform-managed disk by hand without changing the Terraform that owns it |
| `newest_copy`, `not_past_retention`, `in_use_by_image` | The backup copy is protected, too young, or backs an image or lifecycle policy | Nothing: the scan should stop reporting it |
| `disabled` | The worker's switch is off (`enabled = false`) | Intended: turn removal on only on purpose |
| `aws_failed` | An AWS call failed (a permission, an outage) | `aws logs tail /aws/lambda/kaval-<env>-waste-removal --since 1d`; the role's statements in `infra/modules/waste-removal/main.tf` |
| `bad_order` | The order was not one of the three exact shapes | A bug, or someone wrote the orders parameter by hand: look at it (below) and report it |

## An approved order was never answered (alarm `KavalRemovalOrderUnanswered`)

Six hours with no answer from the worker means something between the executor and the worker is broken. Follow the
chain from the left:

```bash
# 1. the executor wrote the order
kubectl get configmap -n kaval-<env> kaval-<env>-removal-orders -o jsonpath='{.data.orders\.json}'
# 2. the server carried it out to AWS (the timer runs every 3 hours; start it now with: systemctl start kaval-cost-pull)
AWS_PROFILE=kaval aws ssm get-parameter --name /kaval/kaval-<env>/removal-orders --query Parameter.Value --output text
# 3. the worker ran and answered
AWS_PROFILE=kaval aws ssm get-parameter --name /kaval/kaval-<env>/removal-result --query Parameter.Value --output text
AWS_PROFILE=kaval aws logs tail /aws/lambda/kaval-<env>-waste-removal --since 1d
# 4. the server carried the answer back
kubectl get configmap -n kaval-<env> kaval-<env>-removal-result -o jsonpath='{.data.result\.json}'
```

The first place the order is missing is the broken link: the executor's right to patch the ConfigMap (step 1), the
server's right to write the parameter or its timer (step 2), the rule that wakes the worker or the worker's role
(step 3; run it by hand with `aws lambda invoke --function-name kaval-<env>-waste-removal /dev/null`), or the copy
back (step 4). Running the worker by hand is safe: it skips orders it has already answered and re-checks AWS for the rest.

## Turn removal off

- **Quickest:** set `removal.enabled: false` in the environment's values and merge; the planner and the executor's
  hand-over stop. Orders already in the parameter are still answered.
- **Hardest stop:** `enabled = false` on the `waste_removal` module in the environment's Terraform (show the plan, apply
  on the owner's go): every order is refused with `disabled`.

## Do not

- Do not delete a disk or a copy by hand because the scan flagged it; use the steps, so there is a backup and a record
- Do not edit the orders or result parameters by hand to "unstick" a removal: the worker re-checks AWS and refuses
  anything that is not true, and a hand-edited answer would hide a broken chain
- Do not add the `KavalRemoval` label by hand to a disk you want gone: it is the proof the backup step ran
- Do not switch the unanswered-order alarm off

## Escalation and ownership

- Owner: Roshan. `removal_failed` is told straight to Slack as an incident; the unanswered-order alarm is critical and
  repeats and escalates after 2 hours like the other Kaval health alarms.

## When it is fixed

The incident closes by itself 48 hours after the waste is gone (a removal done) or after the failing signals stop.
`kaval_removal_oldest_unanswered_order_seconds` is 0 when no order waits. Record the cause in the journal.
