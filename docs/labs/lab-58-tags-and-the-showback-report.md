# Lab 58 — Tags and the show-back report: who owns what, and who spent what

**Phase:** 6 · **Story:** `KAV-93` · **Time:** about 2 hours (Parts 1 to 4 on the laptop, Parts 5 to 7 live) ·
**Cost:** $0.01 per show-back report; everything else is free.

Every Kaval resource carries three labels, a test stops code that would create an unlabelled one, a scan lists the
ones that slipped through, and a report splits the month's bill by AWS service and environment. The decision is
[ADR-0061](../adr/0061-every-resource-carries-three-tags-and-the-bill-is-split-by-them.md); if the scan finds
something, [the runbook](../runbooks/untagged-resources.md).

## Prerequisites

- `AWS_PROFILE=kaval`; on Windows with Git Bash also `PYTHONUTF8=1 PYTHONIOENCODING=utf-8`
- The project's Python environment: `export PATH="$PWD/.venv/Scripts:$PATH"`
- `terraform`, and `make tf-init ENV=staging` done once on this laptop

## Part 1 — Look before changing anything

```bash
aws ce list-cost-allocation-tags --query 'CostAllocationTags[].[TagKey,Status]' --output text   # which labels the bill knows
python scripts/ops/tag_scan.py                                                                    # the scan (Part 3 explains it)
```

Seen on 2026-10-18, before any change: **only `Project` was active** as a cost label, and the scan found **12 of
103 Kaval resources** with a missing label (the staging server, its unlabelled disk, and 10 pre-stop snapshots),
plus 33 of `stock-trader`'s counted and left alone.

## Part 2 — The offline check (runs in CI)

`scripts/ops/tag_policy.py` is both the list of rules and the check. `python scripts/ops/tag_policy.py` runs it over
the repository; `pytest scripts/ops/test_tag_policy.py` runs it and also feeds each rule a broken input (an
environment missing `Env`, a copy-pasted `staging` block in `prod`, a commented-out block, a node module whose disk
or group is unlabelled, a job that forgets one key) and expects the failure.

Run against the code **before** the fix, it reports exactly the defects the live scan found (the group, the disk,
`ManagedBy` and `Env` on the snapshots): 9 problems; after it, none.

## Part 3 — The scan of the live account

```bash
make tag-scan      # read-only; exit status 1 if anything is missing
```

It reads two sources (the tagging service, and EC2 directly for servers, disks, snapshots and fixed addresses,
because a resource with no tags at all is invisible to the first) and prints the Kaval resources with a missing or
wrong label, a count per `Env`, and how many resources belong to another project. Account ids are masked.

## Part 4 — Close the gaps at the source

Four edits, each covered by Part 2's check: the node module (`infra/modules/node/main.tf`) labels the instance, its
disk and the Auto Scaling group from one `required_tags` list; the two stop jobs add `ManagedBy=kaval-lambda`, and
the hard-stop job copies `Env` from the disk it snapshots.

## Part 5 — Staging: apply, then prove a new server is labelled

- [x] `terraform plan` in `infra/envs/staging`, read (3 changed in place, 0 added, 0 destroyed: the launch
  template, the group, the idle-stop job's code), applied 2026-10-18 on the owner's go; a second plan said no changes.
  The group now carries `Env` and `ManagedBy` and launch template version 9 labels the instance and its disk. The
  server that was already running keeps its old labels (tags are applied at launch), so the scan still lists it
- [ ] A new server is launched (`make staging-up` after the current one parks) and `make tag-scan` shows the server
  **and its disk** with all three labels

## Part 6 — Labels on the bill

- [x] On the owner's go (2026-10-18): `aws ce update-cost-allocation-tags-status --cost-allocation-tags-status
  TagKey=Env,Status=Active TagKey=ManagedBy,Status=Active`, then
  `aws ce start-cost-allocation-tag-backfill --backfill-from 2026-08-01T00:00:00Z` (status PROCESSING; once per 24 hours)
- [ ] The next day (the data takes up to a day to appear): `make cost-showback` and read the "no `Env` label" share
- [x] On the owner's go (2026-10-18): the 10 old snapshots and the staging server's root disk were labelled with
  `aws ec2 create-tags` (values read from the snapshots' source disks: 6 prod pre-stop `Env=prod ManagedBy=kaval-lambda`,
  3 staging pre-stop `ManagedBy=kaval-lambda`, 1 prod pre-migration backup `Env=prod ManagedBy=manual`, the root disk
  `Project=kaval Env=staging ManagedBy=terraform`). `make tag-scan` went from 12 findings to 1: the staging server that
  was already running, which a new launch replaces

## Part 7 — Production

Production's own plan is shown first and applied at its next bring-up, together with the other pending server-settings
changes. Its idle-stop and hard-stop jobs are updated by the same apply. Tracked in the production bring-up story.

## What this does not do

It does not run on a schedule or alert (that comes with waste detection, `KAV-94`), and it does not stop anyone
making an unlabelled resource by hand in the console: the scan finds it afterwards.
