# ADR-0029: Staging parks itself when idle; `make staging-down` is the destroy

**Status:** Accepted
**Date:** 2026-10-08
**Related:** [ADR-0004](0004-environment-strategy-and-promotion.md) (promised "self-destructs after
four idle hours"), [ADR-0008](0008-production-database-on-its-own-server.md) (the stop-never-terminate
pattern and the pre-stop snapshot), [ADR-0007](0007-develop-on-an-aws-dev-server.md) (the dev server's in-instance
idle timer), `KAV-59`, Lab 30

## Context

ADR-0004 said staging "self-destructs after four idle hours". `KAV-57` built staging and proved it
live, but tore it down by hand, because nothing yet did either half of that sentence: there was no
`make staging-up`, and nothing watched for idleness.

Two questions had to be answered, and the second one changes the wording of the first ADR.

**1. Who notices idleness, and how?** The dev server's answer (ADR-0007) is a systemd timer inside the
instance that runs `shutdown -h now`. It does not transfer: staging is two machines (a disposable node
and a database server), the node can be mid-replacement, and `shutdown` on one machine stops one
machine.

**2. Can the idle watcher destroy staging?** Destroying means a VPC, a route table, three subnets, an
Auto Scaling Group, a launch template, IAM roles, SSM parameters, a bucket and a data volume with
`prevent_destroy`. Terraform does that well and knows the order. Nothing in AWS can run Terraform
for us without a lot of permission: a Lambda that re-implements the destroy order by hand is a second
copy of the infrastructure to keep correct, and CodeBuild or a CI runner would need an IAM role able to
delete almost everything in the account, plus credentials this project does not yet have in CI.

## Decision

1. **Staging parks itself; it does not destroy itself.** `infra/modules/idle-stop` is a Lambda on a
   15-minute EventBridge Scheduler. When no Session Manager session and no Run Command has touched
   either staging server for four hours, it scales the node's Auto Scaling Group to zero and stops the
   database server after a pre-stop snapshot of its data volume. The same stop-never-terminate shape as
   the budget hard stop.
2. **"Touched" is read from outside the machines**, from `ssm:DescribeSessions` (open and recently
   ended sessions) and `ssm:ListCommands`, floored by each server's launch time (EC2 resets it on every
   start). The node gets no new permission, and no AWS write permission lands on the box that runs the
   workloads. The decision is a pure function with unit tests.
3. **`make staging-up` resumes or builds.** It starts a parked database, applies `infra/envs/staging`
   (which restores the node's desired capacity), and waits until the pods answer. **`make staging-down`
   is the real destroy**: it automates the by-hand steps of Lab 29, including the data-volume release,
   and finishes by counting what is left tagged `Env=staging`.
4. **The lambda's IAM is scoped to staging.** `StopInstances` and `CreateSnapshot` carry a condition on
   the `Env=staging` tag, and the scale-down is allowed only on staging's own group. A bug in the
   function cannot park prod.

## What this costs, honestly

A parked staging is not free the way a destroyed one is. The database server's root disk and 10 GB data
volume stay: roughly **$1.6/month** while parked, against ~$0.50/month when it is destroyed straight
after each release (`docs/cost/budget-plan.md`). The project total moves by cents, not dollars. The
difference matters only if staging is parked for weeks, and `make staging-down` ends it. The Lambda
and Scheduler are inside the free tier (about 2,900 invocations a month), and the Lambda is part of
staging's state, so it disappears with staging.

## Alternatives considered

- **Self-destroy.** What ADR-0004 literally says. Rejected for the permission and duplication reasons
  above. Revisit if CI ever gets AWS credentials for another reason (the nightly dump, Phase 4):
  a scheduled workflow running `terraform destroy` would then be a small addition.
- **An in-instance timer on the node** (the dev server's pattern). Rejected: it would need
  `autoscaling:UpdateAutoScalingGroup` on the node's own role, and the database server would need
  its own timer. The node role also feeds pods through the instance metadata service.
- **A hard time-to-live since launch.** Simpler, and wrong in the dangerous direction: it stops
  staging in the middle of a long release check. Idleness is the right signal.
- **Count network or CPU activity as use.** CloudWatch would give it, but k3s and Flux generate steady
  background traffic, so it never reads as idle. A person at a keyboard always shows up as a session
  or a command.
- **Reuse the budget hard-stop Lambda.** It is armed by spend, scoped to `Project=kaval` (which includes
  prod's database) and knows prod's group. Pointing it at staging would either widen its permissions
  or fork it.

## Consequences

- ADR-0004's "self-destructs" now reads "parks itself, and `staging-down` destroys". It carries a
  dated amendment; the original sentence stays visible.
- Anything that talks to staging through Session Manager keeps it alive, including this repo's own
  scripts. A forgotten open port-forward session also keeps it alive; Session Manager ends idle
  sessions itself after the account's idle timeout (20 minutes by default), after which the clock
  starts.
- The account-wide 02:00 IST nightly auto-stop (ADR-0008) already stops staging's database server
  too, since it stops every running `Project=kaval` server outside an Auto Scaling Group. The node is
  the part only this Lambda scales down.
- **Revisit if:** staging is parked for long stretches (the $1.6 a month starts to matter), or CI gains
  AWS credentials (self-destroy becomes cheap to build).

## Measured (2026-10-08, Lab 30)

Idle threshold set to 15 minutes for the drill. Staging built (56 resources) and answered in about four
minutes; the 08:08 check left it alone at 728 s idle; the 08:23 check parked it at 1,628 s (ASG 1 to 0,
database stopped, pre-stop snapshot completed). `make staging-up` on the parked staging planned 0 to
add, 2 to change and was ready in 282 s; `make staging-down` destroyed 55 resources and left nothing
tagged `Env=staging` in 206 s. Prod was untouched throughout.
