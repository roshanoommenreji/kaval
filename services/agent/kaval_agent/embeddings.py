"""Embeddings, via Ollama's `/api/embed` (KAV-40, ADR-0015).

**all-minilm**: 384 dimensions, ~45 MB on disk, no GPU needed. Runbook chunks and incident
descriptions are short (a heading's worth of text), so a compact embedding model is enough,
and it leaves the 4 GB box's memory for the chat model — `OLLAMA_MAX_LOADED_MODELS=1` means
only one of them is resident at a time regardless, so this is a disk and latency choice, not
a memory one.

This module has no fallback and no retry: a caller that needs one (`index_runbooks.py`, an
interactive tool) decides that for itself. Every returned vector is checked against
`RUNBOOK_EMBED_DIM` before it's trusted — CLAUDE.md's rule that every model output is
validated before use applies to a vector's shape as much as to a proposal's JSON.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import httpx
from kaval_shared.models import RUNBOOK_EMBED_DIM

DEFAULT_MODEL = "all-minilm"


class EmbeddingError(RuntimeError):
    """Ollama refused the request, or returned a vector of the wrong size."""


def embed(
    texts: Sequence[str], *, base_url: str | None = None, model: str | None = None,
    timeout: float = 30.0, client: httpx.Client | None = None,
) -> list[list[float]]:
    """Embed a batch of texts in one call. `texts` and the result are the same length and
    order. `base_url`/`model` default to `OLLAMA_BASE_URL`/`EMBED_MODEL`, the same
    environment variables the gateway already reads for the chat model. `client` is an
    injection point for tests (an `httpx.Client` over `httpx.MockTransport`, so this never
    needs a real Ollama to be exercised) — real callers leave it unset."""
    if not texts:
        return []
    base_url = base_url or os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
    model = model or os.environ.get("EMBED_MODEL", DEFAULT_MODEL)
    owns_client = client is None
    http = client or httpx.Client()
    try:
        resp = http.post(
            f"{base_url}/api/embed", json={"model": model, "input": list(texts)}, timeout=timeout
        )
        resp.raise_for_status()
        vectors = resp.json()["embeddings"]
    except httpx.HTTPError as exc:
        raise EmbeddingError(f"Ollama embed call failed ({model} at {base_url}): {exc}") from exc
    except (KeyError, TypeError) as exc:
        raise EmbeddingError(f"Ollama's response had no 'embeddings' array: {exc}") from exc
    finally:
        if owns_client:
            http.close()
    if len(vectors) != len(texts):
        raise EmbeddingError(f"asked for {len(texts)} embeddings, got {len(vectors)}")
    for v in vectors:
        if len(v) != RUNBOOK_EMBED_DIM:
            raise EmbeddingError(
                f"{model} returned a {len(v)}-dim vector, expected {RUNBOOK_EMBED_DIM} "
                "(RUNBOOK_EMBED_DIM in kaval_shared.models) — is EMBED_MODEL set to "
                f"{DEFAULT_MODEL}?"
            )
    return list(vectors)


def embed_one(
    text: str, *, base_url: str | None = None, model: str | None = None, timeout: float = 30.0,
    client: httpx.Client | None = None,
) -> list[float]:
    """`embed` for a single text — the common case (one incident's query text)."""
    return embed([text], base_url=base_url, model=model, timeout=timeout, client=client)[0]
