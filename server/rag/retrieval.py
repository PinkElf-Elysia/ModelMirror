from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


RETRIEVAL_MODES = {"vector", "fulltext", "hybrid"}
RERANK_PROVIDERS = {"none", "auto", "api", "llm"}
LEGACY_NO_RESULT_POLICY = "legacy_fused_threshold_v2"
ABSOLUTE_NO_RESULT_POLICY = "absolute_relevance_v1"
NO_RESULT_POLICIES = {LEGACY_NO_RESULT_POLICY, ABSOLUTE_NO_RESULT_POLICY}
RRF_CONSTANT = 60


@dataclass(slots=True)
class RetrievalConfig:
    """Validated query-time settings pinned to a knowledge index version."""

    mode: str = "hybrid"
    vector_weight: float = 0.7
    fulltext_weight: float = 0.3
    top_k: int = 5
    score_threshold: float = 0.0
    min_vector_similarity: float | None = None
    min_lexical_confidence: float | None = None
    min_rerank_score: float | None = None
    no_result_policy: str = LEGACY_NO_RESULT_POLICY
    candidate_multiplier: int = 4
    rerank_enabled: bool = False
    rerank_provider: str = "auto"
    rerank_model: str = ""
    rerank_top_n: int = 5

    @classmethod
    def from_mapping(
        cls,
        value: dict[str, Any] | None,
        *,
        base: RetrievalConfig | None = None,
    ) -> RetrievalConfig:
        current = asdict(base or cls())
        if value:
            current.update(value)

        mode = str(current.get("mode") or "hybrid").strip().lower()
        if mode not in RETRIEVAL_MODES:
            raise ValueError("retrieval.mode must be vector, fulltext, or hybrid.")

        vector_weight = _coerce_float(current.get("vector_weight"), "vector_weight")
        fulltext_weight = _coerce_float(current.get("fulltext_weight"), "fulltext_weight")
        if not 0 <= vector_weight <= 1 or not 0 <= fulltext_weight <= 1:
            raise ValueError("retrieval weights must be between 0 and 1.")
        if mode == "hybrid" and vector_weight + fulltext_weight <= 0:
            raise ValueError("hybrid retrieval requires a positive vector or fulltext weight.")
        weight_total = vector_weight + fulltext_weight
        if mode == "hybrid" and weight_total:
            vector_weight /= weight_total
            fulltext_weight /= weight_total

        top_k = _coerce_int(current.get("top_k"), "top_k")
        if not 1 <= top_k <= 50:
            raise ValueError("retrieval.top_k must be between 1 and 50.")

        threshold = _coerce_float(current.get("score_threshold"), "score_threshold")
        if not 0 <= threshold <= 1:
            raise ValueError("retrieval.score_threshold must be between 0 and 1.")

        min_vector_similarity = _coerce_optional_threshold(
            current.get("min_vector_similarity"),
            "min_vector_similarity",
        )
        min_lexical_confidence = _coerce_optional_threshold(
            current.get("min_lexical_confidence"),
            "min_lexical_confidence",
        )
        min_rerank_score = _coerce_optional_threshold(
            current.get("min_rerank_score"),
            "min_rerank_score",
        )
        no_result_policy = str(
            current.get("no_result_policy") or LEGACY_NO_RESULT_POLICY
        ).strip().lower()
        if no_result_policy not in NO_RESULT_POLICIES:
            raise ValueError(
                "retrieval.no_result_policy must be absolute_relevance_v1 or the legacy compatibility policy."
            )

        multiplier = _coerce_int(current.get("candidate_multiplier"), "candidate_multiplier")
        if not 1 <= multiplier <= 10:
            raise ValueError("retrieval.candidate_multiplier must be between 1 and 10.")

        provider = str(current.get("rerank_provider") or "auto").strip().lower()
        if provider not in RERANK_PROVIDERS:
            raise ValueError("retrieval.rerank_provider must be none, auto, api, or llm.")
        rerank_enabled = _coerce_bool(current.get("rerank_enabled"), "rerank_enabled")
        if provider == "none":
            rerank_enabled = False
        rerank_top_n = _coerce_int(current.get("rerank_top_n") or top_k, "rerank_top_n")
        if not 1 <= rerank_top_n <= 50:
            raise ValueError("retrieval.rerank_top_n must be between 1 and 50.")

        rerank_model = str(current.get("rerank_model") or "").strip()
        if len(rerank_model) > 200:
            raise ValueError("retrieval.rerank_model is too long.")

        return cls(
            mode=mode,
            vector_weight=round(vector_weight, 6),
            fulltext_weight=round(fulltext_weight, 6),
            top_k=top_k,
            score_threshold=threshold,
            min_vector_similarity=min_vector_similarity,
            min_lexical_confidence=min_lexical_confidence,
            min_rerank_score=min_rerank_score,
            no_result_policy=no_result_policy,
            candidate_multiplier=multiplier,
            rerank_enabled=rerank_enabled,
            rerank_provider=provider,
            rerank_model=rerank_model,
            rerank_top_n=rerank_top_n,
        )

    @property
    def uses_absolute_relevance(self) -> bool:
        return self.no_result_policy == ABSOLUTE_NO_RESULT_POLICY

    @property
    def required_threshold_score_domains(self) -> tuple[str, ...]:
        if not self.uses_absolute_relevance:
            return ()
        domains: list[str] = []
        if self.mode in {"vector", "hybrid"}:
            domains.append("vector_similarity")
        if self.mode in {"fulltext", "hybrid"}:
            domains.append("lexical_confidence")
        if self.rerank_enabled:
            domains.append("rerank_score")
        return tuple(domains)

    @property
    def threshold_contract_status(self) -> str:
        if not self.uses_absolute_relevance:
            return "legacy"
        values = {
            "vector_similarity": self.min_vector_similarity,
            "lexical_confidence": self.min_lexical_confidence,
            "rerank_score": self.min_rerank_score,
        }
        return (
            "configured"
            if all(values[domain] is not None for domain in self.required_threshold_score_domains)
            else "unconfigured"
        )

    @property
    def channel_thresholds_configured(self) -> bool:
        if not self.uses_absolute_relevance:
            return False
        return (
            self.min_vector_similarity is not None
            if self.mode == "vector"
            else self.min_lexical_confidence is not None
            if self.mode == "fulltext"
            else self.min_vector_similarity is not None
            and self.min_lexical_confidence is not None
        )

    def payload(self) -> dict[str, Any]:
        value = asdict(self)
        if self.uses_absolute_relevance:
            value.pop("score_threshold", None)
        else:
            value.pop("min_vector_similarity", None)
            value.pop("min_lexical_confidence", None)
            value.pop("min_rerank_score", None)
        value["threshold_contract_status"] = self.threshold_contract_status
        return value

    def filter_absolute_channels(
        self,
        vector_items: list[RetrievalCandidate],
        fulltext_items: list[RetrievalCandidate],
    ) -> tuple[
        list[RetrievalCandidate],
        list[RetrievalCandidate],
        list[dict[str, Any]],
    ]:
        """Apply V3 absolute gates before RRF and return a text-free receipt."""

        decisions: dict[str, dict[str, Any]] = {}

        def decision(item: RetrievalCandidate) -> dict[str, Any]:
            return decisions.setdefault(
                item.chunk_id,
                {
                    "chunk_id": item.chunk_id,
                    "vector_score": None,
                    "fulltext_score": None,
                    "vector_passed": None,
                    "fulltext_passed": None,
                    "accepted": True,
                    "raw_score_contract_valid": True,
                    "rejection_reason_codes": [],
                },
            )

        for item in vector_items:
            decision(item)["vector_score"] = item.vector_score
        for item in fulltext_items:
            decision(item)["fulltext_score"] = item.fulltext_score

        if not self.uses_absolute_relevance:
            return vector_items, fulltext_items, list(decisions.values())

        valid_vector_items: list[RetrievalCandidate] = []
        valid_lexical_items: list[RetrievalCandidate] = []
        for item in vector_items:
            score, reason = _absolute_candidate_score(
                item.vector_score,
                missing_reason="missing_vector_similarity",
                invalid_reason="invalid_vector_similarity",
            )
            current = decision(item)
            current["vector_score"] = score
            if reason:
                current["vector_passed"] = False
                current["accepted"] = False
                current["raw_score_contract_valid"] = False
                current["rejection_reason_codes"].append(reason)
            else:
                item.vector_score = score
                valid_vector_items.append(item)
        for item in fulltext_items:
            score, reason = _absolute_candidate_score(
                item.fulltext_score,
                missing_reason="missing_lexical_confidence",
                invalid_reason="invalid_lexical_confidence",
            )
            current = decision(item)
            current["fulltext_score"] = score
            if reason:
                current["fulltext_passed"] = False
                current["accepted"] = False
                current["raw_score_contract_valid"] = False
                current["rejection_reason_codes"].append(reason)
            else:
                item.fulltext_score = score
                valid_lexical_items.append(item)

        if not self.channel_thresholds_configured:
            valid_ids = {
                item.chunk_id for item in valid_vector_items + valid_lexical_items
            }
            for chunk_id, current in decisions.items():
                current["accepted"] = chunk_id in valid_ids
            return valid_vector_items, valid_lexical_items, list(decisions.values())

        vector_passed_ids: set[str] = set()
        lexical_passed_ids: set[str] = set()
        if self.mode in {"vector", "hybrid"}:
            threshold = float(self.min_vector_similarity)
            for item in valid_vector_items:
                current = decision(item)
                if float(item.vector_score) >= threshold:
                    current["vector_passed"] = True
                    vector_passed_ids.add(item.chunk_id)
                else:
                    current["vector_passed"] = False
                    current["rejection_reason_codes"].append(
                        "below_min_vector_similarity"
                    )
        if self.mode in {"fulltext", "hybrid"}:
            threshold = float(self.min_lexical_confidence)
            for item in valid_lexical_items:
                current = decision(item)
                if float(item.fulltext_score) >= threshold:
                    current["fulltext_passed"] = True
                    lexical_passed_ids.add(item.chunk_id)
                else:
                    current["fulltext_passed"] = False
                    current["rejection_reason_codes"].append(
                        "below_min_lexical_confidence"
                    )

        accepted_ids = vector_passed_ids | lexical_passed_ids
        for chunk_id, current in decisions.items():
            current["accepted"] = chunk_id in accepted_ids
        return (
            [item for item in valid_vector_items if item.chunk_id in vector_passed_ids],
            [item for item in valid_lexical_items if item.chunk_id in lexical_passed_ids],
            list(decisions.values()),
        )


