"""The /v1 surface against a real Postgres: paging, filters, the incident timeline, 404s,
and the READ ONLY transaction. Skipped when no database is reachable (services/conftest.py).

Each test writes its own rows inside the rolled-back `db_session` and filters to them
(a unique target, a run_id), so rows already in the dev database never change a result.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from kaval_collector import synthetic
from kaval_gateway import api
from kaval_gateway.main import app
from kaval_shared.models import (
    Action,
    BlastRadius,
    Decision,
    Execution,
    ExecutionStatus,
    Incident,
    Outcome,
    PolicyClass,
    Proposal,
    RiskLevel,
    Severity,
    Signal,
    Verdict,
)
from sqlalchemy import insert
from sqlalchemy.exc import InternalError
from sqlalchemy.orm import Session

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    """The app, reading through the test's rolled-back session instead of its own."""
    app.dependency_overrides[api.read_session] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _signals(db: Session, target: str, times: list[datetime]) -> list[Signal]:
    rows = [
        Signal(source="kubernetes", kind="pod_back_off", target=target,
               value={"count": i}, observed_at=t)
        for i, t in enumerate(times)
    ]
    db.add_all(rows)
    db.flush()
    return rows


def test_openapi_documents_every_route() -> None:
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    assert {"/healthz", "/v1/signals", "/v1/signals/{signal_id}", "/v1/incidents",
            "/v1/incidents/{incident_id}"} <= set(paths)


def test_signals_come_newest_first(client: TestClient, db_session: Session) -> None:
    target = f"test/{uuid.uuid4()}"
    _signals(db_session, target, [NOW - timedelta(minutes=m) for m in (3, 1, 2)])
    items = client.get("/v1/signals", params={"target": target}).json()["items"]
    assert [i["value"]["count"] for i in items] == [1, 2, 0]


def test_cursor_paging_returns_every_row_exactly_once(
    client: TestClient, db_session: Session
) -> None:
    # Two rows share a timestamp and straddle a page boundary: the id tie-break is what
    # keeps one of them from being skipped.
    target = f"test/{uuid.uuid4()}"
    times = [NOW, NOW - timedelta(seconds=1), NOW - timedelta(seconds=1),
             NOW - timedelta(seconds=2), NOW - timedelta(seconds=3)]
    written = {str(s.id) for s in _signals(db_session, target, times)}

    seen: list[str] = []
    params: dict[str, str | int] = {"target": target, "limit": 2}
    while True:
        page = client.get("/v1/signals", params=params).json()
        seen += [i["id"] for i in page["items"]]
        if page["next_cursor"] is None:
            break
        params["cursor"] = page["next_cursor"]
    assert len(seen) == len(written) and set(seen) == written


def test_a_bad_cursor_is_a_400_not_a_500(client: TestClient) -> None:
    assert client.get("/v1/signals", params={"cursor": "not-a-cursor"}).status_code == 400


def test_page_size_is_bounded(client: TestClient) -> None:
    assert client.get("/v1/signals", params={"limit": 201}).status_code == 422


def test_filter_to_one_synthetic_run(client: TestClient, db_session: Session) -> None:
    run = synthetic.generate("exec-format", seed=5)
    synthetic.emit(db_session, run)
    run_id = str(run[0].value["run_id"])

    items = client.get("/v1/signals", params={"run_id": run_id, "synthetic": True}).json()
    assert len(items["items"]) == len(run)
    real_only = client.get("/v1/signals", params={"run_id": run_id, "synthetic": False})
    assert real_only.json()["items"] == []


def test_one_signal_by_id_and_404_for_none(client: TestClient, db_session: Session) -> None:
    (signal,) = _signals(db_session, f"test/{uuid.uuid4()}", [NOW])
    assert client.get(f"/v1/signals/{signal.id}").json()["target"] == signal.target
    missing = client.get(f"/v1/signals/{uuid.uuid4()}")
    assert missing.status_code == 404 and missing.json() == {"detail": "signal not found"}


def _incident_with_timeline(db: Session) -> Incident:
    (signal,) = _signals(db, f"test/{uuid.uuid4()}", [NOW])
    incident = Incident(fingerprint=f"test-{uuid.uuid4()}", severity=Severity.high,
                        opened_at=NOW, signals=[signal])
    proposal = Proposal(incident=incident, summary="restart checkout",
                        root_cause="memory limit too low", confidence=0.82,
                        risk=RiskLevel.low, model="gemma3:1b-it-qat", tokens_in=900,
                        tokens_out=120, cost_usd=Decimal("0.000000"))
    action = Action(proposal=proposal, type="rollout_restart", target=signal.target,
                    params={}, reversible=True, blast_radius=BlastRadius.pod,
                    policy_class=PolicyClass.ask)
    db.add_all([
        incident, proposal, action,
        Decision(action=action, verdict=Verdict.approved, actor="test", decided_at=NOW),
        Execution(action=action, status=ExecutionStatus.success, started_at=NOW,
                  finished_at=NOW),
        Outcome(incident=incident, resolved=True, mttr_sec=94, regression=False,
                measured_at=NOW + timedelta(minutes=5)),
    ])
    db.flush()
    return incident


def test_incident_detail_is_the_whole_timeline(client: TestClient, db_session: Session) -> None:
    incident = _incident_with_timeline(db_session)
    body = client.get(f"/v1/incidents/{incident.id}").json()

    assert body["status"] == "open"
    assert len(body["signals"]) == 1
    (proposal,) = body["proposals"]
    assert proposal["cost_usd"] == "0.000000"  # money stays a string, never a float
    (action,) = proposal["actions"]
    assert action["decision"]["verdict"] == "approved"
    assert action["execution"]["status"] == "success"
    assert body["outcomes"][0]["mttr_sec"] == 94


def test_incidents_filter_by_status(client: TestClient, db_session: Session) -> None:
    incident = _incident_with_timeline(db_session)
    open_ids = {i["id"] for i in
                client.get("/v1/incidents", params={"status": "open", "limit": 200})
                .json()["items"]}
    closed_ids = {i["id"] for i in
                  client.get("/v1/incidents", params={"status": "closed", "limit": 200})
                  .json()["items"]}
    assert str(incident.id) in open_ids and str(incident.id) not in closed_ids


def test_unknown_incident_is_404(client: TestClient) -> None:
    assert client.get(f"/v1/incidents/{uuid.uuid4()}").status_code == 404


def test_the_database_refuses_writes_from_a_read_session(db_session: Session) -> None:
    # db_session is requested only so this skips without a database; the session under
    # test is the real dependency the endpoints use. Hold the generator, as FastAPI does:
    # `next(api.read_session())` alone lets it be collected at once, which closes the
    # read-only session and lets the insert autobegin an ordinary one (found the hard way).
    dependency = api.read_session()
    session = next(dependency)
    try:
        with pytest.raises(InternalError, match="read-only transaction"):
            session.execute(insert(Signal).values(
                source="test", kind="write_attempt", target="test", value={}, observed_at=NOW,
            ))
    finally:
        dependency.close()
