"""The frozen list really lets a listed release through, run by Kyverno itself (KAV-146).

`test_image_policy.py` pins what the policy file says. This runs the file through Kyverno's own
command-line tool (the same 1.20.0 the cluster runs, in Docker) against pods, because the thing
that went wrong on production on 2026-11-13 was not in what the file said but in what Kyverno
does with it: a listed release was refused with "failed signature or attestation verification"
even though the rule let it through, since Kyverno refuses any image whose signature check ran and
failed. Only Kyverno can say whether that happens, so only Kyverno is asked.

It needs Docker and the internet (the tool's image is pulled from ghcr.io, and Kyverno looks for a
signature on a real public image so that the check really runs and really fails), so it does not
run by default and CI skips it. Run it by hand before changing the policy, and after bumping
Kyverno:

    KAVAL_KYVERNO_CLI=1 python -m pytest scripts/dev/test_image_policy_kyverno.py -v

The real policy file is used, rewritten in only two ways: the registry placeholder points at
ghcr.io/kyverno (so the test images are real and public, and unsigned by Kaval), and the frozen
list holds one tag that image really has. Nothing else is touched, so a change to the rule or the
list's shape is what these cases exercise.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
POLICY_FILE = ROOT / "deploy" / "image-policy" / "image-validating-policy.yaml"

# Kyverno's own tool, the version the chart installs (see test_kyverno_chart_is_an_exact_version).
CLI = "ghcr.io/kyverno/kyverno-cli:v1.20.0"
# A public image Kaval did not sign: Kyverno finds no signature from release.yml on main on it.
# Its own digest stands for "the digest Kyverno adds to a tag".
REPO = "ghcr.io/kyverno/kyverno-cli"
DIGEST = "sha256:bd2b8b7c9d550ecf04e0bacaf351adce2f82ce67fd3f291075edcee7f8ab4ab4"
LISTED, UNLISTED = "v1.20.0", "v1.19.0"

pytestmark = pytest.mark.skipif(
    os.environ.get("KAVAL_KYVERNO_CLI") != "1" or shutil.which("docker") is None,
    reason="runs Kyverno's tool in Docker with the internet: set KAVAL_KYVERNO_CLI=1 to run it",
)


def _pod(name: str, *images: str, init: str | None = None) -> str:
    init_block = f"  initContainers:\n  - name: i\n    image: {init}\n" if init else ""
    containers = "".join(f"  - name: c{n}\n    image: {img}\n" for n, img in enumerate(images))
    return (
        f"apiVersion: v1\nkind: Pod\nmetadata: {{name: {name}, namespace: default}}\n"
        f"spec:\n{init_block}  containers:\n{containers}"
    )


def _policy_for_the_test() -> str:
    text = POLICY_FILE.read_text(encoding="utf-8")
    text = text.replace("${registry}/kaval/", "ghcr.io/kyverno/").replace(
        "${kaval_namespace}", "default"
    )
    # The list: swap the real tags for the one tag the public image has, nothing else.
    swapped, count = re.subn(
        r"(\[\n)(?:\s+'sha-[0-9a-f]+',\n)+(\s+\])", rf"\1        '{LISTED}',\n\2", text
    )
    assert count == 1, "the frozen list is no longer a list of 'sha-...' tags in the match rule"
    return swapped


def _refused(tmp_path: Path, pods: dict[str, str]) -> set[str]:
    (tmp_path / "policy.yaml").write_text(_policy_for_the_test(), encoding="utf-8")
    args = ["docker", "run", "--rm", "-v", f"{tmp_path}:/w", CLI, "apply", "/w/policy.yaml"]
    for name, manifest in pods.items():
        (tmp_path / f"{name}.yaml").write_text(manifest, encoding="utf-8")
        args += ["--resource", f"/w/{name}.yaml"]
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}  # Git Bash on Windows must not rewrite /w/...
    out = subprocess.run(args, capture_output=True, text=True, timeout=300, env=env)
    text = out.stdout + out.stderr
    assert "pass:" in text, f"Kyverno's tool did not run: {text[-600:]}"
    return set(re.findall(r"resource default/Pod/([\w-]+) failed", text))


def test_what_kyverno_does_with_the_frozen_list(tmp_path: Path) -> None:
    pods = {
        # Let in: listed, as Kyverno sees it after it adds the digest, and as the pod is written.
        "listed-with-digest": _pod("listed-with-digest", f"{REPO}:{LISTED}@{DIGEST}"),
        "listed-tag-only": _pod("listed-tag-only", f"{REPO}:{LISTED}"),
        # Let in: a listed release beside an image that is not Kaval's at all.
        "listed-beside-outsider": _pod(
            "listed-beside-outsider",
            f"{REPO}:{LISTED}@{DIGEST}",
            init="docker.io/library/busybox:1.36",
        ),
        # Refused: unsigned and not listed, alone and beside a listed one, in an init container.
        "unlisted": _pod("unlisted", f"{REPO}:{UNLISTED}@{DIGEST}"),
        "listed-beside-unlisted": _pod(
            "listed-beside-unlisted",
            f"{REPO}:{LISTED}@{DIGEST}",
            init=f"{REPO}:{UNLISTED}@{DIGEST}",
        ),
        # Refused: no tag at all, so nothing on the list names it.
        "bare-digest": _pod("bare-digest", f"{REPO}@{DIGEST}"),
    }
    refused = _refused(tmp_path, pods)
    assert refused == {"unlisted", "listed-beside-unlisted", "bare-digest"}, (
        f"Kyverno refused {sorted(refused)}; the listed releases must pass, the unlisted must not"
    )
