# Access review: who can do what

A least-privilege review asks of every identity: what is it allowed to do, and is that more than
its job needs? This page is the evidence for `KAV-154`. It has two halves.

| Half | Status |
|---|---|
| **Inside the cluster** (Kubernetes roles and bindings) | **Done 2026-11-15**, from the chart as it is rendered for production and staging |
| **In AWS** (IAM roles, IAM Access Analyzer, the permission boundary) | **Not done.** Needs a read-only look at the live account; planned with a production session |

How to repeat the cluster half: [Lab 100](../labs/lab-100-the-cluster-half-of-the-access-review.md).

## Inside the cluster

### The result in four lines

- **No rule uses a wildcard.** Every rule names its resources and its verbs.
- **Nothing Kaval ships can read a Kubernetes Secret.**
- **The agent has no access to the Kubernetes API at all:** no role, and no token in its pod.
  That is rule 3 of the project (the thinking half has no hands), checked rather than assumed.
- **One finding:** two workloads carry a token they do not need (C-1 below).

### Who holds what (production)

| Identity | Where it applies | What it may do | Why it needs it |
|---|---|---|---|
| `kaval-executor` | the demo namespace only | `get`, `delete` pods; `get`, `patch` deployments; `get`, `list` replica sets | Restart a pod, roll a deployment back, find the previous version |
| `kaval-executor` | Kaval's namespace | `get`, `patch` one named ConfigMap (`kaval-removal-orders`) | Hand a removal order to the worker outside the cluster |
| `kaval-collector` | the demo namespace only | `list`, `watch` events; `get` pods | Turn Kubernetes events into signals |
| `kaval-cost` | Kaval's namespace | `get` four named ConfigMaps | Read the bill, the waste scan and the removal results the server leaves there |
| `kaval-prometheus` | the whole cluster | `get`, `list`, `watch` nodes; `get` node metrics | Scrape the node's own metrics. The only cluster-wide rule, and read-only |
| `kaval-chaos-runner` (**staging only**, off in production) | the demo namespace | `create`, `get`, `delete` deployments; `patch` one named deployment; `list` pods; `create` services; `get`, `delete` one named service | Cause the game-day failures |
| `kaval-chaos-runner` (staging only) | Kaval's namespace | `get` one named ConfigMap | Read the on/off switch |

### Who holds nothing

| Workload | Service account | Token in the pod |
|---|---|---|
| `kaval-agent` | default | **No** |
| `kaval-diagnoser` | default | **No** |
| `kaval-ollama` (the model) | default | **No** |
| `kaval-collector-prometheus` | default | **No** |
| `kaval-gateway` | default | Yes (finding C-1) |
| `kaval-migrate` (the database migration job) | default | Yes (finding C-1) |

The `default` account is bound to no role in either namespace, so a token for it can ask the
API server who it is and little else.

### Findings

| # | Finding | Risk | What happens |
|---|---|---|---|
| **C-1** | The gateway and the migration job do not set `automountServiceAccountToken: false`, so each pod carries a token for the `default` account. Four other workloads that need no API access already switch it off. | Low. The token is bound to no role. But the gateway is the one workload that talks to Slack and takes the approval calls, so it is the wrong place to leave a credential nothing uses | Fix in the chart: one line in each template. A chart change, so it goes staging first, then production. Tracked in `KAV-154` |
| **C-2** | The chaos runner can create and delete *any* deployment in the demo namespace. Kubernetes cannot limit `create` to one name | Accepted. Staging only (production's rendered chart has no chaos runner at all: the scan shows none), and only in the demo namespace, which holds nothing but the demo workload | None |
| **C-3** | Flux applies everything in `deploy/gitops/` with full cluster rights. Its Kustomization and HelmRelease name no service account, which is Flux's default for a cluster with one tenant | Accepted. Whoever can merge to `main` can change the cluster: that is what GitOps means, and `main` is protected by required checks and the promotion guard. The stricter pattern (a limited account per Kustomization) is for clusters shared by teams | None |
| **C-4** | The scripts on the server (the cost pull, the registry login refresh, the Secret recreation) use the cluster's own administrator file | Accepted. Root on the server is administrator of the cluster on that server in any single-server Kubernetes. The server has no inbound port; the only way in is AWS Session Manager, which is the laptop's AWS key ([rotation](secret-rotation.md)) | None |
| **C-5** | The image checker (Kyverno) installs its own cluster-wide roles | Accepted. Only its admission controller runs (the background, cleanup and reports controllers are switched off), at a pinned chart version | None |

### What this half did not look at

- **The live cluster.** This is a review of what git says the cluster gets. Because Flux builds
  the cluster from git, the two should be the same; the check that they are
  (`kubectl auth can-i --list` for each account, and a search for any binding git does not know)
  is read-only and belongs to the next production session.
- **k3s's own system roles** and the roles Flux and Kyverno ship. They are the upstream
  defaults at pinned versions.

## In AWS

Not done yet. What it will cover:

1. **IAM Access Analyzer's external-access findings** for the account, read and recorded here.
   The analyzer is free.
2. **Every role Kaval creates,** with its policy read line by line for wildcards: the two servers,
   the publish role GitHub uses, the cost reader, the waste scan, the waste remover, the backup
   watcher, the lab's roles.
3. **The permission boundary.** Phase 6 promised one on the role that removes waste. The role has
   an allow-list of actions; no boundary was attached. Either one is attached, or an ADR says why
   the allow-list is enough.
4. **The laptop's access key:** what it is allowed to do, and whether it can be narrower.

## Repeat

After any change to a template that holds a `Role`, `ClusterRole`, binding or service account,
and once a year with the secret rotation: run [Lab 100](../labs/lab-100-the-cluster-half-of-the-access-review.md)'s
one command per environment and compare with the tables above.
