"""File the proposal a language model would write, for the memory experiment (KAV-79).

Staging has no language model (it does not fit the 4 GB node). Detection, approval, execution and
verification are all real on staging; only the diagnosis step is stood in for, by this script, and
the game-day post-mortem says so. It finds the newest OPEN incident about the experiment's victim,
asks the real policy engine for the class (so it is `ask`, not whatever this script would like), and
writes the proposal and action rows exactly as the agent's `diagnose.write_proposal` would.

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

VICTIM = "chaos-oomer"
CONFIDENCE = 0.9


def main() -> int:
    namespace = os.environ["CHAOS_TARGET_NAMESPACE"]
    with Session(get_engine()) as db:
        incident = db.scalars(
            select(Incident)
            .where(Incident.closed_at.is_(None), Incident.fingerprint.contains(f"/{VICTIM}"))
            .order_by(Incident.opened_at.desc())
        ).first()
        if incident is None:
            print(f"no open incident about {VICTIM} yet; wait for Kaval to notice, then try again")
            return 1
        reversible, radius = True, BlastRadius.deployment
        klass = policy.classify("increase_memory_limit", radius, reversible, CONFIDENCE)
        proposal = Proposal(
            incident=incident,
            summary=f"{VICTIM} keeps being killed for memory; raise its memory limit "
                    "(filed by hand: staging has no language model)",
            root_cause="memory limit lower than the program needs",
            confidence=CONFIDENCE, risk=RiskLevel.low, model="by-hand",
            tokens_in=0, tokens_out=0, cost_usd=Decimal("0"),
        )
        db.add(proposal)
        db.flush()
        action = Action(
            proposal_id=proposal.id, type="increase_memory_limit",
            target=f"{namespace}/{VICTIM}", params={}, reversible=reversible,
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
