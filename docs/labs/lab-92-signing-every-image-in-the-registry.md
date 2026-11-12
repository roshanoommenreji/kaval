# Lab 92 — Signing every image in the registry

**Phase:** 8 (hardening) · **Story:** `KAV-138` · **Decision:** [ADR-0090](../adr/0090-images-are-signed-keyless-and-the-signature-lives-in-the-registry.md) · **Cost:** $0 · **Time:** 40 minutes (Parts 1-3), one release round for Part 5

## What you will build

Each of the five images gets a **signature** stored in ECR beside it. The signature says: this exact digest was published by `release.yml` on `main`. There is no key to keep: GitHub vouches for the
workflow run, Sigstore issues a certificate that lasts minutes and notes the signature in a public log. The parts list from [Lab 91](lab-91-a-parts-list-and-a-signed-build-record-for-every-image.md) is also bound to the
digest, in GitHub's store. The promotion gate refuses an unsigned image since `KAV-139` ([Lab 94](lab-94-the-promotion-gate-checks-the-signature.md)); the cluster does not yet (`KAV-140`).

## Part 1 — Why the build robot needs a read permission

Signing means looking the image up, and checking the signature means reading it back from the registry. The publish role was push-only (ADR-0030). It now gets two more actions
(`ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer`) **on the same five repositories and nothing else**. Read `infra/modules/ci-publish/main.tf`: a new statement, `ReadKavalImagesToSignAndCheckThem`.
Still no delete, no change to a repository, no other repository.

To see the change Terraform would make, without making it (needs `AWS_PROFILE=kaval` and the settings file `terraform.tfvars` that is not in git):

```bash
scripts/ops/tf-init.sh prod
terraform -chdir=infra/envs/prod plan -target=module.ci_publish -var-file=<path to terraform.tfvars>
```

Expected: `Plan: 0 to add, 1 to change, 0 to destroy` — one in-place edit of the role's policy, adding that one statement. `-target` keeps the plan to this module; apply the saved plan only when you mean to.

## Part 2 — Read the workflow change

In `.github/workflows/release.yml`, job `publish`:

| Step | Why there |
|---|---|
| `install cosign (checksum-verified)` | before any AWS access, at a fixed version, refused if the SHA-256 differs (the same treatment as Trivy) |
| `sign the image (keyless) unless it already carries a valid signature` | after the push, for the digest ECR reported. Checks first; signs only if no valid signature exists; checks again; a signature that cannot be read back **stops the release** |
| `actions/attest` with `sbom-path` | binds the parts list to the digest in GitHub's store (not ECR: cosign's statement would carry the registry host, so the AWS account number, into a public log) |
| the store check | now asks for **both** records (build record and parts list) by digest |

Who may have signed is spelled out in full (`SIGNER_IDENTITY`, `SIGNER_ISSUER` at the top of the file), never a pattern. A signature from another workflow, branch, fork or pull request does not verify.

## Part 3 — The tests

```bash
python -m pytest scripts/dev/test_release_supply_chain.py -q
```

Besides the earlier checks they pin: cosign is fetched at a fixed version with a checksum and before AWS access; the signature is made after the push, for the digest, keyless; the identity is exact
(there is no `regexp` anywhere in the file); the role's read statement names exactly two actions on the five repositories, and nothing in the role can delete or change a repository.

Three tests **run the real signing step** (in `bash`) against a stand-in for cosign, so each outcome is shown, not argued:

- already signed → only a check is made, nothing is signed (a re-run changes nothing);
- not signed → check, sign, check again; every check names the exact identity and issuer;
- **negative:** the check never passes → the step fails, so the release stops.

To see that the last test bites, delete the second `check` line from the step and run the tests: two of them fail.

## Part 4 — The registry's cleanup and immutable tags (what was checked, and how)

- ECR tags here are immutable. The older cosign layout stores a signature under a *tag* (`sha256-<digest>.sig`); a second signature would clash. The layout used now is the registry's own **referrer**
  link: the signature is an untagged item that points at the image. No tag, no clash.
