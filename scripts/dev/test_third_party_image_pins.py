"""The third-party images are pinned by digest and every place that names one agrees (KAV-169).

The weekly image watch (KAV-167) found Prometheus, Ollama and pgvector with known problems
because they were named in several places and nothing moved them together. Until a bot opens
the pull requests (KAV-170), this test stops one place being updated and the others forgotten.

  * the database image: chart, compose, CI, the database server's start-up script and the two
    scripts that start a throwaway copy all name the SAME build, pinned by digest, PostgreSQL 16
    on Debian bookworm (a newer Debian changes the text sort order and can corrupt indexes)
  * Ollama and Prometheus: pinned by digest; compose and the chart agree on Ollama
  * db-image-refresh-remote.sh rewrites a server's unit file to the pin, and only to a pinned
    pg16 image
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PG = re.compile(r"pgvector/pgvector:[0-9.]+-pg16-bookworm@sha256:[0-9a-f]{64}")
OLLAMA = re.compile(r"ollama/ollama:[0-9.]+@sha256:[0-9a-f]{64}")
PROM = re.compile(r"quay\.io/prometheus/prometheus:v[0-9.]+@sha256:[0-9a-f]{64}")


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_the_database_image_is_one_pinned_build_everywhere() -> None:
    places = [
        "deploy/charts/kaval/values.yaml",
        "compose.yaml",
        ".github/workflows/ci.yml",
        "infra/modules/database/user_data.sh.tftpl",
        "scripts/ops/restore-drill.sh",
        "scripts/ops/seed-refresh.sh",
    ]
    found = {p: PG.findall(_text(p)) for p in places}
    for place, refs in found.items():
        assert refs, f"{place} does not name the pinned database image"
    assert len({r for refs in found.values() for r in refs}) == 1, f"the places disagree: {found}"


def test_no_place_still_floats_the_database_tag() -> None:
    for place in ("deploy/charts/kaval/values.yaml", "compose.yaml", ".github/workflows/ci.yml",
                  "infra/modules/database/user_data.sh.tftpl"):
        assert not re.search(r"pgvector/pgvector:pg16(?![\w.-])", _text(place)), place


def test_ollama_is_pinned_by_digest_and_compose_agrees_with_the_chart() -> None:
    chart = set(OLLAMA.findall(_text("deploy/charts/kaval/values.yaml")))
    compose = set(OLLAMA.findall(_text("compose.yaml")))
    assert len(chart) == 1 and chart == compose, (chart, compose)
    assert not re.search(r"ollama/ollama:[0-9.]+(?!\S*@sha256)\s", _text("compose.yaml"))


def test_prometheus_is_pinned_by_digest() -> None:
    assert len(set(PROM.findall(_text("deploy/charts/kaval/values.yaml")))) == 1


def test_every_exception_ends_and_is_scoped() -> None:
    import yaml

    entries = yaml.safe_load(_text(".github/trivy-ignore.yaml"))["vulnerabilities"] or []
    for e in entries:
        assert e.get("expired_at"), f"{e['id']} has no end date"
        assert e.get("paths") or e.get("purls"), f"{e['id']} is not scoped to a file or package"
        assert e.get("statement"), f"{e['id']} has no reason"


bash = shutil.which("bash")


@pytest.mark.skipif(bash is None, reason="needs bash")
def test_the_refresh_script_rewrites_a_unit_to_the_pin_and_is_idempotent(tmp_path: Path) -> None:
    pin = PG.findall(_text("infra/modules/database/user_data.sh.tftpl"))[0]
    unit = tmp_path / "kaval-postgres.service"
    unit.write_text("ExecStart=/usr/bin/docker run --rm --name postgres \\n  -p 5432:5432 \\n"
                    "  pgvector/pgvector:pg16 \\n  -c ssl=on\n", encoding="utf-8")
    script = str(ROOT / "scripts/ops/db-image-refresh-remote.sh")

    def run() -> str:
        out = subprocess.run([bash or "bash", script, "--rewrite-only", str(unit), pin],
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()

    assert run() == "changed pgvector/pgvector:pg16"
    text = unit.read_text(encoding="utf-8")
    assert pin in text and "-c ssl=on" in text
    assert run() == "unchanged"


@pytest.mark.skipif(bash is None, reason="needs bash")
def test_the_refresh_script_refuses_an_image_that_is_not_a_pinned_pg16() -> None:
    script = str(ROOT / "scripts/ops/db-image-refresh-remote.sh")
    wrong_major = "pgvector/pgvector:0.8.7-pg17-bookworm@sha256:" + "a" * 64
    for bad in ("pgvector/pgvector:pg16", wrong_major, "postgres:16"):
        out = subprocess.run([bash or "bash", script], capture_output=True, text=True,
                             env={"PIN": bad, "PATH": "/usr/bin:/bin"}, check=False)
        assert out.returncode == 2 and "refusing" in out.stderr, bad
