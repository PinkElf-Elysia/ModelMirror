from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


RETRIEVAL_MODES = {"vector", "fulltext", "hybrid"}
RERANK_PROVIDERS = {"none", "auto", "api", "llm"}
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
        return sorted(fulltext_items, key=lambda item: (-item.fused_score, item.chunk_id))

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
    minimum = min(raw_scores.values())
    maximum = max(raw_scores.values())
    spread = maximum - minimum
    for chunk_id, raw_score in raw_scores.items():
        by_id[chunk_id].fused_score = 1.0 if spread == 0 else (raw_score - minimum) / spread
    return sorted(by_id.values(), key=lambda item: (-item.fused_score, item.chunk_id))


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
