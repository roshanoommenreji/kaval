# ADR-0062 — Waste is found daily by a read-only scan and reported to a person

- **Status:** Accepted. Built and tested on the laptop 2026-10-18; the staging proof and production are recorded in
  [Lab 59](../labs/lab-59-the-waste-scan.md) as they happen
- **Date:** 2026-10-18
- **Deciders:** Roshan
- **Story:** `KAV-94` (Phase 6) · builds on [ADR-0060](0060-the-cost-reader-the-server-asks-aws-and-the-cluster-never-holds-the-login.md)
  (the way a fact gets from AWS to the cluster without an AWS login in it) and
  [ADR-0061](0061-every-resource-carries-three-tags-and-the-bill-is-split-by-them.md) (the labels this reads) ·
  feeds `KAV-95` (removal)

## Context

The cost reader (`KAV-92`) finds money that moved. It cannot find money that never moves: a disk nobody uses costs
the same every day, so no spike rule will ever see it. Phase 6's exit gate is "the agent finds real waste in your own
account". A read-only look at the live account on 2026-10-18 showed what there is to find, and what is not there:

1. **Nothing is wasted today** (no unattached disk, no unattached address, the oldest backup copy is 12 days old), so
   the rules cannot be tuned on real examples; they are tested on made-up answers and proven live by planting one
   harmless disk.
2. **Staging's real processor use averages 7 to 12 percent when it is up**, because Prometheus, the services and
   the database all run. "Low CPU means idle" would flag a busy server, so idleness is judged by *how long a server
   has been running*, not how busy it looks (staging already parks itself after 4 idle hours; a staging server up
   for 12 hours means that safety failed or someone forgot).
3. **The server's start-up script has no room for a second script** (15,248 of 15,800 bytes after compression; EC2's
   hard limit is 16,384), so the scan cannot be a second program on the server.
4. **Nothing in the cluster may hold an AWS login** (`ADR-0060`), and the scan must keep working while production is
   parked at night.

## What real companies do

| Pattern | Verdict here |
|---|---|
| A paid service that scans for waste (AWS Trusted Advisor, Compute Optimizer, third-party FinOps tools) | Rejected for now. The useful checks need a Business-level support plan or a subscription, and Kaval's point is to build the finding and the approval path itself. Cost Anomaly Detection (decided in ADR-0059) stays as a second opinion on *changes* |
| A scheduled function that looks and reports, read-only, with the result visible where people already look | **Chosen.** Free at this size, and the same shape the stop jobs already use |
| AWS Config rules (one per kind of waste) | Rejected, as in ADR-0061: billed per recorded item and evaluation, for what four read calls give for nothing |
| A rule inside the cluster that calls AWS with its own login | Rejected: it puts an AWS login in the cluster (`ADR-0060`) |

## Decisions

1. **A small function on its own daily schedule (03:15 UTC) looks, and writes one parameter.**
   `infra/modules/waste-scan`: four Describe calls (servers, disks, addresses, snapshots; none can be narrowed by tag
   in IAM) and the right to read and write one parameter by exact name, `/kaval/<env>/waste-scan`. Nothing that
   stops, deletes, detaches, snapshots or labels; a test fails if a changing verb ever appears in the file. It is
   separate from the executor's future role (`KAV-95`): looking and acting never share a login. It runs on its own
   schedule rather than on the server so a parked production still has an up-to-date answer waiting.
2. **The server's existing timer copies the parameter into a ConfigMap, and the existing cost reader pod reads it.**
   Six lines in the cost-pull script (a free read, on every 3-hour tick, with the node's own role allowed to read
   that one parameter), a second ConfigMap the pod may `get` by name (the role still has the one verb), and
   `--waste-handoff` on the same program. No new pod, so the memory budget is unchanged in kind (measured on
   staging, [Lab 59](../labs/lab-59-the-waste-scan.md)). The script grew 280 bytes packed (15,528 of 15,800).
