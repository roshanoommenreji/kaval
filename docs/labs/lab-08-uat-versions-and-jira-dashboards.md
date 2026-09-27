# Lab 08 — UAT, component versions and Jira dashboards

**Phase:** 2 · **Time:** ~45 min · **Cost:** $0 (Jira Free; no app to buy)

Three pieces of release management that a real team has and this project didn't:

- **User acceptance** ([ADR-0012](../adr/0012-user-acceptance-testing.md)): a feature a user
  touches is accepted by that user before it ships, and the acceptance is recorded.
- **Component versions** ([ADR-0013](../adr/0013-component-versions-and-release-naming.md)):
  each part of the system has its own version, and a release lists them: *Kaval 0.3.0: gateway
  1.2.0 · collector 0.4.1*.
- **Jira dashboards** that show all of it, including finished phases, built from a file in the
  repo so they can be rebuilt.

## Prerequisites

- [Lab 07](lab-07-commits-and-jira-link.md) done: commits carry their Jira key
- Admin on the Jira project, and `.env` holding `JIRA_SITE_URL`, `JIRA_EMAIL` and `JIRA_API_TOKEN`

---

## Step 1 — Two fields and a work type, in the Jira UI

Team-managed projects have no built-in Components, so a custom field does that job. In the
project: **⋯ → Space settings → Work types**.

1. **Bug** work type: **+ Add work type → Bug**, if it's missing.
2. **Service** field: open **Story**, drag a **Checkbox** field in, name it `Service`, and add the
   options `gateway`, `collector`, `agent`, `executor`, `mobile`, `inference`, `shared` and
   `infra`. Add it to **Task** and **Bug** too.
   - Use **Checkbox**, because a story can change two services.
   - Name it **Service**, not "Component": Jira already has a system field called "Component/s",
     and searches would be ambiguous.
3. **Found in** field: on **Bug** only, a **Dropdown** with `dev`, `staging` and `prod`. It
   records where each defect was caught.

Check them from a terminal. Jira makes one copy of the field per work type, which is why the
scripts look it up by name and never hardcode an id:

```bash
python - <<'EOF'
import sys; sys.path.insert(0, "scripts/tracking")
from atlassian import call
_, b = call("GET", "/rest/api/3/field")
print([(f["id"], f["name"]) for f in b if f["name"] in ("Service", "Found in")])
EOF
```

## Step 2 — Fill Service on past stories, from their code

Nobody should tick fifty checkboxes by hand. The commits already say which folders each story
changed:

```bash
python scripts/tracking/jira-sync.py backfill-service           # dry run: what it would set
python scripts/tracking/jira-sync.py backfill-service --apply   # then write it
```

```
  KAV-21  would set ['shared']
  KAV-22  would set ['gateway', 'infra']
  KAV-24  would set ['collector', 'gateway', 'infra']
  KAV-25  skip   (no service code)
```

- **How folders map to services** is set in `scripts/tracking/jira_adf.py` (`SERVICE_PATHS`), for
  example `services/gateway/` → gateway and `migrations/` → shared.
- **Empty placeholder files** (`.gitkeep`) don't count.
- **It only fills empty fields**, so a person's choice is never overwritten.

The first dry run here said KAV-21 (the data model) touched **infra**. That turned out to be an
empty placeholder folder moved in the same commit, which is why placeholders are now skipped.
**Read a dry run before you apply it.**

## Step 3 — A UAT story, end to end

A story needs UAT when it changes what an operator sees or decides. Write its **UAT scenarios**
before the work starts:

```bash
python scripts/tracking/jira-sync.py create --epic KAV-7 --points 1 --service gateway \
  --summary "TRIAL — UAT flow" --context "Throwaway story for this lab." \
  --ac "developer-checked criterion" \
  --uat "Given the trial, when I test it, then it behaves"
```

The story gets the `uat` label, and a **UAT scenarios** checklist separate from the
Acceptance Criteria. Now walk it through:

