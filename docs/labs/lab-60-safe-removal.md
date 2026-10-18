# Lab 60 — Safe removal: marking, backing up and deleting waste, with approval

**Phase:** 6 · **Story:** `KAV-95` · **Status:** planned; each box is ticked as the step is built and proven ·
**Cost:** $0 to build; the live proof plants a 1 GiB disk for about a day (about $0.09 a month while it exists) and its
backup copy (under one cent).

Once the scan has found a disk attached to nothing, or a backup copy past its retention, Kaval can remove it, in
steps, and only with a person's approval at each risky step. The decision is
[ADR-0063](../adr/0063-waste-is-removed-in-steps-by-a-separate-worker-and-only-with-approval.md); the finding is
[Lab 59](lab-59-the-waste-scan.md).

## Parts

- [x] **Part 0 — The byte count.** The order and result hand-over fit: the one waste copy was turned into a loop over
  two read parameters plus one write, and the packed script went from 15,528 to 15,736 of the 15,800 limit (EC2's own
  hard limit is 16,384). That leaves almost nothing, so **the next addition to the script must first move something
  out**; the size test fails loudly if it grows. Tests: `pytest scripts/dev/test_cost_pull.py` (35, including the
  negative cases: no order writes nothing, an empty order writes nothing, no cluster means no read and no write, a
  missing result never overwrites the last good copy, the write uses the server's own login and one parameter name)
- [ ] **Part 1 — The worker's rules, without AWS.** Pure function, made-up answers: every refusal (not Kaval's,
  Terraform's, another environment's, attached, attached during the wait, backup unfinished, grace not over,
  retention not over, newest copy of a live disk) and the one case that acts
- [ ] **Part 2 — The worker's login.** The role file holds only the allowed verbs, every one conditioned on the
  Kaval label; a test fails on any other verb (stop, terminate, detach, release, a wildcard)
- [ ] **Part 3 — The planner.** Files step-one and step-two proposals from an incident and the scan's own facts,
  by code, never by the model; refuses a person-only cause; the model is not asked for these two causes
- [ ] **Part 4 — The executor and the policy.** The two handlers write one order and say "handed over"; policy says
  `ask` for both and `never` for anything else; the new blast radius `resource` (migration)
- [ ] **Part 5 — The road there and back.** Orders and results through the server's timer, the cost reader's pod
  and a signal; alarms for failing and for an order with no result (promtool tests)
- [ ] **Part 6 — Staging proof.** Plant a disk; scan twice; the incident; approve step one (label + backup); wait the
  grace (1 hour in staging); approve step two; the disk is gone; restore a disk from the backup and see the data
- [ ] **Part 7 — Negatives, live.** An attached disk, another project's disk and a Terraform-managed disk are each
  refused by the worker even when an order names them; a disk attached during the wait never reaches step two
- [ ] **Part 8 — Production** at its bring-up (`KAV-98`), plan shown first

## What this does not do

It does not remove servers, fixed addresses or unlabelled things (a person does; `KAV-95` is two kinds only). It does
not track money saved (its own roadmap line). It never deletes without both approvals.
