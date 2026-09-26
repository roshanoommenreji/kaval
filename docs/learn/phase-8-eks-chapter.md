# Phase 8 — EKS chapter

> **Written from:** theory
> **Lab:** to be written
> **Cost:** ~$4 per session, ephemeral — `terraform apply`, prove it, `terraform destroy`

## Where this sits

Phases 3–7 built and proved the system on k3s. This phase stands up real EKS for a few hours,
deploys the *identical* Helm chart, exercises the AWS-specific parts k3s cannot teach, records it,
and destroys it.

It unlocks: the word "EKS" on a resume, truthfully — and, more usefully, an understanding of what a
managed control plane actually buys.

## What we're doing

- `infra/envs/lab-eks` — a real EKS cluster from Terraform
- **IRSA** so the executor gets AWS permissions without static keys
- The **AWS Load Balancer Controller**
- Deploying the same chart with only a different values file
- Screenshots and recording
- `terraform destroy`, verified clean 24 hours later

## Why this way

**Because the portability claim needs proving, not asserting.** [ADR-0002](../adr/0002-k3s-for-always-on-eks-as-a-chapter.md)
argues that k3s teaches ~95% of Kubernetes and that EKS-specific knowledge is a contained gap. This
phase either demonstrates that or exposes it as wishful thinking.

There is a second reason, and it is the more interesting one for a course: **understanding what a
managed service does for you is easier after you have run the unmanaged version.** Someone who has
only used EKS thinks a control plane is a thing you pay for. Someone who has run k3s knows it is
an API server, a scheduler, a controller manager and etcd, and can say precisely what $73/month is
buying.

The rejected alternative — a persistent EKS cluster — costs $73/month before nodes, which is
roughly three times the entire project budget, for knowledge that a few hours of exercise delivers.

---

## Key concepts

### What a managed control plane actually is

The control plane is four things:

| Component | Job |
|---|---|
| **API server** | The only way anything talks to the cluster; authn, authz, admission, persistence |
| **etcd** | Consistent key-value store holding all cluster state |
| **Scheduler** | Decides which node each pod runs on |
| **Controller manager** | Runs the reconciliation loops |

On k3s these run as one process on your node, with SQLite instead of etcd. On EKS, AWS runs them
across three availability zones, handles upgrades, patches, certificate rotation and etcd backups,
and gives you an SLA.

**That is what $73/month buys**: not capability, but somebody else being responsible at 3am when
etcd's disk fills. For a personal project that responsibility is fine to hold. For a company, $73
is nothing against an engineer's time.

Being able to state that trade-off in one sentence is worth more than having used either.

### IRSA — the part k3s genuinely cannot teach

**IAM Roles for Service Accounts.** The problem it solves: a pod needs AWS permissions. The naive
answers are all bad.

| Approach | Why it fails |
|---|---|
| Static keys in a Secret | Long-lived credentials in the cluster; rotation is manual; base64 is not encryption |
| The node's instance role | Every pod on the node gets the same permissions — no isolation |
| **IRSA** | Per-ServiceAccount, temporary, automatically rotated |

How it works, and the mechanism is worth knowing because it comes up:

1. The EKS cluster has an **OIDC identity provider** endpoint, publishing signed tokens
2. You register that provider as trusted in IAM
3. An IAM role's **trust policy** says: allow `sts:AssumeRoleWithWebIdentity` for tokens from this provider whose `sub` claim matches a specific namespace and ServiceAccount
4. The ServiceAccount is annotated with the role ARN
5. EKS projects a signed token into the pod at a known path
6. The AWS SDK finds it, calls `AssumeRoleWithWebIdentity`, and receives temporary credentials it refreshes automatically

The result: **no long-lived credential exists anywhere.** The pod proves its identity with a
short-lived token the cluster signs, and AWS exchanges that for equally short-lived credentials.

For Kaval this matters concretely — the executor needs AWS write permissions for the FinOps
actions, and static keys for a component that acts on model output would be exactly the wrong
design.

The generalisable idea is **workload identity federation**, and it is not AWS-specific. GCP and
Azure have direct equivalents, and the pattern — a workload proves who it is with a signed token
rather than holding a secret — is where the industry has landed.

### Managed node groups

A **managed node group** is an EKS-managed ASG: AWS handles the launch template, the AMI, joining
nodes to the cluster, and graceful draining during updates.

The alternatives, worth knowing by name:

- **Self-managed nodes** — your own ASG, your own bootstrap. What Phase 4 does on k3s.
- **Fargate** — no nodes at all; you pay per pod. Simple, more expensive per unit, and with real constraints (no DaemonSets, no privileged pods).
- **Karpenter** — AWS's node autoscaler, which provisions right-sized nodes on demand rather than scaling fixed groups. Increasingly the default recommendation for variable workloads.

### The AWS Load Balancer Controller

An in-cluster controller that watches Ingress and Service resources and creates real AWS load
balancers to match.

- `Ingress` → an **Application Load Balancer** (HTTP/HTTPS, path and host routing)
- `Service type=LoadBalancer` → a **Network Load Balancer** (TCP, faster, no HTTP awareness)

This is the "cloud controller" idea made concrete, and it is precisely what k3s cannot demonstrate:
a Kubernetes object causing a cloud resource to exist. Understanding this is understanding what
"cloud-native" means in practice.

It also explains the cost trap from Phase 4 — each Ingress can create a billable ALB at ~$18/month.
A team creating Ingresses freely creates load balancers freely. Sharing one ALB across Ingresses
via `alb.ingress.kubernetes.io/group.name` is the fix, and it is a genuinely common oversight.

### CNCF conformance, and what "certified Kubernetes" guarantees