```bash
J=scripts/tracking/jira-sync.py
python $J tick KAV-n                       # ticks the acceptance criteria only
python $J uat KAV-n pass --env dev --note "x"
#   KAV-n is 'To Do'; UAT verdicts are given In Staging        <- refused: not in UAT yet
python $J transition KAV-n "In Staging"
python $J uat KAV-n fail --env dev --note "the button did nothing"
#   raised KAV-m  ...                      <- a Bug: uat-defect, Service copied, current sprint
#   KAV-n -> In Progress
python $J transition KAV-n "In Staging"
python $J uat KAV-n pass --env dev --note "retest"
#   KAV-n can't pass UAT while its defects are open: ['KAV-m']  <- refused again
python $J transition KAV-m Done
python $J uat KAV-n pass --env dev --note "defect fixed, both scenarios checked"
#   KAV-n -> Ready for Prod
```

Open the story in Jira. The comments read "UAT failed on dev by …, date: …", then "UAT passed on
dev by …". The UAT scenarios are ticked; the acceptance criteria were ticked separately. Delete the
trial story and its Bug when you're done.

**Why a command, not a comment:** the command refuses the cases that shouldn't pass (not in
UAT, open defect, no scenarios, no `uat` label), and every verdict takes the same queryable
shape. A typed "LGTM" does neither.

## Step 4 — Component versions

Each service carries `__version__`, and three places report it:

```bash
grep __version__ services/*/kaval_*/__init__.py
curl -s localhost:8000/healthz | python -m json.tool | grep version      # via make dev-tunnel
docker run --rm kaval/collector:ci --version
docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.version"}}' kaval/gateway:ci
```

CI's images job reads the version from the code, stamps it on the image, and fails if the label
and the code inside disagree:

```
label=0.1.0 code=0.1.0 want=0.1.0
```

Everything stays at `0.1.0` until the first release in Phase 4, when `release.yml` bumps each
component from the commits that touched its folder.

## Step 5 — The dashboards, from a file

```bash
make jira-dashboards      # or: python scripts/tracking/jira-dashboards.py
```

`scripts/tracking/jira-dashboards.toml` declares 10 filters and 3 dashboards. The first run
creates them; **run it again** and every line says `unchanged`. That's the proof it's safe to
re-run after editing the file.

| Dashboard | Shows |
|---|---|
| **Kaval — Delivery** | Every phase's stories by status, including finished phases, which Jira's Summary page hides; the current sprint; work by service |
| **Kaval — UAT** | Awaiting your acceptance, open UAT defects, signed off, UAT by service |
| **Kaval — Releases** | Stories going into the next release, released ones, flagged work. Empty until Phase 4 |

Gadget settings (which filter, which axes) are written as the dashboard item's properties, the
same way the Jira UI stores them. That part of Jira's API is thinly documented. So the dashboards
story was itself a `uat` story, and it was signed off only after someone looked at each dashboard.

---

## Done when

- [ ] **Service** on Story/Task/Bug and **Found in** on Bug exist, and `Service = gateway` finds stories
- [ ] `backfill-service` dry run read, then applied
- [ ] A trial `uat` story failed (Bug raised) and then passed, and each refusal was seen once
- [ ] `/healthz`, `--version` and the image label all say the same version
- [ ] `make jira-dashboards` twice: the second run is all `unchanged`
- [ ] The three dashboards render, and the dashboards story is signed off with
      `jira-sync.py uat KAV-36 pass --env jira`

## What went wrong, and why (2026-09-28)

- **A control byte broke the whole workflow.** An edit turned the `\1` in a `sed` command into the
  raw byte `0x01`. GitHub then rejected the entire `ci.yml` ("workflow file issue"), so the PR
  showed **no checks at all**, not failing ones. A PR with no checks isn't green. Parsing the YAML
  locally (`python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`) found it.
- **Jira created three Service fields.** Each work type got its own copy. JQL `Service = gateway`
  searches all of them, but the API needs the right id per work type, so the scripts look it up
  by name.
- **The first backfill dry run was wrong for one story.** A moved placeholder file counted as a
  change to infra. Reading the dry run before applying caught it.
