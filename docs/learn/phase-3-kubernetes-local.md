# Phase 3 — Kubernetes local

> **Written from:** theory
> **Lab:** to be written
> **Cost:** $0 — k3d runs in Docker on your laptop

## Where this sits

Phase 2 built an agent that reasons about incidents from synthetic signals. This phase gives it
real ones, and gives the executor something real to fix.

It unlocks: everything about Phase 4 becomes a *deployment* problem rather than a *learning*
problem, because the manifests, the chart and the RBAC will already work.

## What we're doing

- A local cluster with **k3d** — k3s running inside Docker containers
- The **Helm umbrella chart** that will deploy unchanged to k3s on AWS and to EKS
- Real Kubernetes events and Prometheus metrics as signal sources
- The **executor** with scoped RBAC — the privilege split stops being a diagram
- `linux/arm64` image builds, ahead of needing them

## Why this way

**Because RBAC is the architecture, and RBAC cannot be tested without a cluster.** The claim that
the agent cannot touch anything is only true if the ServiceAccount bindings actually say so. On a
laptop, getting that wrong costs a `kubectl` command. On AWS with a live cluster, getting it wrong
is a security finding.

The rejected alternative was going straight to AWS after Phase 2. It merges two hard problems —
"do I understand Kubernetes" and "does my cloud networking work" — into one debugging session
where every symptom has two possible causes.

---

## Key concepts

### The reconciliation loop is the whole idea

Everything else in Kubernetes follows from this.

You submit a **desired state**: "three replicas of this image." A **controller** continuously
compares desired state against **actual state** and takes action to close the gap. A pod dies;
the observed count is two; the controller creates one.

This never stops. It is not a deployment step, it is a permanent background process.

```
   desired state  ─────┐
                       ▼
                  ┌─────────┐
                  │controller│ ──── acts to close the gap
                  └─────────┘
                       ▲
   actual state   ─────┘
```

Two consequences that trip up people from a traditional release background:

- **Manual changes get reverted.** `kubectl scale` to 5 against a Deployment declaring 3, and the controller wins. This feels hostile until you realise it is the same property that heals a crash.
- **"Deploy" means "change the desired state."** The rollout is a consequence, not a command you issue.

This is also exactly the mental model Terraform uses, and — in Phase 4 — Flux. Once you have it,
three tools collapse into one idea.

### Declarative versus imperative, and why it matters here

Imperative: *do these steps.* Declarative: *this is how it should look.*

Declarative wins for infrastructure because it is idempotent (applying twice equals applying once),
because the description is diffable and reviewable, and because the system can keep enforcing it
rather than acting once and forgetting.

It matters specifically for Kaval because the executor's actions are *changes to desired state*,
not commands. "Patch the memory limit to 512Mi" is a declaration; the controller performs the
rollout. That makes actions naturally recordable and often naturally reversible — you know the
previous declared value, so you know how to undo it.

### The object model, minimally

| Object | What it is |
|---|---|
| **Pod** | One or more containers sharing a network namespace and storage. The unit of scheduling. Usually not created directly. |
| **ReplicaSet** | Maintains N identical pods. Rarely touched directly. |
| **Deployment** | Manages ReplicaSets to give you rolling updates and rollback. What you normally write. |
| **Service** | A stable name and virtual IP load-balancing across pods. Pods are ephemeral; Services are not. |
| **Ingress** | HTTP routing from outside the cluster to Services, by host and path. |
| **ConfigMap** | Non-secret configuration, as env vars or mounted files. |
| **Secret** | The same, base64-encoded — **encoding, not encryption**, unless you have enabled encryption at rest. |
| **Namespace** | A scope for names and a boundary for policy and quota. |
| **PersistentVolumeClaim** | A request for durable storage that outlives the pod. |

The single most important relationship: **pods are cattle, Services are the stable address.**
Nothing should ever hold a pod IP.

### Why a pod, and not just a container

