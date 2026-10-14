"""`chaos_propose.py` finds the proposal the diagnosis worker filed rather than filing a second one
(KAV-87): two proposals for one incident would be two approvals for one fix."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chaos_propose  # noqa: E402


def _proposal(model: str, actions: int) -> Any:
    rows = [SimpleNamespace(id=f"a{i}") for i in range(actions)]
    return SimpleNamespace(model=model, actions=rows)


def test_a_proposal_from_the_worker_with_an_action_is_found() -> None:
    mine = _proposal("gemma3:1b-it-qat", 1)
    found = chaos_propose.worker_action([_proposal("by-hand", 1), mine])
    assert found is not None and found[0] is mine and found[1].id == "a0"


def test_a_hand_filed_proposal_is_not_mistaken_for_the_workers() -> None:
    assert chaos_propose.worker_action([_proposal("by-hand", 1)]) is None


def test_a_worker_proposal_with_no_action_gives_nothing_to_approve() -> None:
    assert chaos_propose.worker_action([_proposal("gemma3:1b-it-qat", 0)]) is None


def test_no_proposals_at_all() -> None:
    assert chaos_propose.worker_action([]) is None
