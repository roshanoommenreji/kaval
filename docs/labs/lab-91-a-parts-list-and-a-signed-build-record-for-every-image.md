# Lab 91 — A parts list and a signed build record for every image

**Phase:** 8 (hardening) · **Story:** `KAV-137` · **Decision:** [ADR-0089](../adr/0089-every-image-gets-a-parts-list-and-a-signed-build-record.md) · **Cost:** $0 · **Time:** 30 minutes (Parts 1-3), one release round for Part 4

## What you will build

Two additions to `release.yml`, for each of the five images:

1. a **parts list** (SBOM): every package inside the image, in the standard CycloneDX format;
2. a **signed build record** (provenance): a statement, signed with the workflow run's own identity, that this digest was built by this workflow from this commit.

Nothing that runs changes. Nothing checks them yet; that is `KAV-139` (the promotion gate) and `KAV-140` (the cluster).

## Part 1 — Make a parts list yourself (needs Docker; no AWS)

Use the same Trivy version the release pins (`TRIVY_VERSION` in `release.yml`):

```bash
docker pull alpine:3.20
mkdir -p sbom-trial
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$PWD/sbom-trial:/out" \
  aquasec/trivy:0.74.0 image --format cyclonedx --no-progress --output /out/sbom.cdx.json alpine:3.20
python -c "import json;d=json.load(open('sbom-trial/sbom.cdx.json'));print(d['bomFormat'],len(d['components']),'parts;',len(d['vulnerabilities']),'findings')"
```

Expected: `CycloneDX 15 parts; 0 findings`. Two things to notice. First, the list has **no vulnerability findings**: it says what is inside, not what is wrong with it (the scan does that, and its answer
changes daily). Second, `--scanners none` is not a thing in this Trivy version; the CycloneDX format simply leaves vulnerabilities out unless you ask.

## Part 2 — Read the workflow change

In `.github/workflows/release.yml`, job `publish`:

| Step | Where | Why there |
|---|---|---|
| parts list + upload | right after the Trivy scan, **before** the AWS login | made from the same local image that is pushed; a failure publishes nothing |
| `digest=` output on the push step | end of the push step | the record needs the digest ECR reported, not a tag |
| `actions/attest-build-provenance` | after the push | signs "this digest, this workflow, this commit" with the run's identity; no key to keep |
| "the build record is in GitHub's store" | after that | asks GitHub for a record on the exact digest (up to 5 tries, 6 s apart) and fails the release if none |

Three details worth understanding: the record's name is `kaval/<service>` with **no registry host**, because records on a public repository are public and the host contains the AWS account number; the job
gets exactly one new permission (`attestations: write`) while the `propose` job, which writes to the repository, gets none and still cannot sign; and the in-release check only proves the record **exists**,
because verifying a signature means reading the image in ECR, which the push-only role may not do (since `KAV-138` it can read, and [Lab 92](lab-92-signing-every-image-in-the-registry.md) signs and checks in the release).

## Part 3 — The tests

```bash
python -m pytest scripts/dev/test_release_supply_chain.py -q
```

They pin: the parts list is made from the scanned image and *before* the AWS login; it is checked (CycloneDX, not suspiciously short) and kept 90 days with a missing file as an error; the record is signed
for the digest ECR reported; the attestation action is pinned to a full commit; the public name carries no registry host; the store check fails the release when no record exists (and a `|| true` version
would not pass the test); `publish` has exactly `id-token`, `attestations` and `contents: read` while `propose` has neither of the first two; and the AWS role had no read permission (`KAV-138` changed that test on purpose, see [Lab 92](lab-92-signing-every-image-in-the-registry.md)).

## Part 4 — The live proof (done 2026-11-13, run 43291746398)

`release.yml` can only run for real on `main`, so the proof is a normal release:

1. Merge the story. Run **release** from the Actions tab (Run workflow on `main`). It builds the merge commit's five images afresh (a re-run for a commit that is already published reuses them), so expect five new images in ECR (a few cents of storage at most) and a staging-pin pull request that you should close, because the commit changed no image, or wait for the next service change.
2. Each of the five `publish` jobs shows `N parts listed` and `1 build record(s) stored for sha256:…`. The run page lists `sbom-gateway` … `sbom-backup` under Artifacts.
3. By hand, follow [the runbook](../runbooks/verify-an-image.md) once: download one parts list and open it; run `gh attestation verify` on one digest. It must name the release workflow and the commit.
4. **Negative, by hand:** run the same `gh attestation verify` with `--signer-workflow <owner>/<repo>/.github/workflows/ci.yml`. It must refuse, because the build record was signed by `release.yml`, not
   `ci.yml`.

### What the live proof showed (2026-11-13)

- All five `publish` jobs and `propose` succeeded; each printed its count (for example `143 parts listed` for the collector, `151` for backup) and `1 build record(s) stored for sha256:…`. The five `sbom-<service>` artifacts are on the run page.
- By hand, `gh attestation verify oci://<registry>/kaval/gateway@<digest> --repo … --signer-workflow …/release.yml` exited 0 (in a pipe it prints nothing; add `--format json` to see who signed). The signer was
  `…/.github/workflows/release.yml@refs/heads/main`, the commit `26f2d52…`, the run `43291746398`.
- **Negative:** the same command with `--signer-workflow …/ci.yml` exited 1 ("verifying with issuer sigstore.dev") — refused.
- **Re-run:** running the same release again reused all five images (`is already published; reusing it`) and each job then reported `2 build record(s) stored`: a re-run does not fail, it adds a second record for the same digest. It also reopened the staging-pin pull request (#396, closed unmerged).
- A side effect I had not planned for: the run opened a staging-pin pull request (#395) for the merge commit. It was closed unmerged; staging is deleted and nothing pins to it.

## What to take away

A parts list answers "what is in it"; a build record answers "who made it". Neither stops anything until something *checks* them, which is why the checks are their own stories. The record is stored where
the build robot may write without any new AWS right; verifying it needs a read, so that waits for the places that are allowed one.
