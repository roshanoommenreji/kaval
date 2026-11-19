"""The weekly image watch: is anything production runs, or the next release would ship, newly known
to be vulnerable? (KAV-167, ADR-0106)

Run weekly by `.github/workflows/image-watch.yml`, from GitHub and with no AWS login at all.

Why it exists. Trivy scans an image when someone changes something (ci.yml, release.yml). A flaw
that is announced AFTER a release is therefore not seen until the next change, and Kaval is parked
most of the time. This asks the same question on a schedule, against today's vulnerability list.

What it scans, and why that is enough. Every Kaval image is made from three things that are in git:
a base image pinned by digest (the LAST `FROM` of its Dockerfile is what ships; the earlier ones are
build stages that are thrown away), the locked Python libraries (`uv.lock`), and Kaval's own code
(CodeQL's job). So it scans, for `main` (what the next release would ship) and for each commit
production is pinned to (what the next production start-up would run):

  - `uv.lock` with `trivy fs`;
  - the shipped base image of each service, and every third-party image the chart names, with
    `trivy image` (pulled from public registries; no AWS).

It cannot see what the Dockerfiles install themselves (the build's `apt` steps) or the Helm charts
Flux installs (Kyverno, and so on); ADR-0106 lists both as known gaps. The images are not pulled
from ECR on purpose: that would need a second CI login into AWS (the reason KAV-165 was parked).

Same bar as CI: HIGH and CRITICAL, with a fix available, minus the dated exceptions in
`.github/trivy-ignore.yaml`.

A scan that finds nothing because it scanned nothing must not pass as clean, so each target has a
minimum number of packages it must have read; fewer is "the check broke", not "all clear".

The decisions are pure functions with tests (test_image_watch.py); `main` only runs Trivy, prints.
Exit status: 0 clean, 1 findings to tell a person about, anything else the check itself broke.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

# Fewer than this and the scan did not really look. "plant" is the three-library test file.
MIN_PACKAGES = {"lock": 20, "image": 10, "base": 10, "plant": 3}
SEVERITIES = "HIGH,CRITICAL"
MAX_REPORT = 20000                         # characters; an issue body holds 65k, so stay well under

# A tag like `sha-f280013`. It is the short commit the release was built from (ADR-0035).
_TAG = re.compile(r"^\s*tag:\s*(sha-[0-9a-f]{7,40})\s*(?:#.*)?$", re.MULTILINE)
# A whole-image line such as `image: pgvector/pgvector:pg16` (an `image:` opening a block is not).
_IMAGE = re.compile(r"^\s*image:\s+([^\s#]+)\s*(?:#.*)?$", re.MULTILINE)
_FROM = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+\S+)?\s*$",
                   re.MULTILINE | re.IGNORECASE)


# -- reading the repository --------------------------------------------------------------------

def pinned_tags(values_text: str) -> list[str]:
    """The distinct `sha-...` tags an environment's values file pins, in order."""
    return sorted(set(_TAG.findall(values_text)))


def shipped_base_image(dockerfile_text: str) -> str:
    """The base image a Dockerfile's final stage starts from, which is what ends up in the image."""
    refs = _FROM.findall(dockerfile_text)
    if not refs:
        raise ValueError("no FROM line found")
    return str(refs[-1])


def third_party_images(values_text: str) -> list[str]:
    """Whole-image references in a chart's values (Kaval's own are a repository and a tag)."""
    out = []
    for ref in _IMAGE.findall(values_text):
        ref = ref.strip("\"'")
        if (":" in ref or "@" in ref) and not ref.startswith("kaval/"):
            out.append(ref)
    return sorted(set(out))


# -- reading Trivy's answer --------------------------------------------------------------------

@dataclass(frozen=True)
class Finding:
    vuln_id: str
    package: str
    installed: str
    fixed: str
    severity: str


@dataclass
class Result:
    target: str          # what was scanned, for a person to read ("main: uv.lock")
    kind: str            # "lock", "image" (third party), "base" (a Dockerfile's) or "plant"
    packages: int
    findings: list[Finding] = field(default_factory=list)
    error: str = ""      # set when this target could not be scanned at all


def summarize(target: str, kind: str, trivy_json: Mapping[str, object]) -> Result:
    """Count what Trivy read and keep the fixable HIGH/CRITICAL findings."""
    packages = 0
    findings: list[Finding] = []
    for res in trivy_json.get("Results") or []:  # type: ignore[attr-defined]
        packages += len(res.get("Packages") or [])
        for v in res.get("Vulnerabilities") or []:
            findings.append(Finding(
                vuln_id=str(v.get("VulnerabilityID", "?")),
                package=str(v.get("PkgName", "?")),
                installed=str(v.get("InstalledVersion", "?")),
                fixed=str(v.get("FixedVersion", "")),
                severity=str(v.get("Severity", "?")),
            ))
    return Result(target=target, kind=kind, packages=packages, findings=findings)


