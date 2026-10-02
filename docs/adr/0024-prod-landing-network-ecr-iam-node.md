# ADR-0024 — Prod's landing: network, ECR, IAM, and the spot node

- **Status:** Accepted — built, deployed, verified live
- **Date:** 2026-10-02
- **Deciders:** Roshan

## Context

Phase 0 provisioned only the budget guardrails in `infra/envs/prod`; the `network`, `ecr`, `iam`
and `node` modules existed as empty `.gitkeep` placeholders, with the intended shape already
sketched as a commented block in `infra/envs/prod/main.tf`. Phase 4 (ADR-0004's target) is where
the real system moves off the laptop/dev-server-only world onto AWS. This ADR covers the first,
foundational slice of that: a dedicated VPC, ECR for the four service images, the node's own IAM
role, and a spot `t4g.medium` ASG running k3s.

**Deliberately out of scope**, each its own follow-on story: Flux GitOps reconciliation (so a
replacement node doesn't yet get the Helm releases back automatically), the Cloudflare Tunnel,
the database server (`KAV-32`, ADR-0008), and `make up`/`make down` for prod.

## Decision

### What the IAM module actually is

The commented placeholder in `prod/main.tf` read "agent → read-only, executor → scoped write" —
language that pre-dates `KAV-47`, which already built exactly that split at the **Kubernetes RBAC
layer** (ADR-0021): ServiceAccounts and Roles scoped per-pod inside the cluster, live today. A
per-service **AWS** IAM identity for workloads (IRSA or equivalent) only becomes meaningful once
the executor calls AWS write APIs directly — that's Phase 7's FinOps work, already modeled as the
`aws-apis` node in `architecture.toml` (phase 7, IRSA on EKS in Phase 8). Self-managed k3s has no
IRSA equivalent.

So `infra/modules/iam` provisions exactly one identity: **the node's own operational role** — SSM
management (matching `infra/modules/devbox`) and ECR pull, scoped to Kaval's four repos, not every
ECR repo in the account. `ecr:GetAuthorizationToken` is the one action AWS requires `Resource: "*"`
for; the actual pull actions are scoped to `module.ecr.repository_arns`.

### ECR: immutable tags, scoped lifecycle cleanup

One repo per service (`kaval/gateway`, `kaval/agent`, `kaval/executor`, `kaval/collector`),
`image_tag_mutability = IMMUTABLE`. "Build once, promote the artifact" (ADR-0004) only holds if a
tag can never be silently overwritten — immutability makes that a guarantee ECR enforces, not a
convention CI has to uphold on its own. A lifecycle policy expires **untagged** images after 7
days (failed pushes, reused layers); every `sha-*` tagged image is kept forever — it's the record
of what was ever built and promoted, and ECR's free tier (first 500 MB) comfortably covers a
learning project's release cadence.

### Network: a dedicated VPC, still no NAT Gateway

Unlike `infra/modules/devbox` (lives in the account's default VPC — it's disposable), prod gets
its own VPC, one public subnet, an Internet Gateway, and a route table. No NAT Gateway ($32/mo):
the node gets a public IP for outbound traffic and is reached only through SSM Session Manager, so
nothing needs a private subnet.

### The node: ASG, not a bare instance — and why it couldn't be "persistent" spot

Unlike devbox, the node sits behind a launch template + Auto Scaling Group pinned to size 1. Spot
capacity can be reclaimed with two minutes' notice; the ASG's job is to replace it without anyone
paged.

**Found at apply time:** `instance_market_options.spot_options.spot_instance_type = "persistent"`
is rejected by AWS when the launch template is used inside an ASG — `InvalidQueryParameter:
Incompatible launch template: Auto Scaling only supports the 'one-time' Spot instance type with no
duration.` The fix is to drop `spot_options` entirely and let the ASG itself own replacement; a
persistent spot *request* underneath an ASG that's already re-launching on interruption is
redundant, and apparently actively rejected by the API. Corrected in `infra/modules/node/main.tf`.

**Also found at apply time:** `ap-south-1a` — the AZ `infra/modules/devbox` already defaults to,
and the one this module copied — had no `t4g.medium` spot capacity available (`InsufficientSpot-
Capacity`, a transient condition, the same class of issue `make devbox-up` has hit before per the
Phase 1 journal). AWS's own error pointed at `-1b`/`-1c`. Switched the network module's default
`availability_zone` to `ap-south-1b`, which had capacity. Noted as a possible-again transient
condition, not treated as permanent.

**k3s install stays checksum-verified**, the same reasoning `infra/modules/devbox` already applies
to k3d/kubectl/helm: a pinned SHA-256 against a specific release tag, not the official `curl |
sh` installer trusting an unauthenticated script. The systemd unit is hand-written from k3s's own
`install.sh` template (confirmed by reading it: `Type=notify` for a server, the same
`ExecStartPre`/`KillMode`/`Delegate` lines) so the checksum-verified binary gets identical
supervision without running the installer's own script.

**Found live, after the node came up:** `kubectl` run via `aws ssm send-command` (as root, a
non-login shell) has `$HOME` unset, so `~/.kube/config` — written only to `/home/ec2-user/.kube/`
by cloud-init — can't be found by `~`-expansion even after copying it to `/root/.kube/` by hand
(copying doesn't help; the lookup itself can't resolve `~`). `sudo -i` (a login shell) **does**
read `/etc/environment`, confirmed live. Fixed by pointing `KUBECONFIG` at k3s's own file directly
(`/etc/rancher/k3s/k3s.yaml`, already `644` from `--write-kubeconfig-mode=644`) via
`/etc/environment`, rather than maintaining a second copy. This covers SSH-through-the-SSM-tunnel
(the documented access pattern — confirmed working with zero extra configuration, `$HOME` is set
correctly there) and `sudo -i`. A bare `ssm send-command` without a login shell still needs
`--kubeconfig` spelled out explicitly — that's the ad-hoc verification path used to debug this,
not the documented one.

### Arming the hard-stop Lambda, not just wiring it

The budget module's Lambda (`infra/modules/budget/lambda/hard_stop.py`) was written in Phase 0
with an ASG-scale-to-zero path already coded and gated behind `hard_stop_dry_run`, waiting for a
real `asg_name`. This story sets `asg_name = module.node.asg_name` **and** `hard_stop_dry_run =
false` in the same change — armed from the moment compute exists, not left in dry-run until some
later story remembers to flip it. The whole point of building the guardrail in Phase 0 was to have
it live before anything could overrun it.

## Verification

Live, 2026-10-02, commit at `infra/KAV-50-aws-landing`:

- `terraform plan`: 20 to add, 1 to change (the budget Lambda), 0 to destroy. Applied clean after
  the two apply-time fixes above (spot type, then AZ).
- `kubectl get nodes` over the real access path (SSH through an SSM port-forward tunnel, as
  `ec2-user`, zero extra config) showed one node, `Ready`, `v1.37.1+k3s1`, `arm64`,
  `containerd://2.3.4-k3s1`.
- The node's security group: `IpPermissions: []` — confirmed zero inbound rules, SSM/SSH-tunnel
  only.
- `aws sts get-caller-identity` from the node showed an **assumed role**
  (`kaval-prod-node`), not a static key; `aws ecr get-login-password` succeeded — the IAM wiring
  authenticates to ECR with no long-lived credential anywhere.
- The hard-stop Lambda was invoked manually with a synthetic SNS event (the same kind of real
  test Phase 0's own guardrail lab called for) to prove the newly armed path actually scales a
  real ASG: `{"asg": {"status": "stopped", "asg": "kaval-prod", "desired_before": 1}}`. This also
  served as the session's pause (no `make down` for prod exists yet — see Consequences).
  **Side effect, not a bug:** the Lambda is deliberately blunt (its own docstring says so) and
  also stopped every other `Project=kaval` instance outside an ASG in the same pass — in practice,
  the dev server. Expected design (`make devbox-up` restores it), but worth stating plainly since
  it wasn't the thing under test.

## Cost

Verified live via `aws ec2 describe-spot-price-history` at apply time, not the original plan's
~7-month-old estimate:

| Item | $/mo |
|---|---|
| `t4g.medium` spot (ap-south-1b, ~$0.0103–0.0109/hr × 730h) | ~7.5–8.0 |
| gp3 20 GB root volume | ~1.82 |
| Public IPv4 | ~3.65 |
| ECR storage | ~0 (under the 500 MB free tier at this story's image count) |
| **New recurring total while running** | **~$13/mo** |

In line with ADR-0004's original ~$14/mo Phase 4 estimate, slightly under. See
[budget-plan.md](../cost/budget-plan.md).

## Consequences

**Easier.** A real second compute environment exists; the hard-stop guardrail is now armed
against something real instead of a no-op; ECR immutability makes "build once, promote the
artifact" an enforced guarantee rather than a convention.

**Harder.** No `make up`/`make down` for prod yet, so there's no convenient pause between weekly
sessions — scaling the ASG to zero by hand (or, as happened this session, via the hard-stop
Lambda itself) is the stopgap until that ROADMAP item lands. No Flux yet, so a spot reclamation
today produces an empty k3s, not a self-healing one — `helm upgrade --install` has to be re-run by
hand. Both are tracked, unstarted Phase 4 ROADMAP lines, not hidden gaps.

**Revisit if:** Flux lands and the ASG's `instance_market_options` or launch-template
`create_before_destroy` interacts badly with GitOps-managed reconciliation; `ap-south-1a` capacity
returns and is worth reverting to for some other reason; the `/etc/environment` `KUBECONFIG` fix
turns out not to cover a real automation path once one exists (e.g. `release.yml` deploying via
SSM `send-command` rather than SSH).

## Sources

AWS CLI output against the live account, 2026-10-02 (spot pricing, ASG/Lambda state, `kubectl`
over the SSM tunnel). k3s's own `install.sh` (tag `v1.37.1+k3s1`) for the systemd unit template.
