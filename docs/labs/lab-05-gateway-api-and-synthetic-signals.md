# Lab 05 — Fake incidents in, a real API out

**Phase:** 1 · **Time:** ~1 hr · **Cost:** about 2–4 cents of dev-server time
([Lab 03](lab-03-aws-dev-server.md)), plus a cent or less of CPU credits (see
[budget-plan.md](../cost/budget-plan.md#the-dev-server-adr-0007))

Two halves of the same loop. A **synthetic signal generator** writes the observations one real
failure would produce (a pod running out of memory, an amd64 image on the arm64 node, a cost
spike, a forgotten disk) as ordinary `signal` rows. The **gateway's `/v1` API** reads them back:
listed, filtered and paged, with the OpenAPI contract the phone app will later be built against.

---

## Why this order

Phase 2's agent needs something to diagnose, and Phase 5's app needs an API to call. Neither can
wait for a real cluster (Phase 3) or a real bill worth watching (Phase 7). So the fakes come first,
built to be **indistinguishable in shape** from the real thing:

- **Real payload shapes.** A fake OOM kill is a Kubernetes `Event` with `reason: OOMKilled` and an
  `involvedObject`, a cost spike is a Cost Explorer daily row. When the real collector arrives it
  writes the same shapes, and nothing downstream changes.
- **But never mistakable for real.** Every fake carries `synthetic: true`, the scenario name and
  a `run_id` in its `value`. Metrics can exclude them, and prod can refuse them.
- **Reproducible.** `SEED=42` gives the same pods, messages and numbers every time, which the
  Phase 2 evals depend on. Each run still gets a fresh `run_id`, so two runs stay distinguishable.

The API's conventions are in [ADR-0009](../adr/0009-gateway-api-conventions.md): versioned under
`/v1`, read-only, cursor-paged, money as strings.

---

## Prerequisites

- [Lab 04](lab-04-compose-stack.md) complete: `make dev` brings the stack up healthy
- The laptop's `.venv` installed (`make sync`, which installs what `uv.lock` pins; before `KAV-24` this was `pip install -e ".[gateway,dev]"`), for the tests

---

## Step 1 — Read the files before running them

| File | What it does |
|---|---|
| `services/collector/kaval_collector/synthetic.py` | Four scenarios, each a burst of signals in real payload shapes. `python -m kaval_collector.synthetic --list` |
| `services/collector/Dockerfile` | arm64, non-root, **core dependencies only**: no FastAPI in the collector image |
| `compose.yaml` → `signals` | One-shot service in the `tools` profile, so `make dev` doesn't start it |
| `services/gateway/kaval_gateway/api.py` | The `/v1` routes, the cursor paging, and `read_session`, which opens every request's transaction `READ ONLY` |
| `services/gateway/kaval_gateway/schemas.py` | What the API returns, kept separate from the tables |
| `services/conftest.py` | `db_session`: each test runs inside a transaction that's rolled back, so tests never leave rows behind |

---

## Step 2 — Write some fake incidents

```bash
make dev                                   # if the stack isn't up
make signals                               # lists the scenarios
make signals SCENARIO=oom-crashloop SEED=42
make signals SCENARIO=cost-spike
```

```
oom-crashloop: wrote 6 signals, run_id 7dc25c76-0eaa-45fd-9596-689ee00ea784
cost-spike: wrote 7 signals, run_id 38cafff4-57db-43cd-9f70-93993fc08fb7
```

The first run builds the collector image on the server: 2 min on 2026-09-27, seconds after that.
A run is one transaction, so it lands whole or not at all.

| Scenario | Signals | What the agent should eventually conclude |
|---|---|---|
| `oom-crashloop` | memory at 95–99% of limit → `OOMKilled` → 3 × `BackOff` → Alertmanager `KubePodCrashLooping` | the memory limit is too low for the workload |
| `exec-format` | container exits 255 with `exec format error` → 3 × `BackOff` | the image was built for the wrong CPU architecture |
| `cost-spike` | six normal days of EC2 spend, then one at ~7× | something started that shouldn't have |
| `idle-volume` | an EBS volume `available` (attached to nothing) for 7–21 days | a forgotten disk still billing |

---

## Step 3 — Read them back through the API

In a second terminal: `make dev-tunnel`. Then open **<http://localhost:8000/docs>**. That's the
interactive OpenAPI page, generated from the code. Or from the command line:

```bash
curl "http://localhost:8000/v1/signals?run_id=<run_id from step 2>"
curl "http://localhost:8000/v1/signals?synthetic=false"     # real signals only: none yet
curl "http://localhost:8000/v1/signals?limit=2"             # then pass next_cursor as ?cursor=
curl "http://localhost:8000/v1/incidents"                   # empty until Phase 2 correlates
```

2026-09-27, an `exec-format` run as the API returns it (newest first):

```
08:14:29 kubernetes pod_back_off    kaval-demo/gateway-f52jr-qw4db | Back-off restarting failed container gateway
08:13:31 kubernetes pod_back_off    kaval-demo/gateway-f52jr-qw4db | Back-off restarting failed container gateway
08:12:29 kubernetes pod_back_off    kaval-demo/gateway-f52jr-qw4db | Back-off restarting failed container gateway
08:12:03 kubernetes container_exited kaval-demo/gateway-f52jr-qw4db | exec /usr/local/bin/uvicorn: exec format error
```

Then the answers to bad input, which matter as much as the good ones:

| Request | Answer |
|---|---|
| `?cursor=junk` | `400 {"detail":"invalid cursor"}`, not a 500 |
| `?limit=500` | `422`: pages are capped at 200 |
| `/v1/incidents/<unknown id>` | `404 {"detail":"incident not found"}` |

---

## Step 4 — Run the tests against the real database

```bash
set -a; . ./.env; set +a      # POSTGRES_HOST=localhost: the tunnel
pytest services -q            # 40 passed
```

Without the tunnel, the database tests **skip** rather than fail (28 pass, 12 skip), so `make test`
stays usable with the server stopped. With it, they prove what unit tests can't:

- **Paging never skips a row.** Five signals, two with the same timestamp, read two at a time:
  every row comes back exactly once. The id breaks the tie; a timestamp-only cursor would lose one.
- **The database refuses gateway writes.** An `INSERT` through `read_session` fails with
  `cannot execute INSERT in a read-only transaction`.
- **The incident timeline loads whole.** Signals, a proposal, its action, the decision, the
  execution and the outcome come back in one response, in a fixed number of queries.

---

## Troubleshooting

**`make signals` or `compose up` fails with `Connection timed out during banner exchange` or
`client version 1.48 is too new`.** Both came from the same cause on 2026-09-27: *42 open Session
Manager sessions*. Every `docker --context` call opens an SSH connection through SSM. When the
laptop's end closes, the server's SSH daemon doesn't notice, because its TCP peer is the local SSM
worker, which is still there. Each connection then lingers for SSM's 20-minute idle timeout, and
Compose opens ~7 per command. New handshakes start timing out, and the version error is the
Docker client falling back after one of those timeouts.

The fix is two lines in `/etc/ssh/sshd_config.d/10-kaval-keepalive.conf` on the server
(`ClientAliveInterval 30`, `ClientAliveCountMax 3`): the SSH daemon probes each client and drops
one that doesn't answer within ~90 seconds. It's in the dev server's first-boot script
(`infra/modules/devbox/user_data.sh.tftpl`) and was applied to the running server the same day.
After it, 14 connections dropped to 0 within two minutes. Check the count with:

```bash
aws ssm describe-sessions --state Active --query 'length(Sessions)' --profile kaval --region ap-south-1
```

It also matters for cost: the idle stop counts open SSH connections as "someone is working", so
leaked ones kept the server up for up to 20 extra minutes.

---

## Step 5 — Stop

```bash
make dev-down      # the signals stay in the pgdata volume
make devbox-down
```

---

## Done when

- [ ] `make signals SCENARIO=oom-crashloop SEED=42` writes 6 rows
- [ ] `/docs` loads through the tunnel and lists `/healthz`, `/v1/signals`, `/v1/signals/{signal_id}`, `/v1/incidents`, `/v1/incidents/{incident_id}`
- [ ] `/v1/signals?run_id=` returns exactly that run's signals
- [ ] A bad cursor is a 400, an oversized page is a 422, an unknown id is a 404
- [ ] `pytest services -q`: 40 passed with the tunnel open
- [ ] Journal entry appended

---

## What to write down

- Why fake data should match the real payload shapes, and why it must still be flagged as fake
- Why offset paging breaks on a table that grows while you read it, and how a cursor fixes it
- Why "read-only" belongs in the database transaction, not only in the code
- Why money travels as a string in JSON
- How to find a resource leak you can't see from the laptop (here: counting SSM sessions)