A pod is a group of containers that share a network namespace (same localhost, same IP) and can
share volumes. They are always scheduled together on one node.

This exists for the **sidecar** pattern: a log shipper, a proxy, a credential refresher running
alongside the main container, coupled tightly enough to need the same lifecycle and localhost.

Most pods have one container. The abstraction earns its place when you need the second.

### RBAC: ServiceAccounts, Roles, bindings

Kubernetes authorisation is deliberately simple and composes well:

- A **ServiceAccount** is an identity a pod runs as
- A **Role** is a set of permissions — verbs (`get`, `list`, `patch`, `delete`) on resources (`pods`, `deployments`) — scoped to one namespace
- A **ClusterRole** is the same, cluster-wide
- A **RoleBinding** grants a Role to a subject in a namespace; a **ClusterRoleBinding** grants cluster-wide

Everything is **additive and default-deny**. There is no "deny" rule; you simply do not grant.
Permissions accumulate across all bindings for a subject.

For Kaval:

| Service | ServiceAccount permissions |
|---|---|
| `agent` | `get`, `list`, `watch` on pods, events, deployments. **No write verbs anywhere.** |
| `collector` | Same read-only set |
| `executor` | `patch` and `delete` on pods, `patch` on deployments — in the application namespace only |

The executor's Role is a **RoleBinding, not a ClusterRoleBinding**, deliberately. It cannot touch
`kube-system`. Blast radius is enforced by the cluster, not only by policy — which means a bug in
the policy engine still cannot produce a cluster-wide incident.

You can verify any of this without deploying anything:

```bash
kubectl auth can-i delete pods \
  --as=system:serviceaccount:kaval:agent -n kaval
# expected: no
```

That command is the proof of the architecture claim, and it belongs in CI.

### Helm: templating plus release state

Helm does two distinct things, and conflating them causes confusion:

1. **Templating.** A chart is parameterised YAML; a values file supplies the parameters. This is what makes one chart serve local, k3s and EKS.
2. **Release management.** Helm records what it installed, so `helm upgrade` computes a diff and `helm rollback` can revert to a previous revision.

Chart structure:

```
deploy/charts/kaval/
  Chart.yaml          name, version, dependencies
  values.yaml         defaults
  templates/          the parameterised manifests
```

The portability rule for this project: **environments differ only by values files.** If `lab-eks`
needs a template change that `prod-k3s` does not, the claim that the same chart runs on both is
false — and that is a defect to fix, not a special case to accommodate.

`helm template` renders locally without a cluster, which makes chart changes reviewable in a pull
request as a plain YAML diff.

### k3d, and what it is not

k3d runs k3s inside Docker containers. Nodes are containers; the cluster comes up in seconds and
is disposable.

It is genuinely Kubernetes — same API, same manifests, same RBAC. What it is not is a network
simulator: LoadBalancer services, cloud storage classes and cloud-specific controllers behave
differently or not at all. Those differences are Phase 4's work, and expecting k3d to catch them
leads to a confusing afternoon.

The right mental split: **k3d validates your manifests and your RBAC. It does not validate your
cloud.**

### Kubernetes events as signal, and their traps

Events are records of things the cluster did — scheduling decisions, pulls, restarts, OOM kills.
They are the raw material for Kaval's reliability domain.

Three properties to design around:

- **They expire.** Default retention is about an hour. Events are a stream to consume, not a store to query.
- **They are deduplicated with a count.** A pod restarting fifty times may be one event object with `count: 50`, so naive counting undercounts.
- **They are not guaranteed.** Under pressure, events can be dropped. Never treat absence as evidence.

Which is why the collector normalises events into the `signal` table immediately. The database is
the record; the event stream is only the feed.

---

## Common mistakes

