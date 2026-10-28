# Runbook — the EKS lab (bring it up, use it, take it down, prove it is gone)

Phase 7, [ADR-0067](../adr/0067-the-eks-lab-is-ephemeral-has-its-own-timer-and-is-proven-clean-by-a-label-sweep.md). Real EKS for one session. **The control plane bills $0.10 an hour (about $73 a month) from the moment it exists.** A session is about 4 to 6 hours and costs about $1.2 to $2.

**Who:** Roshan, at the keyboard. Every step that changes AWS is run on his own go, with the exact command shown first.

## Before you start

- Production and staging do not need to be up. The lab is independent of both.
- `AWS_PROFILE=kaval`, region `ap-south-1`. `kubectl` and `helm` installed. `flux` is **not** needed: Flux is installed from its published manifest (step 3).
- Your laptop's public address changes (home internet). In `infra/envs/lab-eks/terraform.tfvars` (git-ignored) put `api_allowed_cidrs = ["<address>/32"]` from `curl https://checkip.amazonaws.com`.
- Cost check: `make cost-report` shows spend so far this month. A session adds about $2.

## 1. Plan, read it, record it

```bash
make lab-plan        # connects to the state, writes lab.tfplan, changes nothing
```

Read the plan. **Expect 44 to add, 0 to change, 0 to destroy** on a clean account. Check by eye:

- the Kubernetes version is **1.35** (a version past standard support bills $0.50 an hour);
- `public_access_cidrs` is only your address;
- the node group is two `t4g.medium`, `ON_DEMAND`, `AL2023_ARM_64_STANDARD`;
- every IRSA role's trust names a namespace and a service account (`sub`), not a wildcard;
- nothing in it touches production or staging.

Copy the plan's summary into the lab note for the day (the plan is a record; `lab.tfplan` itself is git-ignored).

## 2. Bring it up (starts billing)

```bash
make lab-up          # applies exactly the saved plan; about 15 minutes
aws eks update-kubeconfig --name kaval-lab-eks --region ap-south-1 --profile kaval
kubectl get nodes    # two Ready servers
```

**From this moment the clock is running.** The self-destruct timer scales the servers to zero at 4 hours and deletes everything at 6. To prove it before trusting it, set `reaper_dry_run = true` for a first look at its log (`aws logs tail /aws/lambda/kaval-lab-eks-reaper`).

## 3. Install Flux, then give it what it must not read from git

```bash
FLUX=2.9.6
curl -fsSLo flux-install.yaml "https://github.com/fluxcd/flux2/releases/download/v${FLUX}/install.yaml"
# compare its sha256 with the one on the release page (the same check the k3s servers do)
kubectl apply -f flux-install.yaml
```

Then three small objects, made by hand and never committed (each holds an account number or an address):

```bash
kubectl create configmap kaval-registry -n flux-system \
  --from-literal=registry="$(aws sts get-caller-identity --query Account --output text).dkr.ecr.ap-south-1.amazonaws.com"
cd infra/envs/lab-eks
kubectl create configmap kaval-lab-cluster -n flux-system \
  --from-literal=EXECUTOR_ROLE_ARN="$(terraform output -raw executor_role_arn)" \
  --from-literal=LBC_ROLE_ARN="$(terraform output -raw load_balancer_controller_role_arn)" \
  --from-literal=VPC_ID="$(aws ec2 describe-vpcs --filters Name=tag:Name,Values=kaval-lab-eks --query 'Vpcs[0].VpcId' --output text)" \
  --from-literal=LAB_ALLOWED_CIDR="<your address>/32"
```

Do not paste any of those values anywhere else (the registry contains the account number).

```bash
kubectl apply -f deploy/gitops/lab-eks/source.yaml -f deploy/gitops/lab-eks/sync.yaml
kubectl get helmrelease -A -w      # kaval-lab and aws-load-balancer-controller become Ready
```

The chart installs **unmodified**. The only lab-only thing is the executor's IRSA label, added by the lab's own `HelmRelease`. If anything needs a *chart* edit to run on EKS, stop: that is a finding against the portability claim, not something to patch.

## 4. The proofs, in this order

1. **Same chart:** `git diff main -- deploy/charts` is empty; `helm get manifest` is what the other environments render, apart from values.
2. **IRSA:** run a throwaway pod with the executor's service account (`serviceAccountName: kaval-lab-executor`, image `amazon/aws-cli`) and read the proof parameter (`aws ssm get-parameter --name /kaval/kaval-lab-eks/irsa-proof`). It works with no stored key. Then run the same pod with another service account: it is **refused**. Also: the same pod cannot read any other parameter.
3. **Load balancer controller:** the Ingress makes one real load balancer; `curl` the gateway's `/healthz` through it from your address; from anywhere else it is refused. Note the number of load balancers (one).
4. **Observability parity:** Prometheus and the poller are Running from the same chart; the gateway probe target answers.
5. **Strict rules.** First note what the chart does with none. Then:
   - Pod Security: the namespace already `warn`s at `restricted`; read what it says. Then `kubectl label ns kaval-lab pod-security.kubernetes.io/enforce=baseline --overwrite` and restart the deployments; note what is refused.
   - Network policies: `kubectl apply -f deploy/gitops/lab-eks/hardening-sync.yaml`; check the gateway still answers through the load balancer, Prometheus still scrapes, the services still reach the database; a pod in another namespace cannot reach the database.
   - Each thing either rule breaks becomes its own Jira story and roadmap line. Nothing is patched in the lab.
6. Screenshots and the recorded outputs go in the lab note.

## 5. Take it down, and prove it

```bash
kubectl delete ingress -n kaval-lab kaval-lab-gateway     # the controller deletes the load balancer
kubectl get ingress -A                                     # empty
make lab-down                                              # terraform destroy (shows the plan, asks yes)
make lab-sweep                                             # must say: nothing left
```

If `make lab-down` fails part-way, run it again; the timer will also finish the job at 6 hours. If a load balancer or disk shows in the sweep, it is a finding: delete it by its id (shown in the sweep), then record how it escaped.

**24 hours later**, again: `make lab-sweep` and `make cost-report`. The exit gate is both showing nothing, not "destroy succeeded".

## If something is wrong

| Symptom | Do |
|---|---|
| `make lab-up` fails part-way | Read the error, run `make lab-plan` again (it shows what is left to make), then `make lab-up`. Do not leave a half-made cluster overnight: `make lab-down` |
| The nodes never become Ready | `kubectl get nodes`, then `kubectl describe node`; the network add-on is the usual suspect. The cluster still bills: do not stay stuck, destroy |
| The pods cannot pull images | The servers' role allows only Kaval's repositories; check the image name and tag against ECR |
| `kubectl` times out | Your address changed. Update `api_allowed_cidrs`, `make lab-plan`, `make lab-up` (a small change in place) |
| The sweep shows a load balancer | It was made by the controller. `aws elbv2 delete-load-balancer --load-balancer-arn <it>`, then re-run the sweep |
| The timer errors (alarm) | Read `/aws/lambda/kaval-lab-eks-reaper`. If in doubt, `make lab-down` by hand now |

## Kubernetes version and upgrade policy

The lab uses a version in **standard support** (checked with `aws eks describe-cluster-versions`; 1.35 on 2026-10-29, supported to 2027-03-27). It is never upgraded in place: a lab cluster lives for hours and is destroyed. Before each session check the version is still in standard support and has not been overtaken by what CI renders against; if it changed, change both together.
