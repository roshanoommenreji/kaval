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

**Revisit when** an OPA release built with Go 1.27.2 or later appears, or on 2026-10-23, whichever is first.
