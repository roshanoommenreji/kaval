# Phase 4 — AWS landing

> **Written from:** theory
> **Lab:** to be written
> **Cost:** first real spend — posture is **paused between sessions**, ~$2.20/mo parked

## Where this sits

Phases 1–3 built a system that works on a single development server that sleeps when nobody is
using it ([ADR-0007](../adr/0007-develop-on-an-aws-dev-server.md)). This phase puts it
somewhere always reachable and rebuilt from Git, and gives it a real approval surface — Slack,
not a phone (mobile is deferred to Phase 9, [ADR-0026](../adr/0026-slack-chatops-and-deferred-mobile.md)).

It unlocks: everything that requires the system to exist independently of you — a real human
approving a real action from Slack, overnight cost data, and the ability to hand someone your
Slack workspace instead of your laptop.

## What we're doing

- Terraform: VPC and subnet, security group, ECR, IAM roles, spot node
- **k3s bootstrapped by cloud-init** — the node builds itself
- **Flux** reconciling the cluster from Git
- **Slack ChatOps** for approvals (`KAV-55`) — an outbound Socket Mode connection, no ingress at all
- **Postgres on its own database server** (`t4g.small`, on-demand) with a separate EBS data volume, daily snapshots and a nightly dump to S3 ([ADR-0008](../adr/0008-production-database-on-its-own-server.md))
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

### Slack ChatOps instead of a load balancer — or a tunnel

An Application Load Balancer costs about $18/month plus per-request charges. For one small
service, that is most of the budget. A Cloudflare Tunnel avoids that cost, but it still means
something on the internet can reach into the cluster — and most companies are deliberately
unwilling to accept that for an internal operations tool, tunnel or not.

**Slack's Socket Mode** avoids the question entirely: the gateway opens an *outbound* WebSocket
connection to Slack at startup, the same direction every other outbound call in this project
already uses. Slack delivers button-click events down that existing connection. Nothing ever
connects *in*.

The properties this gives you:

- **No inbound ports open, and no public hostname at all.** The security group needs no ingress
  rule — the same posture the node already has for everything else.
- Authentication is Slack's own signed connection, not a certificate or a JWT you have to verify
- Survives node replacement the same way the rest of the cluster does: the connection just
  reopens, because it was never addressed *to* this instance
- Free at this scale

The trade is a dependency on Slack and on someone being in the workspace to click the button. For
an internal approval surface, that is a clearly good trade — and it is the actual industry pattern
(PagerDuty, Opsgenie, and plenty of home-grown ChatOps bots work exactly this way), which a
bespoke mobile app, deferred to Phase 9, is not. See
[ADR-0026](../adr/0026-slack-chatops-and-deferred-mobile.md).

### Persistence on an ephemeral node

The node can vanish. Postgres cannot. So they don't live together.

- The database runs on **its own server**, not inside the spot node
  ([ADR-0008](../adr/0008-production-database-on-its-own-server.md)). It's **on-demand**, because a
  database must never be reclaimable at two minutes' notice, and the app's memory spikes can't
  OOM-kill it. Only the app node's security group can reach port 5432, and there's no SSH.
- Its data sits on a **separate EBS volume**, not the root volume, so replacing the database
  server never touches the data
- Daily **EBS snapshots** (Data Lifecycle Manager, keep 7) and a nightly `pg_dump` to **S3** guard
  against volume loss and human error
- EBS is zone-locked, so the database server and its volume stay in one availability zone, next
  to the app node

### Why the database has no idle stop

The dev server stops itself after an hour with nobody connected, because its users are people. A
database's user is the application, all the time. An idle stop would either never fire or pull the
database out from under a running app. So the database stops only **deliberately** (`make down`),
through the **paused-posture nightly stop** (02:00 IST, for a forgotten `make down`), or through
the **$38 hard stop**. Each path snapshots the data volume first.

Stopping an EC2 instance keeps its EBS volumes, so a normal start resumes from the same disk and
there is nothing to restore. Three things keep that true:

- **Graceful shutdown.** An EC2 stop sends an ACPI shutdown. systemd stops Docker, Docker sends
  SIGTERM with a 60 s grace period, and Postgres does a fast shutdown with a checkpoint. If it was
  cut short anyway, WAL crash recovery replays on start.
