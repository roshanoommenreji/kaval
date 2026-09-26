# Lab 04 — The Compose stack on the dev server, and measuring the models

**Phase:** 1 · **Time:** ~2 hrs (about 30 min of it waiting for the benchmark) · **Cost:** about
2 cents an hour of dev-server time ([Lab 03](lab-03-aws-dev-server.md)), plus ~5 GB of disk that
the server already has

The first time Kaval's pieces run together: **Ollama** serving a small AI model,
**Postgres + pgvector** holding the audit trail, a one-off **migration** step, and the **gateway**
API. It all runs on the AWS dev server, driven from the laptop. Then the four candidate models are
measured on that same machine, which is the size production will be.

---

## Why this order

Nothing in Phase 2 (the agent) can be built until a model answers and a database accepts rows,
so the stack comes first. The model *measurement* comes in the same lab because the stack is what
makes it possible, and because the Phase 2 evals should only spend time on models that actually
fit.

Three decisions shaped the build:

- **The Docker daemon is remote.** `make dev` runs `docker --context kaval-devbox compose`, so
  every container runs on the server. The laptop only sends the build context and reads logs.
- **So there are no bind mounts.** A line like `./src:/app` would mean `./src` *on the server*,
  an empty or missing folder, and the container would quietly see nothing. Code goes in through
  image builds; data lives in **named volumes** on the server's disk (`pgdata`, `ollama-models`),
  which survive `make dev-down` and server stops.
- **So nothing is published to the internet.** Every port binds to `127.0.0.1` *on the server*.
  The laptop reaches them through an SSH tunnel that itself rides Session Manager, so the server
  still has zero inbound rules.

---

## Prerequisites

- [Lab 03](lab-03-aws-dev-server.md) complete: `make devbox-up` works and
  `docker --context kaval-devbox ps` answers
- `.env` copied from `.env.example`, with at least `POSTGRES_*` and `LOCAL_MODEL` set
- Python 3.11 on the laptop (the benchmark script is stdlib only)

**Windows note:** if Docker fails with `SessionManagerPlugin is not found`, the terminal was opened
before the plugin was installed. Open a new one, or add
`C:\Program Files\Amazon\SessionManagerPlugin\bin` to `PATH`.

---

## Step 1 — Read the files before running them

| File | What it does |
|---|---|
| `compose.yaml` | Five services: `postgres`, `ollama`, `model-pull` (one-off: downloads `LOCAL_MODEL`), `migrate` (one-off: `alembic upgrade head`), `gateway` |
| `services/gateway/Dockerfile` | Builds the gateway for arm64, as a non-root user. The same image runs `migrate` |
| `.dockerignore` | An **allowlist**: only `pyproject.toml`, `services/` and `migrations/` are sent to the server. `.env` can't be sent by accident |
| `services/gateway/kaval_gateway/main.py` | `GET /healthz`: 200 only if Postgres is **migrated** and the model is **downloaded** |

The start order is enforced, not hoped for:

```
postgres ──healthy──▶ migrate ──exited 0──┐
ollama ───healthy──▶ model-pull ─exited 0─┴──▶ gateway
```

`depends_on` with `service_healthy` and `service_completed_successfully` means the gateway never
starts against an empty database or a missing model.

Two values in `ollama`'s settings matter for the measurements: `OLLAMA_MAX_LOADED_MODELS=1`
keeps one model in memory at a time (a 4 GB box can't hold two), and
`OLLAMA_CONTEXT_LENGTH=4096` fixes the context, because memory grows with it and the
comparison has to be like for like.

---

## Step 2 — Bring it up

```bash
make dev
```

This starts the server if it's stopped, builds the gateway image **on the server** (so it's
natively arm64), pulls the Postgres and Ollama images, downloads the model, runs migrations and
waits until everything is healthy. First run on 2026-09-26: **3 min 55 s**. Later runs take
seconds, because images, the model and the database are all kept.

Then, in a **second terminal** that you leave open:

```bash
make dev-tunnel
```

It forwards `8000` (gateway), `5432` (Postgres) and **`11435`** (Ollama) to the laptop. Ollama is
on `11435`, not its usual `11434`, because the **Ollama desktop app** on the laptop may already
hold `11434`. On 2026-09-26 it did: the first test prompt went to the *laptop's* Ollama and came
back 404, "model not found". Check with:

```powershell
Get-NetTCPConnection -LocalPort 11434 -State Listen | ForEach-Object { Get-Process -Id $_.OwningProcess }
```

