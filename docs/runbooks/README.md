# Runbooks

**These do double duty.** They are human documentation *and* the corpus the agent retrieves
from when diagnosing an incident. A runbook written here improves the product, not just the
docs — which is the intended defence against documentation rot.

## Format

Keep them tight and factual. The agent embeds these; rambling prose retrieves badly.

```markdown
# <Symptom as an operator would see it>

## Signals
What you observe. Metric names, event reasons, log patterns.

## Likely causes
Ordered by frequency, not by interest.

## Diagnosis
Commands that distinguish between the causes above.

## Remediation
The fix. Mark each action reversible or not, and name its blast radius.

## Do not
Actions that look right and make it worse.
```

## Planned (Phase 5)

- `pod-crashloop.md`
- `pod-oomkilled.md`
- `node-spot-reclaim.md`
- `disk-pressure.md`
- `latency-spike.md`
- `cost-spike.md`
