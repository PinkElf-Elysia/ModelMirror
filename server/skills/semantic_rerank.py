from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .finder import (
    MAX_QUERY_LENGTH,
    MAX_RECALL_RESULTS,
    MAX_RESULTS,
    RANKER_VERSION,
    SkillFinder,
    SkillRuntimeIndexError,
    _normalize,
    rank_skill_candidates,
)


SEARCH_INDEX_VERSION = 1
SEMANTIC_DOCUMENT_VERSION = "skill-semantic-document-v1"
MAX_SEMANTIC_DOCUMENT_CHARACTERS = 1_200
SearchScope = Literal["market", "router"]
RerankStatus = Literal["lexical", "semantic", "lexical_fallback", "shadow"]


class SkillSearchIndexError(RuntimeError):
    code = "skill_search_index_invalid"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _fingerprint_without(value: dict[str, Any], key: str) -> str:
    return _fingerprint({name: item for name, item in value.items() if name != key})


@dataclass(frozen=True)
class SkillRerankRequest:
    query: str
    scope: SearchScope = "market"
    limit: int = MAX_RESULTS
    semantic: bool = False

    def __post_init__(self) -> None:
        if self.scope not in {"market", "router"}:
            raise ValueError("Skill rerank scope must be market or router.")
        if not 1 <= int(self.limit) <= MAX_RESULTS:
            raise ValueError(f"Skill rerank result limit must be between 1 and {MAX_RESULTS}.")
        if len(str(self.query or "")) > MAX_QUERY_LENGTH:
            raise ValueError(f"Skill rerank query must not exceed {MAX_QUERY_LENGTH} characters.")


@dataclass(frozen=True)
class SkillRankingReceipt:
    query_hash: str
    candidate_set_fingerprint: str
    lexical_ranks: tuple[str, ...]
    semantic_ranks: tuple[str, ...]
    final_ranks: tuple[str, ...]
    provider: str
    model: str | None
    strategy_version: str
    duration_ms: int
    fallback_reason: str | None = None

    def serialize(self) -> dict[str, Any]:
        return {
            "queryHash": self.query_hash,
            "candidateSetFingerprint": self.candidate_set_fingerprint,
            "lexicalRanks": list(self.lexical_ranks),
            "semanticRanks": list(self.semantic_ranks),
            "finalRanks": list(self.final_ranks),
            "provider": self.provider,
            "model": self.model,
            "strategyVersion": self.strategy_version,
            "durationMs": self.duration_ms,
            "fallbackReason": self.fallback_reason,
        }


@dataclass(frozen=True)
class SkillRerankOutcome:
    lexical_results: tuple[dict[str, Any], ...]
    final_results: tuple[dict[str, Any], ...]
    status: RerankStatus
    warnings: tuple[str, ...]
    receipt: SkillRankingReceipt

    def serialize(self) -> dict[str, Any]:
        return {
            "lexicalResults": list(self.lexical_results),
            "finalResults": list(self.final_results),
            "status": self.status,
            "warnings": list(self.warnings),
            "receipt": self.receipt.serialize(),
        }


