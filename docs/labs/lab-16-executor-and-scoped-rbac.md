# Lab 16 — The executor, scoped RBAC, and a real approval

**Phase:** 3 · **Time:** ~45 min · **Cost:** $0 (the dev server and k3d cluster you already have)

Lab 15 deployed the part of the stack that could only ever read and write Postgres. This lab
deploys the part that changes things: the executor, bound with RBAC scoped to a namespace it
doesn't live in, consuming a human's real approval through the gateway's first write endpoint.
The design is
[ADR-0021](../adr/0021-the-executor-scoped-rbac-and-the-approval-write-path.md).

This is Phase 3's actual exit gate: a proposed action, approved by a human, executed by a
component whose permissions are provably narrower than the system it runs alongside.

## Prerequisites

- [Lab 15](lab-15-helm-chart-and-k3d.md) done — a running `kaval-local` k3d cluster
- `make dev-tunnel` running, in its own terminal

---

## Step 1 — build the executor image, rebuild the gateway

The gateway's code changed too (the new decision endpoint), so both need rebuilding, not just
the new one:

```bash
make devbox-ssh    # or run the next line from the laptop with DEV_CONTEXT set
docker --context kaval-devbox compose build gateway executor
```

## Step 2 — lint and render, before deploying anything

```bash
helm lint deploy/charts/kaval -f deploy/environments/local/values.yaml
helm template kaval-local deploy/charts/kaval -f deploy/environments/local/values.yaml \
  | kubeconform -strict -kubernetes-version 1.35.5 -summary -
```

Expect `13/13` resources valid — the three new RBAC objects (`Namespace`, `Role`,
`RoleBinding`) plus the executor's `ServiceAccount` and `Deployment`, alongside the five from
Lab 15.

## Step 3 — get the new images into k3d, and get the chart onto the devbox

```bash
k3d image import kaval/gateway:dev kaval/executor:dev -c kaval-local
```

The chart itself has to reach the devbox too — there's no `git pull` on the dev server; it
builds from whatever the laptop sends. `scp -r deploy/charts/kaval
kaval-devbox:kaval-deploy/deploy/charts/kaval` (replacing the old copy) is how the live
verification below actually did it.

## Step 4 — upgrade the release

```bash
helm upgrade kaval-local deploy/charts/kaval -f deploy/environments/local/values.yaml \
  --wait --timeout 180s
```

**Re-importing an image does not restart anything by itself.** If the gateway pod was already
running, it keeps serving the old image until something tells Kubernetes to reschedule it —
same tag, same Deployment spec text, nothing for Helm or Kubernetes to notice changed. If a
request to a route you just added 404s, that's why:

```bash
kubectl rollout restart deployment/kaval-local-gateway
kubectl rollout status deployment/kaval-local-gateway --timeout=60s
```

## Step 5 — prove the RBAC claim, not just read it

```bash
kubectl auth can-i delete pods --as=system:serviceaccount:default:kaval-local-executor -n kaval-demo   # yes
kubectl auth can-i delete pods --as=system:serviceaccount:default:kaval-local-executor -n default      # no
kubectl auth can-i delete pods --as=system:serviceaccount:default:kaval-local-executor -n kube-system  # no
kubectl auth can-i delete pods --as=system:serviceaccount:default:kaval-local-agent     -n kaval-demo  # no
```

The executor can act only in `kaval-demo` — not even in its own namespace, where
postgres/gateway/agent run. The agent, everywhere, still has no write verb at all.

## Step 6 — give the executor something to act on

```bash
kubectl create deployment checkout --image=nginx:alpine --port=80 -n kaval-demo
kubectl rollout status deployment/checkout -n kaval-demo --timeout=90s
kubectl get pods -n kaval-demo -o wide    # note the pod's exact name
```

Not chart-managed on purpose — `kaval-demo` is deliberately empty by default (Chaos, Phase 6,
is what populates it for real). This one `kubectl create` is the ad hoc stand-in until then.

## Step 7 — seed a real action, approve it for real, watch it happen unattended

An incident, a proposal, and one `restart_pod` action targeting the pod from Step 6, written
directly into the cluster's own Postgres (the same technique Lab 15 used for signals) — the
model call it would normally take is already proven in Labs 10–11; this lab's new surface is
everything downstream of a proposal existing:

