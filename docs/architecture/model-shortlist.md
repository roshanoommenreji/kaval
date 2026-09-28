# Local model shortlist — measured

**Measured 2026-09-26 (`KAV-22`)** on the AWS dev server: a `t4g.medium` (2 Graviton vCPUs, 4 GB),
the same size as the production node. Ollama 0.34.4, temperature 0, seed 42, 4096-token context,
one model loaded at a time. Reproduce with `make dev` then `make bench`
([Lab 04](../labs/lab-04-compose-stack.md)).

**What this decides:** which models fit the node and run fast enough to be worth evaluating.
**What it doesn't:** which one diagnoses *correctly*. That's the Phase 2 eval harness, run on the
models this page carries forward.

## The shortlist

All 4-bit, so the comparison is like for like.

| Model | Download | Why it's here |
|---|---|---|
| `gemma3:1b` | 815 MB | The original plan's model |
| `gemma3:1b-it-qat` | 1.0 GB | The same model, quantisation-aware trained by Google, which claims better quality at 4-bit |
| `llama3.2:1b-instruct-q4_K_M` | 808 MB | Meta's 1B, at the same 4-bit level as `gemma3:1b` (the default `llama3.2:1b` tag is 8-bit) |
| `qwen3:1.7b` | 1.4 GB | The largest that plausibly fits; run with thinking off |

**Dropped before measuring: Gemma 4 E2B.** It was on the first list, described as fitting the 4 GB
budget. It doesn't: "E2B" means *effective* 2B, and the 4-bit download is **7.2 GB** (4.3 GB even
as QAT), bigger than the whole server. Lesson: check the file size, not the parameter count.

## Results

3 incidents (an OOM crashloop, an idle EBS volume, an arm64 `exec format error`) × 3 rounds,
model order rotated each round.

| Model | RAM loaded | Load, cold | First token, warm | Prompt speed | Generation | Diagnosis, warm (total) |
|---|---|---|---|---|---|---|
| `gemma3:1b` | 1,275 MiB | 5.2 s | 9.3 s | 26 tok/s | 13.8 tok/s | 20.4 s |
| **`gemma3:1b-it-qat`** | **1,141 MiB** | 7.1 s | 2.6 s | **87 tok/s** | **20.8 tok/s** | 9.9 s |
| `llama3.2:1b-instruct-q4_K_M` | 1,262 MiB | 6.6 s | **1.5 s** | 53 tok/s | 15.8 tok/s | **8.9 s** |
| `qwen3:1.7b` | 1,880 MiB | 10.9 s | 2.7 s | 35 tok/s | 10.7 tok/s | 12.4 s |

**RAM loaded** is the resident memory (VmRSS) of the Ollama container's processes with the model in
memory. The first pass read `docker stats`, which reported ~1.9–2.5 GB. That figure includes file
cache (partly from downloading the other models) and overstated every model by ~0.7 GB.

**First token, warm** is Ollama's own load + prompt-processing time on requests after the first.
Prompt speed is from a cold, uncached request (~270 prompt tokens).

### Following the output format

| Model | JSON, as asked | JSON after removing a code fence | Valid proposal, unconstrained | Valid proposal, **schema-constrained** |
|---|---|---|---|---|
| `gemma3:1b` | 0/9 | 9/9 | 3/9 | **3/3** |
| `gemma3:1b-it-qat` | 0/9 | 9/9 | 3/9 | **3/3** |
| `llama3.2:1b-instruct-q4_K_M` | 9/9 | 9/9 | 9/9 | **3/3** |
| `qwen3:1.7b` | 9/9 | 9/9 | 9/9 | **3/3** |

Unconstrained, both Gemmas wrap their JSON in code fences despite being told not to. Worse, they
**drop `blast_radius` and `reversible`** from actions, and once invented `"volume"` as a blast
radius. Those are exactly the fields the policy engine decides on.

**Constrained decoding fixes it for every model, at no speed cost.** With the proposal's JSON
schema passed as Ollama's `format`, every model produced a valid proposal every time. So the
agent will **always** call the model schema-constrained and then validate with Pydantic anyway
(`CLAUDE.md`: every LLM output is validated before it touches the database).

**"Every time" here meant shape, not bounds — confirmed the hard way when `kaval_agent.diagnose`
went live (`KAV-41`, [ADR-0016](0016-json-schema-enforced-proposal-output.md)).**
`gemma3:1b-it-qat` reliably returned `confidence: 70` for one real incident, twice, despite
`format`'s schema stating `maximum: 1`: Ollama's constrained decoding is grammar-based and
enforces field names, types and enum membership, but not a bounded float's numeric range. The
"always validate with Pydantic anyway" rule above is exactly what caught it — zero rows were
written while this was happening. Fixed by stating the format in the prompt itself ("a
fraction between 0 and 1, never a percentage"), not by the schema or the retry alone. The
lesson generalises: constrained decoding is a strong but partial guarantee, and this table's
9/9 and 3/3 columns measure conformance to *shape*, not to every constraint the schema states.

The unconstrained column stays interesting as a measure of instruction-following, but it no longer rules any
model out.

## Findings

1. **All four fit.** On the prod node the rest of the stack is estimated at ~1.7 GB (k3s,
   services, Prometheus, Flux; see the [overview](overview.md)). Adding the model leaves roughly
   1.1–1.3 GB spare with either Gemma or Llama, but only **~0.5 GB with Qwen3 1.7B**. That's tight
   enough to be a risk. Keeping Postgres off the node (ADR-0008) is what makes Qwen possible at all.
2. **`gemma3:1b-it-qat` beats `gemma3:1b` on every measure:** smaller, 3× faster at reading the
   prompt, 1.5× faster at writing. It's the same model with a different quantisation (`Q4_0` vs
   `Q4_K_M`). The likely reason is that llama.cpp has ARM-optimised kernels for `Q4_0` that
   `Q4_K_M` lacks. That's consistent with every number here, but not proven. **`gemma3:1b` is
   dropped** in favour of its QAT build, which becomes the dev default (`LOCAL_MODEL`).
3. **Every model diagnoses in 9–12 s** once loaded (20 s for plain Gemma). That's fine: diagnosis
   is asynchronous, and nobody watches a cursor. Cold loads add 5–11 s after a model switch or an
   idle unload.
4. **The earlier estimate was ~900 MB for the model.** Measured, it's 1.1–1.9 GB. The footprint
   table in the overview now says so.

## Carried forward to the Phase 2 evals

| Model | Case for it | Case against |
|---|---|---|
| `gemma3:1b-it-qat` | Smallest and fastest; most headroom | Weakest unconstrained instruction-following |
| `llama3.2:1b-instruct-q4_K_M` | Fastest end to end; follows the format unprompted | Mid-pack speed at generation |
| `qwen3:1.7b` | Largest, so plausibly the best diagnoses | Slowest; ~0.5 GB headroom on the node |

The evals decide on **diagnosis quality**, the thing this page can't measure. If two models tie,
the one with more headroom wins, because memory pressure is this project's binding constraint.

Raw results (every reply included) are in `.build/bench/models-20260926T163735Z.json` on the
laptop that ran them. `.build/` is gitignored, so rerun `make bench` to reproduce them.