class SkillSearchIndexV1:
    def __init__(
        self,
        *,
        index_path: str | Path | None = None,
        runtime_index_path: str | Path | None = None,
        trust_index_path: str | Path | None = None,
        client_summary_path: str | Path | None = None,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        self.index_path = Path(
            index_path or Path(__file__).resolve().parent / "data" / "skill_search_index.json"
        )
        self.runtime_index_path = Path(
            runtime_index_path
            or Path(__file__).resolve().parent / "data" / "skill_runtime_index.json"
        )
        self.trust_index_path = Path(
            trust_index_path
            or Path(__file__).resolve().parent / "data" / "skill_trust_index.json"
        )
        self.client_summary_path = Path(
            client_summary_path
            or root / "client" / "src" / "data" / "skillSearchIndex.generated.json"
        )
        self._payload: dict[str, Any] | None = None
        self._runtime_by_id: dict[str, dict[str, Any]] = {}

    @property
    def fingerprint(self) -> str:
        return str(self._load()["fingerprint"])

    def _read_json(self, path: Path, *, label: str) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SkillSearchIndexError(f"{label} is unavailable.") from exc
        if not isinstance(payload, dict):
            raise SkillSearchIndexError(f"{label} is invalid.")
        return payload

    def _load(self) -> dict[str, Any]:
        if self._payload is not None:
            return self._payload
        payload = self._read_json(self.index_path, label="Skill Search index")
        summary = self._read_json(self.client_summary_path, label="Skill Search client summary")
        trust = self._read_json(self.trust_index_path, label="Skill Trust index")
        try:
            runtime = SkillFinder(index_path=self.runtime_index_path)._load_index()
        except SkillRuntimeIndexError as exc:
            raise SkillSearchIndexError("Skill Runtime index is invalid.") from exc
        if (
            payload.get("version") != SEARCH_INDEX_VERSION
            or payload.get("rankerVersion") != RANKER_VERSION
            or payload.get("semanticDocumentVersion") != SEMANTIC_DOCUMENT_VERSION
            or not isinstance(payload.get("candidates"), list)
            or not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("fingerprint") or ""))
            or _fingerprint_without(payload, "fingerprint") != payload.get("fingerprint")
        ):
            raise SkillSearchIndexError("Skill Search index fingerprint is invalid.")
        if (
            _fingerprint_without(trust, "fingerprint") != trust.get("fingerprint")
            or payload.get("directoryFingerprint") != runtime.get("catalogFingerprint")
            or payload.get("directoryFingerprint") != trust.get("catalogFingerprint")
            or payload.get("runtimeIndexFingerprint") != runtime.get("fingerprint")
            or payload.get("trustIndexFingerprint") != trust.get("fingerprint")
            or payload.get("memberIndexFingerprint") != runtime.get("memberIndexFingerprint")
        ):
            raise SkillSearchIndexError(
                "Skill Search, Runtime, and Trust indexes do not share one directory state."
            )

        runtime_by_id = {
            str(candidate["candidateId"]): candidate for candidate in runtime["candidates"]
        }
        candidates_by_id: dict[str, dict[str, Any]] = {}
        catalog_pairs: list[dict[str, str]] = []
        for candidate in payload["candidates"]:
            if not isinstance(candidate, dict):
                raise SkillSearchIndexError("Skill Search index contains an invalid candidate.")
            candidate_id = str(candidate.get("candidateId") or "")
            semantic_document = candidate.get("semanticDocument")
            fingerprint = str(candidate.get("candidateFingerprint") or "")
            candidate_payload = {
                key: value for key, value in candidate.items() if key != "candidateFingerprint"
            }
            if (
                not candidate_id.startswith("catalog:")
                or candidate_id in candidates_by_id
                or candidate.get("sourceType") != "catalog"
                or candidate.get("targetType") not in {"project", "member"}
                or candidate.get("installStatus")
                not in {"ready", "manual", "pending", "reference"}
                or not isinstance(semantic_document, str)
                or not semantic_document
                or len(semantic_document) > MAX_SEMANTIC_DOCUMENT_CHARACTERS
                or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
                or _fingerprint(candidate_payload) != fingerprint
            ):
                raise SkillSearchIndexError("Skill Search candidate fingerprint is invalid.")
            runtime_candidate = runtime_by_id.get(candidate_id)
            if runtime_candidate:
                if (
                    candidate.get("runtimeCandidateFingerprint")
                    != runtime_candidate.get("candidateFingerprint")
                    or candidate.get("trustFingerprint")
                    != (runtime_candidate.get("trust") or {}).get("trustFingerprint")
                ):
                    raise SkillSearchIndexError(
                        "Skill Search candidate Runtime binding is invalid."
                    )
            elif candidate.get("runtimeCandidateFingerprint") is not None:
                raise SkillSearchIndexError(
                    "Skill Search candidate references an unknown Runtime candidate."
                )
            candidates_by_id[candidate_id] = candidate
            catalog_pairs.append(
                {"candidateId": candidate_id, "candidateFingerprint": fingerprint}
            )
        if set(runtime_by_id) - set(candidates_by_id):
            raise SkillSearchIndexError("Skill Search index does not cover Runtime candidates.")
        if _fingerprint(catalog_pairs) != payload.get("searchCatalogFingerprint"):
            raise SkillSearchIndexError("Skill Search catalog fingerprint is invalid.")

        if (
            _fingerprint_without(summary, "fingerprint") != summary.get("fingerprint")
            or summary.get("searchIndexFingerprint") != payload.get("fingerprint")
            or summary.get("searchCatalogFingerprint")
            != payload.get("searchCatalogFingerprint")
            or summary.get("runtimeIndexFingerprint") != runtime.get("fingerprint")
            or summary.get("trustIndexFingerprint") != trust.get("fingerprint")
            or summary.get("directoryFingerprint") != payload.get("directoryFingerprint")
            or summary.get("candidateCount") != len(candidates_by_id)
            or summary.get("runtimeBoundCandidateCount") != len(runtime_by_id)
        ):
            raise SkillSearchIndexError("Skill Search client summary is stale or invalid.")
        self._runtime_by_id = runtime_by_id
        self._payload = payload
        return payload

    def candidates(self, *, scope: SearchScope = "market") -> list[dict[str, Any]]:
        payload = self._load()
        if scope == "market":
            return list(payload["candidates"])
        if scope != "router":
            raise ValueError("Skill search scope must be market or router.")
        return [
            candidate
            for candidate in payload["candidates"]
            if bool(
                (
                    self._runtime_by_id.get(candidate["candidateId"], {}).get("trust")
                    or {}
                ).get("routerEligible")
            )
        ]

    def lexical_search(self, request: SkillRerankRequest) -> SkillRerankOutcome:
        started = time.perf_counter()
        candidates = self.candidates(scope=request.scope)
        status_boost = {"ready": 0.5, "pending": 0.25, "manual": 0.1, "reference": 0.0}
        ranked = rank_skill_candidates(
            request.query,
            candidates,
            limit=MAX_RECALL_RESULTS,
            max_results=MAX_RECALL_RESULTS,
            score_boost=(
                (lambda candidate: status_boost.get(candidate["installStatus"], 0.0))
                if request.scope == "market"
                else None
            ),
        )
        lexical_results = tuple(
            {
                "candidateId": match["candidate"]["candidateId"],
                "candidateFingerprint": match["candidate"]["candidateFingerprint"],
                "runtimeCandidateFingerprint": match["candidate"].get(
                    "runtimeCandidateFingerprint"
                ),
                "name": match["candidate"]["name"],
                "summary": match["candidate"]["description"],
                "category": match["candidate"]["category"],
                "kind": match["candidate"]["kind"],
                "installStatus": match["candidate"]["installStatus"],
                "score": match["score"],
                "reasons": match["reasons"],
            }
            for match in ranked
        )
        final_results = lexical_results[: request.limit]
        candidate_set_fingerprint = (
            self._load()["searchCatalogFingerprint"]
            if request.scope == "market"
            else _fingerprint(
                [
                    {
                        "candidateId": candidate["candidateId"],
                        "candidateFingerprint": candidate["candidateFingerprint"],
                    }
                    for candidate in candidates
                ]
            )
        )
        normalized_query = _normalize(request.query[:MAX_QUERY_LENGTH])
        warnings = (
            ("semantic_rerank_not_configured",) if request.semantic else tuple()
        )
        receipt = SkillRankingReceipt(
            query_hash=hashlib.sha256(normalized_query.encode("utf-8")).hexdigest(),
            candidate_set_fingerprint=candidate_set_fingerprint,
            lexical_ranks=tuple(item["candidateId"] for item in lexical_results),
            semantic_ranks=tuple(),
            final_ranks=tuple(item["candidateId"] for item in final_results),
            provider="none",
            model=None,
            strategy_version=RANKER_VERSION,
            duration_ms=max(0, round((time.perf_counter() - started) * 1_000)),
            fallback_reason=("provider_disabled" if request.semantic else None),
        )
        return SkillRerankOutcome(
            lexical_results=lexical_results,
            final_results=final_results,
            status="lexical_fallback" if request.semantic else "lexical",
            warnings=warnings,
            receipt=receipt,
        )


__all__ = [
    "MAX_SEMANTIC_DOCUMENT_CHARACTERS",
    "SEARCH_INDEX_VERSION",
    "SEMANTIC_DOCUMENT_VERSION",
    "SkillRankingReceipt",
    "SkillRerankOutcome",
    "SkillRerankRequest",
    "SkillSearchIndexError",
    "SkillSearchIndexV1",
]
