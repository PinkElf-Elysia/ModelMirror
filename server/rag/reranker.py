from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(slots=True)
class RerankDocument:
    chunk_id: str
    text: str


@dataclass(slots=True)
class RerankItem:
    chunk_id: str
    score: float


@dataclass(slots=True)
class RerankOutcome:
    items: list[RerankItem]
    provider: str
    warning: str | None = None


class RerankService:
    """Vendor-neutral API reranker with an OpenAI-compatible LLM fallback."""

    def capabilities(self) -> dict[str, Any]:
        api_configured = bool(self._api_key() and self._api_url() and self._api_model())
        llm_configured = bool(self._llm_targets() and self._llm_model())
        return {
            "api_configured": api_configured,
            "api_model": self._api_model() if api_configured else "",
            "llm_configured": llm_configured,
            "llm_model": self._llm_model() if llm_configured else "",
        }

    async def rerank(
        self,
        query: str,
        documents: list[RerankDocument],
        *,
        provider: str,
        model: str = "",
        top_n: int,
    ) -> RerankOutcome:
        if not documents:
            return RerankOutcome(items=[], provider="none")
        providers = [provider]
        if provider == "auto":
            providers = ["api", "llm"]

        warnings: list[str] = []
        for candidate in providers:
            try:
                if candidate == "api" and self.capabilities()["api_configured"]:
                    return RerankOutcome(
                        items=await self._rerank_api(query, documents, model=model, top_n=top_n),
                        provider="api",
                    )
                if candidate == "llm" and self.capabilities()["llm_configured"]:
                    return RerankOutcome(
                        items=await self._rerank_llm(query, documents, model=model, top_n=top_n),
                        provider="llm",
                    )
            except Exception as exc:
                warnings.append(f"{candidate} rerank unavailable: {str(exc)[:160]}")

        return RerankOutcome(
            items=[],
            provider="none",
            warning="; ".join(warnings) or "No rerank provider is configured; fused ranking was used.",
        )

    async def _rerank_api(
        self,
        query: str,
        documents: list[RerankDocument],
        *,
        model: str,
        top_n: int,
    ) -> list[RerankItem]:
        payload = {
            "model": model or self._api_model(),
            "query": query,
            "documents": [item.text[:6000] for item in documents],
            "top_n": min(top_n, len(documents)),
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            response = await client.post(
                self._api_url(),
                headers={"Authorization": f"Bearer {self._api_key()}"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        raw_results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(raw_results, list):
            raise ValueError("Rerank API response is missing results.")
        return _parse_ranked_items(raw_results, documents, top_n)

    async def _rerank_llm(
        self,
        query: str,
        documents: list[RerankDocument],
        *,
        model: str,
        top_n: int,
    ) -> list[RerankItem]:
        compact_documents = [
            {"index": index, "text": item.text[:3000]}
            for index, item in enumerate(documents[:50])
        ]
        payload = {
            "model": model or self._llm_model(),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Rank retrieval candidates by relevance. Return JSON only as "
                        '{"results":[{"index":0,"score":0.9}]}. Scores must be 0..1.'
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"query": query[:4000], "documents": compact_documents, "top_n": top_n},
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0,
            "max_tokens": 1200,
            "stream": False,
        }
        errors: list[str] = []
        for target_name, target_url, target_key in self._llm_targets():
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(45.0, connect=10.0)
                ) as client:
                    response = await client.post(
                        target_url,
                        headers={
                            "Authorization": f"Bearer {target_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    response.raise_for_status()
                    data = response.json()
                choices = data.get("choices") if isinstance(data, dict) else None
                message = choices[0].get("message") if isinstance(choices, list) and choices else None
                content = message.get("content") if isinstance(message, dict) else None
                parsed = _parse_json_object(str(content or ""))
                raw_results = parsed.get("results") if isinstance(parsed, dict) else None
                if not isinstance(raw_results, list):
                    raise ValueError("LLM rerank response is missing results.")
                return _parse_ranked_items(raw_results, documents, top_n)
            except Exception as exc:
                errors.append(f"{target_name}: {str(exc)[:120]}")
        raise RuntimeError("; ".join(errors) or "No LLM rerank target is configured.")

    def _api_url(self) -> str:
        url = os.getenv("RERANK_API_URL", "").strip()
        if url:
            return url
        base = os.getenv("RERANK_API_BASE", "").strip().rstrip("/")
        return f"{base}/rerank" if base else ""

    def _api_key(self) -> str:
        return os.getenv("RERANK_API_KEY", "").strip()

    def _api_model(self) -> str:
        return os.getenv("RERANK_MODEL", "").strip()

    def _llm_targets(self) -> list[tuple[str, str, str]]:
        targets: list[tuple[str, str, str]] = []
        gateway = os.getenv("LLM_GATEWAY_URL", "").strip().rstrip("/")
        gateway_key = os.getenv("LLM_GATEWAY_KEY", "").strip()
        if gateway and gateway_key:
            gateway_url = (
                gateway
                if gateway.endswith("/chat/completions")
                else f"{gateway}/chat/completions"
            )
            targets.append(("llm_gateway", gateway_url, gateway_key))

        openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if openrouter_key:
            targets.append(
                (
                    "openrouter",
                    "https://openrouter.ai/api/v1/chat/completions",
                    openrouter_key,
                )
            )
        return targets

    def _llm_model(self) -> str:
        return (
            os.getenv("RAG_RERANK_LLM_MODEL", "").strip()
            or os.getenv("OPENROUTER_TEXT_FALLBACK_MODEL", "").strip()
        )


def _parse_ranked_items(
    raw_results: list[Any],
    documents: list[RerankDocument],
    top_n: int,
) -> list[RerankItem]:
    items: list[RerankItem] = []
    seen: set[int] = set()
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        try:
            index = int(raw.get("index"))
            score = float(raw.get("relevance_score", raw.get("score")))
        except (TypeError, ValueError):
            continue
        if index < 0 or index >= len(documents) or index in seen:
            continue
        seen.add(index)
        items.append(
            RerankItem(
                chunk_id=documents[index].chunk_id,
                score=max(0.0, min(1.0, score)),
            )
        )
        if len(items) >= top_n:
            break
    if not items:
        raise ValueError("Rerank provider returned no valid candidates.")
    return items


def _parse_json_object(content: str) -> dict[str, Any]:
    candidate = content.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    else:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start : end + 1]
    value = json.loads(candidate)
    if not isinstance(value, dict):
        raise ValueError("Rerank response must be a JSON object.")
    return value
