# Access review: who can do what

A least-privilege review asks of every identity: what is it allowed to do, and is that more than
its job needs? This page is the evidence for `KAV-154`. It has two halves.

| Half | Status |
|---|---|
| **Inside the cluster** (Kubernetes roles and bindings) | **Done 2026-11-15**, from the chart as it is rendered for production and staging |
| **In AWS** (IAM roles, IAM Access Analyzer, the permission boundary, the laptop's key) | **Done 2026-11-16**, read-only, against the live account. Five findings, decided 2026-11-16 ([ADR-0101](../adr/0101-the-aws-access-review-and-what-it-changes.md)) |

How to repeat the cluster half: [Lab 100](../labs/lab-100-the-cluster-half-of-the-access-review.md). The AWS half: [Lab 101](../labs/lab-101-the-aws-half-of-the-access-review.md).

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
| **C-1** | The gateway and the migration job do not set `automountServiceAccountToken: false`, so each pod carries a token for the `default` account. Four other workloads that need no API access already switch it off. | Low. The token is bound to no role. But the gateway is the one workload that talks to Slack and takes the approval calls, so it is the wrong place to leave a credential nothing uses | Fix in the chart: one line in each template. A chart change, so it goes staging first, then production. Tracked in `KAV-154`. **Fixed in the chart 2026-11-17 and live in production 2026-11-18 (`KAV-162`, [Lab 102](../labs/lab-102-taking-away-a-token-nothing-uses.md)), with a test over every environment; live once the release is promoted.** The chart's own Postgres (local and the EKS lab only) had the same gap and got the same line |
| **C-2** | The chaos runner can create and delete *any* deployment in the demo namespace. Kubernetes cannot limit `create` to one name | Accepted. Staging only (production's rendered chart has no chaos runner at all: the scan shows none), and only in the demo namespace, which holds nothing but the demo workload | None |
| **C-3** | Flux applies everything in `deploy/gitops/` with full cluster rights. Its Kustomization and HelmRelease name no service account, which is Flux's default for a cluster with one tenant | Accepted. Whoever can merge to `main` can change the cluster: that is what GitOps means, and `main` is protected by required checks and the promotion guard. The stricter pattern (a limited account per Kustomization) is for clusters shared by teams | None |
| **C-4** | The scripts on the server (the cost pull, the registry login refresh, the Secret recreation) use the cluster's own administrator file | Accepted. Root on the server is administrator of the cluster on that server in any single-server Kubernetes. The server has no inbound port; the only way in is AWS Session Manager, which is the laptop's AWS key ([rotation](secret-rotation.md)) | None |
| **C-5** | The image checker (Kyverno) installs its own cluster-wide roles | Accepted. Only its admission controller runs (the background, cleanup and reports controllers are switched off), at a pinned chart version | None |

### What this half did not look at

- **The live cluster.** This is a review of what git says the cluster gets. Because Flux builds
  the cluster from git, the two should be the same; the check that they are
  (`kubectl auth can-i --list` for each account, and a search for any binding git does not know)
  is read-only and belongs to the next production session. **Done 2026-11-16 (`KAV-147`):** the live cluster matched this review, and C-1 was confirmed live.
- **k3s's own system roles** and the roles Flux and Kyverno ship. They are the upstream
  defaults at pinned versions.

## In AWS

Read on **2026-11-16** with `make iam-review` (read-only: it only lists and reads; it changes nothing
and prints no account number). Staging was not running, so what was read is production's set of roles.
Staging builds its roles from the same templates, so the findings hold for both; the scan is repeated
at the next staging session.

### The result in five lines

- **Kaval owns 13 roles.** None carries a permission boundary. Only two of them change anything
  (the waste remover and the budget brake); the rest read, or write one named parameter.
- **No role has a wildcard action.** Nine statements have a resource of `*` for actions AWS cannot
  narrow (below), and one has it for actions AWS can: a real finding (A-2).
- **IAM Access Analyzer does not exist in the account** (A-1). There was nothing to read, so the
  outside-access review cannot be done until it is switched on. It is free. **Switched on
  2026-11-18 (`KAV-160`, [Lab 105](../labs/lab-105-a-watcher-for-what-outsiders-can-reach.md)):
  three findings, all expected (the two GitHub roles of this repository and the other project's one,
  each locked to one repository and branch); nothing public, nothing shared with another account.**
- **The two roles GitHub uses can only be taken by this repository, on `main`** (no finding).
- **The owner's laptop key is a full administrator, with no second factor** (A-4). Accepted for now,
  with the 90-day change ([ADR-0100](../adr/0100-secrets-are-changed-yearly-and-the-laptop-key-every-90-days.md))
  as the cover.

### Who holds what (the 13 roles)

| Role | Who can take it | What it may do | Changes things? |
|---|---|---|---|
| `kaval-prod-node` | the production server | Pull Kaval's five images; read its own named secrets (database passwords, Slack tokens); write one named parameter (removal orders); read and write the backup bucket; put one named metric; borrow the cost reader | Writes only the orders parameter and its own backups |
| `kaval-prod-database` | the database server | Read its own two named parameters | No |
| `kaval-prod-cost-reader` | only `kaval-prod-node` | `ce:GetCostAndUsage` and nothing else | No |
| `kaval-prod-waste-scan` | the scan function | Look at disks, snapshots, servers, addresses | No |
| `kaval-prod-waste-removal` | the removal function | Back up, mark and delete **a Kaval disk that is not Terraform's and carries the marker**; delete a Kaval copy that is not Terraform's; read the orders and write the result | **Yes: the one that deletes real things** |
| `kaval-budget-hard-stop` | the brake function | Scale an auto-scaling group to zero; stop servers carrying the `Project` label; snapshot Kaval disks | **Yes: the brake** |
| `kaval-ci-publish` | this repository, on `main` only | Push to Kaval's own image repositories | Pushes images |
| `kaval-backup-watch` | this repository, on `main` only | Look at backups and the server's history | No |
| `kaval-ci-plan` (`KAV-89`, applied 2026-11-18) | this repository, from a pull request only | Read two Terraform records, one Parameter Store folder and a few lookups; no write | No |
| `kaval-prod-dlm` | the snapshot scheduler | AWS's own managed policy for scheduled snapshots | Takes snapshots |
| `kaval-devbox` | the dev server | AWS's managed Session Manager policy | No |
| Three scheduler roles (waste scan, waste removal, the nightly auto-stop) | the scheduler | Start one named function each | No |

The two AWS-managed policies (`AmazonSSMManagedInstanceCore`, `AWSDataLifecycleManagerServiceRole`)
are AWS's text, not read line by line here.

### Why a resource of `*` is sometimes unavoidable

| Action | Where | Why it cannot name a resource |
|---|---|---|
| `ec2:Describe*` (servers, disks, snapshots, addresses) | scan, remover, brake, node | AWS lists these without letting a policy limit them to one resource. They only read |
| `autoscaling:DescribeScalingActivities` | backup watcher | Same: a read |
| `ecr:GetAuthorizationToken` | publish role, node | The registry login token is not a thing in a repository. It gives a short login only; what can be pushed or pulled is limited separately, per repository |
| `ce:GetCostAndUsage` | cost reader | The bill is not a resource |
| `cloudwatch:PutMetricData` | node | Allowed, but limited by a condition to the one `Kaval/Heartbeat` namespace |

### Findings

| # | Finding | Risk | What happens |
|---|---|---|---|
| **A-1** | **No IAM Access Analyzer in the account** (`ap-south-1` and `us-east-1` both checked). The outside-access findings the review calls for cannot be read | Medium for visibility: a role or bucket shared with an outside account would not be noticed | Switch on the free analyzer, in the `state` folder, then read the findings. Story **`KAV-160`**: **done 2026-11-18** ([Lab 105](../labs/lab-105-a-watcher-for-what-outsiders-can-reach.md)) |
| **A-2** | **The budget brake may scale down any auto-scaling group in the account**, not only Kaval's: the policy names none. The other project has none today (it runs on a different service), so nothing is wrong now | Low today, and it is the brake: the one job that must never touch what it should not | Limit it to groups carrying Kaval's label, then prove the brake still stops staging's group. Story **`KAV-161`**. **Built and live 2026-11-17** ([Lab 103](../labs/lab-103-a-ceiling-over-the-two-roles-that-act.md)): applied to production's record, fired for real, it stopped staging's group and left production's alone |
| **A-3** | **No role has a permission boundary.** Phase 6 promised one on the waste remover. Its allow-list is already tight (tag conditions; a delete needs the marker), but nothing sits above it to stop a later mistaken edit from widening it | Low today. A boundary is a ceiling: even a widened policy cannot go past it | Recommended: a boundary on the two roles that change things (the remover and the brake), not on all 13. [ADR-0101](../adr/0101-the-aws-access-review-and-what-it-changes.md); story **`KAV-161`**. **Built 2026-11-17**: both boundaries are in the modules and tested against IAM's simulator. **Both are live** (2026-11-17: the brake's, then production's remover's; `make iam-review` shows "yes" on both; staging's remover was proved live the same morning) |
| **A-4** | **The laptop key is `AdministratorAccess`, one key, no second factor on the user.** It is the one credential that can do anything Terraform can | Accepted for now. The key is changed every 90 days (done 2026-11-16; next due by 2027-02-14), it lives in one file on one laptop, and nothing in the cluster or on the servers holds it | The stronger pattern (a login that expires and needs a second factor) is parked in [future scope](../future-scope.md#fs-15--a-laptop-login-that-expires-and-needs-a-second-factor) (FS-15). Not built for v1 |
| **A-5** | The remover's snapshot delete needs the `Project` label but not the `Env` label, so production's remover could delete a staging copy of the same project | Very low: staging and production are never up with orders for each other, and the remover re-checks AWS itself | Add the `Env` condition in the same story as A-2 and A-3 (**`KAV-161`**). **Built 2026-11-17**, as a refusal of another environment's label rather than a "must match", so the worker's rule for old unlabelled copies keeps working ([ADR-0101](../adr/0101-the-aws-access-review-and-what-it-changes.md) amendment); live once applied |

No finding for the two GitHub roles: each can only be taken by a login from this repository's `main`
branch, by its fixed numeric identifiers (not its name, which could be re-registered), with the right
audience.

### What this half did not look at

- **The other project's seven roles** (`stock-trader-...`). They share the account and the laptop key,
  but they are not Kaval's. The analyzer, once on, will see them too.
- **Roles that exist only while staging is up.** The templates are the same; the scan is repeated at
  the next staging session.
- **The lab's EKS roles.** They exist only during a lab session (built and destroyed by
  `infra/envs/lab-eks`), so a scan finds none between sessions.

## Repeat

After any change to a template that holds a `Role`, `ClusterRole`, binding or service account,
and once a year with the secret rotation: run [Lab 100](../labs/lab-100-the-cluster-half-of-the-access-review.md)'s
one command per environment and compare with the tables above. For the AWS half run `make iam-review`
([Lab 101](../labs/lab-101-the-aws-half-of-the-access-review.md)) after any change to a role, and at the
next staging session.
