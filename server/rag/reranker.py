from __future__ import annotations

import asyncio
import json
import os
import re
import time
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
    model: str = ""
    warning: str | None = None
    requested_input_count: int = 0
    input_count: int = 0
    input_char_count: int = 0
    output_count: int = 0
    candidate_limit: int = 20
    input_char_limit: int = 24_000
    timeout_budget_ms: int = 5_000
    elapsed_ms: float = 0.0
    attempted_provider: str = "none"
    attempted_model: str = ""
    fallback_reason: str | None = None
    provider_target: str = ""
    attempted_targets: tuple[str, ...] = ()
    execution_mode: str = "legacy"
    provider_route_receipts: dict[str, Any] | None = None
    fallback_reason_codes: tuple[str, ...] = ()


@dataclass(slots=True)
class PreparedRerankInput:
    query: str
    documents: list[RerankDocument]
    requested_input_count: int
    input_char_count: int
    candidate_limit: int
    input_char_limit: int
    timeout_seconds: float


@dataclass(slots=True)
class _LLMRerankResult:
    items: list[RerankItem]
    target: str
    fallback_reasons: tuple[str, ...]


class RerankAttemptError(RuntimeError):
    """A provider attempt failed with an already-sanitized reason."""


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

    def prepare_managed_input(
        self,
        query: str,
        documents: list[RerankDocument],
        *,
        model: str,
        access_mode: str,
        top_n: int,
    ) -> PreparedRerankInput:
        candidate_limit = _bounded_env_int(
            "RAG_RERANK_MAX_CANDIDATES", 20, minimum=1, maximum=100
        )
        input_char_limit = _bounded_env_int(
            "RAG_RERANK_MAX_INPUT_CHARS", 24_000, minimum=1_000, maximum=200_000
        )
        timeout_seconds = _bounded_env_float(
            "RAG_RERANK_TIMEOUT_SECONDS", 5.0, minimum=0.01, maximum=60.0
        )
        kind = "api" if access_mode == "dedicated" else "llm"
        limited_query, limited_documents, input_char_count = _limit_rerank_payload(
            query,
            documents,
            candidate_limit=candidate_limit,
            input_char_limit=input_char_limit,
            top_n=top_n,
            payload_specs=[(kind, model)],
        )
        return PreparedRerankInput(
            query=limited_query,
            documents=limited_documents,
            requested_input_count=len(documents),
            input_char_count=input_char_count,
            candidate_limit=candidate_limit,
            input_char_limit=input_char_limit,
            timeout_seconds=timeout_seconds,
        )

    async def rerank(
        self,
        query: str,
        documents: list[RerankDocument],
        *,
        provider: str,
        model: str = "",
        top_n: int,
    ) -> RerankOutcome:
        started = time.perf_counter()
        candidate_limit = _bounded_env_int(
            "RAG_RERANK_MAX_CANDIDATES", 20, minimum=1, maximum=100
        )
        input_char_limit = _bounded_env_int(
            "RAG_RERANK_MAX_INPUT_CHARS", 24_000, minimum=1_000, maximum=200_000
        )
        timeout_seconds = _bounded_env_float(
            "RAG_RERANK_TIMEOUT_SECONDS", 5.0, minimum=0.01, maximum=60.0
        )
        providers = [provider]
        if provider == "auto":
            providers = ["api", "llm"]
        capabilities = self.capabilities()
        payload_specs: list[tuple[str, str]] = []
        if "api" in providers and capabilities["api_configured"]:
            payload_specs.append(("api", model or self._api_model()))
        if "llm" in providers and capabilities["llm_configured"]:
            llm_model = self._llm_model()
            if provider == "llm" and model:
                llm_model = model
            if not _looks_like_reranker_model(llm_model):
                payload_specs.append(("llm", llm_model))
        limited_query, limited_documents, _ = _limit_rerank_payload(
            query,
            documents,
            candidate_limit=candidate_limit,
            input_char_limit=input_char_limit,
            top_n=top_n,
            payload_specs=payload_specs,
        )
        attempted_input_char_counts: list[int] = []

        def finalize(outcome: RerankOutcome) -> RerankOutcome:
            outcome.requested_input_count = len(documents)
            outcome.input_count = len(limited_documents)
            outcome.input_char_count = max(attempted_input_char_counts, default=0)
            outcome.output_count = len(outcome.items)
            outcome.candidate_limit = candidate_limit
            outcome.input_char_limit = input_char_limit
            outcome.timeout_budget_ms = int(round(timeout_seconds * 1000))
            outcome.elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
            return outcome

        if not documents:
            return finalize(
                RerankOutcome(
                    items=[],
                    provider="none",
                    model="",
                    fallback_reason="no_candidates",
                )
            )
        if not limited_documents:
            return finalize(
                RerankOutcome(
                    items=[],
                    provider="none",
                    warning=(
                        "Rerank input budget contained no candidate text; "
                        "fused ranking was used."
                    ),
                    fallback_reason="input_budget_exhausted",
                )
            )
        warnings: list[str] = []
        attempted_provider = "none"
        attempted_model = ""
        attempted_targets: list[str] = []

        async def run_with_shared_budget() -> RerankOutcome:
            nonlocal attempted_model, attempted_provider
            for candidate in providers:
                try:
                    if candidate == "api" and self.capabilities()["api_configured"]:
                        effective_model = model or self._api_model()
                        attempted_provider = "api"
                        attempted_model = effective_model
                        attempted_targets.append("rerank_api")
                        attempted_input_char_counts.append(
                            _serialized_payload_char_count(
                                _rerank_api_payload(
                                    limited_query,
                                    limited_documents,
                                    model=effective_model,
                                    top_n=min(top_n, len(limited_documents)),
                                )
                            )
                        )
                        return RerankOutcome(
                            items=await self._rerank_api(
                                limited_query,
                                limited_documents,
                                model=effective_model,
                                top_n=min(top_n, len(limited_documents)),
                                timeout_seconds=timeout_seconds,
                            ),
                            provider="api",
                            model=effective_model,
                            attempted_provider="api",
                            attempted_model=effective_model,
                            provider_target="rerank_api",
                            attempted_targets=tuple(attempted_targets),
                        )
                    if candidate == "llm" and self.capabilities()["llm_configured"]:
                        effective_model = self._llm_model()
                        if provider == "llm" and model:
                            effective_model = model
                        attempted_provider = "llm"
                        attempted_model = effective_model
                        if _looks_like_reranker_model(effective_model):
                            warnings.append(
                                "llm:reranker_model_not_chat_compatible"
                            )
                            continue
                        llm_result = await self._rerank_llm(
                            limited_query,
                            limited_documents,
                            model=effective_model,
                            top_n=min(top_n, len(limited_documents)),
                            timeout_seconds=timeout_seconds,
                            attempted_targets=attempted_targets,
                            attempted_input_char_counts=attempted_input_char_counts,
                        )
                        fallback_reasons = [
                            *warnings,
                            *llm_result.fallback_reasons,
                        ]
                        return RerankOutcome(
                            items=llm_result.items,
                            provider="llm",
                            model=effective_model,
                            attempted_provider="llm",
                            attempted_model=effective_model,
                            warning=(
                                f"Rerank fallback used ({';'.join(fallback_reasons)})."
                                if fallback_reasons
                                else None
                            ),
                            fallback_reason=";".join(fallback_reasons) or None,
                            provider_target=llm_result.target,
                            attempted_targets=tuple(attempted_targets),
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    warnings.append(f"{candidate}:{_safe_rerank_error(exc)}")

            reason = ";".join(warnings) or "provider_not_configured"
            return RerankOutcome(
                items=[],
                provider="none",
                model="",
                warning=f"Rerank unavailable ({reason}); fused ranking was used.",
                attempted_provider=attempted_provider,
                attempted_model=attempted_model,
                fallback_reason=reason,
                attempted_targets=tuple(attempted_targets),
            )

        try:
            async with asyncio.timeout(timeout_seconds):
                return finalize(await run_with_shared_budget())
        except TimeoutError:
            return finalize(
                RerankOutcome(
                    items=[],
                    provider="none",
                    model="",
                    warning=(
                        "Rerank total timeout budget was exhausted; "
                        "fused ranking was used."
                    ),
                    attempted_provider=attempted_provider,
                    attempted_model=attempted_model,
                    fallback_reason="timeout_budget_exhausted",
                    attempted_targets=tuple(attempted_targets),
                )
            )

    async def _rerank_api(
        self,
        query: str,
        documents: list[RerankDocument],
        *,
        model: str,
        top_n: int,
        timeout_seconds: float,
    ) -> list[RerankItem]:
        payload = _rerank_api_payload(
            query,
            documents,
            model=model or self._api_model(),
            top_n=top_n,
        )
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=min(2.0, timeout_seconds))
        ) as client:
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
        timeout_seconds: float,
        attempted_targets: list[str],
        attempted_input_char_counts: list[int],
    ) -> _LLMRerankResult:
        payload = _rerank_llm_payload(
            query,
            documents,
            model=model or self._llm_model(),
            top_n=top_n,
        )
        errors: list[str] = []
        for target_name, target_url, target_key in self._llm_targets():
            attempted_targets.append(target_name)
            attempted_input_char_counts.append(
                _serialized_payload_char_count(payload)
            )
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        timeout_seconds, connect=min(2.0, timeout_seconds)
                    )
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
                return _LLMRerankResult(
                    items=_parse_ranked_items(raw_results, documents, top_n),
                    target=target_name,
                    fallback_reasons=tuple(errors),
                )
            except Exception as exc:
                errors.append(f"{target_name}:{_safe_rerank_error(exc)}")
        raise RerankAttemptError(";".join(errors) or "llm_target_not_configured")

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
    if not items:
        raise ValueError("Rerank provider returned no valid candidates.")
    return sorted(items, key=lambda item: -item.score)[:top_n]


