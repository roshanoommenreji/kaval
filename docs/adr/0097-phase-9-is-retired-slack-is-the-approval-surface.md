# ADR-0097: Phase 9 (the phone app) is retired; Slack is the approval surface and v1 ends at Phase 8

**Status:** Accepted on 2026-11-15 (Roshan: `Retire the phase`, with the goal `Just close v1 cleanly`). A documentation and tracking change: nothing is built, deployed or changed in AWS.
**Deciders:** Roshan
**Related:** [ADR-0026](0026-slack-chatops-and-deferred-mobile.md) (put the phone app off; its Decision 2 is superseded here), [ADR-0048](0048-approvals-nobody-answers-expiry-and-escalation.md) (reminder, escalation, expiry), [ADR-0073](0073-the-approval-route-gets-a-login-and-the-namespace-gets-network-rules.md) (the login on the approval route), [ADR-0082](0082-text-the-model-wrote-is-escaped-before-it-goes-into-slack.md) and [ADR-0084](0084-the-models-summary-is-labelled-and-the-card-says-nobody-has-approved.md) (the Slack card), [ADR-0009](0009-gateway-api-conventions.md) (the gateway has no way in)
**Story:** `KAV-149`

## Context

The first plan made a custom phone app the only way to approve anything: an Expo app, a Cognito login, push notifications, and a Cloudflare Tunnel so the phone could reach the gateway. On 2026-10-03 ADR-0026 replaced that with Slack ChatOps over an outbound connection and moved the app to the last phase, "deferred, not cancelled", with the question "build it at all?" left open until the phase was picked up.

Six weeks later the question can be answered from what exists, not from a guess:

- **Approving works, and it is the hardened path.** Slack posts a card with Approve and Deny (`KAV-55`). The model's text on it is escaped and labelled as unchecked, and the card always ends with a line the code wrote saying nobody has approved yet (`KAV-114`, `KAV-117`). The direct route has a login (`KAV-115`). A decision is written once and cannot be changed (`KAV-116`). Four real cards were seen in Slack on 2026-11-13.
- **A request nobody answers is handled.** A reminder at 15 minutes, a louder message at 30, expiry at 4 hours, and a late click is refused (ADR-0048).
- **The phone already works.** The Slack app on a phone notifies for the channel and shows the same card and buttons. The old exit gate, "the phone buzzes for a real incident and you approve it from bed", is met by an app Kaval did not have to write.
- **Production is parked most of the time** (about $2 a month parked; it runs for sessions). An app of our own would have nothing to talk to for most of any week.

## What real companies do

1. **ChatOps:** approve in Slack or Teams, on a laptop or a phone. Common, and what Kaval does.
2. **A paging tool:** PagerDuty or Opsgenie, with acknowledge and run-action buttons in their apps. Common where there is an on-call rota; a paid product per user.
3. **A custom phone app:** rare. It is built when the app is the product, not to approve an operations change.

## Decision

**Phase 9 is retired: the phone app is not built, on purpose. Slack is Kaval's approval surface, on a phone too. v1 is Phases 0–8, and Phase 8 (harden and publish) is the last phase.**

1. `ROADMAP.md` keeps the Phase 9 section as a record, marked `[-]` (a new legend entry: retired, not built). Its ten lines are struck through, each with what covers it or why it is not needed, so they no longer count as open work.
2. The idea is kept, uncommitted, as **FS-11** in [future-scope.md](../future-scope.md), with the three things that would have to be decided before anyone builds it.
3. The phone app and the Cloudflare Tunnel are removed from the architecture file, the README, the repo guide, the cost plan and `CLAUDE.md`. The empty `mobile/` folder is deleted. [learn/phase-9-mobile-app.md](../learn/phase-9-mobile-app.md) stays, marked retired: OAuth, PKCE and push are worth reading whether or not this project uses them.
4. The dashboard learns the retired state: it says "retired, not built" instead of showing an empty bar, never picks a retired phase as the current one, and does not count it in "phase N of M".
5. Jira: the Phase 9 epic `KAV-10` (no stories) is closed with a note.

### What covered each old Phase 9 line

