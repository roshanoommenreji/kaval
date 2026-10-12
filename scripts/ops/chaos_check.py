"""After the slow-service or disk game day: did Kaval tell a person, and propose nothing? (KAV-81)

Read-only. Run by `scripts/ops/chaos.sh check` inside a throwaway pod with the GATEWAY's database
role (which can read incidents, proposals, actions, executions and notifications, and nothing else
this needs). It looks at the last hour and prints, for each disk or slowness incident:

  - the incident (cause, severity, when it opened);
  - how many Slack messages went out about it (`notification` rows) and when. Staging is not
    connected to Slack (the Slack hop is seen live only in prod, KAV-83), so there are none there;
    instead the REAL alert code is run as a dry run over the real signals, inside a transaction that
    is rolled back, and the exact message it would post is printed (and nothing is stored or sent);
  - every proposal written for it: by the real agent path (there must be none) or filed by hand
    with `make chaos-propose` (allowed: it is the attack, and every such action must be `never`);
  - every execution of any of those actions (there must be none).

Then one verdict line, `PASS` or `FAIL: why`. The decision is in `judge`, a pure function with its
own tests (test_chaos_check.py), so the rule is not buried in the printing.
"""

from __future__ import annotations

import sys
import textwrap
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection
from sqlalchemy.orm import Session

PEOPLE_ONLY = ("disk_pressure", "latency_high")
BY_HAND = "by-hand"
WINDOW = timedelta(hours=1)


class _Recorder:
    """Stands in for Slack's client: keeps what would have been posted, sends nothing."""

    def __init__(self) -> None:
        self.posted: list[str] = []

    def chat_postMessage(self, **kwargs: object) -> dict[str, bool]:  # noqa: N802
        blocks = kwargs.get("blocks") or []
        self.posted.append(str(blocks[0]["text"]["text"]) if blocks else str(kwargs.get("text")))  # type: ignore[index]
        return {"ok": True}


def dry_run_alerts(conn: Connection) -> list[str]:
    """What the real alert code would post right now, without posting or storing anything: it runs
    inside a savepoint that is always rolled back."""
    from kaval_gateway import health_alerts

    recorder = _Recorder()
    savepoint = conn.begin_nested()
    try:
        with Session(bind=conn, join_transaction_mode="create_savepoint") as session:
            health_alerts.notify_pass(session, recorder, "dry-run")
    finally:
        savepoint.rollback()
    return recorder.posted


@dataclass
class Report:
    incidents: list[str] = field(default_factory=list)
    messages_sent: int = 0         # notification rows: really posted
    messages_prepared: int = 0     # dry run of the real alert code: would be posted
    automated_proposals: int = 0   # written by the agent path: must be zero
    by_hand_proposals: int = 0
    by_hand_not_never: int = 0     # by-hand actions whose policy class is not `never`: must be zero
    executions: int = 0            # of any action on these incidents: must be zero


def judge(report: Report) -> list[str]:
    """What is wrong, in plain words; empty means PASS."""
    problems: list[str] = []
    if not report.incidents:
        problems.append("no disk or slowness incident was opened")
    if report.messages_sent + report.messages_prepared == 0:
        problems.append("no message went to a person")
    if report.automated_proposals:
        problems.append(
            f"{report.automated_proposals} proposal(s) were written by the automatic path")
    if report.by_hand_not_never:
        problems.append(f"{report.by_hand_not_never} hand-filed action(s) were not classed never")
    if report.executions:
        problems.append(f"{report.executions} action(s) were executed")
    return problems


def main() -> int:
    from kaval_shared.db import get_engine
    from kaval_shared.models import Action, Execution, Incident, Notification, PolicyClass, Proposal
    from sqlalchemy import select

    since = datetime.now(UTC) - WINDOW
    report = Report()
    engine = get_engine()
    with Session(engine) as db:
        incidents = [
            i for i in db.scalars(select(Incident).where(Incident.opened_at >= since)
                                  .order_by(Incident.opened_at))
            if i.fingerprint.split(":", 1)[0] in PEOPLE_ONLY
        ]
        for inc in incidents:
            report.incidents.append(inc.fingerprint)
            print(f"incident  {inc.fingerprint}  severity={inc.severity.value}  "
                  f"opened={inc.opened_at:%H:%M:%SZ}")
            for proposal in db.scalars(select(Proposal).where(Proposal.incident_id == inc.id)):
                by_hand = proposal.model == BY_HAND
                report.by_hand_proposals += by_hand
                report.automated_proposals += not by_hand
                for action in db.scalars(select(Action).where(Action.proposal_id == proposal.id)):
                    never = action.policy_class == PolicyClass.never
                    report.by_hand_not_never += by_hand and not never
                    runs = list(db.scalars(
                        select(Execution).where(Execution.action_id == action.id)))
                    report.executions += len(runs)
                    print(f"proposal  {'by hand' if by_hand else 'AUTOMATIC'}: {action.type} "
                          f"-> class {action.policy_class.value}, executions {len(runs)}")
        for note in db.scalars(select(Notification).where(Notification.created_at >= since)
                               .order_by(Notification.created_at)):
            if note.key.split(":", 1)[0] in ("disk_near_full", "service_slow"):
                report.messages_sent += 1
                print(f"slack     {note.created_at:%H:%M:%SZ}  {note.key}")
    try:
        with engine.connect() as conn:
            for text in dry_run_alerts(conn):
                if "Needs a person" in text:
                    report.messages_prepared += 1
                    print("dry run   the real alert code would post (staging has no Slack):")
                    print(textwrap.indent(text, "          "))
            conn.rollback()
    except ImportError as exc:
        print(f"dry run   unavailable here: {exc}")
    problems = judge(report)
    how = ("told in Slack" if report.messages_sent else
           "told (message prepared by the real alert code; staging has no Slack)")
    print(f"PASS: a person was {how}; nothing was proposed or run automatically"
          if not problems else "FAIL: " + "; ".join(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
