"""The step between a bot's pull request and green: copy the manifest's pins everywhere.

KAV-170. Everything here runs on a small throwaway tree, so it proves the script's
behaviour (rewrites every place, leaves line endings alone, is idempotent, refuses what it
must) without touching the real files. The real files are checked by
test_third_party_image_pins.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sync_third_party_images as sync
import yaml

ROOT = Path(__file__).resolve().parents[2]
D1, D2 = "a" * 64, "b" * 64
OLD = {
    "database": f"pgvector/pgvector:0.8.6-pg16-bookworm@sha256:{D1}",
    "ollama": f"ollama/ollama:0.40.15@sha256:{D1}",
    "prometheus": f"quay.io/prometheus/prometheus:v3.15.0@sha256:{D1}",
}
NEW = {
    "database": f"pgvector/pgvector:0.8.7-pg16-bookworm@sha256:{D2}",
    "ollama": f"ollama/ollama:0.40.16@sha256:{D2}",
    "prometheus": f"quay.io/prometheus/prometheus:v3.16.0@sha256:{D2}",
}


def manifest(pins: dict[str, str]) -> str:
    return "# never built\n" + "".join(f"FROM {ref} AS {stage}\n" for stage, ref in pins.items())


def build_tree(root: Path, pins: dict[str, str], crlf: bool = False) -> None:
    """Every file the script rewrites, each naming its images with `pins`, among other text."""
    files: dict[str, list[str]] = {}
    for image in sync.IMAGES:
        for rel in image.files:
            files.setdefault(rel, []).append(pins[image.stage])
    for rel, refs in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "".join(f"  image: {ref}  # keep this comment\nunrelated: line\n" for ref in refs)
        path.write_bytes((body.replace("\n", "\r\n") if crlf else body).encode("utf-8"))
    manifest_path = root / sync.MANIFEST
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest(NEW), encoding="utf-8")


def test_every_place_is_rewritten_to_the_manifest_and_nothing_else_changes(tmp_path: Path) -> None:
    build_tree(tmp_path, OLD)
    assert sync.drift(tmp_path), "the tree should start out disagreeing"
    changed = sync.sync(tmp_path)
    assert sorted(changed) == sorted({rel for image in sync.IMAGES for rel in image.files})
    assert sync.drift(tmp_path) == []
    text = (tmp_path / "compose.yaml").read_text(encoding="utf-8")
    assert NEW["database"] in text and NEW["ollama"] in text
    assert "# keep this comment" in text and "unrelated: line" in text
    assert OLD["database"] not in text and OLD["ollama"] not in text


def test_a_second_run_changes_nothing(tmp_path: Path) -> None:
    build_tree(tmp_path, OLD)
    sync.sync(tmp_path)
    assert sync.sync(tmp_path) == []


def test_windows_line_endings_survive(tmp_path: Path) -> None:
    build_tree(tmp_path, OLD, crlf=True)
    sync.sync(tmp_path)
    raw = (tmp_path / "deploy/charts/kaval/values.yaml").read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")


def test_the_check_names_every_place_that_differs_and_says_how_to_fix_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build_tree(tmp_path, OLD)
    assert sync.main(["--check"], tmp_path) == 1
    captured = capsys.readouterr()
    assert "compose.yaml" in captured.out and "values.yaml" in captured.out
    assert "sync_third_party_images.py" in captured.err
    assert sync.main([], tmp_path) == 0
    assert sync.main(["--check"], tmp_path) == 0


def test_one_stray_place_is_found(tmp_path: Path) -> None:
    build_tree(tmp_path, NEW)
    assert sync.drift(tmp_path) == []
    stray = tmp_path / "scripts/ops/seed-refresh.sh"
    text = stray.read_text(encoding="utf-8")
    stray.write_text(text.replace(NEW["database"], OLD["database"]), encoding="utf-8")
    problems = sync.drift(tmp_path)
    assert len(problems) == 1 and problems[0].startswith("scripts/ops/seed-refresh.sh")


def test_a_place_that_stopped_pinning_the_image_is_reported_not_skipped(tmp_path: Path) -> None:
    build_tree(tmp_path, NEW)
    floating = tmp_path / "compose.yaml"
    floating.write_text(
        "image: ollama/ollama:latest\nimage: pgvector/pgvector:pg16\n", encoding="utf-8"
    )
    problems = sync.drift(tmp_path)
    assert any("compose.yaml: does not name ollama/ollama pinned by digest" in p for p in problems)
    with pytest.raises(sync.SyncError, match="nothing to move"):
        sync.sync(tmp_path)


@pytest.mark.parametrize(
    "bad",
    [
        f"pgvector/pgvector:0.8.7-pg17-bookworm@sha256:{D2}",  # a newer PostgreSQL
        f"pgvector/pgvector:0.8.7-pg16-trixie@sha256:{D2}",  # a newer Debian changes sort order
        "pgvector/pgvector:pg16",  # floating
        f"pgvector/pgvector:0.8.7-pg16-bookworm@sha256:{'c' * 63}",  # not a whole digest
        f"postgres:16.4-bookworm@sha256:{D2}",  # not the pgvector image
    ],
)
def test_the_database_image_must_stay_postgres_16_on_bookworm(tmp_path: Path, bad: str) -> None:
    build_tree(tmp_path, OLD)
    (tmp_path / sync.MANIFEST).write_text(manifest({**NEW, "database": bad}), encoding="utf-8")
    with pytest.raises(sync.SyncError, match="refusing"):
        sync.read_manifest(tmp_path)
    assert sync.main([], tmp_path) == 2
    untouched = (tmp_path / "compose.yaml").read_text(encoding="utf-8")
    assert OLD["database"] in untouched, "nothing was rewritten"


def test_a_manifest_without_every_stage_or_with_a_stranger_is_refused(tmp_path: Path) -> None:
    build_tree(tmp_path, OLD)
    path = tmp_path / sync.MANIFEST
    path.write_text(manifest({k: v for k, v in NEW.items() if k != "ollama"}), encoding="utf-8")
    with pytest.raises(sync.SyncError, match="AS ollama"):
        sync.read_manifest(tmp_path)
    path.write_text(manifest(NEW) + f"FROM redis:7.2.4@sha256:{D2} AS cache\n", encoding="utf-8")
    with pytest.raises(sync.SyncError, match="does not know"):
        sync.read_manifest(tmp_path)
    twice = manifest(NEW) + f"FROM ollama/ollama:0.40.17@sha256:{D2} AS ollama\n"
    path.write_text(twice, encoding="utf-8")
    with pytest.raises(sync.SyncError, match="twice"):
        sync.read_manifest(tmp_path)


def test_dependabot_reads_the_manifest_one_pull_request_per_image() -> None:
    config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    entry = next(
        u
        for u in config["updates"]
        if u["package-ecosystem"] == "docker" and u.get("directory") == "/deploy/third-party-images"
    )
    assert entry["schedule"]["interval"] == "weekly"
    # one pull request per image: a bad Ollama bump must not hold up a safe Prometheus one
    assert "groups" not in entry
    # a brand-new release waits a few days before it is proposed
    assert entry["cooldown"]["default-days"] >= 3
    assert entry["commit-message"]["prefix"] == "chore"
