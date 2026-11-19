# Phase 8 — Harden and publish

> **Written from:** experience (rewritten 2026-11-19, when the phase closed and version 1 with it; `KAV-158`). The concept sections below were written in August as theory and kept; the table "What we assumed, and what happened" is from the record
> **Labs:** [71](../labs/lab-71-a-security-policy-owners-and-a-weekly-scorecard.md) to [107](../labs/lab-107-rehearsing-a-secret-rotation-on-staging.md), most of the last third of the course: the security policy, the threat model and its fixes, code scanning, the supply chain ([91](../labs/lab-91-a-parts-list-and-a-signed-build-record-for-every-image.md) to [99](../labs/lab-99-the-frozen-list-lets-old-releases-through.md)), the access review ([100](../labs/lab-100-the-cluster-half-of-the-access-review.md) to [105](../labs/lab-105-a-watcher-for-what-outsiders-can-reach.md)), the plan on pull requests (106) and the rotation rehearsal (107)
> **Cost:** nothing recurring. Each proof session cost cents: a staging session about 1 to 14 cents, a production session about 1 to 2 cents, because both are switched off between sessions

## Where this sits

Phases 0–7 built and proved the system. This phase makes it safe to show people, and turns three
months of work into something a stranger can evaluate in ten minutes.

It unlocks: the resume bullets become writable — because now they describe things that actually
happened. The repository itself went public earlier than planned, on 2026-09-30, on purpose: the
secret hygiene of Phase 0 made that safe, and this phase then had to live up to being watched.

It is also the last phase of version 1. The phone app that was once Phase 9 was retired
([ADR-0097](../adr/0097-phase-9-is-retired-slack-is-the-approval-surface.md)), and version 1 closed here
on 2026-11-19 ([ADR-0105](../adr/0105-version-1-closes-and-the-nightly-drift-check-moves-to-future-scope.md)).

## What we're doing

- A security pass — image scanning, signing that the cluster itself checks, an access review of every Kaval role and of the cluster
- `gitleaks` over the **entire history**, not just the working tree
- A threat model written for this system, with an attack test set for the model, and each gap it found fixed
- Public-repo hygiene: a security policy, code owners, code scanning, a weekly Scorecard, a secret-rotation schedule and one rehearsed rotation
- Architecture diagrams and a README written for a stranger
- Resume bullets, written from what shipped
- A demo video (the script is written; the recording moved to future scope, FS-2)

The phase was planned as one story and finished as 49. The reason is in the table below.

## Why this way

**Because publishing is irreversible.** Once the repository is public, its history is public; a
clone made in the first hour keeps whatever was in it. Every earlier phase's secret hygiene
existed so that this phase is a review rather than a rescue.

The rejected alternative — publish early, clean up later — misunderstands how git works. There is
no later.

---

## Key concepts

### Supply chain security, and why it became the main event

The dependencies you did not write are the largest part of what you ship. A Python service is a
few hundred lines of yours and tens of thousands of lines of other people's. A container image
adds an operating system.

The threats worth naming:

| Threat | Shape |
|---|---|
| **Typosquatting** | A package named `reqeusts` that does something else |
| **Dependency confusion** | A public package shadowing your internal one by version number |
| **Compromised maintainer** | A legitimate package gains malicious code in a new release |
| **Base image drift** | `FROM python:3.11` resolves to something different next month |

The defences are mostly unglamorous: pin versions including transitive ones with a lockfile, pin
base images **by digest** rather than tag, scan continuously rather than once, and generate an
SBOM so that when the next widely-publicised vulnerability lands you can answer "are we affected?"
in minutes rather than days.

That last capability is the real argument for SBOMs, and it is worth phrasing that way.

### SBOM, signing, provenance

An **SBOM** (Software Bill of Materials) lists every component in an artifact with versions and
licences. Standard formats are **SPDX** and **CycloneDX**; `syft` and `trivy` generate them.

**Signing** proves an artifact came from you and has not been altered. **`cosign`** signs container
images; keyless signing uses short-lived certificates tied to an OIDC identity — your CI's
identity, say — rather than a key you must store and protect.

**Provenance** goes further: an attestation describing *how* the artifact was built — which source
commit, which builder, which parameters. **SLSA** is the framework that defines levels of
assurance here.

The chain these produce: *this image came from this commit, built by this pipeline, and contains
these components.* Being able to describe that chain is increasingly a hiring signal, and for
this project it also completes the governance story — the audit trail covers the model's decisions,
and this covers the artifacts.

### Container image hygiene

