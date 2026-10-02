# Lab 20 — Flux GitOps reconciliation, and the ECR bootstrap it needed

**Phase:** 4 · **Time:** ~2 hr (two real AWS findings, not just waiting) · **Cost:** no change —
still ~$13/mo while the node runs, see [ADR-0025](../adr/0025-flux-gitops-and-ecr-bootstrap.md)

Lab 19 landed the node; nothing was deployed onto it. This lab installs Flux so the node
reconciles itself from Git, pushes the first real images to ECR (a one-time manual bootstrap —
`release.yml` automates this later), and fixes a multi-AZ gap the previous lab's single-AZ pin
exposed a second time.

## Prerequisites

- Lab 19 done, prod node landing applied
- `docker --context kaval-devbox buildx inspect kaval-devbox` reachable (`make devbox-up` first)
- This story's branch merged to `main` **before** the live verification steps — Flux's
  `GitRepository` pulls from the published repo, not your working tree. Skipping this is the
  mistake Step 5 below walks through.

---

## Step 1 — the chart learns a registry it never commits

An ECR registry hostname embeds the AWS account ID; CLAUDE.md forbids that in any committed
file. `deploy/charts/kaval/values.yaml` gains `global.imageRegistry` (empty everywhere except
prod) and `global.imagePullSecretName`; a new `kaval.image` helper
(`templates/_helpers.tpl`) prepends the registry onto every service's `image.repository` only
when it's set. Verify the helper before trusting it anywhere real:

```bash
helm template kaval-prod deploy/charts/kaval -f deploy/environments/prod/values.yaml \
  --set global.imageRegistry=000000000000.dkr.ecr.ap-south-1.amazonaws.com \
  | grep 'image:'
# → every service image now prefixed; local/staging renders unchanged (no --set there)
```

## Step 2 — the multi-AZ fix

