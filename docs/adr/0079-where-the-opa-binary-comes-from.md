# ADR-0079: Where the OPA binary in the images comes from

**Status:** Accepted on 2026-11-07. Roshan chose option C (`decide build`). It is built in `KAV-132`, whose merge waits until after the 8 November morning teardown.
**Date:** 2026-11-07
**Related:** [ADR-0033](0033-dated-trivy-exception-for-the-bundled-opa-binary.md) (the dated exceptions; its decision 4 asks this question),
[ADR-0017](0017-opa-policy-engine-and-earned-autonomy.md) (why OPA), [ADR-0010](0010-ci-pipeline-and-supply-chain.md) (supply chain and
scanning), `KAV-64`, `KAV-65`, `KAV-130`, `KAV-131`, `KAV-132`, [Lab 79](../labs/lab-79-building-opa-ourselves-and-proving-it.md)

## Context

The agent and executor images carry `opa`, the engine that sorts every proposed action into `auto`, `ask` or `never`
([ADR-0017](0017-opa-policy-engine-and-earned-autonomy.md)). Today it is the file OPA publishes on its GitHub release page,
checked against the checksum on the same page. OPA publishes nothing else to check against: no signature, no attestation.

Since 2026-10-09 the image scan (Trivy) has flagged that one file five times:

| Finding | What it is | Red from | Cleared | Days |
|---|---|---|---|---|
| CVE-2026-78667, CVE-2026-97031 | Go standard library (`net/http`, `crypto/tls`) | 2026-10-09 | 2026-10-21, OPA v1.21.3 | 12 |
| CVE-2026-78669 | library `golang.org/x/net` 0.58 | 2026-10-10 | 2026-10-21, OPA v1.21.3 | 11 |
| CVE-2026-111943 | Go standard library (`crypto/x509`) | about 2026-10-20 | 2026-10-30, OPA v1.21.4 | 10 |
| CVE-2026-78661 | library `golang.org/x/net` 0.60 (HTML parser) | 2026-11-07 | open, excepted to 2026-11-21 | 14 or more |

Each time, the fixed Go version or library already existed on the day the scan went red. What we waited for was OPA's next
release (they come about weekly: 12, 19, 23 and 29 October, 5 and 6 November), and, for a library, for OPA to raise it. A standing
exception was in force on 21 of the first 29 days. A finding turns both image checks red on **every** pull request, a
documentation-only one included, and stops every publish, until someone approves a dated exception and writes it up
([ADR-0033](0033-dated-trivy-exception-for-the-bundled-opa-binary.md): one CVE per entry, one file path, an end date).

Upstream will not hurry for a finding like the fifth. OPA's own release check is `govulncheck` (every Sunday to Thursday, against the
latest release), which reports a library flaw only when OPA's code can reach the flawed function. Our scanner matches library
*versions*. The two will keep disagreeing about library findings, so waiting for upstream is not a plan.

[ADR-0033](0033-dated-trivy-exception-for-the-bundled-opa-binary.md) decision 4 names a fifth exception as the point to stop adding them
and decide whether to build OPA from source. `KAV-130` approved one more, to 2026-11-21, so that this could be decided calmly. This is
that decision.

## What the trial found

[Lab 79](../labs/lab-79-building-opa-ourselves-and-proving-it.md) builds OPA v1.21.4 (the version in use) from source on a laptop and tests it. Nothing was merged,
deployed or run on AWS.

