"""The Access Analyzer stays the free kind and out of folders that get destroyed (KAV-160).

AWS charges for two of the analyzer's types (unused access, per role; internal access, per
resource) and not for the external-access one. Terraform picks the type with one word, so a slip
of that word would start a monthly bill nobody decided on. These offline rules read the Terraform
files and fail on it. Each checker also runs against a broken input, so a checker that stopped
working would fail here instead of passing silently.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENVS = ROOT / "infra" / "envs"
FREE_TYPES = {"ACCOUNT", "ORGANIZATION"}  # external access: no charge


def uncommented(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def analyzer_types(tf: str) -> list[str]:
    """The `type` of every aws_accessanalyzer_analyzer block in a Terraform file."""
    found: list[str] = []
    for block in re.finditer(
        r'resource\s+"aws_accessanalyzer_analyzer"\s+"[^"]+"\s*\{(.*?)\n\}', uncommented(tf), re.S
    ):
        m = re.search(r'^\s*type\s*=\s*"([^"]+)"', block.group(1), re.M)
        found.append(m.group(1) if m else "")
    return found


def paid_types(tf: str) -> list[str]:
    return [t for t in analyzer_types(tf) if t not in FREE_TYPES]


def test_the_state_folder_has_one_analyzer_and_it_is_the_free_type() -> None:
    types = analyzer_types((ENVS / "state" / "main.tf").read_text(encoding="utf-8"))
    assert types == ["ACCOUNT"]


def test_no_folder_declares_a_paid_analyzer() -> None:
    for tf in ENVS.rglob("*.tf"):
        assert paid_types(tf.read_text(encoding="utf-8")) == [], tf


def test_the_analyzer_is_not_in_a_folder_that_gets_destroyed() -> None:
    # staging is destroyed at the end of every session; an analyzer there would lose its history
    for env in ("staging", "dev"):
        for tf in (ENVS / env).rglob("*.tf"):
            assert analyzer_types(tf.read_text(encoding="utf-8")) == [], tf


def test_the_checker_catches_a_paid_type() -> None:
    broken = 'resource "aws_accessanalyzer_analyzer" "x" {\n  type = "ACCOUNT_UNUSED_ACCESS"\n}\n'
    assert paid_types(broken) == ["ACCOUNT_UNUSED_ACCESS"]
    internal = (
        'resource "aws_accessanalyzer_analyzer" "x" {\n  type = "ACCOUNT_INTERNAL_ACCESS"\n}\n'
    )
    assert paid_types(internal) == ["ACCOUNT_INTERNAL_ACCESS"]


def test_the_checker_ignores_a_commented_out_block_and_a_missing_type() -> None:
    commented = '# resource "aws_accessanalyzer_analyzer" "x" {\n#   type = "ACCOUNT"\n# }\n'
    assert analyzer_types(commented) == []
    no_type = 'resource "aws_accessanalyzer_analyzer" "x" {\n  analyzer_name = "a"\n}\n'
    assert paid_types(no_type) == [""]
