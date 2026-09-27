# Phase 1 — Local first

> **Written from:** experience (rewritten 2026-09-27, when the phase closed; `KAV-26`)
> **Labs:** [02 data model](../labs/lab-02-data-model.md) · [03 dev server](../labs/lab-03-aws-dev-server.md) · [04 Compose stack](../labs/lab-04-compose-stack.md) · [05 API and signals](../labs/lab-05-gateway-api-and-synthetic-signals.md) · [06 CI](../labs/lab-06-ci-pipeline.md) · [07 commits and Jira](../labs/lab-07-commits-and-jira-link.md)
> **Decisions:** [ADR-0007](../adr/0007-develop-on-an-aws-dev-server.md) dev server · [ADR-0008](../adr/0008-production-database-on-its-own-server.md) database server · [ADR-0009](../adr/0009-gateway-api-conventions.md) API · [ADR-0010](../adr/0010-ci-pipeline-and-supply-chain.md) CI · [ADR-0011](../adr/0011-commit-convention-and-jira-link.md) commits
> **Cost:** $0.13 of AWS usage in September, all covered by credits, so $0 billed. The dev server
> runs only during sessions and stops itself after an idle hour; planned at ~$5/month if used weekly

## Where this sits

Phase 0 made spending safe. This phase built every piece the agent will need, and proved the
pieces talk to each other before any of it has to survive a cluster.

It unlocks Phase 2. The agent loop now has:
- a database with the full audit spine;
- real-shaped signals to reason about;
- a measured choice of model;
- the API the phone will use;
- a pipeline that checks every change.

## What we did

