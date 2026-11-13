"""Every tool the build robot downloads is fetched with retries and checked against a checksum.

(KAV-133.) On 8 November 2026 seven CI jobs went red only because GitHub's download servers
answered 503 or 504 or dropped the connection while the robot fetched Trivy, gitleaks, OPA, helm
or kubeconform; each one needed a manual re-run. A retry is safe because the checksum check after
the download is the real gate: a half-fetched or swapped file still fails it. These tests keep
both halves in place, so neither the retry nor the checksum can be dropped quietly later.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = [
    ROOT / ".github" / "workflows" / "ci.yml",
    ROOT / ".github" / "workflows" / "release.yml",
    ROOT / ".github" / "workflows" / "promote.yml",
]

# Trivy twice (ci.yml's image job and release.yml), gitleaks, OPA, helm, kubeconform, and cosign
# (release.yml, KAV-138; promote.yml, KAV-139).
EXPECTED_DOWNLOADS = 8


def _download_steps() -> list[tuple[str, str, str]]:
    """(file, step name, script) for every workflow step whose script runs curl."""
    found: list[tuple[str, str, str]] = []
    for path in WORKFLOWS:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in doc["jobs"].values():
            for step in job.get("steps", []):
                script = step.get("run", "")
                if "curl " in script:
                    found.append((path.name, step.get("name", "?"), script))
    return found


def test_all_the_expected_downloads_are_found() -> None:
    # If the layout changes and this finds nothing, the other tests would pass for no reason.
    assert len(_download_steps()) == EXPECTED_DOWNLOADS


def _ids() -> list[str]:
    return [
        f"{file}-{name.split(' (')[0].replace(' ', '_')}" for file, name, _ in _download_steps()
    ]


@pytest.mark.parametrize(("file", "name", "script"), _download_steps(), ids=_ids())
def test_a_download_retries_on_any_error(file: str, name: str, script: str) -> None:
    assert "--retry 5" in script, f"{file}: '{name}' does not retry"
    assert "--retry-all-errors" in script, (
        f"{file}: '{name}' retries only some errors (a 503 is not one of them)"
    )
    assert "-f" in script.split("curl", 1)[1].split()[0], (
        f"{file}: '{name}' would save an error page as the tool"
    )


@pytest.mark.parametrize(("file", "name", "script"), _download_steps(), ids=_ids())
def test_a_download_is_still_checked_against_its_pinned_checksum(
    file: str, name: str, script: str
) -> None:
    assert "sha256sum --check --strict" in script, f"{file}: '{name}' no longer checks the checksum"
