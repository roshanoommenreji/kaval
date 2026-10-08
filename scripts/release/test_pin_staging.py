from __future__ import annotations

from pathlib import Path

import pin_staging
import pytest

SAMPLE = """\
gateway:
  replicas: 1
  image:
    tag: sha-ffb436b
agent:
  image:
    tag: sha-ffb436b
executor:
  image:
    tag: sha-ffb436b
collector:
  image:
    tag: sha-ffb436b
"""


def test_pin_sets_every_tag_line() -> None:
    new, count = pin_staging.pin(SAMPLE, "sha-1a2b3c4")
    assert count == 4
    assert new.count("tag: sha-1a2b3c4") == 4
    assert "ffb436b" not in new


def test_pin_leaves_everything_else_alone() -> None:
    new, _ = pin_staging.pin(SAMPLE, "sha-1a2b3c4")
    assert new.replace("sha-1a2b3c4", "sha-ffb436b") == SAMPLE


def test_pin_is_idempotent() -> None:
    once, _ = pin_staging.pin(SAMPLE, "sha-1a2b3c4")
    twice, count = pin_staging.pin(once, "sha-1a2b3c4")
    assert twice == once
    assert count == 4


def test_pin_ignores_tags_that_are_not_release_tags() -> None:
    text = "image:\n  tag: latest\n  other: sha-ffb436b\n"
    new, count = pin_staging.pin(text, "sha-1a2b3c4")
    assert count == 0
    assert new == text


BAD_TAGS = ["latest", "sha-xyz1234", "sha-123", "1a2b3c4", "sha-1a2b3c4; rm -rf /"]


@pytest.mark.parametrize("bad", BAD_TAGS)
def test_pin_rejects_a_tag_that_is_not_a_release_tag(bad: str) -> None:
    with pytest.raises(ValueError):
        pin_staging.pin(SAMPLE, bad)


def _repo(tmp_path: Path, tags: int = 4) -> Path:
    body = "".join(f"s{i}:\n  image:\n    tag: sha-ffb436b\n" for i in range(tags))
    for name in pin_staging.FILES:
        target = tmp_path / name
        target.parent.mkdir(parents=True)
        target.write_text(body, encoding="utf-8")
    return tmp_path


def test_main_rewrites_both_files(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert pin_staging.main(["--tag", "sha-1a2b3c4", "--root", str(root)]) == 0
    for name in pin_staging.FILES:
        assert (root / name).read_text(encoding="utf-8").count("sha-1a2b3c4") == 4


def test_main_refuses_to_half_pin_when_a_file_changed_shape(tmp_path: Path) -> None:
    root = _repo(tmp_path, tags=3)
    assert pin_staging.main(["--tag", "sha-1a2b3c4", "--root", str(root)]) == 1
    # Nothing was written to either file.
    for name in pin_staging.FILES:
        assert "sha-1a2b3c4" not in (root / name).read_text(encoding="utf-8")