```python
# run with: kubectl exec <some-pod-with-kaval_shared-installed> -- python seed.py
# (not the executor pod itself at its default resource limits — see "What actually
#  happened" below for why, and use an unconstrained throwaway pod instead)
from datetime import UTC, datetime
from decimal import Decimal
from kaval_shared.db import get_engine
from kaval_shared.models import Action, BlastRadius, Incident, PolicyClass, Proposal, RiskLevel, Severity
from sqlalchemy.orm import Session

TARGET = "kaval-demo/<the pod name from Step 6>"
with Session(get_engine()) as db:
    incident = Incident(fingerprint="crashloop:k8s:kaval-demo/checkout",
                        severity=Severity.high, opened_at=datetime.now(UTC))
    proposal = Proposal(incident=incident, summary="checkout is crashlooping; restart it",
                        root_cause="transient bad state, confirmed by a clean restart in the runbook",
                        confidence=0.93, risk=RiskLevel.low, model="gemma3:1b-it-qat",
                        tokens_in=850, tokens_out=90, cost_usd=Decimal("0"))
    db.add_all([incident, proposal]); db.flush()
    action = Action(proposal_id=proposal.id, type="restart_pod", target=TARGET, params={},
                    reversible=True, blast_radius=BlastRadius.pod, policy_class=PolicyClass.ask)
    db.add(action); db.commit()
    print(f"action_id={action.id}")
```

Approve it for real, through the gateway — reach it however your network allows
(`kubectl port-forward svc/kaval-local-gateway 18000:8000` was used live):

```bash
python scripts/ops/approve.py <action_id> approved --actor roshan \
  --reason "crashloop restart, known-safe runbook" --gateway-url http://localhost:18000
```

Now **do nothing** and watch the already-running executor's own logs:

```bash
kubectl logs -l app.kubernetes.io/component=executor -f
```

Within 15 seconds (`executor.executeEverySeconds`), it should act on its own.

## Step 8 — read the whole audit trail back through the gateway

```bash
python -c "
import urllib.request, json
req = urllib.request.Request('http://localhost:18000/v1/incidents?limit=1')
inc_id = json.loads(urllib.request.urlopen(req).read())['items'][0]['id']
detail = json.loads(urllib.request.urlopen(f'http://localhost:18000/v1/incidents/{inc_id}').read())
print(json.dumps(detail, indent=2))
"
```

Confirm the incident's one proposal's one action carries a `decision` (verdict `approved`,
your `actor`/`reason`) and an `execution` (`status: "success"`, `before_state`/`after_state`
both populated, `stdout` naming the exact pod deleted) — every field this endpoint reads, all
written by components that never talked to each other directly.

## Done when

- [x] `helm lint`/`helm template | kubeconform` both clean, 13/13 resources
- [x] The four `kubectl auth can-i` checks return exactly `yes, no, no, no`
- [x] An action approved through the real gateway endpoint is executed by the
      already-running, continuously-looping executor with no manual trigger
- [x] `GET /v1/incidents/{id}` shows the full timeline: incident → proposal → action →
      decision → execution
- [x] A simulated `never`-class approval (bypassing the gateway's own refusal) is refused by
      the executor's own independent re-check

---

## What actually happened, live (2026-10-01)

Two more real bugs, on top of the RBAC and defense-in-depth design — both are
[ADR-0021](../adr/0021-the-executor-scoped-rbac-and-the-approval-write-path.md#two-more-bugs-found-live)'s
full write-up; the short version:

**The first approval attempt got a real `404`.** The gateway image had been rebuilt and
re-imported into k3d, but the running pod was still the old one — `k3d image import` doesn't
restart anything, and a same-tag, same-spec Deployment gives Helm nothing to notice changed.
`kubectl rollout restart deployment/kaval-local-gateway` fixed it; Step 4 above now says so up
front instead of letting the next person rediscover it the same way.

**Seeding the demo action by `kubectl exec`-ing into the executor pod OOM-killed it.** The
executor's own steady-state loop fits comfortably in its 128Mi limit — it had been running for
hours, zero restarts, before and after this — but a second Python process (importing
SQLAlchemy, Pydantic, psycopg) sharing that same cgroup pushed it over, confirmed via
`lastState.terminated.reason: OOMKilled`, not guessed. Worked around with a throwaway,
unconstrained `kubectl run` pod running the same image instead — Step 7's comment reflects
this; the chart's real resource limits weren't loosened for an ad hoc diagnostic script that
isn't what they're sized for.

The happy path, live and complete, with real IDs:

```
success   restart_pod          314e84fc-74ab-4cc8-96b0-432bcfc4b6b7  deleted pod
kaval-demo/checkout-7dbcfc8749-t6gxj (uid d2bbe074-99ed-4485-9a61-f5e3519402d9, was Running,
owner=ReplicaSet)
```

`kubectl get pods -n kaval-demo` immediately after: a new pod, a new name, `RESTARTS 0` — the
ReplicaSet healing it, which is the point; the executor's job ends at "delete", not "replace".

The refusal path, live, with the gateway's own check bypassed entirely (the `Decision` row was
written straight into Postgres, never through `POST /v1/actions/{id}/decisions`):

```
skipped   delete_pvc           4120b028-221d-4ba1-a179-c553b51c2c99  refused: policy re-check
says 'never' (stored class was ask)
```

`kubectl get pvc -n kaval-demo` before and after: unchanged. Nothing was touched, because
nothing authorized it to be — not the gateway's check this time, the executor's own.
