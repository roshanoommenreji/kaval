# Lab 21 — Slack ChatOps, outbound only

**Phase:** 4 · **Time:** ~30 min for the code, plus a one-time Slack App setup you do yourself
**Cost:** $0 (Slack's free tier; no new AWS resource)

Phase 4's exit gate only needed the node to rebuild and redeploy itself. It never needed a way
for a human to approve anything from outside the cluster — `scripts/ops/approve.py`, run over
the same SSH/SSM tunnel used for everything else, has been enough so far. This lab replaces
that stopgap with a real approval surface that still never opens an inbound port: Slack's
**Socket Mode**. The design and the reasoning for deferring mobile instead are in
[ADR-0026](../adr/0026-slack-chatops-and-deferred-mobile.md).

This lab has two parts, both done. **Part 1** — the code, migration, and tests — was built and
unit-tested first. **Part 2** — a real Slack App (nothing in this repo can create one on your
behalf) and the live, end-to-end verification — is below, including the one real mistake made
along the way and how it was corrected.

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

- [x] `kubectl logs` on the gateway pod shows `slack_chatops: connected, notifying #<channel>
  every 30.0s`. Needed one real fix first: nothing configured Python's root logger, so this
  and every other app-level log line was silently dropped — `logging.basicConfig()` added to
  `main.py`, reading the chart's existing (previously unused) `LOG_LEVEL`.
- [x] Trigger a real `ask`-class proposal and confirm the message appears with working
  Approve/Deny buttons. Two real synthetic incidents (`oom-crashloop`, and a genuinely crashing
  pod triggering a real `crashloop:k8s:...` event) both diagnosed to **zero actions** — correct,
  conservative model behaviour, not a bug: the only runbook that exists yet
  (`restore-from-backup.md`) doesn't match either failure, and this model won't invent a fix
  without one (same documented behaviour as `KAV-41`/`KAV-43`). Crash-specific runbooks are
  Phase 5 scope. Isolated the Slack-specific mechanics with one manually-inserted `ask`-class
  `Action` row instead, flagged `{"manual_test": true}` in `params` — same table, same code
  path, deliberately not claimed as a model-generated proposal.
- [x] Click Approve. `GET /v1/incidents/{id}` confirmed the decision: `verdict: approved`,
  `actor: slack:roshanoommenreji`. The executor acted within one second —
  `execution.status: "success"`, `stdout: "deleted pod kaval-demo/crashy ..."`.
- [ ] Kill the gateway pod mid-session and confirm it reconnects without a manual step — not
  yet exercised; the Socket Mode client's own reconnect behaviour is documented upstream but
  wasn't forced and observed here.

**One real incident along the way, not hidden:** debugging the Slack App setup in Slack's own
UI, the actual token values briefly appeared in a terminal command's output by mistake (a shell
quoting bug in a presence-check). Both the bot token and the app-level token were rotated in
Slack before continuing — the channel ID isn't a credential, so it didn't need rotating. Worth
remembering: a presence-only check must use `[ -n "$VAR" ]`, never `${VAR:-placeholder}` (which
expands to the real value when set).

**Environment used:** the local k3d cluster on the dev server (`kaval-devbox`), not prod —
consistent with how `KAV-47`/`KAV-48` were proven before prod existed as a target. Prod's own
`values.yaml` gets the same `gateway.slackSecretName` wiring, and its own `kaval-slack` Secret,
whenever prod is next brought up for other Phase 4 work — adding it now, with no Secret to back
it, would just make the next Flux reconcile fail to start the gateway.