| Mistake | What it costs |
|---|---|
| Creating pods directly instead of a Deployment | No rollout, no rollback, no self-healing on node loss |
| Referencing a pod IP anywhere | Breaks on the next restart |
| Expecting a manual `kubectl scale` to persist | The controller reverts it; confusion follows |
| Assuming Secrets are encrypted | Base64 is encoding. Anyone with read access reads them. |
| ClusterRoleBinding where a RoleBinding suffices | Blast radius silently becomes the whole cluster |
| Environment-specific template changes | Portability claim quietly becomes false |
| Querying events as if they persist | They expire in about an hour |
| Counting event objects instead of reading `count` | Fifty restarts look like one |
| Expecting k3d to catch cloud networking problems | Phase 4 surprises you anyway |

## Glossary

| Term | Meaning |
|---|---|
| **Control plane** | API server, scheduler, controller manager, etcd — the cluster's brain |
| **Node** | A machine running pods |
| **kubelet** | Node agent that starts containers and reports status |
| **Reconciliation loop** | Continuous comparison of desired and actual state |
| **Controller** | The process running that loop for a resource type |
| **Desired state** | What you declared |
| **Pod** | Co-scheduled containers sharing network and storage; unit of scheduling |
| **Sidecar** | A helper container in the same pod |
| **ReplicaSet** | Maintains N identical pods |
| **Deployment** | Manages ReplicaSets; provides rolling update and rollback |
| **Service** | Stable name and virtual IP load-balancing across pods |
| **Ingress** | HTTP routing into the cluster |
| **ConfigMap / Secret** | Non-secret / base64-encoded configuration |
| **Namespace** | Name scope and policy boundary |
| **PVC** | PersistentVolumeClaim — a request for durable storage |
| **ServiceAccount** | The identity a pod runs as |
| **Role / ClusterRole** | Permissions, namespaced / cluster-wide |
| **RoleBinding** | Grants a Role to a subject |
| **Verb** | An allowed operation: get, list, watch, create, patch, delete |
| **Helm chart** | Parameterised manifests plus metadata |
| **Values file** | The parameters for one environment |
| **Release** | An installed instance of a chart, with revision history |
| **k3d** | k3s running in Docker containers |
| **Event** | A record of a cluster action; expires in roughly an hour |

## Check yourself

1. You `kubectl scale` a Deployment to 5. It goes back to 3. Explain precisely why, and why that is the same mechanism that heals a crash.
2. Write the `kubectl auth can-i` command that proves the agent cannot delete pods.
3. Why is the executor bound with a RoleBinding rather than a ClusterRoleBinding, and what does that buy that policy alone does not?
4. Someone says Secrets are encrypted. Correct them precisely.
5. Your chart needs one extra annotation on EKS but not on k3s. What does the project's portability rule say to do?
6. Why can't you query Kubernetes events for what happened yesterday?
7. A pod restarted 50 times. Why might your event-counting code report 1?

## In an interview

**"How did you make sure the AI component couldn't damage the cluster?"**

> "Kubernetes RBAC does the enforcing, not my application code. The agent runs as a ServiceAccount
> whose Role has only get, list and watch — no write verb exists anywhere in its bindings, so even
> if the process were fully compromised the API server refuses. The executor is a separate
> ServiceAccount with patch and delete, bound with a RoleBinding scoped to the application
> namespace rather than a ClusterRole, so it structurally cannot touch kube-system. That means a
> bug in my policy engine still can't cause a cluster-wide incident — the blast radius is enforced
> one layer below my code. I assert it in CI with `kubectl auth can-i --as=system:serviceaccount:...`,
> so the claim is tested rather than asserted."

The strong move is that the guarantee lives below your own code, and that you test it.

## Further reading

- Kubernetes documentation — *Controllers* and *Object Management* (the reconciliation model)
- Kubernetes documentation — *Using RBAC Authorization*, including `auth can-i`
- Helm documentation — *Charts* and *Chart Template Guide*
- k3d documentation — cluster creation and registry integration
- Kelsey Hightower, *Kubernetes The Hard Way* — worth reading even without doing it
