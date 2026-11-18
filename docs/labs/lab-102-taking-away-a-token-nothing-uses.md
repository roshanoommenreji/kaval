# Lab 102 — Taking away a token nothing uses

**Phase:** 8 (hardening) · **Story:** `KAV-162` (and `KAV-150`) · **Finding:** C-1 in [access-review.md](../architecture/access-review.md) · **Decision:** [ADR-0101](../adr/0101-the-aws-access-review-and-what-it-changes.md) · **Cost:** $0 to build; the live proof rides on a staging session that is happening anyway

## What you will build

A one-line change in three chart templates, and a test that fails if any pod in any environment ever carries a Kubernetes login that nothing needs. You will prove the test fails before the change and passes after.

You need the repository, the project's Python environment (`make sync`) and `helm`. No cluster and no AWS login.

## Why

Every Kubernetes pod is handed a small login file for its namespace's `default` account unless the chart says otherwise. For most pods that file is harmless: the `default` account is bound to no role, so the login can do nothing. But "harmless today" depends on nobody ever binding a role to `default`. The gateway is the one pod that talks to Slack and takes approval calls, so it is the wrong place to leave a credential that nothing uses. Real teams switch it off for every pod that does not call the Kubernetes API, because a credential that is not there cannot be stolen, and the pod loses nothing.

The access review ([Lab 100](lab-100-the-cluster-half-of-the-access-review.md)) found two such pods in production: the gateway and the database migration job. The chart's own Postgres, used only on a laptop and in the EKS lab, had the same gap; it was found by the test below.

## Part 1 — See the problem

```bash
helm template kaval deploy/charts/kaval -n kaval \
  -f deploy/environments/prod/values.yaml | python scripts/dev/rbac_scan.py | tail -4
```

Before the change the last lines read:

```
TOKENS NOTHING NEEDS:
  Deployment/kaval-gateway mounts the token of 'default', which is bound to no role
  Job/kaval-migrate-1 mounts the token of 'default', which is bound to no role
```

## Part 2 — Check that nothing uses it

Before removing a credential, check that no code reads it. The gateway and the migration job talk to Postgres and to Slack; neither calls the Kubernetes API.

```bash
grep -rniE "kubernetes|serviceaccount|kubeconfig|/var/run/secrets" services/gateway migrations
```

The only hits are the word "kubernetes" as a label on test signals, and a comment about the approver token (a Kubernetes *Secret*, delivered as an environment variable, not the login file).

## Part 3 — The change

Under each pod's `spec:` in `deploy/charts/kaval/templates/gateway.yaml`, `migrate-job.yaml` and `postgres.yaml`, add:

```yaml
      automountServiceAccountToken: false
```

The diagnoser, the model, the agent and one Prometheus pod already carry this line; the three templates now match them.

## Part 4 — The test that keeps it that way

`scripts/dev/test_chart_no_spare_token.py` renders the chart for `local`, `staging`, `prod` and `lab-eks` and runs the same scan on each (`rbac_scan.review`). It asserts that no workload mounts a token nothing needs, and that the gateway and the migration job are seen by the scan and show "no".

```bash
python -m pytest scripts/dev/test_chart_no_spare_token.py -q
```

To see it catch the mistake, take the change back out (`git stash -- deploy`) and run it again: six tests fail, one per environment and one per named pod. Put the change back and they pass. A test that has never failed has not been shown to work.

## Part 5 — The comments (`KAV-150`)

Four comments in service code still described a phone app that was retired on 2026-11-15 ([ADR-0097](../adr/0097-phase-9-is-retired-slack-is-the-approval-surface.md)): the gateway's API description, a schema docstring, the package docstring and the executor's `redact.py` docstring. They were left on purpose then, because changing a comment inside a service changes its image and owes a release round. They ride this one, since the gateway needs a release anyway. The gateway's API description is shown at `/openapi.json`, so it is the one place a reader outside the repository could have seen the old wording.

## Part 6 — The live proof (done 2026-11-18)

Released as **v0.4.4** (`sha-f280013`), passed the staging smoke test 7 of 7 (2026-11-18, [Lab 104](lab-104-a-second-size-for-the-app-server.md) tells the rest of that day), and promoted through `promote.yml` (#452). Production started on it at 09:48 IST and `make prod-smoke` passed 9 of 9 (10 pods running, no restarts, every running digest equal to the registry's, the database at `d4f7a2c9e815`, both API calls 200, the timers set). Then, read-only from the server over Session Manager:

```bash
kubectl get pods -n kaval-prod -o custom-columns=POD:.metadata.name,VOLUMES:.spec.volumes[*].name --no-headers
kubectl get deploy -n kaval-prod -o custom-columns=NAME:.metadata.name,AUTOMOUNT:.spec.template.spec.automountServiceAccountToken --no-headers
```

Both gateway pods and the agent, the diagnoser and the Prometheus collector list **no** `kube-api-access` volume (`automount` false for the gateway, agent, diagnoser, collector-prometheus and Ollama); only the collector, the cost collector, the executor and Prometheus still carry one, which are the accepted findings of the review (they call the cluster's API). The migration job ran before the services, as a hook, and is gone, with the database at head. Five network rules and one approver entry were also as expected.

## What to take away

- A default is a decision somebody else made. Read what a pod is given, not only what you asked for.
- Check that nothing uses a thing before taking it away, then take it away everywhere it is not needed.
- Put the rule in a test that runs on every environment, so the next new workload cannot forget.
