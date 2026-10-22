# Lab 63 — Savings tracking, the cost-spike runbook and the Phase 6 exit test

**Phase:** 6 · **Story:** `KAV-97` · **Time:** about 3 hours (Parts 1 to 3 on the laptop, Part 4 live) ·
**Cost:** $0 to build; the live proof planted a 1 GiB disk for about 90 minutes (well under a cent) and ran staging for
about two to three hours (under 15 cents)

Kaval finds waste, asks a person twice, and deletes it ([Lab 59](lab-59-the-waste-scan.md),
[Lab 60](lab-60-safe-removal.md)). This lab adds the accounting that turns that into a claim you can show: money
promised, money approved, money confirmed saved. The decision is
[ADR-0065](../adr/0065-savings-count-only-what-the-worker-confirmed-gone.md). It also adds the runbook for a jump in the
bill and carries the phase's exit test.

## Prerequisites

- The project's Python environment: `export PATH="$PWD/.venv/Scripts:$PATH"`
- A throwaway Postgres for the database tests (Part 1); `AWS_PROFILE=kaval` for Part 4
- Part 4 needs `make tf-init ENV=staging` done on this laptop and staging up (`make staging-up`, run by the owner)

## Part 1 — The promise becomes a number

The removal planner now writes the monthly saving it promised into the delete step's `params` as
`saving_usd_month` (a plain amount such as `1.82`). If the scan's amount is missing or not shaped like an amount, nothing is
written, so "no price" can never be mistaken for $0.

```bash
pytest services/agent/tests/test_removal_planner.py -q
```

Read these first: `test_the_delete_step_stores_its_promised_saving_as_a_number`,
`test_a_missing_or_odd_price_is_stored_as_none_not_zero` (empty, text, negative, a comma, a float, `None`) and
`test_the_backup_step_and_a_backup_copy_carry_no_promised_saving`.

## Part 2 — The report

```bash
make savings-report        # whichever database you point it at (local, like make mttr)
make staging-savings       # staging's database, read over Session Manager (needs staging up)
```

Each thing proposed for deletion is one row in one state: `confirmed` (a person said yes **and** the worker answered
`removal_done` after the question), `approved` (yes, not yet confirmed), `waiting`, `declined`, `expired` (nobody
answered in a day). Totals show what was promised, what a person approved, what is confirmed gone each month, and an
estimate of the money saved so far (the monthly amount times the days since it was confirmed, over 30). It states on
every run that this is an estimate priced from the scan's table, not read off the AWS bill.

```bash
pytest services/executor/tests/test_savings.py -q      # 22 tests
```

What the tests refuse to count as saved (the "needs a yes" rule was checked by removing it and watching its test
fail): a confirmation with no yes behind it, approved-but-unconfirmed, a failed removal, a declined proposal, an expired question, an answer that
predates the question, another disk's answer, the backup step, the same disk asked about twice (one row, counted once).
Backup copies carry no amount: they are counted and shown as "no price", never as $0.

## Part 3 — The cost-spike runbook

`docs/runbooks/cost-spike.md` (indexed for the agent by `make index-runbooks`, listed in the runbook index): what the
rule that raises a `cost_spike` incident means (a service's day over three times the median of the week before and at
least 25 cents above it), the likely causes in order, the read-only commands that tell them apart, the remediation (every
step yours, each marked reversible or not), what not to do, and who owns it. It is for a person: the policy attaches no
fix to a cost spike.

## Part 4 — The Phase 6 exit test (live, staging; each step on the owner's own go)

The gate: *the agent finds real waste in your own account and you approve the fix.* Real means a real, billed disk in
the real account, found by the daily scan, asked about by code (never the model), and removed only after **your**
approvals. This is the `KAV-95` live proof run again on the finished pipeline, with the savings report reading it.

**Passed 2026-10-22** (times UTC; the owner approved both steps from their own terminal):

1. Merge of the change; `release.yml` built the images (`sha-5fc2f34`) and opened the pin pull request (#235), merged by the owner
2. `ASSUME_YES=1 make staging-up` (07:10 to 07:13, about 2 minutes 10 seconds to all pods ready). `make staging-savings` already
   worked live: it listed yesterday's `KAV-95` disk as confirmed ($0.09 a month) with "no price" for its promise, because that
   proposal predates the stored number
3. 07:16 a 1 GiB disk was created (`aws ec2 create-volume ... --tag-specifications` with `Project=kaval`, `Env=staging`,
   `ManagedBy=manual`, `Name=kav97-exit-test`), attached to nothing. The scan function was run by hand three times
   (`aws lambda invoke --function-name kaval-staging-waste-scan`): `findings: {volume_unattached: 1}`. The server's copy step
   was started by hand (`systemctl start kaval-cost-pull`, over Session Manager) instead of waiting up to three hours
4. 07:24 the planner filed step one (`backup_and_mark_volume`, action `826944d4`). **07:27:20 the owner approved**; the executor
   handed the order over at 07:27:40. After the next copy the worker labelled the disk at 07:39:06 and its backup copy
   (`snap-0ed60f5c51f4ec52e`) completed. Nothing was deleted
5. After the hour's grace (08:39): scan, copy, and at 08:44:05 the planner filed step two (`delete_idle_volume`, action
   `fe0818e4`) with `saving_usd_month = 0.09` stored. **08:46:18 the owner approved.** The worker deleted the disk (gone at
   08:47:48); the backup copy remained, `completed`, labelled `Env=staging`
6. `make staging-savings` at 08:49 showed the disk `confirmed`, promised $0.09, confirmed $0.09, alongside the earlier one:

```
vol-09b0b4178d6d7994f    confirmed       n/a   $  0.09     0  2026-10-21 09:12
vol-04b3904f5d31b2cd1    confirmed   $  0.09   $  0.09     0  2026-10-22 08:44
confirmed 2  approved 0  waiting 0  declined 0  expired 0
promised (everything priced)   $0.09 a month
approved by a person           $0.09 a month
confirmed gone (realised)      $0.18 a month
```

7. The `cost-spike.md` runbook was found in staging's retrieval index (7 sections; all 22 runbooks present), because runbooks
   ship in the image and the diagnoser indexes them at start

**Found live, fixed:** a second step-one question for the same disk was filed at 07:27:32, 12 seconds after the first yes and 8
seconds before the executor wrote its record. The planner held off asking again only once an execution row existed
(`removal._skip`), so a pass in that gap asked again. The duplicate (`1462f1c9`) was never approved and expires on its own. Fixed
in PR #236 (the pause now starts at the yes) with a test that fails without the fix.

**Seen, left as it is:** the first row (yesterday's disk) has no stored promise, so "confirmed gone" ($0.18) is larger than
"promised" ($0.09). That is the report being honest about a proposal that predates the change; production will show the same
for anything proposed before its next bring-up.

Cleanup: the test disk is already gone; the backup copy is kept 30 days by the scan's rule and is removed by hand or by the
removal path when it is past retention. Staging parks itself after four idle hours (or `make staging-down`).

## What this does not do

It does not read savings off the AWS bill, price backup copies, or push the numbers anywhere: the report is a command a
person runs. Production picks up the change at its next bring-up.
