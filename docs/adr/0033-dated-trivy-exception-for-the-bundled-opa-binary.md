# ADR-0033: A dated, path-scoped Trivy exception for two Go CVEs in the bundled OPA binary

**Status:** Accepted
**Date:** 2026-10-09
**Related:** [ADR-0010](0010-ci-pipeline-and-supply-chain.md) (image scanning),
[ADR-0017](0017-opa-policy-engine-and-earned-autonomy.md) (why OPA),
[ADR-0030](0030-ci-publishes-images-by-oidc-and-staging-is-pinned-by-pull-request.md) (the scan also
gates publishing), `KAV-64`

## Context

On 2026-10-09 the `image agent` and `image executor` checks began failing on every pull request. Trivy
reported two HIGH vulnerabilities in `usr/local/bin/opa`, the OPA binary both images bundle:

| CVE | Where | Fixed in |
|---|---|---|
| CVE-2026-78667 | Go `net/http`, denial of service via crafted HTTP | Go 1.26.9, 1.27.2 |
| CVE-2026-97031 | Go `crypto/tls`, denial of service | Go 1.26.9, 1.27.2 |

Both were published after our images were last built clean (the scan on 2026-10-08 passed), so nothing
in the repository changed to cause this. Those two image checks are required on `main`, so nothing could
merge, and `release.yml` runs the same scan before publishing, so nothing could be published either.

Pinned OPA is v1.21.0. v1.21.1 (2026-09-29) was downloaded, its checksum compared with the published one,
and its embedded Go version read: also `go1.27.1`. No OPA release built with Go 1.27.2 exists yet, so
updating OPA does not clear the finding.

## Decision

**1. An exception file, not a weaker scan.** `.github/trivy-ignore.yaml` is passed to both Trivy steps
(`ci.yml`, `release.yml`) with `--ignorefile`. The severity threshold, `--ignore-unfixed` and the exit
code are untouched.

**2. Three limits keep it narrow.**

- One entry per CVE id, so a third vulnerability in the same binary is still caught.
- Each entry is scoped to the path `usr/local/bin/opa`, so the same CVE in Python, the base image or any
  other file still fails the scan.
- Each entry has `expired_at: 2026-10-23`. After that date Trivy stops ignoring it and the scan fails
  again without anyone remembering to remove it.

**3. Why it is safe to overlook.** Both flaws let a network client exhaust a Go program that is
*serving* HTTP or TLS. The agent and executor run OPA as a short-lived command (`opa eval` per decision,
`services/agent/kaval_agent/policy.py`); it opens no listening port and is never reachable by a client.
The flaw exists in the binary but the path to it does not exist in how it is used.

**4. The exit.** Pin an OPA release built with Go 1.27.2 or later (three places: both Dockerfiles and
`ci.yml`, with new checksums), delete the entries, and delete the file if it is empty. If none exists by
2026-10-23 the scan fails again, which forces a decision: extend the date with a fresh look, or build OPA
from source.

## Alternatives considered

| Option | Why not |
|---|---|
| **Update OPA to v1.21.1** | Tried first. Built with the same Go 1.27.1, so still flagged. |
| **Wait for an OPA release** | Strictest, but blocks every merge and every publish for an unknown time, including unrelated fixes. |
| **Replace OPA** (Python rules, Cedar, another engine) | A redesign of the safety rulebook (ADR-0017) to solve a two-week finding. Worth deciding on its merits, not under this pressure. |
| **Build OPA from source with Go 1.27.2** | A second supply chain to own (toolchain, build flags, checksum of our own artefact) for a flaw that cannot be reached. Revisit only if the date passes. |
| **Lower the severity bar for the whole scan** | Weakens it for every image and every future finding. |
| **Ignore the CVEs for the whole image, or with no date** | Hides a real regression in some other file, and an undated exception is permanent in practice. |

## Consequences

**Easier.** Required checks go green again, and the exception documents itself.

**Harder.** For two weeks the scan knowingly passes an image that carries two known flaws in one file.
The statement above is the justification and it holds only while OPA stays a one-shot command: if OPA were
ever run as a server (for example as a sidecar answering other services), this exception must be removed
the same day.

## Amendment, 2026-10-10: a third CVE of the same kind

CVE-2026-78669 (HTTP/2 SETTINGS denial of service in `golang.org/x/net` v0.58.0, fixed in 0.60.0, and Go
stdlib 1.27.1) appeared the day after this ADR and turned the agent and executor image checks red on the
`KAV-66` documentation pull request. This is the case decision 2 was written for: one entry per CVE means a
new vulnerability is *seen*, not silently absorbed. OPA v1.21.1 is still the newest release (checked
2026-10-10), so there is nothing to update to. Roshan approved a third entry on the same terms: only
`usr/local/bin/opa`, ending 2026-10-23, same justification (a one-shot `opa eval`, never a server). If a
fourth appears the pattern is clear enough to stop adding entries and decide on replacing or building OPA;
`KAV-65` is the place.

**Revisit when** an OPA release built with Go 1.27.2 or later appears, or on 2026-10-23, whichever is first.

