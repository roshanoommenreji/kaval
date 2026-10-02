# ADR-0025: Flux GitOps reconciliation, and the ECR bootstrap it needed

**Status:** Accepted — built, deployed, verified live
**Date:** 2026-10-02
**Related:** [ADR-0004](0004-environment-strategy-and-promotion.md) (build once, promote the
artifact), [ADR-0024](0024-prod-landing-network-ecr-iam-node.md) (the node this installs
onto, and the IAM-scoping decision this ADR builds on), `KAV-51`, Lab 20

## Context

KAV-50 landed a real node on AWS, but nothing was deployed onto it — `main.tf`'s own header
comment called this out: a spot reclamation would produce a fresh, empty k3s, with
`helm upgrade --install` needing to be re-run by hand. That's not the self-healing property
Phase 4's exit gate actually tests ("terminate the node by hand; it rebuilds itself from Git
in under 5 minutes"). This story closes that gap with Flux.

Doing that honestly surfaced two problems that had to be solved first, not glossed over:

1. **Nothing was in ECR yet.** CI builds and Trivy-scans real arm64 images on every PR
   (`images` job, `ci.yml`) but deliberately never pushes — that's `release.yml`'s job, and
   `release.yml` doesn't exist yet (it's later in Phase 4's own task list). Flux needs
   something real to pull, so this story did a one-time manual build+push — the same
   `sha-<short>` tagging `release.yml` will automate later, not a different scheme.
2. **An ECR registry hostname embeds the AWS account ID.** CLAUDE.md forbids a real account
   ID in any committed file. A GitOps values file is, by definition, committed. Those two
   facts are in direct tension, and the resolution below is the actual substance of this ADR.

## Decision 1: the registry and the ECR credential never reach Git

Two things Flux's `HelmRelease` needs are both derived from the node's own identity at
reconcile time, from a `ConfigMap` and a `Secret` the node's cloud-init creates — never from a
committed value:

| What | Where it lives | How it's derived | Why it can't be committed |
|---|---|---|---|
| Registry hostname | `ConfigMap/kaval-registry` (`flux-system`) | `$ACCOUNT.dkr.ecr.$REGION.amazonaws.com`, read from the node's own IMDSv2 instance identity document | Embeds the AWS account ID |
| ECR login token | `Secret/ecr-cred` (`kaval-prod`, `docker-registry` type) | `aws ecr get-login-password`, using the node's own IAM role (already scoped to the four repos by ADR-0024) | Expires every 12h — committing one would be committing a credential that's already stale |

The `HelmRelease` merges the registry in via `valuesFrom`/`targetPath` into a new chart value,
`global.imageRegistry` (empty for local/k3d, where images are loaded directly and nothing is
ever pulled from a registry that needs auth):

```yaml
valuesFrom:
  - kind: ConfigMap
    name: kaval-registry
    valuesKey: registry
    targetPath: global.imageRegistry
```

A new Helm helper, `kaval.image` (`deploy/charts/kaval/templates/_helpers.tpl`), prepends it
onto every service's `image.repository` when set; `global.imagePullSecretName` (set to
`ecr-cred` only in prod's `values.yaml` — the Secret's *name* is a repo fact, not a secret)
adds `imagePullSecrets` to every Deployment/Job's pod spec the same way.

**The alternative considered and rejected:** `flux bootstrap github`, the usual one-command
Flux setup. It commits a `flux-system` manifest straight to `main` on your behalf — which
would both bypass the PR-required branch protection this repo has enforced since `KAV-24`,
and give Flux's own tooling write access to the repo, neither of which this project does
casually. Cloud-init instead runs plain `flux install` (controllers only, manifests embedded
in the binary) and applies the `GitRepository`/`HelmRelease` objects directly from
`deploy/gitops/prod/`, authored and reviewed through a normal PR like everything else.

## Decision 2: a refreshed Secret, not an EKS-style credential provider