---

## Step 3 — Prove each piece

```bash
curl http://localhost:8000/healthz
```

```json
{"status":"ok","postgres":{"ok":true,"detail":"migrated to 0e7a61c13abe"},
 "ollama":{"ok":true,"detail":"gemma3:1b ready"}}
```

The model answers through the **OpenAI-compatible** API, which is the shape the agent will use:

```bash
curl http://localhost:11435/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"gemma3:1b","temperature":0,"messages":[{"role":"user","content":"Reply with exactly: kaval ok"}]}'
```

2026-09-26: `"kaval ok"` in 5.1 s, including loading the model.

Then check the properties that matter more than "it runs":

```bash
docker --context kaval-devbox image inspect kaval/gateway:dev --format '{{.Architecture}}'   # arm64
docker --context kaval-devbox exec kaval-gateway-1 id                                        # uid=10001(kaval)
docker --context kaval-devbox exec kaval-gateway-1 ls -a /app                                # no .env
```

---

## Step 4 — Measure the shortlist

```bash
make bench
```

`scripts/dev/bench_models.py` opens its own tunnel (port 11436), downloads any missing models, then
runs **4 models × 3 incidents × 3 rounds**, rotating the model order each round so no model always
goes first. Temperature 0, fixed seed, 4096-token context. Timings come from Ollama's own
counters, so the tunnel's latency doesn't pollute them. Raw results go to `.build/bench/`
(gitignored).

The shortlist, all 4-bit so it's like for like:

| Model | Download |
|---|---|
| `gemma3:1b` | 815 MB |
| `gemma3:1b-it-qat` | 1.0 GB |
| `llama3.2:1b-instruct-q4_K_M` | 808 MB |
| `qwen3:1.7b` | 1.4 GB |

**Gemma 4 E2B was on the first list and was dropped before measuring.** Its "2B" is its
*effective* size; the 4-bit download is 7.2 GB (4.3 GB even as QAT), more than the whole server.
A model's name is not its footprint: check the download size first.

After the main run, the script adds two passes to the same results file (both run automatically
on a fresh `make bench`):

- **Resident memory** (`--memory`): unloads everything, loads one model, reads `VmRSS` of the
  container's processes. The first attempt used `docker stats`, which counts file cache and
  overstated every model by ~0.7 GB. Always check what a memory number includes.
- **Schema-constrained** (`--constrained`): one round with the proposal's JSON schema passed as
  Ollama's `format`.

2026-09-26 results, in short:

| Model | RAM loaded | Generation | Diagnosis (warm) | Valid proposal: free / constrained |
|---|---|---|---|---|
| `gemma3:1b` | 1,275 MiB | 13.8 tok/s | 20.4 s | 3/9 / 3/3 |
| `gemma3:1b-it-qat` | 1,141 MiB | 20.8 tok/s | 9.9 s | 3/9 / 3/3 |
| `llama3.2:1b-instruct-q4_K_M` | 1,262 MiB | 15.8 tok/s | 8.9 s | 9/9 / 3/3 |
| `qwen3:1.7b` | 1,880 MiB | 10.7 tok/s | 12.4 s | 9/9 / 3/3 |

All four fit. Constrained decoding makes every model produce valid proposals, so the agent always
uses it. `gemma3:1b` is dropped for its QAT build, which is smaller and faster (and becomes the
dev default, `LOCAL_MODEL=gemma3:1b-it-qat`). The other three go to the Phase 2 evals. Full
results and reasoning: [model-shortlist.md](../architecture/model-shortlist.md).

---

## Step 5 — Stop

```bash
make dev-down      # containers stop; volumes (database, models) stay: ~9 GB of the 30 GB disk
make devbox-down   # or just leave it: it stops itself after an idle hour
```

---

## Done when

- [ ] `make dev` brings the stack up healthy on the dev server
- [ ] `/healthz` returns `ok` through `make dev-tunnel`
- [ ] The model answers through `/v1/chat/completions`
- [ ] The gateway image is arm64, runs as a non-root user, and contains no `.env`
- [ ] `make bench` completes and the results are written up
- [ ] Journal entry appended

---

## What to write down

- Why a remote Docker daemon makes bind mounts a trap, and what replaces them
- Why every port binds to `127.0.0.1` and what that buys over a security-group rule
- Why `depends_on` needs conditions, not just names
- Why a model's parameter count doesn't tell you whether it fits
- Why the benchmark measures fit and speed but not correctness, and what does