- ECR's lifecycle policy removes referrers within 24 hours of their image being removed (AWS documentation). Our rule 1 removes *untagged* images after a week, so one thing is checked rather than
  assumed: that it does not remove signatures of images that still exist. The live proof runs ECR's lifecycle **preview** (read-only) and lists what it would remove.

## Part 5 — The live proof

`release.yml` runs for real only on `main`, so the proof is a normal release, in this order:

1. Merge the story's pull request. **Apply the permission first** (Terraform, with the owner's go): a release run on `main` that carries the sign step before the role can read stops at that step. Nothing
   unsigned is promoted, and the staging pin is not opened.
2. Run **release** from the Actions tab. Each of the five jobs ends the sign step with `signed, and the signature checked`, and the store check lists a `slsa.dev/provenance` and a `cyclonedx` record.
3. By hand (runbook): `cosign verify` on one digest with the exact identity passes.
4. **Negative, by hand:** the same with `--certificate-identity` set to `…/ci.yml@refs/heads/main` is refused.
5. **Re-run:** run release again for the same commit; the sign step says `already carries a valid signature` and adds nothing.
6. **Public log:** fetch the Sigstore log entry of the signature and confirm it does not contain the account number.
7. **Cleanup rule:** ECR's lifecycle *preview* only judges what is old enough today, so it cannot answer this on the day (a signature pushed minutes ago is never old enough). Use a throwaway test instead: Part 6.

### What the live proof showed (2026-11-13, release run 43299508135, commit 152b59e)

- All five `publish` jobs and `propose` succeeded. Each job's sign step ended `sha256:… signed, and the signature checked`, and the store check found one record of each kind (`slsa.dev/provenance` and `cyclonedx`) for the new digests.
- **By hand, right identity:** `cosign verify <registry>/kaval/gateway@<digest> --certificate-identity …/release.yml@refs/heads/main --certificate-oidc-issuer https://token.actions.githubusercontent.com` exited 0 and found one signature.
- **Negative, by hand (all exit 1):** the identity `…/ci.yml@refs/heads/main`; the identity on a different branch (`refs/heads/other`); the right identity with a different issuer (`https://accounts.google.com`).
- **Re-run** (run 43300302606, same commit): every job said `is already published; reusing it` and then `already carries a valid signature from this workflow; nothing to add`. Nothing was signed twice.
- **What ECR holds:** the signature is an untagged item whose manifest has `subject` set to the image's digest and `artifactType` `application/vnd.dev.sigstore.bundle.v0.3+json`. Its signed statement holds only the digest; the public log entry holds only hashes. Neither contains the account number.
- **Parts list:** `gh api -X GET repos/<owner>/<repo>/attestations/<digest> -f predicate_type=https://cyclonedx.org/bom` returns one record.
- **Not as expected:** the proof showed the AWS account number in the public Actions log (the registry address in several steps, the role address as an input). That is not new with this story; it is filed as `KAV-141`.
- Both runs opened a staging-pin pull request for the merge commit (#399, closed unmerged; the second run found the branch already there).

## Part 6 — The cleanup-rule test (a throwaway repository, 3 KB)

1. Create a repository with immutable tags and the tags `Env=dryrun`, `ManagedBy=manual-throwaway`; push any small image; sign it twice with a local key (`cosign generate-key-pair`, `cosign sign --key cosign.key --yes <repo>@<digest>`). Two signature items appear in `aws ecr describe-images` with `artifactMediaType` `application/vnd.dev.sigstore.bundle.v0.3+json` and no tag. Verify with `cosign verify --key cosign.pub`; a different key must be refused.
2. Give the repository a lifecycle rule with `tagStatus: untagged`, `sinceImagePushed`, 1 day (the smallest allowed).
3. After a day plus up to 24 hours, list the repository again. **Both signatures still there** means the untagged rule spares signatures; **gone** means it removes them and rule 1 needs an exception.
4. Delete the repository (`aws ecr delete-repository --force`).

Result: _to be filled in on 2026-11-15._

## What to take away

A signature in the registry is the first thing a *checker* can ask the registry about the image it is about to use. Everything before it was a record you had to go and find. The permission was the
price: two read actions on five repositories. The rule that kept it small: grant exactly what the new step needs, say so in a test, and never let a public log carry the account number.
