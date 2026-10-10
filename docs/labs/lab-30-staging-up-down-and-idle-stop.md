# Lab 30 — `make staging-up` / `staging-down`, and staging that parks itself

**Phase:** 4 · **Time:** one session, mostly waiting for the clock · **Cost:** about $0.045/hour while
staging is up; the whole drill (build, idle, park, resume, destroy) was roughly an hour and a half of
that, so under $0.10

[Lab 29](lab-29-staging-environment.md) built staging and proved it live, then tore it down with a page
of commands typed by hand, because nothing yet created it on request or noticed it was idle.
[ADR-0004](../adr/0004-environment-strategy-and-promotion.md) had promised both: `make staging-up`, and
"self-destructs after four idle hours". This lab builds them and runs the full cycle against real AWS.

One word changed on the way: staging **parks** itself rather than destroying itself.
[ADR-0029](../adr/0029-staging-parks-itself-when-idle.md) explains why (nothing in AWS can run Terraform
for us without a very broad role). `make staging-down` is the real destroy.

---

## 1. The idle stop (`infra/modules/idle-stop`)

A Lambda, run every 15 minutes by EventBridge Scheduler. It asks three questions and stops when the
answer to all of them is "nothing, for `idle_hours`":

1. Is there an open Session Manager session on a staging server, or one that ended recently?
   (`ssm:DescribeSessions`)
2. Was a Run Command sent to a staging server recently? (`ssm:ListCommands`)
3. When did each server last start? (EC2's launch time, which resets on every stop and start.) This is
   the floor, so a node that came up a minute ago is never reaped before its first use.

The newest of those is "last activity". If `now - last_activity >= idle_hours`, it scales the node's Auto
Scaling Group to zero, takes a snapshot of the database's data volume, and stops the database server.
It never terminates anything. The decision itself is one pure function, `decide()`, in
`infra/modules/idle-stop/lambda/idle_stop.py`, with seven unit tests that need no AWS:

```
pytest infra/modules/idle-stop/lambda -q        # 7 passed
```

Its IAM is the narrow part: the scale-down is allowed only on staging's own group, and `StopInstances`
and `CreateSnapshot` carry a condition on the `Env=staging` tag. It is wired into
`infra/envs/staging/main.tf` as `module "idle_stop"`, so it lives and dies with staging. The module
refuses an `idle_hours` below 0.1 (six minutes), which would park a node before it had finished booting.

## 2. The lifecycle script (`scripts/ops/staging.sh`)

Three verbs, behind `make staging-up`, `make staging-status` and `make staging-down`.

- **`up`**: if the database server is stopped (parked), start it and wait for it; then
  `terraform apply` in `infra/envs/staging` (this also restores the node's desired capacity, which the
  idle stop set to zero); then poll the node over Session Manager until every pod in `kaval-staging` is
  `Running n/n`. Without `ASSUME_YES=1` it asks first and Terraform shows its own plan and prompt.
- **`status`**: the database server's state, then the pods, read with `kubectl` through Session Manager.
- **`down`**: stop the database, release its data volume from Terraform state (it has `prevent_destroy`,
  see Lab 29), destroy, delete the detached volume and staging's snapshots, and print what is left
  tagged `Env=staging`. The volume and snapshots are found by tag, not by state, so it still works
  after a previous run died halfway.

Note that `status` and `up`'s readiness polling are themselves Run Commands, so they count as activity.
That is correct: someone looking at staging is using it.

## 3. The plan, then the apply

For the drill the threshold was set to 15 minutes so the park could be seen inside a session
(`TF_VAR_idle_hours=0.25`). The real default is four hours.

```
cd infra/envs/staging
TF_VAR_idle_hours=0.25 terraform plan
# Plan: 56 to add, 0 to change, 0 to destroy.
```

That is Lab 29's 48 resources plus the eight for `idle_stop` (function, two roles and their policies,
log group, schedule, permission), all named `kaval-staging-*`. Then, from the repo root:

```
ASSUME_YES=1 TF_VAR_idle_hours=0.25 bash scripts/ops/staging.sh up
```

The node launched at 07:52:30 UTC and all four services (`agent`, `collector`, `executor`, `gateway`)
were `1/1 Running` about four minutes later; the script printed `Staging is up` and exited 0.

## 4. The Lambda, invoked by hand

Before trusting the clock, ask the function what it thinks:

```
aws lambda invoke --function-name kaval-staging-idle-stop --payload '{}' --cli-binary-format raw-in-base64-out out.json
cat out.json     # {"status": "active", "idle_seconds": 35}
```

