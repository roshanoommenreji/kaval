# Lab 60 — Safe removal: marking, backing up and deleting waste, with approval

**Phase:** 6 · **Story:** `KAV-95` · **Time:** about 4 hours (Parts 0 to 5 on the laptop, Parts 6 to 8 live) ·
**Cost:** $0 to build; the live proof plants a 1 GiB disk for about a day (about $0.09 a month while it exists) and its
backup copy (under one cent).

Once the scan has found a disk attached to nothing, or a backup copy past its retention, Kaval can remove it, in
steps, and only with a person's approval at each risky step. The decision is
[ADR-0063](../adr/0063-waste-is-removed-in-steps-by-a-separate-worker-and-only-with-approval.md); the finding is
[Lab 59](lab-59-the-waste-scan.md); when you are asked to approve, or a removal fails or stalls,
[the runbook](../runbooks/waste-removal.md).

The path of one disk, with the five places that can say no:

```
scan finds a loose disk ──► planner files step 1 (by code) ──► you approve ──► executor writes an order
   (Lab 59)                                                                            │
                                                                                       ▼
 server's timer ──► parameter ──► removal worker re-reads AWS, acts, answers ──► parameter ──► timer ──► signal
                                       (labels the disk, makes a backup copy)
 ... a grace period (7 days in production, 1 hour in staging) ...
 scan sees the mark + a finished backup ──► planner files step 2 ──► you approve ──► same road ──► disk deleted
```

## Prerequisites

- `AWS_PROFILE=kaval`; on Windows with Git Bash also `PYTHONUTF8=1 PYTHONIOENCODING=utf-8`
- The project's Python environment: `export PATH="$PWD/.venv/Scripts:$PATH"`
- Docker (a throwaway Postgres, and `make rules-test`), `terraform`, `helm`
- Parts 6 to 8 need `make tf-init ENV=staging` done on this laptop

## Part 0 — The byte count (before anything else was built)

