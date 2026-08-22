from __future__ import annotations

import hashlib
import math
import os
from typing import Any

import httpx


EMBEDDING_HTTP_REQUEST_TIMEOUT_SECONDS = 30.0
EMBEDDING_HTTP_CONNECT_TIMEOUT_SECONDS = 10.0
EMBEDDING_HTTP_MAX_CONNECTIONS = 20
EMBEDDING_HTTP_MAX_KEEPALIVE_CONNECTIONS = 10
EMBEDDING_HTTP_KEEPALIVE_EXPIRY_SECONDS = 30.0


class EmbeddingError(RuntimeError):
    """Raised when embedding generation fails."""


class EmbeddingClient:
    """OpenAI-compatible embedding client with a deterministic local fallback."""

    def __init__(
        self,
        *,
        api_base: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        dimension: int = 384,
    ) -> None:
        self.api_base = (api_base or os.getenv("EMBEDDING_API_BASE") or "").strip()
        self.api_key = (api_key or os.getenv("EMBEDDING_API_KEY") or "").strip()
        self.model = (model or os.getenv("EMBEDDING_MODEL") or "text-embedding-3-small").strip()
        self.dimension = dimension
        self.embedding_mode = os.getenv("RAG_EMBEDDING_MODE", "").strip().lower()
        self._client: httpx.AsyncClient | None = None

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    EMBEDDING_HTTP_REQUEST_TIMEOUT_SECONDS,
                    connect=EMBEDDING_HTTP_CONNECT_TIMEOUT_SECONDS,
                ),
                limits=httpx.Limits(
                    max_connections=EMBEDDING_HTTP_MAX_CONNECTIONS,
                    max_keepalive_connections=EMBEDDING_HTTP_MAX_KEEPALIVE_CONNECTIONS,
                    keepalive_expiry=EMBEDDING_HTTP_KEEPALIVE_EXPIRY_SECONDS,
                ),
            )
        return self._client

    @staticmethod
    def execution_contract() -> dict[str, int]:
        return {
            "request_timeout_ms": int(
                EMBEDDING_HTTP_REQUEST_TIMEOUT_SECONDS * 1000
            ),
            "connect_timeout_ms": int(
                EMBEDDING_HTTP_CONNECT_TIMEOUT_SECONDS * 1000
            ),
            "max_connections": EMBEDDING_HTTP_MAX_CONNECTIONS,
            "max_keepalive_connections": EMBEDDING_HTTP_MAX_KEEPALIVE_CONNECTIONS,
            "keepalive_expiry_ms": int(
                EMBEDDING_HTTP_KEEPALIVE_EXPIRY_SECONDS * 1000
            ),
        }

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return embeddings for a batch of texts."""

        if not texts:
            return []
        if self.embedding_mode == "hash" or not self.api_key:
            return [self._hash_embedding(text) for text in texts]

        base = self.api_base or "https://api.openai.com/v1"
        url = f"{base.rstrip('/')}/embeddings"
        payload = {
            "model": self.model,
            "input": texts,
            "dimensions": self.dimension,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = await self._http_client().post(
                url,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"Embedding API 调用失败：{exc}") from exc
        except ValueError as exc:
            raise EmbeddingError("Embedding API 返回了无效 JSON。") from exc

        items = data.get("data")
        if not isinstance(items, list):
            raise EmbeddingError("Embedding API 返回缺少 data 字段。")

        sorted_items = sorted(items, key=lambda item: item.get("index", 0))
        embeddings: list[list[float]] = []
        for item in sorted_items:
            embedding = item.get("embedding")
            if not isinstance(embedding, list):
                raise EmbeddingError("Embedding API 返回了无效 embedding。")
            embeddings.append([float(value) for value in embedding])
        return embeddings

    def embed_texts_locally(self, texts: list[str]) -> list[list[float]]:
        """Build deterministic local embeddings without contacting a provider."""

        return [self._hash_embedding(text) for text in texts]

    def _hash_embedding(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        normalized = "".join(text.lower().split())
        tokens = list(normalized)
        tokens.extend(normalized[index : index + 2] for index in range(max(0, len(normalized) - 1)))

        for token in tokens or [text]:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign

        return _normalize(vector)


def _normalize(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return vector
    return [value / magnitude for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Return cosine similarity for two normalized or non-normalized vectors."""

    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)
