"""Kaval agent: reads signals, writes suggestions. It never gets write access to the cluster or AWS.

Its only outputs are rows in its own tables: `incident` (from `kaval_agent.correlate`, KAV-39)
and, from the context builder and the model call later in Phase 2, `proposal` and `action`.
Anything that changes a real system is the executor's job, and only after policy and a human.
"""

# This component's own version (ADR-0013). Bumped by the conventional commits that touch
# services/agent/ or the shared code it ships; CI stamps it on the image as
# org.opencontainers.image.version and checks the two agree.
__version__ = "0.1.0"
