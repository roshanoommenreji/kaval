# ADR-0002 — k3s for the permanent cluster, EKS as an ephemeral chapter

- **Status:** Accepted
- **Date:** 2026-08-22
- **Deciders:** Roshan

## Context

The project needs Kubernetes for two different reasons, and they pull in opposite directions.

**Reason one: the product must be alive.** Half of Kaval is a FinOps agent, and AWS Cost Explorer
reports at daily granularity with roughly 24 hours of lag. An account that only exists during work
sessions generates nothing for the agent to analyse. The reliability half has a similar need — a
2am push notification is only meaningful if something is running at 2am. "It has been running
continuously since February" is also a credibility claim in an interview that nothing else
substitutes for.

**Reason two: the resume needs the word EKS.** To an engineer conducting the interview, k3s is
fine and arguably better — running your own control plane demonstrates you understand what a
managed service is doing for you. To an applicant tracking system or a non-technical recruiter,
"EKS" is the token that matches the job description and "k3s" is noise.

The constraint that makes this a real decision is the **$25/month ceiling**. A conventional
always-on EKS setup does not come close:

| Component | $/mo |
|---|---|
| EKS control plane | 73 |
| NAT Gateway | 32 |
| Application Load Balancer | 18 |
| 2 × t3.medium nodes | 30 |
| **Total** | **~153** |

That is six times the budget, and the control plane is not even the largest line.

Three options were considered:

1. **EKS only, destroyed between sessions.** Purest AWS experience and the strongest keyword. But
   the system is dead between sessions, which removes the overnight cost data, the real push
   alerts, and the ability to show anyone the app on demand — that is, most of the product.
2. **k3s only.** Cheapest and simplest, all effort goes into the product. But no IRSA, meaning the
   executor authenticates to AWS with static keys, which is genuinely worse practice; and the
   resume says "Kubernetes" rather than "EKS".
3. **Both.**

## Decision

Run **k3s on a single `t4g.medium` spot instance** as the permanent home of the system, and treat
**real EKS as a dedicated ephemeral chapter** (Phase 8) that stands up via Terraform, deploys the
*identical* Helm chart, exercises IRSA and the AWS Load Balancer Controller, gets recorded, and is
destroyed the same day.

k3s is a CNCF-certified conformant Kubernetes distribution — the same conformance suite AWS must
pass to call EKS "Kubernetes". `kubectl`, manifests, Helm, HPA, RBAC, CRDs and operators are all
identical. What differs is the AWS-specific glue, which is precisely what Phase 8 is for.

Environments differ **only by Helm values files**. If `lab-eks` ever needs a template change that
`prod-k3s` does not, the portability claim is false and that is a defect, not a workaround.

Additionally: the node runs on **spot**. AWS may reclaim it with two minutes' notice, so the node
bootstraps from cloud-init and Flux reconciles the entire cluster from Git in under five minutes.
The infrastructure is therefore chaos-tested by Amazon, continuously, at no cost — which is the
same thesis the product itself is arguing.

## Consequences

**Easier.** The system is genuinely alive for about $11/month. The FinOps agent gets real data.
Both "Kubernetes" and "Amazon EKS" can be written on a resume truthfully. Chart portability
becomes a demonstrated property rather than an assertion — a stronger claim than either platform
alone would support.

**Harder.** Two environments to keep working. The k3s control plane is now our problem: an
upgrade, a full disk, or a corrupted etcd/SQLite is ours to fix. That is more learning, but it is
also more time.

**Cost.** ~$9.20/month for the spot instance plus ~$2 storage, against ~$153 for the conventional
equivalent. EKS chapter sessions run about $4 each.

**Risk accepted.** Spot reclamation can interrupt a demo. Mitigated by the rebuild automation,
which is Phase 4's exit gate — and a reclamation happening live on camera would be good content
rather than a disaster.

**Revisit if:** the k3s control plane consumes more than roughly two sessions of unplanned
maintenance, or if an employer target makes managed-Kubernetes experience specifically decisive.
In either case, a persistent EKS cluster becomes worth its $73/month and this ADR gets superseded.
