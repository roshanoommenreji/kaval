"""The executor's half of removal (KAV-95, ADR-0063): an approved order is handed over and recorded
as exactly that, and nothing is handed over unless every extra condition holds.

Nothing here calls Kubernetes or AWS: the cluster is a small fake with the two calls the hand-over
makes. Most tests are about what is refused: a proposal from the model, a cause removal may not act
on, a target of the wrong kind, removal switched off, and an action nobody approved."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from kaval_agent import policy as policy_mod
from kaval_agent.removal import RULE_MODEL, STEP_BACKUP, STEP_DELETE, STEP_SNAPSHOT
from kaval_executor import executor, removal
from kaval_shared.models import (
    Action,
    BlastRadius,
    Decision,
    ExecutionStatus,
    Incident,
    PolicyClass,
    Proposal,
    RiskLevel,
    Severity,
    Verdict,
)
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
VID = "vol-0abc1234def56789a"
SID = "snap-0abc1234def56789a"
MAP = "kaval-staging-removal-orders"


class FakeCluster:
    """The ConfigMap API: one ConfigMap, read and patched."""

    def __init__(self, data: dict[str, str] | None = None, missing: bool = False) -> None:
        self.data = data if data is not None else {}
        self.missing = missing
        self.patches: list[tuple[str, str, dict[str, Any]]] = []

    def read_namespaced_config_map(self, name: str, namespace: str) -> Any:
        if self.missing:
            raise RuntimeError("404 not found")
        return type("CM", (), {"data": self.data})()

    def patch_namespaced_config_map(self, name: str, namespace: str, body: dict[str, Any]) -> None:
        self.patches.append((name, namespace, body))
        self.data.update(body["data"])


def _action(
    db: Session,
    *,
    kind: str = STEP_DELETE,
    target: str = VID,
    cause: str = "idle_volume",
    model: str = RULE_MODEL,
    verdict: Verdict | None = Verdict.approved,
) -> Action:
    incident = Incident(
        fingerprint=f"{cause}:aws:ebs/{target}", severity=Severity.low, opened_at=NOW
    )
    proposal = Proposal(
        incident=incident,
        summary="s",
        root_cause="r",
        confidence=1.0,
        risk=RiskLevel.low,
        model=model,
        tokens_in=0,
        tokens_out=0,
        cost_usd=Decimal("0"),
    )
    action = Action(
        proposal=proposal,
        type=kind,
        target=target,
        params={},
        reversible=False,
        blast_radius=BlastRadius.resource,
        policy_class=PolicyClass.ask,
    )
    db.add_all([incident, proposal, action])
    db.flush()
    if verdict is not None:
        db.add(Decision(action_id=action.id, verdict=verdict, actor="test", decided_at=NOW))
        db.flush()
    db.refresh(action)
    return action


@pytest.fixture(autouse=True)
def _on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAVAL_REMOVAL_ENABLED", "true")
    monkeypatch.setenv("KAVAL_NAMESPACE", "kaval-staging")
    monkeypatch.setenv("KAVAL_REMOVAL_ORDERS", MAP)
    monkeypatch.setattr(policy_mod, "classify", lambda *a, **kw: PolicyClass.ask)


# ── the extra conditions (pure) ─────────────────────────────────────────────────────────────────


def test_a_planner_made_disk_step_for_a_loose_disk_is_allowed(db_session: Session) -> None:
    for kind in (STEP_BACKUP, STEP_DELETE):
        assert removal.refusal(_action(db_session, kind=kind)) is None


def test_a_planner_made_copy_step_for_an_old_copy_is_allowed(db_session: Session) -> None:
    a = _action(db_session, kind=STEP_SNAPSHOT, target=SID, cause="snapshot_past_retention")
    assert removal.refusal(a) is None


def test_removal_is_off_unless_exactly_true(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = _action(db_session)
    for value in ("", "false", "1", "yes"):
        monkeypatch.setenv("KAVAL_REMOVAL_ENABLED", value)
        reason = removal.refusal(a)
        assert reason is not None and "switched off" in reason
    monkeypatch.delenv("KAVAL_REMOVAL_ENABLED")
    assert removal.refusal(a) is not None


def test_a_proposal_from_the_model_can_never_order_a_removal(db_session: Session) -> None:
    a = _action(db_session, model="gemma3:1b-it-qat")
    reason = removal.refusal(a)
    assert reason is not None and "removal planner" in reason


@pytest.mark.parametrize(
    "cause",
    [
        "oom_killed",
        "idle_address",
        "server_overstay",
        "resource_unlabelled",
        "removal_failed",
        "disk_pressure",
        "kaval_loop_stalled",
        "cost_spike",
    ],
)
def test_a_cause_removal_may_not_act_on_is_refused(db_session: Session, cause: str) -> None:
    reason = removal.refusal(_action(db_session, cause=cause))
    assert reason is not None and "not one removal may act on" in reason


def test_a_disk_step_for_the_backup_copy_cause_and_the_reverse_are_refused(
    db_session: Session,
) -> None:
    assert removal.refusal(_action(db_session, cause="snapshot_past_retention")) is not None
    a = _action(db_session, kind=STEP_SNAPSHOT, target=SID, cause="idle_volume")
    assert removal.refusal(a) is not None


@pytest.mark.parametrize(
    "target",
    ["", "*", "i-0abc1234def56789a", SID, "vol-ZZZ", VID + "\n", "vol-0abc1234def56789a; rm"],
)
def test_a_target_that_is_not_a_disk_is_refused_for_a_disk_step(
    db_session: Session, target: str
) -> None:
    assert removal.refusal(_action(db_session, target=target)) is not None


def test_a_disk_is_not_a_copy_for_the_copy_step(db_session: Session) -> None:
    a = _action(db_session, kind=STEP_SNAPSHOT, target=VID, cause="snapshot_past_retention")
    assert removal.refusal(a) is not None


def test_only_the_three_steps_map_to_worker_words() -> None:
    assert removal.ORDER_STEPS == {
        STEP_BACKUP: "mark_backup",
        STEP_DELETE: "delete_volume",
        STEP_SNAPSHOT: "delete_snapshot",
    }


# ── the orders document ─────────────────────────────────────────────────────────────────────────


def _order(n: int) -> dict[str, str]:
    return {"id": f"0f0f0f0f-{n:04x}", "step": "delete_volume", "target": VID, "at": "x"}


def test_an_order_is_added_once() -> None:
    first = removal.add_order(None, _order(1))
    again = removal.add_order(first, _order(1))
    assert json.loads(again) == {"orders": [_order(1)]}


def test_only_the_newest_five_are_kept() -> None:
    doc = None
    for n in range(9):
        doc = removal.add_order(doc, _order(n))
    ids = [o["id"] for o in json.loads(doc or "{}")["orders"]]
    assert ids == [_order(n)["id"] for n in range(4, 9)]


@pytest.mark.parametrize("bad", ["not json", "[]", '{"orders": "x"}', '{"orders": [1, null]}', ""])
def test_a_garbled_document_is_replaced_not_carried_forward(bad: str) -> None:
    assert json.loads(removal.add_order(bad, _order(1))) == {"orders": [_order(1)]}


def test_the_order_holds_the_step_word_the_target_and_a_time_never_free_text(
    db_session: Session,
) -> None:
    order = removal.make_order(_action(db_session), NOW)
    assert set(order) == {"id", "step", "target", "at"}
    assert order["step"] == "delete_volume" and order["target"] == VID
    assert order["at"] == "2026-10-19T12:00:00Z"


# ── the hand-over and execute_one ───────────────────────────────────────────────────────────────


def test_hand_over_patches_only_the_one_configmap(db_session: Session) -> None:
    cluster = FakeCluster({"orders.json": '{"orders":[]}'})
    before, after, out = removal.hand_over(
        _action(db_session), v1=cluster, namespace="kaval-staging", configmap=MAP, now=NOW
    )
    [(name, namespace, body)] = cluster.patches
    assert (name, namespace) == (MAP, "kaval-staging") and set(body["data"]) == {"orders.json"}
    assert before == {"orders_in_configmap": 0} and after["handed_over"] is True
    assert "handed over" in out and "done" not in out.split(";")[0]


def test_an_approved_step_is_handed_over_and_recorded_as_handed_over_not_done(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cluster = FakeCluster({})
    monkeypatch.setattr(executor, "_core_v1", lambda: cluster)
    action = _action(db_session)
    result = executor.execute_one(db_session, action, now=NOW)
    assert result.status == ExecutionStatus.success
    db_session.refresh(action)
    assert action.execution is not None
    assert "handed over" in (action.execution.stdout or "")
    assert json.loads(cluster.data["orders.json"])["orders"][0]["id"] == str(action.id)


def test_an_action_nobody_approved_is_not_even_selected(db_session: Session) -> None:
    _action(db_session, verdict=None)
    assert executor._ready_actions(db_session) == []


def test_a_denied_removal_is_never_handed_over(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cluster = FakeCluster({})
    monkeypatch.setattr(executor, "_core_v1", lambda: cluster)
    action = _action(db_session, verdict=Verdict.denied)
    result = executor.execute_one(db_session, action, now=NOW)
    assert result.status == ExecutionStatus.skipped and cluster.patches == []


def test_a_model_made_removal_is_skipped_and_recorded_with_why(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cluster = FakeCluster({})
    monkeypatch.setattr(executor, "_core_v1", lambda: cluster)
    action = _action(db_session, model="gemma3:1b-it-qat")
    result = executor.execute_one(db_session, action, now=NOW)
    assert result.status == ExecutionStatus.skipped and cluster.patches == []
    assert "removal planner" in result.detail


def test_a_policy_never_stops_it_even_for_a_planner_made_step(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(policy_mod, "classify", lambda *a, **kw: PolicyClass.never)
    cluster = FakeCluster({})
    monkeypatch.setattr(executor, "_core_v1", lambda: cluster)
    result = executor.execute_one(db_session, _action(db_session), now=NOW)
    assert result.status == ExecutionStatus.skipped and cluster.patches == []


def test_an_old_approval_is_refused_like_any_other(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cluster = FakeCluster({})
    monkeypatch.setattr(executor, "_core_v1", lambda: cluster)
    action = _action(db_session)
    late = datetime(2026, 10, 19, 20, 0, tzinfo=UTC)
    assert executor.execute_one(db_session, action, now=late).status == ExecutionStatus.skipped
    assert cluster.patches == []


def test_when_the_configmap_is_missing_the_execution_is_recorded_as_failed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(executor, "_core_v1", lambda: FakeCluster(missing=True))
    result = executor.execute_one(db_session, _action(db_session), now=NOW)
    assert result.status == ExecutionStatus.failed


def test_a_removal_type_is_not_a_cluster_handler() -> None:
    from kaval_executor import k8s

    assert not set(removal.ORDER_STEPS) & set(k8s.SUPPORTED_ACTIONS)


def test_the_random_id_in_an_order_is_the_actions_own(db_session: Session) -> None:
    a = _action(db_session)
    assert uuid.UUID(removal.make_order(a, NOW)["id"]) == a.id
