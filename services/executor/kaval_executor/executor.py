"""The executor: the only Kaval component that changes anything (CLAUDE.md constraint 3,
KAV-47).

Selection, one pass: an action with no `execution` row yet (one per action, like one
`decision` per action), and whose stored data already looks authorized — its own
`policy_class` is `auto`, or it carries a human `Decision` whose verdict is `approved` or
`auto_approved`. An action still only `ask`, with no decision, is simply not selected: it
reappears on a later pass once a human decides, nothing is written about it meanwhile, and in
particular **no `execution` row is ever written for an action that isn't ready yet** — the
unique constraint on `execution.action_id` means a row written too early would permanently
block that action from ever actually being executed once it is approved.

For every action this pass *does* select, before touching the cluster: `policy.classify()` is
called again, fresh, from the action's own `type`/`blast_radius`/`reversible` and its
proposal's `confidence` — never from the stored `policy_class`. This is ADR-0006 rule 3 and
`policy/README.md`'s "evaluated twice" made real, not just stated: if `promotions.json`
changed between proposal time and now, or if the stored `policy_class` was ever wrong
(a bug, or a compromised writer), the fresh check is what the cluster actually sees, and a
`never` or an unauthorized `ask` on the fresh check is refused — recorded as a skipped
`execution` row with why, never executed, never silently dropped.

    python -m kaval_executor.executor              # one pass (make execute)
    python -m kaval_executor.executor --every 15   # keep going, one pass every 15 s
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from kaval_agent import policy as policy_mod
from kaval_shared.models import Action, Decision, Execution, ExecutionStatus, PolicyClass, Verdict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kaval_executor import __version__, k8s
from kaval_executor.redact import redact

_APPROVED_VERDICTS = {Verdict.approved, Verdict.auto_approved}


@dataclass(frozen=True)
class Executed:
    action_id: object
    action_type: str
    status: ExecutionStatus
    detail: str


def _ready_actions(session: Session) -> list[Action]:
    """Actions with no execution yet, whose stored data already looks authorized — oldest
    first, so a backlog drains in approval order rather than arbitrarily."""
    return list(session.scalars(
        select(Action)
        .outerjoin(Decision, Decision.action_id == Action.id)
        .outerjoin(Execution, Execution.action_id == Action.id)
        .where(
            Execution.id.is_(None),
            (Action.policy_class == PolicyClass.auto)
            | (Decision.verdict.in_(_APPROVED_VERDICTS)),
        )
        .order_by(Action.created_at)
    ))


def _refusal(action: Action, fresh: PolicyClass) -> str | None:
    """`None` if the fresh classification actually authorizes acting now; otherwise the
    reason it doesn't. Trusts only `fresh` and `action.decision` — never
    `action.policy_class`, which is exactly the field this whole check exists not to trust."""
    if fresh == PolicyClass.never:
        return (f"policy re-check says 'never' (stored class was "
                f"{action.policy_class.value})")
    if fresh == PolicyClass.auto:
        return None
    # fresh == ask: only a real human approval authorizes this. `_ready_actions` only
    # selects actions whose *stored* class already looked ready, so reaching `ask` here
    # means the stored class (auto) and today's policy disagree — drift worth refusing and
    # recording, not silently acting on.
    if action.decision is not None and action.decision.verdict in _APPROVED_VERDICTS:
        return None
    return (f"policy re-check says 'ask' and there is no human approval (stored class was "
            f"{action.policy_class.value})")


def execute_one(session: Session, action: Action, *, now: datetime | None = None) -> Executed:
    now = now or datetime.now(UTC)
    fresh = policy_mod.classify(
        action.type, action.blast_radius, action.reversible, action.proposal.confidence,
    )
    if (reason := _refusal(action, fresh)) is not None:
        return _record(session, action, ExecutionStatus.skipped, f"refused: {reason}",
                       started=now, finished=now)

    handler = k8s.SUPPORTED_ACTIONS.get(action.type)
    if handler is None:
        return _record(
            session, action, ExecutionStatus.skipped,
            f"refused: no executor handler for action type {action.type!r}",
            started=now, finished=now,
        )

    started = datetime.now(UTC)
    try:
        before, after, stdout = handler(action.target)
        status = ExecutionStatus.success
    except Exception as exc:
        # The one place a real cluster call happens. Anything it raises becomes a failed,
        # recorded execution — never an exception that stops the rest of this pass, the
        # same "a policy engine that fails should produce more questions, not fewer" shape
        # kaval_agent.policy.classify uses for its own failure path.
        before, after = None, None
        stdout, status = f"{type(exc).__name__}: {exc}", ExecutionStatus.failed
    return _record(session, action, status, stdout, before=before, after=after,
                   started=started, finished=datetime.now(UTC))


def _record(
    session: Session, action: Action, status: ExecutionStatus, stdout: str, *,
    started: datetime, finished: datetime,
    before: dict[str, object] | None = None, after: dict[str, object] | None = None,
) -> Executed:
    """Write the one `execution` row this action will ever get. `redact()` runs here,
    unconditionally, before the row is built — not a step a caller could forget (ADR-0005:
    redact at write time, the only place that actually prevents a secret from ever landing
    in the column, not merely from surviving a later anonymise pass)."""
    execution = Execution(
        action_id=action.id, status=status, stdout=redact(stdout),
        before_state=before, after_state=after, started_at=started, finished_at=finished,
    )
    session.add(execution)
    try:
        session.commit()
    except IntegrityError:
        # Another executor replica (there is only ever meant to be one, `replicas: 1`, but
        # the unique constraint is what actually guarantees it, not the deployment config)
        # committed this action's execution first. Its row is the real one; this pass's
        # attempt is simply dropped, not retried as a second execution of the same action.
        session.rollback()
        return Executed(action.id, action.type, ExecutionStatus.skipped,
                        "already executed by another pass")
    return Executed(action.id, action.type, status, stdout[:160])


def run_once(session: Session) -> list[Executed]:
    return [execute_one(session, action) for action in _ready_actions(session)]


def report(results: Sequence[Executed]) -> list[str]:
    if not results:
        return ["nothing to execute"]
    return [f"{r.status.value:9} {r.action_type:20} {r.action_id}  {r.detail}"
           for r in results]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m kaval_executor.executor",
        description="Execute approved (or already auto-classified) actions. Re-checks "
                     "policy itself, from each action's own fields, immediately before "
                     "acting — never trusts a stored policy_class.",
    )
    parser.add_argument("--every", type=float, metavar="SECONDS",
                        help="keep running, one pass every SECONDS; default: one pass")
    parser.add_argument("--version", action="version", version=f"kaval-executor {__version__}")
    args = parser.parse_args(argv)

    from kaval_shared.db import get_engine  # only a real run needs the database
    k8s.load_config()

    try:
        while True:
            with Session(get_engine()) as session:
                for line in report(run_once(session)):
                    print(line, flush=True)
            if not args.every:
                return 0
            time.sleep(args.every)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
