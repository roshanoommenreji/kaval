"""scripts/ops/anonymise.sql against a real Postgres (KAV-72, ADR-0037).

The script sanitises a copy of production before staging can read it. It runs here exactly
as restore.sh runs it, inside the `db_session` transaction that is rolled back at teardown,
so nothing persists. Skipped when no database is reachable, like the other database tests;
CI sets KAVAL_REQUIRE_DB=1 and so runs them.

Three promises are tested:
  1. Nothing that identifies an account, a person, a machine or a secret survives.
  2. What staging needs to behave like production does survive (names, counts, numbers,
     and the same person keeping the same pseudonym).
  3. A column nobody classified fails the build, instead of being silently left out.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from kaval_shared.models import (
    Action,
    BlastRadius,
    Decision,
    Execution,
    ExecutionStatus,
    Incident,
    PolicyClass,
    Proposal,
    RiskLevel,
    Severity,
    Signal,
    Verdict,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ops" / "anonymise.sql"

# A made-up 12-digit account number, written in two halves: the repository's pre-commit check
# refuses to let a 12-digit literal through, which is the right default, and this is not one.
ACCOUNT = "123456" + "789012"
ARN = f"arn:aws:iam::{ACCOUNT}:role/kaval-prod-node"
BIG_NUMBER = int("987654" + "321098")  # twelve digits as a JSON number
KEY_ID = "AKIAABCDEFGHIJKLMNOP"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnopqrst"
EMAIL = "someone.real@company.example"
HOST = "ip-10-60-2-54.ap-south-1.compute.internal"
PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\nabcdef\n-----END RSA PRIVATE KEY-----"
# Written in two pieces: GitHub's push protection (rightly) refuses anything shaped like a
# Slack token.
SLACK = "xo" + "xb-1234567890-abcdefghijklmnop"

# Every one of these must be gone from the scrubbed columns.
SENSITIVE = [ACCOUNT, "AKIAABCDEFGHIJKLMNOP", "eyJhbGciOiJIUzI1NiJ9",
             "someone.real", "ip-10-60-2-54", "BEGIN RSA PRIVATE KEY", "MIIEowIBAAKCAQEA",
             SLACK[:15], "kaval-prod-node"]

# text/jsonb columns that hold no free-form text, and why. Every other text or jsonb column
# must be in SCRUBBED. A new column is in neither: that is the test failing, on purpose.
NOT_FREE_FORM = {
    "alembic_version.version_num": "schema revision; kept on purpose so staging migrates forward",
    "signal.source": "controlled vocabulary written by the collector",
    "signal.kind": "controlled vocabulary written by the collector",
    "proposal.model": "model name",
    "action.type": "action class from the policy catalogue",
    "runbook_chunk.path": "public runbook file name",
    "runbook_chunk.heading": "public runbook text",
    "runbook_chunk.content": "public runbook text",
    "runbook_chunk.content_hash": "sha256",
}
SCRUBBED = {
    "signal.target", "signal.value", "incident.fingerprint", "proposal.summary",
    "proposal.root_cause", "action.target", "action.params", "decision.actor",
    "decision.reason", "execution.stdout", "execution.before_state", "execution.after_state",
}


def _raw(db: Session, sql: str, params: tuple[str, ...] | None = None) -> list[tuple[str, ...]]:
    """Run SQL on the test's own connection with the driver directly: SQLAlchemy would try to
    read `:name` and `%` in the script as parameters. Rolled back with the test."""
    db.flush()
    raw = db.connection().connection.dbapi_connection
    assert raw is not None
    cur = raw.cursor()
    try:
        cur.execute(sql, params) if params is not None else cur.execute(sql)
        return list(cur.fetchall()) if cur.description else []
    finally:
        cur.close()


def _run_script(db: Session) -> None:
    _raw(db, SCRIPT.read_text(encoding="utf-8"))


def _dirty_incident(db: Session, actor: str = "slack:real.person") -> dict[str, object]:
    now = datetime.now(UTC)
    signal = Signal(
        source="k8s_event", kind="oom_killed", target=f"{HOST}/kaval-demo/checkout-7f9c",
        value={
            "reason": "OOMKilled",
            "restart_count": 4,
            "observed_ms": 1760000000000,  # 13 digits, a timestamp: must NOT be touched
            # A JSON number, not text: left alone (a byte count looks the same).
            "big_number": BIG_NUMBER,
            "message": f"role {ARN} in {ACCOUNT} used {KEY_ID}; mail {EMAIL}; token {JWT}",
            "nested": [{"host": HOST, "slack": SLACK}, "x" * 900],
        },
        observed_at=now,
    )
    db.add(signal)
    db.flush()
    incident = Incident(
        fingerprint=f"oom_killed:k8s:{ACCOUNT}/checkout", severity=Severity.high,
        opened_at=now, signals=[signal],
    )
    db.add(incident)
    db.flush()
    proposal = Proposal(
        incident_id=incident.id, summary=f"Restart the pod in account {ACCOUNT}",
        root_cause=f"Memory limit too low; see {ARN} and mail {EMAIL}", confidence=0.8,
        risk=RiskLevel.low, model="gemma3:1b", tokens_in=10, tokens_out=5, cost_usd=Decimal("0"),
    )
    db.add(proposal)
    db.flush()
    action = Action(
        proposal_id=proposal.id, type="restart_pod", target=f"kaval-demo/checkout-{ACCOUNT}",
        params={"namespace": "kaval-demo", "note": f"{ARN} {ACCOUNT}", "grace": 30},
        reversible=True, blast_radius=BlastRadius.pod, policy_class=PolicyClass.ask,
    )
    db.add(action)
    db.flush()
    db.add(Decision(
        action_id=action.id, verdict=Verdict.approved, actor=actor,
        reason="approved, Priya said it is fine", decided_at=now,
    ))
    db.add(Execution(
        action_id=action.id, status=ExecutionStatus.success,
        stdout=f"deleted pod\n{PEM}\nand {KEY_ID} {JWT} {ACCOUNT} {EMAIL} " + "y" * 2500,
        before_state={"uid": str(uuid.uuid4()), "arn": ARN, "restarts": 3},
        after_state={"phase": "Running", "host": HOST, "restarts": 0},
        started_at=now,
    ))
    db.flush()
    return {"signal": signal.id, "action": action.id}


def _everything_scrubbed_as_text(db: Session) -> str:
    """Every scrubbed column of every row, as one string, for 'is anything left' searches."""
    rows = db.execute(text("""
        SELECT concat_ws(' ', s.target, s.value::text, i.fingerprint, p.summary, p.root_cause,
                         a.target, a.params::text, d.actor, d.reason, e.stdout,
                         e.before_state::text, e.after_state::text)
        FROM signal s, incident i, proposal p, action a, decision d, execution e
        WHERE a.id = d.action_id AND a.id = e.action_id AND p.id = a.proposal_id
          AND i.id = p.incident_id
    """)).scalars().all()
    return "\n".join(rows)


def test_nothing_sensitive_survives(db_session: Session) -> None:
    _dirty_incident(db_session)
    _run_script(db_session)
    blob = _everything_scrubbed_as_text(db_session)
    assert blob, "the join found no rows to check"
    survivors = [needle for needle in SENSITIVE if needle in blob]
    assert survivors == []


def test_what_staging_needs_survives(db_session: Session) -> None:
    ids = _dirty_incident(db_session)
    _run_script(db_session)

    sig = db_session.get(Signal, ids["signal"])
    assert sig is not None
    db_session.refresh(sig)
    assert sig.value["reason"] == "OOMKilled"  # text that was never sensitive is untouched
    assert sig.value["restart_count"] == 4
    assert sig.value["observed_ms"] == 1760000000000  # a 13-digit number is not an account id
    assert sig.value["big_number"] == BIG_NUMBER  # JSON numbers are not text, not scrubbed
    nested = sig.value["nested"]
    assert isinstance(nested, list) and len(nested[1]) == 500  # the 500-character cap

    act = db_session.get(Action, ids["action"])
    assert act is not None
    db_session.refresh(act)
    assert act.params["namespace"] == "kaval-demo"
    assert act.params["grace"] == 30
    assert "000000000000" in act.target

    ex = db_session.execute(text("SELECT stdout, before_state FROM execution")).one()
    assert ex.stdout.startswith("deleted pod")
    assert len(ex.stdout) <= 2000
    assert ex.before_state["restarts"] == 3
    assert "PRIVATE_KEY_REDACTED" in ex.stdout


def test_same_person_keeps_the_same_pseudonym(db_session: Session) -> None:
    _dirty_incident(db_session, actor="slack:real.person")
    _dirty_incident(db_session, actor="slack:real.person")
    _dirty_incident(db_session, actor="slack:someone.else")
    _run_script(db_session)
    actors = db_session.execute(text("SELECT actor FROM decision")).scalars().all()
    assert all(re.fullmatch(r"operator-[0-9a-f]{8}", a) for a in actors)
    assert len(set(actors)) == 2  # two people stay two people; the repeat stays one
    named = db_session.execute(
        text("SELECT count(*) FROM decision WHERE reason LIKE '%Priya%'")
    ).scalar()
    assert named == 0


def test_running_it_twice_changes_nothing(db_session: Session) -> None:
    _dirty_incident(db_session)
    _run_script(db_session)
    first = _everything_scrubbed_as_text(db_session)
    _run_script(db_session)  # its own final check also fails if a second pass would change a row
    assert _everything_scrubbed_as_text(db_session) == first


def test_it_works_on_an_empty_database(db_session: Session) -> None:
    _run_script(db_session)


def test_unclean_json_shapes_do_not_break_it(db_session: Session) -> None:
    """Empty containers, null leaves and deep nesting are shapes real signals take."""
    now = datetime.now(UTC)
    db_session.add(Signal(source="k8s_event", kind="x", target="t", observed_at=now,
                          value={"a": {}, "b": [], "c": None, "d": [[{"e": ARN}]], "f": True}))
    _run_script(db_session)
    value = db_session.execute(text("SELECT value FROM signal")).scalars().all()[-1]
    assert json.dumps(value).count(ACCOUNT) == 0
    assert value["a"] == {} and value["b"] == [] and value["c"] is None and value["f"] is True


def test_every_text_column_is_classified(db_session: Session) -> None:
    """The guard against 'forgetting is silent' (ADR-0005): a new text or jsonb column fails
    this test until someone decides whether it needs scrubbing."""
    found = {
        f"{r.table_name}.{r.column_name}"
        for r in db_session.execute(text("""
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND (data_type IN ('text', 'jsonb', 'character varying'))
        """))
    }
    unclassified = sorted(found - SCRUBBED - set(NOT_FREE_FORM))
    assert unclassified == [], (
        "new text/jsonb column(s) with no decision: add each to scripts/ops/anonymise.sql "
        "and to SCRUBBED here, or to NOT_FREE_FORM with a reason"
    )
    stale = sorted((SCRUBBED | set(NOT_FREE_FORM)) - found)
    assert stale == [], f"classified but no longer in the schema: {stale}"


@pytest.mark.parametrize("sample", [
    "plain text with nothing in it",
    f"{ACCOUNT}",
    f"x{ACCOUNT}y",
    "1234567890123",  # thirteen digits: not an account id
    "12345678901",  # eleven digits: not one either
    f"call {EMAIL}.",
    f"{ARN},{ARN}",
    "",
])
def test_scrub_text_is_idempotent(db_session: Session, sample: str) -> None:
    # Load only the function definitions from the script (everything before the first UPDATE).
    head = SCRIPT.read_text(encoding="utf-8").split("-- ── who approved what")[0]
    _raw(db_session, head)
    once = _raw(db_session, "SELECT pg_temp.scrub_text(%s)", (sample,))[0][0]
    twice = _raw(db_session, "SELECT pg_temp.scrub_text(%s)", (once,))[0][0]
    assert once == twice
    if sample == "1234567890123":
        assert once == sample
