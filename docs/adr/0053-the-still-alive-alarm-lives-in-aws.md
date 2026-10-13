# ADR-0053 — The "still alive" alarm lives in AWS

- **Status:** Accepted — built and tested (Part 2 of `KAV-83`); the staging proof and the switch-on in
  production are recorded in [Lab 50](../labs/lab-50-closing-phase-5.md). Supersedes
  [ADR-0052](0052-the-outside-still-alive-check.md).
- **Date:** 2026-10-13
- **Deciders:** Roshan
- **Story:** `KAV-83` (Phase 5, slice 7) · umbrella `KAV-76`

## Context

The need is unchanged from ADR-0052: everything Kaval does to notice trouble runs inside the system it
watches, so a lost server is silent (the [lost-server game day](../postmortems/2026-10-12-node.md) lost about
2 min 10 s with nothing able to alert, and would have lost forever had the server not come back). Something
that is not inside the server must expect a regular sign of life and complain when it stops.

ADR-0052 chose an outside dead-man's-switch service. Roshan asked whether a small always-on AWS server running
Prometheus could do it instead. It could not do it better (it costs about $7 to $9 a month, shares the region's
failure points, needs its own watcher, and needs an alerting path Kaval deliberately does not run, ADR-0043),
but the question was really "can it be AWS-only", and **it can, with no server at all**. Roshan chose that.

## What real companies do

| Pattern | One line | Fits here? |
|---|---|---|
| Outside dead-man's switch (healthchecks.io, PagerDuty heartbeat) | The system pings out; the service alarms when pings stop | Built first (ADR-0052); needs an outside company and a stored secret |
| The cloud's own alarm on a custom metric ("no data" alarm; Datadog "no data" monitor) | The system puts a datapoint; an alarm rings when they stop | **Chosen:** no server, no secret, no outside company |
| A second always-on watcher server | Another Prometheus or script on another machine | No: dearer, same failure points, more to look after |

## Decision 1 — a CloudWatch alarm on a heartbeat metric, and on the group's own size

The node puts one datapoint (`Kaval/Heartbeat`, `StillAlive`, dimension `Environment`) every two minutes.
One alarm (`infra/modules/still-alive`) rings, and e-mails through an SNS topic, when for **five five-minute
readings in a row (25 minutes)**:

    IF( FILL(wanted, 1) > 0  AND  FILL(heartbeat, 0) < 1 , 1 , 0 )

where `wanted` is the app-server group's own desired size (`AWS/AutoScaling`, `GroupDesiredCapacity`, which the
node module now turns on; free) and `heartbeat` is the sum of the datapoints.

- **A parked production is silent by itself:** it wants 0 servers, so the rule is 0. There is no cover job, no
  ping from anywhere, and no secret. This replaces ADR-0052's GitHub job, its role and its workflow.
- **"Wants a server and is quiet"** covers a server that died, one that never launched, and a poller that stopped.
- **A missing reading leans toward ringing, never toward quiet.** A missing group size counts as "wants a server"
  (1), a missing heartbeat as "no beats" (0), and a missing series altogether as "breaching". Tried against real,
  empty CloudWatch data before it was written: parked 0, wants-and-silent 1, both missing 1 (Lab 50).
- **25 minutes** rides out the 2 min 10 s self-heal and the 5 to 8 minute bring-up, as in ADR-0052.
- **Out and back:** both the alarm and the return to normal e-mail (`alarm_actions`, `ok_actions`).

## Decision 2 — the host sends the datapoint, and only on clear evidence

A pod cannot reach the node's AWS role (deliberate, ADR-0021), so the poller keeps **no AWS credentials**. A
systemd timer on the node (`/usr/local/sbin/kaval-heartbeat`, every two minutes) reads the poller's health page
(the service every pod already serves on `:9100`, through the Kubernetes API) and puts a datapoint **only if
the poller finished a pass in the last 150 seconds**. The poller's own "last pass finished" time is the evidence;
nothing new is measured. Anything unclear sends nothing: the page unreachable, no finish time, a stale one, one
from the future, a number that is not a number, or another service's line (all tested, and the two limits were
mutation-checked). Nothing is printed on success, so the journal holds only the reasons a beat was missing.

The node's role gains exactly one permission: `cloudwatch:PutMetricData`, with the condition that the namespace
is `Kaval/Heartbeat`. That action has no resource-level permission, so the namespace condition is the fence; the
role still cannot read metrics, change an alarm, or write to any other namespace.

## Decision 3 — what ADR-0052 built is removed, not left to rot

The heartbeat module in the collector, its tests, the chart's `prometheus.heartbeat` value and optional Secret
reference, the node's `kaval-heartbeat` Secret step and SSM parameter, `still_alive.py`, `set-heartbeat.sh`, the
`still-alive` workflow and its GitHub role are all deleted. (The SSM parameter had been applied to staging; the
Terraform plan for this change destroys it.) ADR-0052 stays as the record of the road not taken.

## Decision 4 — what is not built

- **No e-mail subscription in staging.** A new subscription needs a click on a confirmation e-mail, and staging
  is rebuilt often. Its alarm state is read with `aws cloudwatch describe-alarms`; the e-mail path is proven in
  production's roll-out with `aws cloudwatch set-alarm-state` (a test ring and a return to normal).
- **No second alarm for "the heartbeat itself is broken".** If the node's timer stops, the datapoints stop, and
  that is exactly what rings the alarm; a wrong "all fine" is not possible from this side.
- **No Lambda and no composite alarm.** One alarm with one expression is enough, and a composite is $0.50 a month
  more for the same rule.

## Consequences

- **What it does not cover (the price of "AWS only"):** an outage of this region or this AWS account takes the
  alarm down with the server, and CloudWatch or SNS being down means no e-mail. ADR-0052's outside service
  survived those; this does not. Accepted by Roshan, who chose "no outside company".
- **The $48 spending brake** scales the group to 0, which this alarm reads as parked (silent). The budget alerts
  at $42 and $46 and the brake's own message are what tell you that.
- **Cost** (ap-south-1 list prices from the AWS Price List API, 2026-10-13): one custom metric $0.30, the alarm's
  two metrics $0.20 (a standard alarm is $0.10 per metric), about 21,600 `PutMetricData` calls a month at $0.01
  per 1,000 = $0.22, SNS e-mail free at this size, the group metric free: **about $0.72 a month, and probably $0**
  inside CloudWatch's always-free allowance (10 metrics, 10 alarms, 1 million requests; not verified for this
  account). Staging's alarm exists only while staging does.
- **A running server picks up the heartbeat script only when it is replaced:** the script is written at first
  boot (as the nightly dump's is). Staging is replaced once for the proof; production gets it with its next
  bring-up, which is Part 3 anyway.
- **First e-mail needs a click:** AWS sends a confirmation link to the address; until it is clicked, no alert
  arrives. Part 3 checks this with a test ring.
- **Rollback:** delete the module call (the alarm and topic go), or stop the timer
  (`systemctl disable --now kaval-heartbeat.timer`), which makes the alarm ring while the server runs, so remove
  the alarm first.
- **Tested:** the node script against stand-ins (every unclear case sends nothing; two mutations caught), the
  alarm's settings (rule, fills, 5 x 5 minutes, both actions, the namespace fence), Terraform validate and format,
  a read-only run of the expression against real empty CloudWatch data, and the live staging proof in Lab 50
  (datapoints arrive; stopping the poller rings the alarm; a parked staging stays quiet).
