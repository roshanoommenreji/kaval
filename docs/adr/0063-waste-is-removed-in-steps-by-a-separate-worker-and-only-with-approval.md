# ADR-0063 — Waste is removed in steps, by a separate worker, and only with a person's approval

- **Status:** Accepted (plan approved by Roshan 2026-10-19). Built and tested on the laptop 2026-10-19; the staging
  proof and production are recorded in [Lab 60](../labs/lab-60-safe-removal.md) as they happen
- **Date:** 2026-10-19
- **Deciders:** Roshan
- **Story:** `KAV-95` (Phase 6) · builds on [ADR-0062](0062-waste-is-found-daily-by-a-read-only-scan-and-reported-to-a-person.md)
  (the finding), [ADR-0060](0060-the-cost-reader-the-server-asks-aws-and-the-cluster-never-holds-the-login.md)
  (no AWS login in the cluster) and [ADR-0061](0061-every-resource-carries-three-tags-and-the-bill-is-split-by-them.md)
  (the labels that say whose a thing is and whether deleting it is final)

## Context

Kaval can now find a disk attached to nothing and a backup copy past its retention, and tells a person. Nothing can
act on it. This is the first time Kaval would be allowed to **delete something in AWS**, so the rules matter more than
the code:

1. **The cluster holds no AWS login** (ADR-0060), and the agent never has write access (`CLAUDE.md` constraint 3).
2. **A wrong delete cannot be taken back**, so the design must make the wrong delete hard in more than one place.
3. **The account is shared** with another project (`stock-trader`), and some things are Terraform's (it would
   recreate them or complain), so "looks unused" is not enough to act on.
4. **The server's start-up script has about 270 bytes of room** (ADR-0062), so the hand-over to AWS must be small.
5. **There is almost nothing to remove today** (ADR-0062: the real account has no waste), so the value now is the
   safe path itself, proven on a planted disk.

## What real companies do

| Pattern | Verdict here |
|---|---|
| Delete automatically on a schedule (cloud janitor tools, a "sweep" job) | Rejected. One wrong rule deletes live data, and "unused for a week" is a guess |
| Report only (tickets, a dashboard) | Where we are. Safe, saves nothing |
| **Mark it, tell the owner, wait a grace period, take a backup, then delete, with a person approving** (Netflix's Janitor Monkey "mark, notify, wait, then delete"; AWS's own advice to snapshot before deleting) | **Chosen** |
| A model decides what to delete | Rejected. The model may explain; it never picks a thing to delete |

## Decisions

1. **Two approvals per item, with a wait between.** Step one (`backup_and_mark_volume`): label the disk
   `KavalRemoval=<date>` and take a backup copy (`KavalBackupOf=<disk id>`); nothing is deleted. A grace period passes
   (**7 days in production; 1 hour in staging, so the live proof is quick**, a chart value). Step two
   (`delete_idle_volume`): re-check everything, then delete. Each step is its own proposal, shown to a person with
   what will happen, the money saved a month, and how to undo it. Nothing is ever `auto` (no promotion exists, and
   the ADR-0017 rule that nothing is born automatic stands).
2. **The first slice is two kinds of waste.** A disk attached to nothing (`volume_unattached`, whose incident is
   `idle_volume`) and a backup copy past its retention (`snapshot_past_retention`, delete after approval, with no
   backup of a backup). Servers stay for a person (they sit in a scaling group and have their own parking job),
   fixed addresses stay for a person (releasing one changes the IP), and unlabelled things stay for a person
   (someone has to say whose they are). `snapshot_past_retention` therefore leaves the people-only list in
   `kaval_shared.health.WASTE_SIGNALS` and `policy/policy.rego`, on purpose, in this ADR (ADR-0062 decision 4 said
   this story would do it); the other three stay.
3. **The proposal is made by code, not by the model.** A small planner in the agent's existing diagnosis loop reads
   the incident and the scan's own facts and files the proposal (`model` = `rule:removal`, no tokens, no cost). The
   model is not asked about these two causes, so a confused or manipulated model has nothing to say about what gets
   deleted. The golden evals are unchanged (they call the model directly).
