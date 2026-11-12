# Lab 88 — Packing our own code with a hash-checked tool

**Phase:** 8 (hardening) · **Story:** `KAV-111` · **Time:** about 45 minutes (the arm64 builds are slow on a laptop) · **Cost:** $0 (Docker on your own laptop; nothing in AWS for Parts 1 to 5)

Every Kaval image installs its libraries with `pip install --require-hashes`, so a library whose file changed is refused. The last step, installing Kaval's own code, was not checked, and
the tool that packs that code was downloaded fresh on every build. This lab shows the gap, closes it, and proves the images still hold the same code
([ADR-0086](../adr/0086-kavals-own-code-is-packed-by-a-hash-checked-tool-and-installed-as-a-wheel.md); the Scorecard row that found it is in [Lab 71](lab-71-a-security-policy-owners-and-a-weekly-scorecard.md)).

## Prerequisites

- The repository checked out, `make sync`
- Docker running and able to build `linux/arm64` images (on a Windows or x86 laptop this works through emulation and is slow)
- A saved copy of the old Dockerfile for the comparison: `git show f2cd828:services/gateway/Dockerfile > /tmp/old-gateway.Dockerfile` (any commit before this story will do)

## Part 1 — See the gap (the old Dockerfile)

```bash
docker build --platform linux/arm64 -f /tmp/old-gateway.Dockerfile -t kav-old-gateway . 2>&1 | tee old.log
grep -n "Installing build dependencies" old.log
```

You will find `Installing build dependencies: started` and, about 38 seconds later, `finished`. That step is pip downloading `hatchling` and its helpers from PyPI so it can build Kaval's wheel.
Nothing in `uv.lock` and no hash is involved; the pin in `pyproject.toml` (`hatchling==1.33.0`) names a version, not a file. The Scorecard counted the four `pip install --no-deps .` lines;
the download is the part that matters.

## Part 2 — Put the tool in the lock

`pyproject.toml` gets a second listing of the tool, as a dependency group:

```toml
[dependency-groups]
build = ["hatchling==1.33.0"]
```

Then lock **with the same `uv` version the images use** (0.13.7; a newer or older `uv` rewrites unrelated lines of the file):

```bash
uv tool run --from uv==0.13.7 uv lock
git diff --stat uv.lock           # additions only: hatchling, tomlkit, trove-classifiers (+ the lines pointing at them)
uv tool run --from uv==0.13.7 uv export --frozen --only-group build --no-emit-project
```

The export lists six packages (`hatchling`, `packaging`, `pathspec`, `pluggy`, `tomlkit`, `trove-classifiers`), each with the file hashes pip will demand. The group is not a default group, so `make sync` does not install it.

## Part 3 — The `wheel` stage

Each of the four Dockerfiles (`services/gateway`, `collector`, `agent`, `executor`) now has three changes:

1. The `lock` stage also runs `uv export --frozen --only-group build --no-emit-project -o /build-requirements.txt`.
2. A new `wheel` stage, on the same digest-pinned base image as the runtime stage, does exactly this:
   ```dockerfile
   RUN pip install --require-hashes -r /tmp/build-requirements.txt
   COPY pyproject.toml ./                      # and the service packages, as before
   RUN pip wheel --no-deps --no-build-isolation --wheel-dir /wheels .
   ```
   `--no-build-isolation` is the one that matters: pip may not fetch a packing tool of its own, so it uses the checked one already installed. `--no-deps` stops it fetching libraries.
3. The runtime stage no longer copies the source. It copies `/wheels` from the `wheel` stage, runs `pip install --no-deps /wheels/*.whl`, deletes `/wheels`, and drops pip and setuptools as before. A wheel holds the Python packages only,
   so a file read at run time must still be copied in by hand: the **gateway** image keeps `pyproject.toml` and `migrations/` (the migration job runs `alembic upgrade head` there).

## Part 4 — The test that keeps it this way

```bash
python -m pytest scripts/dev/test_pip_install_pins.py -v
```

31 tests. They fail on the commit before this change for the right reasons (17 about the real files) and pass after; the four added after the staging failure (below) fail on the first version of the gateway image. They check that:

- every `pip install` in the four Dockerfiles is hash-checked or installs only finished `.whl` files;
- the `wheel` stage installs the tool by hash **before** building, and builds with `--no-build-isolation --no-deps`;
- the runtime stage copies the wheel, installs it, and has no packing tool in it;
- the `wheel` and runtime stages use the same base-image digest;
- `hatchling` has one version in `pyproject.toml`'s `build-system`, its `build` group and `uv.lock`;
- the rule itself refuses the lines it must refuse: `pip install --no-deps .`, an editable install, a bare `hatchling==1.33.0`, an unhashed requirements file, a wheel plus an unchecked name;
- the gateway image still copies `pyproject.toml` and `migrations/` into `/app`, alembic's settings still say `script_location = "%(here)s/migrations"`, and the chart's migrate job still runs `alembic upgrade head` from the gateway image.

## Part 5 — Build the new images and compare them with the old

