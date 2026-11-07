# Lab 80 — OPA built inside the images: the pins, the checks, and how to bump them

**Phase:** 8 (hardening) · **Story:** `KAV-132` · **Time:** about 45 minutes, most of it waiting for builds · **Cost:** $0. Everything runs on a laptop with Docker; nothing touches AWS.

[ADR-0079](../adr/0079-where-the-opa-binary-comes-from.md) decided on 2026-11-07 that Kaval builds OPA itself, and [Lab 79](lab-79-building-opa-ourselves-and-proving-it.md) was the trial that showed it could. This lab is the result. Stage 1b of `services/agent/Dockerfile` and `services/executor/Dockerfile` no longer downloads OPA's release file: it compiles OPA from the commit that release was cut from, inside a Go image pinned by digest, with the one library the scanner flagged raised, and stops unless the result is the file recorded in the Dockerfile. You will read what is pinned, build the stage by hand and get the recorded checksum, make it refuse three wrong things, check the finished image the way CI does, scan it with **no** exception, and learn the four routine changes (a Go patch, an OPA release, a new scanner finding, an emptied override list).

## Prerequisites

- Docker (Desktop on Windows or macOS) with about 8 GB of memory and 6 GB of free disk; the first run pulls the Go image (about 1 GB).
- This repository at the commit that contains this lab, and a terminal in its root. In Git Bash on Windows, put `MSYS_NO_PATHCONV=1` in front of `docker` commands and write host paths inside them with `$(cygpath -m ...)`.
- The Python stages of the full image run under emulation on an x86 laptop, so Part 4 takes 7 to 10 minutes per image (the agent's took 7, the executor's 10); the OPA stage itself does not (it runs natively and cross-compiles).

## Part 1 — What the stage pins, and what stops each pin from being wrong (read-only)

Read stage 1b in `services/agent/Dockerfile` (the executor's is identical, and `scripts/dev/test_opa_build_pins.py` fails if the two ever differ):

| Pin | Where | What stops it being wrong |
|---|---|---|
| The Go toolchain | the digest on the stage's `FROM` line (`golang:1.27.3-trixie@sha256:af1bfc26...`, the image OPA's own Makefile names for its releases) | the digest is content-addressed; Dependabot proposes patch and digest bumps and ignores `golang` minor and major (`.github/dependabot.yml`) |
| The source | `OPA_COMMIT` (40 hex characters) and `OPA_VERSION` | `git fetch` by commit, then the stage checks `git rev-parse HEAD` equals it, then tags the checkout `v$OPA_VERSION` (without the tag Go stamps a made-up version and scanners report OPA itself as old, Lab 79 "A trap") |
| The fixes | `OPA_GO_GET`, a list of `module@version` raised above the versions in OPA's own `go.mod` | Go's checksum database verifies every library; the second step looks each one up in the finished program with `go version -m` and stops if it is missing |
| The result | `OPA_SHA256_ARM64`, declared after the compile and checked in its **own** step | builds are byte-for-byte reproducible, so a changed value means an input changed; the failing build prints the new value; changing the expected value never recompiles |
| The platform | `test "${TARGETARCH}" = arm64` | the images are `linux/arm64` only ([CLAUDE.md](../../CLAUDE.md) constraint 2); the old download stage fetched the arm64 file whatever the target, silently |

The stage runs on the machine that builds (`FROM --platform=$BUILDPLATFORM`) and cross-compiles: native on CI's arm64 runner, fast on a laptop. After the compile it empties Go's module and build caches in the same step, so the stage's saved layer holds the source tree and the 60 MB program, not the downloaded libraries and compiled packages (sizes are in Part 2); that layer is what the CI layer cache keeps.

## Part 2 — Build the stage by hand and get the recorded checksum

Cut the stage out of the Dockerfile exactly as written, add a last stage that only exports the file, and build it:

