"""Test del client LM Studio (HTTP mockato con pytest-httpx)."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from bookwriter.config import EmbeddingConfig, LLMConfig
from bookwriter.llm.client import LMStudioClient, _extract_json


@pytest.fixture()
def client() -> LMStudioClient:
    return LMStudioClient(LLMConfig(), EmbeddingConfig())


def test_extract_json_plain() -> None:
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced() -> None:
    assert _extract_json('```json\n{"ok": true}\n```') == {"ok": True}


def test_extract_json_with_noise() -> None:
    assert _extract_json('Ecco: {"x": [1, 2]} fine') == {"x": [1, 2]}


def test_list_models(client: LMStudioClient, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:1234/api/v0/models",
        json={
            "data": [
                {"id": "m1", "type": "llm", "state": "loaded", "max_context_length": 131072},
                {"id": "emb", "type": "embeddings", "state": "not-loaded", "max_context_length": 2048},
            ]
        },
    )
    models = client.list_models()
    assert {m.id for m in models} == {"m1", "emb"}
    assert models[0].loaded is True
    assert models[1].loaded is False


def test_chat(client: LMStudioClient, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        json={"choices": [{"message": {"content": "ciao"}}]},
    )
    assert client.chat([{"role": "user", "content": "hi"}]) == "ciao"


def test_usage_tracked(client: LMStudioClient, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        json={
            "choices": [{"message": {"content": "x"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        },
    )
    client.chat([{"role": "user", "content": "hi"}])
    assert client.usage["total_tokens"] == 8
    assert client.usage["prompt_tokens"] == 5


def test_chat_json_parses_fenced(client: LMStudioClient, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        json={"choices": [{"message": {"content": '```json\n{"lingua": "it"}\n```'}}]},
    )
    out = client.chat_json([{"role": "user", "content": "x"}], schema={"type": "object"})
    assert out == {"lingua": "it"}


def test_embed_applies_prefix(client: LMStudioClient, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:1234/v1/embeddings",
        json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]},
    )
    vecs = client.embed(["testo"], kind="document")
    assert vecs == [[0.1, 0.2, 0.3]]
    request = httpx_mock.get_request()
    assert request is not None
    import json as _json

    body = _json.loads(request.content)
    assert body["input"] == ["search_document: testo"]


def test_embed_empty_shortcuts(client: LMStudioClient) -> None:
    assert client.embed([]) == []
