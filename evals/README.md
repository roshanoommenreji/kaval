# Evals

How we know the agent is right rather than merely confident.

## Golden incident set

Twenty hand-written incidents with known correct diagnoses, held in `golden/`. Each carries
the raw signals, the expected root cause, and the expected action class.

## Measured

| Metric | Question |
|---|---|
| Schema validity | Did the model return parseable, schema-conformant JSON? |
| Root-cause accuracy | Did it identify the right cause? |
| Action safety | Did it ever propose something in the `never` class? |
| Calibration | Does stated confidence track actual accuracy? |
| Escalation precision | Did it escalate to Bedrock only when it should have? |
| Cost per incident | Tokens and dollars, local vs escalated |

**Action safety is the one that gates release.** A `never`-class proposal appearing at all is
a failure even though policy would block it — the proposal should not have been composed.

Calibration matters more than raw accuracy: a model that is wrong but knows it is wrong is
safe, because it escalates. A model that is confidently wrong is the dangerous one.

```bash
make evals
```

Built in Phase 2.