```bash
export LAB=~/lab80 && rm -rf $LAB && mkdir -p $LAB/ctx $LAB/out
awk '/ AS opa$/ {p=1} /^# .*Stage 2/ {p=0} p' services/agent/Dockerfile > $LAB/ctx/Dockerfile
printf '\nFROM scratch AS export\nCOPY --from=opa /opa /opa\n' >> $LAB/ctx/Dockerfile
docker buildx build --no-cache --progress=plain --platform linux/arm64 --target export -o type=local,dest=$LAB/out $LAB/ctx 2>&1 | grep -E 'opa 1\.|checksum|DONE|ERROR' | tail -4
sha256sum $LAB/out/opa
```

The last line of the build says `opa 1.21.4 built from 05c845177c80747a2aabb607f9318f981993cc76, overrides applied, sha256 f6850f6c6b1335f62918bdb281f627a1b029ae16ecf8811e2ccefa174c228ba3`, and `sha256sum` prints the same value: the one in the Dockerfile. On 2026-11-07 five clean builds took between 111 and 178 seconds on a 12-CPU laptop (178 s is the one printed in this part, run while two other builds were using the machine); a build with the compile already cached takes seconds. The file is 60,298,924 bytes, the same size as Lab 79's, and differs from it in exactly 40 of them: the first part of the Go build ID (`go tool buildid`; a hash of the build's inputs, which included the flag), written twice near the start of the file. The rest of the ID, which hashes the program's contents, is identical, so it is the same program with a different label. What the stage leaves behind after its clean-up, measured: the 138 MB source tree and the 58 MiB program; Go's module cache is gone and its build cache is empty.

Three checks that the recipe is what it says:

