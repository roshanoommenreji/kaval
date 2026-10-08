# Lab 32 — `release.yml` stage one: publish by OIDC, pin staging by pull request

**Phase:** 4 · **Time:** about three hours (most of it the two failures below) · **Story:** `KAV-61`
**Cost:** $0. IAM roles and OIDC are free, GitHub-hosted runners (arm64 too) are free for a public
repo, and ECR stores five small images for pennies. Prod's apply added no running resource.

CI could build the images but never publish them (ADR-0010), and the one image prod runs was pushed
by hand from a laptop. This lab gives CI a way to publish, and gives staging the same deploy path
as prod: a tag committed to Git, pulled by Flux. The decisions and the alternatives are in
[ADR-0030](../adr/0030-ci-publishes-images-by-oidc-and-staging-is-pinned-by-pull-request.md). This
page is the build, the live run and the two things that went wrong.

**Result:** a merged change produces five scanned arm64 images in ECR and a pull request that points
staging at them, in **1 min 47 s**, with no stored AWS key and no laptop. Not built yet: the smoke
test, the "passed staging" record, version tags and the change record (the next stage).

---

## 1. Check what is already in the account

Before writing any IAM, look at what exists. The account already had a GitHub OIDC provider:

```bash
aws iam list-open-id-connect-providers
aws iam get-open-id-connect-provider --open-id-connect-provider-arn <that arn> \
  --query "{created:CreateDate,client:ClientIDList,tags:Tags}"
```

It was created on 2026-09-26 and tagged `Project=stock-trader`: another project's Terraform owns it.
There is one per account, so creating a second would fail, and importing it would put it under this
repo's `terraform destroy`. The module therefore **looks it up** (`data "aws_iam_openid_connect_provider"`)
and manages only its own role.

## 2. The role (`infra/modules/ci-publish`)

- **Trust:** `sts:AssumeRoleWithWebIdentity` from that provider, audience `sts.amazonaws.com`, and the
  token's `sub` must be exactly the repo on `refs/heads/main` (`StringEquals`, no wildcard).
- **Permissions:** `ecr:GetAuthorizationToken` (account-wide by design; grants nothing by itself) and,
  on the five Kaval repositories only: upload layers, `PutImage`, `DescribeImages`. No delete, nothing
  else. Tags are immutable, so it cannot overwrite an image.
- **Also:** `kaval/backup` joins the ECR module (CI builds five images; there were four repositories).

Wired into `infra/envs/prod` (that is where ECR lives). The plan, read before anything was applied:

```
module.ci_publish.aws_iam_role.publish will be created
module.ci_publish.aws_iam_role_policy.publish will be created
module.ecr.aws_ecr_repository.this["backup"] will be created
module.ecr.aws_ecr_lifecycle_policy.this["backup"] will be created
module.iam.aws_iam_role_policy.ecr_pull will be updated in-place     (node may pull backup too)
Plan: 4 to add, 1 to change, 0 to destroy.
```

Applied from the saved plan file. The role's ARN contains the account id, so it goes into a repository
**variable**, never a file:

```bash
gh variable set AWS_PUBLISH_ROLE_ARN --body "$(terraform -chdir=infra/envs/prod output -raw ci_publish_role_arn)"
```

One repository setting was turned on for the bot's pull request: Settings → Actions → "Allow GitHub
Actions to create and approve pull requests".

## 3. The workflow (`.github/workflows/release.yml`)

Two jobs, and neither has both powers:

| Job | Can reach AWS? | Can write the repo? |
|---|---|---|
| `publish` (5 in parallel, one per image) | Yes: `id-token: write`, assumes the role | No: `contents: read` |
| `propose` | No: no `id-token` | Yes: pushes a branch, opens a pull request |

`publish`, per image: build on a native arm64 runner → check arm64 and non-root → Trivy scan (fails on
fixable HIGH/CRITICAL) → assume the role → push `sha-<7 chars>` (or reuse it if that tag exists, so a
re-run is safe) → read the digest back **from ECR**. `propose` downloads the digests, runs
`scripts/release/pin_staging.py` (rewrites the four tags in `helmrelease.yaml` and
`values.yaml` together, refuses if either file has the wrong number), and opens the pull request with
a digest table. It does not push to `main`.

It starts on a push to `main` that changes an image's inputs, or by hand
(`gh workflow run release.yml --ref main`). The pin pull request touches only `deploy/`, so merging it
does not start another release. Confirmed: merging the pin pull request started no run at all.

## 4. Failure one: Trivy stopped the backup image (the gate working)

