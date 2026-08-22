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
    max_output_tokens: int = 1_200
    timeout_budget_ms: int = 5_000
    elapsed_ms: float = 0.0
    attempted_provider: str = "none"
    attempted_model: str = ""
    fallback_reason: str | None = None
    provider_target: str = ""
    attempted_targets: tuple[str, ...] = ()
    evidence_verdict: str = "unavailable"
    support_score: float | None = None
    evidence_reason_code: str | None = None
    external_call_count: int = 0
    provider_http_elapsed_ms: float | None = None
    provider_prompt_tokens: int | None = None
    provider_completion_tokens: int | None = None
    provider_total_tokens: int | None = None
    provider_response_char_count: int | None = None


@dataclass(slots=True)
class _LLMRerankResult:
    items: list[RerankItem]
    target: str
    fallback_reasons: tuple[str, ...]
    evidence_verdict: str = "unavailable"
    support_score: float | None = None
    evidence_reason_code: str | None = None
    provider_http_elapsed_ms: float | None = None
    provider_prompt_tokens: int | None = None
    provider_completion_tokens: int | None = None
    provider_total_tokens: int | None = None
    provider_response_char_count: int | None = None


class RerankAttemptError(RuntimeError):
    """A provider attempt failed with an already-sanitized reason."""


class _RerankResponseError(ValueError):
    """A provider response violated a known contract without retaining payload data."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RerankService:
    """Vendor-neutral API reranker with an OpenAI-compatible LLM fallback."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                    keepalive_expiry=30.0,
                )
            )
        return self._client

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()

    def capabilities(self) -> dict[str, Any]:
        api_configured = bool(self._api_key() and self._api_url() and self._api_model())
        llm_configured = bool(self._llm_targets() and self._llm_model())
        evidence_verifier_model = self._evidence_verifier_model()
        evidence_verifier_configured = bool(
            self._llm_targets() and evidence_verifier_model
        )
        return {
            "api_configured": api_configured,
            "api_model": self._api_model() if api_configured else "",
            "llm_configured": llm_configured,
            "llm_model": self._llm_model() if llm_configured else "",
            "evidence_verifier_configured": evidence_verifier_configured,
            "evidence_verifier_model": (
                evidence_verifier_model if evidence_verifier_configured else ""
            ),
        }

    @staticmethod
    def runtime_contract() -> dict[str, int]:
        timeout_seconds = _bounded_env_float(
            "RAG_RERANK_TIMEOUT_SECONDS", 5.0, minimum=0.01, maximum=60.0
        )
        return {
            "rerank_candidate_limit": _bounded_env_int(
                "RAG_RERANK_MAX_CANDIDATES", 20, minimum=1, maximum=100
            ),
            "rerank_input_char_limit": _bounded_env_int(
                "RAG_RERANK_MAX_INPUT_CHARS",
                24_000,
                minimum=1_000,
                maximum=200_000,
            ),
            "evidence_verifier_candidate_limit": _bounded_env_int(
                "RAG_EVIDENCE_VERIFIER_MAX_CANDIDATES",
                20,
                minimum=1,
                maximum=100,
            ),
            "evidence_verifier_input_char_limit": _bounded_env_int(
                "RAG_EVIDENCE_VERIFIER_MAX_INPUT_CHARS",
                12_000,
                minimum=1_000,
                maximum=200_000,
            ),
            "evidence_verifier_base_max_output_tokens": _bounded_env_int(
                "RAG_EVIDENCE_VERIFIER_MAX_OUTPUT_TOKENS",
                300,
                minimum=100,
                maximum=1_200,
            ),
            "timeout_budget_ms": int(round(timeout_seconds * 1000)),
            "max_connections": 20,
            "max_keepalive_connections": 10,
            "keepalive_expiry_ms": 30_000,
        }

    @staticmethod
    def execution_contract(
        *,
        top_n: int,
        require_evidence_verdict: bool,
    ) -> dict[str, int]:
        runtime = RerankService.runtime_contract()
        candidate_limit = runtime["rerank_candidate_limit"]
        input_char_limit = runtime["rerank_input_char_limit"]
        max_output_tokens = 1_200
        if require_evidence_verdict:
            candidate_limit = max(
                min(top_n, 100),
                runtime["evidence_verifier_candidate_limit"],
            )
            input_char_limit = runtime["evidence_verifier_input_char_limit"]
            max_output_tokens = max(
                runtime["evidence_verifier_base_max_output_tokens"],
                min(1_200, 100 + min(top_n, 50) * 16),
            )
        return {
            "candidate_limit": candidate_limit,
            "input_char_limit": input_char_limit,
            "max_output_tokens": max_output_tokens,
            "timeout_budget_ms": runtime["timeout_budget_ms"],
            "max_connections": runtime["max_connections"],
            "max_keepalive_connections": runtime["max_keepalive_connections"],
            "keepalive_expiry_ms": runtime["keepalive_expiry_ms"],
        }

    async def rerank(
        self,
        query: str,
        documents: list[RerankDocument],
        *,
        provider: str,
        model: str = "",
        top_n: int,
        max_provider_attempts: int | None = None,
        require_evidence_verdict: bool = False,
    ) -> RerankOutcome:
        started = time.perf_counter()
        execution_contract = self.execution_contract(
            top_n=top_n,
            require_evidence_verdict=require_evidence_verdict,
        )
        candidate_limit = execution_contract["candidate_limit"]
        input_char_limit = execution_contract["input_char_limit"]
        max_output_tokens = execution_contract["max_output_tokens"]
        timeout_seconds = execution_contract["timeout_budget_ms"] / 1000
        providers = [provider]
        if provider == "auto":
            providers = ["api", "llm"]
        capabilities = self.capabilities()
        requested_llm_model = (
            model if provider == "llm" and model else self._llm_model()
        )
        llm_configured = bool(self._llm_targets() and requested_llm_model)
        payload_specs: list[tuple[str, str]] = []
        if "api" in providers and capabilities["api_configured"]:
            payload_specs.append(("api", model or self._api_model()))
        if "llm" in providers and llm_configured:
            llm_model = requested_llm_model
            if not _looks_like_reranker_model(llm_model):
                payload_specs.append(("llm", llm_model))
        limited_query, limited_documents, _ = _limit_rerank_payload(
            query,
            documents,
            candidate_limit=candidate_limit,
            input_char_limit=input_char_limit,
            top_n=top_n,
            payload_specs=payload_specs,
            llm_max_tokens=max_output_tokens,
        )
        attempted_input_char_counts: list[int] = []
        if max_provider_attempts is not None and max_provider_attempts != 1:
            raise ValueError("max_provider_attempts must be 1 when specified.")

        def finalize(outcome: RerankOutcome) -> RerankOutcome:
            outcome.requested_input_count = len(documents)
            outcome.input_count = len(limited_documents)
            outcome.input_char_count = max(attempted_input_char_counts, default=0)
            outcome.output_count = len(outcome.items)
            outcome.candidate_limit = candidate_limit
            outcome.input_char_limit = input_char_limit
            outcome.max_output_tokens = max_output_tokens
            outcome.timeout_budget_ms = int(round(timeout_seconds * 1000))
            outcome.elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
            outcome.external_call_count = max(
                int(outcome.external_call_count), len(outcome.attempted_targets)
            )
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
                    if candidate == "llm" and llm_configured:
                        effective_model = requested_llm_model
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
                            max_provider_attempts=max_provider_attempts,
                            require_evidence_verdict=require_evidence_verdict,
                            max_output_tokens=max_output_tokens,
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
                            evidence_verdict=llm_result.evidence_verdict,
                            support_score=llm_result.support_score,
                            evidence_reason_code=llm_result.evidence_reason_code,
                            provider_http_elapsed_ms=(
                                llm_result.provider_http_elapsed_ms
                            ),
                            provider_prompt_tokens=(
                                llm_result.provider_prompt_tokens
                            ),
                            provider_completion_tokens=(
                                llm_result.provider_completion_tokens
                            ),
                            provider_total_tokens=llm_result.provider_total_tokens,
                            provider_response_char_count=(
                                llm_result.provider_response_char_count
                            ),
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
        response = await self._http_client().post(
            self._api_url(),
            headers={"Authorization": f"Bearer {self._api_key()}"},
            json=payload,
            timeout=httpx.Timeout(
                timeout_seconds,
                connect=min(2.0, timeout_seconds),
            ),
        )
        response.raise_for_status()
        data = response.json()
        raw_results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(raw_results, list):
            raise _RerankResponseError("invalid_ranked_items")
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
        max_provider_attempts: int | None,
        require_evidence_verdict: bool,
        max_output_tokens: int,
    ) -> _LLMRerankResult:
        payload = _rerank_llm_payload(
            query,
            documents,
            model=model or self._llm_model(),
            top_n=top_n,
            max_tokens=max_output_tokens,
        )
        errors: list[str] = []
        targets = self._llm_targets()
        if max_provider_attempts is not None:
            targets = targets[:max_provider_attempts]
        for target_name, target_url, target_key in targets:
            attempted_targets.append(target_name)
            attempted_input_char_counts.append(
                _serialized_payload_char_count(payload)
            )
            try:
                http_started = time.perf_counter()
                response = await self._http_client().post(
                    target_url,
                    headers={
                        "Authorization": f"Bearer {target_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=httpx.Timeout(
                        timeout_seconds,
                        connect=min(2.0, timeout_seconds),
                    ),
                )
                provider_http_elapsed_ms = round(
                    (time.perf_counter() - http_started) * 1000,
                    3,
                )
                response.raise_for_status()
                data = response.json()
                choices = data.get("choices") if isinstance(data, dict) else None
                message = choices[0].get("message") if isinstance(choices, list) and choices else None
                content = message.get("content") if isinstance(message, dict) else None
                provider_usage = _safe_provider_usage(
                    data.get("usage") if isinstance(data, dict) else None
                )
                parsed = _parse_json_object(str(content or ""))
                verdict, support_score, reason_code = _parse_evidence_verdict(parsed)
                if require_evidence_verdict and verdict == "unavailable":
                    raise _RerankResponseError("missing_evidence_verdict")
                raw_results = parsed.get("results") if isinstance(parsed, dict) else None
                if (
                    require_evidence_verdict
                    and verdict == "abstain"
                    and (raw_results is None or raw_results == [])
                ):
                    ranked_items: list[RerankItem] = []
                elif not isinstance(raw_results, list):
                    raise _RerankResponseError("invalid_ranked_items")
                else:
                    ranked_items = _parse_ranked_items(
                        raw_results, documents, top_n
                    )
                return _LLMRerankResult(
                    items=ranked_items,
                    target=target_name,
                    fallback_reasons=tuple(errors),
                    evidence_verdict=verdict,
                    support_score=support_score,
                    evidence_reason_code=reason_code,
                    provider_http_elapsed_ms=provider_http_elapsed_ms,
                    provider_prompt_tokens=provider_usage["prompt_tokens"],
                    provider_completion_tokens=provider_usage["completion_tokens"],
                    provider_total_tokens=provider_usage["total_tokens"],
                    provider_response_char_count=len(str(content or "")),
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

    def _evidence_verifier_model(self) -> str:
        return os.getenv("RAG_EVIDENCE_VERIFIER_LLM_MODEL", "").strip()


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
        raise _RerankResponseError("invalid_ranked_items")
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
        raise _RerankResponseError("invalid_json_response")
    return value


_RERANK_LLM_SYSTEM_PROMPT = (
    "Use only the supplied candidate text. Rank candidates by relevance and decide "
    "whether they explicitly support a complete answer to the query. Topic or entity "
    "overlap alone is not support; if the requested fact is absent, answerable must be "
    "false. Return JSON only as "
    '{"answerable":true,"support_score":0.9,"reason_code":"supported",'
    '"results":[{"index":0,"score":0.9}]}. Scores must be 0..1. reason_code '
    "must be supported, requested_fact_absent, insufficient_context, or conflicting_evidence. "
    "When answerable is false, results may be an empty list; when answerable is true, "
    "results must contain at least one valid candidate."
)


def _parse_evidence_verdict(value: dict[str, Any]) -> tuple[str, float | None, str | None]:
    if "answerable" not in value:
        return "unavailable", None, None
    answerable = value.get("answerable")
    if not isinstance(answerable, bool):
        raise _RerankResponseError("invalid_evidence_verdict")
    try:
        support_score = float(value.get("support_score"))
    except (TypeError, ValueError) as exc:
        raise _RerankResponseError("invalid_evidence_verdict") from exc
    if not 0 <= support_score <= 1:
        raise _RerankResponseError("invalid_evidence_verdict")
    allowed_reasons = {
        "supported",
        "requested_fact_absent",
        "insufficient_context",
        "conflicting_evidence",
    }
    reason_code = str(value.get("reason_code") or "").strip().lower()
    if reason_code not in allowed_reasons:
        raise _RerankResponseError("invalid_reason_code")
    if answerable and reason_code != "supported":
        raise _RerankResponseError("invalid_reason_code")
    if not answerable and reason_code == "supported":
        raise _RerankResponseError("invalid_reason_code")
    return (
        "answerable" if answerable else "abstain",
        round(support_score, 6),
        reason_code,
    )


def _safe_provider_usage(value: Any) -> dict[str, int | None]:
    usage = value if isinstance(value, dict) else {}

    def optional_non_negative_int(key: str) -> int | None:
        item = usage.get(key)
        if type(item) is not int or item < 0:
            return None
        return item

    return {
        "prompt_tokens": optional_non_negative_int("prompt_tokens"),
        "completion_tokens": optional_non_negative_int("completion_tokens"),
        "total_tokens": optional_non_negative_int("total_tokens"),
    }


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
    max_tokens: int = 1_200,
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
        "max_tokens": max_tokens,
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
    llm_max_tokens: int = 1_200,
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
                    max_tokens=llm_max_tokens,
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
    if isinstance(exc, _RerankResponseError):
        return exc.code
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
