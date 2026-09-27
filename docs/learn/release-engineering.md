# Release engineering

> **Written from:** theory
> **Spans:** Phases 1, 3 and 4 — this is the one cross-cutting page
> **Decisions:** [ADR-0004](../adr/0004-environment-strategy-and-promotion.md)

## Where this sits

Every other page in this folder covers one phase. This one does not, because release engineering
is not a phase — it is the spine running through Phase 1 (CI), Phase 3 (promotion mechanics) and
Phase 4 (the real pipeline).

It is also the page closest to what Roshan already does professionally, which changes its purpose.
The others teach unfamiliar things. This one takes existing knowledge and makes it **demonstrable**
— because "I am a Release Manager" is a claim, and a generated change record with a measured
time-to-rollback is evidence.

## What we're doing

- Three environments in a promotion path: `local` → `staging` → `prod`
- Staging as a genuine second cluster, created per release and destroyed after
- Build once, promote the artifact — never rebuild for production
- An approval gate that provably refuses what has not passed staging
- Change records generated from commits and Jira, not typed
- Rollback as a rehearsed, timed property
- DORA metrics measured rather than estimated

## Why this way

**Because the product and the process argue the same thing.** Kaval validates a proposal,
classifies it by policy, has a human approve it, executes it, then verifies the outcome. The
pipeline validates a build, deploys it to staging, has a human approve it, promotes it, then
verifies. If the delivery process lacked those gates, it would be arguing against the architecture
it delivers.

The rejected alternative was namespace-based staging on the production node — free, and genuinely
common. It fails on the class of change most likely to cause an outage: node-level changes,
cloud-init, k3s upgrades, spot reclamation. A staging environment that cannot test infrastructure
changes is a staging environment for application bugs only, and application bugs are the ones unit
tests already catch.

---

## Key concepts

### Environment parity, and where it always breaks

The purpose of a non-production environment is to be **wrong in the same ways** production is
wrong. Every difference is a class of bug that can reach production untested.

The differences that matter, roughly in order of how often they bite:

| Difference | What escapes |
|---|---|
| Instance size or memory | OOM under real load — this project's binding constraint |
| Different model or a stubbed one | Inference latency, memory, output shape |
| Data volume | Slow queries, index bloat, pagination bugs |
| Data *shape* | The malformed row nobody generated synthetically |
| Managed service versions | Behaviour changes between Postgres minors |
| Network topology | Timeouts, DNS, TLS |
| Secrets and IAM | Permission errors found in production |

This project buys exact parity on instance type, memory and model — the top two — and closes the
data gap by **seeding staging from the latest sanitised production snapshot** on every
`make staging-up`. Staging gets real data shape and real volume, so slow queries and index
behaviour surface before production sees them.

That gap was originally recorded as accepted. It did not survive being questioned: the nightly
production dump already existed, so restoring it was a handful of lines rather than a constraint.
[ADR-0004](../adr/0004-environment-strategy-and-promotion.md) still carries the original
paragraph, struck through, because a decision reversed with a reason is more useful than one that
appears to have been right first time.

**The restore pays a second dividend.** Because it runs on every release, the backup is verified
several times a month instead of never — which is how the claim *a backup is not verified until
you have restored one* stops being advice and becomes a property of the system.

The gap that genuinely remains is **freshness**: the snapshot is up to 24 hours old, so staging
never holds the last day of production. In an interview, "staging is identical including data
shape and volume, seeded from a sanitised prod snapshot that's up to a day old" is a much better
answer than "staging is just like prod."

### Sanitising on the way in

Copying production data into a lower environment unsanitised is the practice auditors flag. The
anonymisation runs **before anything can read the database**, not after, and it asserts its own
coverage — if a 12-digit account ID or a credential-shaped string survives, the restore aborts
rather than leaving a partially-sanitised staging database readable.

The maintenance hazard is that a new column holding something sensitive is a new line in the
script, and forgetting is silent. The assertions are the guard against that.

### Build once, promote the artifact