Before the first release, the pull request's own CI failed on `image backup` with **20 HIGH findings**:
five CVEs in four Debian `python3.11` packages that `awscli` pulls in, all fixed in `3.11.2-6+deb12u9`.
The same check had passed hours earlier on the previous pull request. Cause: CI's layer cache held an
`apt-get` result from before Debian published the fix. The Dockerfile already carried comments about
exactly this pattern (an earlier perl and pcre2 case). Naming the four packages on the install line
changes that instruction, which forces a fresh `apt-get`; the check went green. The same scan in
`release.yml` would have refused to publish the image, which is the reason it is there.

## 5. Failure two: the role refused GitHub (the subject claim)

The merge started the first release. All five `publish` jobs failed at the same step:

```
Retry AssumeRole: attempt 1 of 12 failed: Could not assume role with OIDC:
Not authorized to perform sts:AssumeRoleWithWebIdentity.
```

My trust policy asked for `repo:roshanoommenreji/kaval:ref:refs/heads/main`. The job's token said
something else. CloudTrail records the failed attempts with the identity AWS saw:

```bash
aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=AssumeRoleWithWebIdentity
# userName: repo:roshanoommenreji@37763145/kaval@1388844431:ref:refs/heads/main
gh api repos/roshanoommenreji/kaval/actions/oidc/customization/sub
# {"use_immutable_subject":true, "sub_claim_prefix":"repo:roshanoommenreji@37763145/kaval@1388844431"}
```

This repository uses GitHub's **immutable subject** format (the default for new repositories): the
owner and repository carry numeric ids. That is the stricter form, because someone who later takes a
released repo name gets different ids. The fix was one line in the trust condition
(`github_repo_claim`), planned (0 add, 1 change) and applied. The role refusing a subject it did not
recognise is the security working; the cost was a wrong guess about the claim's shape.

Lesson for anyone doing this: do not guess the `sub`. Read it from the repo's OIDC customisation
setting (or CloudTrail after one failed attempt) before writing the trust policy.

## 6. The live run

`gh workflow run release.yml --ref main` → **success in 1 min 47 s**:

| Job | Time |
|---|---|
| publish ×5 (parallel) | 67 s (backup) to 84 s (gateway) |
| propose | 12 s |

```
sha-2459417  kaval/gateway    sha256:e0daaa15...a4820
             kaval/agent      sha256:022d9904...d5
             kaval/executor   sha256:c98df639...924
             kaval/collector  sha256:fa98daeb...a98
             kaval/backup     sha256:80e82f26...b7
```

The digests in the pull request table matched `aws ecr describe-images` for every repository. The
first image under each repository from the 2 October laptop push (`sha-ffb436b`) is still there, which
is what prod runs: **prod did not move**.

The pull request `chore(release): point staging at sha-2459417` changed exactly eight lines, four in
each of `deploy/gitops/staging/helmrelease.yaml` and `deploy/environments/staging/values.yaml`.

## 7. Failure three, smaller: who starts CI on the bot's pull request

I expected GitHub not to run CI at all for a pull request opened with the workflow token, and added a
`gh workflow run ci.yml --ref <branch>` as a workaround. What happened: GitHub *did* create the
`pull_request` CI run, but held it as `action_required` (a first-time contributor rule applied to the
bot). The dispatched run succeeded, yet the pull request stayed `BLOCKED` with no checks listed.
Approving the held run (`gh api -X POST repos/roshanoommenreji/kaval/actions/runs/<id>/approve`, or
"Approve and run" in the Actions tab) ran all ten checks green and the pull request became `CLEAN`.

So the workaround did not help and was removed (along with the `actions: write` permission it needed).
The approval stays, and is a reasonable human step before staging moves. It is one click per release.

## What this does not do

- **No smoke test, no "passed staging" record.** Staging has the new tag only once the pin merges and
  staging is up. Proving the digest works there, and recording it so `promote.yml` can refuse an
  untested one, is the next stage.
- **No version bumps, tags or release notes yet** (ADR-0013).
- **Prod is untouched.** `promote.yml` is the only thing that will ever write `deploy/gitops/prod/`.
- **The cluster does not need any of this to run.** The node pulls with its own role (ADR-0025); this
  role only matters for publishing.

## Reproduce

1. Read your repo's `sub` claim (`gh api repos/<owner>/<repo>/actions/oidc/customization/sub`).
2. Apply `infra/modules/ci-publish` with that claim and your ECR repository ARNs; set the
   `AWS_PUBLISH_ROLE_ARN` repository variable; enable "Allow GitHub Actions to create pull requests".
3. `gh workflow run release.yml --ref main`, then approve the held CI run on the pull request it opens.
4. Compare the table's digests with `aws ecr describe-images --repository-name kaval/<svc>`.
