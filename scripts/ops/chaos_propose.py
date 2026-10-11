"""File the proposal a language model would write, for a chaos experiment (KAV-79, KAV-80).

Staging has no language model (it does not fit the 4 GB node). Detection, approval, execution and
verification are all real on staging; only the diagnosis step is stood in for, by this script, and
the game-day post-mortem says so. It finds the newest OPEN incident about the experiment's victim,
asks the real policy engine for the class (so it is `ask`, not whatever this script would like), and
writes the proposal and action rows exactly as the agent's `diagnose.write_proposal` would.

Which experiment comes from `CHAOS_EXPERIMENT` (`oom`, the default, or `badrelease`):
  oom         raise the victim's memory limit
  badrelease  roll the victim back to its previous version (NOT restart it: a restart brings the
              same broken version back, which is exactly what the experiment checks Kaval knows)

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
    "badrelease": ("chaos-badrelease", "rollback_deployment",
                   "crashes on start since its latest release; roll it back to the previous version",
                   "the latest release is broken (exits at start-up); the previous one ran fine"),
}


def main() -> int:
    namespace = os.environ["CHAOS_TARGET_NAMESPACE"]
    experiment = os.environ.get("CHAOS_EXPERIMENT", "oom")
    if experiment not in EXPERIMENTS:
        print(f"unknown experiment {experiment!r} (have: {', '.join(EXPERIMENTS)})")
        return 2
    victim, action_type, what, root_cause = EXPERIMENTS[experiment]
    with Session(get_engine()) as db:
        incident = db.scalars(
            select(Incident)
            .where(Incident.closed_at.is_(None), Incident.fingerprint.contains(f"/{victim}"))
            .order_by(Incident.opened_at.desc())
        ).first()
        if incident is None:
            print(f"no open incident about {victim} yet; wait for Kaval to notice, then try again")
            return 1
        reversible, radius = True, BlastRadius.deployment
        klass = policy.classify(action_type, radius, reversible, CONFIDENCE)
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