| Practice | Reason |
|---|---|
| Minimal base — distroless or Alpine | Fewer packages, smaller attack surface, faster pulls |
| Multi-stage builds | Build tools do not ship to production |
| Non-root `USER` | A container escape starts unprivileged |
| Pin base images by digest | `python:3.11` is a moving target |
| No secrets in build args | They persist in image layers, visible with `docker history` |
| Scan in CI | Trivy or Grype, failing the build on high severity |

The build-args trap catches people: a secret passed as `--build-arg` is recorded in the layer
metadata. Deleting the file in a later layer does not remove it, for the same reason deleting a
file in a later commit does not remove it from git history.

### Least privilege as a process

Phase 0 granted `kaval-dev` `AdministratorAccess` with a written commitment to narrow it here.
That commitment came due, and was answered in part: the roles Kaval's own servers use were reviewed and bounded, but the laptop's key stayed an administrator's, by a written and accepted decision ([FS-15](../future-scope.md#fs-15--a-laptop-login-that-expires-and-needs-a-second-factor)).

The honest method is empirical, not theoretical:

1. Enable **CloudTrail** and let it record for a period of normal use
2. Query which API actions were actually called by this principal
3. Generate a policy from that set — **IAM Access Analyzer** does this directly from CloudTrail
4. Apply it, then watch for `AccessDenied` and add back what you genuinely broke

This is a far better story than "I wrote a policy from the documentation," because it is grounded
in observed behaviour. It is also the standard professional approach, and describing it correctly
signals experience.

The Kubernetes half is the same exercise with `kubectl auth can-i`, and the assertions from Phase
3 should already be in CI.

### Threat modelling this specific system

*The real one is written: [docs/architecture/threat-model.md](../architecture/threat-model.md) (`KAV-112`). Building it found gaps the argument below did not predict, which is the reason to write one.*

Generic security advice is weak in an interview. Threat modelling your own architecture is strong.

The dominant threat for Kaval is **prompt injection through telemetry**, and it deserves stating
precisely because it is genuinely unusual.

The agent reads Kubernetes events, log lines and container names. Some of that text is
attacker-influenceable — a pod name, a crafted log message, an error string echoed from user
input. So an attacker can put text into the agent's context window.

Trace what that buys them:

```
attacker text in a log line
   └─▶ enters the agent's context
       └─▶ may influence the proposal it composes
           └─▶ proposal is validated against a schema        ← invalid shapes die here
               └─▶ actions classified by OPA policy          ← never-class dies here
                   └─▶ human reads the proposal              ← implausible dies here
                       └─▶ executor re-checks policy         ← second enforcement
                           └─▶ RBAC restricts what is possible ← below the app entirely
```

The worst outcome is a bad proposal a human has to read and reject. **The agent has read-only
credentials, so there is no path from influencing its output to changing the cluster.**

That is why the privilege split is the architecture and not a feature — it is the mitigation for
the primary threat, and it was designed in from the start rather than bolted on here.

