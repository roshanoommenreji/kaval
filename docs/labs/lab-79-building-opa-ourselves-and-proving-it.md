# Lab 79 — Building OPA ourselves, and proving it is the same program

**Phase:** 8 (hardening) · **Story:** `KAV-131` · **Time:** about 90 minutes, most of it waiting for builds · **Cost:** $0. Everything runs on a laptop with Docker; nothing touches AWS and nothing in the repository changes.

[ADR-0079](../adr/0079-where-the-opa-binary-comes-from.md) asks whether Kaval should keep downloading OPA's release file and excusing each scanner finding, or build OPA from its published source. This lab is the trial behind that decision: it builds OPA v1.21.4 (the version in use) from source with the flawed library raised, then checks five things: it is the commit OPA released, it is the same program, the scanner is satisfied with no exception, it can be built again to the byte, and what it costs.

## Prerequisites

- Docker (Desktop on Windows or macOS) with about 8 GB of memory and 6 GB of free disk; the first run pulls the Go image (about 1 GB) and three small ones.
- `gh` (the GitHub CLI) logged in, for public data only. `python` 3.11 or later. `git`.
- A scratch folder outside the repository, called `$LAB` below. In Git Bash on Windows, put `MSYS_NO_PATHCONV=1` in front of `docker` commands and write host paths with `cygpath -m`.
- Run the commands from the repository root unless a line says otherwise.

```bash
export LAB=~/lab79 && mkdir -p $LAB/official $LAB/out
export GO=golang:1.27.3-trixie@sha256:af1bfc2614ebb1dbd31af514b78740fd678850e76498762b425ecee4c5c6ea80   # the image OPA's own Makefile names for its releases
```

## Part 1 — See the finding, and see that its code is not in the program (read-only)

Download the release file and the checksum OPA publishes beside it, and check both against the pin in the Dockerfile:

```bash
gh release download v1.21.4 -R open-policy-agent/opa -p opa_linux_arm64_static -p opa_linux_arm64_static.sha256 -D $LAB/official
(cd $LAB/official && sha256sum -c opa_linux_arm64_static.sha256)
grep -m1 'ARG OPA_SHA256=' services/agent/Dockerfile          # the same 810aa96e... value
```

Scan it with the Trivy version CI pins, flags and all, and an **empty** exception list, which is what CI would see with no exception at all:

```bash
mkdir -p $LAB/rootfs/usr/local/bin && cp $LAB/official/opa_linux_arm64_static $LAB/rootfs/usr/local/bin/opa
printf 'vulnerabilities: []\n' > $LAB/no-exceptions.yaml
docker run --rm -v $LAB/rootfs:/rootfs:ro -v $LAB/no-exceptions.yaml:/ig.yaml:ro aquasec/trivy:0.74.0 \
  rootfs --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --ignorefile /ig.yaml --exit-code 1 --no-progress /rootfs
```

On 2026-11-07 this exits 1 with `Total: 1 (HIGH: 1, CRITICAL: 0)`: CVE-2026-78661, library `golang.org/x/net`, installed v0.60.0, fixed in 0.62.0.

Now ask the file itself. Go writes the libraries it was built from into the program, and the program keeps the names of every function it contains:

```bash
docker run --rm -v $LAB/official:/o:ro $GO go version -m /o/opa_linux_arm64_static | grep -E 'golang.org/x/net|vcs.revision|-tags'
docker run --rm -v $LAB/official:/o:ro $GO sh -c 'go tool nm /o/opa_linux_arm64_static | grep -c "golang.org/x/net/html\."'
docker run --rm -v $LAB/official:/o:ro $GO sh -c 'go tool nm /o/opa_linux_arm64_static | grep -oE "golang.org/x/net/[A-Za-z0-9_/]+" | sort | uniq -c | sort -rn'
```

The library is v0.60.0, the build settings are `-tags=opa_wasm` and the commit `05c845177c80...`, and **the count of functions from the flawed package, `x/net/html`, is 0**. Only `http2`, `http2/hpack`, `trace`, `dns/dnsmessage`, `internal/timeseries`, `idna`, `http/httpproxy` and `http/httpguts` from that library are in the program. Trivy matches the library's *version*, not the functions in use, which is why it flags a flaw whose code is not even there. (The same count is 0 for both rebuilds below.)