| Question | Answer |
|---|---|
| Is the source what OPA released? | Yes. The tag `v1.21.4` is commit `05c8451`, agreed by the source archive's header, GitHub's API and `git ls-remote`. The build fetches that commit and stops if it checked out anything else. |
| Is a rebuild the same program? | Yes. Built untouched in the Go image OPA's own recipe names (`golang:1.27.3-trixie`), it has the same 94 libraries and build settings as the official file and is within 0.04% of its size (60.30 and 60.32 MB). `opa version` reports the same details and Kaval's 29 policy tests pass. Asked a grid of policy questions the way the agent asks them, the two files gave identical answers: 1,000 of 1,000 on amd64 (natively) and 308 of 308 on arm64 (under emulation), once with the real, empty promotions file and again with one promotion added so that `auto` can happen. A control (the same file asked against the two data sets) differed on exactly the questions where a promotion makes the answer `auto` (1 on amd64, 4 on arm64), so the comparison can see a difference. |
| What does the fix change? | One command, `go get golang.org/x/net@v0.62.0`, raises six libraries inside the program: `x/net` 0.60.0 to 0.62.0 and five siblings from the same Google repository (`crypto`, `sync`, `sys`, `term`, `text`). |
| Is the flawed code even in the program? | **No.** The flawed package, `x/net/html`, contributes zero functions to the official file or to the rebuilds; only `http2`, `idna` and a few helpers from that library are in it. Trivy matches the library's version, not what is used. So the exception's safety statement, until now argued from how OPA is used, is checked against the file. |
| Does the rebuild pass the scan? | **Yes, with no exception.** The same Trivy and flags CI uses and an empty exception list: the official file fails with CVE-2026-78661 and nothing else (the control), the rebuild with the fix passes. |
| Can it be built again to the byte? | Yes. Two builds from empty caches gave identical files, and so did a build through a Dockerfile stage with `docker buildx` (arm64 and amd64), the way CI would. A wrong expected checksum stops that build and leaves no file. |
| What does it cost? | About 2 minutes a build on 4 CPUs (roughly a GitHub runner), 3 min 3 s when every library is also downloaded, 2 min 46 s through `buildx` on 12 CPUs. No AWS cost. The file is the same size, so the images do not grow. |
| What went wrong on the way? | With no git tag at the commit, Go stamps the program with a made-up version (`v0.0.0-<date>-<commit>`) and scanners then report OPA itself as old: three old Trivy findings and one `govulncheck` finding, none real. The recipe tags the checkout, as the official build does (`v1.21.4+dirty`). |
| What is next? | `govulncheck` already reports a different library in the **official** file, `klauspost/compress` 1.20.0 (GO-2026-6865, fixed in 1.20.2), which Trivy does not yet; it is probably the sixth finding. A second override in the same recipe cleared it, and `govulncheck` then reported no vulnerabilities. |
| OPA's own tests | OPA's whole unit-test suite (its slow tests left out) on the fixed tree, 12 minutes on 4 CPUs: 129 packages pass and one test failed once, `TestActivateV1Bundles` in OPA's SDK (`context deadline exceeded`, a timing limit, on a machine busy with other tests). Run ten times in a row it failed once on the **untouched** tag and not at all on the fixed tree, and the whole package passed three times on both: a timing-sensitive test, not an effect of the library change. None of OPA's own code imports `x/net` directly. |

## Options

