"""Kaval agent: reads signals, writes suggestions. It never gets write access to the cluster or AWS.

Its only outputs are rows in its own tables: `incident` (from `kaval_agent.correlate`, KAV-39),
and `proposal`/`action` (from `kaval_agent.diagnose`, KAV-41 — context assembled by
`kaval_agent.context`, KAV-40). Anything that changes a real system is the executor's job, and
only after policy and a human.
"""

# This component's own version (ADR-0013). Bumped by the conventional commits that touch
# services/agent/ or the shared code it ships; CI stamps it on the image as
# org.opencontainers.image.version and checks the two agree.
__version__ = "0.1.0"