The idiomatic way for a *self-managed* Kubernetes node to authenticate to ECR without a
refresh loop is the kubelet image credential provider plugin
(`kubernetes/cloud-provider-aws`'s `ecr-credential-provider`) — the mechanism EKS's own
optimized AMIs use internally. This project doesn't use it, deliberately: ADR-0024 already
decided that a per-workload AWS identity (IRSA or equivalent) is Phase 7/8 scope, because
self-managed k3s has no IRSA equivalent and nothing yet calls AWS write APIs directly. Pulling
in a credential-provider binary now, with no prebuilt checksummed release artifact readily
available for it, would be solving a Phase 7/8 problem two phases early.

Instead: a systemd timer (`kaval-gitops-bootstrap.timer`, every 6h — comfortably inside the
12h ECR token lifetime) re-runs the same script cloud-init runs at boot, refreshing both the
`ecr-cred` Secret and the `kaval-registry` ConfigMap from the node's own role. Documented
honestly as the interim answer, not the final one.

## Decision 3: the network spans three AZs, not one

Unrelated to Flux on paper, found while bringing the node back up for this story: AWS refused
`t4g.medium` spot capacity in `ap-south-1a` at KAV-50's apply (2026-10-02), so that story
pinned `ap-south-1b` as the default. This story's own apply then hit the identical error
*in `ap-south-1b`*, the same day — with AWS's error pointing at `ap-south-1a`/`-1c`. Two
different AZs failing within one day is spot capacity being genuinely volatile, not a one-off
worth another single-AZ pin.

`infra/modules/network` now provisions one public subnet per AZ (`ap-south-1a/b/c`), all
routed through the same internet gateway and route table (routing doesn't vary by AZ — only
the subnet's placement does), and the ASG's `vpc_zone_identifier` takes all three. A single
instance still only ever runs in one AZ at a time; this just lets the ASG's own launch choose
whichever has capacity right now, instead of a human re-pinning one AZ after every failure.

## Decision 4: the VPC CIDR cannot be `10.42.0.0/16`

Found live, during this story's own verification: k3s's default pod-network CIDR (Flannel)
is `10.42.0.0/16` — the exact range `infra/modules/network`'s `vpc_cidr` had used since
ADR-0024. The VPC's own Amazon-provided DNS resolver sits at `10.42.0.2` (AWS's base+2
convention); because that address also falls inside the range Flannel's overlay claims for
pods, traffic from a pod to `10.42.0.2:53` got captured by the overlay instead of reaching the
real resolver. CoreDNS's upstream `forward . /etc/resolv.conf` failed with
`connection refused`/`i/o timeout` on every external name — including Flux's own
`GitRepository` trying to clone GitHub, which is what surfaced it. The host itself resolved
names fine throughout (it isn't behind the pod overlay), which is what made this a
pods-only, DNS-only symptom rather than an obviously-broken network.

`vpc_cidr` moved to `10.60.0.0/16` (subnets `10.60.1/2/3.0/24`), clear of both k3s's pod CIDR
(`10.42.0.0/16`) and its default service CIDR (`10.43.0.0/16`, visible as CoreDNS's own
`10.43.0.10` ClusterIP). No code elsewhere referenced the old range by value.

## Decision 5: the ECR credential never gets traced or passed as an argument

Also found live, re-running the bootstrap script by hand while diagnosing the above: the
script's `set -x` (inherited from the pattern every other section of this file uses) traced
`kubectl create secret docker-registry ... --docker-password=<token>` in full — putting a
real, live ECR credential into both `/var/log/cloud-init-output.log` in plaintext and this
session's own SSH output. Fixed two ways, not one: `set -x` is off for this script specifically
(every other credential-free section of `user_data.sh.tftpl` keeps it), and the Secret is now
built as a YAML manifest and piped to `kubectl apply -f -` via stdin rather than passed through
`--docker-password=`, so it never sits in the process's own argv (`ps aux`-visible to anything
else on the node) either.

## Decision 6: a chart label can't carry Flux's own chart-version suffix

Found live, once the CIDR fix (Decision 4) let `GitRepository` actually clone the repo: the
`HelmRelease` install itself then failed — `Namespace "kaval-demo-prod" is invalid:
metadata.labels: Invalid value: "kaval-0.1.0+1"`. Source-controller's own documented
behaviour is the cause: packaging a `HelmChart` with `valuesFiles` set appends `+<n>` semver
build metadata to the chart version, because each distinct values combination needs its own
artifact revision. `kaval.labels` (`_helpers.tpl`) put `.Chart.Version` straight into
`helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}` — and `+` isn't a legal Kubernetes
label-value character. Every environment before this one ran `helm install`/`upgrade`
directly, which never produces that suffix, so the bug was latent in every prior lab, invisible
until Flux's packaging path exercised it.

Fixed with Helm's own standard scaffold convention for exactly this case:
`{{ .Chart.Version | replace "+" "_" }}`. Verified by temporarily setting `Chart.yaml`'s
version to `0.1.0+1` and confirming `helm template` renders `kaval-0.1.0_1`, not a re-read of
the fix — a label that happens to work once, from one real value, is not the same as knowing
the substitution is correct for every version string this project will ever use.

## Decision 7: a Kustomization, not just a one-time apply, actually watches the directory

Found live, immediately after Decision 6's fix merged: `GitRepository` picked up the new
commit, but `HelmRelease` kept failing with the *identical, pre-fix* error. The reason wasn't
the label fix itself — it was that `HelmChart`'s default `reconcileStrategy` (`ChartVersion`)
only repackages the chart when `Chart.yaml`'s own `version:` field changes, not merely because
the underlying files did (source-controller's own documented warning, read and missed the
first time). Fixing *that* (`reconcileStrategy: Revision`) exposed the deeper gap: cloud-init's
`kaval-gitops-bootstrap` script only ever `kubectl apply`s `namespace.yaml`/`source.yaml`/
`helmrelease.yaml` **once**, at boot (and every 6h via the credential-refresh timer, which
re-applies them as a side effect, but still only on that schedule). Nothing was actually
watching `deploy/gitops/prod/` for changes between those points — so this very fix would have
sat un-applied on the already-running node for up to 6 hours, which is not "reconciles itself
from Git" in any meaningful sense.

A Flux `Kustomization` (`kustomize-controller`, `sync.yaml`) closes the gap properly: it
watches the same `GitRepository`, on its own 1-minute interval, and reconciles the whole
directory — including its own definition and `helmrelease.yaml`'s `reconcileStrategy` field,
had cloud-init not already set it. It deliberately does **not** manage `kaval-registry`/
`ecr-cred`: those are never committed (Decision 1), so they stay the bootstrap script's job.

## Decision 8: `valuesFiles` silently drops the chart's own defaults — use `values:` instead

Found live, once the Kustomization (Decision 7) let the fixed `HelmRelease` actually
reconcile: `Helm install failed ... PersistentVolumeClaim "kaval-prod-postgres" is invalid:
spec.resources[storage]: Invalid value: "0"`. `postgres.storage` is never set in
`deploy/environments/prod/values.yaml` — it relies on the chart's own default, `2Gi`.
Locally, `helm template -f deploy/environments/prod/values.yaml` renders that default
correctly, every time. Flux's packaging did not.

The cause is in source-controller's own documented wording: `.spec.chart.spec.valuesFiles`
is "an alternative list of values files to use **as** the chart values (`values.yaml`)" —
not merged with the chart's bundled defaults, but substituted for them during packaging.
Any key `deploy/environments/prod/values.yaml` doesn't set (nearly everything except its own
explicit overrides) simply isn't there in the packaged chart, and Kubernetes' own API
validation is what actually surfaces the result as `"0"`, not Helm.