def _group(r: Result) -> list[str]:
    """One line per package, not per vulnerability: a Go program built with an old standard library
    has dozens, and a list of fifty lines in an issue is a list nobody reads."""
    groups: dict[tuple[str, str], list[Finding]] = {}
    for f in r.findings:
        groups.setdefault((f.package, f.installed), []).append(f)
    lines = []
    for (package, installed), fs in groups.items():
        if len(fs) == 1:
            f = fs[0]
            lines.append(f"{r.target}: {f.severity} {f.vuln_id} in {package} {installed}"
                         + (f" (fixed in {f.fixed})" if f.fixed else ""))
            continue
        worst = "CRITICAL" if any(f.severity == "CRITICAL" for f in fs) else "HIGH"
        fixes = list(dict.fromkeys(v.strip() for f in fs for v in f.fixed.split(",") if v.strip()))
        ids = ", ".join(f.vuln_id for f in fs[:3]) + (", ..." if len(fs) > 3 else "")
        lines.append(f"{r.target}: {len(fs)} problems in {package} {installed}, worst {worst} "
                     f"({ids})" + (f"; fixes in {', '.join(fixes[:4])}" if fixes else ""))
    return lines


def verdict(results: Sequence[Result]) -> tuple[str, list[str]]:
    """("clean" | "found" | "broken", lines for a person). Broken beats found: an incomplete look
    must be said out loud even when it also found something."""
    broken = [
        f"{r.target}: could not be scanned ({r.error})" if r.error else
        f"{r.target}: read only {r.packages} packages (needs at least {MIN_PACKAGES[r.kind]}), "
        "so this is not an all-clear"
        for r in results if r.error or r.packages < MIN_PACKAGES[r.kind]
    ]
    if not results:
        broken.append("nothing was scanned")
    found = [line for r in results for line in _group(r)]
    if broken:
        return "broken", broken + found
    return ("found", found) if found else ("clean", [])


def render(status: str, lines: Sequence[str]) -> str:
    """The text the workflow puts in the GitHub issue; the lines are CVE ids and package names."""
    if status == "clean":
        return "The weekly image watch found nothing new."
    head = {
        "found": "The weekly image watch found known problems that have a fix:",
        "broken": "The weekly image watch could not look properly, so nobody knows if all is well:",
    }[status]
    body = "\n".join(f"- {line}" for line in lines)
    text = f"{head}\n\n{body}"
    if len(text) > MAX_REPORT:
        text = text[:MAX_REPORT] + "\n\n(cut short; the full list is in the run log)"
    return text


# -- running Trivy ------------------------------------------------------------------------------

def _trivy(args: Sequence[str]) -> dict[str, object]:
    cmd = ["trivy", *args, "--format", "json", "--quiet", "--skip-version-check"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1500, check=False)
    if proc.returncode != 0:
        tail = proc.stderr.strip()[-400:]
        raise RuntimeError(f"trivy {args[0]} failed ({proc.returncode}): {tail}")
    out: dict[str, object] = json.loads(proc.stdout)
    return out


# 20 minutes: Trivy's default of 5 is not enough for the language-model image, which holds several
# gigabytes of GPU libraries (found by the first real run, 2026-11-19).
_COMMON = ["--scanners", "vuln", "--severity", SEVERITIES, "--ignore-unfixed", "--list-all-pkgs",
           "--timeout", "20m"]
# Those libraries are copied in, not installed by the package manager, so there is nothing in them
# for a vulnerability list to match; skipping the folder saves most of the time.
_IMAGE_SKIP = ["--skip-dirs", "usr/lib/ollama"]


def scan_lock(label: str, directory: Path, ignorefile: Path) -> Result:
    data = _trivy(["fs", *_COMMON, "--ignorefile", str(ignorefile), str(directory)])
    return summarize(f"{label}: uv.lock", "lock", data)


def scan_image(label: str, ref: str, ignorefile: Path, base: bool = False) -> Result:
    """A third-party image is read whole. A Dockerfile's base image is read for its operating-system
    packages only: its Python tools (pip, setuptools, wheel) are removed from Kaval's finished
    images (the last stage of each Dockerfile), and the libraries that stay are in uv.lock,
    which is scanned separately."""
    scope = ["--pkg-types", "os"] if base else []
    data = _trivy(["image", *_COMMON, *_IMAGE_SKIP, *scope, "--ignorefile", str(ignorefile), ref])
    return summarize(f"{label}: {short(ref)}", "base" if base else "image", data)