The single most important principle here, and the one most often violated.

Build the artifact **once**. Tag it immutably. Promote *that exact artifact* through every
environment. Never rebuild.

Why it matters concretely: a rebuild from the same commit can differ. Base images move under
floating tags, dependency resolution picks a new patch version, a build tool updates. So a rebuild
is a *different artifact*, and if you rebuild for production you have deployed something staging
never tested — while believing you tested it.

The mechanics:

- Tag by immutable identifier — commit SHA, not `latest`, not `v1.2` re-pointed
- Reference by **digest** (`sha256:…`) in production, because even a tag can be moved
- Environments differ only by configuration and which digest they pin

`promote.yml` enforces this by refusing a digest that has no record of passing staging. The
negative test — deliberately trying to promote an unproven digest and confirming refusal — is
what makes it a gate rather than a step.

### Immutable tags versus mutable tags

`latest` is the canonical mistake. Two deploys of `latest` a week apart are different software
with the same name, and you cannot tell what is running or roll back to a known point.

| Tag style | Immutable? | Use |
|---|---|---|
| `latest` | No | Local experimentation only |
| `v1.2` | No — can be re-pointed | Human-readable release marker |
| `sha-a3f9c2` | Effectively | What CI produces |
| `sha256:…` digest | Yes, cryptographically | What production pins |

ECR supports **tag immutability** at the repository level, which turns the convention into an
enforced rule. Worth enabling.

### The gate, and what makes one real

A gate is only a gate if it can **refuse**. Three properties:

1. **It can say no.** A rubber stamp is a step, not a gate.
2. **Refusal is automatic where the criterion is mechanical.** Tests failing should block without a human deciding.
3. **The human decision is informed.** An approver who cannot see what changed, what staging proved, and how to undo it is not approving — they are clicking.

Which is exactly the same reasoning as the product's approval screen, where the missing element is
usually *what happens if you decline*.

The mechanism here: `promote.yml` as a manual `workflow_dispatch`, moving to GitHub Environments
with required reviewers when the repo goes public. **Verify the plan terms** — deployment
protection rules on private repositories are a paid feature, and GitHub changes what is included.

### Progressive delivery, and why not here

Worth knowing by name, even though this project does not use it.

| Strategy | Shape |
|---|---|
| **Recreate** | Stop old, start new. Downtime. |
| **Rolling** | Replace pods gradually. Kubernetes default. Two versions coexist briefly. |
| **Blue-green** | Two full environments, switch traffic at once. Instant rollback, double the resources. |
| **Canary** | Route a small percentage to the new version, watch metrics, widen or abort. |
| **Feature flag** | Deploy the code dark, enable per user or cohort. Decouples deploy from release. |

Kaval uses **rolling**, because it is the Kubernetes default and one node cannot host two full
environments. The property to understand from the list is the last one: **deploy and release are
different events**, and separating them is what lets you ship continuously without exposing
unfinished work.

Note also that rolling updates mean two versions run simultaneously for a few seconds — so
database migrations must be backward-compatible with the previous version. That constraint
surprises people and is the source of a lot of deployment incidents.

### Change records worth writing

A change record exists to answer, later, under pressure: *what changed, who approved it, and how
do we undo it?*

Most enterprise change records fail because they are typed by hand, so they are written to satisfy
a process rather than to be read. Generating them inverts that — the record is a by-product of
the deploy, so it is accurate and it costs nothing.

What has to be in it:

| Field | Because |
|---|---|
| Exact image digests | "Version 1.4" is ambiguous; a digest is not |
| Changes since the last release | From conventional commits |
| Linked issues | From the Jira keys in the commits |
| Risk assessment | Derived — does this touch infrastructure, policy, or the executor? |
| Staging evidence | What actually passed, not that something was tested |
| Rollback plan | The previous Helm revision, and the last *measured* rollback time |
| Post-deploy verification | Whether it worked, recorded after the fact |

