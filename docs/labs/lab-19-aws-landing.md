# Lab 19 — AWS landing: network, ECR, IAM, and a real k3s node

**Phase:** 4 · **Time:** ~45 min (most of it waiting on a `terraform apply`) · **Cost:** ~$13/mo
while the node runs — see [ADR-0024](../adr/0024-prod-landing-network-ecr-iam-node.md)

Every prior lab ran against a local k3d cluster or the single dev server. This lab stands up the
first real piece of prod: a dedicated VPC, four ECR repos, the node's own IAM role, and a spot
`t4g.medium` running k3s. It does **not** yet give you a working Kaval deployment — Flux, the
Cloudflare Tunnel and the database server are each their own later lab. The point here is the
landing itself: can Terraform build it, and does `kubectl get nodes` say `Ready`.

## Prerequisites

- `infra/envs/prod/terraform.tfvars` exists with `alert_email` and `ssh_public_key` set (generate
  a key first: `ssh-keygen -t ed25519 -f ~/.ssh/kaval-prod -C kaval-prod`)
- AWS credentials work: `aws sts get-caller-identity --profile kaval`
- Phase 0's budget guardrails already applied (`module.budget` exists in state)

---

## Step 1 — plan before touching anything

```bash
cd infra/envs/prod
terraform init
terraform validate
terraform plan -out=/tmp/kaval-prod.tfplan
```

Expect **20 to add, 1 to change** (the budget Lambda's hard-stop wiring), **0 to destroy**. State
the cost out loud before applying — this is real AWS spend from here on, not a local lab.

## Step 2 — apply

```bash
terraform apply /tmp/kaval-prod.tfplan
```

**Two known failure modes, both found live and both fixed in the module code** — you shouldn't
hit them again, but if a future AWS change reintroduces one:

- `Incompatible launch template: Auto Scaling only supports the 'one-time' Spot instance type` —
  an ASG-managed launch template can't request `persistent` spot. `infra/modules/node/main.tf`
  already omits `spot_options` for this reason.
- `InsufficientSpotCapacity` in a specific AZ — transient, the same class of thing
  `make devbox-up` has hit before. `infra/modules/network/variables.tf`'s `availability_zone`
  default is set to wherever capacity was confirmed at the time this lab was written
  (`ap-south-1b`); override it in `terraform.tfvars` if it recurs elsewhere.

## Step 3 — verify the node is actually up

```bash
INSTANCE_ID=$(aws autoscaling describe-auto-scaling-groups --profile kaval --region ap-south-1 \
  --auto-scaling-group-names kaval-prod \
  --query 'AutoScalingGroups[0].Instances[0].InstanceId' --output text)

aws ssm describe-instance-information --profile kaval --region ap-south-1 \
  --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
  --query 'InstanceInformationList[0].PingStatus' --output text
# → Online
```

## Step 4 — reach it the real way: SSH through an SSM tunnel, no open port

```bash
aws ssm start-session --profile kaval --region ap-south-1 \
  --target "$INSTANCE_ID" \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["22"],"localPortNumber":["2222"]}' &

ssh -i ~/.ssh/kaval-prod -p 2222 ec2-user@localhost "kubectl get nodes -o wide"
```

Expect one node, `Ready`, `v1.37.1+k3s1`, `arm64`. `$HOME` resolves correctly here (confirmed
live) so `kubectl` finds `~/.kube/config` with zero extra flags — this is the documented access
pattern, unlike a bare `aws ssm send-command`, where `$HOME` is unset (see ADR-0024).

## Step 5 — confirm no inbound port is open

```bash
aws ec2 describe-security-groups --profile kaval --region ap-south-1 \
  --filters "Name=group-name,Values=kaval-prod-node" \
  --query 'SecurityGroups[0].IpPermissions' --output json
# → []
```

## Step 6 — confirm the node's IAM role, not a static key, reaches ECR

```bash
ssh -i ~/.ssh/kaval-prod -p 2222 ec2-user@localhost \
  "aws sts get-caller-identity --query Arn --output text && aws ecr get-login-password --region ap-south-1 | head -c 20"
```

Expect an `assumed-role/kaval-prod-node/...` ARN, and a token (not an auth error).

## Step 7 — pause for the session

No `make up`/`make down` exists for prod yet (a later ROADMAP item). Until it does, pause by hand:

```bash
aws autoscaling update-auto-scaling-group --profile kaval --region ap-south-1 \
  --auto-scaling-group-name kaval-prod --min-size 0 --desired-capacity 0
```

Resume with `--min-size 1 --desired-capacity 1`. Storage (the launch template, ASG, IAM, ECR
images) all persist; only the running instance goes away and comes back as a fresh boot (nothing
is GitOps-managed yet, so nothing is lost — there's nothing running on it yet to lose).

## Done when

- [x] `terraform plan` showed exactly the network/ECR/IAM/node resources, cost stated first
- [x] `terraform apply` succeeded (after the two apply-time fixes above)
- [x] `kubectl get nodes` reports `Ready` over the SSH-through-SSM-tunnel path
- [x] The node's security group has zero inbound rules
- [x] The node reaches ECR using its own IAM role, no static credentials
- [x] The hard-stop Lambda is armed (`hard_stop_dry_run = false`) against the real ASG, and was
      proven live with a manual test invocation

---

## What actually happened, live (2026-10-02)

Both apply-time errors above happened in this exact order on the real account: the spot-type
rejection first, then (after that fix) `InsufficientSpotCapacity` in `ap-south-1a` — AWS's own
error message named `-1b`/`-1c` as having capacity, so the network module's default AZ moved to
`-1b`, which worked on the next apply.

Once up, `kubectl get nodes` was clean on the first real try over the SSM tunnel. The one thing
that *didn't* work cleanly was debugging via `aws ssm send-command` directly (not through SSH) —
`kubectl` failed there with "connection refused" because that shell has no `$HOME`, so `~/.kube/
config` can't resolve. Fixed by pointing `KUBECONFIG` at k3s's own kubeconfig file directly via
`/etc/environment`, which login shells (SSH, `sudo -i`) pick up. Full root-cause in ADR-0024.

The hard-stop Lambda, armed for the first time against a real ASG, was invoked manually with a
synthetic SNS event to prove it end-to-end rather than taking the wiring on faith — exactly the
kind of real test the Phase 0 guardrail lab asked for back when nothing existed to scale down yet.
It worked: `kaval-prod` scaled from 1 to 0. It also stopped the dev server in the same pass,
because the Lambda stops *every* `Project=kaval` instance outside an ASG, not just the one under
test — expected, not a bug, but worth knowing before you run this test yourself: it will pause
whatever else is running too. Resume the dev server with `make devbox-up`.
