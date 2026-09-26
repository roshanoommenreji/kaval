# Phase 1 — Local first

> **Written from:** theory
> **Lab:** to be written
> **Cost:** ~$5/month. Changed from $0: the stack now runs on an AWS dev server, not the laptop

> **Changed 2026-09-26 ([ADR-0007](../adr/0007-develop-on-an-aws-dev-server.md)).** This page
> was written arguing for the laptop, and the argument below is still sound. The decision went
> the other way for two reasons: measurements (memory and speed of the candidate models) need
> the real 4 GB Graviton machine, and building natively on arm64 removes a whole class of Phase 4
> surprises. Two things keep the original worry small. The dev server uses the default VPC and a
> single role, so no networking or IAM work is pulled forward. And the laptop stays the editor:
> `docker` commands reach the server through a Docker context. The cost is slower builds (the
> build context travels over the tunnel) and about $5 a month. "Local" in this phase's name now
> means *your own environment*, wherever it runs.

## Where this sits

Phase 0 made spending safe. This phase deliberately spends nothing anyway.

It unlocks: a working end-to-end loop you understand completely, before any of it has to survive
a network, a cluster, or a bill.

## What we're doing

A `docker-compose` stack on the dev server containing everything the real system will have, in
miniature:

- **Ollama** serving a small local model: Gemma 3 1B, Gemma 3 1B QAT, Llama 3.2 1B and Qwen3 1.7B
  are measured side by side ([results](../architecture/model-shortlist.md)), and Phase 2's evals
  pick one. Gemma 4 E2B was on the first list and is 7.2 GB at 4-bit: a model's name says "2B",
  its file says otherwise, so check the download size before believing it fits
- **Postgres** with the `pgvector` extension
- **gateway** — a FastAPI service exposing REST
- **a synthetic signal generator** — fake pod crashes and cost spikes, so there is something to react to

Plus the database schema: `signal`, `incident`, `proposal`, `action`, `decision`, `execution`,
`outcome`.

## Why this way

**Because the hard part is not the cloud.** The hard part is getting an LLM to produce something
structured and trustworthy from messy telemetry. That problem is identical whether it runs on your
laptop or on a Graviton node in Mumbai — and it is enormously faster to iterate on locally, where
a change takes two seconds instead of a container build, a registry push, and a reconciliation.

The rejected alternative was starting on AWS "to make it real." That front-loads the least
interesting failures — networking, IAM, image architecture — while you are still working out what
the system even does.

There is also a discipline point. Anything that only works because it is on AWS is a thing you do
not understand yet.

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

```dockerfile
COPY requirements.txt .
RUN pip install -r requirements.txt   # cached unless requirements change
COPY . .                              # changes on every edit
```

Reverse those two and every source edit reinstalls every dependency. The rule is: **least
frequently changing first**.

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
scheme. `q4_K_M` is a common quality/size sweet spot.

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
diagnosis is **asynchronous** — a signal arrives, the agent thinks for twenty seconds, a proposal
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

### pgvector, and why not a dedicated vector database

`pgvector` adds a `vector` column type to Postgres, with distance operators and indexes (HNSW or
IVFFlat) for approximate nearest-neighbour search.

Dedicated vector databases exist and are faster at very large scale. This project has thousands of
rows, not millions. Using Postgres means one database, one backup, one connection pool, and joins
between an incident's embedding and its relational data in a single query — the incident, its
signals, its proposals and its outcome, together.

The general lesson is worth carrying: **adding a specialised datastore is a cost, and it should be
paid only when the general one has actually failed you.**

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

## Check yourself

1. Why can't a Linux container run on Windows without a virtual machine somewhere?
2. Your Dockerfile reinstalls every dependency on each build. What is almost certainly wrong?
3. The model gave a good answer, then seemed to forget it a minute later. Why is that expected?
4. What does `q4` cost you, and why is it acceptable for classifying known incidents but not for novel reasoning?
5. Why would "OOMKilled" and "terminated due to memory limit" match in vector search but not keyword search?
6. Why is 10 tokens/second acceptable for this system but not for a chatbot?
7. Why Postgres with pgvector rather than a dedicated vector database — and at what point would that answer change?

## In an interview

**"You self-hosted an LLM. Why not just use an API?"**

> "Both, actually — and the routing between them is the point. A quantized Gemma 3 1B runs on the
> same $9/month ARM node as everything else and handles incidents that resemble ones I've already
> seen, matched by vector similarity against the incident history. That's most of them, and it
> costs nothing per call. Novel or low-confidence cases escalate to Bedrock. I record tokens and
> cost on both paths, so the saving is measured rather than claimed. The reason self-hosting is
> viable at all is that diagnosis is asynchronous — nobody's watching a cursor, so 10 tokens a
> second is fine. If it were an interactive chat product I'd have made the opposite call."

The strength there is treating it as an engineering trade-off with a measurement, rather than an
ideological preference for self-hosting.

## Further reading

- Docker documentation — *Best practices for writing Dockerfiles* (build cache section)
- `pgvector` README — indexing and distance operators
- Ollama documentation — Modelfiles and the OpenAI-compatible endpoint
- Google's Gemma model card — sizes, context window, intended uses
- Any current explainer on tokenization; the details change, the principle does not