Fixed by using `HelmRelease.spec.values`/`spec.valuesFrom` instead — a different mechanism
from `chart.spec.valuesFiles`, one that merges normally **on top of** the chart's defaults
(Flux's own docs show this exact "prod env values" pattern). The first attempt at this tried
to avoid duplicating `deploy/environments/prod/values.yaml`'s content by generating a
ConfigMap from it via Kustomize's `configMapGenerator` with a relative `../../` path —
Kustomize refused it outright (`file '...' is not in or below` the kustomization's own
directory), a security restriction not worth fighting for one file. `helmrelease.yaml`'s
`spec.values` carries a hand-kept copy of that file's content instead, flagged as a known gap
in both files until `release.yml` gives this project a real env-values pipeline.

## Decision 9: a stuck remediation needs a fresh `HelmRelease`, and the launch template needs a real apply, not just a `kubectl apply`

Two closing findings, once Decision 8's fix had actually merged. First: `HelmRelease` kept
retrying with values that resolved to `{}` (helm-controller's own log: `resetting values to
the chart's original version: {}`) even on the commit carrying the fix — several consecutive
`upgrade` attempts had already been exhausted against the *pre-fix* chart (missing
`postgres.image`, PVC `storage: "0"`, etc.), and the accumulated failure/remediation state
didn't clear itself just because the underlying commit changed. `kubectl delete helmrelease
kaval-prod -n flux-system` followed by re-applying the same file gave it a clean slate — a
fresh `install`, not a confused `upgrade` — and it succeeded immediately. Flux's own documented
`reconcile.fluxcd.io/requestedAt` annotation is the lighter-weight way to request this without
deleting the object; worth trying first if this recurs.

Second: the live node that finally succeeded was running an **older** launch-template version
than `main` — it had been spot-reclaimed and relaunched by the ASG between this story's commits,
picking up everything through the CIDR/credential fix but not yet `sync.yaml`'s own bootstrap
step, because that fix had only ever been hand-applied to the *previous* node over SSH, never
actually baked into the launch template via `terraform apply`. The Kustomization genuinely
didn't exist on the cluster until it was applied by hand one more time. A final
`terraform apply` (0 added, 2 changed, 0 destroyed — no instance replacement, just the launch
template's `user_data` for future boots) closed that gap for real. The lesson generalizes: a
manual `kubectl apply` during live debugging fixes *that* node; only a `terraform apply` makes
the fix part of what a replacement node actually boots with.

## Consequences

- A spot reclamation today reaches the state this story set out to prove: a fresh node boots,
  installs Flux, bootstraps the same `GitRepository`/`HelmRelease`, and Flux reconciles the
  last commit on `main` — no `helm upgrade --install` by hand.
- `release.yml` (still unbuilt) inherits the tagging scheme this story's manual bootstrap push
  used (`sha-<short>`) rather than inventing its own.
- The ECR-credential-refresh timer is an acknowledged interim measure. IRSA-equivalent
  per-workload identity, when it lands in Phase 7/8, likely replaces it outright rather than
  coexisting with it.
- Multi-AZ spreads where the node *can* land, but the project is still one instance — a
  genuinely region-wide `t4g.medium` spot shortage (not just the specific AZs already seen)
  would still block a launch. Confirmed live during this very story: several consecutive
  launch attempts failed across all three AZs in the same minutes-long window before one
  succeeded. Not solved here; `t4g.small`/on-demand fallback would be the next lever, and
  isn't built.
- `helmrelease.yaml`'s inline `values:` duplicates `deploy/environments/prod/values.yaml`
  (Decision 8) — a real, open gap. A change to one without the other silently diverges prod's
  real deployment from what Lab 18's rehearsal and CI's helm-lint loop both render. Flagged in
  both files; `release.yml`'s eventual env-values pipeline is the real fix.

## Sources

- Flux `GitRepository`/`HelmChart`/`HelmRelease` v2 API docs (fluxcd.io: source-controller,
  helm-controller) — `valuesFiles` paths are relative to the Source reference;
  `valuesFrom`/`targetPath` merges a ConfigMap/Secret key into a values path.
- `kubernetes/cloud-provider-aws` — the `ecr-credential-provider` kubelet plugin, considered
  and deferred per Decision 2.
- AWS's own `terraform apply` error messages (quoted verbatim in Lab 20) for the AZ-capacity
  findings.