| Old line | Now |
|---|---|
| Decide: build mobile at all | Decided here: no |
| Expo scaffold, screens, Cognito login | Not built. Slack's own app is the client, Slack's login is the login |
| Push notifications | The Slack app's notifications for the approvals channel |
| Approve / deny round trip | In Slack since `KAV-55` |
| A second factor on Approve, tokens in the device's secure store | No app, so no token of Kaval's on any device. The phone's lock and the Slack login are what stand in front of the button; see "Accepted" below |
| Same expiry and escalation rules as Slack | There is one surface, so one set of rules (ADR-0048) |
| Distribution through a test track, an update and revocation path | Nothing to distribute. Revoking a person is removing them from the Slack channel |
| Push reliability: what if the push never arrives | The reminder at 15 minutes and the louder message at 30 are the retry; the request expires at 4 hours and nothing is done without a decision |

## Planning standard

- **Whole path:** nothing that runs in a cluster changes, so there is no local, staging or production rollout and no release round.
- **Security:** a gain. No inbound path (no tunnel, no domain, no public hostname), no second login system, no token of Kaval's on a phone. The gateway stays unreachable from outside (ADR-0009, ADR-0026).
- **Failure:** unchanged. If Slack is down, no card is posted and nothing is approved, which is the safe direction; the stand-by is `scripts/ops/approve.py` over Session Manager, behind the route's login.
- **Observability:** unchanged; the notifier's own health alerts ([ADR-0042](0042-watching-the-watcher.md)).
- **Cost:** $0, and the "about $12 a year for a tunnel and a domain, if mobile is built" line leaves the cost plan.
- **Testing:** the dashboard change has tests that failed first; no other code changes.
- **People:** Roshan owns the Slack channel and is the one approver. The channel's membership is the list of who can approve.

## Accepted, and said plainly

- **No fingerprint check on the Approve button.** Anyone holding the unlocked phone, signed in to Slack, can press it. The same is true of an unlocked laptop today. What limits the damage is not the button: the executor checks policy again by itself before acting, removals need two approvals, and nothing is on the `auto` list. If a second person ever approves, or the `ask` list grows to riskier actions, this is the first thing to revisit (Slack's own re-authentication settings before a custom app).
- **A dependency on Slack** for approving at all, accepted since ADR-0026.
- **No incident timeline on a phone.** The card carries the summary; the detail is read on a laptop.

## Rejected

- **Build the app as planned.** About eight to ten sessions, the first inbound path into the system, a login to run, and an app with nothing to reach while production is parked. It would teach Expo and OAuth, which are not what an AI platform role asks about, and it would approve nothing Slack cannot.
- **A small read-only app** (incidents and a timeline; approving stays in Slack). It still needs the way in and the login, for a screen read a few times a month.
- **A new, smaller Phase 9 that proves Slack on the phone.** The proof is one press of a button during a production session, which is a line in a lab, not a phase. If it is done, it is recorded below.
- **Delete the Phase 9 section.** A struck-through record of what was planned and why it was dropped is more honest than a roadmap that never had it.

## Consequences

- The roadmap's phases are 0–8; "weeks 25–28" no longer exist in the plan. The dashboard's totals lose ten open lines, so the percentage rises without any work being done: that is the plan getting smaller, and the dashboard says "retired" where it happened.
- ADR-0026's Decision 2 ("deferred, not cancelled") is superseded. ADR-0009 and ADR-0073 said Cognito would arrive "if Phase 9 is built"; both now point here.
- `mobile` stops being a service name in the Jira script and the repo map.
- Phase 8's exit gate (repo public, video recorded, bullets written) is now the exit gate of v1. *(Amended 2026-11-16: the video moved to future scope, FS-2.)*
- **Left as it is, on purpose:** four comments inside the service code that still say "deferred mobile app" (the gateway's API description, a schema docstring and its package docstring; the executor's `redact.py`). Changing them changes two images, and an image change owes a release round, a staging smoke test and a promotion ([ADR-0081](0081-a-promotion-must-be-a-tagged-release.md)); that is too much for wording. They are corrected with the next gateway and executor release (`KAV-150`).
- `.env.example` loses its three unused phone-app settings (Cognito pool, Cognito client, Expo push token); nothing ever read them.

## Proof so far

- The decision and its sweep: this change (`KAV-149`).
- **2026-11-16 (`KAV-147`), production:** the owner pressed the buttons on real Slack cards. Two **Approves** (11:53 and 11:54 IST) were carried out: the executor handed each removal order to the removal worker and both snapshots are gone from AWS. Four **Denies** (11:59 and 12:11 IST) were recorded and nothing changed. Every decision names its actor, `slack:roshanoommenreji`. The record is [Lab 64](../labs/lab-64-closing-phase-6-on-production.md) Part 5. Slack on a phone was not separately timed, so "from the phone" is still the owner's word, not a recorded fact.