def _looks_like_reranker_model(model: str) -> bool:
    normalized = model.strip().lower()
    return "rerank" in normalized or "reranker" in normalized


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


_RERANK_LLM_SYSTEM_PROMPT = (
    "Rank retrieval candidates by relevance. Return JSON only as "
    '{"results":[{"index":0,"score":0.9}]}. Scores must be 0..1.'
)


def _rerank_api_payload(
    query: str,
    documents: list[RerankDocument],
    *,
    model: str,
    top_n: int,
) -> dict[str, Any]:
    return {
        "model": model,
        "query": query,
        "documents": [item.text for item in documents],
        "top_n": min(top_n, len(documents)),
    }


def _rerank_llm_payload(
    query: str,
    documents: list[RerankDocument],
    *,
    model: str,
    top_n: int,
) -> dict[str, Any]:
    compact_documents = [
        {"index": index, "text": item.text}
        for index, item in enumerate(documents)
    ]
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": _RERANK_LLM_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"query": query, "documents": compact_documents, "top_n": top_n},
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0,
        "max_tokens": 1200,
        "stream": False,
    }


def _serialized_payload_char_count(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False))


def _limit_rerank_payload(
    query: str,
    documents: list[RerankDocument],
    *,
    candidate_limit: int,
    input_char_limit: int,
    top_n: int,
    payload_specs: list[tuple[str, str]],
) -> tuple[str, list[RerankDocument], int]:
    if not payload_specs:
        return _limit_rerank_input(
            query,
            documents,
            candidate_limit=candidate_limit,
            input_char_limit=input_char_limit,
        )

    def candidate_for(
        text_budget: int,
        max_candidates: int,
    ) -> tuple[str, list[RerankDocument], int]:
        limited_query, limited_documents, _ = _limit_rerank_input(
            query,
            documents,
            candidate_limit=max_candidates,
            input_char_limit=text_budget,
        )
        payload_counts = []
        for kind, model in payload_specs:
            if kind == "api":
                payload = _rerank_api_payload(
                    limited_query,
                    limited_documents,
                    model=model,
                    top_n=top_n,
                )
            else:
                payload = _rerank_llm_payload(
                    limited_query,
                    limited_documents,
                    model=model,
                    top_n=top_n,
                )
            payload_counts.append(_serialized_payload_char_count(payload))
        return limited_query, limited_documents, max(payload_counts, default=0)

    max_candidates = min(candidate_limit, len(documents))
    low = 0
    high = max_candidates
    feasible_candidate_count = 0
    while low <= high:
        midpoint = (low + high) // 2
        candidate = candidate_for(0, midpoint)
        if candidate[2] <= input_char_limit:
            feasible_candidate_count = midpoint
            low = midpoint + 1
        else:
            high = midpoint - 1
    if feasible_candidate_count == 0:
        return candidate_for(0, 0)

    best = candidate_for(0, feasible_candidate_count)
    low = 0
    high = input_char_limit
    while low <= high:
        midpoint = (low + high) // 2
        candidate = candidate_for(midpoint, feasible_candidate_count)
        if candidate[2] <= input_char_limit:
            best = candidate
            low = midpoint + 1
        else:
            high = midpoint - 1
    return best


