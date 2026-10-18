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

- [ ] `terraform plan` in `infra/envs/staging` (expect: the function, its role, its schedule and log group, the
  parameter, and one changed policy on the server's role), read and applied on the owner's go
- [ ] The build containing the reader is pinned in staging (done 2026-10-18, `sha-d7b274d`, PR #202), then
  `costReader.wasteScan.enabled: true` is set in staging's values (a second change, `KAV-94` staging-on PR; an older
  image would not start with the new argument). Merge it only after the apply and a fresh launch, because the server
  gets the copy step with the next launch and until then the reader would report the hand-over as missing
- [ ] `aws lambda invoke --function-name kaval-staging-waste-scan /dev/null` writes `status: ok`; the pod's log shows
  `nothing new`; `kaval_waste_scan_failing` is 0
- [ ] Memory of the cost reader pod before and after, measured (`kubectl top pod`; the limit is 128 MiB, it ran at 86)

## Part 6 — Plant waste, find it, remove it, find nothing

On the owner's go, with a separate go for the removal:

- [ ] Create a 1 GiB disk labelled `Project=kaval Env=staging ManagedBy=manual`, attached to nothing
- [ ] Run the function twice (the first only remembers it). Expect an incident named `idle_volume:aws:ebs/vol-…`
- [ ] A 13-hour-old staging server, if one is up, shows as `server_overstay`; a deliberately unlabelled test resource
  shows as `resource_unlabelled`
- [ ] Delete the disk. Run the function again: no finding. The incident closes 48 hours later by itself
- [ ] The negative checks: another project's resources and production servers are never in the output

## Part 7 — Failure is loud

- [ ] Break the parameter on purpose (a copy of the function with a bad name, or the role's `Look` statement removed
  in a throwaway apply): the document says `failed` with the fixed word `describe_failed` and the alarm number is 1
- [ ] Restore it; the number returns to 0 by itself

## Part 8 — Production

Production's own plan is shown first and applied at its bring-up, together with the cost reader and the tag fixes, in
the production bring-up story (`KAV-98`). Its first scan also reports the unlabelled resources that exist then.

## What this does not do

It does not remove anything (`KAV-95`). It does not read every kind of resource (servers, disks, snapshots and fixed
addresses only; `make tag-scan` reads the rest). It cannot see a disk that is attached to a stopped server and never
used: that costs the same and looks in use. Where it flags something, the first step is always a person looking.
