# ADR-0089: every image gets a parts list and a signed build record

**Status:** Accepted on 2026-11-13 (Roshan: "go", starting the supply-chain line of Phase 8). Built, tested and proven live on 2026-11-13 (release run 43291746398).
**Date:** 2026-11-13
**Related:** [ADR-0010](0010-ci-pipeline-and-supply-chain.md) (CI and the supply chain), [ADR-0030](0030-ci-publishes-images-by-oidc-and-staging-is-pinned-by-pull-request.md) (CI publishes by OIDC, push-only),
[ADR-0032](0032-promote-workflow-and-the-promotion-guard.md) (the promotion guard), [threat model](../architecture/threat-model.md) T-SUPPLY-2, [Lab 91](../labs/lab-91-a-parts-list-and-a-signed-build-record-for-every-image.md),
`KAV-137` (this story), `KAV-138` (cosign signatures in the registry), `KAV-139` (the promotion gate checks them), `KAV-140` (the cluster refuses unsigned images)

## Context

Kaval builds each image once, scans it with Trivy, pushes it to ECR and promotes that exact digest. What it could not say was **what is inside** a given image (which packages, at which versions) or
**who built it**. If a library is found to be bad next month, "which of our images contain it?" has no answer except rebuilding and rescanning. And nothing proves that the digest in ECR came from this
repository's release workflow rather than from somebody with the push role.

The roadmap line asks for the industry set: a parts list (SBOM) per image, signed images, build records (provenance), and signatures checked before deploy. That is four steps with different costs and
risks, so it is four stories. This ADR covers the first: the parts list and the build record. It changes nothing that runs.

## Options (what real companies do)

1. **Cosign signs everything in the registry** (signature, SBOM and provenance as artifacts next to the image, checked by a cluster policy). The most complete pattern. It needs the build robot to read
   its own repositories, which is an AWS permission change, so it is the second story, not the first.
2. **GitHub's own attestations** (`actions/attest-build-provenance`): the workflow signs a statement about the digest with its own identity (Sigstore, no key to keep) and GitHub stores it. Anyone can check it
   with `gh attestation verify`. No AWS change at all. It does not by itself stop a cluster from running an unsigned image.
3. **Build-tool built-ins** (BuildKit's `--sbom` and `--provenance`). Not usable here: the build loads the image into the runner and pushes it only after the scan, and a loaded image does not carry
   BuildKit's attachments.

## Decision

**Option 2 now, option 1 next** (`KAV-138`), then the checks that make either of them matter (`KAV-139`, `KAV-140`).

- **The parts list.** After the Trivy scan and **before** any AWS access, the release job asks the same pinned, checksum-verified Trivy for a CycloneDX list of the image it just scanned (the local image the push
  later uploads, so the list is of what ships). The list carries parts only, no vulnerability findings (those are the scan's job and change daily). The step fails if the file is not CycloneDX or lists
  fewer than 20 parts, because an empty list is worse than none. The file is kept as a build artifact (`sbom-<service>`) for 90 days.
- **The build record.** After the push, `actions/attest-build-provenance` (pinned by commit, as every action here) signs a statement that *this digest* was built by *this workflow* from *this commit*, with
  the run's own identity. GitHub stores it. A following step asks GitHub's store for a record on that exact digest, retries for about 30 seconds, and **fails the release** if there is none.
- **Two choices that matter.**
  - The record's name is `kaval/<service>`, with **no registry host**. Records for a public repository are public and go into a public log; the host contains the AWS account number, which must never be
    published (CLAUDE.md constraint 5). The digest is what identifies the image, so nothing is lost.
  - The step in the release only proves the record **exists**. Checking its *signature* needs a read of the image in ECR, and the build robot's role is deliberately push-only. The signature is checked
    where a read is allowed: by hand ([runbook](../runbooks/verify-an-image.md)), and in `KAV-139` at the promotion gate.
- **Permissions.** The `publish` job gains one: `attestations: write`. The `propose` job (the one that writes to the repository) gains nothing, and still cannot sign or reach AWS. The top level stays
  `permissions: {}`. A test pins all three.
- **Re-runs.** ECR tags are immutable, so a re-run for a commit that is already published reuses the pushed image. It signs another record for the same digest, which is harmless. A run for a *new* commit builds new images and opens a staging-pin pull request, even if the commit changed no image (seen on the proof run; the pull request was closed).

## Consequences

- **Cost:** $0. No AWS resource; GitHub Actions minutes for a few extra seconds per image; artifacts of a few hundred kilobytes.
- **What it does not do yet:** nothing *checks* anything. A person can verify an image by hand today; the promotion gate does it after `KAV-139`; the cluster after `KAV-140`. Until then the threat model's
  T-SUPPLY-2 stays a gap, with its first two mitigations built.
- **Failure mode:** if the parts list or the record cannot be made, the release stops **before or right after the push** and no promotion can follow; images already running are untouched. Turning the feature
  off is deleting the three steps; nothing else depends on them yet.
- **Honest limit:** this run's identity is trusted because GitHub's Sigstore says so. Someone who can change `release.yml` on `main` can change what is signed; branch protection and the required checks are
  the guard there (T-SUPPLY-3, accepted).
- **Proven live (2026-11-13):** a manual release run on `main` made all five parts lists and build records and each job found its record in the store. `gh attestation verify` on the gateway digest passed for `release.yml` on `main` and was refused for `ci.yml`. Details in Lab 91, Part 4.