The rollback field is the one most often hand-waved. "We would roll back" is not a plan.
"`helm rollback kaval 7`, last measured at 94 seconds" is.

### Rollback is a property you test, not a plan you write

Same principle as Phase 0's budget alarm and Phase 6's chaos experiments: **an untested recovery
path does not work.**

So rollback is drilled, not documented. `make rollback` performs a real `helm rollback` and
records how long it took, and that measured figure goes into the next change record.

The things that make rollback fail in practice, which is why it must be exercised:

- A database migration that dropped a column the old version needs
- Config or a secret that changed shape between versions
- The old image evicted from the registry by a cleanup policy
- State written in the new format that the old version cannot read

The first is the most common, and the discipline that avoids it is **expand-contract**: add the
new column, deploy code that writes both, migrate, then remove the old column in a *later*
release. Never in the same one.

### DORA metrics

From the DevOps Research and Assessment programme. Four measures, and the useful insight is that
they do not trade off against each other the way people assume — high performers are better at all
four simultaneously.

| Metric | Measures | Derived here from |
|---|---|---|
| **Deployment frequency** | How often you reach production | Count of records in `docs/releases/` |
| **Lead time for changes** | Commit → running in production | First commit timestamp → promote timestamp |
| **Change failure rate** | Proportion of deploys causing degradation | Releases followed by a rollback or incident |
| **Time to restore** | How long to recover | Measured by `make rollback`, and by MTTR from Phase 6 |

The pairing is the point: frequency and lead time measure **speed**, failure rate and restore time
measure **stability**. Reporting only the first pair is how teams justify shipping recklessly;
reporting only the second is how they justify shipping never.

For a solo weekly project the absolute numbers are unimpressive by design. Having measured them at
all, and being able to say what they *mean*, is the signal.

### Trunk-based development and conventional commits

Short-lived branches merged to `main` frequently, rather than long-lived feature branches. It
keeps merges small, which keeps them safe, and it means `main` is always a candidate for release.

**Conventional commits** (`feat:`, `fix:`, `docs:`, `infra:`, `chore:`) turn commit messages into
structured data. Release notes generate themselves, and semantic versioning can be derived: `fix:`
bumps patch, `feat:` bumps minor, a breaking-change footer bumps major.

Put the Jira key in every commit (`feat: KAV-12 ...`) and connect the tracker to the repository,
and each issue shows the code that delivered it without anyone linking it by hand. Jira's *smart
commits* go one step further: `KAV-12 #comment ... #time 2h #done` in a message comments, logs
time and moves the issue. There's a catch that's easy to miss. Jira runs those commands only when
the commit's author email matches a Jira user. Kaval commits under the GitHub noreply address to
keep a personal email out of public history, so it uses the key for linking and a script for
status ([ADR-0011](../adr/0011-commit-convention-and-jira-link.md)). A convention is only real if
something checks it: here a `commit-msg` hook and CI run the same checker.

---

## Common mistakes

| Mistake | What it costs |
|---|---|
| Rebuilding the image for production | You deployed something staging never tested, while believing you did |
| Deploying by mutable tag | You cannot tell what is running or return to a known point |
| Staging smaller than production | The memory and load bugs — the ones that actually take you down |
| A gate that has never refused anything | It is a step wearing a gate's clothes |
| Change records typed by hand | Written to satisfy a process, so nobody reads them |
| "We would roll back" as a rollback plan | Untested, and usually blocked by a migration |
| A destructive migration in the same release as the code change | Rollback becomes impossible exactly when you need it |
| Measuring only speed metrics | Justifies shipping recklessly |
| Measuring only stability metrics | Justifies shipping never |
| Long-lived feature branches | Merges get large, and large merges are where incidents come from |

## Glossary