Go's own checker answers the question the other way round, by function. OPA runs it weekly on its releases (`release-vulnerability-check.yaml`):

```bash
docker run --rm -v $LAB/official:/o:ro $GO sh -c 'go install golang.org/x/vuln/cmd/govulncheck@v1.9.0 && /go/bin/govulncheck -mode=binary /o/opa_linux_arm64_static'
```

It says nothing about `x/net`, and reports a **different** flaw, GO-2026-6865 in `github.com/klauspost/compress` (v1.20.0, fixed in 1.20.2, five vulnerable functions present). Trivy did not flag that one on 2026-11-07; databases fill in over days, and that is how the fifth finding arrived. It is probably the sixth.

## Part 2 — Check the source three ways

```bash
gh api repos/open-policy-agent/opa/git/ref/tags/v1.21.4 --jq .object.sha                # 1. GitHub's API
git ls-remote https://github.com/open-policy-agent/opa.git refs/tags/v1.21.4            # 2. git itself
python - <<'EOF'                                                                        # 3. the id GitHub writes into the source archive
import tarfile, urllib.request, io
data = urllib.request.urlopen("https://github.com/open-policy-agent/opa/archive/refs/tags/v1.21.4.tar.gz").read()
print(tarfile.open(fileobj=io.BytesIO(data), mode="r:gz").pax_headers["comment"])
EOF
```

All three print `05c845177c80747a2aabb607f9318f981993cc76`. The build below fetches that exact commit and stops if what it checked out is anything else (a fourth check, made by git itself, because a commit id is a hash of its content).

## Part 3 — Build it

OPA's own recipe (its `Makefile` at the tag) is: no C code, `-buildmode=exe`, the `opa_wasm` tag, a hostname stamp, inside the Go image above. The script below follows it and adds two things: `-trimpath` (so the result does not depend on where it was built) and a git tag (see "A trap" below). It keeps the recipe's hostname stamp with a fixed value; that stamp turns out to do nothing in this version (Part 4.3), and is kept only so that the checksums below can be reproduced. Save it as `$LAB/build-opa.sh`:

```bash
#!/bin/bash
# Runs inside the Go image. Builds OPA from the commit its release tag points at, with the library fix as the only change.
set -euo pipefail
COMMIT=05c845177c80747a2aabb607f9318f981993cc76      # the commit tag v1.21.4 points at
VERSION=1.21.4
OVERRIDES="${OVERRIDES-golang.org/x/net@v0.62.0}"     # the fix for CVE-2026-78661; empty means "build the tag untouched"
ARCH="${ARCH:-arm64}"; NAME="${NAME:-opa_linux_$ARCH}"
export GOTOOLCHAIN=local GOPROXY=https://proxy.golang.org CGO_ENABLED=0 GOOS=linux GOFLAGS=-buildmode=exe
mkdir /src && cd /src && git init -q
git fetch -q --depth 1 https://github.com/open-policy-agent/opa.git "$COMMIT"
git checkout -q --detach FETCH_HEAD
[ "$(git rev-parse HEAD)" = "$COMMIT" ] || { echo "WRONG COMMIT"; exit 3; }   # stop if GitHub ever handed us something else
git tag "v$VERSION"                                   # so Go stamps the real version, not v0.0.0-<date>-<commit>
[ -z "$OVERRIDES" ] || go get $OVERRIDES              # raises the library, and its siblings in the same repository
start=$(date +%s)
GOARCH="$ARCH" go build -trimpath -tags=opa_wasm -ldflags "-X github.com/open-policy-agent/opa/version.Hostname=kaval-build" -o "/out/$NAME" .
echo "built $NAME in $(( $(date +%s) - start ))s: $(stat -c %s /out/$NAME) bytes, sha256 $(sha256sum /out/$NAME | cut -d' ' -f1)"
go version -m "/out/$NAME" > "/out/$NAME.modules.txt"
```

