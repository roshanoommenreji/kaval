# Verify an image by hand (parts list and build record)

Every image the release workflow builds has a parts list (SBOM) and a signed build record ([ADR-0089](../adr/0089-every-image-gets-a-parts-list-and-a-signed-build-record.md)).
This is how a person reads and checks them. It only reads: nothing here changes an image, a cluster or a pin. For a person, no fix is proposed.

## Signals

- A library is announced as vulnerable and you need to know which images contain it
- You want proof that the image in ECR came from this repository's release workflow, not from a hand push
- `promote.yml` or a later check says a build record is missing or does not verify

## Likely causes

1. The image was built before 2026-11-13 (`KAV-137`): it has no parts list and no build record, by design. Rebuild by releasing again; do not hand-make one
2. The release run that built it failed after the push, before the record was stored (a re-run of that release adds it)
3. The parts list's 90 days have passed (the build artifact expires); a later story keeps it in the registry (`KAV-138`)

## Diagnosis

1. Find the release run that built the digest: Actions → **release** → the run for that commit. Each service's job summary lists its `sha-` tag and digest.
2. **The parts list:** `gh run download <run-id> -n sbom-gateway` (and `-n sbom-collector` and so on). It is a CycloneDX JSON file. Which images contain a package:
   `gh run download <run-id> --pattern 'sbom-*'` then search the files for the package name.
3. **The build record**, read straight from GitHub's store by digest (needs no AWS):
   `gh api repos/<owner>/<repo>/attestations/sha256:<digest> --jq '.attestations | length'` is 1 or more.
4. **Check the signature** (needs a read of ECR, so it is a laptop job): log in to the registry with your own AWS login
   (`aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin <account>.dkr.ecr.ap-south-1.amazonaws.com`), then
   `gh attestation verify oci://<account>.dkr.ecr.ap-south-1.amazonaws.com/kaval/gateway@sha256:<digest> --repo <owner>/<repo> --signer-workflow <owner>/<repo>/.github/workflows/release.yml`.
   A pass names the workflow, the commit and the run. Take the digest from ECR (`aws ecr describe-images`), not from a tag you were told.

## Remediation

- Missing record on a new image: re-run the **release** workflow for that commit (it reuses the pushed image and adds the record). Reversible, touches no running system.
- Missing record on an old image: nothing to fix; release again and promote the new build.

## Do not

- Do not trust an image because a record exists: check the signer (`--signer-workflow`) and the repository (`--repo`). A record from another workflow or repository is not this one's.
- Do not verify against a tag. Use the digest ECR reports; tags are names, the digest is the image.
- Do not paste the account number into an issue, a pull request or a commit: the registry host contains it.
