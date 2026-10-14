"""File the proposal a language model would write, for a chaos experiment (KAV-79, KAV-80).

Until KAV-87 staging had no language model, and this script stood in for the diagnosis step. Now the
diagnosis worker files the proposal itself (ADR-0055), so for `oom` and `badrelease` this script
FINDS that proposal and prints its action instead of filing a second one (two proposals for one
incident would be two approvals for one fix). `CHAOS_BY_HAND=1` files one by hand anyway, for a
run with the model switched off; the post-mortem says which was used.

It finds the newest OPEN incident about the experiment's victim,
asks the real policy engine for the class (so it is `ask`, not whatever this script would like), and
writes the proposal and action rows exactly as the agent's `diagnose.write_proposal` would.

Which experiment comes from `CHAOS_EXPERIMENT` (`oom`, the default, `badrelease`, `slow` or `disk`):
  oom         raise the victim's memory limit
  badrelease  roll the victim back to its previous version (NOT restart it: a restart brings the
              same broken version back, which is exactly what the experiment checks Kaval knows)
  slow, disk  (KAV-81, ADR-0047) the WRONG fix on purpose: a restart, which is what a model might
              plausibly suggest and what does nothing for a slow service or a full disk. These
              incidents are for a person, so the policy says `never`, and the gateway then refuses
              to let anyone approve it (HTTP 409). The game day checks exactly that refusal.

Run by `scripts/ops/chaos.sh propose`, inside a throwaway pod (never inside a running service: a
second process in a 128 MiB container is how this project once OOM-killed its own agent) with the
AGENT's database credentials, which is the role allowed to write proposals.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal

from kaval_agent import policy
from kaval_shared.db import get_engine
from kaval_shared.models import Action, BlastRadius, Incident, Proposal, RiskLevel
from sqlalchemy import select
from sqlalchemy.orm import Session

CONFIDENCE = 0.9

# experiment -> (victim, action type, what the diagnosis says)
EXPERIMENTS = {
    "oom": ("chaos-oomer", "increase_memory_limit",
            "keeps being killed for memory; raise its memory limit",
            "memory limit lower than the program needs"),
    "slow": ("chaos-slowpoke", "restart_pod",
             "is slow; restart it (a plausible but useless suggestion, filed by hand)",
             "the service has become slow"),
    "disk": ("node-disk", "restart_pod",
             "the disk is nearly full; restart something (a plausible but useless suggestion)",
             "the node disk is filling"),
    "badrelease": ("chaos-badrelease", "rollback_deployment",
                   "crashes on start since its latest release; roll it back to the previous version",
                   "the latest release is broken (exits at start-up); the previous one ran fine"),
}


def worker_action(proposals: list[Proposal]) -> tuple[Proposal, Action] | None:
    """The first action of a proposal the diagnosis worker filed (not by hand), if there is one."""
    for proposal in proposals:
        if proposal.model != "by-hand" and proposal.actions:
            return proposal, proposal.actions[0]
    return None


def main() -> int:
    namespace = os.environ["CHAOS_TARGET_NAMESPACE"]
    experiment = os.environ.get("CHAOS_EXPERIMENT", "oom")
    if experiment not in EXPERIMENTS:
        print(f"unknown experiment {experiment!r} (have: {', '.join(EXPERIMENTS)})")
        return 2
    victim, action_type, what, root_cause = EXPERIMENTS[experiment]
    people_only = experiment in ("slow", "disk")
    # The incident for these two is named for the problem; the program may also have an ordinary
    # incident (a start-up blip), which is NOT the one under test.
    cause_prefix = {"slow": "latency_high:", "disk": "disk_pressure:"}.get(experiment, "")
    with Session(get_engine()) as db:
        incident = db.scalars(
            select(Incident)
            .where(Incident.closed_at.is_(None), Incident.fingerprint.contains(f"/{victim}"),
                   Incident.fingerprint.startswith(cause_prefix))
            .order_by(Incident.opened_at.desc())
        ).first()
        if incident is None:
            print(f"no open incident about {victim} yet; wait for Kaval to notice, then try again")
            return 1
        if not people_only and os.environ.get("CHAOS_BY_HAND") != "1":
            found = worker_action(list(incident.proposals))
            if found is not None:
                proposal, action = found
                print(f"incident {incident.fingerprint}")
                print(f"Kaval diagnosed this itself ({proposal.model}): {proposal.summary}")
                print(f"policy class: {action.policy_class.value}")
                print(f"ACTION {action.id}")
                return 0
            if incident.proposals:
                print("the diagnosis worker answered with no fix for this incident; "
                      "CHAOS_BY_HAND=1 files one by hand")
                return 1
            print("the diagnosis worker has not proposed anything yet (it runs every 30 s and "
                  "may be waiting for the model); try again, or CHAOS_BY_HAND=1 to file by hand")
            return 1
        reversible = True
        radius = BlastRadius.pod if people_only else BlastRadius.deployment
        cause = incident.fingerprint.split(":", 1)[0]
        # The cause is passed exactly as the agent and the executor pass it, so for a disk or a
        # slow service the real policy says `never`, whatever this script would like.
        klass = policy.classify(action_type, radius, reversible, CONFIDENCE, cause=cause)
        proposal = Proposal(
            incident=incident,
            summary=f"{victim} {what} (filed by hand: staging has no language model)",
            root_cause=root_cause,
            confidence=CONFIDENCE, risk=RiskLevel.low, model="by-hand",
            tokens_in=0, tokens_out=0, cost_usd=Decimal("0"),
        )
        db.add(proposal)
        db.flush()
        action = Action(
            proposal_id=proposal.id, type=action_type,
            target=f"{namespace}/{victim}", params={}, reversible=reversible,
            blast_radius=radius, policy_class=klass,
        )
        db.add(action)
        db.commit()
        print(f"incident {incident.fingerprint}")
        print(f"policy class: {klass.value}")
        print(f"ACTION {action.id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
