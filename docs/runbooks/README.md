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

## Written

- `restore-from-backup.md`, `rollback-prod.md`, `kaval-stopped-reporting.md` (`KAV-78`), `pod-oomkilled.md` (`KAV-79`), `pod-crashloop-after-release.md` and `node-lost.md` (`KAV-80`), `disk-pressure.md` and `latency-spike.md` (`KAV-81`; both are for a person: no fix is proposed), `approval-unanswered.md`, `backup-missing.md` and `restore-drill.md` (`KAV-82`), `diagnosis-not-happening.md` (`KAV-87`; incidents are not getting a diagnosis, for a person), `still-alive-alarm.md` and `prod-rollout.md` (`KAV-83`; the second is a procedure, bring production up, check it, park it, with the two manual fixes `KAV-85` will remove)

## Planned (Phase 5)

- `cost-spike.md`