4. **A separate worker with its own narrow login does the AWS part.** `infra/modules/waste-removal`: a function
   with a role that can create a backup copy, label it, and delete one disk or one backup copy, and **only** where
   the thing is labelled `Project=kaval`, and **never** where `ManagedBy=terraform`. It cannot stop, terminate,
   detach, release or change anything else. Looking (the scan's role) and acting (this role) never share a login. A
   test fails if the role file grows a verb outside the list.
5. **The worker re-checks the world itself and refuses on any doubt.** An order names one thing and one step. Before
   acting the worker reads AWS again and requires: Kaval's label; the environment matches; not Terraform's; for a
   disk, still attached to nothing, and (for the delete step) marked at least the grace period ago with a **finished**
   backup copy of this disk; for a backup copy, past retention. So a forged or stale order can only remove
   something that already passes every rule, and "approved last week" never beats "attached since".
6. **Orders and results travel by the same road as the scan, reversed, with no AWS login in the cluster.** The
   executor's handler writes one order into one ConfigMap (a new, narrow right: create or patch that one name);
   the server's existing timer copies it into one parameter (`.../removal-orders`) with the server's own role; a
   parameter-change rule wakes the worker, which acts and writes `.../removal-result` (fixed words and ids only,
   never AWS's error text); the same timer copies the result into the cluster, where the cost reader's pod turns
   it into a signal. The executor records the handover honestly ("order handed over; the result arrives with the
   next copy"), not "done". The order and result are small (one order at a time, so they fit the script's 270 bytes
   once the existing waste copy is made to handle both); the byte count is measured before anything else is built,
   and if it does not fit, an alternative is brought back to Roshan.
7. **State lives in AWS labels, not in Kaval's database.** `KavalRemoval` and `KavalBackupOf` are on the
   resources, and the scan reports them (`pending_removal_since`, `backup_snapshot_id`, `backup_state`), so a
   rebuilt cluster loses nothing and the planner files step two only when AWS says step one finished and the grace
   has passed. If anyone attaches the disk meanwhile, the scan stops reporting it as loose and the removal simply
   never reaches step two.
8. **A new blast radius, `resource`.** The existing ones (`pod` … `account`) have no word for one disk; `account` is
   refused by the policy and `node` would be a lie. A migration adds the value (it only adds). The policy treats it
   as `ask`, like any non-`account` radius.
9. **The removal's own backup copies expire and are cleaned up by the same rule.** A copy kept for 30 days, then
   found by the scan as past retention. The scan's "never flag the newest copy of a disk" rule would protect a
   removed disk's last copy forever, so a copy labelled `KavalBackupOf` whose disk no longer exists is eligible after
   30 days (a person still approves its deletion).
10. **Loud when it fails, off with one switch.** A refused or failed order becomes a `removal_failed` signal and an
    incident for a person (people-only), told straight to Slack like the other waste findings. One alarm covers what
    no signal can: `KavalRemovalOrderUnanswered` (the oldest order has no answer after 6 hours). The planner and the
    executor's hand-over are off unless `removal.enabled` is set in the values (a chart refuses to render it without
    the cost reader and the waste scan being read), and the worker refuses everything if its module's `enabled` is
    false, so the safe state is the default. Staging first; production at its bring-up (`KAV-98`).

## Consequences

- **Cost:** $0 to build and run (Lambda, the rule and parameters are free at this size). Backup copies cost about
  $0.05 per GB a month for 30 days. The saving today is about zero because there is no waste; it is measured per
  removal for the later "savings tracked" line.
- **Capacity:** no new pod. The executor and the agent do slightly more. The server's script grows by a small
  amount that is measured against the 15,800 limit before the rest is built.
- **Latency:** the server's timer runs every 3 hours, so an order can wait up to 3 hours for its result. It is
  fine for a 7-day process; the live proof starts the timer by hand.
- **What the worker cannot protect against:** a person approving the wrong thing. The proposal shows the id, size,
  age and what it costs, and the second step shows the backup copy that makes it undoable.
- **The executor gains one right in its own namespace.** ADR-0021 gave it none; it may now `get` and `patch` ONE
  ConfigMap by name (the orders). The chart's test proves it has no other right there.
- **Two checks the model cannot reach.** The executor only carries out a removal for a proposal whose `model` is
  `rule:removal`, and the approval message shows its preview only for such a proposal, so text a model put in an
  action is never shown or acted on as if Kaval had written it.
- **The server's start-up script is now full:** 15,736 of the 15,800 bytes. The next addition must first move
  something out; the size test fails before EC2 does (its own limit is 16,384).
- **Found by the end-to-end test:** after a disk was deleted, the last scan's fact was still fresh for a day, so the
  planner could have asked to delete it again. A step the worker has answered `ok` for is now never asked again,
  and a question still waiting for an answer is never asked a second time.
- **Found by the live proof (2026-10-19): the planner could not read approvals.** It runs inside the diagnosis
  worker, so as the agent's database login, which had no right to read `decision` or `execution`; it failed every
  pass with "permission denied". The agent now gets **read-only** access to those two tables (`db-roles.sql`) and
  still cannot write either: approving stays with the gateway, executing with the executor. A role test runs the
  planner's own lookup as that login and proves the writes are refused. Precedent: `outcome` (found live 2026-10-15).
- **A residual risk, accepted:** a disk attached, changed and detached again between two daily scans during the wait
  could lose those changes (the backup is from the moment it was marked). The seven-day grace and the scan's daily
  look make it unlikely; the backup copy is kept 30 days.
- **Not decided here:** removing servers or addresses; tracking money saved (its own roadmap line); a permission
  boundary for the executor's Kubernetes role (unchanged).
