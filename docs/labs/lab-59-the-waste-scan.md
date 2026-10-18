# Lab 59 — The waste scan: finding money that never moves

**Phase:** 6 · **Story:** `KAV-94` · **Time:** about 3 hours (Parts 1 to 4 on the laptop, Parts 5 to 7 live) ·
**Cost:** $0 for the scan itself; the live proof plants a 1 GiB disk for a day or two (about $0.09 a month while it exists).

Once a day a small function looks for four kinds of waste and for resources missing their labels, and leaves what it
found where the cluster can read it. Each finding becomes an incident for a person. The decision is
[ADR-0062](../adr/0062-waste-is-found-daily-by-a-read-only-scan-and-reported-to-a-person.md); when it finds something,
[the runbook](../runbooks/waste-found.md); when it stops running, [this one](../runbooks/waste-scan-not-running.md).

## Prerequisites

- `AWS_PROFILE=kaval`; on Windows with Git Bash also `PYTHONUTF8=1 PYTHONIOENCODING=utf-8`
- The project's Python environment: `export PATH="$PWD/.venv/Scripts:$PATH"`
- Docker (for a throwaway Postgres and for `make rules-test`), `terraform`, `helm`
- `make tf-init ENV=staging` done once on this laptop

## Part 1 — Look at the account before changing anything

```bash
make tag-scan     # read-only; the same labels the scan will watch
aws ec2 describe-volumes --filters Name=status,Values=available --query 'Volumes[].[VolumeId,Size]' --output text
aws ec2 describe-addresses --query 'Addresses[?AssociationId==null].[AllocationId]' --output text
aws ec2 describe-snapshots --owner-ids self --query 'sort_by(Snapshots,&StartTime)[0].[SnapshotId,StartTime]' --output text
```

Seen on 2026-10-18: no unattached disk, no unattached address, and the oldest backup copy was 12 days old. **There is
nothing to find**, so the rules cannot be tuned on real examples; Parts 2 and 6 use made-up answers and a planted disk.

## Part 2 — The rules, tested without AWS

`infra/modules/waste-scan/lambda/waste_scan.py` holds the rules in one pure function, `scan(inventory, now, env,
previous)`. Run its tests:

```bash
pytest infra/modules/waste-scan/lambda -q
```

They feed it made-up AWS answers and check what it says **and what it must not say**: a clean account is silent,
another project's resources are counted and never reported, a disk seen once is remembered but not reported, a
production server is never flagged however long it runs, the newest copy of a disk is never flagged however old, no
tag text reaches the output (a tag set to "ignore previous instructions…" is not in what is written), the output
always fits a parameter, and the role file contains no verb that changes the account.

## Part 3 — The reading side, against a real database

The cluster's half (`services/collector/kaval_collector/waste.py`) reads the hand-over and writes signals. It treats
the hand-over as untrusted: every finding is rebuilt from a whitelist, so a malformed or hostile one is refused and
counted, never stored. Its tests need Postgres; a throwaway one:

```bash
docker run -d --rm --name kaval-test-pg -e POSTGRES_PASSWORD=testpw -e POSTGRES_USER=kaval -e POSTGRES_DB=kaval \
  -p 55432:5432 pgvector/pgvector:pg16
export POSTGRES_USER=kaval POSTGRES_PASSWORD=testpw POSTGRES_HOST=localhost POSTGRES_PORT=55432 POSTGRES_DB=kaval
alembic upgrade head
KAVAL_REQUIRE_DB=1 pytest services/collector/tests/test_waste.py services/agent/tests/test_correlate.py -q
docker stop kaval-test-pg
```

The same file checks that the five kinds become the incidents the runbook names, with the right severities, and that
four of them are "for a person" (no model, no proposal, policy says `never`).

## Part 4 — The server's copy step, the chart and the alarms

```bash
pytest scripts/dev/test_cost_pull.py scripts/dev/test_chart_cost.py -q   # the copy step, and what the chart renders
make rules-test                                                          # promtool over both alarm files
opa test policy/                                                         # or the openpolicyagent/opa container
```

The copy step is six lines in the cost-pull script, run on every 3-hour tick before the bill's own freshness check: it
reads one parameter with the server's own role and writes one ConfigMap, and a failure never stops the bill. The tests
cut the script out of the template and run it against stand-ins for `aws`, `kubectl` and `curl`, including: the copy
happens when the bill is fresh and asks for no money; a missing parameter leaves the old ConfigMap alone; no cluster
means no read; the read uses the server's own login, never the cost reader's borrowed one.

## Part 5 — Staging: apply, switch on, prove