```bash
for s in gateway collector agent executor; do
  docker build --platform linux/arm64 -f services/$s/Dockerfile -t kav-new-$s .
done
grep -c "Installing build dependencies" new.log     # 0: nothing is fetched for the build any more
```

To prove the images hold the same code, list the files in the old and the new image and compare:

```bash
id=$(docker create --platform linux/arm64 kav-new-gateway); docker export "$id" | tar -t | sort > new.txt; docker rm "$id"
id=$(docker create --platform linux/arm64 kav-old-gateway); docker export "$id" | tar -t | sort > old.txt; docker rm "$id"
diff old.txt new.txt
```

**Result (2026-11-11, all four built for arm64 on the laptop, old and new; the gateway row is the corrected build, see Part 5b):**

| Image | Files added | Files removed | What was removed | Packing tool fetched during the build (old → new) | Starts, imports its code from `site-packages` |
|---|---|---|---|---|---|
| gateway | 0 | 51 | `/app/services/…` (it keeps `/app/pyproject.toml` and `/app/migrations`) | yes → no | yes (0.4.0); `alembic heads` answers |
| collector | 0 | 52 | `/app/pyproject.toml`, `/app/services/…` | yes → no | yes (0.3.0) |
| agent | 0 | 52 | the same | yes → no | yes (0.4.0); `opa` 1.21.4 runs; `/app` still holds `policy` and `runbooks` |
| executor | 0 | 63 | the same, plus the executor's own source copy | yes → no | yes (0.3.0) |

The installed code under `site-packages` and every dependency is the same list of files (comparing builds of different versions also shows the `kaval-<version>.dist-info` folder renamed, nothing else). The removed source copy is
never imported: the code runs from `site-packages` ([ADR-0017](../adr/0017-opa-policy-engine-and-earned-autonomy.md)), and `KAVAL_POLICY_DIR` and `KAVAL_RUNBOOKS_DIR` point at `/app/policy` and `/app/runbooks`, which are still there.

## Part 5b — Run the thing the image is for (this step was missing the first time)

A file list proves what is inside an image, not that the image still works. The first version of this change also removed `/app/pyproject.toml` from the gateway image. The list showed it; the lab said "nothing reads it" because the search had
only covered the source folders; and staging's migration job then failed with `No 'script_location' key found in configuration` (alembic reads `[tool.alembic]` from that file). Run the migration tool in the built image:

```bash
docker run --rm --platform linux/arm64 --entrypoint alembic kav-new-gateway heads
```

A good image prints the current migration head (`d4f7a2c9e815 (head)` on 2026-11-12). The broken image printed the `script_location` error, on the laptop, in a second. For every file an image drops, search the whole repository, not only the Python, for what reads it (alembic, the chart, the entrypoints) before calling it unused.

## Part 6 — Prove the new rule refuses

Put the old line back in one Dockerfile and run the test:

```bash
sed -i 's#pip install --no-deps /wheels/\*.whl#pip install --no-deps .#' services/gateway/Dockerfile
python -m pytest scripts/dev/test_pip_install_pins.py -q     # fails: gateway has an install with no hash check
git checkout services/gateway/Dockerfile
```

## Part 7 — The live proof (staging; the first attempt failed, the second is still to come)

**First attempt, 2026-11-12 00:00 (release v0.4.1, never promoted):** staging built, but the migration job failed in the first version of the gateway image (the missing `pyproject.toml`, Part 5b), so no app started and the
smoke test could not pass. The KAV-116 database check run on that copy was inconclusive (read and insert also answered false, because the permissions are applied only after the release is ready). Staging was deleted at 00:09 (about 2 cents),
the sweep was clean and production's 23 backup copies were unchanged. This is staging doing its job: nothing reached production.

**Second attempt:** a corrected gateway image goes through the release round again (v0.4.2), then a fresh staging session. The images change, so the change goes through the normal release round: merge, the bot's release pull request, staging smoke (7 of 7), and promotion to production on its own go. `make staging-up` builds
staging from the new images and `make staging-smoke` runs the checks; no new command is needed. The staging session is shared with the `KAV-116` proof ([Lab 87](lab-87-a-decision-is-written-once.md)),
about 5 cents.

After the first weekly Scorecard run on `main` (Sundays), read the Pinned-Dependencies row again and write the number in [Lab 71](lab-71-a-security-policy-owners-and-a-weekly-scorecard.md)'s table. Do not expect it to be 10 on the strength of this alone; the row also counts GitHub Actions and other Dockerfile lines.

## Done when

- [x] The four images build, with no "Installing build dependencies" step, and hold the same installed code as before
- [x] 31 tests in `scripts/dev/test_pip_install_pins.py` pass, and fail on the old Dockerfiles and on the first gateway image
- [ ] CI is green on the pull request (14 checks, including the image build and Trivy for all four images)
- [x] The release round has run (v0.4.2) and staging smoke passed 7 of 7 on the new images *(2026-11-12, `sha-d1058eb`, including the migration that failed on v0.4.1)*
- [ ] Production has been promoted on its own go
- [ ] The Scorecard's Pinned-Dependencies row has been re-read and recorded
