"""The eval harness (KAV-43, ADR-0018): every golden incident through the real pipeline --
correlate -> context -> diagnose -> policy -- scored against known-good answers.

Needs a live dev-server stack (`make dev-tunnel`), like `make diagnose` and `make context`:
this is not part of `make test`, which stays pure, fast, and Ollama-free. Every signal this
writes is flagged `synthetic: true` (`golden.py`'s own builders do this), and the resulting
incident/proposal/action rows are real spine rows, the same way `make signals` + `make
diagnose` already produce real rows from synthetic input -- no separate table, no special
case; see ADR-0018 for why that's the right call.

One `correlate()` pass handles every case, including the two recurrence cases' two batches
each: `plan()`'s own per-subject quiet-window logic (services/agent/kaval_agent/correlate.py)
already separates a batch backdated past the 15-minute k8s quiet window from a fresher one
sharing the same subject into two separate incidents, one already closed, in a single pass --
see that module's docstring. This harness only has to backdate its two batches far enough
apart and call `correlate()` once at the end.

    python -m evals.run [--only NAME] [--json PATH] [--prompt before|after|both]

`--prompt` (KAV-84, ADR-0054): `after` is the prompt Kaval runs today (the default). `before` is the
prompt as it was before the model was told which fixes exist, kept frozen in `evals/baseline.py`.
`both` diagnoses every case with each, from the one context built once, so a before-and-after is a
single run on a single database state. The `before` proposals are filed with the model name
suffixed `+baseline-prompt`, so they can be told apart (and ignored) afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from kaval_agent.context import Context, build_context
from kaval_agent.correlate import correlate
from kaval_agent.diagnose import DiagnosisError, diagnose, write_proposal
from kaval_shared.models import Incident, Signal
from sqlalchemy import select
from sqlalchemy.orm import Session

from evals.baseline import BASELINE_SYSTEM_PROMPT, BaselineDiagnosis
from evals.golden import GOLDEN_INCIDENTS, GoldenIncident
from evals.scoring import CaseResult, Report, score_case

# Comfortably past GROUP_WAIT (60s) and well inside the k8s QUIET window (15min): the scored
# incident opens immediately and stays open, the way a fresh real incident would mid-triage.
SCORED_OFFSET = timedelta(minutes=6)
# Comfortably past the 15-minute quiet window relative to SCORED_OFFSET too, so a recurrence
# case's history batch is fully closed before its scored batch is even written.
HISTORY_OFFSET = timedelta(minutes=40)


def _write_signals(session: Session, now: datetime) -> dict[str, str]:
    """Write every case's signals (and, for recurrence cases, a backdated history batch
    first). Returns {case name: the scored batch's target}, for finding each case's incident
    again after `correlate()` runs."""
    targets: dict[str, str] = {}
    for golden in GOLDEN_INCIDENTS:
        if golden.expect_recurrence:
            session.add_all(golden.build(now - HISTORY_OFFSET))
        signals = golden.build(now - SCORED_OFFSET)
        session.add_all(signals)
        targets[golden.name] = signals[0].target
    session.commit()
    return targets


def _incident_for(session: Session, target: str) -> Incident | None:
    return session.scalars(
        select(Incident).join(Incident.signals)
        .where(Signal.target == target)
        .order_by(Incident.opened_at.desc())
        .limit(1)
    ).first()


def run_case(
    session: Session, golden: GoldenIncident, incident: Incident | None, *, model: str,
    context: Context | None = None, variant: str = "after", expected_target: str | None = None,
) -> CaseResult:
    if incident is None:
        return score_case(golden, error="correlate() did not open an incident for this case")

    if context is None:
        context = build_context(session, incident, changes=None)
    prompt_kwargs: dict[str, object] = {}
    if variant == "before":
        # The old message was the context and nothing after it; the old schema let `actions`
        # be left out.
        prompt_kwargs = {
            "system_prompt": BASELINE_SYSTEM_PROMPT, "diagnosis_model": BaselineDiagnosis,
            "user_message_override": context.render(), "narrow_to_incident": False,
        }
        model = f"{model}+baseline-prompt"
    try:
        diagnosis, usage = diagnose(context, **prompt_kwargs)  # type: ignore[arg-type]
    except DiagnosisError as exc:
        return score_case(golden, error=str(exc))

    proposal = write_proposal(session, incident, diagnosis, usage, model=model)
    # write_proposal already classified every action (KAV-42) -- read it back, don't reclassify.
    action_classes = tuple(a.policy_class for a in proposal.actions)
    return score_case(
        golden, confidence=proposal.confidence, stated_confidence=diagnosis.confidence,
        root_cause=diagnosis.root_cause,
        summary=diagnosis.summary, action_classes=action_classes,
        action_types=tuple(a.type for a in proposal.actions),
        action_targets=tuple(a.target for a in proposal.actions),
        expected_target=expected_target,
        history_len=len(context.history), tokens_in=usage.tokens_in, tokens_out=usage.tokens_out,
    )


def run_all(
    session: Session, *, only: str | None = None, variants: tuple[str, ...] = ("after",),
) -> dict[str, Report]:
    now = datetime.now(UTC)
    cases = [g for g in GOLDEN_INCIDENTS if only is None or g.name == only]
    if not cases:
        raise SystemExit(f"no golden incident named {only!r}")
    # Every case's signals are written regardless of `--only` -- correlate() runs the same
    # single pass either way, and `--only` just narrows which case gets diagnosed and scored.
    targets = _write_signals(session, now)
    plan = correlate(session, now=now)
    if plan is None:
        raise SystemExit("another correlator holds the advisory lock; try again")

    model = os.environ.get("LOCAL_MODEL", "")
    results: dict[str, list[CaseResult]] = {v: [] for v in variants}
    for golden in cases:
        incident = _incident_for(session, targets[golden.name])
        # One context per case, shared by every variant: the only thing that differs between
        # "before" and "after" is the prompt (and the schema's wording of `action.type`).
        context = build_context(session, incident, changes=None) if incident else None
        for variant in variants:
            results[variant].append(run_case(
                session, golden, incident, model=model, context=context, variant=variant,
                expected_target=targets[golden.name],
            ))
    return {v: Report(results=r) for v, r in results.items()}


def _json_rows(report: Report, variant: str) -> list[dict[str, object]]:
    return [
        {
            "variant": variant, "name": r.name, "passed": r.passed,
            "schema_valid": r.schema_valid,
            "error": r.error, "confidence": r.confidence,
            "stated_confidence": r.stated_confidence, "any_never": r.any_never,
            "keyword_ok": r.keyword_ok, "confidence_ok": r.confidence_ok,
            "recurrence_ok": r.recurrence_ok, "fix_ok": r.fix_ok,
            "fix_wrong_target": r.fix_wrong_target, "extra_fixes": r.extra_fixes,
            "action_types": list(r.action_types), "action_targets": list(r.action_targets),
            "history_len": r.history_len, "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
        }
        for r in report.results
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.run",
        description="Run every golden incident through the real pipeline and score it. "
                     "Run from the laptop with `make dev-tunnel` up.",
    )
    parser.add_argument("--only", help="run a single golden incident by name")
    parser.add_argument("--json", help="also write the raw results as JSON to this path")
    parser.add_argument("--prompt", choices=("before", "after", "both"), default="after",
                        help="which diagnosis prompt to score: today's (after, the default), the "
                             "one from before KAV-84 told the model which fixes exist (before), or "
                             "both, case by case, for a before-and-after in one run")
    args = parser.parse_args(argv)

    from kaval_shared.db import get_engine  # only a real run needs the database

    variants = ("before", "after") if args.prompt == "both" else (args.prompt,)
    with Session(get_engine()) as session:
        reports = run_all(session, only=args.only, variants=variants)

    for variant, report in reports.items():
        if len(reports) > 1:
            print(f"===== prompt: {variant} =====")
        print(report.render())
        print()

    if args.json:
        with open(args.json, "w") as f:
            json.dump([row for v, r in reports.items() for row in _json_rows(r, v)], f, indent=2)

    # The hard release gate (ADR-0018): schema validity and action safety, always. Root-cause
    # keyword rate, calibration and (KAV-84) the fix rate are reported, not gated -- a baseline
    # has to exist before a "good enough" rate can be argued for; see the ADRs. The gate is on
    # the prompt Kaval runs today; the frozen baseline is only there to compare against.
    report = reports["after"] if "after" in reports else reports["before"]
    if report.any_never_count > 0 or report.schema_valid_count < report.total:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
