# Lab 66 — The EKS lab, live: two sessions, nine findings, and what was proven

**Phase:** 7 · **Story:** `KAV-102` (story B) · **Time:** two sessions, 2026-10-29/30 (about 6 h 40 m, the second half of it the timer's) and 2026-10-30 (2 h 22 m) · **Cost:** about $2.45 in all

[Lab 65](lab-65-building-the-eks-lab-before-spending-anything.md) wrote everything the live run needs. This lab is the live run: the real cluster was made, the same chart was put on it with no chart edit, the proofs were run, and it was torn down and checked. The commands are in the [runbook](../runbooks/lab-eks.md) (corrected by what happened here). The decisions are in [ADR-0067](../adr/0067-the-eks-lab-is-ephemeral-has-its-own-timer-and-is-proven-clean-by-a-label-sweep.md).

## Prerequisites

- Lab 65 merged; `make lab-plan` shows **45 to add, 0 to change, 0 to destroy** (it was 44 until finding 1 below added one policy)
- `AWS_PROFILE=kaval`, region `ap-south-1`, `kubectl`; Flux is installed from its published manifest
- Your laptop's public address in `infra/envs/lab-eks/terraform.tfvars` (git-ignored), **checked again just before `make lab-up`**: it changed twice across the two sessions

## What was proven

Everything below was run live in session two unless it says otherwise.

| Claim | Proof |
|---|---|
| The servers join and become Ready | Two servers Ready on the first try (after the fix for finding 1) |
| The database runs on the cluster's disk | Postgres healthy on an `xfs` disk (finding 2) |
| The chart is unmodified | `git diff main -- deploy/charts` empty; the only lab-only object is the Flux post-renderer that adds the executor's role label, in the lab's own deploy file |
| Observability parity | Prometheus: 8 of 8 targets up, from the same chart, by values only |
| IRSA works with no stored key | A throwaway pod using the executor's service account read the one proof parameter. The same service account was **refused** on a real production parameter. A pod on the `default` service account got no credentials at all (it does not fall back to the servers' role) |
| The load balancer controller makes exactly one balancer | One Application Load Balancer. `/openapi.json` and `/v1/incidents` answered 200 from the laptop; a pod inside the cluster was timed out (the allow-list holds) |
| Pod Security | The chart **passes `baseline`** (six stateless deployments restarted fine under `enforce=baseline`; the database was left out because its disk can only attach to one server) and a privileged host-network pod was **refused**. The chart **fails `restricted`** (finding 9) |
| Network policies | A pod in the `default` namespace reached the database before the policies and was blocked after. The laptop to load balancer to gateway to database path, Prometheus's scrape and the services all still worked (after finding 6) |
| The timer works (session one) | The lab was left overnight. Servers to zero at 4 h 09 m, load balancer and node group deleted at 6 h 09 m, cluster at 6 h 24 m, target group at 6 h 39 m (CloudTrail and the timer's own log agree) |
| Teardown is clean | 45 resources destroyed; AWS showed 0 clusters, 0 load balancers, 0 networks, 0 servers, 0 disks tagged for the lab. The 24-hour check is `KAV-103` |

## Part 1 — Session one (2026-10-29): the cluster comes up, and the first three surprises

`make lab-up` took 29 minutes (19:02 to 19:31 UTC), the plan applied as saved.

### Finding 1 — The servers could not pull AWS's own add-on images

The nodes stayed `NotReady`. `kubectl -n kube-system get pods` showed `ImagePullBackOff`; the event said `403 ... not authorized to perform: ecr:BatchGetImage`. The servers' role (Lab 65, ADR-0067 section 4) allowed pulling only Kaval's repositories. The network and proxy add-ons come from AWS's own registry in another account, so they were refused.

Fix: a second inline policy on the servers' role that allows pulling **only** `eks/*`, `amazon-k8s-cni*` and `amazon/*` from that registry, and nothing else (no push, no wildcard repository). It was added by hand first to get the nodes up, then written as `aws_iam_role_policy.node_ecr_addons` and a test (`test_the_servers_may_pull_aws_add_on_images_and_only_those_besides_kavals`). That is why the plan became 45. AWS's registry number is read from the proxy add-on's image address and lives in the git-ignored `terraform.tfvars` (`eks_registry_account_id`), never in git.

The hand-made policy had to be removed before `terraform destroy`, or the role could not be deleted. **A thing fixed by hand must reach the code before the teardown.**

### Finding 2 — The chart's database and a new disk's `lost+found`

Postgres crash-looped: its data folder was not empty. The chart mounts the disk straight on Postgres's data directory, and a new `ext4` disk has a `lost+found` folder in it that Postgres refuses. k3s's local disks do not have one, which is why it never showed before.

Fix: a StorageClass `gp3` with `fstype: xfs` (no `lost+found`), and a Flux `force` annotation because a class's parameters cannot be changed in place. A test pins both.

### Finding 3 — A tag the provider takes back

The subnets need two tags from outside (`aws_ec2_tag`) so the load balancer controller can find them. The next `terraform plan` wanted to **remove** them, because the subnet resource owns its tag set. Fix: the AWS provider's `ignore_tags` with the prefix `kubernetes.io/`. A test pins it.

All three went in one pull request (#257). The session was stopped for the night with the cluster up; **the timer deleted it**, which is the timer's first live proof. It left one orphan: the database's disk (made by the disk driver, so unknown to Terraform). It was deleted by hand, then the 38 other resources by `terraform destroy`.

## Part 2 — Session two (2026-10-30): the proofs, and six more findings

`make lab-up` at 18:13 UTC. The fixes from session one held: nodes Ready first try, Postgres healthy.

### Finding 4 — A fresh install over the chart's own database deadlocks

Flux installed the release; the agent, executor and Prometheus poller crashed with "relation does not exist" and Flux removed and retried the release forever. In this lab the chart brings its own Postgres, so the migration is a Helm `post-install` hook. Helm runs a post-install hook only **after every Deployment is ready**, and the agent, executor and poller cannot be ready until the migration has made the tables. Staging and production never meet this: their database is a separate server, migrated before the install.

Fix: `install.disableWait: true` in the lab's own `HelmRelease` (pull request #260). The hook then runs at once and the services recover. The chart is not edited; the underlying weakness is a story (below). A test pins the setting and the reason.

A Flux release that is "Stalled / cannot remediate" needs a nudge to try again: annotate the `HelmRelease` with `reconcile.fluxcd.io/requestedAt` and `reconcile.fluxcd.io/forceAt`.

### Finding 5 — The gateway's health page needs the language model

The load balancer marked the gateway unhealthy. `/healthz` also checks the model, the lab has none, so it answers 503 "degraded" **by design**. The chart's own probes already avoid it. Fix: the Ingress health-check path is `/openapi.json` (pull request #261), which answers 200 whenever the app is up. A test forbids `/healthz` there. The real weakness (one page serving as both "is it alive" and "is it ready for work") is a story.

### Finding 6 — My network policies matched one pod of seven

After applying the policies the gateway returned 500, the database timed out and Prometheus could not scrape. The policies selected pods by `app.kubernetes.io/part-of: kaval`; the chart puts that label on its objects but the **pods** carry only `instance` and `component`. The deny-all rule applied to all seven pods; the allow rules matched one. Fix: select on `instance` (pull request #262), plus a test that every label a policy selects on must appear in the chart's pod-label block, which fails on the old file. Lesson: a policy that "applied" proves nothing; the before-and-after test does.

### Finding 7 — The laptop's address changed, twice

`kubectl` timed out (it looked like a DNS failure). The cluster's API is limited to the laptop's address, which had changed. Fix, in order: update `terraform.tfvars`, `make lab-plan` (it shows **0 to add, 1 to change, 0 to destroy**: only the address list changes), `make lab-up` (in place), then update the ConfigMap `kaval-lab-cluster` (`LAB_ALLOWED_CIDR`) so the load balancer's allow-list follows. Nothing had been applied by the failed calls. The runbook now checks the address before the first step.

### Finding 8 — Once the policies are on, the API server cannot reach the pods

Reading a pod through the Kubernetes API's proxy stopped working: the deny-all rule applies to it too. Use `kubectl port-forward` for checks after the policies go on. (Not a defect; a thing to know.)

### Finding 9 — The chart fails Pod Security `restricted`

All seven pods lack `allowPrivilegeEscalation: false`, dropped capabilities, `runAsNonRoot` and a `seccompProfile`. It passes `baseline`, which is what the lab enforced. Production's k3s enforces neither, so nothing there ever said so. This is a story, fixed on staging first, never patched in the lab.

### Also

- The tag-based sweep reads the Resource Groups Tagging API, which lags behind a deletion: it listed two disks that `describe-volumes` said did not exist. The sweep now confirms each tagged disk, snapshot, address, network card and NAT gateway with its own service before reporting it, and names what it ignored (`KAV-107`, 2026-10-31).
- Reaping by the timer does not repair Terraform's record: after the timer deleted things in session one, `terraform destroy` still had to run.

## Part 3 — The teardown order that works

The first overnight cleanup showed the order matters. Use this one:

1. Suspend the two lab Flux kustomizations (`kaval-lab`, `kaval-lab-hardening`) so nothing is re-created.
2. Delete the Ingress (the controller deletes the load balancer).
3. Delete the `HelmRelease` `kaval-lab` (the database's claim goes, and with it the disk the driver made; this is the step that avoids session one's orphan).
4. Check AWS: **0 load balancers, 0 disks tagged for the lab.**
5. `terraform plan -destroy -out lab-destroy.tfplan` (45 resources), read it, `terraform apply lab-destroy.tfplan`.
6. `make lab-sweep`, and again 24 hours later with `make cost-report`.

## Part 4 — Stories filed from this lab

Each is its own Jira story and roadmap line (Phase 7 follow-ups); none was patched in the lab.

1. **The chart should install over its own database** without the lab's `disableWait`: make the migration safe for a fresh in-cluster Postgres.
2. **The chart's pods need a security context** to pass `restricted`.
3. **Split the gateway's health into "alive" and "ready"** (app up versus model ready).
4. ~~`lab_sweep.py` should confirm a tagged resource exists before reporting it.~~ Done 2026-10-31 (`KAV-107`).

## Done when

- The proofs in the table above were run live, with outputs kept (account numbers masked)
- The runbook, ADR-0067's amendment, the journal and the cost note record what happened
- The chart is unchanged (`git diff main --name-only -- deploy/charts` empty)
- `make lab-sweep` is clean, and clean again 24 hours later (`KAV-103`)

## Cost

| Session | Lab alive | About |
|---|---|---|
| 1 (2026-10-29/30) | 19:03 to 01:42 UTC, ended by the timer | $1.60 |
| 2 (2026-10-30) | 18:13 to 20:35 UTC, ended by hand | $0.85 |
| **Total** | | **about $2.45** (the plan said about $6 for three sessions) |

The control plane is $0.10 an hour, the two servers $0.0224 an hour each, the load balancer about $0.024 an hour. A third session is not needed.
