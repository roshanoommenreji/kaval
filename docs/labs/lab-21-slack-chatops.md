# Lab 21 — Slack ChatOps, outbound only

**Phase:** 4 · **Time:** ~30 min for the code, plus a one-time Slack App setup you do yourself
**Cost:** $0 (Slack's free tier; no new AWS resource)

Phase 4's exit gate only needed the node to rebuild and redeploy itself. It never needed a way
for a human to approve anything from outside the cluster — `scripts/ops/approve.py`, run over
the same SSH/SSM tunnel used for everything else, has been enough so far. This lab replaces
that stopgap with a real approval surface that still never opens an inbound port: Slack's
**Socket Mode**. The design and the reasoning for deferring mobile instead are in
[ADR-0026](../adr/0026-slack-chatops-and-deferred-mobile.md).

This lab has two parts. **Part 1** is done and verified in this repo already — the code,
migration, and tests. **Part 2** is yours to do: nothing here can create a Slack App on your
behalf, so the live, end-to-end verification waits on that.

---

## Part 1 — what's built (reproducible from zero)

### Step 1 — the migration

```bash
make migrate
```

Adds `action.slack_notified_at` (nullable timestamp) — set once a Slack message has actually
been posted for that action, so the notifier poller can ask "which `ask`-class actions have I
not told anyone about yet" directly in SQL (`kaval_gateway.slack_chatops.pending_actions`).

### Step 2 — the one shared write path

`kaval_gateway/decisions.py`'s `record_decision()` is the only place a human's approve/deny
ever becomes a `decision` row. `api.decide()` (the HTTP endpoint, `KAV-47`) and
`slack_chatops.handle_block_actions()` (a Slack button click, this lab) both call it — there is
exactly one set of checks (`never` refused, no double-decision), not two copies that could
quietly drift apart.

### Step 3 — the gateway's outbound connection

`kaval_gateway/slack_chatops.py` does three things, all through `slack_sdk`:

1. Opens an outbound Socket Mode WebSocket to Slack at startup (`start()`, wired into
   `main.py`'s FastAPI lifespan). This is a no-op — exactly today's behaviour — unless
   `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` and `SLACK_CHANNEL_ID` are all set. They aren't, in any
   environment yet, including CI.
2. A background thread polls every `SLACK_POLL_SECONDS` (default 30) for pending `ask`-class
   actions and posts each one once, with Approve/Deny buttons (`notify_pass`).
3. A button click arrives over the same connection and calls `record_decision()`
   (`handle_block_actions`).

No inbound port, no Cloudflare Tunnel, no domain — the gateway's security posture is otherwise
unchanged from `KAV-23`/`ADR-0009`: still bound to `127.0.0.1` for everything else.

### Step 4 — run it

```bash
make lint    # ruff + mypy, clean
make test    # new: services/gateway/tests/test_slack_chatops.py
```

The new tests use real `WebClient`/`BaseSocketModeClient` subclasses with their network calls
overridden to record arguments — never a mock of the whole module — so the actual query
(`pending_actions`) and decision logic (`record_decision`) run for real against the test
database. They verify: a pending action is found and a decided/already-notified/non-`ask` one
isn't; a notify pass posts once per action and marks it, and a second pass finds nothing left;
an Approve click records a `decision` row with `actor = "slack:<username>"`; a stale click on
an already-decided action is ignored, not an error; `start()` is a genuine no-op without the
three env vars.

### Step 5 — the Helm chart

```bash
helm lint deploy/charts/kaval
helm template kaval deploy/charts/kaval --set gateway.slackSecretName=kaval-slack \
  | grep -A3 "secretRef:" 
```

`values.yaml` gains `gateway.slackSecretName` — empty by default, same posture as
`global.imagePullSecretName`/`ecr-cred`: only the Secret's **name** is a chart value, never a
token. When set, the gateway Deployment adds an extra `envFrom.secretRef` for it.

---

## Part 2 — your turn: a real Slack App

Nothing in this repo can do this part; it needs your own Slack account.

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → From scratch.
   Name it `Kaval`, pick your own workspace.
2. **Settings → Socket Mode** → toggle on.
3. **Settings → Basic Information → App-Level Tokens** → generate one with the
   `connections:write` scope. This is `SLACK_APP_TOKEN` (`xapp-...`).
4. **Features → OAuth & Permissions → Scopes → Bot Token Scopes** → add `chat:write`.
   **Install to Workspace**. The **Bot User OAuth Token** this produces is
   `SLACK_BOT_TOKEN` (`xoxb-...`).
5. **Features → Interactivity & Shortcuts** → toggle on (Socket Mode delivers the payloads;
   you don't need a Request URL).
6. Create a channel (e.g. `#kaval-approvals`), invite the bot to it (`/invite @Kaval`), and
   copy its channel ID (right-click the channel name → View channel details → bottom of the
   panel). This is `SLACK_CHANNEL_ID` (`C...`).
7. Create the Secret out of band, the same way `ecr-cred` is created — **never commit these
   values**:
   ```bash
   kubectl create secret generic kaval-slack -n kaval-prod \
     --from-literal=SLACK_BOT_TOKEN=xoxb-... \
     --from-literal=SLACK_APP_TOKEN=xapp-... \
     --from-literal=SLACK_CHANNEL_ID=C...
   ```
8. Set `gateway.slackSecretName: kaval-slack` in `deploy/environments/prod/values.yaml` (and
   the hand-kept copy in `deploy/gitops/prod/helmrelease.yaml` — the known gap from
   ADR-0025 Decision 8) and let Flux reconcile, or `helm upgrade` locally first.

## Verification, once Part 2 is done

- `kubectl logs` on the gateway pod shows `slack_chatops: connected, notifying #<channel> every
  30.0s`.
- Trigger a real `ask`-class proposal (any synthetic incident with default policy works) and
  confirm the message appears in the Slack channel within one poll interval, with working
  Approve/Deny buttons.
- Click Approve. Confirm a reply appears in-thread, `GET /v1/incidents/{id}` shows the decision
  with `actor` starting `slack:`, and the executor acts on it exactly as it would for a
  CLI-approved action.
- Kill the gateway pod mid-session and confirm it reconnects on restart without any manual step.

**Not yet done, honestly:** this live verification. The code, migration, and chart wiring are
complete and tested; a real Slack App, and the end-to-end click-to-execution proof, wait on
Part 2 — Roshan's own setup step, same pattern as KAV-44's AWS Marketplace blocker (ADR-0019).