def short(ref: str) -> str:
    """An image reference with its digest cut to 12 characters, so an issue line stays readable."""
    head, sep, digest = ref.partition("@sha256:")
    return f"{head}@{digest[:12]}" if sep else ref


def _name(kind: str, label: str, arg: str) -> str:
    """A target as a person reads it: "main: uv.lock", "main: python:3.11-slim-bookworm@…"."""
    return f"{label}: " + {"lock": "uv.lock", "plant": "requirements.txt"}.get(kind, short(arg))


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout


def _show(commit: str, path: str, cwd: Path) -> str:
    return _git("show", f"{commit}:{path}", cwd=cwd)


def _targets_for(label: str, commit: str, root: Path, work: Path) -> list[tuple[str, str, object]]:
    """What to scan at one commit: the lock file and the shipped images, as (kind, label, arg)."""
    folder = work / label.replace(" ", "_")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "uv.lock").write_text(_show(commit, "uv.lock", root), encoding="utf-8")
    bases: set[str] = set()
    for line in _git("ls-tree", "-r", "--name-only", commit, "services", cwd=root).splitlines():
        if line.endswith("/Dockerfile"):
            bases.add(shipped_base_image(_show(commit, line, root)))
    others = set(third_party_images(_show(commit, "deploy/charts/kaval/values.yaml", root)))
    return [("lock", label, folder),
            *(("base", label, ref) for ref in sorted(bases)),
            *(("image", label, ref) for ref in sorted(others - bases))]


def main(argv: Sequence[str]) -> int:
    root = Path(_git("rev-parse", "--show-toplevel", cwd=Path.cwd()).strip())
    ignorefile = root / ".github" / "trivy-ignore.yaml"
    plant = "--plant" in argv   # a deliberately old library, to prove the watch can find something
    report_to = Path(argv[argv.index("--report") + 1]) if "--report" in argv else None
    jobs: list[tuple[str, str, object]] = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        jobs += _targets_for("main", "HEAD", root, work)
        prod_values = (root / "deploy/environments/prod/values.yaml").read_text(encoding="utf-8")
        for tag in pinned_tags(prod_values):
            commit = _git("rev-parse", "--verify", f"{tag[4:]}^{{commit}}", cwd=root).strip()
            jobs += _targets_for(f"production pin {tag}", commit, root, work)
        if plant:
            planted = work / "planted"
            planted.mkdir()
            # Built here and never committed: a vulnerable file in git would raise real alerts.
            (planted / "requirements.txt").write_text(
                "requests==2.19.0\nurllib3==1.24\nPyYAML==5.3\n", encoding="utf-8")
            jobs.append(("plant", "planted test file", planted))

        results: list[Result] = []
        seen: set[tuple[str, str]] = set()
        for kind, label, arg in jobs:
            if kind in ("image", "base"):
                if (kind, str(arg)) in seen:
                    continue            # same image at several commits: look once
                seen.add((kind, str(arg)))
            name = _name(kind, label, str(arg))
            try:
                if kind == "lock":
                    result = scan_lock(label, Path(str(arg)), ignorefile)
                elif kind == "plant":
                    data = _trivy(["fs", *_COMMON, "--ignorefile", str(ignorefile), str(arg)])
                    result = summarize(name, "plant", data)
                else:
                    result = scan_image(label, str(arg), ignorefile, base=kind == "base")
            except (RuntimeError, ValueError, subprocess.TimeoutExpired) as err:
                # One target that cannot be scanned is reported as such; the others still run.
                result = Result(target=name, kind=kind, packages=0, error=str(err)[:300])
            results.append(result)
            note = f", ERROR {result.error}" if result.error else ""
            print(f"scanned {result.target}: {result.packages} packages, "
                  f"{len(result.findings)} findings{note}", flush=True)
            for f in result.findings:    # the log has the whole list; the issue has the grouped one
                print(f"  {f.severity} {f.vuln_id} {f.package} {f.installed} fixed in {f.fixed}")

    status, lines = verdict(results)
    print(f"STATUS: {status}")
    for line in lines:
        print(f"PROBLEM: {line}")
    if report_to is not None:
        report_to.write_text(render(status, lines), encoding="utf-8")
    return {"clean": 0, "found": 1, "broken": 2}[status]


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as err:  # the check itself broke: never read as "all clear"
        print(f"STATUS: broken\nPROBLEM: the check could not run: {err}")
        if "--report" in sys.argv:
            Path(sys.argv[sys.argv.index("--report") + 1]).write_text(
                render("broken", [f"the check could not run: {err}"]), encoding="utf-8")
        sys.exit(2)
