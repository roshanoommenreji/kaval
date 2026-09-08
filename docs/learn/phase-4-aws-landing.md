# Phase 4 — AWS landing

> **Written from:** theory
> **Lab:** to be written
> **Cost:** first real spend — posture is **paused between sessions**, ~$2.20/mo parked

## Where this sits

Phases 1–3 built a system that works on a laptop. This phase puts it somewhere it can be woken by
a phone at 2am.

It unlocks: everything that requires the system to exist independently of you — push notifications,
overnight cost data, and the ability to hand someone your phone.

## What we're doing

- Terraform: VPC and subnet, security group, ECR, IAM roles, spot node
- **k3s bootstrapped by cloud-init** — the node builds itself
- **Flux** reconciling the cluster from Git
- **Cloudflare Tunnel** for ingress — no load balancer
- Postgres on an EBS volume, nightly dump to S3
- `linux/arm64` images
- `make up` / `make down`

## Why this way

**Because the exit gate is "terminate the node and watch it rebuild itself in under five
minutes."** Everything in this phase is shaped by that requirement, and the requirement exists
because the node is a spot instance that AWS can reclaim at any moment.

That constraint turns out to be a gift. A machine you cannot treat as precious forces every piece
of configuration into Git, which is where it should have been anyway.

The rejected alternative was an on-demand instance for stability. It costs roughly $24/month
against $9, consuming the entire budget for a property — uninterrupted uptime — that this project
specifically does not need and arguably benefits from lacking.

---

## Key concepts

### VPC, subnets, route tables, security groups

A **VPC** is a private network you define, with a CIDR block like `10.0.0.0/16`. A **subnet** is a
slice of it bound to one availability zone.

The distinction that matters: **a subnet is public or private according to its route table.** A
route table with a route to an **Internet Gateway** makes it public. That is the entire definition
— there is no "public" flag.

- **Internet Gateway** — free, allows two-way traffic for instances that have a public IP
- **NAT Gateway** — **$32/month plus data**, allows *outbound only* for instances without public IPs

**Security groups** are stateful virtual firewalls on the instance's network interface. Stateful
means a reply to an allowed outbound connection is automatically allowed back in; you write one
rule, not two.

This project uses a **public subnet with a tight security group** rather than a private subnet with
a NAT Gateway. The security posture is very close — the actual control is the security group
either way — and it saves more than the entire monthly budget. Understanding *why* those two
designs are nearly equivalent is worth more than knowing the default architecture diagram.

### Why NAT Gateway costs what it does

It is a managed, horizontally scaled, highly available service billed hourly *and* per gigabyte
processed. It is genuinely useful at scale, where you have dozens of private instances that need
outbound access.

For a single node, it is $32/month to avoid giving one instance a public IP. That is not a
trade-off worth making here, and recognising when the reference architecture is wrong for your
scale is a large part of controlling cloud cost.

### Spot instances and the interruption contract

Spot instances use spare EC2 capacity at a steep discount — typically 60–90% off on-demand. The
contract: **AWS can reclaim the instance with two minutes' notice.**

The notice arrives via instance metadata and as an EventBridge event. Two minutes is enough to
drain a node and finish in-flight work, if you have built for it.

`t4g.medium` is roughly $0.0336/hour on-demand and around $0.0126/hour on spot — about $24/month
versus about $9.

The design response is not to avoid interruption but to make it boring:

- All state in EBS and S3, not on the instance's root volume
- All configuration in Git
- An Auto Scaling Group that replaces the instance automatically
- cloud-init that rebuilds k3s from scratch
- Flux that reconciles the workloads

The result is that AWS chaos-tests the rebuild path continuously, for free — which is the same
argument the product itself makes about deliberate failure injection.

### Auto Scaling Groups as a self-healing primitive

An **ASG** maintains a desired count of instances from a **launch template**. Here it is set to
min 1, max 1, desired 1 — not for scaling, but because an ASG is the simplest thing that notices
an instance is gone and creates another.

