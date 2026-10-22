# Lab 63 — Savings tracking, the cost-spike runbook and the Phase 6 exit test

**Phase:** 6 · **Story:** `KAV-97` · **Time:** about 3 hours (Parts 1 to 3 on the laptop, Part 4 live) ·
**Cost:** $0 to build; the live proof plants a 1 GiB disk for a couple of hours (about 9 cents a month while it exists,
so well under a cent) and runs staging for about two hours (about $0.09)

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
approvals. This is the `KAV-95` live proof run again, on the finished pipeline, with the savings report reading it.

**Not yet run.** Steps, in order:

1. Merge this change; `release.yml` builds the images and pins staging to them
2. `make staging-up` (the owner's go), about 5 minutes
3. Plant a 1 GiB disk labelled `Project=kaval Env=staging ManagedBy=manual`, attached to nothing (Lab 60 Part 6)
4. Two scans later: the step-one proposal appears (the backup and the mark; it saves nothing, so the report does not
   list it). Approve it yourself: `make chaos-approve ACTION=<id>`
5. After the hour's grace and the next scan, the step-two proposal appears with its promised saving. `make staging-savings`
   shows `waiting` with the promise. Approve it yourself
6. The worker answers; the report shows `confirmed`, the same amount as promised, and days saved
7. `make staging-down` or park it, and delete the backup copy

Record here, when run: the times, the action ids, and the report's output.

## What this does not do

It does not read savings off the AWS bill, price backup copies, or push the numbers anywhere: the report is a command a
person runs. Production picks up the change at its next bring-up.
