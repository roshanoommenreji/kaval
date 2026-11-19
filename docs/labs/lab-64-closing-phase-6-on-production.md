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

## Part 5 — The session of 2026-11-16 (`KAV-147`)

A second production session, 9 hours 40 minutes, on the owner's typed go (up 11:31 IST, parked 21:12 IST). It closed what the first could not: a real button pressed, the scheduled scan seen, the checker's six-hour login ([Lab 98](lab-98-the-image-alarm-after-a-checker-restart.md) Part 6) and a live look at who holds what ([Lab 100](lab-100-the-cluster-half-of-the-access-review.md)). The database server's start needed one retry after AWS reported no capacity at first.

**Buttons pressed on real production cards** (the asks are forgotten snapshots the waste scan found; actor `slack:roshanoommenreji` on every row):

| Time (IST) | Press | Snapshot | What happened |
|---|---|---|---|
| 11:53 | Approve | `snap-05c5441b2406967bf` | Executor `success`: the removal order was handed to the removal worker |
| 11:54 | Approve | `snap-03e1b71bf4dcfd8ff` | The same |
| 11:59 | Deny | `snap-00d6126c3fdb686d2` | Recorded, not executed; the snapshot still exists |
| 12:11 | Deny x3 | `snap-02fd6f1b25fd2e385`, `snap-0ed512e8c3bc91c11`, `snap-0181c66ce31680851` | Recorded, not executed |

Read back at 21:10 IST from AWS: both approved snapshots no longer exist (`InvalidSnapshot.NotFound`); the denied one does. Ten other asks were left undecided on purpose; they expire on their own (ADR-0048) and **must not be approved after the park**, because the server that could carry them out is gone.

**The scheduled waste scan** was seen to have run by itself at 03:15 UTC on 14, 15 and 16 November, with 0 errors.

**Parked** by the runbook ([prod-rollout.md](../runbooks/prod-rollout.md) step 8): a fresh dump first (432K, uploaded), the plan showed one change (app server group 1 to 0), applied at 21:12 IST, the data volume snapshotted and the database stopped, the alarm `OK`. A read-only sweep found no running server, no loose disk, no address, no gateway or load balancer, no cluster. About 45 cents from running hours (the bill will confirm).

## Part 6 — The session of 2026-11-18 (`KAV-100`, `KAV-163`)

A third production session, about 1 hour 25 minutes, on the owner's typed gos (database resumed 23:34 IST on 18 November,
parked 00:57 IST on 19 November). It had two jobs. **`KAV-100`:** let one real question sit unanswered while both gateway
copies run, then count the messages Slack was sent against the two copies' logs. **`KAV-163`:** see whether the first
start-up under the two-size policy ([ADR-0103](../adr/0103-the-app-server-falls-back-to-a-second-size.md)) behaved.

**Start-up.** Database resumed 23:34; the plan was shown first (one change: the server group 0 to 1); applied at 23:41;
`make prod-smoke` passed 9 of 9 at 23:46 on `sha-f280013` (`v0.4.4`). The group got a `t4g.medium` at once. AWS had no
shortage this time, so **the second size was not used**, and the fallback is still proven on staging only (Lab 104).

**Why a disk had to be made.** The planner only asks about something it has not asked about lately. The four old snapshots
denied on 16 November are held back for seven days (`DENIED_COOLDOWN` in `removal.py`), and that is what happened: three
scans and a cost copy found 8 snapshot sightings and filed no question. That silence is a free proof that a "no" is not
asked again for a week. So a question had to come from something new: a 1 GiB disk (`vol-0ebbb4e58a35b100e`, tagged
`Project=kaval`, `Env=prod`, `ManagedBy=manual`, named `kav100-planted-disk`, in the same zone as the server), made on his
typed `go plant disk` at 00:12. A disk nobody has attached is the thing the waste scan looks for, and the plan for a lone
new disk is "back it up, then mark it", which deletes nothing.

**Getting the question out** (the order matters: scan first, copy after, because the planner ignores a sighting older than
the newest scan; steps are in [prod-rollout.md](../runbooks/prod-rollout.md), the planted-disk part):

1. `aws lambda invoke` of `kaval-prod-waste-scan`, three times (the first can run before a new disk is visible; here the
   second and third saw it).
2. `systemctl start kaval-cost-pull` on the app server, over Session Manager.
3. Wait. The collector read the file at 00:19:38 IST (18:49:38 UTC), the signal `volume_unattached` landed, and the
   planner filed the question at **00:20:35 IST**, posted to Slack at once. About five minutes from the scan (00:15) to the card.

The owner pressed nothing. He had been told to leave it alone, because an answer would end the thing being measured.

**The count.** The notifier writes one row per message into the `notification` table, keyed
`approval-reminder:<id>`, `approval-escalated:<id>` or `approval-expired:<id>`, and the key is unique, so a second send
would fail rather than duplicate. Read on the database server over Session Manager
(`docker exec postgres psql -U kaval -d kaval -At -c "select key, created_at from notification where key like '%<id>%'"`),
checked against `kubectl logs` of both gateway pods:

| What | Time (UTC) | After the question |
|---|---|---|
| Question filed (`backup_and_mark_volume`, `4cd1383b-…`) | 18:50:35 | 0 |
| `approval-reminder` row | 19:05:51 | 15 min 16 s |
| `approval-escalated` row | 19:20:52 | 30 min 17 s |
| Decisions recorded | none | the owner pressed nothing |

**One reminder and one escalation, no duplicates.** The logs agree: the two gateway pods take turns, and the other one
logged "another gateway copy is notifying, skipping this round". That is the lock from
[ADR-0069](../adr/0069-only-one-gateway-copy-notifies-at-a-time.md) working in production with real timing. It is the
check `KAV-100` was waiting for since the double alert of 27 October.

**Then the clean-up**, each on its own typed go: the disk was deleted by hand (`go delete disk`) and `describe-volumes`
now says it does not exist, with no loose available disk left; a fresh dump was taken at 00:56 (`kaval-20261118T192623Z.dump`,
436 KB); the plan showed one change (group 1 to 0); `apply park` at 00:57; the database was paused with its snapshot;
read back: server group 0 of 0, app server terminated, database stopped, alarm `kaval-prod-still-alive` `OK`.

**Left open on purpose.** The question is still in Slack. Its disk is gone and no server is left to carry anything out, so
it expires by itself and **must not be approved or denied** (the same rule as Part 5). It is the second time a question
outlived production; the ordinary expiry notice is what the runbook already promises for that.

**Cost:** about 7 cents (database server and app server for about an hour and a half, plus a 1 GiB disk for under an
hour); an estimate from running time, the bill confirms it.

## What was found live

1. **A double alert** *(closed 2026-11-19, Part 6).* The "nobody has answered" alert appeared twice at the same minute. Production runs two copies of
   the gateway (staging runs one), and the reminder, escalation and expiry messages are sent by a pass that reads "already
   sent?" and then posts, with no lock between the two copies and no unique key on the record
   (`services/gateway/kaval_gateway/approvals.py`, `notify_pass`). Harmless, but production would double every one of
   those messages. Filed as its own story (`KAV-100`) rather than patched in this run; it needs a lock or a claim-before-send, tested
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
