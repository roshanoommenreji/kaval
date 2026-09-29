# Lab 12 — The policy engine: auto, ask, never

**Phase:** 2 · **Time:** ~20 min · **Cost:** $0 — nothing here touches AWS

Every action [Lab 11](lab-11-proposal-output.md) writes has carried `policy_class=ask`,
hardcoded, since the model has no say in it and nothing else decided it yet. This lab builds the
real classifier: OPA evaluating a Rego policy against `blast_radius`, `reversible` and
`confidence`. The design is
[ADR-0017](../adr/0017-opa-policy-engine-and-earned-autonomy.md).

## Prerequisites

- [Lab 11](lab-11-proposal-output.md) done
- `opa` installed (`docs/labs/lab-00-toolchain.md`: `scoop install opa`, or the equivalent for
  your platform) and on `PATH` — `opa version` should print something

---

## Step 1 — Read the policy, then test it standalone

```bash
opa test policy/ -v
```

14 tests pass, purely against `policy/policy.rego` and `policy/promotions.json` — no Python, no
database, no agent involved yet. The two worth reading before moving on:

- `test_never_overrides_a_matching_promotion` — a promotion entry can never resurrect a
  `never`-class type, even if it matches the entry exactly.
- `test_auto_does_not_fire_with_empty_promotions` — the best possible case for `auto` (pod
  blast radius, reversible, confidence 0.999) still lands on `ask` while
  `policy/promotions.json` is `[]`.

## Step 2 — Classify a hypothetical action, no database

```bash
make policy-check TYPE=restart_pod BLAST_RADIUS=pod CONFIDENCE=0.95 REVERSIBLE=1
# ask   -- see Step 4 for why this isn't auto
make policy-check TYPE=delete_pvc BLAST_RADIUS=pod CONFIDENCE=0.99 REVERSIBLE=1
# never -- keyword match on the type, confidence is irrelevant
make policy-check TYPE=scale_deployment BLAST_RADIUS=deployment CONFIDENCE=0.6 REVERSIBLE=1
# ask   -- the default, for everything not otherwise classified
```

`kaval_agent.policy.classify()` is what runs underneath: it shells out to
`opa eval --format=json -I -d policy/ data.policy.decision`, feeding the action and confidence
as JSON on stdin, and maps the string OPA returns to `PolicyClass`.

## Step 3 — Diagnose a real incident and see it classified for real

```bash
make signals SCENARIO=oom-crashloop
make correlate
make diagnose INCIDENT=<the incident's id>
```

```
wrote proposal 3f2a... with 1 action(s): ask
```

`write_proposal` now calls `policy.classify()` per action instead of hardcoding `ask` — same
inputs (`blast_radius`, `reversible` from the action; `confidence` from the proposal), a real
answer instead of a stopgap.

## Step 4 — Prove "nothing is born auto," against the real file, not a mock

```bash
python -m kaval_agent.policy --type restart_pod --blast-radius pod --reversible --confidence 0.999
# ask
```

This is deliberately the best possible input for `auto` — the narrowest blast radius, reversible,
99.9% confidence — and it still classifies `ask`, because `policy/promotions.json` is `[]`.
`test_policy.py::test_nothing_is_born_auto` asserts exactly this against the real bundle on disk.

**What went wrong, and why (2026-09-29).** The first version of `policy.rego` referenced
`data.promotions.promoted`, and the first version of `policy_test.rego` proved the "nothing is
born auto" property by `with data.promotions as {...}` — Rego's test-mocking construct, which
creates the named data path for the test regardless of whether anything real lives there.  All
14 `opa test` cases passed. But `opa eval` against the *real* bundle showed the true path was
`data.auto_promotions`, not `data.promotions.promoted`: a JSON data file loaded from a bundle
root merges its own top-level keys directly under `data`, using neither the directory nor the
filename as a path segment — confirmed by querying `data` directly (`opa eval ... "data"` prints
the whole tree). The mocked test could not have caught this, because `with` sets the path it's
told to, real or not. It was `test_nothing_is_born_auto` — the pytest version, calling
`policy.classify()` against the unmocked file on disk — that would have caught it, and does now
that the path is fixed. **The lesson generalises past this one bug:** whenever a test claims a
component is unreachable or a value is absent, mocking the very thing whose absence you're
proving is the one shortcut that can't tell you if you got the real address wrong.

## Step 5 — See the promotion mechanism work, without touching production data

```bash
opa eval --format=json -I -d policy/ "data.policy.decision" <<'EOF'
{"action": {"type": "restart_pod", "target": "x", "blast_radius": "pod", "reversible": true}, "confidence": 0.95}
EOF
```

Still `ask`. Now open `policy/policy_test.rego` and read
`test_auto_fires_once_promoted` — it supplies a promotion list inline with `with
data.auto_promotions as [...]` and shows the identical input classifying `auto`. That test *is*
the demonstration of what a real promotion changes: one entry in `policy/promotions.json`, cited
to an ADR, and nothing else in the engine moves.

## Step 6 — Why the deployed image needs an environment variable this lab doesn't

If you check out `services/agent/kaval_agent/policy.py`, `DEFAULT_POLICY_DIR` is computed from
`__file__`, four `.parent`s up. That's correct here, on a laptop with an editable install —
`kaval_agent/policy.py -> kaval_agent -> agent -> services -> repo root`. It is **not** correct
inside the built agent image, where `pyproject.toml`'s hatchling build installs `kaval_agent`
flattened into site-packages rather than nested under `services/agent/`. CI's per-image check
(`.github/workflows/ci.yml`'s `images` job, the one that actually runs `opa eval` inside a
built container) caught this: every action silently fell back to `ask`, for the boring but
easy-to-miss reason that its policy directory didn't exist. `KAVAL_POLICY_DIR=/app/policy`, set
in the Dockerfile, is now checked first; the `__file__` walk is the fallback for exactly the
case this lab exercises. See ADR-0017 for the full account, and pytest's
`test_default_policy_dir_prefers_the_env_var` for the regression test.

---

## Done when

- [x] `opa test policy/ -v` — 14/14 pass
- [x] `make policy-check` classifies a denylisted type as `never` regardless of confidence
- [x] `make diagnose INCIDENT=<uuid>` writes actions with a real (non-hardcoded) `policy_class`
- [x] `python -m kaval_agent.policy --type restart_pod --blast-radius pod --reversible
      --confidence 0.999` returns `ask`, against the real, unmocked `policy/promotions.json`
- [x] `pytest services/agent/tests/test_policy.py -v` — 12/12 pass (skips instead of failing if
      `opa` isn't installed; CI sets `KAVAL_REQUIRE_OPA=1` so it can't skip there)
- [x] Existing `action` rows from Lab 11 (KAV-41, `policy_class=ask` stopgap) are unchanged —
      verified with a direct query against the dev-server database, not just by not writing code
      that touches them