Secondary threats worth having answers for: a compromised Bedrock response (same pipeline, same
defences); a compromised executor (scoped RBAC, namespace-bound, no `kube-system`); an unlocked
phone or laptop signed in to Slack (the executor re-checks policy, removals need two approvals; there is no
phone app and so no token of Kaval's on a device, [ADR-0097](../adr/0097-phase-9-is-retired-slack-is-the-approval-surface.md)).

### Publishing: what to remove and what to keep

Removing before publication:

- Account IDs, ARNs, endpoint hostnames, email addresses
- `terraform.tfstate` — confirm it never entered history
- Anything in `.env` that is not in `.env.example`
- Screenshots with account identifiers visible in the corner

Keeping, deliberately:

- The journal, including the wrong turns
- ADRs for decisions later superseded
- The cost figures, including mistakes
- The `Written from: theory` markers on pages not yet corrected by experience

The instinct is to present a clean narrative. Resist it. **The visible reasoning is what
distinguishes this from a tutorial follow-along**, and a reader who sees a decision reversed with
an explanation trusts the rest of the repository more, not less.

### Verifying history is clean

```bash
gitleaks detect --source . --config .gitleaks.toml --verbose
```

This scans **every commit**, not the working tree. It is the check that matters, and the reason
the hook went in before commit one.

If something is found: rewriting history with `git filter-repo` is possible, but the credential
must be **rotated regardless**. Once a secret has existed in a repository you cannot prove nobody
cloned it. Rotation is the fix; history rewriting is tidying.

### Writing the resume bullets honestly

The rule is that every bullet describes something that happened and that you could be questioned
about for ten minutes.

Weak: *"Built an AI-powered Kubernetes automation platform."*

Strong: *"Built an autonomous Kubernetes remediation agent with human-in-the-loop approval —
self-hosted Gemma 3 on AWS, sub-5-minute MTTR across five injected failure classes, held under
$50/month against a ~$150 conventional baseline."*

The difference is that the second contains numbers you measured and can defend. The first invites
"what does powered mean?" — the second invites "how did you measure MTTR?", which is a question
you have spent seven months earning the right to answer.

---

## What we assumed, and what happened

Every row is something this page or the plan assumed, next to what the record shows. The pattern that
repeats: **the first version of a safety control was usually right in design and wrong in one
small, checkable way, and a real run on staging found it, not review.**

| We assumed | What actually happened | Where |
|---|---|---|
| Phase 8 is one story: "make it safe" | Asking the planning standard's questions (whole path, attacker, failure) turned it into 49 stories. The first estimate of a safety phase is a floor | [Retrospective](../retrospective-v1.md) |
| The repository goes public at the end | It went public on 2026-09-30, on purpose, because Phase 0's secret checks were already in place. The history had to be clean from then on, not only at the end | [Roadmap](../../ROADMAP.md) |
| The model is the dangerous part | The model did obey planted text in 2 of 7 marker cases of the injection set. What kept it harmless was everything around it: the schema, the policy, the human, the executor's own check. Gaps the threat model found were fixed as their own stories (a login on the approval route, escaped Slack text, the model's text labelled on the card) | Labs 72, 73, 84, 86 |
| Scanning once is enough | The Scorecard found four `pip install` lines with no hashes (`KAV-111`). The fix removed a file the database migration needed, and the first staging run failed on 2026-11-12. Nothing reached production; the fix went out as v0.4.2 | Lab 88 |
| A scan that finds nothing is good news | A pull request's code scan reports only the lines it changed, so its "nothing found" meant little. Four mistakes planted on purpose in a throwaway pull request proved it can find things (all four, two critical) | Lab 81 |
| Signing images is the goal | The goal is that something *refuses* an unsigned image. Signatures are checked by `promote.yml`, then by the cluster: watching first, refusing on staging, then on production's next start-up. Each refusing step was proved by presenting something that should be refused and seeing it turned away | Labs 94 to 97 |
| An image's version is obvious | Production still said `v0.1.0` after several promotions, because the readable tag was made only by a manual step nobody ran. A promotion now has to be a tagged release | Labs 83, 90 |
| We have backups | The restore drill on 2026-11-08 restored in one second and was recorded as **failed**, because the newest dump was twelve days old. A dump is now taken as production is parked | Lab 89 |
| Download servers are always up | On 2026-11-08 GitHub's download servers failed seven CI jobs in one day. The download lines now retry, and the pinned checksum check after each is unchanged | Lab 82 |
| Least privilege means shrinking `AdministratorAccess` | The review read 13 Kaval roles: no wildcard action, but five findings. There was no Access Analyzer, the budget brake could scale any auto-scaling group, and no role had a permission boundary. The laptop key is an administrator's with no second factor; that one was accepted and parked (FS-15) | Lab 101, ADR-0101 |
| A rotation is a Terraform apply | Postgres reads its admin password only when its disk is first made, so a new value in the store never reaches the database. One `ALTER ROLE` has to come first; no server restart is needed. An earlier decision record said a restart was; it was amended | Lab 107 |
| A plan on every pull request catches drift | It compares the code with Terraform's record and does not refresh from the account, so a change made by hand is not shown until the next apply. The nightly check that would show it was moved to future scope | Lab 106, FS-17 |

## Common mistakes

| Mistake | What it costs |
|---|---|
| Scanning the working tree, not history | The secret in commit 12 is still public |
| Rewriting history and not rotating | You cannot prove nobody cloned it |
| Secrets in Docker build args | Persist in layer metadata; `docker history` shows them |
| Pinning base images by tag | `python:3.11` silently changes underneath you |
| Writing a least-privilege policy from documentation | Guesswork; CloudTrail tells you what was actually used |
| Publishing without checking screenshots | Account IDs in the corner of an image |
| Deleting the wrong turns for a clean narrative | Removes exactly what makes it credible |
| Generic security answers in an interview | Threat modelling your own system is far stronger |
| Resume bullets you cannot defend for ten minutes | The follow-up question ends badly |

## Glossary