```bash
docker run --rm --cpus 4 -v $LAB/out:/out -v $LAB/build-opa.sh:/build-opa.sh:ro $GO bash /build-opa.sh                  # the fixed build
docker run --rm --cpus 4 -e OVERRIDES= -e NAME=opa_linux_arm64_untouched -v $LAB/out:/out -v $LAB/build-opa.sh:/build-opa.sh:ro $GO bash /build-opa.sh   # the tag untouched
```

`--cpus 4` is about what a GitHub-hosted runner has. Measured on 2026-11-07 (Go 1.27.3, image digest above):

| Build (empty build cache, libraries already downloaded) | Time | Size | sha256 (first 16) |
|---|---|---|---|
| fixed, first build | 2 min 7 s | 60,298,924 bytes | 72f3485863ff50b0 |
| fixed, second build | 2 min 2 s | 60,298,924 bytes | 72f3485863ff50b0 |
| the tag untouched (with the git tag) | | 60,297,441 bytes | 64a0bd1533ed1a00 |
| the official release file | | 60,319,859 bytes | 810aa96e44f1f852 |

The two fixed builds are **byte-identical**. The very first build, which also downloaded every library, took 3 min 3 s.

**What changed.** `diff` of the library lists that `go version -m` prints for the official file, the untouched rebuild and the fixed rebuild:

```bash
docker run --rm -v $LAB/official:/o:ro $GO go version -m /o/opa_linux_arm64_static | awk '$1=="dep"{print $2,$3}' > $LAB/mods-official.txt
awk '$1=="dep"{print $2,$3}' $LAB/out/opa_linux_arm64_untouched.modules.txt > $LAB/mods-untouched.txt
awk '$1=="dep"{print $2,$3}' $LAB/out/opa_linux_arm64.modules.txt > $LAB/mods-fixed.txt
diff $LAB/mods-official.txt $LAB/mods-untouched.txt && echo "official and untouched rebuild: the same 94 libraries"
diff $LAB/mods-untouched.txt $LAB/mods-fixed.txt
```

The untouched rebuild has exactly the official file's 94 libraries. The fixed one differs in six, all from one Google repository: `golang.org/x/net` 0.60.0 to 0.62.0, and `crypto` 0.57.0 to 0.58.0, `sync` 0.23.0 to 0.24.0, `sys` 0.48.0 to 0.49.0, `term` 0.46.0 to 0.47.0, `text` 0.42.0 to 0.43.0 (`go get` raises a library's siblings together; `x/tools` also moves in `go.mod` but is not in the program).

**A trap.** The first rebuilds did not tag the checkout. With no tag at the commit, Go stamps the program `v0.0.0-20261023130056-05c845177c80`, a made-up version; the official file says `v1.21.4+dirty`. Scanners compare that number with "fixed in 1.4.0" and conclude OPA itself is old: Trivy reported three more findings (CVE-2022-28946, CVE-2022-33082, CVE-2025-46569) and `govulncheck` one (GO-2025-3660), none of them real. The `git tag` line removes all four. Check it with `go version -m ... | grep -w mod`.

## Part 4 — Test it

**4.1 The scanner, with no exception.** The same command as Part 1, on the fixed file. Run Part 1's scan of the official file in the same sitting: it must still fail, or a pass here proves nothing.

```bash
rm -rf $LAB/rootfs && mkdir -p $LAB/rootfs/usr/local/bin && cp $LAB/out/opa_linux_arm64 $LAB/rootfs/usr/local/bin/opa
docker run --rm -v $LAB/rootfs:/rootfs:ro -v $LAB/no-exceptions.yaml:/ig.yaml:ro aquasec/trivy:0.74.0 \
  rootfs --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --ignorefile /ig.yaml --exit-code 1 --no-progress /rootfs; echo "exit $?"
```

