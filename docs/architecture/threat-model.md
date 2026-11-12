# Kaval threat model (STRIDE)

**Written:** 2026-11-01 · **Story:** `KAV-112` · **Decision:** [ADR-0072](../adr/0072-a-written-threat-model-and-a-prompt-injection-set.md) · **Lab:** [Lab 72](../labs/lab-72-the-threat-model-and-the-prompt-injection-set.md)

A threat model answers four questions: what are we protecting, who might attack it, how, and what
stops them. This one is for the whole of Kaval, with most of the weight on the unusual part: **an
AI agent that reads text an outsider can influence**.

## In plain words

- Kaval watches a cluster, asks a small AI model what went wrong, and proposes a fix. A person
  approves it. Only then does a separate part (the executor) change anything.
- The text the AI reads (error messages, a pod's name) can be written by whoever controls a
  workload. So an attacker can **put words in front of the AI**. They cannot give it tools, because
  it has none: its only output is a suggestion in a table.
- That design holds up well. Seven separate layers sit between planted text and a changed cluster,
  and the tests in this change prove the ones that are plain code, without needing the model.
- The review also found **real gaps**, listed in [What is not covered yet](#what-is-not-covered-yet).
  The serious one: the "record an approval" route has no login, and nothing in the chart limits who
  inside the cluster can call it (`KAV-115`).

## What is protected

| Asset | Why it matters |
|---|---|
| The cluster and the AWS account | The only things Kaval can damage. Reached only through the executor. |
| The approval decision | The human-in-the-loop promise. A forged approval defeats it. |
| The database | The audit trail; also the only channel from the agent to everything else. |
| Secrets (database passwords, Slack tokens, the AWS login) | Outside git, in the node's parameter store and cluster Secrets. |
| The bill | A $50 ceiling; a spending brake stops the servers at $48 ([ADR-0064](../adr/0064-the-spending-brake-names-every-server-group-and-is-proven-on-staging.md)). |
| The repository and what it builds | Public since 2026-09-30. What `main` says is what runs. |

## Who might attack

1. **A stranger** reading the public repository.
2. **Someone who can put text into telemetry:** the owner of a workload, anyone who can make a pod
   crash with a chosen message or choose its name. *This is the actor the rest of the document is
   built around.*
3. **Something already inside the cluster:** a compromised pod that can talk to other pods.
4. **A member of the Slack channel.**
5. **A malicious contributor or dependency.**
6. **The model itself:** not an attacker, but it can be wrong or be fooled, and is treated as untrusted.

## How data moves, and where trust changes

```
 1. repo / CI / registry ──▶ Flux ──▶ cluster                       (supply chain)
                                      │
 2. workloads (UNTRUSTED TEXT) ──▶ collector ──▶ database            (signals)
                                                    │
 3.                              agent ◀────────────┘   builds the model's input from:
                                  │                      signal text, runbooks, past incidents,
                                  ▼                      recent commit subjects
                              model (local, or Bedrock)    no tools, no secrets, text in / JSON out
                                  │
                       reply checked: shape, fix list, target ──▶ policy ──▶ proposal row
                                                                                │
 4.            Slack card (model-written summary) ◀─────────────────────────────┘
                   │  a person clicks Approve
                   ▼
              gateway ──▶ decision row
                                │
 5.            executor ◀───────┘  re-checks policy, then one of three Kubernetes calls
                   │              (or hands an order to the removal worker)
                   ▼
              cluster (one namespace)            AWS (only through the removal worker)

 6. the operator's laptop ──▶ AWS (admin)                            (operations)
```

The trust changes at: **2** (anyone who controls a workload writes here), **3** (the model's output
is untrusted), **4** (a person reads model-written text), **5** (the one place that mutates).

## How to read the tables

**S**poofing · **T**ampering · **R**epudiation · **I**nformation disclosure · **D**enial of service ·
**E**levation of privilege. *Status:* **Covered** (a control exists and something proves it),
**Partly** (a control exists but not all of the threat), **Gap** (not controlled; names the story).
The IDs (`T-AGENT-1`) are quoted by the tests, and `scripts/dev/test_threat_model.py` fails if a
test cites an ID that is not in this file.

## 2 and 3: untrusted text, the agent and the model

| ID | Threat | What stops it | Proof | Status |
|---|---|---|---|---|
| T-AGENT-1 | **T/E.** Planted text changes which fixes the model is offered, or makes it think the list says something else. | The list is built by code from the incident's *cause* and is the last thing the model reads; the cause is named by the correlator, not by the model. | `services/agent/tests/test_injection.py` (hostile text, 8 payloads, list unchanged and last); [ADR-0054](../adr/0054-telling-the-model-which-fixes-exist.md) | Covered |
| T-AGENT-2 | **T.** Planted text chooses the cause. | Only two switches exist: the phrase `exec format error` in a message, and a reason of `OOMKilled`. Either only picks between the three supported fixes, for the incident's own workload, and a person still approves. Reach: choose which of three fixes is proposed for a pod the attacker can already crash. | `test_text_has_two_switches_and_both_only_choose_between_supported_fixes`; `services/agent/kaval_agent/correlate.py` | Covered (accepted) |
| T-AGENT-3 | **E.** The model obeys the text and proposes something destructive (delete a namespace, end an EC2 server, a different target). | Seven layers, each tested alone: the reply format Ollama decodes against (one fix type, one target); a second check of the parsed reply; the answer-format model (extra fields refused); the policy's `never` list; a human; the executor's own re-check; Kubernetes RBAC. The first live run proved it: the model *was* fooled, the policy caught it ([ADR-0018](../adr/0018-eval-harness-and-golden-incidents.md)). | `test_injection.py` (types, targets, counts), `test_narrowing.py`, `test_injection_policy.py`, `evals/injection.py` | Covered |
| T-AGENT-4 | **T.** A newline in a signal's text starts a line that looks like one of the message's own headers (`RUNBOOKS:`), forging a section. | Covered (in code since 2026-11-10, `KAV-113`, [ADR-0083](../adr/0083-a-signals-text-is-flattened-and-capped-before-the-model-reads-it.md); passed staging in v0.3.0 and promoted 2026-11-10; production ran this build on 2026-11-10 with no errors, but had no incident to show it on): every message the model is shown is flattened to one line first, so no line of it can start like a heading. The real fix list is also last and the reply is checked afterwards. | `test_a_signal_message_cannot_forge_a_section_header`, `test_signal_text.py` | Covered |
| T-AGENT-5 | **D.** One very long message fills the model's input and burns time. | Covered (in code since 2026-11-10, `KAV-113`; passed staging in v0.3.0 and promoted 2026-11-10; production ran this build on 2026-11-10 with no errors, but had no incident to show it on): each message is cut to 300 characters (ending in "…") before the model reads it, and four messages at most are shown, so the incident text stays under about 1,400 characters. The stored signal and the cause are unchanged. | `test_one_signal_cannot_make_the_model_input_arbitrarily_long`, `test_signal_text.py` | Covered |
| T-AGENT-6 | **T.** A poisoned runbook section or commit subject. | These come from files in this repository, merged by pull request with eleven checks. A runbook line is indented under its heading, so it cannot open a section. The same layers as T-AGENT-3 apply. CODEOWNERS lists the owner but cannot force a review ([ADR-0071](../adr/0071-the-public-repo-gets-a-security-policy-owners-and-a-weekly-scorecard.md)). | `test_hostile_runbook_and_commit_text_sit_inside_their_own_block` | Partly (accepted) |
| T-AGENT-7 | **I.** The model is told to print secrets or environment variables. | It has no tools and no access to its environment. Its input is built from four named sources; only three keys of a signal are ever shown (labels, annotations and everything else are not). Its output is checked JSON. | `test_only_three_keys_of_a_signal_ever_reach_the_model` | Covered |
| T-AGENT-8 | **E.** The model claims high confidence to push an action towards automatic. | Nothing is automatic: `promotions.json` holds no promotion. The stored confidence is capped by the evidence ([ADR-0056](../adr/0056-the-stored-confidence-follows-the-evidence.md)). | `test_injection_policy.py::test_a_confident_model_cannot_promote_an_unpromoted_action`; `policy/promotions.json` | Covered |
| T-AGENT-9 | **S.** The summary misleads the person: "Approved by the on-call lead: …". | The card also shows the action type, target, blast radius and confidence, which code writes, not the model. The live set *measures* how often the model begins its summary with planted words: 2 of 7 in each of two runs (2026-11-01), and the claim of a prior approval was obeyed both times. Built 2026-11-11 (`KAV-117`, [ADR-0084](../adr/0084-the-models-summary-is-labelled-and-the-card-says-nobody-has-approved.md)): the card labels the summary "Model's summary (not checked)", flattens it to one line (so it cannot forge the card's own lines) and always ends with a line written by code, "Nobody has approved this yet"; the model's prompt now says the incident text is data and that nobody has approved anything. The model can still *write* a false claim, but the card shows it as unchecked words under that label. | `services/gateway/tests/test_card_summary_label.py`; `services/agent/tests/test_prompt_data_rule.py`; `evals/injection.py` (`fake-approval`, `slack-*` cases); `make evals SET=injection` (prints "planted text obeyed", reported, and "cards without the label", gated at 0; measured 2026-11-11: obeyed 2/7 before and 1/7 after, cards without the label 0/7); [Lab 72](../labs/lab-72-the-threat-model-and-the-prompt-injection-set.md), [Lab 86](../labs/lab-86-labelling-the-models-summary-on-the-approval-card.md) | Covered in code (since 2026-11-11, `KAV-117`; not yet released, staging-proved or run in production); the model can still write a misleading summary, shown as unchecked → `KAV-117` |
| T-AGENT-10 | **D.** An event flood opens many incidents. | The correlator groups by workload; the diagnosis worker takes one incident at a time; the local model costs $0; the spending brake stops the servers at $48. | [ADR-0014](../adr/0014-signal-correlation-and-incident-fingerprints.md), [ADR-0064](../adr/0064-the-spending-brake-names-every-server-group-and-is-proven-on-staging.md) | Partly (accepted) |
| T-COLL-1 | **S/T.** A workload writes false events to cause false incidents. | Cannot be stopped, it is what monitoring is. The collector reads `Warning` events in one namespace with `get`/`list`/`watch` on events and `get` on pods, nothing else. The effect is bounded by T-AGENT-1 to 3. | `deploy/charts/kaval/templates/collector.yaml` | Covered (accepted) |

## 4: the person, Slack and the gateway

| ID | Threat | What stops it | Proof | Status |
|---|---|---|---|---|
| T-SLACK-1 | **T.** Model-written text changes what Approve approves. | The buttons carry the action's own id, written by code. Labels are fixed plain text. | `services/gateway/tests/test_injection_slack.py` | Covered |
| T-SLACK-2 | **S/T.** Model-written text becomes Slack markup: `<!channel>` pings everyone; `<url\|Approve all fixes>` draws a link under any label; `<@user>` mentions a person. Slack treats `<`, `>` and `&` as control characters ([its formatting rules](https://docs.slack.dev/messaging/formatting-message-text/)). | **Built 2026-11-09 (`KAV-114`, [ADR-0082](../adr/0082-text-the-model-wrote-is-escaped-before-it-goes-into-slack.md)):** the summary, action type, target, "what will happen" lines and health-alert names are escaped (`&amp;`, `&lt;`, `&gt;`) from one helper, `slack_text.py`, in the card, the notification line and the reminder, escalation and expiry messages. | `test_model_written_text_cannot_become_slack_markup` (six cases, now ordinary passing tests) and the rest of `test_injection_slack.py`; `make evals SET=injection` prints and gates "cards with live Slack markup: 0/7" (seven cases plant a marker). Live: the model did not write `<!channel>` or the fake link in either 2026-11-01 run. | Covered (in code since 2026-11-09, `KAV-114`; passed staging in v0.3.0 and promoted 2026-11-10; production ran this build on 2026-11-10 and its Slack notifier ran with no errors; the first real card in Slack still waits for a real proposal, because staging has no Slack and production had none) |
| T-APPROVE-1 | **S/E.** Anything that can reach the gateway can record an approval. Was: `POST /v1/actions/{id}/decisions` had no authentication and the actor was free text. | **Login built (`KAV-115`, [ADR-0073](../adr/0073-the-approval-route-gets-a-login-and-the-namespace-gets-network-rules.md)):** the call needs a bearer token from a Secret, compared in constant time, checked before the database or the body is read, never logged, shut (503) when none is configured; the actor is stored as `api:<name>` so it cannot pose as `slack:`. The token's home is built (Parameter Store, rebuilt into the Secret on every boot; [runbook](../runbooks/approval-refused.md)). The chart now carries network rules (`templates/networkpolicy.yaml`, on for staging and production): the gateway's port accepts the poller and no other pod. An alert (`KavalApprovalRefused`) tells a person when the login is being guessed at. **Proved live on staging 2026-11-01** ([Lab 73](../labs/lab-73-a-login-on-the-approval-route.md), Part 6): a demo-namespace pod is refused, the login answers 401 / 401 / 404 for no, wrong and real token, the alert fires, the token is never logged. Production runs it from its next promotion; until its token Secret exists its route is shut (503). Bounded as before: a `never` action cannot be approved; the executor re-checks; the fixes are three, aimed at the incident's own workload; an approval is good for 60 minutes ([ADR-0048](../adr/0048-approvals-nobody-answers-expiry-and-escalation.md)). | `services/gateway/tests/test_approver_auth.py` (30 tests); `scripts/dev/test_chart_approver.py`; `scripts/dev/test_approver_token_bootstrap.py`; `scripts/dev/test_chart_netpol.py` | Covered (proved on staging; production on promotion) → `KAV-115` |
| T-APPROVE-2 | **E.** Any member of the Slack channel can press Approve; there is no list of approvers. | The workspace has one person. Whether the channel is private is a Slack setting, not something this repository can show. | `services/gateway/kaval_gateway/slack_chatops.py` | Partly (accepted for a one-person workspace; revisit with `KAV-115`) |
| T-APPROVE-3 | **E.** A stale approval is used later. | An approval expires; the executor refuses one older than the limit; a request nobody answered is final. | [ADR-0048](../adr/0048-approvals-nobody-answers-expiry-and-escalation.md); `services/executor/kaval_executor/executor.py` | Covered |
| T-APPROVE-4 | **R/T.** A verdict is changed after the fact. | Decisions carry actor and time and are written once: a second verdict on an action is refused by a unique constraint. **Built 2026-11-11 (`KAV-116`, [ADR-0085](../adr/0085-the-gateways-database-login-can-record-a-decision-but-never-change-it.md)):** the gateway's database login can record and read a decision and no longer change or delete one; the permissions file takes the right back on every run, so a database that already had it loses it. A test starts from the old state to prove that, and another lists every login's rights table by table. | `scripts/ops/db-roles.sql`; `services/shared/tests/test_db_roles.py` | Partly → `KAV-116`: in the file and tested; stays listed until staging and production have applied it (production at its next start-up) |
| T-SLACK-3 | **I.** The Slack tokens are stolen. | Kept outside git. No rotation schedule yet. | ROADMAP Phase 8 line (secret rotation) | Partly → ROADMAP line |

## 5: the executor and the cloud

| ID | Threat | What stops it | Proof | Status |
|---|---|---|---|---|
| T-EXEC-1 | **E.** What the model wrote in `params` reaches a cluster call. | Every handler is called with the target and nothing else. A handler that grows a second parameter fails a test. | `test_every_executor_handler_takes_the_target_and_nothing_else` | Covered |
| T-EXEC-2 | **T/E.** A row with a wrong stored class (a bug, or a compromised writer) is acted on. | The executor re-classifies from the action's own fields and the incident's cause; it never trusts the stored class. An action with no handler is skipped and recorded. | `services/executor/kaval_executor/executor.py`; `services/executor/tests/test_executor_selection.py`; `test_an_action_type_with_no_handler_cannot_run_even_if_it_is_approved` | Covered |
| T-EXEC-3 | **E.** A compromised executor reaches further than the three fixes. | A Role in one namespace: pods `get`/`delete`, deployments `get`/`patch`, replicasets `get`/`list`, one named ConfigMap. No Secrets, no cluster-wide rights. The memory fix has a ceiling of 512 MiB. | `deploy/charts/kaval/templates/executor.yaml`; `services/executor/kaval_executor/k8s.py` | Covered |
| T-EXEC-4 | **E.** The model orders a deletion in AWS. | Only the rule-based removal planner can: the proposal's model must be the planner, the incident must be a removable cause, and the target must be an AWS id of the right kind. The executor holds no AWS login. | `services/executor/kaval_executor/removal.py`; `test_a_removal_ordered_by_the_model_is_refused`; `test_a_removal_step_is_never_on_the_list_the_model_is_shown` | Covered |
| T-EXEC-5 | **I.** A secret ends up in the execution log the gateway shows. | Command output is redacted before it is stored. | `services/executor/kaval_executor/redact.py`; `services/executor/tests/test_redact.py` | Covered |
| T-EXEC-6 | **E.** A new executor action is added without anyone reviewing it. | The model's list and the executor's handlers must be identical, or CI fails. | `services/agent/tests/test_fixes.py` | Covered |
| T-DATA-1 | **E.** The agent writes something only the executor or a human may. | Separate database logins: the agent can read decisions and executions but not write them; only the gateway inserts a decision; only the executor writes an execution. | `scripts/ops/db-roles.sql` | Covered |

## 1 and 6: supply chain and operations

| ID | Threat | What stops it | Proof | Status |
|---|---|---|---|---|
| T-SUPPLY-1 | **T.** A tampered action or dependency in the pipeline. | Every third-party action pinned to a full commit; libraries installed by hash; Dependabot weekly; Trivy on images. Kaval's own code is packed by a tool that also comes from the lock by hash, then installed as a wheel (`KAV-111`); nothing in the build is fetched unchecked. | `scripts/dev/test_repo_hygiene.py`; `scripts/dev/test_pip_install_pins.py`; [ADR-0010](../adr/0010-ci-pipeline-and-supply-chain.md); [ADR-0086](../adr/0086-kavals-own-code-is-packed-by-a-hash-checked-tool-and-installed-as-a-wheel.md) | Partly → `KAV-111` (in the files and tested; stays until staging has built and passed it) |
| T-SUPPLY-2 | **T.** Images are not signed and nothing verifies a signature before deploy. | Build once, promote the digest that passed staging; the promotion guard blocks hand edits. Since 2026-11-13 (`KAV-137`) every image also has a parts list (SBOM) and a signed build record naming the release workflow and commit; nothing checks them yet. Since `KAV-138` (built 2026-11-13) the release also signs each image keyless in the registry, checks the signature straight away, and binds the parts list to the digest; no promotion or cluster check refuses an unsigned image yet. | [ADR-0032](../adr/0032-promote-workflow-and-the-promotion-guard.md); [ADR-0089](../adr/0089-every-image-gets-a-parts-list-and-a-signed-build-record.md); [ADR-0090](../adr/0090-images-are-signed-keyless-and-the-signature-lives-in-the-registry.md); `scripts/dev/test_release_supply_chain.py`; ROADMAP Phase 8 supply-chain line | Partly (parts list and build record proven live 2026-11-13; signing built 2026-11-13, live proof pending) → `KAV-139`, `KAV-140` |
| T-SUPPLY-3 | **T.** An unreviewed change reaches `main`. | A pull request with eleven required checks. Administrators can bypass and there is no second reviewer (one person), recorded as accepted. | [ADR-0071](../adr/0071-the-public-repo-gets-a-security-policy-owners-and-a-weekly-scorecard.md) | Partly (accepted) |
| T-SUPPLY-4 | **I.** A secret enters git. | gitleaks before commit and over the full history in CI; the repository has a security policy and private reporting. | `.github/SECURITY.md`; `.github/workflows/ci.yml` | Covered |
| T-SUPPLY-5 | **T.** No static code analysis runs. Was: nothing read the code for known kinds of security mistake (Scorecard's SAST check scored 0). | CodeQL scans the Python and the workflow files on every pull request, after each merge and every Monday, and files what it finds in the Security tab. It is advice, not a gate: it becomes a required check after a clean week. | `.github/workflows/codeql.yml`; `scripts/dev/test_codeql_workflow.py`; [ADR-0080](../adr/0080-codeql-scans-every-pull-request-and-starts-as-advice.md); [Lab 81](../labs/lab-81-turning-on-codeql-and-reading-what-it-says.md) | Partly (advice until it is required, ADR-0080) → built 2026-11-07 (`KAV-110`) |
| T-OPS-1 | **E.** The operator's AWS login is stolen or too broad. | The laptop is the only admin path; no inbound ports on the nodes (Session Manager only). A least-privilege review with IAM Access Analyzer is not done. | ROADMAP Phase 8 IAM line | **Gap** → ROADMAP line |
| T-OPS-2 | **D.** Runaway spend, caused by an attacker or a bug. | Alerts at $42 and $46; a hard stop at $48 that scales the servers to zero. | [ADR-0064](../adr/0064-the-spending-brake-names-every-server-group-and-is-proven-on-staging.md) | Covered |
| T-OPS-3 | **D.** Database lost or corrupted. | Snapshots, a monthly restore drill. | `docs/runbooks/restore-drill.md`; `docs/drills/` | Covered |
| T-OPS-4 | **I.** A password is printed into a log. Was: `db-roles.sql` made `psql` print the four service-login passwords into the node's journal and boot log on every run (found on staging 2026-11-04; production's app server ran the same file). | `\gset` keeps each result out of the output; a test runs the real `psql` with fake passwords and fails if one is printed, another proves the roles still get the passwords, a third fails any new bare password line. The logs are readable by root and the `adm` group only and are shipped nowhere. | [ADR-0077](../adr/0077-the-database-roles-script-keeps-passwords-out-of-psqls-output.md); `services/shared/tests/test_db_roles_output.py` | Covered → built, tested and proved on staging 2026-11-05 (each password 0 times in the boot log and the journal), again on a second, resumed node 2026-11-06, and on production's new server 2026-11-07 (all five 0 times in its boot log and journal; `log_statement = none`) (`KAV-125`) |

## What is not covered yet

| Gap | Story | Size of the risk |
|---|---|---|
| The approval route has no login and the cluster has no network rules | `KAV-115` | The largest: it undermines "a human approves". Bounded today by what an approval can do. |
| Model-written text is live Slack markup | `KAV-114` | Fixed in code 2026-11-09 ([ADR-0082](../adr/0082-text-the-model-wrote-is-escaped-before-it-goes-into-slack.md)); passed staging and promoted in v0.3.0 on 2026-11-10 (Jira Done); stays listed until production has run it. |
| Signal text is not flattened or capped | `KAV-113` | Fixed in code 2026-11-10 ([ADR-0083](../adr/0083-a-signals-text-is-flattened-and-capped-before-the-model-reads-it.md)); passed staging and promoted in v0.3.0 on 2026-11-10 (Jira Done); stays listed until production has run it. |
| The gateway can update a decision | `KAV-116` | Fixed in the permissions file 2026-11-11 ([ADR-0085](../adr/0085-the-gateways-database-login-can-record-a-decision-but-never-change-it.md)), tested from a database that still had the old right; stays listed until production has applied it at its next start-up. Low until then: nothing uses the right, but the design says write-once. |
| The model's summary is shown as if it were reliable, and the model obeys "already approved" text | `KAV-117` | Fixed in code 2026-11-11 ([ADR-0084](../adr/0084-the-models-summary-is-labelled-and-the-card-says-nobody-has-approved.md)): the card labels the summary as unchecked and always says nobody has approved; the model can still obey (measured 2 of 7 in each earlier run; 2/7 before this change and 1/7 after in the 2026-11-11 run, which is within the model's own randomness, so the prompt sentence is not shown to help; cards without the label 0/7: [Lab 86](../labs/lab-86-labelling-the-models-summary-on-the-approval-card.md) Part 5). Stays listed until it has passed staging and production has run it. |
| Code scanning is advice, not a gate | [ADR-0080](../adr/0080-codeql-scans-every-pull-request-and-starts-as-advice.md) | Low: a finding is seen on the pull request and in the Security tab but does not block a merge until the scan becomes a required check. |
| Own code installed without a hash | `KAV-111` | Fixed in the Dockerfiles 2026-11-11 ([ADR-0086](../adr/0086-kavals-own-code-is-packed-by-a-hash-checked-tool-and-installed-as-a-wheel.md)): the packing tool comes from the lock by hash and the image installs a finished wheel; stays listed until staging has built and passed it. Low until then. |
| No signature check at the promotion gate or in the cluster; no IAM review; no secret rotation schedule | ROADMAP Phase 8 lines; `KAV-139`, `KAV-140` | Already planned. |

## Not in this model

Said so that the gap is a decision, not an oversight: the model weights themselves (Ollama's image
and the Gemma download) beyond "treat the model as untrusted"; vulnerabilities in Kubernetes, k3s or
the Linux kernel; compromise of the GitHub, Atlassian or Slack accounts themselves (two-factor and
account hygiene are the owner's); physical access.

## Keeping it true

- A new component, a new place text enters, or a new action type adds rows **in the same change**.
  The Definition of Done's documentation sweep names this file.
- A gap is closed by its story: the expected-failure test turns into a passing one, strict mode
  fails the run until the marker is removed, and this table's status moves to **Covered**.
- `scripts/dev/test_threat_model.py` keeps the file honest: every row has a status, every gap names a
  story or a ROADMAP line, every file named as proof exists, and every ID a test cites is here.
- The live measurement is `make evals SET=injection` on the dev server. Re-run it whenever the
  model, the prompt or the fix list changes.