The CNCF runs a **Software Conformance Program**. A distribution passes a large open test suite
(`sonobuoy`) exercising the documented API surface. Passing earns the right to use the name
"Kubernetes."

Both EKS and k3s are certified. What that guarantees:

- The same API objects behave the same way
- The same manifests apply
- `kubectl`, Helm, operators and CRDs work identically

What it does **not** guarantee:

- Cloud integrations — LoadBalancer, storage classes, IRSA
- Performance or scale characteristics
- Which optional components are installed

This is exactly the boundary this phase tests. The chart should apply unchanged; the values file
differs where cloud integration differs. Any *template* change needed for EKS is a defect against
the portability claim, not an acceptable special case.

### Ephemeral environments as a practice

The habit this phase builds is more valuable than the EKS knowledge: **an environment that exists
only while needed, created and destroyed by code.**

Properties worth naming:

- Everything is in Terraform, because nothing survives to be clicked into place
- No configuration drift, because there is no long-lived thing to drift
- Cost is proportional to use
- The teardown path is exercised constantly, so it works

The discipline this demands: **verify the destroy.** `terraform destroy` removes what Terraform
created. It does not remove what Terraform did not create — and the AWS Load Balancer Controller
creates load balancers *outside* Terraform's knowledge. An orphaned ALB after teardown is the
classic version of this, billing $18/month indefinitely.

Which is why the exit gate is not "destroy succeeded" but "`make cost-report` 24 hours later shows
nothing." **A portfolio project that quietly bills forever is an anti-credential.**

---

## Common mistakes

| Mistake | What it costs |
|---|---|
| Static AWS keys in a Kubernetes Secret | Long-lived credentials with manual rotation, in a base64 wrapper |
| Using the node instance role for pod permissions | Every pod on the node inherits them |
| IRSA trust policy without the ServiceAccount in `sub` | Any ServiceAccount in the cluster can assume the role |
| An Ingress per service, unshared | An ALB each, ~$18/month each |
| Assuming `terraform destroy` removes everything | Controller-created load balancers survive and bill |
| Not checking cost the next day | The orphaned resource is found on the statement |
| Template changes to make the chart work on EKS | The portability claim silently becomes false |
| Assuming certified conformance covers cloud integrations | It covers the API surface, not LoadBalancer or storage |

## Glossary

| Term | Meaning |
|---|---|
| **Control plane** | API server, etcd, scheduler, controller manager |
| **etcd** | Distributed consistent key-value store holding cluster state |
| **Managed node group** | EKS-managed ASG with AMI handling and graceful updates |
| **Self-managed node** | A node you provision and bootstrap yourself |
| **Fargate** | Serverless pods; no nodes to manage |
| **Karpenter** | AWS node autoscaler that provisions right-sized nodes on demand |
| **IRSA** | IAM Roles for Service Accounts — per-pod AWS identity |
| **OIDC provider** | The cluster endpoint publishing signed identity tokens |
| **Trust policy** | The IAM policy declaring who may assume a role |
| **`AssumeRoleWithWebIdentity`** | The STS call exchanging a signed token for temporary credentials |
| **Projected token** | Short-lived signed token the kubelet mounts into a pod |
| **Workload identity federation** | The general pattern: prove identity with a token, not a secret |
| **AWS Load Balancer Controller** | Turns Ingress and Service objects into real AWS load balancers |
| **ALB / NLB** | Application (HTTP-aware) / Network (TCP, faster) load balancer |
| **Ingress group** | Annotation letting several Ingresses share one ALB |
| **CNCF conformance** | Test suite a distribution must pass to be called Kubernetes |
| **sonobuoy** | The conformance test runner |
| **Ephemeral environment** | An environment created and destroyed on demand by code |

## Check yourself

1. In one sentence, what does $73/month for an EKS control plane actually buy?
2. Walk through IRSA from pod start to a signed AWS API call. Name every component.
3. Why is the node instance role a bad way to give a pod AWS permissions?
4. An IRSA trust policy omits the ServiceAccount from the `sub` condition. What is now possible?
5. Your chart needs an extra annotation on EKS. What does the project's portability rule require?
6. `terraform destroy` completed cleanly. Why might you still be billed $18/month?
7. What does CNCF conformance guarantee, and name two things it does not?

## In an interview

**"You mention both k3s and EKS. Why both?"**

> "The system's permanent home is k3s on a single spot instance, because an always-on EKS control
> plane is $73 a month before nodes and my ceiling for the whole project was $40 a month. EKS is an
> ephemeral chapter — Terraform up, deploy the identical Helm chart with only a different values
> file, exercise the things k3s genuinely can't teach, then destroy it the same day. The
> interesting one is IRSA: the executor needs AWS write permissions, and static keys in a Secret
> for a component that acts on model output would be exactly the wrong design. IRSA gives it a
> per-ServiceAccount role via OIDC federation, so no long-lived credential exists anywhere. Doing
> k3s first also meant that when I got to EKS I could say what the managed control plane was
> actually buying — it's not capability, it's somebody else being responsible when etcd's disk
> fills. And my exit gate wasn't 'destroy succeeded', it was checking the cost report 24 hours
> later, because the load balancer controller creates ALBs outside Terraform's state and they
> survive a destroy."

The last sentence is the one that signals real operational experience.

## Further reading

- AWS EKS User Guide — *IAM roles for service accounts* (read the trust policy section closely)
- AWS EKS User Guide — managed node groups and Fargate profiles
- AWS Load Balancer Controller documentation — annotations, especially `group.name`
- Karpenter documentation — the provisioning model
- CNCF Software Conformance Program — what the certification covers
