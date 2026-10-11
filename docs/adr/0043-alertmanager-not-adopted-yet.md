# ADR-0043 — Alertmanager is not adopted yet

- **Status:** Accepted — a recorded "not now", with the conditions that would change it
- **Date:** 2026-10-11
- **Deciders:** Roshan
- **Story:** `KAV-78` · the roadmap line "Alertmanager as a signal source: adopt it or record why not"

## Context

Prometheus is the polled half of metrics: Kaval asks it a question every 30 seconds
([ADR-0040](0040-prometheus-as-a-polled-signal-source.md)). Alertmanager is the pushed half: Prometheus
evaluates alert rules and, when one fires, sends it to Alertmanager, which groups, de-duplicates,
silences and routes it (to Slack, a pager, a webhook). Pushing is the usual design in companies with
several teams and many alert sources. The roadmap asked for a decision, not a default.

## Decision

**Do not run Alertmanager now.** Prometheus already holds the list of firing alerts as the series
`ALERTS{alertstate="firing"}`; the collector's Prometheus poller reads it every 30 seconds and writes
each as the `alert_firing` signal the correlator already knows ([ADR-0042](0042-watching-the-watcher.md)).
Grouping is the correlator's job, de-duplication is the poller's renew rule and the gateway's
reminder period, and routing is one place (Slack, from the gateway). Alertmanager would do the same
three things in a second program.

## Alternatives considered

| Alternative | Why not now |
|---|---|
| Run Alertmanager and receive its webhook in the collector | One more Deployment and port on a node where memory is the binding constraint (the tight 4 GB budget in the overview), a webhook endpoint to authenticate, and a second place where "who gets told" is decided. Its value (routing trees, silences, on-call schedules) is for many people and many alert sources; there is one person and one source |
| Alertmanager in front of Slack | Would duplicate `health_alerts`, with the same Slack token in two places |
| Neither: only poll | **This.** |

## What polling gives up, honestly

- **Latency:** an alert reaches a signal within one poll (30 s) after it fires. Alertmanager's own
  group wait is similar. Not a loss at these timescales.
- **Silences:** there is no "mute this alert for two hours" button. For one operator, closing the
  incident or stopping the cause is the equivalent. If the alert is wrong, the rule is fixed.
- **Alerts that fire and clear between two polls** are missed. The rules all have a `for:` wait of one
  to two minutes, so a firing alert lasts longer than a poll by construction.
- **Routing trees:** none, and none needed with one destination.

## Revisit when

- A second alert source needs the same routing (a second cluster, an external service).
- More than one person must be told differently depending on the alert.
- Someone needs silences or on-call schedules.
- Prometheus is moved off the app node, where its memory no longer competes.

Any of these makes Alertmanager a small, well-understood addition: the poller's `ALERTS` read can
stay as a second, independent path (the pushed path then no longer being the only one that tells
a person).

## Consequences

- No new program, port or secret. The roadmap line is closed by this record.
- The alert path is shorter, which is why it can be proven end to end in one staging session.
- `ALERTS` is the interface: any rule that carries the label `kaval_health="true"` and a `service`
  label reaches a person, and any rule without it does not. That label is the allow-list.
