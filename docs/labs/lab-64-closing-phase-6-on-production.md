# Lab 64 — Closing Phase 6 on production: the smoke test, a planted disk, and a night

**Phase:** 6 · **Story:** `KAV-90` (also `KAV-92` criterion 5 and `KAV-98` criterion 4) · **Time:** about 2 hours of
attention, then a night nobody has to be awake for · **Cost:** about $0.30 in all (see Cost below)

Phase 6 was built and proven on staging. Five checks could only be answered by production itself: that it comes up
from parked with the plan shown first, that its nightly dump fires on its own, that its own gateway posts a real
finding to Slack, what its time to restore is, and that it is parked again afterwards. This lab is how they were
answered on 2026-10-26 to 2026-10-28. The decisions are in
[ADR-0066](../adr/0066-production-gets-one-real-planted-finding-and-a-read-only-smoke-test.md).

All times are IST (UTC+5:30). The shell on the laptop (Git Bash) prints UTC even when asked for IST; AWS shows UTC too.

## Prerequisites

- `AWS_PROFILE=kaval`, Terraform connected to production (`scripts/ops/tf-init.sh prod`)
- Production parked (server group at 0, database stopped) and `main` frozen: no merges while the run is in progress
- The release to run is already promoted (`promote.yml`, then its pull request merged) and on the passed-staging record
- A person at the keyboard who can answer in Slack; each live step below is run only on that person's own go

## Part 1 — Staging first, on the exact build (day 1)

`ASSUME_YES=1 make staging-up` (about 4.5 cents an hour), then `make staging-smoke` on `sha-eb3f81a`. A pass is recorded
in `deploy/promotion/passed-staging.json` by a pull request. This also boots the **newer server image**
(`ami-07512216ac4bcba51`) once on staging, because the server's image is looked up as "latest" at plan time and
production's plan would otherwise pick it up untested. Staging then parked itself at the 02:00 IST brake
([Lab 62](lab-62-the-spending-brake.md)).

Then `gh workflow run promote.yml --ref main -f tag=sha-eb3f81a`: the gate checks the record, the registry's digests, the
direction and the `uat` sign-offs, and opens a pull request that points production at the tag. Merging it changes
nothing live; production is parked.

## Part 2 — Production's plan, shown first (day 2, evening)

```bash
bash scripts/ops/tf-init.sh prod
cd infra/envs/prod
terraform plan -input=false -var app_node_desired_capacity=1 -out=<file>
```

The `-var` overrides the parked setting for this one plan, so the file `node.auto.tfvars` (the parked setting) never has
to be moved and nothing has to be restored afterwards. The plan showed exactly two in-place changes: the server group
`0 -> 1` and the launch template's image `ami-0b16... -> ami-07512216ac4bcba51`. **The image must be the one staging
tested; if it is any other id, stop and re-test on staging.**

On the owner's go:

```bash
aws ec2 create-snapshot --volume-id <the database data volume> --description "kaval-prod pre-bring-up backup before <tag>, DB stopped"
aws ec2 wait snapshot-completed --snapshot-ids <it>
bash scripts/ops/resume-database.sh          # starts it, waits for SSM, pg_isready + a sanity query
terraform apply <file>                       # on a second go
```

The first apply attempt failed with a DNS error on the laptop ("no such host" for the state bucket). Nothing had been
applied; running the same saved plan again worked.

## Part 3 — The smoke test and the planted disk

```bash
make prod-smoke
```

Passed first time, about 4 minutes after the apply: 10 pods running with no restarts, every service on
`sha-eb3f81a`, the running digests equal the registry's, the database at the migration head, the API answering, the
images equal to the ones on the staging record, and the server's three timers active with the dump set for 19:30
UTC. Notes (not gates): the model server was still loading; 1,763 MB of memory was available.

Then one real 1 GiB disk was planted on the owner's go:

```bash
aws ec2 create-volume --availability-zone ap-south-1a --size 1 --volume-type gp3 \
  --tag-specifications 'ResourceType=volume,Tags=[{Key=Project,Value=kaval},{Key=Env,Value=prod},{Key=ManagedBy,Value=manual},{Key=Name,Value=kav90-prod-planted-disk}]'
aws lambda invoke --function-name kaval-prod-waste-scan --cli-binary-format raw-in-base64-out out.json   # three times
python ssm_run_prod.py "systemctl start kaval-cost-pull"   # over Session Manager, instead of waiting up to 3 hours
```

The first scan ran while the disk was still being created and found nothing; the second and third found
`volume_unattached: 1`. **Order matters:** scan, then the copy step. The planner ignores a sighting that is older than the
newest scan, so a scan that runs after the last copy leaves the planner waiting for the next one.

What production did by itself, with no row written by hand:

| Time (IST) | What |
|---|---|
| 22:25 | incident `idle_volume:aws:ebs/vol-0b2a...` opened by the collector |
| 22:27 | the planner's step-one question reached Slack, with Approve and Deny buttons, the plain-language "what will happen" and "to undo" lines, blast radius `resource`, reversible, confidence 1.00 |
| 22:57 | the "nobody has answered for 30 minutes" alert fired by itself |

It was not answered: the point of the check is that the post is seen, and an answer is not required by `KAV-90`.
The disk was deleted by hand afterwards (`aws ec2 delete-volume`, on the owner's go) and `describe-volumes` showed no loose
disk left in the account.

## Part 4 — The night

Nothing to do. By morning:

- **01:00** the dump timer fired by itself (19:30:05 UTC) and wrote `postgres/kaval-20261027T193005Z.dump` (348 KB) and
  `postgres/LATEST` in production's backup bucket.
- **02:00** the brake took the server group to 0 (20:30:06 UTC) and the database server got its pre-stop snapshot
  (`kaval hard-stop pre-stop snapshot`), then was stopped. This is the brake's first real run on production.
- The daily **backup-watch** run (GitHub Actions) passed after that dump.
- `terraform plan` on production says **No changes**: parked, matching its files.

## What was found live

1. **A double alert.** The "nobody has answered" alert appeared twice at the same minute. Production runs two copies of
   the gateway (staging runs one), and the reminder, escalation and expiry messages are sent by a pass that reads "already
   sent?" and then posts, with no lock between the two copies and no unique key on the record
   (`services/gateway/kaval_gateway/approvals.py`, `notify_pass`). Harmless, but production would double every one of
   those messages. Filed as its own story rather than patched in this run; it needs a lock or a claim-before-send, tested
   with two passes at once, and proven on staging with two copies first.
2. **A question can outlive production.** The question expired at about 02:26, after the brake had stopped the server at
   02:00, so nothing could send the "expired" notice or take the buttons off. Pressing Approve then would reach a parked
   gateway. This happens whenever production is parked with an open question; noted for the follow-up.
3. **The Approve button in production has not been pressed.** Slack to production's gateway (the interactive path) is
   proven on staging only. It is exercised the first time a real production question is answered.

## Done when

- `make prod-smoke` passed on the real production
- the dump timer fired by itself and the daily check passed on that dump
- a real Slack post from production was seen
- production's restore time is recorded as staying a staging figure, with the reason ([Lab 44](lab-44-recording-whether-a-fix-held.md))
- production is parked, the planted disk is gone, and the cost is noted in `docs/cost/budget-plan.md`

## Cost

About **$0.30**: production's server group ran 16:48 to 20:30 UTC (3 h 42 min, about $0.08) and its database server about
3 h 46 min (about $0.04); staging ran 2 h 37 min the night before (about $0.09); the planted disk lived about 6 hours
(under a cent) and the backup copies cost a few cents a month until deleted. The plan expected 70 cents for 9 hours; the
brake stopped it at about 4. The figure is from running hours; the bill will confirm it.

The backup copy taken before the bring-up (`snap-065c5442b7b7f5f36`) and the test copy from the earlier staging proof
(`snap-0ed60f5c51f4ec52e`) are still there; each is deleted only when the owner says so.