@dataclass(slots=True)
class RetrievalCandidate:
    chunk_id: str
    doc_id: str
    document_name: str
    matched_text: str
    context_text: str
    parent_chunk_id: str | None = None
    chunk_type: str = "standard"
    start_char: int = 0
    end_char: int = 0
    page_number: int | None = None
    slide: int | None = None
    heading_path: tuple[str, ...] = ()
    sheet: str | None = None
    row_range: str | None = None
    visual_kind: str | None = None
    source_block_id: str | None = None
    vector_score: float | None = None
    fulltext_score: float | None = None
    fused_score: float = 0.0
    rerank_score: float | None = None

    @property
    def score(self) -> float:
        return self.rerank_score if self.rerank_score is not None else self.fused_score


def fuse_rankings(
    vector_items: list[RetrievalCandidate],
    fulltext_items: list[RetrievalCandidate],
    config: RetrievalConfig,
) -> list[RetrievalCandidate]:
    """Fuse ranked lists using weighted reciprocal rank fusion."""

    if config.mode == "vector":
        for item in vector_items:
            item.fused_score = max(0.0, min(1.0, float(item.vector_score or 0.0)))
        return sorted(vector_items, key=lambda item: (-item.fused_score, item.chunk_id))
    if config.mode == "fulltext":
        for item in fulltext_items:
            item.fused_score = max(0.0, min(1.0, float(item.fulltext_score or 0.0)))
        # The lexical store already orders by BM25. Confidence is used for
        # thresholding and must not replace the proven lexical rank order.
        return fulltext_items

    by_id: dict[str, RetrievalCandidate] = {}
    raw_scores: dict[str, float] = {}

    def merge(items: list[RetrievalCandidate], *, weight: float, score_field: str) -> None:
        for rank, item in enumerate(items, start=1):
            current = by_id.setdefault(item.chunk_id, item)
            source_score = getattr(item, score_field)
            if source_score is not None:
                setattr(current, score_field, source_score)
            raw_scores[item.chunk_id] = raw_scores.get(item.chunk_id, 0.0) + (
                weight / (RRF_CONSTANT + rank)
            )

    if config.mode in {"vector", "hybrid"}:
        merge(
            vector_items,
            weight=1.0 if config.mode == "vector" else config.vector_weight,
            score_field="vector_score",
        )
    if config.mode in {"fulltext", "hybrid"}:
        merge(
            fulltext_items,
            weight=1.0 if config.mode == "fulltext" else config.fulltext_weight,
            score_field="fulltext_score",
        )

    if not raw_scores:
        return []
    # Normalize against the theoretical rank-1 maximum, not the observed
    # candidate min/max. Observed min/max makes every result set manufacture a
    # score of both 0 and 1 and changes existing scores when a tail candidate is
    # added, so thresholds cannot be compared across queries or benchmarks.
    maximum = (
        config.vector_weight / (RRF_CONSTANT + 1)
        + config.fulltext_weight / (RRF_CONSTANT + 1)
    )
    for chunk_id, raw_score in raw_scores.items():
        by_id[chunk_id].fused_score = max(
            0.0,
            min(1.0, raw_score / maximum if maximum > 0 else 0.0),
        )
    return sorted(by_id.values(), key=lambda item: (-item.fused_score, item.chunk_id))


