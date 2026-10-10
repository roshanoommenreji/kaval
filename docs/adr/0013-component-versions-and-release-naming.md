# ADR-0013 — Component versions, and a release named by the product with its components listed

- **Status:** Accepted
- **Date:** 2026-09-28
- **Deciders:** Roshan
- **Jira:** `KAV-35`

## Context

[ADR-0011](0011-commit-convention-and-jira-link.md) moved semantic versioning to Phase 4, as one
version for the whole repository. Roshan pointed out how releases work at their job: **each
component carries its own code version, and the release notes name them.** A release that says
only "v0.4.0" can't answer "which gateway is in prod?" when the gateway and the collector change at
different speeds.

Kaval has several deployable components in one repository:
- the gateway and the collector today;
- the agent and the executor in Phases 2–3;
- the mobile app in Phase 5.

Each is built into its own image. Images are already tagged by commit SHA
([ADR-0004](0004-environment-strategy-and-promotion.md)), which identifies a build exactly but
means nothing to a person.

## Decision

1. **Every component has its own semantic version**, starting at `0.1.0`, written once in its code:
   `services/<svc>/kaval_<svc>/__init__.py` → `__version__`. The mobile app will use
   `package.json`.
2. **A component's version bumps from the commits that touched its folder**, by conventional
   commit type since its last tag:
   - `fix:` → patch;
   - `feat:` → minor;
   - `!` or a `BREAKING CHANGE:` footer → major.

   A change under `services/shared/` or `migrations/` bumps every service that ships it, because
   the shared code is inside their images.
3. **The product has a version too**, in `pyproject.toml` (`0.1.0` today). A **release is named
   by the product version and lists its components' versions** (Roshan's choice). That list is the
   release's bill of materials:

   > **Kaval 0.3.0**: gateway 1.2.0 · collector 0.4.1 · agent 0.1.0

   The product version bumps like a component, from the largest change among the components it
   carries.
4. **Tags:** `gateway-v1.2.0` for a component, `v0.3.0` for the product. Image tags stay the
   commit SHA (the thing promoted); the version rides along as metadata.
5. **Every running thing says its version:**
   - the gateway, in `/healthz` (`"version"`) and its OpenAPI;
   - the collector, via `--version`;
   - every image, via the OCI label `org.opencontainers.image.version`.

   CI stamps that label from `__version__` and checks, inside the built image, that the label and
   the code agree.
6. **Jira:**
   - one Jira **Release** (fix version) per product version, named `Kaval 0.3.0`, with the
     component list in its description;
   - stories carry the **Service** field (the components they change), set by
     `jira-sync.py create --service`, and backfilled for past stories from the folders their
     commits touched;
   - the change record groups a release's stories by service, next to that service's version, and
     flags a story whose ticked services don't match the folders its commits changed.
7. **Automation arrives with `release.yml` (Phase 4).** It covers computing the bumps, writing
   them, tagging and generating the notes. It will be either release-please in manifest mode (the
   standard tool for per-package versions in one repository) or a small stdlib script, decided
   then. Until then the versions stay at `0.1.0`, because nothing has been released. A version
   means something only once a release exists.

## Alternatives considered

| Option | Why not |
|---|---|
| **One version for everything** (ADR-0011's plan) | Simple, but it hides which component changed. A collector-only fix would "bump" the gateway too |
| **Per-component releases only, no product version** | Each component ships alone, and nothing names the combination that was tested together. Staging tests a *set* of images, so the set needs a name |
| **Calendar versions** (`2026.10.1`) | Suits fixed release windows. Kaval releases when a phase needs it, and semver tells a reader whether a change is breaking; a date doesn't |
| **The version in `pyproject.toml` per service** | One `pyproject.toml` builds every service today; splitting the packaging is an open thread (uv workspaces) for Phase 3. `__version__` works now and moves cleanly if that split happens |
| **Jira Components instead of a Service field** | Team-managed projects don't have Components ([JRACLOUD-92571](https://jira.atlassian.com/browse/JRACLOUD-92571)). A Checkbox field does the same job |

## Consequences

- "Which gateway is in prod?" has an answer from three places that must agree: the change
  record, the image label, and `/healthz`.
- Versions only move through commits, so a version can't be bumped by hand and forgotten in the
  notes.
- A commit touching two components bumps both. That's correct, and it's also a reason to keep
  commits to one component where practical.
- The Service field depends on people ticking it. Every new story sets it via `--service`, and
  release time cross-checks it against the code, so a missing tick gets caught instead of
  silently making the notes wrong.

## Amendment 2026-10-10 (KAV-67, ADR-0035)

- **Decision 7 is settled: a small stdlib script, not release-please** (`scripts/release/versions.py`).
  `release-prepare.yml` opens the bump pull request, `release.yml` tags after the images are pushed, and
  `promote.yml` carries the generated change record ([ADR-0035](0035-computed-versions-tags-and-the-generated-change-record.md)).
- **Decision 2, for versions below 1.0.0:** a breaking change (`!` or a `BREAKING CHANGE:` footer) moves the
  **minor** number, so that 1.0.0 is a decision about the project and not a side effect of one commit. From
  1.0.0 the rule above stands.
- The baseline is `v0.1.0` and `<svc>-v0.1.0`, tagged at `sha-2459417`, the version prod is pinned to. The
  statement "versions stay at 0.1.0 until a release exists" is now history: a release exists.
- **Decision 6, the Jira Release, is built (2026-10-10, Lab 42).** `jira-sync.py release X.Y.Z` reads that
  version's change record (`docs/releases/<date>-vX.Y.Z.md`, ADR-0035): the `Kaval X.Y.Z: ...` line becomes the
  Release's description, the record's date its release date, and every Jira key under `## Issues` gets the Release
  as its Fix version. It is a **manual** step after the promotion pull request merges, because the job that
  writes the record has no Jira access on purpose. It is a dry run unless given `--apply`, and running it twice
  changes nothing the second time. `Kaval 0.1.0` was made live with its nine issues. The Release is marked
  released: a record exists only for a version that was promoted to prod. Not done: moving a story that was missed
  in the commits (a story whose commits name no Jira key is not in the record, so it is not in the Release).