A `docker-compose` stack on an AWS dev server (a `t4g.medium`, the production node's size),
driven from the laptop through a Docker context:

- **Postgres 16** (the `pgvector/pgvector` image) with the **7-table spine**: `signal`, `incident`,
  `proposal`, `action`, `decision`, `execution`, `outcome`. It's built as SQLAlchemy models and an
  Alembic migration that CI applies, checks for drift, reverses and re-applies (`KAV-21`)
- **Ollama** with four small models **measured side by side** on the real hardware: memory, speed,
  and whether each can produce a valid proposal
  ([model-shortlist.md](../architecture/model-shortlist.md)). `gemma3:1b-it-qat` became the
  default; plain `gemma3:1b` was dropped (`KAV-22`)
- **The gateway**: `/healthz`, which passes only when the database is migrated *and* the model is
  pulled, plus a read-only, versioned, cursor-paged `/v1` API for signals and incidents (`KAV-23`)
- **A synthetic signal generator**: four failure scenarios in the exact payload shapes real
  sources produce, every row flagged `synthetic` (`KAV-23`)
- **CI on every pull request**: lint, tests on a real Postgres, migrations, a secrets scan of the
  whole history, Terraform, and native arm64 images scanned by Trivy. Everything it uses is pinned
  against supply-chain attacks (`KAV-24`)
- **A delivery workflow**: a commit convention checked by a hook and by CI, and Jira linked to
  GitHub so each issue shows its commits, branches and PRs (`KAV-25`)

The exit gate, "a fake incident flows end-to-end and lands in the database", was met for signals:
generator → Postgres → `/v1` API, verified on the dev server. Grouping those signals into an
`incident` row is correlation, which is the first task of Phase 2.

## Why this way, and what changed on the way

**The plan was the laptop, and the argument for it was sound.** The hard part is getting an LLM to
produce something structured and trustworthy from messy telemetry, and that problem iterates
fastest close to hand. Starting "on AWS to make it real" front-loads networking and IAM while
you're still working out what the system does.

**Measurement changed it.** This phase had to answer three questions:
- How much memory does a model really take?
- How fast does it run on 2 Graviton cores?
- Does an arm64 image build and run?

A Windows laptop can't answer any of them. So the stack moved to a dev server the size of the
production node ([ADR-0007](../adr/0007-develop-on-an-aws-dev-server.md)). What kept it cheap and
small:
- it uses the default VPC and a single role, so no Phase 4 networking arrived early;
- access is only through Session Manager, with no open SSH port;
- it stops itself after an idle hour;
- the laptop stays the editor.

"Local" in this phase's name ended up meaning *your own environment*, wherever it runs.

The same instinct, to measure rather than assume, decided most of the phase:

| Assumed | Measured | What it changed |
|---|---|---|
| A 1B model takes ~900 MB | 1.1–1.3 GB loaded; Qwen3 1.7B takes 1.9 GB | Postgres moved off the app node to its own server ([ADR-0008](../adr/0008-production-database-on-its-own-server.md)), and the ceiling rose to $40 |
| Gemma 4 E2B fits the 4 GB node | The 4-bit file alone is 7.2 GB | Dropped before measuring. A name's "2B" is not a size |
| Asking for JSON gets JSON | Both Gemmas fenced it 9/9 times and dropped the safety fields 6/9 | The agent always decodes against the proposal's JSON schema, then validates with Pydantic |
| `docker stats` shows a model's memory | It counted file cache, ~0.7 GB too high | Memory is read from the processes' resident size |
| The 4-bit formats are interchangeable | `Q4_0` read prompts 3× faster than `Q4_K_M` on Graviton | The QAT build (`Q4_0`) is the default |
| `pip install` from `pyproject.toml` is reproducible | The first lockfile pulled SQLAlchemy 2.1 and broke type-checking | `uv.lock` with hashes, used by the laptop, CI and the images alike |
| A clean base image passes a scan | Trivy found 2 HIGH CVEs inside the base image's `setuptools` | Packaging tools are removed from the runtime images |
| Smart commits move Jira issues | Only if the commit email matches a Jira user | Link by key; status moves by script ([ADR-0011](../adr/0011-commit-convention-and-jira-link.md)) |

---

## Key concepts

### Containers are not small VMs

A virtual machine virtualises hardware and runs a full guest kernel. A container shares the host
kernel and is isolated by kernel features — namespaces (what a process can *see*: its own PIDs,
network, mounts) and cgroups (what it can *use*: CPU, memory, I/O).

Consequences that matter in practice:

- A container starts in milliseconds because there is no kernel to boot
- A container cannot run a different kernel — which is why a Linux container needs a Linux kernel, and why Docker Desktop on Windows quietly runs a Linux VM for you
- A container is not a security boundary of the same strength as a VM; the shared kernel is shared attack surface

### Images are layers, and layer order is a performance decision

An image is a stack of read-only filesystem layers plus metadata. Each Dockerfile instruction adds
a layer. A running container adds a thin writable layer on top.

Layers are cached and content-addressed. If a layer's inputs have not changed, it is reused.

This is why the conventional Dockerfile ordering exists:

The gateway's Dockerfile, simplified:

```dockerfile
COPY --from=lock /requirements.txt /tmp/requirements.txt
RUN pip install --require-hashes -r /tmp/requirements.txt   # cached until uv.lock changes
COPY services/ services/                                    # changes on every edit
RUN pip install --no-deps .
```

Reverse them and every source edit reinstalls every dependency. The rule is: **least
frequently changing first**. Two more things the real file does are worth copying:
- **A multi-stage build.** `uv` turns the lockfile into a hashed requirements list in a throwaway
  first stage, so the final image never contains `uv`.
- **Deleting what the running service never uses.** `pip`, `setuptools` and `wheel` come out, and
  a scanner finding went with them.

### docker-compose is a local orchestrator, not a deployment tool

Compose reads a YAML file describing services, networks and volumes, and runs them together on
one machine. Containers reach each other by service name — `postgres:5432` works because Compose
provides DNS on a shared network.

It is genuinely useful for local development and genuinely not a production tool: no scheduling,
no self-healing across machines, no rolling updates. Phase 3 replaces it with Kubernetes, and the
mental jump is smaller than it looks because the *concepts* — a set of services, networked, with
config and volumes — carry over. What Kubernetes adds is a controller that continuously works to
make reality match the description.

### What a language model file actually contains

A model is a large collection of floating-point numbers — the **weights** — organised into layers,
plus a **tokenizer** that maps text to integers and back.

Gemma 3 1B means roughly one billion parameters. At full 32-bit precision that would be about 4 GB.
Inference is a lot of matrix multiplication using those weights; no learning happens, nothing is
stored between requests. The model is a pure function from token sequence to next-token
probabilities.

That last point matters more than it sounds: **the model has no memory.** Anything it "knows"
about your cluster is text you put in the prompt this time. Conversation history is just previous
messages re-sent. This is why context construction, in Phase 2, is where the real engineering is.

### Quantization is why this runs on a 4 GB node at all

Quantization stores weights at lower precision — 4 bits instead of 32, typically. A `q4` Gemma 3
1B is an 815 MB download instead of 4 GB. Loaded, it takes more than the file: measured on the dev
server, **1.1–1.3 GB** for the 1B models and 1.9 GB for Qwen3 1.7B, because the context cache and
working buffers come on top ([model-shortlist.md](../architecture/model-shortlist.md)).

*Which* 4-bit format also matters, and it's measured, not theory. `gemma3:1b` (`Q4_K_M`) and
`gemma3:1b-it-qat` (`Q4_0`) are the same model. On the Graviton server the `Q4_0` build read
prompts **3× faster** and wrote 1.5× faster. The likely reason is that llama.cpp has ARM-optimised
kernels for `Q4_0`.

The trade is accuracy for size and speed. At 4-bit the quality loss on a 1B model is real but
modest, and for classifying known incident patterns it is acceptable. For novel reasoning it is
not, which is exactly why the architecture escalates hard cases to Bedrock rather than pretending
a quantized 1B model is enough.

Naming you will see: `q4_0`, `q4_K_M`, `q8_0`. The number is bits; the suffix describes the
scheme. `q4_K_M` is often called the quality/size sweet spot, and on x86 it usually is. On this ARM
hardware the plainer `q4_0` was faster. The general lesson: a format's reputation was
earned on someone else's hardware.

### Tokens and the context window

Models do not read characters or words. Text is split into **tokens** — subword fragments. English
averages roughly four characters per token, so 1,000 tokens is about 750 words. Code and JSON
tokenize less efficiently than prose.

The **context window** is the maximum number of tokens the model can attend to at once — prompt
plus generated output together. Exceed it and something must be dropped.

Two operational consequences:

- **Cost and latency scale with tokens**, which is why dumping raw logs into a prompt is both expensive and slow
- **Attention cost grows faster than linearly** with sequence length, so a longer prompt is not merely proportionally slower

This is the entire reason Phase 2 builds a *context builder* rather than concatenating everything
available.

### Why CPU inference is viable here

Generation is sequential: each token depends on the previous one. On a small quantized model the
bottleneck is memory bandwidth rather than raw compute. Measured on the 2-vCPU Graviton dev
server, the 1B–1.7B models generate **11–21 tokens/second**, and a whole diagnosis takes 9–12 s
once the model is loaded.

For a chat interface that is uncomfortably slow. For this system it is fine, because incident
diagnosis is **asynchronous** — a signal arrives, the agent thinks for about ten seconds, a proposal
appears. Nobody is watching a cursor blink.

Recognising when latency does and does not matter is what makes the $9/month node sufficient
instead of needing a $250/month GPU.

### Embeddings, and why vectors mean similarity

An **embedding** is a fixed-length vector of floats representing a piece of text, produced by a
model trained so that texts with similar meaning land near each other in that space.

"Pod terminated due to memory limit" and "container OOMKilled" share almost no words but sit close
together as vectors. Keyword search cannot connect them; vector search can.

Closeness is usually **cosine similarity** — the angle between vectors, ignoring magnitude — so
identical direction scores 1.0 and unrelated text scores near 0.

This is how the system finds prior incidents resembling the current one, which is what lets a
small model handle a case it has effectively seen before.

*Not exercised yet.* Phase 1 runs the `pgvector` image, but nothing stores an embedding so far.
The first vector column arrives with Phase 2's runbook retrieval, and this section will be
rewritten from that experience then.

### pgvector, and why not a dedicated vector database

`pgvector` adds a `vector` column type to Postgres, with distance operators and indexes (HNSW or
IVFFlat) for approximate nearest-neighbour search.

Dedicated vector databases exist and are faster at very large scale. This project has thousands of
rows, not millions. Using Postgres means one database, one backup, one connection pool, and joins
between an incident's embedding and its relational data in a single query — the incident, its
signals, its proposals and its outcome, together.

The general lesson is worth carrying: **adding a specialised datastore is a cost, and it should be
paid only when the general one has actually failed you.**

### Fake data should look real, and still be labelled fake

Everything downstream of the collector needs signals to work on long before a real cluster
exists. The synthetic generator (`KAV-23`) therefore writes each scenario as the exact burst
one real failure produces: a memory warning from Prometheus, a Kubernetes `Event` with
`reason: OOMKilled`, three `BackOff` events with a rising restart count, then an Alertmanager
alert. Same payload shapes the real sources use, so when they arrive nothing downstream changes.

Two properties make fake data safe to keep around. It is **labelled**: every row carries
`synthetic: true`, so metrics and production can exclude it and a fake can never be mistaken for
an outage. And it is **reproducible**: the same seed gives the same pods and numbers, which is
what lets an eval compare two models on identical input.

### An API is a contract, and paging is part of it

The gateway's endpoints are what the phone app will be built against, so their shape outlives the
code behind them. [ADR-0009](../adr/0009-gateway-api-conventions.md) fixes the rules first:

- **Versioned** under `/v1`, so a breaking change can live beside the old one.
- **Documented from the code.** FastAPI generates the OpenAPI description, so it can't drift.
- **Cursor paging, not page numbers.** On a table that grows while you read it, `?page=2` shifts
  every time a row arrives, so rows get skipped or shown twice. A cursor says "older than this exact
  row" (its timestamp plus its id, because two rows can share a timestamp) and can't slip.
- **Read-only enforced by the database.** Each request's transaction starts `READ ONLY`, so even
  a buggy endpoint can't change the audit trail. "The code doesn't write" is a promise;
  "Postgres refuses writes" is a guarantee.

### CI turns "it works on my machine" into a recorded fact

Continuous integration runs the same checks on a clean machine for every proposed change, and
records the result on the pull request. It's worth something for three reasons, and they're
separate:

- **A clean machine.** The laptop has packages, files and state that a fresh runner doesn't. CI
  finds what you forgot to commit, and anything that only worked because of your setup.
- **The same checks every time.** Nobody skips the slow test when they're in a hurry. The
  pipeline doesn't get tired.
- **A gate, not a report.** A check nobody has to pass is decoration. Here a PR merges only
  when every check is green ([contributing](../contributing.md)).

Two design rules follow. First, CI must be able to **fail** for the right reasons: a database test
that silently skips when the database is missing turns a broken pipeline green. That's why CI
sets `KAVAL_REQUIRE_DB=1`. Second, test against the real thing where it's cheap. A Postgres
service container costs seconds, and SQLite would pass tests that Postgres fails, because it has
no JSONB, no row comparison and no read-only transactions.

### Pinning: what a version number doesn't promise

`fastapi>=0.115` means "any version from 0.115 up", so each install can get different code.
A **lockfile** (`uv.lock`) records the exact version of every package, including the ones
installed only because something else needs them, and a hash of each file. Install from it and
the laptop, CI and the images all run byte-identical dependencies. The first lock in this repo
proved the point at once: it resolved SQLAlchemy 2.1, which broke the mypy setup the laptop's
older copy had been hiding.

The same idea applies at every layer, because every name that can be re-pointed is a way in:

| Thing | Movable name | Fixed name |
|---|---|---|
| Python package | `sqlalchemy>=2.0` | `uv.lock` version + sha256 |
| Container image | `python:3.11-slim-bookworm` | `…@sha256:a36c24f9…` digest |
| GitHub Action | `actions/checkout@v7` | `actions/checkout@3d3c42e5…` commit SHA |
| Downloaded tool | "latest release" | version + sha256 written in the workflow |

Pinning isn't freezing. Dependabot proposes updates weekly as ordinary pull requests, and CI
checks each one. Updates still arrive, but deliberately, one reviewed change at a time. That's
not paranoia: in March 2026, attackers moved 76 of `trivy-action`'s 77 version tags to
credential-stealing code, and every workflow that pinned by tag ran it.

A scanner like Trivy also finds what you didn't know you shipped. The first scan here flagged two
HIGH CVEs in packages this project never installed: old copies bundled inside `setuptools`, which
the base image ships. The fix wasn't to ignore the finding but to remove what the running service
never uses.

### A commit message is data, and a convention is only real if something checks it

`feat: KAV-25 commit-msg hook` carries three facts a machine can use: a **type** (release notes
group by it, and the version bump can be derived from it), a **key** (the tracker links the commit
to its issue), and a summary short enough for `git log --oneline`. Written by hand, a convention
decays within weeks. Checked, it holds. The check lives in two places, the `commit-msg` hook and
CI, but it's **one function called by both**, so the two can't disagree.

Connecting the tracker to the repository is what makes the key pay off. With GitHub for Atlassian (Atlassian's app), each
issue shows its branch, commits, pull request and CI result, and nobody links anything by hand.
Jira's *smart commits* (`KAV-25 #done`) would also move the issue, but only when the commit's
author email matches a Jira user. This repo commits under the GitHub noreply address, so the
public history never exposes a personal inbox. The commands would therefore be silently dropped,
and the check refuses them ([ADR-0011](../adr/0011-commit-convention-and-jira-link.md)). The
lesson is broader than Jira: read the vendor's conditions before building on a feature, because a
feature that fails silently looks exactly like one that works.

---

## Common mistakes

| Mistake | What it costs |
|---|---|
| `COPY . .` before installing dependencies | Full dependency reinstall on every source edit |
| Assuming the model remembers previous requests | Baffling behaviour; it is stateless, you resend context |
| Dumping raw logs into the prompt | Slow, expensive, and worse answers — noise crowds out signal |
| Reaching for a vector database at thousands of rows | A second datastore to run, back up and reason about, for no gain |
| Testing only with tidy synthetic signals | The real system meets malformed, duplicated and out-of-order events |
| Building the image on your laptop and assuming it runs on the node | Phase 4's arm64 lesson, learned late |
| Judging a model's fit by its name ("E2B", "1B") | Gemma 4 E2B is a 7.2 GB download; it could never fit the 4 GB node. Check the file size |
| Asking a small model for JSON and trusting it | Gemma returned fenced JSON 9/9 times and dropped the safety fields 6/9. Constrain decoding to a schema, then validate |
| Measuring model memory with `docker stats` | It counts file cache too and overstated every model by ~0.7 GB. Read the processes' resident memory |
| Using a bind mount with a remote Docker context | The path resolves on the server, not the laptop; the container quietly sees an empty folder |
| Paging a growing table with `?page=N` | Rows skipped or repeated as new ones arrive. Use a cursor with a tie-breaker |
| Enforcing "read-only" only in application code | One bug writes to the audit trail. Make the database refuse it (`READ ONLY` transaction, then a read-only role) |
| Letting fake data look exactly like real data | A test run is mistaken for an outage, or pollutes a metric. Flag every fake row |
| Assuming a closed SSH client closes the server's connection | Through Session Manager it doesn't: 42 connections leaked in 20 minutes. `ClientAliveInterval` makes the server check |
| Pinning GitHub Actions by tag (`@v4`) | A tag can be moved to malicious code; pin the commit SHA and let Dependabot bump it |
| Letting CI skip tests it can't run | A missing database turns a green run into a lie. Make CI fail where the laptop would skip |
| Installing from `pyproject.toml` ranges instead of a lock | The laptop, CI and the image each get different versions; the bug appears in only one of them |
| Committing a Terraform lock generated on one OS | CI on Linux can't verify the provider. `terraform providers lock -platform=…` for every OS in use |
| Silencing a scanner finding instead of fixing it | The vulnerable package stays. Often the fix is removing something the image never needed |
| Enforcing a commit convention only with a local hook | Hooks aren't versioned; a fresh clone skips them. CI has to check the same rules |
| Writing the hook's rules and CI's rules separately | They drift, and a message passes one and fails the other. Call one checker from both |
| Using smart-commit commands without checking the email match | `#done` is silently ignored, and the board lags while everyone believes it moved |
| Committing with a personal email to a repo that will go public | The address is in every commit for good; removing it means rewriting history |

## Glossary

| Term | Meaning |
|---|---|
| **Container** | An isolated process using the host kernel, bounded by namespaces and cgroups |
| **Namespace** | Kernel feature controlling what a process can see |
| **cgroup** | Kernel feature controlling what resources a process can use |
| **Image** | A stack of read-only filesystem layers plus metadata |
| **Layer** | One filesystem diff in an image; cached by content |
| **Registry** | Where images are stored and pulled from |
| **docker-compose** | Runs a multi-container application on one machine from a YAML description |
| **Weights** | The learned parameters of a model |
| **Parameter** | One number in the model; "1B" means a billion of them |
| **Tokenizer** | Maps text to integer tokens and back |
| **Token** | A subword unit; roughly four characters of English |
| **Context window** | Maximum tokens the model can attend to at once, prompt plus output |
| **Quantization** | Storing weights at reduced precision to shrink size and speed inference |
| **Inference** | Running a trained model to get output; no learning occurs |
| **Ollama** | Local model server exposing an OpenAI-compatible API |
| **Constrained decoding** | Restricting generation so the output can only match a given grammar or JSON schema (Ollama's `format`) |
| **QAT** | Quantisation-aware training: the model is trained knowing it will be stored at low precision, so it loses less |
| **Docker context** | A named Docker endpoint; `kaval-devbox` sends every command to the dev server over SSH |
| **Embedding** | A fixed-length vector representing text meaning |
| **Cosine similarity** | Angle-based closeness between vectors; 1.0 is identical direction |
| **Vector search** | Finding nearest neighbours in embedding space |
| **pgvector** | Postgres extension adding vector types, operators and indexes |
| **HNSW** | A graph index for fast approximate nearest-neighbour search |
| **Stateless** | Retains nothing between requests |
| **Synthetic signal** | A fake observation in a real payload shape, flagged `synthetic: true` |
| **OpenAPI** | A machine-readable description of an HTTP API; FastAPI generates it at `/openapi.json` and renders it at `/docs` |
| **Cursor pagination** | Paging by "rows after this one" instead of by page number; stable while rows are added |
| **Read-only transaction** | `SET TRANSACTION READ ONLY`: Postgres rejects any write inside it |
| **CI** | Continuous integration: automated checks on a clean machine for every proposed change |
| **Pull request (PR)** | A proposed merge of a branch into `main`, where CI results and review are recorded |
| **Service container** | A container GitHub Actions starts next to a job, e.g. a real Postgres for the tests |
| **Lockfile** | Exact versions and hashes of every dependency, including transitive ones (`uv.lock`) |
| **Transitive dependency** | A package you get because one of your dependencies needs it |
| **Digest** | The sha256 of an image manifest; unlike a tag, it can't be re-pointed |
| **SHA pinning** | Referencing an action by full commit hash instead of by tag |
| **Supply-chain attack** | Compromising something you depend on (a package, an action, an image) instead of you |
| **CVE** | A public identifier for a known vulnerability, e.g. CVE-2026-24049 |
| **Dependabot** | GitHub's bot that opens PRs to update pinned dependencies |
| **Conventional commits** | A message format, `type(scope): summary`, that makes history machine-readable |
| **commit-msg hook** | A Git hook that sees the message before the commit is created, and can refuse it |
| **Smart commit** | A Jira command inside a commit message (`#comment`, `#time`, `#done`); runs only if the author email matches a Jira user |
| **Development panel** | The part of a Jira issue listing its linked branches, commits, PRs and builds |
| **Noreply email** | GitHub's `<id>+<user>@users.noreply.github.com` address, used as the commit author to keep a real inbox private |

## Check yourself

1. Why can't a Linux container run on Windows without a virtual machine somewhere?
2. Your Dockerfile reinstalls every dependency on each build. What is almost certainly wrong?
3. The model gave a good answer, then seemed to forget it a minute later. Why is that expected?
4. What does `q4` cost you, and why is it acceptable for classifying known incidents but not for novel reasoning?
5. Why would "OOMKilled" and "terminated due to memory limit" match in vector search but not keyword search?
6. Why is 10 tokens/second acceptable for this system but not for a chatbot?
7. Why Postgres with pgvector rather than a dedicated vector database — and at what point would that answer change?
8. Signals arrive every few seconds. Why does `?page=2` return different rows each time you ask, and what does a cursor use to avoid it?
9. The gateway's code never writes to the database. Why make Postgres enforce that anyway?
10. A workflow uses `some-org/scan-action@v2`. What exactly could change under you, and how do you stop it?
11. The 12 database tests skip on your laptop. Why must the same tests *fail* in CI when there's no database?
12. Trivy reports a HIGH CVE in a package you never installed. Where did it come from, and what is the right fix?
13. A `commit-msg` hook already checks the convention. Why does CI check it again?
14. `KAV-25 #done` is in a pushed commit and the issue didn't move. What's the first thing to compare?

## In an interview

**"How did you choose which model to run?"**

> "I measured, because the first guesses were wrong. I ran four small models on a server the same
> size as production: 2 Graviton cores, 4 GB. I recorded resident memory, load time, speed, and
> whether each could produce a valid remediation proposal. Two findings changed the architecture.
> First, the models took 1.1 to 1.9 GB, not the 900 MB I'd estimated, so I moved Postgres to its
> own server rather than squeeze it. Second, left unconstrained, the Gemma models dropped the two
> fields my policy engine decides on, blast radius and reversibility, six times out of nine. So the
> agent never trusts free-form output: it decodes against the JSON schema and then validates.
> Speed, about ten seconds a diagnosis, was never the problem, because nobody watches the agent
> think."

The strength there is that every number is one you measured, and each one changed a decision.
Which model is *most accurate* is deliberately left to Phase 2's evals, and saying so is part of
the answer.

**"What does your CI actually protect against?"**

> "Three things. First, code that only works on my machine: tests run on a clean runner against a
> real Postgres, and they're not allowed to skip. Second, drift: the lockfile, image digests and
> action SHAs mean CI tests exactly what ships. Third, the pipeline itself as an attack path: every
> third-party action is pinned to a commit, because in March 2026 someone re-pointed a popular
> scanner's version tags to credential-stealing code."

## Further reading

- Docker documentation — *Best practices for writing Dockerfiles* (build cache section)
- `pgvector` README — indexing and distance operators
- Ollama documentation — Modelfiles and the OpenAI-compatible endpoint
- Google's Gemma model card — sizes, context window, intended uses
- Any current explainer on tokenization; the details change, the principle does not
- GitHub Docs — *Security hardening for GitHub Actions* (pinning actions to a full-length commit SHA)
- Atlassian Support — *Process work items with smart commits* (the email-match condition)
- uv documentation — *Locking and syncing*
