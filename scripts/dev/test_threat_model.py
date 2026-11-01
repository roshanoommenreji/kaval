"""The threat model stays honest (KAV-112, ADR-0072).

A threat model rots in a specific way: a row says "covered" and the thing that covered it is gone,
or
a gap is "known" and nobody can find the story. These tests read `docs/architecture/threat-model.md`
and fail on the rot that can be checked by a machine:

  * every row has a status, and a Gap or Partly row names a story (KAV-nnn) or a ROADMAP line
  * every file the Proof column names exists
  * every `T-...` ID that a test or a story cites is a row in the file
  * every expected-failure test names a story that the threat model lists as a gap
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "architecture" / "threat-model.md"
ID = re.compile(r"\bT-[A-Z]+-\d+\b")
PATH_IN_BACKTICKS = re.compile(
    r"`((?:services|docs|deploy|policy|scripts|evals|\.github|infra)/[^`\s]+)`"
)
STATUS = re.compile(r"\*\*(Covered|Partly|Gap)\*\*|^(Covered|Partly)\b")


def _rows() -> list[tuple[str, list[str]]]:
    """(id, cells) for every table row whose first cell is an ID."""
    rows = []
    for line in DOC.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| T-"):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        rows.append((cells[0], cells))
    return rows


def _status(cells: list[str]) -> str:
    return cells[-1]


def test_the_model_has_rows_for_every_boundary() -> None:
    prefixes = {i.rsplit("-", 1)[0] for i, _ in _rows()}
    wanted = {"T-AGENT", "T-COLL", "T-SLACK", "T-APPROVE", "T-EXEC", "T-DATA", "T-SUPPLY", "T-OPS"}
    assert wanted <= prefixes


def test_ids_are_unique() -> None:
    ids = [i for i, _ in _rows()]
    assert len(ids) == len(set(ids))


def test_every_row_has_five_cells_and_a_status() -> None:
    for row_id, cells in _rows():
        assert len(cells) == 5, f"{row_id}: {len(cells)} cells"
        status = _status(cells)
        assert status.startswith(("Covered", "Partly", "**Gap**")), f"{row_id}: status {status!r}"


def test_every_partial_row_is_accepted_measured_or_names_where_the_rest_comes_from() -> None:
    for row_id, cells in _rows():
        status = _status(cells)
        if status.startswith("Partly"):
            assert re.search(r"accepted|measured|KAV-\d+|ROADMAP", status), f"{row_id}: {status!r}"


def test_every_gap_row_names_a_story_or_roadmap_line() -> None:
    for row_id, cells in _rows():
        if _status(cells).startswith("**Gap**"):
            assert re.search(r"KAV-\d+|ROADMAP", _status(cells)), row_id


def test_every_file_named_as_proof_exists() -> None:
    for row_id, cells in _rows():
        for ref in PATH_IN_BACKTICKS.findall(cells[3]):
            path = ref.split("::")[0]
            assert (ROOT / path).exists(), f"{row_id}: {path} is not in the repository"


def test_every_id_a_test_cites_is_a_row() -> None:
    known = {i for i, _ in _rows()}
    cited: set[str] = set()
    for pattern in ("services/*/tests/test_*injection*.py", "evals/*injection*.py"):
        for path in ROOT.glob(pattern):
            cited |= set(ID.findall(path.read_text(encoding="utf-8")))
    assert cited, "no test cites a threat-model ID: the link between them has been lost"
    assert cited <= known, f"cited but not in the model: {sorted(cited - known)}"


def test_every_expected_failure_names_a_story_the_model_lists_as_a_gap() -> None:
    model = DOC.read_text(encoding="utf-8")
    for path in ROOT.glob("services/*/tests/test_*injection*.py"):
        text = path.read_text(encoding="utf-8")
        for story in re.findall(r'xfail\(strict=True,\s*reason="(KAV-\d+)', text):
            assert story in model, f"{path.name}: {story} is not in the threat model"


def test_the_document_links_resolve() -> None:
    text = DOC.read_text(encoding="utf-8")
    for target in re.findall(r"\]\((\.\./[^)#\s]+)", text):
        assert (DOC.parent / target).resolve().exists(), f"broken link: {target}"