- **The same bytes every time.** The agent's recipe built from empty caches and the executor's recipe built from empty caches gave the identical file. (CI's own build, on a different machine, is Part 9.)
- **The only change from Lab 79 is one flag.** Lab 79's script built `72f34858...`. That recipe passed `-ldflags "-X .../version.Hostname=kaval-build"`, which turned out to do nothing in OPA v1.21.4 (both files print an empty hostname), so this stage leaves it out. Putting the flag back into this stage's compile line and the old checksum into the check reproduced `72f34858...` exactly, so the guard, the cache clean-up and the step order change nothing in the program.
- **It is the same program.** Part 6.

## Part 3 — Make it refuse

A check you have not seen fail proves nothing. Each command below changes one input with `--build-arg`, from the same `$LAB/ctx`:

```bash
B="docker buildx build --progress=plain --target export -o type=local,dest=$LAB/out-bad $LAB/ctx"
$B --platform linux/amd64 2>&1 | grep -E '^#[0-9]+ [0-9.]+ (the images are|opa checksum|override not)'      # 3.1 the wrong platform
$B --platform linux/arm64 --build-arg OPA_SHA256_ARM64=$(printf '0%.0s' {1..64}) 2>&1 | grep -E '^#[0-9]+ [0-9.]+ (the images are|opa checksum|override not)'   # 3.2 a wrong checksum
$B --platform linux/arm64 --build-arg 'OPA_GO_GET=golang.org/x/net@v0.62.0 golang.org/x/tools@v0.51.0' 2>&1 | grep -E '^#[0-9]+ [0-9.]+ (the images are|opa checksum|override not)'   # 3.3 a fix that is not in the program
ls $LAB/out-bad 2>&1 | head -2
```

(The `grep` keeps only the build's own messages; without it Docker also echoes the long command it is running.)

| Change | What the build says | How long (2026-11-07) |
|---|---|---|
| 3.1 `--platform linux/amd64` | `the images are linux/arm64 only: build with --platform linux/arm64` | 2 s |
| 3.2 expected checksum all zeros | `opa checksum differs: expected 000...0, built f6850f6c...228ba3` (it prints the value to copy, and the compile layer is reused) | 2 s |
| 3.3 `golang.org/x/tools@v0.51.0` in the list | `override not applied: golang.org/x/tools@v0.51.0` (that library is in OPA's `go.mod`, so `go get` is happy, but the program does not use it; the check looks at the finished program) | 174 s: the changed list recompiles |

None of the three leaves a file: `ls` says `out-bad` does not exist.

## Part 4 — The finished image, checked the way CI checks it

```bash
v=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' services/agent/kaval_agent/__init__.py)
docker buildx build --platform linux/arm64 -f services/agent/Dockerfile --build-arg VERSION=$v -t kaval/agent:ci --load .
docker run --rm --platform linux/arm64 --entrypoint opa kaval/agent:ci version
docker run --rm --platform linux/arm64 --entrypoint sha256sum kaval/agent:ci /usr/local/bin/opa
```

```
Version: 1.21.4
Build Commit: 05c845177c80747a2aabb607f9318f981993cc76-dirty
Build Timestamp: 2026-10-23T13:00:56Z
Build Hostname:
Go Version: go1.27.3
Platform: linux/arm64
Rego Version: v1
WebAssembly: available
```

(`-dirty` is there because `go get` edited `go.mod`; OPA's own release says the same. `Build Hostname` is empty in both for the reason in Part 2.)

The last command prints `f6850f6c...228ba3`, the Dockerfile's value. CI does not trust the eye: the step "agent and executor images carry a working opa" in `.github/workflows/ci.yml` reads `OPA_VERSION`, `OPA_COMMIT` and the Go version **from the Dockerfile** and fails unless `opa version` reports that version, a `Build Commit` starting with that commit, `Go Version: go1.27.3` and `WebAssembly: available`. The step's own text was run here against both images (`ALL IMAGE CHECKS PASSED` for `agent` and for `executor`), then `scripts/dev/test_opa_build_pins.py` was shown able to fail: 13 deliberate breakages (a different commit in one Dockerfile, a zero checksum, a floating `@latest` override, a Go image without its digest, the commit check or the version tag or the arm64 guard deleted, the checksum compared in the compile step, the runtime copy moved, the runner's `OPA_VERSION` changed alone, the Go assertion removed from `ci.yml`, the Dependabot rule removed), each made on a scratch copy of the files, were each caught by the test that is meant to catch them, and the unbroken control passed.

## Part 5 — The scanner, with no exception

`.github/trivy-ignore.yaml` is now an empty list (`vulnerabilities: []`); both scans (`ci.yml` and `release.yml`) still read it. Scan the image with the Trivy version and flags CI uses:

```bash
TRIVY=$(grep -m1 'TRIVY_VERSION:' .github/workflows/ci.yml | sed -E 's/.*"([0-9.]+)".*/\1/')
docker save kaval/agent:ci -o $LAB/agent.tar
docker run --rm -v $LAB/agent.tar:/image.tar:ro -v "$PWD/.github/trivy-ignore.yaml":/ig.yaml:ro -v kaval-trivy-cache:/root/.cache aquasec/trivy:$TRIVY \
  image --input /image.tar --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --ignorefile /ig.yaml --exit-code 1 --no-progress; echo "exit $?"
```

Results on 2026-11-07 (Trivy 0.74.0, `--exit-code 1`, the exception file as committed, an empty list):

| Image | Trivy exit | Findings |
|---|---|---|
| `kaval/agent:ci`, this recipe | **0** | none |
| `kaval/executor:ci`, this recipe | **0** | none |
| control: an image built from `main`'s Dockerfile (the downloaded OPA) | **1** | exactly one: `usr/local/bin/opa` (gobinary), `golang.org/x/net` v0.60.0, CVE-2026-78661, HIGH, fixed in 0.62.0 |

The control is what makes the two passes mean something: the same scan, on the same kind of image, with the same empty list, fails on the one flaw this change exists to remove. (The first attempt at the control built nothing, because the script that fetched `main`'s Dockerfile had written an empty file, and its "scan" failed on a missing input: exit 1 for the wrong reason, which looks exactly like the right one. The control script now stops unless the image exists and keeps the scan's whole output, and the output above is from that run.)

## Part 6 — Same answers as OPA's own file

Lab 79 Part 4.3's comparison, re-run on the file Part 2 built: OPA's published arm64 file (the one the Dockerfile used to download, `810aa96e...`, fetched as in Lab 79 Part 1) against ours, both asked Kaval's real question, `opa eval --format=json -I -d policy/ data.policy.decision`, over every 30th input of the grid, 100 questions (grid position 30 is among them: `restart_pod`, blast radius `pod`, reversible, confidence 0.92, the first input where a promotion can make the answer `auto`; the control needs at least one such input, and every 60th would miss it). The files are arm64, so on an x86 laptop they run under emulation: it asks one question at a time, so it took about 11 minutes on 2026-11-07.

```bash
mkdir -p $LAB/official && gh release download v1.21.4 -R open-policy-agent/opa -p opa_linux_arm64_static -D $LAB/official
# $LAB/compare_opa.py is the script printed in Lab 79 Part 4.3
docker run --rm --platform linux/arm64 -v $LAB/official:/o:ro -v $LAB/out:/n:ro -v "$PWD/policy:/policy:ro" -v $LAB/compare_opa.py:/t.py:ro \
  python:3.11-slim-bookworm python /t.py /o/opa_linux_arm64_static /n/opa /policy 30
```

It prints one line per round (2026-11-07):

```
empty promotions (as in production): 100 questions, identical answers 100
one promotion added: 100 questions, identical answers 100
CONTROL: the same file, different data (must differ): 100 questions, identical answers 99
```

Both rounds with the official file and ours must show every answer identical, and the control, which asks the same file against the two data sets, must show a difference, or the comparison could not have noticed one. The script exits 0 only in that case.

A stricter run of the same idea (a script that is not in the repository: it puts every `auto`-capable input into the sample and evaluates four at a time, and it also checks that the two files' `opa version` fields and `opa test` results agree) gave these results on the same two files:

| | Official file | Our build |
|---|---|---|
| `opa version` | `Version: 1.21.4`, `Build Commit: 05c8451...-dirty`, `Build Timestamp: 2026-10-23T13:00:56Z`, `Go Version: go1.27.3`, `Platform: linux/arm64` | the same, field for field |
| Kaval's policy tests (`opa test policy/`) | 29 of 29 pass | 29 of 29 pass |
| 308 questions, the real (empty) promotions file | `ask` 127, `never` 181 | **308 of 308 identical answers** |
| the same questions with one promotion added | `ask` 123, `auto` 4, `never` 181 | **308 of 308 identical answers** |
| control: the official file asked against the two data sets | differs on **4** (exactly the `auto` ones), so the comparison can see a difference | |

## Part 7 — The four routine changes

1. **Dependabot proposes a newer Go patch or digest** (a pull request that edits the `FROM golang:...@sha256:...` line in both Dockerfiles). The image jobs fail with `opa checksum differs: expected <old>, built <new>` for a new Go *version*: read the change (a new Go patch is the point of this design), copy `<new>` into `OPA_SHA256_ARM64` in **both** Dockerfiles in the same pull request, and CI goes green. A new *digest* of the same Go version should give the same file; if the build prints a different value, find out why before copying it.
2. **OPA publishes a release you want.** Check the commit three ways (Lab 79 Part 2), then change `OPA_VERSION` and `OPA_COMMIT` in both Dockerfiles, run Part 2 for the new checksum, put it in both, and change `OPA_VERSION` and `OPA_SHA256` (the amd64 file on OPA's release page, used for `opa test`) in `ci.yml`. The tests in `scripts/dev/test_opa_build_pins.py` fail until all three places agree. Re-run Parts 5 and 6, and OPA's own tests if the override list changed (Lab 79 Part 4.4).
3. **The scanner flags a library inside OPA.** Add `module@version` (the fixed version) to `OPA_GO_GET`, with a comment naming the finding, the date and what ends it. The second step then fails if it did not land. This replaces an entry in `.github/trivy-ignore.yaml`: a finding in the Go standard library is answered by a newer Go image; a finding in a library by a line here. Only a finding with *no* fix anywhere is a case for an exception, under the rules in ADR-0033.
4. **OPA catches up.** When OPA's own `go.mod` at the pinned tag already has the fixed version (`git show <tag>:go.mod`), delete that entry from `OPA_GO_GET`. An empty list is allowed.

## Part 8 — If it breaks

| What you see | What it means | What to do |
|---|---|---|
| `the images are linux/arm64 only` | built without `--platform linux/arm64` | add it (`make build` and `compose.yaml` already do) |
| `opa checksum differs: expected X, built Y` | an input changed: the Go image, `OPA_COMMIT`, `OPA_GO_GET`, or a library the build resolved | read what changed; if it is intended, copy `Y` into both Dockerfiles |
| `override not applied: m@v` | the library is not in the finished program, or `go get` settled on another version | `go version -m` the file; remove or correct the entry |
| `go: downloading ...` fails, or `git fetch` fails | GitHub or the Go proxy was unreachable | run it again; nothing was published (the build fails closed) |
| Trivy reports CVE-2022-28946, CVE-2022-33082 or CVE-2025-46569 for `opa` | the `git tag` line is missing, so Go stamped a made-up old version | restore it |
| `test_both_images_build_opa_with_identical_instructions` fails | one Dockerfile was changed and the other not | make them match |

## Part 9 — What it showed

All on 2026-11-07, nothing touching AWS:

| Question | Answer |
|---|---|
| Is it the commit OPA released? | Yes: the stage fetches `05c8451...` by hash, checks it, and stops otherwise |
| Is the file the same every time, on every machine? | Yes: three clean builds on the laptop (the agent's recipe, the executor's recipe, Part 2) gave `f6850f6c...`; and in pull request #332 **CI's native arm64 runner**, a different kind of machine from the laptop (which cross-compiles for arm64), built the same checksum in both image jobs |
| What changed from Lab 79's file? | Only the do-nothing hostname flag: put back, the stage reproduces Lab 79's `72f34858...`; the two files differ in 40 bytes, all of them the start of Go's build ID |
| Does it refuse wrong inputs? | Yes: the wrong platform (2 s), a wrong checksum (2 s, prints the right one) and a fix that is not in the program (174 s: it recompiles first) each stop the build and leave no file |
| Is it the same program as OPA's file? | Yes: Part 6 |
| Does the scanner accept it with no exception? | Yes: both real images exit 0 with an empty exception list; an image from `main`'s Dockerfile exits 1 on CVE-2026-78661 alone |
| Does CI check what is inside the image? | Yes: `Version`, `Build Commit`, `Go Version` and `WebAssembly` are asserted against the Dockerfile's pins; the step's own text passed on both laptop-built images and in CI |
| Do the new tests have teeth? | Yes: 13 deliberate breakages, 13 caught, and the unbroken control passes |
| The whole suite | 1,677 passed, 356 skipped (they need a database or OPA; CI runs them), 8 expected failures, in 4 minutes |
| What does it cost? | AWS: $0. GitHub's minutes are free for a public repository. On CI the stage took **42 s** to compile on the arm64 runner (fetch, `go get`, build, clean-up) and the whole `image agent` job 2 min 11 s (the executor's 2 min 25 s) against 37 s for the same job on the documents-only pull request #331, which still has the download stage. On the next push to the same pull request the compile layer came from the CI cache and the two jobs took 1 min 1 s (agent) and 1 min 7 s (executor): about 25 s more than the download. The laptop's 111 to 178 s was cross-compiling beside other work. The image does not grow: the file is the same size as the one it replaces |

## Part 10 — Clean up

```bash
rm -rf $LAB
docker image rm kaval/agent:ci kaval/executor:ci kaval/agent:main-control 2>/dev/null
docker volume rm kaval-kav131-gomod kaval-trivy-cache 2>/dev/null          # the Go module cache and the Trivy database cache
docker image rm golang:1.27.3-trixie aquasec/trivy:$TRIVY debian:bookworm-slim   # about 2.5 GB; they are pulled again when needed
```