It is also the lever the budget hard-stop Lambda pulls: setting desired capacity to zero
terminates the node and stops nearly all spend, while EBS, ECR and S3 persist.

### cloud-init: how a blank machine becomes a cluster

**cloud-init** runs user-supplied configuration on first boot. The user-data script here:

1. Installs k3s
2. Attaches and mounts the EBS data volume
3. Installs Flux and points it at the Git repository
4. Exits

Everything after that is Flux's job.

The property to aim for is that the script is **idempotent and unattended**. There is nobody to
answer a prompt, and it may run again on a replacement instance an hour from now.

### GitOps, and why pull beats push

Traditional CD **pushes**: a pipeline holds cluster credentials and applies changes.

**GitOps pulls**: an agent inside the cluster watches Git and applies what it finds.

The advantages are concrete rather than philosophical:

- **No cluster credentials in CI.** A compromised pipeline cannot reach the cluster.
- **Git is the desired state.** Drift is detected and corrected continuously, not just at deploy time.
- **Recovery is trivial.** A new empty cluster pointed at the repository converges to the correct state on its own — which is precisely the spot-reclamation requirement.
- **The audit trail is `git log`.**

Note that this is the same reconciliation loop from Phase 3, applied one level up: Flux reconciles
the *cluster* against Git the way a controller reconciles *pods* against a Deployment.

### Flux, and why not ArgoCD

Both are mature CNCF GitOps tools. ArgoCD has an excellent UI and a richer feature set. Flux is a
set of controllers with no UI.

The deciding factor here is memory: the node has 4 GB, and roughly 2.8 GB is already committed.
ArgoCD's components want several hundred megabytes; Flux wants around a hundred. On a 4 GB box
that difference is decisive.

It is a good example of a constraint making a decision that would otherwise be a preference.

### ECR, and the arm64 trap

**ECR** is AWS's container registry. Private, IAM-authenticated, first 500 MB free.

The trap that costs people an afternoon: **`t4g` instances are ARM (Graviton), and a container
image built on an x86 laptop will not run on them.** The build succeeds. The push succeeds. The
pod fails with `exec format error`, which does not obviously mean "wrong CPU architecture."

The fix is to build explicitly for the target:

```bash
docker buildx build --platform linux/arm64 -t <repo>:tag .
```

This project builds arm64 from Phase 1 precisely so this is never discovered late. The general
lesson: **an image is architecture-specific, and the failure surfaces at runtime on a different
machine, which is the worst possible time.**

### Cloudflare Tunnel instead of a load balancer

An Application Load Balancer costs about $18/month plus per-request charges. For one small
service, that is most of the budget.

A **Cloudflare Tunnel** runs a lightweight daemon in the cluster that establishes an *outbound*
connection to Cloudflare. Traffic arrives at Cloudflare and is delivered down that existing
connection.

The properties this gives you:

- **No inbound ports open.** The security group needs no ingress rule at all.
- HTTPS terminated by Cloudflare, certificate managed
- A stable hostname regardless of the node's IP — which matters when spot replaces the instance
- Free at this scale

The trade is a dependency on Cloudflare and a small latency addition. For a personal project with
a mobile client, that is a clearly good trade, and being able to explain *why* it is good here and
would not be for a high-throughput API is the useful part.

### Persistence on an ephemeral node

The node can vanish. Postgres cannot.

- The database lives on a **separate EBS volume**, not the root volume, so it survives instance termination and is reattached by the replacement
- A nightly `pg_dump` to **S3** guards against volume loss and human error
- EBS is zone-locked, so the ASG must be constrained to the volume's availability zone

**Backups are not verified until you have restored one.** That restore belongs in a lab, not in a
plan.

---

## Common mistakes