Its log line named the last activity: the `status` command run a minute earlier. That is the first real
proof that the IAM grants (`ssm:DescribeSessions`, `ssm:ListCommands`, the EC2 reads) work and that Run
Commands are seen.

## 5. Leave it alone

Nothing was sent to staging after that. The scheduled runs read, from the function's log group
(`/aws/lambda/kaval-staging-idle-stop`):

| Time (UTC) | Verdict |
|---|---|
| 07:53:37 | `idle=False idle_for=11s` (first run, right after apply) |
| 08:08:23 | `idle=False idle_for=728s limit=900s` |
| 08:23:23 | `idle=True idle_for=1628s limit=900s` then `IDLE STOP APPLIED. kaval-staging scaled 1 -> 0.` and `Stopped i-0a64e8ff…` |

The second run is the one that matters: 728 seconds is inside the limit, so it correctly left staging
alone. The third parked it. Afterwards: the Auto Scaling Group at desired capacity 0, the database
server `stopped`, and one snapshot named `kaval-staging-database-pre-stop`, completed. Parking happened
up to one schedule interval after the threshold passed (here 12 minutes late: the threshold was crossed
at 08:11 and the next run was 08:23), which is the price of a 15-minute check.

## 6. Resume: `make staging-up` on a parked staging

```
ASSUME_YES=1 bash scripts/ops/staging.sh up
```

It saw the database server `stopped`, started it and waited for Session Manager and the health check
(`pg_isready` plus a sanity query: passed), then ran `terraform apply`. The plan was exactly what a
resume should be: **0 to add, 2 to change, 0 to destroy**. The Auto Scaling Group's desired capacity and
minimum went from 0 back to 1 (the idle stop had changed them outside Terraform, so Terraform put them
back), and the Lambda's `IDLE_HOURS` went from the drill's 0.25 back to 4. A fresh node booted, rebuilt
k3s and Flux from Git, and reattached to the surviving database. All four services were `1/1 Running`
and the script exited 0: **282 seconds** from the command to ready, including the database start.

## 7. Tear down: `make staging-down`

```
ASSUME_YES=1 bash scripts/ops/staging.sh down
```

It stopped the database server, released the data volume from state, planned **0 to add, 0 to change,
55 to destroy** (Lab 29's 47, plus the eight idle-stop resources), applied it, deleted the detached
volume and the pre-stop snapshot by tag, and printed `Left tagged Env=staging: 0 instances, 0 volumes,
0 VPCs`. **206 seconds.**

Checked separately afterwards: no Lambda, IAM role, SSM parameter, bucket, schedule or Auto Scaling
Group with `staging` in its name remained, and prod was exactly as before (its Auto Scaling Group at 0,
its database `stopped`, its data volume intact).

## Findings

- **The park fired 12 minutes after the threshold, by design.** The check runs every 15 minutes, so
  "after four hours idle" means four hours up to 15 minutes. For a cost control that is fine, and it
  is why the interval is a variable rather than a constant.
- **Run Commands are activity, so this lab's own scripts keep staging alive.** `status` and the readiness
  polling in `up` count. That is the right behaviour, and it means nothing else had to be built to
  stop a script from racing the clock.
- **Terraform noticed the idle stop's changes and undid them** on the next apply (desired capacity 0 to
  1). This is the same drift `make up` and `make down` handle for prod with `node.auto.tfvars`; staging
  needs no such file because `up` always wants the node back and `down` destroys it.
- **Git Bash rewrites `/aws/lambda/...` into a Windows path** when it is passed to the AWS CLI. Reading
  the Lambda's log group from this machine needs `MSYS_NO_PATHCONV=1`, the same trap as Lab 27.

## What this does not do

- It does not seed staging from a sanitised prod snapshot. `restore.sh` and `anonymise.sql` exist but
  need the nightly dump to S3. The timer that takes it is built (Lab 40, `KAV-74`) but has not yet run on
  AWS, and the restore into staging is `KAV-73`. Staging still comes up with an empty database. (Later: built and proven locally in Lab 41, ADR-0039.)
- It does not drive a release. `release.yml`, `promote.yml` and `rollback.yml` are separate lines.
- It does not destroy on idle (ADR-0029). A parked staging costs about $1.64 a month until
  `make staging-down`.
- It cannot see use that is not Session Manager. If staging ever gets a public endpoint that people
  hit directly, "idle" will need another signal.
