# ADR-0067 — The EKS lab is ephemeral, deletes itself on a timer, and is proven clean by a sweep of labels

- **Status:** Accepted (plan approved by Roshan 2026-10-29). Built in `KAV-101`; the live run is `KAV-102`; the 24-hour check is `KAV-103`
- **Date:** 2026-10-29
- **Deciders:** Roshan
- **Story:** `KAV-101` (Phase 7, story A) · builds on [ADR-0002](0002-k3s-for-always-on-eks-as-a-chapter.md) (k3s permanent, EKS as a chapter), [ADR-0004](0004-environment-strategy-and-promotion.md) (`lab-eks` is not a promotion tier), [ADR-0029](0029-staging-parks-itself-when-idle.md) (the self-stop pattern), [ADR-0064](0064-the-spending-brake-names-every-server-group-and-is-proven-on-staging.md) (the brake)

## Context

Phase 7 proves that the chart production runs also runs on real Amazon EKS with a different values file and no template edit, and exercises what k3s cannot teach: a role per pod (IRSA), a controller that makes real load balancers, a managed control plane, and the stricter rules EKS-grade clusters enforce.

The cost shape is unlike everything else in the project. EKS's control plane costs **$0.10 an hour (about $73 a month) from the moment it exists**, whether or not anything runs on it (checked against the AWS price list, ap-south-1, 2026-10-28). A Kubernetes version past standard support costs **$0.50 an hour**. A load balancer is about 2.4 cents an hour. Left running for a month, the lab alone would exceed the whole $50 ceiling. And `terraform destroy` has a known gap: the load balancer controller and the disk driver create load balancers and disks that Terraform has no record of, and those survive a destroy and keep billing.

## Decision

### 1. One real cluster, for one session, made and destroyed by code

`infra/envs/lab-eks` (module `eks-lab`): a control plane, one managed node group of two `t4g.medium` On-Demand servers (arm, like the images), in public subnets with no NAT gateway, with the add-ons the chart needs. Kubernetes **1.35** (the version CI already checks the chart against; in standard support to 2027-03-27). The cluster's API is reachable only from the laptop's address (the module refuses `0.0.0.0/0`). Sign-in is by EKS access entries, not the old `aws-auth` ConfigMap. Cost is about **$1.2 to $2 for a six-hour session**; the roadmap's $4 is kept as the cap.

| Alternative | Why not |
|---|---|
| A persistent EKS cluster | $73 a month before servers: more than the whole ceiling for knowledge a few hours delivers ([ADR-0002](0002-k3s-for-always-on-eks-as-a-chapter.md)) |
| EKS Auto Mode or Fargate | Fewer things to learn about, which is the opposite of the point; Fargate also cannot run everything the chart does |
| Spot servers | A capacity shortage would stall a session that has a timer; the saving is cents |
| `eksctl` or the console | Nothing in the repo would record what was made or let a stranger repeat it |

### 2. A timer deletes it if a person forgets (`infra/modules/lab-reaper`)

A small scheduled function (every 15 minutes) works only from the cluster's age, which AWS records at creation: at **4 hours** it scales the servers to zero; at **6 hours** it deletes the load balancers the controller made, then the node group, then the cluster, then the loose security groups and target groups. Each pass does the next possible step, so a step that fails (a security group still held by a load balancer that is being deleted) is retried. Its IAM role can touch only the named cluster and what carries that cluster's label. The decision is a pure function with unit tests, including the cases where it must do nothing. An optional alarm rings when the timer errors or goes silent for an hour.

Why a function and not Terraform on a schedule: Terraform cannot run from inside AWS without a very broad role ([ADR-0029](0029-staging-parks-itself-when-idle.md)). The function deletes outside Terraform, so `make lab-down` is still run afterwards; it finds the cluster gone and removes the rest.

The nightly spending brake ([ADR-0064](0064-the-spending-brake-names-every-server-group-and-is-proven-on-staging.md)) does **not** cover this lab: it scales named server groups, and an EKS node group's group has a generated name; the control plane is not a server at all. The timer is the only automatic safety besides the $48 budget alert, which is why it is tested on its own before the cluster is trusted to it.

### 3. The chart is installed unmodified; the one lab-only thing lives in the lab's own deploy file

Flux installs the chart from the same path as production, from `deploy/gitops/lab-eks`. The executor's pod needs the IRSA label (`eks.amazonaws.com/role-arn`) on its service account. The chart has no setting for it and the portability rule forbids editing the chart for the lab, so the lab's `HelmRelease` adds the label to the rendered result with a **post-renderer** patch. The role ARN, the registry host, the VPC id and the laptop's address (account- or person-specific) come from ConfigMaps made by hand when the lab comes up, never committed. A test fails if the chart mentions the lab or the label, if the two copies of the lab's values differ, or if any committed file holds an account number.

| Alternative | Why not |
|---|---|
| Add an optional `serviceAccount.annotations` setting to the chart | Small and off everywhere else, but it is a template edit made for one environment: the exact thing the portability claim says is a defect |
| Annotate the service account by hand after install | Drift nobody can see in git; the next reconcile may revert it |

### 4. IRSA with the smallest honest proof

