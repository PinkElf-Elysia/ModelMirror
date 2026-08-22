from __future__ import annotations

import pytest

from server.rag import embedder as embedder_module
from server.rag.embedder import EmbeddingClient


@pytest.mark.asyncio
async def test_openai_compatible_embedding_request_locks_dimension(monkeypatch):
    observed: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "data": [
                    {
                        "index": 0,
                        "embedding": [0.0] * 384,
                    }
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def post(self, url, *, headers, json):
            observed.update({"url": url, "headers": headers, "json": json})
            return FakeResponse()

    monkeypatch.setattr(
        embedder_module.httpx,
        "AsyncClient",
        lambda **_kwargs: FakeClient(),
    )
    client = EmbeddingClient(
        api_base="https://embedding.example/v1",
        api_key="test-key",
        model="text-embedding-3-small",
        dimension=384,
    )

    vectors = await client.embed_texts(["dimension contract"])

    assert len(vectors[0]) == 384
    assert observed["json"] == {
        "model": "text-embedding-3-small",
        "input": ["dimension contract"],
        "dimensions": 384,
    }


@pytest.mark.asyncio
async def test_embedding_client_reuses_connection_pool_until_closed(monkeypatch):
    instances: list[object] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": [{"index": 0, "embedding": [0.0] * 8}]}

    class FakeClient:
        def __init__(self, **_kwargs):
            self.closed = False
            instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            self.closed = True

        async def post(self, _url, **_kwargs):
            return FakeResponse()

        async def aclose(self) -> None:
            self.closed = True

    monkeypatch.setattr(embedder_module.httpx, "AsyncClient", FakeClient)
    client = EmbeddingClient(
        api_base="https://embedding.example/v1",
        api_key="test-key",
        model="text-embedding-3-small",
        dimension=8,
    )

    await client.embed_texts(["first query"])
    await client.embed_texts(["second query"])

    assert len(instances) == 1
    assert instances[0].closed is False

    await client.aclose()

    assert instances[0].closed is True
