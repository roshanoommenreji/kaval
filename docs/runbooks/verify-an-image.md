# Verify an image by hand (parts list and build record)

Every image the release workflow builds has a parts list (SBOM), a signed build record ([ADR-0089](../adr/0089-every-image-gets-a-parts-list-and-a-signed-build-record.md)) and, since `KAV-138`, a signature kept in ECR beside it ([ADR-0090](../adr/0090-images-are-signed-keyless-and-the-signature-lives-in-the-registry.md)).
This is how a person reads and checks them. It only reads: nothing here changes an image, a cluster or a pin. For a person, no fix is proposed.

## Signals

- The cluster's check flagged an image (`UnsignedImageSeen`): see [image flagged by the cluster check](image-flagged-by-the-cluster-check.md), then come back here to check the signature by hand
- A library is announced as vulnerable and you need to know which images contain it
- You want proof that the image in ECR came from this repository's release workflow, not from a hand push
- `promote.yml` or a later check says a build record is missing or does not verify
- `promote.yml` stopped at **verify** with `FAIL  every image carries a valid signature…` (or the build record / parts list line): the promotion gate refuses an image that is not signed by `release.yml` on `main` (`KAV-139`, [ADR-0092](../adr/0092-promotion-refuses-an-image-whose-signature-does-not-verify.md))

## Likely causes

1. The image was built before 2026-11-13 (`KAV-137`): it has no parts list and no build record, by design. Rebuild by releasing again; do not hand-make one
2. The release run that built it failed after the push, before the record was stored (a re-run of that release adds it)
3. The parts list's 90 days have passed (the build artifact expires); the copy bound to the digest in GitHub's store does not expire (`KAV-138`)
4. The image was built before the signing step existed (`KAV-138`): it has no signature, by design. Release again; do not hand-sign it
5. The release run stopped at **sign the image**: the build robot could not read ECR (the Terraform permission of `KAV-138` is not applied yet) or Sigstore was unreachable. Apply the permission or re-run later; nothing unsigned was promoted
6. A promotion was refused for a release that was built before signing existed: every release on the passed-staging record up to and including 2026-11-12 has no signature. It cannot be fixed by hand; release again, let it pass staging, and promote that

## Diagnosis

1. Find the release run that built the digest: Actions → **release** → the run for that commit. Each service's job summary lists its `sha-` tag and digest.
2. **The parts list:** `gh run download <run-id> -n sbom-gateway` (and `-n sbom-collector` and so on). It is a CycloneDX JSON file. Which images contain a package:
   `gh run download <run-id> --pattern 'sbom-*'` then search the files for the package name.
3. **The build record**, read straight from GitHub's store by digest (needs no AWS):
   `gh api repos/<owner>/<repo>/attestations/sha256:<digest> --jq '.attestations | length'` is 1 or more.
4. **Check the signature** (needs a read of ECR, so it is a laptop job): log in to the registry with your own AWS login
   (`aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin <account>.dkr.ecr.ap-south-1.amazonaws.com`), then
   `gh attestation verify oci://<account>.dkr.ecr.ap-south-1.amazonaws.com/kaval/gateway@sha256:<digest> --repo <owner>/<repo> --signer-workflow <owner>/<repo>/.github/workflows/release.yml`.
   A pass exits 0 (piped, it prints nothing; add `--format json` to see the signer workflow, the commit and the run). A wrong signer exits 1. Take the digest from ECR (`aws ecr describe-images`), not from a tag you were told.
5. **Check the signature kept in the registry** (cosign; same login as step 4; the workflow uses cosign 3.1.4, see `COSIGN_VERSION`):
   `cosign verify <account>.dkr.ecr.ap-south-1.amazonaws.com/kaval/gateway@sha256:<digest> --certificate-identity https://github.com/<owner>/<repo>/.github/workflows/release.yml@refs/heads/main --certificate-oidc-issuer https://token.actions.githubusercontent.com`.
   A pass prints the signature as JSON and exits 0. The same command with another identity (for example `…/ci.yml@refs/heads/main`) must exit 1. Spell the identity out; never use a pattern. The output contains the registry host (the account number): do not paste it anywhere public. A pass looks like `The cosign claims were validated` and `The code-signing certificate was verified`; a refusal ends with an error and exit 1 (shown on 2026-11-13 for the wrong workflow, the wrong branch and the wrong issuer).
6. **The parts list bound to the digest:** `gh api -X GET repos/<owner>/<repo>/attestations/sha256:<digest> -f predicate_type=https://cyclonedx.org/bom --jq '.attestations | length'` is 1 or more.

7. **Ask the promotion gate's question by hand** (read-only; same login as step 4). `REGISTRY` is `<account>.dkr.ecr.ap-south-1.amazonaws.com`, built in the shell and never pasted anywhere public; `COSIGN` is the path to cosign if it is not on your PATH:
   `AWS_REGION=ap-south-1 REGISTRY=… python scripts/release/promote.py signatures --tag sha-<commit>`.
   It prints three PASS or FAIL lines (signature, build record, parts list) for all four images, with the registry address hidden, and exits 0 (all pass), 1 (refused) or 2 (cannot decide: a tool, the login or the network is missing). This is exactly what `promote.yml` asks.

## Remediation

- Missing record on a new image: re-run the **release** workflow for that commit (a commit already published reuses its image and adds the record; a new commit builds new images and opens a staging-pin pull request you can close). Reversible, touches no running system.
- Missing record on an old image: nothing to fix; release again and promote the new build.
- A promotion refused on signatures: do not look for a way around it. Find out which image failed which line; if the release was built after 2026-11-13 the likely cause is a failed or partial release run (re-run **release** for that commit and look at the sign step), otherwise it is an old release (above). `rollback.yml` is not signature-gated (an old, unsigned version can still be rolled back to, on purpose, [ADR-0092](../adr/0092-promotion-refuses-an-image-whose-signature-does-not-verify.md)).

## Do not

- Do not trust an image because a record exists: check the signer (`--signer-workflow`) and the repository (`--repo`). A record from another workflow or repository is not this one's.
- Do not verify against a tag. Use the digest ECR reports; tags are names, the digest is the image.
- Do not paste the account number into an issue, a pull request or a commit: the registry host contains it.
