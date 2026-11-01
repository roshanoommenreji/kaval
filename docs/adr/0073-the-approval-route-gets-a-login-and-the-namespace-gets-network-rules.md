# ADR-0073 — The approval route gets a login, and the namespace gets network rules

- **Status:** Accepted (plan approved by Roshan 2026-11-02). Part 1 (the login, #298) is merged; part 2 (the token's home) is built; part 3 (the network rules) is to do, see "Delivery"
- **Date:** 2026-11-02
- **Deciders:** Roshan
- **Story:** `KAV-115` (found by `KAV-112`, [ADR-0072](0072-a-written-threat-model-and-a-prompt-injection-set.md), threat `T-APPROVE-1`) · builds on [ADR-0026](0026-slack-chatops-and-deferred-mobile.md) (Slack's path does not use this route), [ADR-0048](0048-approvals-nobody-answers-expiry-and-escalation.md) (an approval lasts 60 minutes) and [ADR-0027](0027-automatic-secret-and-role-recreation.md) (secrets rebuilt from Parameter Store on every boot)

## Context

`POST /v1/actions/{id}/decisions` records a human's approval. It asked nobody who was calling, took the actor's name as free text, and the chart used by staging and production has no network rules, so any pod in the cluster could reach it. That includes the demo workloads, which are where untrusted text enters. A pod that was influenced could approve a proposal for itself, and "a human approves" is the sentence the whole safety case rests on.

What an approval can do is bounded (a `never` action cannot be approved, the executor re-checks, three fixes exist, aimed at the incident's own workload, an approval is good for 60 minutes), which is why this was filed as the largest gap rather than an emergency. It is still the gap that makes the other layers matter.

## Decision

Two independent controls, so that if one is wrong the other still holds, and one record of who decided.

1. **A bearer token on the route** (`kaval_gateway.approver_auth`). The call must carry `Authorization: Bearer <token>`. The token is `APPROVER_TOKEN`, from a Kubernetes Secret named `kaval-approver`. Rules, each a test:
   - **Closed unless configured.** No token, or one shorter than 24 characters, and every call is refused with 503. A forgotten setting never means an open door.
   - **Constant-time compare** (`hmac.compare_digest`).
   - **Checked first**, before the database session or the body is read, so a caller without the token costs a comparison. A bad body with no token is a 401, not a 422 that explains the shape.
   - **Never written down**: not in a log line, a reply, or a metric label. A refusal is logged as one of three fixed words (`switched_off`, `missing`, `wrong`) plus the caller's address, and counted in `kaval_approval_refused_total{outcome}`.
   - A test fails if any other write route is added without it.
2. **The actor is recorded as `api:<name>`.** The name is still the caller's word, but it can no longer pose as `slack:<name>`, which only Slack's path writes.
3. **Network rules in the chart** (own pull request, `networkPolicy.enabled`, on for staging and production): ingress to the namespace is closed by default and opened only for what is needed. This also closes the *reads*, which carry no token.
4. **The token's home** (own pull request, `infra/modules/approver-token`): generated once by Terraform, kept in Parameter Store as a SecureString at `/kaval/<env>/approver/token`, written into the cluster's Secret by the node's bootstrap on every boot, the same path the database passwords and Slack tokens already take. Nobody types it into `kubectl`, and the node can rebuild it. **Unlike the Slack and database Secrets, a failure to read it does not stop the bootstrap**: a gateway with no token is shut, the safe state, and a login Secret is not worth taking the cluster's start-up down. The value is also in Terraform's state, the same position as the database passwords (state is a private, versioned, encrypted bucket, ADR-0058).

Slack's buttons are not affected: that surface connects outward with its own tokens and calls `record_decision` directly.

## What real companies do, and why this one

1. **Authenticate the write and restrict the network** (chosen): the usual pairing. Authentication alone fails if a token leaks; network rules alone fail if one pod in the allowed set is compromised. Each covers the other's miss.
2. **A service mesh with mutual TLS and identity per workload** (Istio, Linkerd): the strongest answer and the right one for dozens of services. Memory on a 4 GB node, a second control plane to keep patched, and a learning curve out of proportion to one route.
3. **OIDC login (Cognito, GitHub, Google) with a JWT per person:** right once there is a mobile app or more than one approver; the plan already says Cognito arrives only if Phase 9 is built. One person with a terminal and Slack does not need it. The `api:` prefix keeps the record honest until then.
4. **Remove the route and approve only through Slack:** simplest of all, and rejected: `make approve` is the fallback when Slack is down, and the game days use it.

## Consequences

- **Between parts 1 and 4 an environment has no token**, so its route answers 503 and `make approve` / `make chaos-approve` cannot work there. Safe, and Slack still works. Parts 1 and 4 are therefore promoted to production together.
- **A shared secret has the usual weakness**: whoever can read the Secret can approve. On the cluster that is anyone with `kubectl` on the node, which is the owner. None of the Roles the chart creates grants `secrets` (checked by reading `deploy/charts/kaval/templates`), so no Kaval service account can read it, and it is handed only to the gateway's own container as an environment variable.
- **Rotation** is replacing the Parameter Store value and letting the next boot (or the six-hour timer) rewrite the Secret, then a gateway restart. Written in the runbook.
- **The reads stay open** to anything that can reach the gateway until part 3 lands; the test that every write route needs the login says so in its docstring.
- **Cost $0** for parts 1 and 3; part 4 adds one Parameter Store SecureString per environment (standard tier, free).

## Delivery

Three pull requests, each through staging before production: the login (this one), the token's home, the network rules. The live proof is one staging session (about $0.50): a pod in the demo namespace cannot reach the gateway, a call without the token is refused, one with it works, the smoke test and the health checks still pass.