Lab 19 pinned `ap-south-1b` after AWS rejected `-1a`'s capacity. This lab's own first apply then
hit `InsufficientSpotCapacity` **in `-1b`**, the same day — AWS's error pointed at `-1a`/`-1c`
this time. Two different AZs failing within hours is volatility, not a one-off worth chasing with
another single-AZ pin. `infra/modules/network` now provisions one subnet per AZ
(`ap-south-1a/b/c`, one shared route table — routing doesn't vary by AZ) and the ASG's
`vpc_zone_identifier` takes all three, so AWS's own launch picks whichever has capacity:

```bash
cd infra/envs/prod
terraform plan -out=/tmp/kav51.tfplan
# → 7 to add (2 more subnets + associations), 3 to destroy (the old singular ones), 1 replace (ASG)
terraform apply /tmp/kav51.tfplan
```

**Found live, in order, on the real account:** a CIDR-conflict error
(`10.42.1.0/24 conflicts with another subnet`) on the very next apply — the old single subnet's
destroy and the new `ap-south-1a` subnet's create (same CIDR) raced, and AWS hadn't released the
address yet. A plain retry (`terraform plan` + `apply` again) succeeded once the destroy had
actually propagated — nothing to fix in code, just a transient ordering issue between two
separate resources.

## Step 3 — push the first real images (one-time, manual)

No image existed in ECR yet — CI builds and Trivy-scans real `arm64` images on every PR but
deliberately never pushes (`release.yml`'s job, still unbuilt). Flux needs something real to
pull, so this lab does the one push `release.yml` will later automate, using the identical
`sha-<short>` tag scheme Lab 18's promotion rehearsal already established:

```bash
REGISTRY=$(cd infra/envs/prod && terraform state show 'module.ecr.aws_ecr_repository.this["gateway"]' \
  | sed -n 's#.*repository_url *= *"\([^/]*\)/.*#\1#p')
aws ecr get-login-password --profile kaval --region ap-south-1 \
  | docker login --username AWS --password-stdin "$REGISTRY"

SHA=sha-$(git rev-parse --short HEAD)
for svc in gateway agent executor collector; do
  v=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "services/$svc/kaval_$svc/__init__.py")
  docker --context kaval-devbox buildx build --platform linux/arm64 \
    -f "services/$svc/Dockerfile" --build-arg "VERSION=$v" \
    -t "$REGISTRY/kaval/$svc:$SHA" --push .
done
```

One image (`collector`) hit a flaky SSM tunnel mid-push — the remote `kaval-devbox` buildx
context dropped its session (`Connection closed by UNKNOWN port 65535`) partway through, after
three other images had already pushed cleanly through the same context. Plain SSH to the box
kept working throughout, so the box itself was fine; only that one buildkit session was bad.
Building the same image locally under QEMU emulation instead (`--builder desktop-linux` in place
of `--builder kaval-devbox`) pushed it without issue — slower, but it's a one-time bootstrap, not
something that needs to be fast.

## Step 4 — Flux, and what it bootstraps

`infra/modules/node/user_data.sh.tftpl` now installs Flux (checksum-verified, matching every
other binary this node installs) and runs `flux install` — the controllers only, **not**
`flux bootstrap`, which commits a manifest straight to `main` and would bypass the PR-required
branch protection this repo has had since `KAV-24`. A small script,
`/usr/local/sbin/kaval-gitops-bootstrap`, then:

1. reads the node's own region and account ID from its IMDSv2 instance identity document;
2. creates `ConfigMap/kaval-registry` (`flux-system`) and `Secret/ecr-cred` (`kaval-prod`,
   `docker-registry` type, from a freshly-fetched `aws ecr get-login-password`) — both from the
   node's own identity, never committed;
3. applies `deploy/gitops/prod/{namespace,source,helmrelease}.yaml`, fetched by URL from this
   repo's `main` branch.

A systemd timer re-runs the same script every 6h, comfortably inside the 12h ECR token lifetime,
so a long-running node's credential never goes stale between Flux's own reconciles.

## Step 5 — the mistake worth keeping: verify before merging

The first live check, right after `terraform apply` finished, failed:

```
curl -fsSL https://raw.githubusercontent.com/roshanoommenreji/kaval/main/deploy/gitops/prod/namespace.yaml
  → curl: (22) The requested URL returned error: 404
  → error: no objects passed to apply
```

Obvious once seen: `deploy/gitops/prod/` existed only in the local working tree. Flux's
`GitRepository` — and this bootstrap script — both pull from the *published* `main`, which had
none of this story's changes yet. **The fix is the normal one, not a workaround**: branch, PR,
CI green, merge — then re-run the one idempotent command, no reboot needed:

```bash
ssh -i ~/.ssh/kaval-prod ec2-user@<instance-id>   # through the SSM tunnel, as in Lab 19
sudo /usr/local/sbin/kaval-gitops-bootstrap
```

## Step 6 — verify, for real this time

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl get gitrepository,helmrelease -n flux-system
kubectl get pods -n kaval-prod
```

**Found live, right here:** the `GitRepository` reported `failed to checkout and determine
revision: ... dial tcp: lookup github.com on 10.43.0.10:53: server misbehaving`. The node itself
resolved `github.com` fine (`getent hosts github.com`) — only pods couldn't. `kubectl logs -n
kube-system -l k8s-app=kube-dns` showed CoreDNS's own upstream forward failing with
`connection refused`/`i/o timeout`. Root cause: `infra/modules/network`'s VPC CIDR was
`10.42.0.0/16` — **identical to k3s's own default pod-network CIDR**. The VPC's real DNS
resolver lives at the base-plus-2 address, `10.42.0.2`, which then sat inside the range
Flannel's overlay claims for pods, so pod traffic to it never left the overlay. Fixed by moving
the VPC to `10.60.0.0/16` (ADR-0025, Decision 4) — not a workaround, the actual collision.

Separately, re-running `kaval-gitops-bootstrap` by hand to pick up the published manifests
(Step 5) showed the live ECR token in the SSH session's own output — the script's `set -x` was
tracing the `kubectl create secret ... --docker-password=<token>` command in full, which also
meant it was sitting in `/var/log/cloud-init-output.log` on the node in plaintext. Fixed by
turning tracing off for this script and building the Secret as YAML piped via stdin instead of
a `--docker-password=` argument (ADR-0025, Decision 5) — a real credential-handling bug in this
story's own code, not an AWS quirk.

**A third finding, once the CIDR fix actually let `GitRepository` clone the repo:** the
`HelmRelease` install itself then failed —
`Namespace "kaval-demo-prod" is invalid: metadata.labels: Invalid value: "kaval-0.1.0+1"`.
Flux's helm-controller packages a chart with `valuesFiles` set under an appended `+<n>` semver
suffix (source-controller's own documented behaviour), and the chart's `helm.sh/chart` label
put `.Chart.Version` into a label value raw — `+` isn't legal there. Every prior lab ran `helm
install`/`upgrade` directly, which never produces that suffix, so this was latent the whole
time. Fixed with Helm's own standard convention, `{{ .Chart.Version | replace "+" "_" }}`
(ADR-0025, Decision 6) — verified by temporarily setting `Chart.yaml` to `0.1.0+1` and
confirming `helm template` renders the sanitised label, not just reading the fix and trusting it.

## Done when

- [ ] `helm template --set global.imageRegistry=...` shows every service's image correctly
      prefixed, and local/staging unaffected
- [ ] `terraform apply` succeeds with the node spanning all three AZs
- [ ] All four images pushed to ECR, tagged `sha-<short>`
- [ ] This story merged to `main` through a PR, CI green
- [ ] `kubectl get gitrepository,helmrelease -n flux-system` shows both `Ready`
- [ ] `kubectl get pods -n kaval-prod` shows the real Kaval services running on the real node

---

## What actually happened, live (2026-10-02)

Three real findings, in the order they happened: the CIDR-conflict race on the subnet
replacement (Step 2), the flaky remote buildx session on the `collector` push (Step 3), and the
404 from verifying before merging (Step 5) — each fixed by the straightforward thing, not a
workaround. The multi-AZ network change itself was prompted by a second, independent spot-capacity
failure in a *different* AZ than Lab 19's, within the same day — strong enough evidence that one
pinned AZ isn't a durable fix for this instance size in this region.
