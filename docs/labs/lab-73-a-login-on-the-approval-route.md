# Lab 73 — A login on the approval route

**Phase:** 8 (security) · **Story:** `KAV-115` · **Time:** about 45 minutes for Parts 1 to 4 · **Cost:** $0 for Parts 1 to 4; the live proof (Part 6) is one staging session, about $0.50

Before this lab, anything that could reach the gateway could record an approval. This lab adds a token to that one route and proves, with tests, that it refuses everyone else. The decision is [ADR-0073](../adr/0073-the-approval-route-gets-a-login-and-the-namespace-gets-network-rules.md); the threat is `T-APPROVE-1` in [the threat model](../architecture/threat-model.md).

## Prerequisites

- The project's Python environment (`make sync`). For Part 3, `helm` on your path.
- For Part 2 only: Docker, to start a throwaway database.

## Part 1 — See the old behaviour, then the new

Read `services/gateway/kaval_gateway/approver_auth.py`: forty lines, one function that matters (`require_approver`). Then `api.py`: the route carries `dependencies=[Depends(require_approver)]`, which FastAPI runs *before* the database session is opened and before the body is read.

```bash
python -m pytest services/gateway/tests/test_approver_auth.py -q
```

Thirty tests, no database. The ones worth reading:
- `test_with_no_usable_token_configured_the_route_is_shut`: no token, an empty one, a short one: all 503, even for a caller who "knows" the value.
- `test_a_wrong_token_is_a_401`: right length but wrong, one short, one long, a correct beginning, a different case, 500 characters, SQL: all 401.
- `test_a_refused_call_never_opens_the_database`: the stand-in session fails the test if anything asks for it.
- `test_neither_the_log_nor_the_reply_carries_the_token`.
- `test_every_route_that_writes_requires_the_login`: add a second write route without the login and this fails.

## Part 2 — Against a real database

The existing approval tests send the token now, and one new test shows the actor is stored as `api:<name>`.

```bash
docker run -d --name kaval-pg -e POSTGRES_PASSWORD=testonly -e POSTGRES_USER=kaval -e POSTGRES_DB=kaval \
  -p 127.0.0.1:55432:5432 pgvector/pgvector:pg16
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 POSTGRES_DB=kaval POSTGRES_USER=kaval POSTGRES_PASSWORD=testonly
alembic upgrade head
KAVAL_REQUIRE_DB=1 python -m pytest services/gateway -q
docker rm -f kaval-pg
```

Expect `127 passed` plus the six expected failures of `KAV-114`.

## Part 3 — The chart

```bash
python -m pytest scripts/dev/test_chart_approver.py scripts/dev/test_gitops_values_in_sync.py -q
```

Staging and production read `APPROVER_TOKEN` from the Secret `kaval-approver` (`optional: true`, so a missing Secret leaves the pod running with the route shut instead of crash-looping); the token is never a literal in the chart; an environment that names no Secret gets no variable at all. The Flux copies of the settings must match the environment files, and a test fails the moment they differ.

## Part 4 — Approving from your own terminal

```bash
export KAVAL_APPROVER_TOKEN=...    # the gateway's token; never a command-line option, so it stays out of history
make approve ACTION=<uuid> VERDICT=approved
```

Without the variable the script stops before it calls anything. `make chaos-approve` reads the token from the cluster's own Secret on the node and uses it there; it never reaches your laptop or the output.

## Part 5 — Where the token comes from

The token is not typed anywhere. `infra/modules/approver-token` makes it (`random_password`, 43 letters and digits) and stores it as a SecureString at `/kaval/<env>/approver/token`; the node's role is allowed to read exactly that parameter; the node's bootstrap, on every boot and every 6 hours, writes the Secret `kaval-approver` from it.

```bash
python -m pytest scripts/dev/test_approver_token_bootstrap.py -q
```

The bootstrap step is cut out of `infra/modules/node/user_data.sh.tftpl` and run with stand-ins for `aws` and `kubectl`. The negative cases are the point: a failed call, an empty value and a value of spaces all leave **no Secret** and let the bootstrap carry on (a shut route is the safe state), and the token is never printed. The last one was found by the test itself: a value of spaces used to make a Secret.

Read-only look at what Terraform would do (production, 2026-11-02): `Plan: 2 to add, 3 to change, 0 to destroy`. The two additions are the parameter and the password; the three changes are the node's read permission (one more parameter), the launch template (the new bootstrap text) and the scaling group pointing at the new template version. Nothing is replaced, and the group is parked at zero, so no server is touched. Show the plan before any apply; production only when asked.

To get the token into your shell for `make approve`, without showing it: see [the runbook](../runbooks/approval-refused.md).

One thing to know before you add anything to that script: it must pack to under 16,384 bytes (EC2's limit on start-up text), and `scripts/dev/test_cost_pull.py` fails at 15,800 to make the next person notice. This step first pushed it to 16,060, so it was written shorter and two repeated comments were trimmed (15,744 now). Check with `python -m pytest scripts/dev/test_cost_pull.py -q -k ec2_limit`.

## Part 6 — The live proof

*(Written when it is run: one staging session, each step on its own go.)*

## Done when

- [x] The route refuses a missing, wrong or malformed token, and is shut when none is configured *(2026-11-02)*
- [x] A refusal is logged and counted with a fixed word, and never carries the token *(2026-11-02)*
- [x] The actor is stored as `api:<name>` *(2026-11-02)*
- [x] The token's home is built and tested: the Terraform, the read permission, the bootstrap step and a runbook *(2026-11-02; applying it is a step of the live proof)*
- [ ] The network rules are in the chart
- [ ] The live proof is recorded, and the threat-model row says Covered

## Cost

$0.