Each pod that needs AWS gets its own role, trusted by the cluster's identity endpoint for exactly one namespace and one service account (`sub`) and one audience. Today the executor reaches AWS through no API of its own (the removal worker is a separate job, [ADR-0063](0063-waste-is-removed-in-steps-by-a-separate-worker-and-only-with-approval.md)), so the lab does not invent a fake action: the executor's role may **read one named parameter**, and the proof is a throwaway pod using that service account reading it, then a pod using a different service account being **refused**. The load balancer controller, the disk driver and the network add-on get their own roles the same way; the servers' role carries only what a server needs (join the cluster, pull Kaval's images, Session Manager).

### 5. Load balancers: one, shared, and limited to the laptop

The controller makes one Application Load Balancer for the gateway; `group.name` makes Ingresses share it ($0.024 an hour, not one each), and `inbound-cidrs` limits it to the laptop's address. The Ingress is the lab's own object (the chart has none; production reaches the gateway through a tunnel). It is removed before destroy.

### 6. Strict rules are applied in a second step and what they break is recorded, not patched

First the chart runs on EKS with nothing extra and that is recorded. Then Pod Security Standards (`warn`/`audit` at `restricted` from the start; enforced by `kubectl label` in the run) and network policies (a hand-applied switch, `hardening-sync.yaml`, deliberately outside the lab's own Flux object) go on. Anything they break becomes its own story, fixed on k3s staging first, never quietly patched in the lab.

### 7. "Clean" is proven by a sweep of labels, not by memory

`make lab-sweep` (`scripts/ops/lab_sweep.py`, read-only) looks for anything carrying `Env=lab-eks`, the controller's cluster label or the lab's name, and splits findings into **billing** (cluster, servers, load balancers, disks, snapshots, addresses, NAT gateways, network interfaces) and **leftover** (network, roles, the timer). It exits non-zero on any finding. It is run after `make lab-down` and again 24 hours later together with `make cost-report`: that second run, not "destroy succeeded", is the exit gate. The disks the disk driver makes carry no Terraform label of their own, so the driver is told to put the three required labels on each one.

## Consequences

- Terraform's `lab-eks` state lives in the same private bucket as the other environments (`scripts/ops/tf-init.sh lab-eks`); `tag_policy` knows the `lab-eks` environment, and the waste scan and the removal worker's copies of it are kept equal by their existing test. The removal worker still acts only on its own environment's resources.
- The lab adds nothing to the always-on bill and about $5 to $6 for three runs, replacing the old $15 placeholder line in the budget plan.
- The Kubernetes version and upgrade policy are written down in the runbook: lab clusters are always created on a version in standard support and never upgraded in place (they are destroyed instead).
- The AWS Load Balancer Controller is pinned (chart and controller 3.5.0), and its published IAM policy is committed unedited, so an upgrade is a re-download and a diff.

## What would make us revisit

A second ephemeral environment of this cost class would reuse the timer and the sweep. If a future phase needs EKS to stay up for a day, the timer's six hours become a variable with a firm cap (already limited to 12), and the cost note is redone.

## Amendment 2026-10-31 — what the live run changed (`KAV-102`, [Lab 66](../labs/lab-66-the-eks-lab-live-two-sessions-and-what-they-found.md))

The decisions above held: one cluster for one session, the timer, the unmodified chart, IRSA with one named parameter, one shared load balancer limited to the laptop, and the sweep. The timer was proven live (servers to zero at 4 h 09 m, everything deleted by 6 h 39 m). Two sessions cost about $2.45 against the plan's $6 for three. Six details were wrong or missing and are now different:

1. **The servers' role may also pull AWS's own add-on images** (`eks/*`, `amazon-k8s-cni*`, `amazon/*`, pull-only, no wildcard repository). Section 4 said it carried "only what a server needs"; the add-ons are what a server needs first. The plan is 45 resources, not 44.
2. **The database's disk is `xfs`.** The chart mounts a disk on Postgres's data directory and `ext4`'s `lost+found` stops it. A StorageClass with `xfs` and a Flux `force` annotation.
3. **The provider ignores the `kubernetes.io/` subnet tags** that are added from outside, or each apply strips them.
4. **The lab's release does not wait for the services during install** (`install.disableWait`). With the chart's own database the migration is a post-install hook that Helm runs only after every Deployment is ready: a deadlock. This is a setting of the lab's own deploy file, not a chart edit; the weakness in the chart is a story. Staging and production, whose database is a separate server migrated before install, never meet it.
5. **The load balancer health-checks `/openapi.json`**, not `/healthz`: the latter also needs the language model, which the lab does not have.
6. **Network policies select on `app.kubernetes.io/instance`**, the label the chart's pods really carry (not `part-of`, which only its objects carry). A test now checks every selected label against the chart's pod-label block.

Added to the procedure: the laptop's address is checked immediately before `make lab-up` (it changed twice in two sessions) and updated in two places when it changes; teardown suspends Flux, deletes the Ingress, **deletes the HelmRelease so the database disk goes with it**, checks AWS shows no load balancer or disk, and only then runs `terraform plan -destroy` and applies it (the order is in the [runbook](../runbooks/lab-eks.md)). The sweep reads a label index that lags behind deletions; it can list a thing that is already gone, so since 2026-10-31 (`KAV-107`) it confirms each tagged disk, snapshot, address, network card and NAT gateway with its own service first, and names what it ignored.

The chart fails Pod Security `restricted` (no security context on any of its seven pods) and passes `baseline`. That is a finding against the chart, not the lab, filed as its own story; it is why the portability claim is "the same chart runs with no template edit", which was proven, rather than "the same chart is production-grade for EKS", which it is not yet.