| Term | Meaning |
|---|---|
| **Environment parity** | How closely non-prod resembles prod; every gap is an untested bug class |
| **Promotion** | Moving the *same* artifact to a higher environment |
| **Build once** | Build a single artifact and promote it, never rebuild per environment |
| **Immutable tag** | An identifier that cannot be repointed to different content |
| **Digest** | Cryptographic content hash — `sha256:…`; the only truly immutable reference |
| **Artifact** | The built thing being promoted: a container image here |
| **Gate** | A checkpoint that can refuse |
| **Recreate / rolling / blue-green / canary** | Deployment strategies, in ascending order of safety and cost |
| **Feature flag** | Runtime switch separating deploy from release |
| **Deploy vs release** | Getting code onto machines vs exposing it to users |
| **Expand-contract** | Add new, migrate, remove old in a later release — keeps rollback possible |
| **Backward-compatible migration** | A schema change the previous version still runs against |
| **Change record** | The document of what changed, who approved, and how to undo |
| **Rollback** | Returning to the previous known-good release |
| **RPO / RTO** | How much data you can lose / how long recovery takes |
| **Data masking** | Replacing sensitive values when copying to a lower environment |
| **Time to restore** | Measured duration from failure to recovery |
| **DORA** | DevOps Research and Assessment; the four-metric framework |
| **Deployment frequency** | How often you reach production |
| **Lead time for changes** | Commit to running in production |
| **Change failure rate** | Proportion of deploys causing degradation |
| **Trunk-based development** | Short-lived branches merged frequently to `main` |
| **Conventional commits** | Structured commit prefixes enabling generated notes and versioning |
| **Semantic versioning** | `MAJOR.MINOR.PATCH` with defined bump rules |
| **Smart commit** | A commit message that transitions or annotates a Jira issue |
| **`workflow_dispatch`** | A GitHub Actions workflow run manually — the gate on a free private repo |

## Check yourself

1. Why is rebuilding an image for production dangerous even when the commit is identical?
2. What is the difference between a tag and a digest, and which does production pin?
3. Name the top two parity gaps this project closes and the one it deliberately accepts.
4. What three properties make something a gate rather than a step?
5. During a rolling update two versions run at once. What does that force to be true of your database migrations?
6. Explain expand-contract, and say which release the destructive step belongs in.
7. Why do the four DORA metrics come in two pairs, and what goes wrong reporting only one pair?
8. Your rollback plan says "revert the Helm release." Give three reasons it might still fail.

## In an interview

**"You're a Release Manager. What was your release process on this project?"**

> "Three environments — local, staging, prod — with staging as a genuine second cluster rather
> than a namespace: its own node, own k3s, own database. It's identical to prod, same instance
> type and same model, because memory pressure on a 4 GB node is this project's binding
> constraint and a smaller staging box would miss exactly that. It's created per release by
> Terraform and destroyed after, so it costs about a dollar a month and every release exercises
> the from-scratch rebuild path.
>
> The pipeline builds once. CI runs on the PR, merge to main pushes an image tagged by commit SHA
> and deploys it to staging, and promotion deploys *that same digest* — promote refuses a digest
> with no record of passing staging, and I test that refusal, because a gate that's never said no
> isn't a gate. Every production deploy generates a change record with the digests, the changes,
> the linked issues, the staging evidence, and a rollback plan carrying the last *measured*
> time-to-restore rather than an assertion.
>
> I track the four DORA metrics. The absolute numbers are unimpressive — it's a solo project at
> five hours a week — but they're measured, and the honest gap is that staging is created fresh
> so it holds no accumulated data. Anything that only shows up after months of production data
> won't be caught there, and I compensate with monitoring rather than pretending otherwise."

Two things make that answer work: the negative test on the gate, and volunteering the parity gap
before being asked.

## Further reading

- Jez Humble & David Farley, *Continuous Delivery* — build-once and the deployment pipeline
- Nicole Forsgren, Jez Humble & Gene Kim, *Accelerate* — the DORA research
- Google, *DORA State of DevOps* reports — current benchmarks
- Martin Fowler, *BlueGreenDeployment* and *FeatureToggle*
- conventionalcommits.org and semver.org — both short
- GitHub Actions documentation — environments, deployment protection rules, `workflow_dispatch`
