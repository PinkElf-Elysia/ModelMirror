from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


RETRIEVAL_MODES = {"vector", "fulltext", "hybrid"}
RERANK_PROVIDERS = {"none", "auto", "api", "llm"}
ABSTENTION_SCORE_DOMAINS = {"vector_score", "evidence_verdict_v1"}
RRF_CONSTANT = 60


@dataclass(slots=True)
class RetrievalConfig:
    """Validated query-time settings pinned to a knowledge index version."""

    mode: str = "hybrid"
    vector_weight: float = 0.7
    fulltext_weight: float = 0.3
    top_k: int = 5
    score_threshold: float = 0.0
    candidate_multiplier: int = 4
    rerank_enabled: bool = False
    rerank_provider: str = "auto"
    rerank_model: str = ""
    rerank_top_n: int = 5
    abstention_enabled: bool = False
    abstention_score_domain: str = "vector_score"
    abstention_threshold: float = 0.0
    evidence_verification_enabled: bool = False

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

        abstention_enabled = _coerce_bool(
            current.get("abstention_enabled"), "abstention_enabled"
        )
        abstention_score_domain = str(
            current.get("abstention_score_domain") or "vector_score"
        ).strip().lower()
        if abstention_score_domain not in ABSTENTION_SCORE_DOMAINS:
            raise ValueError(
                "retrieval.abstention_score_domain must be vector_score; "
                "rank-derived fused_score is not answerability evidence."
            )
        abstention_threshold = _coerce_float(
            current.get("abstention_threshold"), "abstention_threshold"
        )
        if not -1 <= abstention_threshold <= 1:
            raise ValueError(
                "retrieval.abstention_threshold must be between -1 and 1."
            )
        if abstention_enabled and mode == "fulltext":
            raise ValueError(
                "retrieval abstention currently requires vector or hybrid retrieval."
            )
        evidence_verification_enabled = _coerce_bool(
            current.get("evidence_verification_enabled"),
            "evidence_verification_enabled",
        )
        if evidence_verification_enabled and (
            not rerank_enabled or provider != "llm"
        ):
            raise ValueError(
                "retrieval evidence verification requires rerank_provider=llm "
                "with rerank_enabled=true."
            )
        if evidence_verification_enabled:
            abstention_score_domain = "evidence_verdict_v1"

        return cls(
            mode=mode,
            vector_weight=round(vector_weight, 6),
            fulltext_weight=round(fulltext_weight, 6),
            top_k=top_k,
            score_threshold=threshold,
            candidate_multiplier=multiplier,
            rerank_enabled=rerank_enabled,
            rerank_provider=provider,
            rerank_model=rerank_model,
            rerank_top_n=rerank_top_n,
            abstention_enabled=abstention_enabled,
            abstention_score_domain=abstention_score_domain,
            abstention_threshold=round(abstention_threshold, 6),
            evidence_verification_enabled=evidence_verification_enabled,
        )

    def payload(self) -> dict[str, Any]:
        return asdict(self)


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
    """Apply a stable recall threshold and parent dedupe without reordering."""

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

    # Fusion and successful rerank already establish the authoritative order.
    # Forcing one result per document here can move a higher-scoring Gold block
    # below a fixed evaluation cutoff merely because another document exists.
    return deduplicated[:top_k]


def apply_abstention(
    items: list[RetrievalCandidate],
    config: RetrievalConfig,
) -> tuple[list[RetrievalCandidate], dict[str, Any]]:
    """Apply an explicit no-answer decision after ranking and selection.

    RRF fused scores intentionally never enter this decision: they describe
    relative rank, while abstention requires an absolute evidence domain.
    """

    input_count = len(items)
    decision: dict[str, Any] = {
        "abstention_enabled": bool(config.abstention_enabled),
        "abstention_applied": False,
        "abstained": False,
        "abstention_score_domain": config.abstention_score_domain,
        "abstention_threshold": config.abstention_threshold,
        "abstention_score": None,
        "abstention_input_count": input_count,
        "abstention_reason": "disabled",
    }
    if not items:
        decision.update(
            {
                "abstention_applied": True,
                "abstained": True,
                "abstention_reason": "no_candidates",
            }
        )
        return [], decision
    if not config.abstention_enabled:
        return items, decision

    decision["abstention_applied"] = True
    scores = [
        float(item.vector_score)
        for item in items
        if item.vector_score is not None
    ]
    if not scores:
        decision.update(
            {
                "abstained": True,
                "abstention_reason": "missing_vector_score",
            }
        )
        return [], decision

    evidence_score = max(scores)
    decision["abstention_score"] = round(evidence_score, 6)
    if evidence_score < config.abstention_threshold:
        decision.update(
            {
                "abstained": True,
                "abstention_reason": "below_threshold",
            }
        )
        return [], decision
    decision["abstention_reason"] = "accepted"
    return items, decision


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


def _coerce_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError(f"retrieval.{name} must be a boolean.")