| Term | Meaning |
|---|---|
| **Supply chain security** | Securing everything you ship that you did not write |
| **Typosquatting** | A malicious package named like a popular one |
| **Dependency confusion** | A public package shadowing an internal one |
| **SBOM** | Software Bill of Materials — every component with version and licence |
| **SPDX / CycloneDX** | The two standard SBOM formats |
| **`cosign`** | Signs container images; supports keyless OIDC-based signing |
| **Provenance** | An attestation of how an artifact was built |
| **SLSA** | Framework defining levels of supply-chain assurance |
| **Distroless** | Base image with no shell or package manager |
| **Multi-stage build** | Build in one stage, copy only artifacts to a minimal final stage |
| **Digest pin** | Referencing an image by content hash rather than mutable tag |
| **Trivy / Grype** | Vulnerability scanners for images and dependencies |
| **CloudTrail** | AWS audit log of API calls |
| **IAM Access Analyzer** | Generates least-privilege policies from observed CloudTrail activity |
| **Threat model** | Structured analysis of who attacks what, and how you stop it |
| **Prompt injection** | Attacker-controlled text in the context altering model behaviour |
| **`git filter-repo`** | Rewrites git history; the modern replacement for `filter-branch` |
| **gitleaks** | Scans commits and history for credentials |
| **Admission policy (Kyverno)** | A checker that the cluster asks before it starts a pod. Kaval's refuses an image whose signature does not verify |
| **Permission boundary** | A ceiling on a role: whatever the role is later granted, it can never do more than the boundary allows |
| **Access Analyzer** | AWS's free service that reports what outside accounts can reach |
| **Drift** | A difference between what the code says exists and what the account really has, usually from a change made by hand |
| **SAST / CodeQL** | Static application security testing: a program reads the source code without running it and looks for known kinds of mistake. CodeQL is GitHub's, and free on a public repository. A scan that finds nothing proves little until it has been seen finding something |

## Check yourself

1. Why does rewriting history not make a leaked credential safe?
2. A secret passed as `--build-arg` and deleted in a later layer. Is it gone? Why not?
3. Why generate a least-privilege policy from CloudTrail rather than from the documentation?
4. Trace prompt injection through a log line all the way to its worst outcome. Name every layer that stops it.
5. Why keep the journal's wrong turns and the superseded ADRs when publishing?
6. What question does an SBOM let you answer quickly that you otherwise cannot?
7. Rewrite "Built an AI-powered Kubernetes automation platform" as a bullet you could defend for ten minutes.
8. A restore drill restores cleanly in one second and is still recorded as failed. Why, and what was changed?
9. Why does a plan shown on every pull request not catch a change made by hand in the console?
10. Why does replacing the database's admin password in the secret store not change the password the database accepts?

## In an interview

**"What's the security risk of letting an AI touch your infrastructure, and how did you handle it?"**

> "The specific threat for my system is prompt injection through telemetry, which is unusual enough
> to be worth being precise about. My agent reads Kubernetes events, container names and log lines,
> and some of that text is attacker-influenceable — a crafted log message, an error string echoed
> from user input. So an attacker can get text into the model's context window. What I made sure
> of is that it buys them nothing: the reasoning service holds read-only credentials and its only
> possible output is a row in a proposals table. From there the proposal is schema-validated,
> classified by an OPA policy where the never class ignores confidence entirely, read by a human,
> re-checked by the executor, and finally bounded by Kubernetes RBAC that's enforced below my own
> code. The worst case is a bad suggestion someone has to read and reject. That's why I describe
> the privilege split as the architecture rather than a feature — it's the mitigation for the
> primary threat, and it was there from the first commit rather than added during hardening."

Naming an unusual, system-specific threat and then walking the mitigation chain is considerably
stronger than reciting OWASP.

**"How do you know your controls work and are not just written down?"**

> "I make each one refuse something, and I keep the record of it refusing. Image signing is the
> example: it is not enough that images are signed, so the promotion workflow refuses one whose
> signature does not verify, and the cluster refuses it too. That went in watching-only first, then
> refusing on staging, then on production, and each refusing stage was proved by presenting an image
> that should be refused and seeing it turned away. The same habit caught my own mistakes: a restore drill that restored in a
> second but was recorded as failed because the backup was twelve days old, and a first staging run
> of a change that looked fine in review and was broken. None of those reached production, because
> staging is a rule, not a courtesy."

A control you have watched say no is worth more than ten you have only written.

## Further reading

- OWASP Top 10 for LLM Applications — prompt injection and insecure output handling
- SLSA framework — the build levels and what each requires
- Sigstore / `cosign` documentation — keyless signing
- AWS IAM Access Analyzer — generating policies from CloudTrail
- `git filter-repo` documentation — and its warnings
- Adam Shostack, *Threat Modeling: Designing for Security* — STRIDE remains a useful checklist
