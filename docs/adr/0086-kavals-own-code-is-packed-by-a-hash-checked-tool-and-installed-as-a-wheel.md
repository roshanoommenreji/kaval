# ADR-0086: Kaval's own code is packed by a hash-checked tool and installed as a wheel

**Status:** Accepted on 2026-11-11 (Roshan: "go with your recommendations" on the plan). Built in `KAV-111`; the staging proof (2026-11-12, 7 of 7) and the Scorecard re-read (2026-11-12) are done: see Consequences.
**Date:** 2026-11-11
**Related:** [ADR-0010](0010-ci-pipeline-and-supply-chain.md) (libraries installed by hash, base images pinned by digest),
[ADR-0071](0071-the-public-repo-gets-a-security-policy-owners-and-a-weekly-scorecard.md) (the Scorecard run that found this),
[ADR-0072](0072-a-written-threat-model-and-a-prompt-injection-set.md) (threat-model row T-SUPPLY-1),
[ADR-0079](0079-where-the-opa-binary-comes-from.md) (the same idea for OPA: build from a pinned source, check the result),
[Lab 88](../labs/lab-88-packing-our-own-code-with-a-hash-checked-tool.md), `KAV-111`

## Context

Every service image installs its libraries with `pip install --require-hashes`, so a library whose file changed is refused. The last step of each image was different:
`pip install --no-deps .`, which builds Kaval's own code into a wheel and installs it. The OpenSSF Scorecard (Lab 71) flagged those four lines as "installed without a hash".

Looking behind the finding showed a bigger gap than the one the Scorecard names. To build the wheel, pip fetches the packing tool, `hatchling`, from PyPI on every build, together with the
tool's own helpers (`packaging`, `pathspec`, `pluggy`, `tomlkit`, `trove-classifiers`). We pinned the tool's version (`hatchling==1.33.0`), but a version number is not a fingerprint, the helpers were not
pinned at all, and none of it was hash-checked. A tampered copy of any of those six would have run during the build and could have changed the code that ships in every image.
The code itself arrives from the repository checkout, which CI has just verified, so a hash of it would add nothing; the tool is the part that matters.

## Options (what real companies do)

1. **Leave it and write it down.** Costs nothing and keeps the real gap open. The Scorecard row stays at 8.
2. **Install in editable mode (`pip install --no-deps -e .`).** The Scorecard accepts this form for a local directory. It would raise the score and add no safety (the tool is still fetched unchecked),
   and an editable link is an odd thing to put in a production image.
3. **Pack the code in a separate build stage with the tool installed from the lock by hash, and install only the finished wheel in the runtime image.** The usual answer in teams that care about build
   provenance. It closes the real gap, and a plain `pip install /wheels/*.whl` is the form the Scorecard counts as pinned.

## Decision

Option 3, in all four service images (gateway, collector, agent, executor):

1. **`uv.lock` holds the packing tool.** `pyproject.toml` gets `[dependency-groups] build = ["hatchling==1.33.0"]`, so the lock contains `hatchling` and its five helpers with file hashes. The group is not a default
   group, so `make sync` and CI's `uv sync` do not install it; only the images use it. The `build-system` pin stays too, because pip and other tools read it.
2. **The `lock` stage exports the group** (`uv export --frozen --only-group build`) to `/build-requirements.txt`, the same way it already exports the libraries.
3. **A new `wheel` stage** (same digest-pinned Python base as the runtime image) installs that file with `pip install --require-hashes`, copies the repository's service packages, and runs
   `pip wheel --no-deps --no-build-isolation`. `--no-build-isolation` is the important flag: it forbids pip from fetching its own packing tool, and `--no-deps` forbids fetching libraries.
4. **The runtime image copies in the wheel and installs it** (`pip install --no-deps /wheels/*.whl`), deletes it, then drops pip and setuptools as before. It no longer copies the source packages, so the
   image holds one copy of the code (the installed one) and never holds the packing tool. **Files the code reads at run time are not in the wheel and are still copied in by hand:** the gateway image keeps
   `pyproject.toml` and `migrations/`, because the migration job runs `alembic upgrade head` there and alembic reads `[tool.alembic]` from that file (see "What went wrong first" below).
5. **A test keeps it this way** (`scripts/dev/test_pip_install_pins.py`): every `pip install` in the four Dockerfiles is either hash-checked or a finished `.whl`; the wheel stage installs the tool by hash before
   building and builds with `--no-build-isolation --no-deps`; the runtime stage has no packing tool; the wheel stage uses the same base-image digest as the runtime stage; and the version of `hatchling` is the same in `pyproject.toml`'s
   `build-system`, its `build` group and `uv.lock`. The rule itself is tested with the cases it must refuse (the old line, an editable install, a bare name, an unhashed requirements file).

## Consequences

- **The build is now closed:** every file the build installs or runs comes from the lock with a hash or from the pinned base image. The only thing not hash-checked is the repository's own source, which is what is being built.
- **The image is slightly different inside, on purpose:** the copy of the source under `/app/services` is gone (and `/app/pyproject.toml` in the agent, collector and executor, which never read it; the gateway keeps it) (the installed copy in `site-packages` is the one that ran all along; `KAVAL_POLICY_DIR` and the runbooks path were
  already set explicitly for that reason, [ADR-0017](0017-opa-policy-engine-and-earned-autonomy.md)). A before-and-after listing of each image is in Lab 88.
- **What went wrong first (2026-11-12):** the first version of this change also dropped `/app/pyproject.toml` from every image, and the before-and-after listing showed it, but it was waved through because the search for
  "what reads these files" looked only at the source folders. The gateway's migration job then failed on staging ("No 'script_location' key found in configuration"), staging never came up, and release v0.4.1 was
  never promoted. Production was not affected. The fix keeps `pyproject.toml` in the gateway image, adds tests that the gateway image holds what the migration job reads (and that the chart still runs alembic from it), and makes
  `docker run … alembic heads` on the built image a step of the lab. Lesson: when an image is cut down, every removed file needs its reader found by search, and the real proof is a staging build, not a file list.
- **A new image means a release round.** The change merges, the bot opens the release pull request, the images build, staging smoke tests them, and promotion to production waits for its own go. Nothing runs in AWS because of this change itself.
- **Dependabot keeps working:** its `uv` update reads `pyproject.toml` and rewrites `uv.lock`, so a new `hatchling` arrives in both places at once; if only the `build-system` line is bumped, the version test goes red rather than letting the two drift.
- **The Scorecard's Pinned-Dependencies row** should rise now that the four lines are gone; the real number is read from a Scorecard run after this is on `main`, not promised here. **Read on 2026-11-12:** the scan closed the four Pinned-Dependencies alerts as fixed and raised none, so the row went from 8 to 10 ([Lab 71](../labs/lab-71-a-security-policy-owners-and-a-weekly-scorecard.md)).
- Cost: $0. Build time grows by a few seconds (one more stage); the stage is cached until `uv.lock` or the code changes.