def select_candidates(
    items: list[RetrievalCandidate],
    *,
    score_threshold: float,
    top_k: int,
) -> list[RetrievalCandidate]:
    """Apply a stable recall threshold, parent dedupe, then document diversity."""

    eligible = [item for item in items if item.fused_score >= score_threshold]
    deduplicated: list[RetrievalCandidate] = []
    seen_parent_groups: set[tuple[str, str]] = set()
    for item in eligible:
        if item.parent_chunk_id:
            group = (item.doc_id, item.parent_chunk_id)
            if group in seen_parent_groups:
                continue
            seen_parent_groups.add(group)
        deduplicated.append(item)

    selected: list[RetrievalCandidate] = []
    deferred: list[RetrievalCandidate] = []
    seen_documents: set[str] = set()
    for item in deduplicated:
        if item.doc_id in seen_documents:
            deferred.append(item)
            continue
        selected.append(item)
        seen_documents.add(item.doc_id)
        if len(selected) >= top_k:
            return selected

    for item in deferred:
        selected.append(item)
        if len(selected) >= top_k:
            break
    return selected


def _coerce_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"retrieval.{name} must be an integer.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"retrieval.{name} must be an integer.") from exc


def _coerce_float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"retrieval.{name} must be a number.")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"retrieval.{name} must be a number.") from exc


def _coerce_optional_threshold(value: Any, name: str) -> float | None:
    if value is None:
        return None
    threshold = _coerce_float(value, name)
    if not 0 <= threshold <= 1:
        raise ValueError(f"retrieval.{name} must be between 0 and 1.")
    return threshold


def _absolute_candidate_score(
    value: Any,
    *,
    missing_reason: str,
    invalid_reason: str,
) -> tuple[float | None, str | None]:
    if value is None:
        return None, missing_reason
    if isinstance(value, bool):
        return None, invalid_reason
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None, invalid_reason
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        return None, invalid_reason
    return score, None


def _coerce_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError(f"retrieval.{name} must be a boolean.")