- [x] The order and result hand-over fit: the one waste copy was turned into a loop over two read parameters plus one
  write, and the packed start-up script went from 15,528 to 15,736 of the 15,800 limit (EC2's own hard limit is
  16,384). That leaves almost nothing, so **the next addition to the script must first move something out**; the size
  test fails loudly if it grows. `pytest scripts/dev/test_cost_pull.py` (35 tests, including: no order writes
  nothing, an empty order writes nothing, no cluster means no read and no write, a missing result never overwrites
  the last good copy, the write uses the server's own login and exactly one parameter name)

## Part 1 — The worker's rules, without AWS

- [x] `infra/modules/waste-removal/lambda/waste_removal.py` holds one pure function, `decide()`. Its tests feed it
  made-up AWS answers and check what it must **refuse**: another project's disk, a disk with no `Project` label at
  all, Terraform's, another environment's, one that is attached (or got attached during the wait), one not yet
  marked or still inside its grace, one whose backup is missing or unfinished, the newest copy of any disk, a
  backup copy that is too young, anything that is not exactly one of three order shapes (a trailing newline, `*`,
  an `i-` id for a disk step), and everything when the switch is off:

```bash
pytest infra/modules/waste-removal/lambda -q    # 69 tests
```

  A real hole was found here by a test: a `$` at the end of a pattern also accepts a trailing newline, so an id with a
  newline after it passed. Every pattern in the worker and in the reader now uses `fullmatch`.

## Part 2 — The worker's login

- [x] The role file names exactly ten verbs (two `Describe`, `CreateSnapshot`, `CreateTags`, `DeleteVolume`,
  `DeleteSnapshot`, two parameter verbs, two log verbs) and a test fails on anything else (no stop, terminate,
  detach, release, copy, modify, attach, no wildcard verb, no `iam:` or `sts:`). Every changing verb is fenced in IAM
  to `Project=kaval` and not `ManagedBy=terraform`; the disk verbs also to this environment; **`DeleteVolume`
  additionally requires the `KavalRemoval` label to exist**, so IAM itself refuses an unmarked delete even if the
  code did not. The label verb may put only that one key. The server's role may write exactly one parameter

```bash
terraform -chdir=infra/modules/waste-removal init -backend=false && terraform -chdir=infra/modules/waste-removal validate
```

## Part 3 — The planner

- [x] `services/agent/kaval_agent/removal.py` files step-one and step-two proposals from the incident and the scan's
  own facts (read off AWS's labels: `pending_removal_since`, `backup_state`), by code. It never asks the model (the
  diagnoser's queue skips the two removable causes when removal is on), never asks a step twice, never asks about a
  scan fact older than 36 hours, never has more than five waiting, files nothing when switched off, and never
  proposes a delete until the backup copy is `completed` and the grace has passed. Their tests need Postgres:

```bash
docker run -d --rm --name kaval-test-pg -e POSTGRES_PASSWORD=testpw -e POSTGRES_USER=kaval -e POSTGRES_DB=kaval \
  -p 55432:5432 pgvector/pgvector:pg16
export POSTGRES_USER=kaval POSTGRES_PASSWORD=testpw POSTGRES_HOST=localhost POSTGRES_PORT=55432 POSTGRES_DB=kaval
alembic upgrade head
KAVAL_REQUIRE_DB=1 pytest services/agent/tests/test_removal_planner.py -q
```

## Part 4 — The executor, the policy and the approval message

- [x] The executor writes ONE order into ONE ConfigMap and records **"handed over"**, never "done". It refuses a
  removal unless removal is on, the proposal's `model` is `rule:removal`, the incident's cause is one of the two
  removable ones, and the target is the right kind of AWS id for the step. The policy says `ask` for the three steps
  and `never` for a `removal_failed` incident; a promotion cannot make any of it automatic. A migration adds the
  blast radius `resource`. The approval message shows what will happen and how to undo it, only for a rule-made
  proposal (text a model put in an action is never shown as if it were a fact):

```bash
KAVAL_REQUIRE_DB=1 pytest services/executor/tests/test_removal_handover.py services/gateway/tests/test_slack_chatops.py -q
docker run --rm -v "$(pwd -W):/w" openpolicyagent/opa:latest test /w/policy      # 29 tests
```

## Part 5 — The road there and back, and the whole ladder

- [x] The cost reader's pod reads the worker's answers (a whitelist of fixed words; `removal_done` is a record,
  `removal_failed` an incident for a person) and exports the age of the oldest unanswered order; a promtool rule turns
  six hours of it into `KavalRemovalOrderUnanswered`. The chart has one switch (`removal.enabled`) and refuses to
  render it without the cost reader and the waste scan being read:

```bash
make rules-test                                   # promtool, five rule files
pytest scripts/dev/test_chart_removal.py -q      # the rights the chart grants, and the negative cases
```

- [x] **The whole ladder, end to end, with a pretend AWS** (`services/agent/tests/test_removal_ladder.py`): the real
  scan, reader, correlator, planner, approval, executor, worker and answer reader, with one clock moved by hand. It
  proves: a loose disk goes up both steps and is deleted with its backup kept; a disk attached during the wait is
  never deleted; a forged delete order for an attached, or an unmarked, disk is refused by the worker itself and
  becomes an incident for a person. It also found a real bug (the planner could have asked to delete a deleted disk
  a day later), fixed before anything ran live.

## Part 6 — Staging proof (live; each step on the owner's own go)

- [x] `terraform plan` in `infra/envs/staging` (expect: the worker, its role, its rule, its schedule, two
  parameters and one changed policy on the server's role), read and applied on the owner's go (2026-10-19: 14 added, 4 changed, 0 destroyed; a second plan said "No changes")
- [x] The build containing the planner and the new executor is pinned in staging, then `removal.enabled: true` is set
  in staging's values (a second change). Merge only after the apply and a fresh server launch, because the server
  gets the order and result copy with its next launch (2026-10-19, PRs #208 and #209)
- [x] Plant a 1 GiB disk labelled `Project=kaval Env=staging ManagedBy=manual`, attached to nothing. Run the scan
  twice. A step-one proposal appears with its preview; **approve**: the disk gets the `KavalRemoval` label and a backup
  copy, nothing is deleted (2026-10-19, after the read grant below: label at 06:22:55Z, backup completed)
- [x] After the hour's grace and the next scan, a step-two proposal appears naming the backup; **approve**: the disk is
  gone and the backup copy remains (2026-10-21: scans twice, the server's copy, the proposal appeared within 5
  minutes, approved at 09:27Z; the worker answered `ok` at 09:30Z and the cost reader recorded `removal_done`; the
  disk was gone, the backup completed and kept, saving $0.09 a month)
- [x] **Undo:** create a disk from the backup copy and see the data is back; delete the test disk and copy afterwards
  (2026-10-21: `aws ec2 create-volume --snapshot-id <backup>` gave a 1 GiB disk, `available`, recorded as made from
  the backup. The test disk had never held files, so what this shows is that a disk can be rebuilt from the copy, not
  a file coming back. Both the new disk and the backup copy were deleted afterwards)

> **Staging's approval window is 15 minutes** (the gateway is given short timers for game days; production's default
> is 4 hours). Have the action id ready before the proposal appears: a rule-made proposal has no `make chaos-propose`
> experiment to print it, so read it from the `action` table (a throwaway pod run as the agent's login, as the chaos
> script does) and approve with `make chaos-approve ACTION=<id>`.

> **Found live on the first run (2026-10-19):** the planner failed every pass with `permission denied for table
> decision` because the agent's database login could not read approvals. Fixed with a read-only grant on `decision`
> and `execution` in `scripts/ops/db-roles.sql`, plus a role test (`services/shared/tests/test_db_roles.py`). The
> database server's own log (over Session Manager) shows the full message; the pod log shows only the error's type.

## Part 7 — Negative checks, live

- [x] An attached disk, another project's disk and a Terraform-managed disk are each refused by the worker even when
  an order names them (`aws ssm put-parameter` of a hand-made order, then the worker's `refused` answer and a
  `removal_failed` incident). 2026-10-21, five throwaway 1 GiB disks and five hand-made `delete_volume` orders: other
  project `not_kaval`, Terraform-owned `terraform_owned`, attached `attached`, marked a moment ago `grace_not_over`,
  marked long ago but with no backup `no_backup`. All five disks still existed afterwards, no backup was made, and
  all five were deleted by hand at the end. The `removal_failed` notes appear when the server next copies the
  answers into the cluster
- [x] A disk attached during the wait never reaches step two (the attached disk above was refused at the delete
  step with `attached`; the check runs before the wait and the backup are even looked at)

## Part 8 — Production

- [ ] At its bring-up (`KAV-98`): the module is already wired in `infra/envs/prod/main.tf`; plan shown first, applied
  on the owner's go, then `removal.enabled` in production's values. Production keeps the seven-day grace

## What this does not do

It does not remove servers, fixed addresses or unlabelled things (a person does; `KAV-95` is two kinds only). It does
not track money saved (its own roadmap line). It never deletes without both approvals, and never anything labelled
for another project, another environment or Terraform.
