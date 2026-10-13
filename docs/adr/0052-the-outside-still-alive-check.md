# ADR-0052 — The outside "still alive" check

- **Status:** **Superseded by [ADR-0053](0053-the-still-alive-alarm-lives-in-aws.md)** the same day. This
  version (a ping to healthchecks.io, with a GitHub job covering a parked production) was built, tested and
  merged (PR #155), then replaced before it ran anywhere, because Roshan chose "no outside company at all".
  Kept as the record of what was weighed; its code, role, workflow and secret handling are removed.
- **Date:** 2026-10-13
- **Deciders:** Roshan
- **Story:** `KAV-83` (Phase 5, slice 7) · umbrella `KAV-76`

## Context

Everything Kaval does to notice trouble runs inside the system it watches. The lost-server game day
([post-mortem](../postmortems/2026-10-12-node.md)) showed the gap: for about 2 min 10 s while the server was
gone and replaced, nothing could alert, and if it had not come back, nothing would ever have said so. Silence
inside Kaval looks the same as "all quiet". A real operations team answers this with something **outside**
the system that expects a regular sign of life and complains when it stops.

Two facts shape the design:

1. **Production has no public address** (no tunnel, ADR-0004), so an outside service cannot call in to ask.
   The sign of life has to be pushed out.
2. **Production is parked most of the time** ([ADR-0028](0028-prod-app-node-on-demand-and-ceiling-50.md)).
   A parked production sends nothing, so a plain "alarm when the pings stop" would go off every parked day.

## What real companies do

| Pattern | One line | Fits here? |
|---|---|---|
| Dead-man's-switch service (healthchecks.io, Cronitor, Dead Man's Snitch) | The system pings out on a schedule; the service alarms when the pings stop | Yes: needs no inbound address |
| An outside prober calling a public health URL (Pingdom, UptimeRobot, CloudWatch Synthetics) | The service calls in and alarms when the answer stops | No: production has no public address, and adding one (a tunnel, a load balancer) is cost and attack surface for this alone |
| A second monitoring stack in another account or region | Prometheus-on-Prometheus, or a cloud-native monitor | Over-built for one small server; its own failure needs watching too |

