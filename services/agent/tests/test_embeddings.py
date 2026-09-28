"""Embedding calls, against a mocked transport — never a real Ollama (KAV-40)."""

from __future__ import annotations

import json

import httpx
import pytest
from kaval_agent import embeddings
from kaval_shared.models import RUNBOOK_EMBED_DIM


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


def _vector(n: int = RUNBOOK_EMBED_DIM) -> list[float]:
    return [0.01] * n


def test_embeds_a_batch_in_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        assert payload["model"] == "all-minilm"
        assert payload["input"] == ["a", "b"]
        return httpx.Response(200, json={"embeddings": [_vector(), _vector()]})

    client = _client(httpx.MockTransport(handler))
    vectors = embeddings.embed(["a", "b"], base_url="http://fake", model="all-minilm",
                               client=client)
    assert len(vectors) == 2
    assert all(len(v) == RUNBOOK_EMBED_DIM for v in vectors)


def test_empty_input_makes_no_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not have called Ollama")

    client = _client(httpx.MockTransport(handler))
    assert embeddings.embed([], client=client) == []


def test_wrong_dimension_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(embeddings.EmbeddingError, match="2-dim"):
        embeddings.embed(["x"], client=client)


def test_count_mismatch_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [_vector()]})

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(embeddings.EmbeddingError, match="asked for 2"):
        embeddings.embed(["x", "y"], client=client)


def test_http_error_is_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="model not found")

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(embeddings.EmbeddingError, match="embed call failed"):
        embeddings.embed(["x"], client=client)


def test_malformed_response_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(embeddings.EmbeddingError, match="no 'embeddings'"):
        embeddings.embed(["x"], client=client)


def test_embed_one_returns_a_bare_vector() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [_vector()]})

    client = _client(httpx.MockTransport(handler))
    vector = embeddings.embed_one("x", client=client)
    assert len(vector) == RUNBOOK_EMBED_DIM
