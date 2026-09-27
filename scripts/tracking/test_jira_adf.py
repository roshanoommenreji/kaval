"""Tests for the Jira document helpers (KAV-34)."""

from __future__ import annotations

import jira_adf as adf

DOD = "Definition of Done"


def test_description_without_uat_has_no_uat_section() -> None:
    doc = adf.description("ctx", ["a", "b"], DOD)
    assert [i for i in adf.items(doc, adf.AC)] == [(False, "a"), (False, "b")]
    assert adf.items(doc, adf.UAT) == []


def test_ticking_acceptance_criteria_leaves_uat_scenarios_alone() -> None:
    doc = adf.description("ctx", ["a", "b"], DOD, uat=["Given x, then y"])
    assert adf.tick(doc, adf.AC) == 2
    assert adf.items(doc, adf.AC) == [(True, "a"), (True, "b")]
    assert adf.items(doc, adf.UAT) == [(False, "Given x, then y")]


def test_ticking_uat_scenarios_leaves_acceptance_criteria_alone() -> None:
    doc = adf.description("ctx", ["a"], DOD, uat=["s1", "s2"])
    assert adf.tick(doc, adf.UAT) == 2
    assert adf.items(doc, adf.AC) == [(False, "a")]
    assert adf.tick(doc, adf.UAT) == 0  # already done: nothing changes


def test_legacy_story_without_headings_ticks_every_checklist() -> None:
    doc = adf.description("ctx", ["a", "b"], DOD)
    doc["content"] = [n for n in doc["content"] if n["type"] != "heading"]
    assert adf.tick(doc, adf.AC) == 2


def test_every_checklist_node_carries_a_local_id() -> None:
    doc = adf.description("ctx", ["a"], DOD, uat=["s"])
    lists = [n for n in doc["content"] if n["type"] == "taskList"]
    assert len(lists) == 2
    for tl in lists:
        assert tl["attrs"]["localId"]
        assert all(i["attrs"]["localId"] for i in tl["content"])


def test_services_from_paths() -> None:
    paths = [
        "services/gateway/kaval_gateway/api.py",
        "services/shared/kaval_shared/models.py",
        "migrations/versions/0001_spine.py",
        "infra/envs/dev/main.tf",
        "compose.yaml",
        "docs/labs/lab-05.md",
        ".github/workflows/ci.yml",
    ]
    assert adf.services_for(paths) == ["gateway", "infra", "shared"]


def test_docs_only_change_belongs_to_no_service() -> None:
    assert adf.services_for(["docs/adr/0012.md", "README.md", "scripts/tracking/x.py"]) == []
