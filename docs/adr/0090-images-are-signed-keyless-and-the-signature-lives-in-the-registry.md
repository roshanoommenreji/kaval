# ADR-0090: images are signed keyless, and the signature lives in the registry

**Status:** Accepted on 2026-11-13 (Roshan: "go", the second story of the supply-chain line). Built, tested and proven live on 2026-11-13 (permission applied on his go, release run 43299508135). One follow-up is open: whether ECR's "untagged images expire" rule spares signatures (see Consequences).
**Date:** 2026-11-13
**Related:** [ADR-0089](0089-every-image-gets-a-parts-list-and-a-signed-build-record.md) (the parts list and the build record, the story before this one),
[ADR-0030](0030-ci-publishes-images-by-oidc-and-staging-is-pinned-by-pull-request.md) (the publish role, push-only until this ADR), [ADR-0010](0010-ci-pipeline-and-supply-chain.md),
[threat model](../architecture/threat-model.md) T-SUPPLY-2, [Lab 92](../labs/lab-92-signing-every-image-in-the-registry.md), [runbook](../runbooks/verify-an-image.md),
`KAV-138` (this story), `KAV-139` (the promotion gate refuses an image whose signature does not verify), `KAV-140` (the cluster refuses unsigned images)

## Context

After `KAV-137` every image has a parts list and a signed build record, but both live **outside** the registry (a workflow artifact and GitHub's store). Someone who pulls `kaval/gateway@sha256:...`
from ECR sees nothing that says who published it, and a later check (the promotion gate, a cluster policy) wants to ask the **registry** about the image it is about to use. The roadmap line asks for
signed images with cosign, keyless, in ECR.

## Options (what real companies do)

1. **A long-lived signing key** (kept in a vault or a CI secret). The old way. The key can leak, must be rotated, and whoever holds it can sign anything.
2. **A cloud key service** (AWS KMS, AWS Signer with Notation). Works well, but is tied to one cloud and needs more AWS permissions and a per-signature cost.
3. **Keyless signing with the CI identity** (cosign + Sigstore). The workflow proves who it is to GitHub for one run; Sigstore issues a certificate that lasts minutes and notes the signature in a public log.
   There is nothing to keep. This is the current standard for open-source and for GitHub-based teams.

## Decision

**Option 3.** Signed by `release.yml`, on `main`, as the run's own identity.

- **What is signed:** the **digest** ECR reported for the pushed image, never a tag. cosign `3.1.4`, fetched at a fixed version and refused if its SHA-256 differs (the same treatment as Trivy).
- **Where it lives:** in ECR, beside the image, stored as a *referrer* (the registry's own record that this item is about that image). ECR has supported this since 2024. A signature stored this way is
  **untagged**, so ECR's immutable-tag rule cannot clash with it (the older `sha256-....sig` tag scheme would), and ECR's lifecycle policy removes it within 24 hours of the image it belongs to being
  removed (documented behaviour, checked in the live proof, below).
- **Skip what is already signed.** The step first asks "does this digest already carry a valid signature from this workflow?". If yes, nothing is added, so a re-run of a published commit changes nothing.
  If not, it signs, then checks the signature straight away. A signature that cannot be read back **stops the release** before the staging pin is proposed.
- **Who may have signed is written out in full**, never as a pattern: `https://github.com/roshanoommenreji/kaval/.github/workflows/release.yml@refs/heads/main`, issued by GitHub's token service. A signature
  made by another workflow, branch, fork or pull request does not verify. A test pins this and a test runs the real step against a stand-in signing tool to show the three outcomes (already signed, signed
  now, cannot be verified).
- **The parts list joins the build record** in GitHub's store (`actions/attest`, pinned by commit), so "these are the parts of this exact image" outlives the 90-day workflow artifact. It stays in GitHub's
  store, not in ECR, because cosign's statement would name the registry host and so the AWS account number, in a log that is public.
- **The AWS change.** The publish role gains `ecr:BatchGetImage` and `ecr:GetDownloadUrlForLayer` on the **same five repositories** it already pushes to. Signing needs to look the image up and checking needs to
  read the signature back. It still cannot delete, change a repository, or read any other repository (tests pin all of that). The change is one in-place edit of one IAM policy; Terraform's plan was shown to
  Roshan, and nothing is applied without his go.

## Consequences

- **Cost:** $0. IAM is free; a signature is a few kilobytes in ECR; Sigstore is free; a few seconds per image in CI.
- **What it does not do yet:** nothing *refuses* an unsigned image. The promotion gate does after `KAV-139` (built 2026-11-13, [ADR-0092](0092-promotion-refuses-an-image-whose-signature-does-not-verify.md)), the cluster watches for it since `KAV-140` ([ADR-0093](0093-the-cluster-checks-image-signatures-with-kyverno-watching-first.md), watch-only; refusing is `KAV-142`). Until then T-SUPPLY-2 stays partly open.
- **Order matters:** the permission must be applied **before** the first release on `main` that carries the signing step, or that release stops at the sign step (nothing unsigned is promoted; staging's
  pin pull request is not opened). The runbook says so.
- **Failure and off switch:** the release stops at the sign step if Sigstore or ECR cannot be reached; re-run it later. Turning the feature off is removing the sign step and the read statement; images
  already signed stay signed.
- **Public traces (measured, not assumed):** the signed statement holds only the image's digest (cosign 3 does not write the registry name into it) and the public Sigstore log entry holds only hashes. Neither contains the account number; the live proof read both from a real signature. **But the proof found a different leak that was already there:** the GitHub Actions logs of this public repository print the registry address (so the account number) in plain text, because `configure-aws-credentials` masks the account number only when asked and the role address is a repository *variable*. I had assumed the log masks it while building this story; it does not. It is filed as `KAV-141`; the signing step adds a few more lines of the same kind and no new kind of leak.
- **Open follow-up (the cleanup rule):** ECR's lifecycle documentation says signatures are removed with their image, but not whether our rule 1 (untagged images expire after 7 days) also removes signatures, which are untagged. A throwaway repository with a 1-day version of the rule has held two signatures since 2026-11-13 01:48 IST; the result is read on 2026-11-15 and recorded in Lab 92. If signatures are removed, rule 1 gets an exception before any signed image is a week old in a way that matters (production's pins are weeks old).
- **Honest limit (unchanged from ADR-0089):** someone who can change `release.yml` on `main` can change what is signed; branch protection and the required checks are the guard (T-SUPPLY-3, accepted).