- [x] `terraform plan` in `infra/envs/staging` (expect: the function, its role, its schedule and log group, the
  parameter, and one changed policy on the server's role), read and applied on the owner's go (2026-10-18: 9 added, 3 changed, 0 destroyed; a second plan said No changes)
- [x] The build containing the reader is pinned in staging (done 2026-10-18, `sha-d7b274d`, PR #202), then
  `costReader.wasteScan.enabled: true` is set in staging's values (a second change, `KAV-94` staging-on PR; an older
  image would not start with the new argument). Merge it only after the apply and a fresh launch, because the server
  gets the copy step with the next launch and until then the reader would report the hand-over as missing
- [x] `aws lambda invoke --function-name kaval-staging-waste-scan /dev/null` writes `status: ok`; the pod's log shows
  `nothing new`; `kaval_waste_scan_failing` is 0 (first scan 2026-10-18 16:51 UTC: ok, 0 findings; the server's own
  timer copied it into the cluster by itself)
- [x] Memory of the cost reader pod before and after, measured (`kubectl top pod`; the limit is 128 MiB, it ran at 86):
  84 MiB with the waste reading on

## Part 6 — Plant waste, find it, remove it, find nothing

On the owner's go, with a separate go for the removal:

- [x] Create a 1 GiB disk labelled `Project=kaval Env=staging ManagedBy=manual`, attached to nothing (2026-10-18, $0.09 a month)
- [x] Run the function twice (the first only remembers it). Expect an incident named `idle_volume:aws:ebs/vol-…`
  (proved: scan 1 listed it only under `candidates`; scan 2 reported it; after the server's timer ran, the cost reader
  wrote one signal about two minutes later and the correlator opened `idle_volume:aws:ebs/vol-…`, low. Seen: the report
  says `days_unattached: 1` for a disk seconds old, because it counts the calendar day it was first seen)
- [ ] A 13-hour-old staging server, if one is up, shows as `server_overstay` (not yet seen live: staging parks itself
  after 4 idle hours, so it has to be kept awake past 12 hours)
- [x] A deliberately unlabelled test resource shows as `resource_unlabelled` (2026-10-18: a 1 GiB disk labelled
  `Project=kaval ManagedBy=manual` with no `Env`; one scan, one finding with `problems: ["Env"]`, and about 5 minutes
  after the copy the correlator opened `resource_unlabelled:aws:ebs/vol-…`, low; the disk was deleted and the next scan
  said `findings: []`)
- [x] Delete the disk. Run the function again: no finding (proved 2026-10-18: `findings: []`, `candidates: {}`, copied
  into the cluster). The incident closes 48 hours later by itself (to be seen on 2026-10-20)
- [x] The negative check for another project (2026-10-18: a 1 GiB disk labelled `Project=waste-test-other` attached to
  nothing; the scan output had `left_alone: 1` and no finding for it)
- [ ] The negative check for production servers: not possible live while production is parked (no server exists); it
  stays covered by the unit tests and is to be seen at the production bring-up (`KAV-98`)

## Part 7 — Failure is loud

- [x] Break it on purpose: instead of a Terraform apply, one temporary inline rule that only **denies** the four
  `Describe` calls was added to the function's role (`aws iam put-role-policy --policy-name drill-deny-look`, a deny
  overrides the allow; Terraform does not manage it). Run the function: it fails with `UnauthorizedOperation`, and the
  document says `status: failed`, `reason: describe_failed`. After the server's copy step (`systemctl start
  kaval-cost-pull`) and the reader's next 5-minute poll, its log said `no good waste scan: describe_failed` and
  `kaval_waste_scan_failing` was **1** (2026-10-18, about 17:57 to 18:03 UTC)
- [x] Restore it (`aws iam delete-role-policy`): the next run wrote `status: ok`; once copied, the number was **0** again
  within 5 minutes with no other action, and `terraform plan` in staging said No changes. The alarm itself needs the
  number at 1 for 4 hours before it fires, so it did not fire in this 15-minute test; that rule is covered by the
  promtool tests (`make rules-test`)

## Part 8 — Production

Production's own plan is shown first and applied at its bring-up, together with the cost reader and the tag fixes, in
the production bring-up story (`KAV-98`). Its first scan also reports the unlabelled resources that exist then.

## What this does not do

It does not remove anything itself (removal is a separate worker, [Lab 60](lab-60-safe-removal.md), `KAV-95`). It does not read every kind of resource (servers, disks, snapshots and fixed
addresses only; `make tag-scan` reads the rest). It cannot see a disk that is attached to a stopped server and never
used: that costs the same and looks in use. Where it flags something, the first step is always a person looking.
