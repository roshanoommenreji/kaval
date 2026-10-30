# Lab 65 — Building the EKS lab before spending anything: the code, the timer, the sweep and the plan

**Phase:** 7 · **Story:** `KAV-101` (story A) · **Time:** about 3 hours · **Cost:** $0 (nothing is created in AWS; the plan only reads)

Real EKS costs $0.10 an hour from the moment it exists. This lab writes and tests everything the live run needs, so that the live run (`KAV-102`, [runbook](../runbooks/lab-eks.md)) spends its hours on proving things, not on writing code with the meter running. The decisions are in [ADR-0067](../adr/0067-the-eks-lab-is-ephemeral-has-its-own-timer-and-is-proven-clean-by-a-label-sweep.md).

## Prerequisites

- The repository on a short branch (`infra/KAV-101-...`), Terraform 1.11 or later, `helm`, Python with the project's `.venv` on the path
- `AWS_PROFILE=kaval` (only the final read-only plan needs AWS)

## Part 1 — Check what real EKS costs, before writing a line

```bash
aws pricing get-products --region us-east-1 --service-code AmazonEKS \
  --filters Type=TERM_MATCH,Field=usagetype,Value=APS3-AmazonEKS-Hours:perCluster      # 0.10 an hour
aws pricing get-products ... Value=APS3-AmazonEKS-Hours:extendedSupport                # 0.50 an hour
aws eks describe-cluster-versions --query 'clusterVersions[].{v:clusterVersion,s:status,end:endOfStandardSupportDate}'
```

Found on 2026-10-28: the control plane is $0.10 an hour; a version past standard support is **$0.50**, five times more; `t4g.medium` is $0.0224 an hour; 1.35 is in standard support to 2027-03-27 and is the version CI already renders the chart against. That fixed the version and the budget: about $1.2 to $2 for six hours.

## Part 2 — The cluster, as code (`infra/modules/eks-lab`, `infra/envs/lab-eks`)

- The existing `network` module makes the VPC (10.62.0.0/16, public subnets, no NAT gateway). The subnets get the two labels the load balancer controller needs by `aws_ec2_tag`, so the module production uses is untouched.
- `module.cluster`: the control plane (API reachable only from `api_allowed_cidrs`; the variable refuses `0.0.0.0/0`), one managed node group (two `t4g.medium`, On-Demand, arm), and the add-ons in the order the cluster needs them: network and proxy, then the servers, then DNS and the disk driver. Network policy enforcement is switched on in the network add-on, because k3s ignores network policies and EKS does not.
- IRSA: the cluster's identity endpoint is registered in IAM, and four roles (disk driver, network, load balancer controller, executor) each trust exactly one namespace and one service account plus the audience. The controller's role is AWS's own published policy for v3.5.0, committed unedited. The executor's role reads **one parameter**, `/kaval/kaval-lab-eks/irsa-proof`, which Terraform also creates.
- The servers' role may pull Kaval's repositories and use Session Manager, nothing else.
- The disks the disk driver makes carry the three required labels (`extraVolumeTags`), otherwise they would be unlabelled and the sweep could not find a leftover.

```bash
terraform -chdir=infra/envs/lab-eks init -backend=false && terraform -chdir=infra/envs/lab-eks validate   # also what CI runs
```

## Part 3 — The self-destruct timer (`infra/modules/lab-reaper`)

`reaper.py` runs every 15 minutes and works only from the cluster's age (AWS records it): at 4 hours it scales the servers to zero; at 6 it deletes the controller's load balancers, then the node group, then the cluster, then the loose security groups and target groups. The decision is a pure function, `decide(now, state, scale_in_hours, delete_hours)`, with 12 tests:

- a young cluster is left alone; the 4-hour line is inclusive; servers already at zero are not scaled again;
- after 6 hours load balancers go first, the cluster is deleted only once no node group is left, a cluster already being deleted is not deleted again;
- orphans of a cluster that is already gone are cleaned (load balancer first, because the security group is still held by it);
- a clock skew that puts the creation time slightly in the future does not produce a negative age.

```bash
pytest infra/modules/lab-reaper/lambda -q      # 12 passed
```

Its IAM role can touch only the named cluster and things carrying `elbv2.k8s.aws/cluster=kaval-lab-eks`.

## Part 4 — The sweep (`make lab-sweep`)

`scripts/ops/lab_sweep.py` asks the account, read-only, for anything carrying `Env=lab-eks`, the controller's cluster label or the lab's name, and sorts it into **billing** and **leftover**. It exits 1 on any finding. Tested off AWS: a clean account passes; a load balancer the controller made fails (the classic that `terraform destroy` misses); a disk the database left behind fails; a leftover network or role is not billing but still fails the strict check; the same resource seen twice is one finding.

## Part 5 — The deploy files: the same chart, a different values file

`deploy/environments/lab-eks/values.yaml` and `deploy/gitops/lab-eks/` (namespace with Pod Security warnings on at the strictest level, a `gp3` StorageClass, the repository, the lab's release, the controller's release, one shared Ingress limited to the laptop, and the network policies kept behind a hand-applied switch).

The one lab-only thing is the executor's IRSA label, added by a **post-renderer** in the lab's own release, so the chart is byte-for-byte what production runs. `scripts/dev/test_lab_eks.py` makes that a failing test rather than a promise: same chart path as production; no mention of the lab or the label inside the chart; the names that must agree across Terraform and the deploy files do agree; the Ingress is never open to the world; no account number in any committed file. The existing sync test now covers the lab's two copies of its values as well.

```bash
helm lint deploy/charts/kaval -f deploy/environments/lab-eks/values.yaml     # 0 failed
git diff main --name-only -- deploy/charts                                   # empty: no chart edit
```

## Part 6 — The plan, read and recorded

```bash
make lab-plan      # = tf-init lab-eks, then terraform plan -out=lab.tfplan: reads only
```

Result (2026-10-29): **44 to add, 0 to change, 0 to destroy**: the network (9), the cluster, its role, the node group and node role, four add-ons, the identity provider and four IRSA roles with their policies, six subnet labels, the proof parameter, and the timer (function, schedule, two roles, log group). Nothing touches production or staging. The cluster's API shows exactly one allowed address. Nothing was applied. (The live run added one policy the servers needed, so the plan became **45**: [Lab 66](lab-66-the-eks-lab-live-two-sessions-and-what-they-found.md), finding 1.)

## What was found on the way

- The first draft made the lab's database password come from a Secret. The CI step that renders every environment's values with no extra arguments would have failed, and Flux's inline `values:` override its `valuesFrom` anyway. So the lab uses a plain throwaway password, the same posture as `deploy/environments/local`: a database that lives hours, holds nothing real and is reachable only inside the cluster.
- The waste scan and the removal worker keep their own copies of the tag policy and a test keeps them equal to the real one; adding the `lab-eks` environment failed that test until both copies learned it. The test earned its place.
- Writing a timer that deletes outside Terraform means `make lab-down` is still needed afterwards, to remove what Terraform remembers. The runbook says so.

## Done when

- `terraform validate` passes on the new environment (CI runs it); the plan is recorded above
- 12 timer tests and 6 sweep tests and 10 lab-consistency tests pass; `make lint` shows only the four known unrelated errors
- the chart is unchanged (`git diff main --name-only -- deploy/charts` is empty)
- the ADR, the runbook, the cost note, the repo guide and the architecture entry are in the same pull request

## Cost

$0. The only AWS calls were price lookups, `describe-cluster-versions` and a read-only plan. The live run's cost is in [docs/cost/budget-plan.md](../cost/budget-plan.md).