def _limit_rerank_input(
    query: str,
    documents: list[RerankDocument],
    *,
    candidate_limit: int,
    input_char_limit: int,
) -> tuple[str, list[RerankDocument], int]:
    selected = documents[:candidate_limit]
    query_limit = min(4_000, max(0, input_char_limit // 6))
    limited_query = query[:query_limit]
    remaining = max(0, input_char_limit - len(limited_query))
    limited_documents: list[RerankDocument] = []
    for index, document in enumerate(selected):
        remaining_slots = len(selected) - index
        per_document_limit = remaining // remaining_slots if remaining_slots else 0
        limited_text = document.text[:per_document_limit]
        limited_documents.append(
            RerankDocument(chunk_id=document.chunk_id, text=limited_text)
        )
        remaining -= len(limited_text)
    input_char_count = len(limited_query) + sum(
        len(document.text) for document in limited_documents
    )
    return limited_query, limited_documents, input_char_count


def _bounded_env_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _bounded_env_float(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _safe_rerank_error(exc: Exception) -> str:
    if isinstance(exc, RerankAttemptError):
        return str(exc)[:160]
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "provider_timeout"
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_json_response"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_status_{exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return "http_request_failed"
    if isinstance(exc, ValueError):
        return "invalid_provider_response"
    return "provider_error"
