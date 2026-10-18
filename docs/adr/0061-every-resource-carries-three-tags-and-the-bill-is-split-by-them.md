# ADR-0061 — Every Kaval resource carries three tags, and the bill is split by them

- **Status:** Accepted. Built and tested on the laptop 2026-10-18; the staging proof, the billing switch-on and
  production are recorded in [Lab 58](../labs/lab-58-tags-and-the-showback-report.md) as they happen
- **Date:** 2026-10-18
- **Deciders:** Roshan
- **Story:** `KAV-93` (Phase 6) · builds on [ADR-0059](0059-always-on-cost-model-and-measuring-usage-before-credits.md)
  (attribution before waste) · feeds `KAV-94` (waste detection reads "age plus no label")

## Context

Waste detection (`KAV-94`) and cost proposals (`KAV-95`) both need to answer "whose is this, and is it safe to
remove?". The answer is a label on the resource. A read-only look at the real account on 2026-10-18 found:

1. **The labels mostly exist** (`Project`, `Env`, `ManagedBy`, from Terraform's provider-wide default tags), but
   **12 of Kaval's 103 resources were missing something**: the staging server carried only `Name` and `Project`,
   its disk carried nothing at all, and 10 "before-stop" snapshots made by Kaval's own safety jobs lacked `Env`
   and `ManagedBy`. Terraform's default tags do not reach what an Auto Scaling group launches, and the two jobs
   built their snapshot labels by hand and forgot two of three.
2. **The bill can only be split by `Project`.** Only that key is activated as a cost allocation tag in AWS
   Billing; `Env` and `ManagedBy` are off, so "cost per environment" was impossible however well the resources
   were labelled.
3. **Labels are safety, not decoration.** The hard-stop and idle-stop jobs find servers by `Project=kaval`. A
   server that loses that label is not stopped when the budget is hit.
4. **The account is shared** with another project, `stock-trader` (37 resources). Whatever checks labels must not
   treat its resources as Kaval's problem, and must never act on them.

## What real companies do

| Pattern | Verdict here |
|---|---|
| Default tags in the infrastructure code, plus a check in the pipeline before anything is built | **Chosen (layer 1).** Free, runs on every pull request |
| A scan of the live account for what the code did not label, with a report | **Chosen (layer 2).** Finds what layer 1 cannot see: what a service launches by itself, what a person made by hand |
| A rule in the cloud that flags or blocks unlabelled resources (AWS Config rules; organisation-wide tag policies and service control policies) | Rejected for now. Config bills per recorded item and per rule evaluation, so it is a recurring cost for a problem a read-only scan already finds; tag policies and service control policies need an AWS Organization, which a single account does not have. Revisit if the account ever joins one |
| More keys (`Owner`, `Component`, `CostCenter`) | Rejected for now. One person owns everything, so `Owner` would be a constant; the server's `Name` already says which part it is. Cheap to add later because the check reads one list |

## Decisions

1. **Three required keys, with fixed values.** `Project` = `kaval`; `Env` = one of `prod`, `staging`, `dev`,
   `state`; `ManagedBy` = one of `terraform` (Terraform made it and would make it again), `kaval-lambda` (one of
   Kaval's own jobs made it, so deleting it is final) or `manual` (a person made it by hand). The key stays `Env`,
   not `Environment`: renaming would re-label every resource for nothing. `ManagedBy` is the one that matters most
   for `KAV-95`: it says whether removing a resource is final.
2. **Two layers of enforcement.**
   - *Before building:* `scripts/ops/tag_policy.py`, run as a test on every pull request. It reads each
     environment's default tags (the `Env` must match the environment), the server module (the instance, its disk
     and the group must all be given the three keys) and both stop jobs (their snapshot labels must name all
     three). It is tested on broken inputs too, so a rule that stopped working would fail instead of passing. It
     cannot see the final labels on real resources: CI holds no AWS login, on purpose.
   - *After building:* `make tag-scan` (`scripts/ops/tag_scan.py`), read-only, lists Kaval resources with a missing
     or wrong label. It also asks EC2 directly about servers, disks, snapshots and fixed addresses, because the
     tagging service cannot show a resource that has no tags at all. A resource with no `Project` is treated as
     Kaval's: a forgotten Kaval resource must not escape by having lost its label. Resources with a different
     `Project` are counted and left alone. Exit status 1 when anything is found, so it can be scheduled later.
3. **The gaps found are closed at the source.** The node module writes the three keys on the instance, on its disk
   and on the Auto Scaling group (`propagate_at_launch`); the idle-stop job adds `ManagedBy=kaval-lambda`; the
   hard-stop job adds `ManagedBy=kaval-lambda` and copies `Env` from the disk it snapshots (`unknown` if the disk
   has none, a value the scan reports rather than a guess). The 10 existing snapshots and the staging disk are
   labelled by hand, once, with the owner's go.
4. **The bill is split by the same labels.** `Env` and `ManagedBy` are activated as cost allocation tags, and
   AWS's backfill is requested so past months are split too. (The learn page said tagging is never retroactive;
   that has not been true since AWS added the backfill request, and only resources that were labelled at the
   time can be attributed, which here is everything Terraform made.) `make cost-showback` asks Cost Explorer one
   question ($0.01) and writes `docs/cost/showback-<month>.md`: AWS service by environment, usage before credits,
   with **the share of spend that has no `Env` label** as the headline, because that is the number to drive down.
   Some of it never reaches zero (support, tax, data transfer, the Cost Explorer requests themselves).
5. **`stock-trader` is exempt and untouched.** The scan counts its resources and names the project; nothing here
   reads, labels or changes them.

## Consequences

- Cost: $0.01 per report; activating the labels and the backfill are free. No memory or processor change; the
  server's start-up script is not touched, so its size headroom stays as it is.
- The labels on a **new** server and disk only appear when a server is next launched (staging at its next
  `make staging-up`; production at its next bring-up, together with the pending server-settings change, so no
  extra step).
- Layer 2 (`make tag-scan`) is run by hand. For the four resource types that cost money (servers, disks, snapshots,
  fixed addresses) a daily check with an alert now exists too: the waste scan's `resource_unlabelled` finding
  ([ADR-0062](0062-waste-is-found-daily-by-a-read-only-scan-and-reported-to-a-person.md), `KAV-94`), which carries
  a copy of the rules above kept equal by a test. `make tag-scan` stays for every other resource type and as the
  check before a production bring-up.
- Anyone able to change tags can still hide a resource from the hard stop by removing `Project`; the scan reports
  the missing label, and nothing in Kaval has any write access to tags (the agent has no AWS write access at all).
