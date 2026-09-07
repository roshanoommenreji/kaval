# ADR-0001 — Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-08-22
- **Deciders:** Roshan

## Context

This project runs at 4–6 hours a week over roughly seven months, in sessions a week or more apart.
Two things follow from that.

First, decisions get forgotten. By February it will not be obvious why Postgres runs in-cluster
rather than on RDS, and the temptation will be to "fix" it — re-litigating something already
settled for good reasons.

Second, the documentation is a deliverable. This project is intended to become a YouTube series or
course, and the *reasoning* is the most valuable part of that. Nobody needs another tutorial that
shows what to type. The interesting content is why this and not that, and what it cost.

A Claude Code session also starts cold each time. Written decisions are how context survives.

## Decision

Use Architecture Decision Records, one Markdown file per decision, numbered sequentially, in
`docs/adr/`. Format follows Michael Nygard's original convention.

Write an ADR when a choice:

- has a plausible alternative someone would reasonably pick
- would be expensive or disruptive to reverse later
- constrains future work
- costs money on a recurring basis

Do **not** write one for choices with an obvious default and no real trade-off.

An ADR is immutable once Accepted. To change a decision, write a new ADR that supersedes it and
mark the old one `Superseded by ADR-XXXX`. The wrong turns stay visible — for a course, the
abandoned path is often the more instructive half.

### Format

```markdown
# ADR-XXXX — Title

- Status: Proposed | Accepted | Superseded by ADR-YYYY
- Date: YYYY-MM-DD
- Deciders: who

## Context
The forces at play. What makes this a real decision rather than an obvious one.

## Decision
What was chosen, stated plainly.

## Consequences
What this makes easier, what it makes harder, what it costs per month,
and what would have to be true for us to revisit it.
```

## Consequences

**Easier:** a stranger — or a future session — can reconstruct the reasoning without asking.
Decisions stop being re-argued. The course gets its script for free.

**Harder:** a small tax on every real decision, maybe ten minutes. Some judgement is needed about
what deserves one; over-recording is as unhelpful as under-recording.

**Cost:** none.

**Revisit if:** ADRs start being written for trivia, or stop being written at all. Both mean the
threshold above needs restating.