| Mistake | What it costs |
|---|---|
| Accepting the default VPC wizard | A NAT Gateway you did not ask for, at $32/month |
| Building images on an x86 laptop for a Graviton node | `exec format error` at runtime, hours of confusion |
| Putting state on the instance root volume | Lost at the next spot reclamation |
| Treating spot interruption as an outage | Fighting the platform rather than designing for it |
| ASG unconstrained by availability zone | New instance in a zone that cannot attach the EBS volume |
| Cluster credentials in CI | The thing GitOps exists to avoid |
| Assuming a backup works | It does not until you have restored it |
| Reaching for an ALB out of habit | $18/month for something a free tunnel does better here |

## Glossary

| Term | Meaning |
|---|---|
| **VPC** | A logically isolated private network you define |
| **CIDR** | Address range notation, e.g. `10.0.0.0/16` |
| **Subnet** | A slice of a VPC, bound to one availability zone |
| **Route table** | Rules deciding where traffic goes; defines public vs private |
| **Internet Gateway** | Free VPC component enabling two-way internet traffic |
| **NAT Gateway** | Managed outbound-only internet access; ~$32/month |
| **Security group** | Stateful instance-level firewall |
| **Availability zone** | An isolated datacentre within a region |
| **Spot instance** | Spare capacity at a discount, reclaimable on two minutes' notice |
| **On-demand** | Full-price, uninterrupted instance |
| **Auto Scaling Group** | Maintains a desired instance count from a launch template |
| **Launch template** | The instance specification an ASG creates from |
| **cloud-init** | Runs configuration on a machine's first boot |
| **User data** | The script cloud-init executes |
| **GitOps** | An in-cluster agent pulls desired state from Git |
| **Flux** | Lightweight GitOps controllers, no UI |
| **Drift** | Divergence between declared and actual state |
| **ECR** | AWS container registry |
| **buildx** | Docker's multi-architecture builder |
| **Graviton** | AWS ARM processors; the `t4g` family |
| **`exec format error`** | The kernel refusing a binary built for another architecture |
| **Cloudflare Tunnel** | Outbound-only connection exposing a service without open inbound ports |
| **EBS** | Network-attached block storage, zone-locked |

## Check yourself

1. What single thing makes a subnet public rather than private?
2. Your pod reports `exec format error`. What happened, and at which earlier step should it have been caught?
3. Why does a security group need only one rule for an outbound-initiated connection?
4. AWS reclaims your node at 3am. Walk through every step of the recovery, and name what would break it.
5. Why is the ASG min 1 max 1, when nothing is scaling?
6. Give three concrete reasons pull-based GitOps is safer than a pushing pipeline.
7. Why Flux rather than ArgoCD here — and would that answer change on an 8 GB node?
8. Your EBS volume is in `ap-south-1a` and the ASG spans three zones. What goes wrong?

## In an interview

**"You ran production on spot instances. Wasn't that risky?"**

> "It was the point. Spot means AWS can take the node with two minutes' notice, so I built for
> that: all state on a separate EBS volume and in S3, all configuration in Git, an ASG that
> replaces the instance, cloud-init that installs k3s, and Flux that reconciles the workloads.
> The exit gate for the phase was terminating the node by hand and watching it come back in under
> five minutes. The result is that AWS chaos-tests my recovery path continuously and for free —
> which is the same argument the product makes about deliberate failure injection. It also cost
> $9 a month instead of $24. I'd make a different call for something with a real uptime SLA, but
> then I'd also be paying for multi-AZ, and the honest version of that trade-off is the interesting
> conversation."

Naming the exit gate is what makes this credible — it is a tested property, not an intention.

## Further reading

- AWS VPC User Guide — route tables and the public/private distinction
- AWS EC2 documentation — *Spot Instances* and interruption notices
- cloud-init documentation — modules and user-data formats
- Flux documentation — the GitOps Toolkit controllers
- Docker documentation — *Multi-platform images* and buildx
- Cloudflare documentation — Zero Trust tunnels
