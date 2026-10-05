# Lab 25 — Nightly auto-stop, the third stop path

**Phase:** 4 · **Time:** ~20 min build, plus one `terraform plan`/`apply` cycle
**Cost:** $0 — reuses an already-billed-for Lambda; EventBridge Scheduler's invocation volume
here is nowhere near its free tier

ADR-0008's stopping table has named three ways the database server stops since it was written:
`make down`/`make up`, the $38 hard-stop Lambda, and a 02:00 IST nightly schedule "if `make down`
was forgotten." The first two existed (Lab 24 closed the gap in the first one). This lab builds
the third. See [ADR-0008's 2026-10-05 amendment](../adr/0008-production-database-on-its-own-server.md)
for the full reasoning.

---

## What's built

### The schedule reuses the hard-stop Lambda — it doesn't duplicate it

`infra/modules/budget/lambda/hard_stop.py` already does exactly what 02:00 IST needs: scale the
app node's ASG to zero, and snapshot-then-stop every `Project=kaval` instance outside an ASG
(the database server; the dev server too, though that one usually auto-stops itself first,
ADR-0007). It already no-ops correctly when there's nothing to do — an ASG already at
`desired=0` stays at `desired=0`, an instance that's already stopped is skipped by the
`instance-state-name=running,pending` filter. So a nightly firing when `make down` was *not*
forgotten is silent and harmless. Writing a second Lambda with the same logic would just be two
places to keep in sync; invoking the same one from a new trigger is the whole lab.

### New Terraform, in `infra/modules/budget`

```hcl
resource "aws_scheduler_schedule" "nightly_auto_stop" {
  name       = "${var.name_prefix}-nightly-auto-stop"
  group_name = "default"
  state      = var.nightly_auto_stop_enabled ? "ENABLED" : "DISABLED"

  schedule_expression          = "cron(0 2 * * ? *)"
  schedule_expression_timezone = "Asia/Kolkata"

  flexible_time_window { mode = "OFF" }

  target {
    arn      = aws_lambda_function.hard_stop.arn
    role_arn = aws_iam_role.nightly_auto_stop_scheduler.arn
  }
}
```

EventBridge Scheduler takes an IANA timezone directly (`schedule_expression_timezone`), so
"02:00" means 02:00 IST without a manual UTC-offset conversion (20:30 UTC the previous day,
year-round — India has no DST) to get wrong twice a year if India ever changed it.

A narrow IAM role lets `scheduler.amazonaws.com` do exactly one thing — `lambda:InvokeFunction`
on the hard-stop function's ARN, nothing else — mirroring how the hard-stop Lambda's own role is
scoped to exactly scale-and-stop. The matching `aws_lambda_permission` (principal
`scheduler.amazonaws.com`, source ARN the schedule itself) is the other half of letting a
Scheduler-triggered invocation through; without it the role existing isn't enough.

### The toggle

```hcl
variable "nightly_auto_stop_enabled" {
  type    = bool
  default = true
}
```

`true` in `infra/envs/prod` for Phases 4–6 (the paused-between-sessions posture this whole
schedule exists for). The plan is to flip it `false` once Phase 7 makes the system always-on —
stopping things nightly would then be wrong, not redundant, so the switch needs to be a real
Terraform variable, not a comment saying "remember to remove this."

## Verification

```
$ aws scheduler get-schedule --name kaval-nightly-auto-stop --group-name default \
    --query '{State:State,Expr:ScheduleExpression,Tz:ScheduleExpressionTimezone,Target:Target.Arn}'
State: ENABLED
Expr:  cron(0 2 * * ? *)
Tz:    Asia/Kolkata
Target: arn:aws:lambda:ap-south-1:<account>:function:kaval-budget-hard-stop
```

`terraform plan` before applying showed exactly 4 resources to add (the role, its inline policy,
the Lambda permission, the schedule) and 0 to change — nothing about the existing hard-stop path
drifted from adding a second trigger to it. The Lambda's own correctness (scale-to-zero,
snapshot-then-stop, the no-op branches) was already proven live in Lab 01 and exercised for real
by the $38 threshold (`KAV-30`) and by Lab 24's pre-stop snapshot — this lab adds a second way to
reach the same proven code, not new logic to prove from scratch.

## What this doesn't do

It's a brake, not the primary mechanism — `make down` pausing things promptly (within the same
session) is still what keeps the bill down day to day. This only catches the case where that
step was skipped and the system was left running overnight. It also doesn't touch the dev
server's own 1-hour idle auto-stop (ADR-0007), which is independent and usually fires first.

## Related

- [ADR-0008](../adr/0008-production-database-on-its-own-server.md) — the decision and its
  2026-10-05 amendment
- [Lab 24](lab-24-pre-stop-snapshot-and-database-pause.md) — the pre-stop snapshot this schedule
  inherits for free, by calling the same Lambda
- `ROADMAP.md` Phase 4 — "Nightly auto-stop" line, now ticked