| File | Exception list | Result (2026-11-07) |
|---|---|---|
| the official release file | empty | exit 1: CVE-2026-78661, and nothing else |
| rebuild, one override (`x/net`) | empty | **exit 0** |
| the same, built a second time | empty | exit 0 |
| rebuild, two overrides (`x/net`, `klauspost/compress`) | empty | exit 0 |
| rebuild, one override | the file CI has today | exit 0 |
| the trap in Part 3: tag left off, no override | empty | exit 1: CVE-2022-28946, CVE-2022-33082, CVE-2025-46569 and CVE-2026-78661 |
| the trap, with the override | empty | exit 1: the three old ones |

**4.2 Go's checker.** The same `govulncheck` command as Part 1 (mount `$LAB/out` instead of `$LAB/official`), on the fixed file, then on a build whose override list also has the next fix:

```bash
docker run --rm --cpus 4 -e OVERRIDES="golang.org/x/net@v0.62.0 github.com/klauspost/compress@v1.20.2" -e NAME=opa_linux_arm64_two \
  -v $LAB/out:/out -v $LAB/build-opa.sh:/build-opa.sh:ro $GO bash /build-opa.sh
```

| File | `govulncheck -mode=binary` |
|---|---|
| the official release file | 1 affected: GO-2026-6865 (`klauspost/compress` 1.20.0) |
| rebuild, one override | the same 1 (the fake finding about OPA itself is gone with the tag) |
| rebuild, two overrides | **No vulnerabilities found** |

**4.3 The same program?** Both files report the same details, and both pass Kaval's 29 policy tests (an arm64 container; on an x86 laptop it is emulated and takes a few seconds):

```bash
docker run --rm --platform linux/arm64 -v $LAB/out:/o:ro -v "$PWD/policy:/policy:ro" debian:bookworm-slim \
  sh -c 'cp /o/opa_linux_arm64 /opa && chmod +x /opa && /opa version && /opa test /policy -v | tail -3'
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
...
PASS: 29/29
```