| | **A.** Keep downloading the release; keep dated exceptions | **B.** A, plus a proof for each exception | **C.** Build OPA ourselves from its release commit | **D.** A vendor's rebuilt OPA |
|---|---|---|---|---|
| What real teams do | Most small teams: pin the vendor's file and checksum, scanner exceptions with an end date | Security teams with a triage process: a reachability check (`govulncheck`), then a VEX statement ("not affected, because...") that Trivy can read | Teams with vulnerability deadlines, and the hardened-image vendors themselves: fetch pinned source, raise the fixed dependency with `go get`, build | Pay a vendor to do C: Chainguard, Docker Hardened Images |
| A finding is cleared in | 10 to 14 days (OPA's next release) | Not cleared, excused with evidence | The day the fixed Go image or library exists | The vendor's pace |
| Covers the Go standard-library findings (3 of the 5) | Only when OPA ships a newer Go | No: OPA links the server and TLS code, so the checker reports them too | Yes, with a newer Go image | Yes |
| Cost | $0, and about an hour of our time per finding | $0, plus a CI step and a VEX file to keep | $0 in AWS and CI minutes; about 2½ minutes of build when a pin changes; a recipe to own | Free tiers do not fit (below) |
| Verdict | What we do today; it keeps recurring | A good addition later, not a fix | **Recommended** | Not available free in the form we need |

**On B.** It would have settled the fifth finding with evidence (the flawed package is not in the binary, below) but not the three
standard-library ones, and it adds a CI step and a file to maintain for something that still has to be approved each time.

**On D.** Chainguard's free images carry only the `latest` tag; versioned tags are the paid tier ([documentation](https://www.chainguard.dev/docs/chainguard/containers/overview/index.md)),
so a pinned, repeatable dependency is not possible on the free tier. Docker made its hardened images free under Apache 2.0 in December 2025
([InfoQ](https://www.infoq.com/news/2025/12/docker-hardened-images)); its catalogue lists OPA-based tools (Gatekeeper, kube-mgmt), but a search found no plain `opa` image,
so it is unconfirmed either way and not worth building a plan on.

**Not now (E).** Compile the policy to WebAssembly and evaluate it inside Python, or replace the engine. That redesigns the one `opa eval` call
([ADR-0017](0017-opa-policy-engine-and-earned-autonomy.md)), adds a WebAssembly runtime with findings of its own, and is a decision for its own
merits, as [ADR-0033](0033-dated-trivy-exception-for-the-bundled-opa-binary.md) already said.

## Recommendation

**Option C: build OPA from its release commit in both Dockerfiles, with the Go toolchain pinned by digest, the fixes listed as overrides and the result checked by
checksum.** B's `govulncheck` check is a good later addition, not a substitute.

1. *It ends the pattern instead of managing it.* Every one of the five findings had a fix available on the day it appeared, and the sixth is already visible.
   With C each is a one-line change that day, not a decision and a two-week wait.
2. *The trial answered the doubts.* The same program, a clean scan with no exception, the same bytes on every rebuild, two minutes of build only when a pin
   changes, $0.
3. *The trust moves; it does not grow.* Today it is "OPA's release page". With C it is one commit (content-addressed), the official Go image by digest and Go's
   checksum database for every library, all pinned in a repository Roshan reviews.

What it asks of us: a twenty-line recipe written in two Dockerfiles, a recorded checksum to update whenever the toolchain moves, and a look at OPA's
releases each week (already part of each session). This was a recommendation for Roshan to accept, change or decline.

## Decision

**2026-11-07: Roshan accepted it** (`decide build`, option C, about 17:10 IST, the same day the trial was written up). The recipe is the one in Lab 79 Part 5 and the
story below is `KAV-132`. One detail is settled in that story rather than here: the `-ldflags` hostname stamp that the lab copied from OPA's own recipe does nothing
in v1.21.4, so the implementation leaves it out and the checksums it pins are new ones, not the lab's. Measured in the implementation's first CI run: the stage took 42 s to compile on the arm64 runner, and that runner produced the same checksum as the laptop ([Lab 80](../labs/lab-80-opa-built-in-the-images.md)).

## The story (`KAV-132`)

Roshan chose C, so it is its own Jira story, `KAV-132` (`KAV-131`'s second criterion asked for the plan to exist before the decision was made).
Its images change, so the merge waits until after the 8 November morning teardown (tonight's staging reads `main`); the path is the usual one. To be live before the
exception ends on 2026-11-21 it needs about a week (build and merge after the teardown, one staging session, promotion); deciding on 7 November leaves a margin.

| Question | Plan |
|---|---|
| Toolchain pin | `golang:1.27.3-trixie@sha256:af1bfc26...` on the stage's `FROM` line: the image OPA's own Makefile names for its releases. Dependabot already watches both Dockerfiles weekly and will propose new patch digests; add an `ignore` for `golang` minor and major versions, as for `python`. |
| Source pin | `OPA_COMMIT` (40 hex characters) and `OPA_VERSION`. The stage stops if the fetched commit differs, and tags the checkout `v$OPA_VERSION` so that Go stamps the real version (without the tag it stamps `v0.0.0-<date>-<commit>` and the scanners report OPA itself as old; see Lab 79). |
| Overrides | `OPA_GO_GET="golang.org/x/net@v0.62.0"`: a list, each entry commented with the finding it answers and when it ends. The stage stops if an entry is not in the finished binary (`go version -m`). Raised libraries are verified by Go's checksum database. |
| Artifact checksum | `OPA_SHA256_ARM64` (the images are arm64), checked in its own step after the compile. Builds are byte-reproducible (two builds from empty caches gave the same file), so a changed value means an input changed and a person reads the change. A Dependabot toolchain bump therefore needs the new value in the same pull request; the failing build prints it. |
| Build time on the CI runner | In the trial: 2 min 2 s to 2 min 22 s per build on 4 CPUs (about what the runner has) with the libraries cached, 3 min 3 s fully cold. CI builds on native arm64 runners with the GitHub layer cache, so the compile is skipped whenever the stage's inputs are unchanged. The image jobs allow 20 minutes. |
| Who bumps it | Go: Dependabot. OPA release and overrides: when the scan goes red, and at the weekly look at OPA's releases that already happens each session (`gh api repos/open-policy-agent/opa/releases`). A scheduled workflow that opens an issue on a new release is a later option. |
| Tests | (1) A pytest that both Dockerfiles carry identical pins and that `ci.yml`'s `OPA_VERSION` agrees. (2) The CI image check that already runs `opa version` and a real classification inside each image gains an assertion on the Go version and commit it reports. (3) Trivy with no entry for OPA. (4) The comparison script in Lab 79, re-run on every OPA or toolchain bump. `ci.yml` keeps downloading OPA's amd64 release for `opa test` and the pytest suite: that file is neither shipped nor scanned. |
| Path | Local (done, Lab 79) -> merge, held until after the 8 November teardown because the images change -> CI builds both images -> the release pull request pins staging -> staging smoke test -> `promote.yml` to production. |
| Rollback | Revert the Dockerfile commit (back to the download), or `rollback.yml` to the previous image. |
| Exit | The overrides list empties as OPA catches up. The stage stays: the speed of taking a new Go patch is the point. Going back to the download needs a new ADR. |

**Had A been chosen instead,** [ADR-0033](0033-dated-trivy-exception-for-the-bundled-opa-binary.md)'s rules would have stood (one CVE per entry, one file path, an end
date, Roshan's approval) with a new limit: an exception for a *library* finding would have had to carry the proof from Lab 79 Part 1 (the flawed package's symbols
absent from the binary, or `govulncheck -mode=binary` reporting it not called), and a sixth finding that an OPA release had not cleared inside its fourteen
days would have started the story above anyway.

## Consequences

**Easier (if C).** The scan stays a clean signal: no standing exception to hide a real finding behind. A fixed Go patch or library reaches the images the
day it exists, by a pull request, not in two weeks by luck. The next finding (below) costs one line.

**Harder.** We own a recipe of about twenty lines, written in two Dockerfiles (as the download stage already is). Every Go toolchain bump changes the
binary and so its recorded checksum. The first build of an image after a pin changes takes about two and a half minutes longer. The libraries we raise
are newer than the ones OPA's own tests ran against, so the gate for that is the comparison on Kaval's policy plus OPA's own test suite, re-run by hand
whenever the override list changes (Lab 79, Part 4); the list is kept as short as possible and emptied as soon as OPA catches up.

**Security.** The trust moves; it does not grow. Today it is "OPA's release page" (a checksum from the same page, no signature). With C it is the source at one
commit (content-addressed), the official Go image by digest, and Go's checksum database for every library, all pinned in a repository that Roshan reviews. OPA is the
engine behind the safety rulebook, so being able to rebuild it and compare is a gain: a tampered OPA could answer `auto` where the rule says `ask`. Nothing is
`auto` today (the promotions file is empty) and the executor's own scoped permissions still limit what any action can do, but it is the reason the pins are
content-addressed and the result is checksummed. Nothing changes in the privilege split: the agent still cannot write.

**Failure.** Any problem (a different commit, a library not applied, a checksum that moved, a network failure) stops the image build, so no new image is published and
the running ones are untouched. It fails closed, like the scan does today.

**Observability.** CI asserts what `opa version` reports; Trivy runs with no entry for OPA, so a new finding is loud; `govulncheck -mode=binary` on the finished file
is the second opinion used in the trial and shows the next finding early.

**Cost and capacity.** No AWS cost. GitHub's minutes are free for a public repository. The binary is the same size (60.3 MB), so the images do not grow.

**Revisit** if OPA starts publishing signed, reproducible releases whose libraries satisfy the scanner within days (then go back to the download), or if the engine is
replaced.