## Updated 2026-10-21 (KAV-65): three closed, one new

OPA v1.21.3 (2026-10-19) is built with Go 1.27.2 (`.go-version` in the release tag) and its `go.mod` has
`golang.org/x/net v0.60.0`, the fixed version for all three entries. Both published checksums were compared with
files downloaded for the check (they matched). The pin moved from v1.21.0 in both Dockerfiles and `ci.yml`, the three
entries were removed, and the file is kept with an empty list because both image scans pass it with `--ignorefile`.
The exit in decision 4 was followed as written, two days before the dates ran out.

**One new finding, same kind.** Scanning the new binary with Trivy (`rootfs`, HIGH and CRITICAL, fixed only) found
CVE-2026-111943, a `crypto/x509` flaw published after the last green run (2026-10-19). It is fixed in Go 1.27.3 and
1.26.10; v1.21.3 and OPA's main branch were both still on Go 1.27.2, so no OPA release carries the fix. The old
v1.21.0 binary has it too, so without any change every image scan would have failed. Roshan approved ONE entry on the
same terms (only `usr/local/bin/opa`, a one-shot `opa eval` that checks no certificates) ending **2026-11-04**,
two weeks. **Revisit by then:** an OPA release built with Go 1.27.3 or later, else build OPA from source in the
Dockerfile with the fixed Go (the option decision 4 names). A fifth entry in this binary is the signal to do that.

## Updated 2026-10-30 (KAV-65): the last one closed
OPA v1.21.4 (2026-10-23) is built with Go 1.27.3 (`.go-version` in the release tag; the downloaded binary, run with
`opa version`, prints `go1.27.3`). It is a patch release on the line we already run, so nothing else changes; v1.22.0
(2026-10-29) is also on Go 1.27.3 but is a feature release, left for its own change. The pin moved from v1.21.3 in both
Dockerfiles (arm64 build) and `ci.yml` (amd64 build), the checksums are the ones GitHub publishes for those two
files, and the exception was removed. The file stays, with an empty list. Because the agent and executor images
change, they go through staging before prod like any other image. The deadline of 2026-11-04 was met five days early.

## Updated 2026-11-07 (KAV-130): a fifth finding, and why one more entry is allowed once
**A library this time, not the Go standard library.** On 2026-11-07 `image agent` and `image executor` began failing on
every pull request (a docs-only one included). Trivy reports CVE-2026-78661 (GHSA-75pg-wvh3-34jr, published 2026-10-28):
`golang.org/x/net` v0.60.0 inside `usr/local/bin/opa`. The `html` package's parser does quadratic work on crafted input.
It is fixed in x/net v0.62.0 (tagged 2026-10-27). OPA v1.22.2 (2026-11-06) and OPA's main branch both still have
`golang.org/x/net v0.60.0` in `go.mod` (read at each tag on 2026-11-07), so no OPA release carries the fix, and the pinned
v1.21.4 has it too: without a change every image scan fails and nothing can merge or publish.

Roshan approved ONE entry on the same terms (only `usr/local/bin/opa`; a one-shot `opa eval` on Kaval's own policy and
data, given no HTML and serving nothing), ending **2026-11-21**, two weeks. Reproduced locally first: the pinned binary,
its checksum agreeing three ways (the Dockerfile's pin, OPA's published file, the downloaded bytes), scanned with the
Trivy version CI pins. With no exception the scan fails with this CVE; with the entry it passes; the same entry with a
wrong path, an end date in the past, or a different CVE id still fails.

**This is the fifth entry in this binary, the signal decision 4 names.** It was weighed, not skipped. The first four were
Go standard-library flaws that only an OPA built with a newer Go can clear. This one is a library that OPA updates in
batches (the most recent batch found was merged 2026-09-24), and its fix has been out since 2026-10-27. Building OPA from
source means a second supply chain (a toolchain pin, our own checksum, build time, someone to bump it) for a flaw that
cannot be reached, so it is not done now. It is not left to luck either: `KAV-131` is the decision story, due before this
entry ends, and the end date makes the scan fail again by itself.

**Revisit by 2026-11-21:** an OPA release built with x/net 0.62.0 or later (pin it in both Dockerfiles and `ci.yml`, check
the checksums two ways, delete the entry), else the `KAV-131` decision.

## Updated 2026-11-07 (KAV-131): the question decision 4 left open is answered: build
Decision 4's other route, building OPA from source, was tried on a laptop and compared with the alternatives in
[ADR-0079](0079-where-the-opa-binary-comes-from.md), and **Roshan accepted it the same day (`decide build`)**. Two facts from the trial matter here. First,
the flawed package behind the fifth entry, `x/net/html`, is **not in the OPA binary at all** (zero of its functions), so this entry's safety statement is now
checked against the file itself and not only argued from how OPA is used. Second, the rules above stay in force until the build (`KAV-132`) is live: one entry
per CVE, one file path, an end date, Roshan's approval. When that story is merged the entry for CVE-2026-78661 is deleted and both image scans pass with no
exception; the exception file stays, empty, for any other finding, under the same rules.