(The official file prints the same lines. `Build Hostname` is empty in both: the stamp copied from OPA's recipe does nothing in this version, so the implementation drops it.) Then ask both files the question Kaval asks, in the way `services/agent/kaval_agent/policy.py` asks it, over a grid of inputs and compare the raw answers. Save as `$LAB/compare_opa.py`:

```python
"""compare_opa.py OPA_A OPA_B POLICY_DIR EVERY_NTH: do two OPA files answer Kaval's real question identically?"""
import itertools, json, os, shutil, subprocess, sys, tempfile
a, b, policy, nth = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
work = tempfile.mkdtemp()
def stage(src, name):
    dst = os.path.join(work, name); shutil.copy(src, dst); os.chmod(dst, 0o755); return dst
A, B = stage(a, "a"), stage(b, "b")
TYPES = ["restart_pod", "delete_pvc", "scale_deployment", "remove_persistentvolumeclaim", "terminate_EC2_instance",
         "rollout_restart", "namespace_cleanup", "billing_adjust", "noop", ""]
RADII = ["pod", "deployment", "namespace", "node", "cluster", "account"]
CAUSES = [None, "oom_killed", "disk_pressure", "kaval_agent_down", "latency_high"]
def grid():
    for i, (t, r, c, rev, cause) in enumerate(itertools.product(TYPES, RADII, [0.0, 0.5, 0.9, 0.92, 1.0], [True, False], CAUSES)):
        if i % nth == 0:
            doc = {"action": {"type": t, "blast_radius": r, "reversible": rev}, "confidence": c}
            if cause:
                doc["cause"] = cause
            yield doc
def ask(opa, pdir, doc):  # the exact call kaval_agent/policy.py makes
    p = subprocess.run([opa, "eval", "--format=json", "-I", "-d", pdir, "data.policy.decision"],
                       input=json.dumps(doc), capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr
def compare(label, x, dx, y, dy):
    docs = list(grid()); same = sum(ask(x, dx, d) == ask(y, dy, d) for d in docs)
    print(f"{label}: {len(docs)} questions, identical answers {same}"); return len(docs), same
n1, s1 = compare("empty promotions (as in production)", A, policy, B, policy)
promo = os.path.join(work, "p"); shutil.copytree(policy, promo)   # one promotion, so that `auto` can happen at all
json.dump({"auto_promotions": [{"action_type": "restart_pod", "blast_radius": "pod", "requires_reversible": True,
                                "min_confidence": 0.9}]}, open(os.path.join(promo, "promotions.json"), "w"))
n2, s2 = compare("one promotion added", A, promo, B, promo)
nc, sc = compare("CONTROL: the same file, different data (must differ)", A, policy, A, promo)
sys.exit(0 if (s1 == n1 and s2 == n2 and sc < nc) else 1)
```

```bash
docker run --rm --platform linux/amd64 -v $LAB/official-amd64:/o:ro -v $LAB/out:/n:ro -v "$PWD/policy:/policy:ro" -v $LAB/compare_opa.py:/t.py:ro \
  python:3.11-slim-bookworm python /t.py /o/opa_linux_amd64_static /n/opa_linux_amd64 /policy 3
```

(Build the amd64 file first with `-e ARCH=amd64`, and download `opa_linux_amd64_static` the way Part 1 did.) The control is what makes "no differences" mean something: the same file asked against two different data sets must differ, and it did. On 2026-11-07 the amd64 comparison (every third input, 1,000 questions) found 0 differences with the real data and 0 with a promotion added, and the control differed on exactly the 1 question where a promotion makes the answer `auto`. The arm64 files were compared under emulation (an x86 laptop) with every `auto`-capable input forced into the sample, 308 questions: 0 differences with the real data and 0 with a promotion added (4 `auto` answers), for the rebuild with one override and again for the one with two; the control differed on exactly those 4. Choose `EVERY_NTH` so that at least one input where the policy can answer `auto` is sampled (3 does; every 60th does not, and then the control cannot fire and the script exits 1, which is the guard working: the first emulated run of this trial used 60 and had to be redone).

**4.4 OPA's own tests.** None of OPA's own code imports `golang.org/x/net` directly (it comes in through other libraries), so the bump can only change what those libraries do. Run OPA's whole unit-test suite (its slow tests left out) on the fixed tree, in the same Go image, with the same steps as Part 3 (the trial ran them from a script; this is the same commands in one line):

```bash
docker run --rm --cpus 4 --memory 6g $GO bash -c '
  export GOTOOLCHAIN=local GOFLAGS=-buildmode=exe CGO_ENABLED=0
  git init -q /src && cd /src && git fetch -q --depth 1 https://github.com/open-policy-agent/opa.git 05c845177c80747a2aabb607f9318f981993cc76
  git checkout -q --detach FETCH_HEAD && git tag v1.21.4 && go get golang.org/x/net@v0.62.0
  go test -tags=opa_wasm -count=1 -p 2 -timeout 40m ./... 2>&1 | grep -E "^(ok|FAIL|--- FAIL)" | sort | uniq -c | sort -rn | head'
```

It takes about 12 minutes on 4 CPUs. On 2026-11-07: 129 packages passed, 128 have no tests, and **one test failed once**, `TestActivateV1Bundles` in `v1/sdk` (`context deadline exceeded`, a timing limit, on a machine busy with other tests). To tell a flaky test from a broken library, run that one test ten times in a row on the untouched tag and on the fixed tree (`go test -tags=opa_wasm -count=10 -p 1 -run '^TestActivateV1Bundles$' ./v1/sdk/`): it failed once on the **untouched** tag and not at all on the fixed tree, and the whole package passed three times on both. A flaky test, not an effect of the bump.

## Part 5 — Build it the way CI will: a Dockerfile stage

The shape the two service Dockerfiles would get in place of the download stage (a draft, kept out of the repository until the decision is made). It has the same inputs as the script and two more checks, each in its own step so that changing an expected value does not recompile:

```dockerfile
# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM golang:1.27.3-trixie@sha256:af1bfc2614ebb1dbd31af514b78740fd678850e76498762b425ecee4c5c6ea80 AS opa
ARG TARGETARCH
ARG OPA_VERSION=1.21.4
ARG OPA_COMMIT=05c845177c80747a2aabb607f9318f981993cc76
ARG OPA_GO_GET="golang.org/x/net@v0.62.0"
ENV GOTOOLCHAIN=local GOPROXY=https://proxy.golang.org CGO_ENABLED=0 GOOS=linux GOFLAGS=-buildmode=exe
WORKDIR /src
RUN set -eu; \
    git init -q; \
    git fetch -q --depth 1 https://github.com/open-policy-agent/opa.git "${OPA_COMMIT}"; \
    git checkout -q --detach FETCH_HEAD; \
    test "$(git rev-parse HEAD)" = "${OPA_COMMIT}"; \
    git tag "v${OPA_VERSION}"; \
    go get ${OPA_GO_GET}; \
    GOARCH="${TARGETARCH}" go build -trimpath -tags=opa_wasm \
      -ldflags "-X github.com/open-policy-agent/opa/version.Hostname=kaval-build" -o /opa .
ARG OPA_SHA256_ARM64=72f3485863ff50b07c50197d464a8ed95da59e0cc09c5a42e819e4e2824eed9b
ARG OPA_SHA256_AMD64=f3b773388c99a6d2d7c4ded08dff2a771108864b03951f9d5d1f752d024fcaab
RUN set -eu; \
    for spec in ${OPA_GO_GET}; do \
      mod="${spec%@*}"; ver="${spec#*@}"; \
      go version -m /opa | grep -Eq "[[:space:]]${mod}[[:space:]]+${ver}[[:space:]]" || { echo "override not applied: ${spec}"; exit 1; }; \
    done; \
    case "${TARGETARCH}" in arm64) want="${OPA_SHA256_ARM64}";; amd64) want="${OPA_SHA256_AMD64}";; *) echo "unsupported arch"; exit 1;; esac; \
    echo "${want}  /opa" | sha256sum -c -

FROM scratch AS export
COPY --from=opa /opa /opa
```

(`export` exists only so the lab can pull the file out; in a service Dockerfile the runtime stage would `COPY --from=opa /opa /usr/local/bin/opa`, exactly as it copies the download today.) Save it as `$LAB/ctx/Dockerfile` and build:

```bash
docker buildx build --no-cache --platform linux/arm64 --target export -o type=local,dest=$LAB/export-arm64 $LAB/ctx
sha256sum $LAB/export-arm64/opa          # 72f34858...: the same bytes the script made
```

On 2026-11-07 the arm64 build took 2 min 46 s on a 12-CPU laptop and the file was **identical** to the script's, down to the checksum, although it was made by a different route. The amd64 stage (`--platform linux/amd64`, 2 min 17 s) matched the script's amd64 file (`f3b77338...`) the same way. A wrong expected checksum (`--build-arg OPA_SHA256_ARM64=000...0`) stops the build and produces no file: the log says `sha256sum: WARNING: 1 computed checksum did NOT match` and `/opa: FAILED`, and it does so in under a second, because the compiled layer is reused. That is the point of keeping the check in its own step.

## Part 6 — What it showed, and clearing up

| Question | Answer |
|---|---|
| Is the source what OPA released? | Yes: commit `05c8451`, agreed by three sources, and the build stops if it checked out anything else |
| Is the rebuild the same program? | The same 94 libraries, build settings and size within 0.04%; same details; 29 of 29 policy tests; identical answers to 1,000 policy questions on amd64 and 308 on arm64 |
| Is the scanner satisfied with no exception? | Yes, with one library raised (and the version tag in place) |
| Can it be built again to the byte? | Yes: twice from empty caches, once more by this lab's own script in a fresh container, and through `docker buildx` (arm64 and amd64) |
| What does it cost? | About 2 minutes a build on 4 CPUs (3 min 3 s from fully cold, 2 min 46 s through buildx); no AWS cost; the same 60.3 MB |
| What would come next? | `govulncheck` already flags `klauspost/compress`; one more override in the same recipe cleared it |

The decision, and the plan for each answer, are in [ADR-0079](../adr/0079-where-the-opa-binary-comes-from.md). Clear up (Docker only; nothing in the repository or AWS was touched):

```bash
rm -rf $LAB
docker image rm golang:1.27.3-trixie aquasec/trivy:0.74.0 debian:bookworm-slim   # about 2.5 GB; they are pulled again when needed
```