3. **Five kinds of finding, each a signal the correlator already knows how to turn into an incident.** All are written
   as source `aws_ec2`:

   | Kind | Rule (defaults; edit the constants at the top of `waste_scan.py` to change) | Incident cause · severity |
   |---|---|---|
   | `volume_unattached` | attached to nothing on **two scans in a row** | `idle_volume` · low (its older path, unchanged) |
   | `address_unattached` | same rule; costs $3.65 a month unattached | `idle_address` · low |
   | `snapshot_past_retention` | pre-stop copies older than 30 days; hand-made older than 90; unlabelled older than 30. **Never the newest copy of a disk.** Lifecycle-managed and image-backing copies skipped | `snapshot_past_retention` · low |
   | `server_overstay` | a staging or dev server running more than 12 hours. **Production is never flagged** | `server_overstay` · medium (it bills by the hour) |
   | `resource_unlabelled` | a Kaval resource missing `Project`, `Env` or `ManagedBy`, or holding a value outside the list (the ADR-0061 rules, copied into the function and kept equal by a test) | `resource_unlabelled` · low |

   "Two scans in a row" is the guard against flagging a disk that is mid-move or a restore drill in progress: the
   function keeps what it saw last time in the same parameter. Each scan covers its own environment (by the `Env`
   label); unlabelled resources and unlabelled copies are covered by every environment's scan, since no
   environment owns them. `stock-trader`'s resources are counted and never reported.
4. **Waste findings are for a person until removal exists.** *(Updated by [ADR-0063](0063-waste-is-removed-in-steps-by-a-separate-worker-and-only-with-approval.md): the loose disk and the old backup copy now have a removal path, with a person approving each step; the other three stay for a person.)* Four of the five are added to the list of causes with no
   automated fix (`kaval_shared.health.WASTE_SIGNALS`, the same list in `policy/policy.rego`), so the diagnosis model
   is not asked about them, no proposal can be filed for one, and the policy says `never` to any action attached to
   one. `KAV-95` takes the kinds it can safely act on out of that list on purpose, in its own ADR. A disk attached to
   nothing keeps its older path, which the model explains but cannot fix, and which the golden evals measure.
5. **The ConfigMap is untrusted input.** It is the one place a person or a bug could put text in front of a model, so
   the reader rebuilds every finding from a whitelist: a known kind, a target made from an AWS id that must match the
   finding's own id, only the named fields, each checked against a fixed shape (id patterns, whole numbers, a
   handful of fixed words, a two-decimal money string). Anything else is dropped and counted, never stored. The
   function in turn writes only ids, sizes, dates and fixed words, never a tag value or AWS's error text.
6. **Two alarms, like the cost reader's.** `KavalWasteScanFailing` (the scan or its hand-over failed and still has
   four hours later) and `KavalWasteScanStale` (no good scan for 36 hours). "The pod is gone" is the cost reader's
   existing `KavalCostReaderMissing`, because it is the same pod. A scan that has not run yet (a new environment's
   first day) is waiting, not failing. A person is told straight from the alert, as for the other health alarms.
7. **Switched on in two steps, because the image must come first.** The chart's `costReader.wasteScan.enabled` is off
   by default: an older collector image does not know the extra argument and would not start. Staging turns it on in
   a second change after the build containing the reader is pinned (the same order as `KAV-92`). Production at its
   bring-up (`KAV-98`).
8. **An incident stays open while the waste is, and closes by itself.** The scan restates every finding daily; the
   correlator closes an AWS incident after 48 quiet hours, so removing the waste closes its incident two days later
   with nothing else to do.

## Consequences

- **Cost: $0 a month.** Lambda and the schedule are free at 30 runs a month, Describe calls are free, a standard
  parameter is free. Proving it live plants a 1 GiB disk for a day or two: about $0.09 a month while it exists.
- **Capacity:** one more ConfigMap read every 5 minutes by the pod that already exists; measured on staging.
- **A finding the function cannot fit is counted, not hidden.** A standard parameter holds 4 KB: at most 25 findings
  are kept (most important kinds first) and the rest are counted in `truncated`, which the reader prints.
- **The daily scan overlaps `make tag-scan` for four resource types** (servers, disks, snapshots, addresses). The
  command stays: it also reads every other resource type through the tagging service, and it is the on-demand
  check before a production bring-up.
- **If the function stops, the alarm is on the cluster side**, so a parked production is quiet until it starts and
  then has 36 hours of grace. A failing function does not alarm on its own (an AWS alarm per function would be a
  recurring cost for what the stale rule already says).
- **Not decided here:** removal, a grace period, and what is safe to delete (decided in
  [ADR-0063](0063-waste-is-removed-in-steps-by-a-separate-worker-and-only-with-approval.md), `KAV-95`); the Cost and Usage Report (line items), which the bill by service did not make necessary.
