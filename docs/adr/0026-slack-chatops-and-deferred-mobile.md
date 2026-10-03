# ADR-0026: Slack ChatOps via Socket Mode; mobile app deferred to Phase 9

**Status:** Accepted
**Date:** 2026-10-03
**Related:** [ADR-0009](0009-gateway-api-conventions.md) (gateway auth, amended below),
[ADR-0004](0004-environment-strategy-and-promotion.md) (build once, promote the artifact —
unaffected), `KAV-55`

## Context

Phase 4's ROADMAP task list included a Cloudflare Tunnel, whose only real purpose was to let
the Phase 5 mobile app reach the gateway from outside the cluster — ADR-0009 and the gateway's
own API description already said as much: no auth, and no external exposure, until the mobile
app's Cognito integration existed.

A conversation about that plan raised two separate, compounding points:

1. **A custom mobile client is unusual in real operations tooling.** Most companies approve
   incidents through PagerDuty, Opsgenie, or a ChatOps bot in Slack/Teams — not a bespoke app.
   Building one anyway is defensible for a portfolio project, but building it *before* anything
   else can be approved is not; it gates every later phase's demo on finishing Expo, Cognito,
   and push notifications first.
2. **Real companies are reluctant to expose internal systems to the internet at all** — even
   behind a tunnel. A Cloudflare Tunnel avoids the cost of a load balancer, but it still answers
   "how does the public internet reach in?" The more realistic posture is: it doesn't.

Both points point the same direction: don't build a public ingress path for this at all, and
don't make mobile a prerequisite for approving anything.

## Decision 1: Slack ChatOps via Socket Mode replaces the Cloudflare Tunnel plan

Slack's **Socket Mode** lets an app open an *outbound* WebSocket connection to Slack and receive
events — including interactive button clicks — over that connection. Nothing ever connects in.

This is the same direction-of-connection principle already used everywhere else in this
project's AWS footprint: the node's security group has zero inbound rules, and all access is
SSM, never SSH. Socket Mode extends that same posture to the approval surface.

Concretely:

- The gateway process opens an outbound Socket Mode connection to Slack at startup.
- A background poller finds undecided `policy_class = ask` actions and posts them to a Slack
  channel with Approve/Deny buttons, via Slack's outbound Web API (`chat.postMessage`).
- A button click arrives over the same Socket Mode connection and is routed internally to the
  same decision-recording path `POST /v1/actions/{id}/decisions` already uses (`KAV-47`).
- No Cloudflare Tunnel, no domain purchase, no inbound security group rule, no public hostname.

The trade is a dependency on Slack, and on a human being in the workspace to see the message.
For an internal approval surface that is a clearly good trade, and it is the actual industry
pattern — which a from-scratch mobile app is not.

## Decision 2: the mobile app is deferred to Phase 9, not cancelled

Mobile was Phase 5. It becomes **Phase 9** — last, after Chaos+proof, FinOps, EKS, and
Harden+publish, which all move up one number (6→5, 7→6, 8→7, 9→8). Whether to build it at all,
and if so how it would reach the gateway (the Cloudflare Tunnel + domain this ADR removes from
the active plan, or something else decided then), is left open and revisited only if Phase 9 is
actually picked up.

Nothing else is blocked on this decision: Slack ChatOps gives every later phase (chaos
experiments, FinOps cost proposals) a real human-in-the-loop approval path well before Phase 9
would ever be reached.

## Consequences

- ADR-0009's gateway description is amended: the planned external-facing surface is Slack
  ChatOps (`KAV-55`), authenticated by Slack's own signed Socket Mode connection, not Cognito.
  Cognito JWT verification is only needed if Phase 9 is ever built.
- `docs/architecture/architecture.toml`: `mobile` and `tunnel` nodes move to `phase = 9`,
  labelled deferred; the journey view's `j-ask`/`j-deny`/`j-approve` nodes move to `phase = 4`
  (Slack is when a human first really gets asked, not Phase 9).
- `docs/cost/budget-plan.md`: the Cloudflare Tunnel + domain line (~$12/yr) moves from "planned"
  to "not currently needed"; Slack is $0 at this scale.
- `ROADMAP.md`, five `docs/learn/phase-N-*.md` files, `docs/repo-guide.md`, `README.md`,
  `CLAUDE.md`, `docs/contributing.md` renumbered to match.
- `scripts/ops/approve.py` is unaffected and stays as a documented fallback once Slack ChatOps
  ships.

## Rejected alternatives

- **Keep mobile at Phase 5, build Slack ChatOps later too.** Rejected: it's the inversion of
  what actually unblocks the project. The approval surface is the one thing every later phase's
  demo depends on; it shouldn't wait on the hardest, least industry-typical piece.
- **Inbound webhook (Slack Events API) behind the Cloudflare Tunnel, instead of Socket Mode.**
  Rejected per Decision 1 — it reopens exactly the "expose something to the internet" question
  this ADR exists to close, for no benefit Socket Mode doesn't already give.
- **Drop mobile from the roadmap entirely.** Rejected — not asked for, and the OAuth/PKCE/
  Cognito/push-notification content in `docs/learn/phase-9-mobile-app.md` is still worth having
  written if the phase is ever picked up.
