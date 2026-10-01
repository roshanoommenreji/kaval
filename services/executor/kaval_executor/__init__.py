"""Kaval executor: the only component that mutates anything (CLAUDE.md constraint 3, KAV-47).

It consumes actions that are already `auto`-classified or already carry a human's `approved`
decision, re-checks policy itself from the action's own fields immediately before acting
(never trusting the stored `policy_class` — ADR-0006 rule 3, `policy/README.md`'s "evaluated
twice"), and writes exactly one `execution` row per action, with `stdout` already redacted
(ADR-0005). Nothing it reads or writes grants it an opinion on what *should* happen — that's
the agent's and the policy engine's job. It only ever asks "is this one, specific, already-
decided thing still authorized right now", and acts or refuses.
"""

# This component's own version (ADR-0013). Bumped by the conventional commits that touch
# services/executor/ or the shared code it ships; CI stamps it on the image as
# org.opencontainers.image.version and checks the two agree.
__version__ = "0.1.0"
