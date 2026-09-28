# ADR-0009 — Gateway API conventions

- **Status:** Accepted
- **Date:** 2026-09-27
- **Deciders:** Roshan
- **Jira:** `KAV-23`

## Context

`KAV-23` gives the gateway its first real endpoints. The phone app (Phase 5) will be built against
them, and whatever shape they take now is the shape every later endpoint copies. Changing a
published API later means changing the app too, so the conventions are settled here, before the
first client exists.

## Decision

| Convention | Choice | Why |
|---|---|---|
| Versioning | Everything under **`/v1`**. Health probes (`/healthz`) stay outside | A breaking change becomes `/v2` alongside `/v1`, so an app already installed on a phone keeps working. Probes are for Kubernetes, not a client contract |
| Writes | **None until the approval flow.** Every request runs in a Postgres `READ ONLY` transaction | The audit trail can't be changed by a gateway bug. The database refuses the write; code review isn't the only guard. Per-service database roles (ADR-0008, Phase 4) add the same limit at the credential level |
| Lists | Newest first, **cursor-paged**: `?limit=` (1–200, default 50) and an opaque `?cursor=` from the previous page's `next_cursor` | Signals arrive continuously. With `?page=N`, rows that land between two requests shift everything, so some get skipped or shown twice. A cursor means "older than this exact row" (its timestamp, with the id to break ties), so each row appears exactly once |
| Money | Decimal amounts are **strings** in JSON (`"cost_usd": "0.000120"`) | A JSON number is a float in most clients, and floats can't represent most cents exactly |
| Errors | `404` for an unknown id, `400` for a malformed cursor, `422` for an out-of-range parameter. Body: `{"detail": "..."}` | FastAPI's own shape, so the app can handle every error one way. Never a `500` for bad input |
| Response shape | Pydantic response models (`kaval_gateway/schemas.py`), kept separate from the ORM models | A new column in a table doesn't reach the API by accident, and the API can change without a migration |
| Documentation | OpenAPI generated from the code: `/docs` (interactive) and `/openapi.json` | The contract can't drift from the implementation, and the Phase 5 app can generate its client types from it |
| Authentication | **None in Phases 1–4.** The gateway binds to `127.0.0.1` on the server and is reached through an SSH tunnel | There is no client yet to authenticate. Cognito JWT verification arrives with the app in Phase 5, before the gateway is ever reachable from outside (via Cloudflare Tunnel) |

## Alternatives rejected

- **Offset paging (`?page=N`).** Simpler to write, but wrong for a table that grows while you read
  it. That's the exact failure above.
- **No version prefix, add one when needed.** Adding a prefix later is itself a breaking change
  for every client already out there.
- **GraphQL.** It suits clients composing many resource types freely. This API has one client and
  a handful of screens. REST plus OpenAPI is simpler and has better tooling at this size.
- **Authentication now.** It would mean building Cognito into a service with no users, on an API
  nobody outside the server can reach yet. The rule is written down instead: the gateway doesn't
  go public before JWT verification (Phase 5).

## Consequences

- Every later endpoint follows these rules. A new list endpoint reuses the `_page` helper in
  `kaval_gateway/api.py` rather than inventing its own paging.
- The first write endpoint (a `decision` from approve/deny) needs a deliberate, separate
  read-write session. That makes the change visible in review.
- `/v1/incidents` is empty until correlation lands in Phase 2. That's expected: the endpoint and
  its contract exist first. *(Update 2026-09-28: correlation landed in `KAV-39`; `make correlate`
  fills it. See [ADR-0014](0014-signal-correlation-and-incident-fingerprints.md).)*