Recommendation, chosen by Roshan: **a dead-man's-switch service**, the free tier of
[healthchecks.io](https://healthchecks.io) (20 checks at $0; Kaval uses two).

## Decision 1 — production pings out; silence is the alarm

The Prometheus poller (`kaval_collector.heartbeat`, called from `kaval_collector.prometheus`) sends one small
`https` request after a pass that **fully worked**: Prometheus answered, the database took the writes, the
slowness probe ran. At most one ping per 2 minutes. A pass that failed sends nothing, so a poller that is up
but not working goes quiet and is noticed; that is the point of pinging from the loop and not from a timer.

The check on the service is set to **expect a ping every 10 minutes, with 15 minutes of grace**, so an alarm
comes about 25 minutes after the last sign of life. That rides out the 2 min 10 s self-heal, the 5-to-8-minute
bring-up, and a late scheduled run (Decision 2) without a false alarm, and it is still far inside "someone
should look today". It can be tightened once the real bring-up time is measured in Part 3.

A failing ping never stops Kaval: it is retried every minute, said once in the log, and the loop carries on
(a monitoring service being down must not take the poller down with it).

## Decision 2 — while production is parked, GitHub pings for it, and only when it is clearly parked

`.github/workflows/still-alive.yml` runs every five minutes from GitHub (so it works when AWS is parked or
broken) and runs `scripts/ops/still_alive.py`. The rule has three outcomes:

| What the app-server group says | Meaning | The job |
|---|---|---|
| wants 0 servers and has none | parked | sends the ping for it |
| wants a server, or still has one | running | sends **nothing**: its own poller speaks, or, if the poller is dead, silence is exactly what must show |
| cannot be read, not there, or odd | unknown | sends nothing, exits red |

The unsafe direction is covering for a production that is running but dead, so a ping needs a **positive**
"parked", never merely an absence of "running". Edge cases are in the tests (a group that wants zero but still
has a server, a boolean where a number should be, two groups with the same name, an empty answer).

Its AWS role (`infra/modules/still-alive`, `kaval-still-alive`) can do one thing: describe auto-scaling groups.
That API has no resource-level permission, so the resource is `*`; it is read-only and names servers, not data.
Trust is the same as the other two GitHub roles: this repository, the `main` branch, no wildcard.

The job is **off until** the repository variable `AWS_STILL_ALIVE_ROLE_ARN` is set, which happens only after
the role is applied in Part 3, so merging this work changes nothing in production by itself.

## Decision 3 — the address is a secret and travels like one

Anyone holding the ping address can keep a dead system looking alive, so it is handled like a password:

- **Never pasted to an assistant or into chat** (ADR-0026). `scripts/ops/set-heartbeat.sh <staging|prod>` is
  run by Roshan, reads it from a hidden prompt, passes it to AWS in a temporary file only the owner can read
  (not as an argument: the AWS CLI would *fetch* a value starting `https://`, and arguments show in the process
  list), and for production also stores it as the GitHub secret `PROD_HEARTBEAT_URL`.
- **AWS side:** SSM Parameter Store `/kaval/<env>/heartbeat/url` (SecureString), created by Terraform with a
  `REPLACE_ME` placeholder and `ignore_changes`, the same pattern as the Slack tokens. The node's start-up
  script turns it into the Secret `kaval-heartbeat`, and **always** creates it: a missing, unreadable,
  placeholder or non-`https` value gives an *empty* Secret, never a failed start-up (tested, including the
  failing cases). The Slack lines before it abort the script on a missing value; this one must not.
- **Cluster side:** the chart holds only the Secret's *name* (`prometheus.heartbeat.secretName`), never the
  address; the reference is optional, so a missing Secret cannot stop the poller; a test fails if any
  `heartbeat` setting contains `http`. Only the poller has it.
- **In the code:** `https` only, no user name in it, redirects are not followed, a 5-second timeout, and the
  address is never printed or put in an error message (the failure reasons are a fixed set of words).

## Decision 4 — what is not built

- **No management API key.** The service offers a second, powerful key for creating and changing checks;
  holding one would add a secret that can silence the alarm. The checks are made once by hand, and the code
  only ever uses the ping address.
- **No "fail" signal and no start/stop pairing.** Silence is enough, and a fail signal would need a second
  code path that could itself break.
- **No GitHub issue from this job.** The outside service e-mails Roshan. A red run only means "could not decide
  or could not ping"; if that persists the service goes late and says so.
- **No Slack message from the outside service.** Slack is part of what is being watched; e-mail is not.

## Consequences

- **Cost:** AWS $0 (a role, a parameter). healthchecks.io free tier. A GitHub run every five minutes is free
  on a public repository. The poller adds one small web request every 2 minutes (memory is not measurable
  against the 86 MiB it already uses; the production re-measure in Part 3 records it).
- **Known limits.** GitHub switches scheduled workflows off after 60 days without repository activity and may
  delay a run under load; either makes a *parked* production look quiet and the service raises a **false**
  alarm, never a missed real one. The service keeps only the latest 100 ping records per check, so it is an
  alarm and not a history; the history stays in Kaval's own tables. A new check is silent until its first ping,
  so the first ping is confirmed by hand (Lab 50).
- **A change of address** on a running server needs the node's start-up refresh and a poller restart; the
  runbook says how. A new server picks it up alone.
- **Rollback:** clear the value (set `REPLACE_ME`), or unset `AWS_STILL_ALIVE_ROLE_ARN` to stop only the
  parked cover. Pausing the check on the service stops the e-mails and loses nothing.
- **Tested at every level:** unit (throttle, retry, redaction, refusal of `http`, no redirect, timeout, never
  raises), a real local web server for the request itself, the poller's real loop (no ping after a failed
  pass), the decision table, the chart rendering, the bootstrap step with `aws` failing, and a live proof on
  staging (Lab 50 Part 2). The negative case is the proof: stop the poller and watch the alarm arrive.