- **A snapshot before every stop**, tagged `Reason=pre-stop`.
- **A health check on start** (`pg_isready` plus a sanity query). It restores the pre-stop
  snapshot **only if the check fails**. Restoring on every start would be slower and would throw
  away good data.

That gives no data loss and a clean resume. It does **not** give uninterrupted service while
paused, because the app is stopped too. Continuous service is the always-on posture from Phase 6.

**Backups are not verified until you have restored one.** In this project that restore is not a
lab exercise — `make staging-up` performs it on every release, so the backup is verified several
times a month automatically.

### RPO and RTO, stated rather than assumed

Two numbers every system has, and most teams have never written down.

**RPO — recovery point objective.** How much data you can afford to lose, measured backwards from
the failure. It is decided by backup *frequency*. A nightly dump means an RPO of 24 hours: a
failure at 23:00 loses the whole day.

**RTO — recovery time objective.** How long recovery takes, measured forwards. It is decided by
restore *speed*, and it is only real if you have measured it.

This project chose **RPO 24 hours** deliberately — see
[ADR-0005](../adr/0005-data-durability-and-staging-seeding.md) — because the likely failure is
spot reclamation and the EBS volume already survives that. Continuous WAL archiving would give an
RPO of seconds, but costs ~100 MB of a 1.2 GB memory budget and adds a sidecar that can fail
silently, at which point you have a backup that stopped working three weeks ago.

The distinction worth internalising: **the volume protects against instance loss; the dump
protects against volume loss.** They are different failures and one mechanism does not cover both.

| Mechanism | Protects against | Does not protect against |
|---|---|---|
| Database on its own server | App-node spot reclamation, app memory spikes | Loss of the database server itself |
| EBS volume separate from root | Instance replacement or termination | Volume corruption, `DROP TABLE`, AZ loss |
| Nightly `pg_dump` to S3 | Volume loss, human error, AZ loss | The last 24 hours |
| EBS snapshot (daily + before every stop) | Volume corruption, fast rollback, a bad stop | The gap since the snapshot |

Knowing which of your mechanisms covers which failure — and being able to say which failure is
*likely* rather than merely possible — is the difference between a backup strategy and a backup.

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
| **Socket Mode** | Slack's outbound-only WebSocket; the app connects to Slack, never the reverse |
| **EBS** | Network-attached block storage, zone-locked |
| **RPO** | Recovery point objective — how much data you can afford to lose |
| **RTO** | Recovery time objective — how long recovery takes, once measured |
| **`pg_dump`** | Logical Postgres backup; portable, restorable selectively |
| **WAL archiving** | Streaming the write-ahead log for point-in-time recovery |
| **DLM** | Data Lifecycle Manager — AWS's scheduler for EBS snapshots and their retention |
| **Termination protection** | `disable_api_termination`: the instance refuses a terminate call until it's switched off |

## Check yourself

1. What single thing makes a subnet public rather than private?
2. Your pod reports `exec format error`. What happened, and at which earlier step should it have been caught?
3. Why does a security group need only one rule for an outbound-initiated connection?
4. AWS reclaims your node at 3am. Walk through every step of the recovery, and name what would break it.
5. Why is the ASG min 1 max 1, when nothing is scaling?
6. Give three concrete reasons pull-based GitOps is safer than a pushing pipeline.
7. Why Flux rather than ArgoCD here — and would that answer change on an 8 GB node?
8. Your EBS volume is in `ap-south-1a` and the ASG spans three zones. What goes wrong?
9. Which failures does the separate EBS volume protect against, and which does it not? Name the mechanism that covers the rest.
10. Your RPO is 24 hours and your last dump ran at 02:00. The volume dies at 23:00. What exactly have you lost, and which table hurts most?
11. Why does the dev server get an idle stop but the database server doesn't? What stops the database instead, and what does each stop path do first?

## In an interview

**"You ran production on spot instances. Wasn't that risky?"**

> "It was the point. Spot means AWS can take the node with two minutes' notice, so I built for
> that: the database on its own on-demand server, all state on EBS and in S3, all configuration in Git, an ASG that
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
- Slack API documentation — Socket Mode and Block Kit interactivity
