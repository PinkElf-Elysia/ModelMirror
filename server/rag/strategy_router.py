from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .retrieval import RetrievalConfig

if TYPE_CHECKING:
    from .rag_service import RagService


RULES_VERSION = "rag-strategy-rules-v1"
SUPPORTED_OBJECTIVES = {"balanced", "quality", "low_latency"}
REQUIREMENT_KEYS = (
    "exact_terms",
    "semantic_rewrite",
    "cross_language",
    "long_context",
    "confusable_content",
    "citation_precision",
)
MAX_PROFILE_DOCUMENTS = 100
MAX_PROFILE_CHARACTERS = 500_000

_EXACT_TERM = re.compile(
    r"(?:\b[A-Z][A-Z0-9_./-]{2,}\b|\b[A-Za-z]{1,12}-\d{2,}\b|\b\d{4,}\b)"
)
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN_WORD = re.compile(r"\b[A-Za-z]{2,}\b")


class RagStrategyRecommendationNotFoundError(KeyError):
    pass


class RagStrategyConflictError(RuntimeError):
    pass


class RagStrategyStateError(RuntimeError):
    pass


class RagStrategyValidationError(ValueError):
    pass


class RagStrategyService:
    """Deterministic, explainable strategy recommendations over the existing RAG draft."""

    def __init__(self, rag_service: RagService) -> None:
        self.rag_service = rag_service

    def capabilities(self) -> dict[str, Any]:
        embedding = self.rag_service.retrieval_capabilities().get("embedding", {})
        rerank = self.rag_service.reranker.capabilities()
        return {
            "rules_version": RULES_VERSION,
            "objectives": sorted(SUPPORTED_OBJECTIVES),
            "requirements": list(REQUIREMENT_KEYS),
            "limits": {
                "max_documents": MAX_PROFILE_DOCUMENTS,
                "max_characters": MAX_PROFILE_CHARACTERS,
                "max_alternatives": 2,
            },
            "chunking_strategies": ["recursive_character", "parent_child"],
            "retrieval_modes": ["fulltext", "vector", "hybrid"],
            "score_threshold_fixed": 0.0,
            "embedding": {
                "provider": str(embedding.get("provider") or "hash"),
                "degraded": bool(embedding.get("degraded", True)),
            },
            "rerank": {
                "available": bool(
                    rerank.get("api_configured") or rerank.get("llm_configured")
                ),
                "api_configured": bool(rerank.get("api_configured")),
                "llm_configured": bool(rerank.get("llm_configured")),
            },
            "deferred_strategies": [
                "sentence_window",
                "semantic_chunking",
                "contextual_retrieval",
                "late_chunking",
                "raptor",
            ],
            "rules": [
                {
                    "rule_id": f"R{index}",
                    "classification": classification,
                    "summary": summary,
                }
                for index, classification, summary in _RULE_SUMMARIES
            ],
        }

    def create_recommendation(
        self,
        kb_id: str,
        *,
        objective: str,
        requirements: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_objective = str(objective or "balanced").strip().lower()
        if clean_objective not in SUPPORTED_OBJECTIVES:
            raise RagStrategyValidationError(
                "objective must be balanced, quality, or low_latency."
            )
        normalized_requirements = self._normalize_requirements(requirements)
        snapshot = self._build_snapshot(kb_id, clean_objective, normalized_requirements)
        recommendation = self._recommend(snapshot)
        now = time.time()
        record = {
            "recommendation_id": f"ragrec_{uuid.uuid4().hex}",
            "kb_id": kb_id,
            "rules_version": RULES_VERSION,
            "state": recommendation["state"],
            "objective": clean_objective,
            "requirements": normalized_requirements,
            "corpus_hash": snapshot["corpus_hash"],
            "snapshot_hash": snapshot["snapshot_hash"],
            "target_version_id": snapshot["target_version_id"],
            "draft_id": snapshot["draft_id"],
            "draft_version": snapshot["draft_version"],
            "corpus_profile": snapshot["corpus_profile"],
            "current_profile": snapshot["current_profile"],
            "profiles": recommendation["profiles"],
            "warnings": recommendation["warnings"],
            "insufficient_reasons": recommendation["insufficient_reasons"],
            "recommendation_checksum": recommendation["recommendation_checksum"],
            "created_at": now,
            "updated_at": now,
            "applied_at": None,
            "applied_profile_id": None,
            "applied_draft_version": None,
        }
        with self.rag_service._metadata_lock:
            metadata = self.rag_service._read_metadata_unlocked()
            self.rag_service._ensure_kb_exists(metadata, kb_id)
            metadata["rag_strategy_recommendations"][record["recommendation_id"]] = record
            self.rag_service._write_metadata_unlocked(metadata)
        return self._payload(record, state=record["state"])

    def list_recommendations(self, kb_id: str) -> list[dict[str, Any]]:
        metadata = self.rag_service._read_metadata()
        self.rag_service._ensure_kb_exists(metadata, kb_id)
        records = [
            item
            for item in metadata["rag_strategy_recommendations"].values()
            if isinstance(item, dict) and str(item.get("kb_id")) == kb_id
        ]
        return [
            self._payload(item, state=self._current_state(item, metadata))
            for item in sorted(
                records,
                key=lambda value: float(value.get("created_at") or 0),
                reverse=True,
            )
        ]

    def get_recommendation(self, recommendation_id: str) -> dict[str, Any]:
        metadata = self.rag_service._read_metadata()
        record = metadata["rag_strategy_recommendations"].get(recommendation_id)
        if not isinstance(record, dict):
            raise RagStrategyRecommendationNotFoundError(
                "RAG strategy recommendation not found."
            )
        return self._payload(record, state=self._current_state(record, metadata))

    def apply_recommendation(
        self,
        recommendation_id: str,
        *,
        expected_draft_version: int,
        profile_id: str = "primary",
        confirm_low_confidence: bool = False,
    ) -> dict[str, Any]:
        with self.rag_service._metadata_lock:
            metadata = self.rag_service._read_metadata_unlocked()
            record = metadata["rag_strategy_recommendations"].get(recommendation_id)
            if not isinstance(record, dict):
                raise RagStrategyRecommendationNotFoundError(
                    "RAG strategy recommendation not found."
                )
            kb_id = str(record.get("kb_id") or "")
            self.rag_service._ensure_kb_exists(metadata, kb_id)
            state = self._current_state(record, metadata)
            if state == "stale":
                raise RagStrategyConflictError(
                    "Recommendation is stale; analyze the current corpus and draft again."
                )
            if state == "insufficient_data":
                raise RagStrategyStateError(
                    "Insufficient evidence recommendations cannot be applied."
                )
            if state == "applied":
                raise RagStrategyStateError("Recommendation has already been applied.")

            draft = self.rag_service._pipeline_draft_record(metadata, kb_id)
            current_version = int(draft["version"])
            if int(expected_draft_version) != current_version:
                raise RagStrategyConflictError(
                    "Pipeline draft changed; reload before applying the recommendation."
                )
            if current_version != int(record.get("draft_version") or 0):
                raise RagStrategyConflictError(
                    "Recommendation was created for an older pipeline draft."
                )

            selected = next(
                (
                    item
                    for item in record.get("profiles", [])
                    if isinstance(item, dict)
                    and str(item.get("profile_id") or "") == profile_id
                ),
                None,
            )
            if not isinstance(selected, dict):
                raise RagStrategyValidationError("Recommendation profile not found.")
            if (
                str(selected.get("confidence") or "low") == "low"
                and not confirm_low_confidence
            ):
                raise RagStrategyStateError(
                    "Low confidence recommendations require explicit confirmation."
                )

            draft_payload = self.rag_service.update_pipeline_draft(
                kb_id,
                {
                    "stage_chunker": {
                        "config": dict(selected.get("chunker") or {})
                    }
                },
                retrieval_profile=dict(selected.get("retrieval") or {}),
            )
            latest = self.rag_service._read_metadata_unlocked()
            stored = latest["rag_strategy_recommendations"].get(recommendation_id)
            if isinstance(stored, dict):
                stored["state"] = "applied"
                stored["applied_at"] = time.time()
                stored["updated_at"] = stored["applied_at"]
                stored["applied_profile_id"] = profile_id
                stored["applied_draft_version"] = int(draft_payload["version"])
                self.rag_service._write_metadata_unlocked(latest)
                record = stored
            return {
                "recommendation": self._payload(record, state="applied"),
                "draft": draft_payload,
            }

    def _build_snapshot(
        self,
        kb_id: str,
        objective: str,
        requirements: dict[str, bool],
    ) -> dict[str, Any]:
        metadata = self.rag_service._read_metadata()
        self.rag_service._ensure_kb_exists(metadata, kb_id)
        draft = self.rag_service._pipeline_draft_record(metadata, kb_id)
        documents = self._documents_for_kb(metadata, kb_id)
        corpus_hash = self._corpus_hash(documents)
        corpus_profile = self._profile_corpus(documents, draft)
        active_version_id = metadata["pipeline_active_versions"].get(kb_id)
        current_profile = {
            "chunker": json.loads(json.dumps(draft["stages"]["stage_chunker"])),
            "retrieval": json.loads(json.dumps(draft["retrieval_profile"])),
            "embedding": json.loads(json.dumps(draft["embedding_profile"])),
        }
        snapshot_payload = {
            "rules_version": RULES_VERSION,
            "kb_id": kb_id,
            "corpus_hash": corpus_hash,
            "target_version_id": active_version_id,
            "draft_id": draft["draft_id"],
            "draft_version": int(draft["version"]),
            "objective": objective,
            "requirements": requirements,
            "corpus_profile": corpus_profile,
            "current_profile": current_profile,
        }
        return {
            **snapshot_payload,
            "snapshot_hash": _checksum(snapshot_payload),
        }

    def _profile_corpus(
        self,
        documents: list[dict[str, Any]],
        draft: dict[str, Any],
    ) -> dict[str, Any]:
        sampled = documents[:MAX_PROFILE_DOCUMENTS]
        block_lengths: list[int] = []
        kind_counts: Counter[str] = Counter()
        exact_term_count = 0
        cjk_count = 0
        latin_word_count = 0
        analyzed_characters = 0
        analyzed_documents = 0
        visual_documents = 0
        skipped_documents = 0
        warning_counts: Counter[str] = Counter()
        max_heading_depth = 0
        character_limit_reached = False
        processor_config = dict(draft["stages"].get("stage_processor") or {})

        for document in sampled:
            if analyzed_characters >= MAX_PROFILE_CHARACTERS:
                character_limit_reached = True
                break
            filename = str(document.get("filename") or "")
            if bool(document.get("visual_candidate")):
                visual_documents += 1
            path = Path(str(document.get("stored_path") or ""))
            if not path.is_file():
                skipped_documents += 1
                warning_counts["source unavailable"] += 1
                continue
            try:
                processed = self.rag_service.document_processor.process(
                    path,
                    filename=filename,
                    source_id=str(document.get("id") or ""),
                    config=processor_config,
                )
            except Exception as exc:  # Parsing failures are a profile warning, not an API failure.
                skipped_documents += 1
                warning_counts[type(exc).__name__] += 1
                continue
            analyzed_documents += 1
            for block in processed.blocks:
                remaining = MAX_PROFILE_CHARACTERS - analyzed_characters
                if remaining <= 0:
                    character_limit_reached = True
                    break
                text = block.text[:remaining]
                if not text:
                    continue
                length = len(text)
                analyzed_characters += length
                block_lengths.append(length)
                kind_counts[str(block.kind or "paragraph")] += 1
                exact_term_count += len(_EXACT_TERM.findall(text))
                cjk_count += len(_CJK.findall(text))
                latin_word_count += len(_LATIN_WORD.findall(text))
                max_heading_depth = max(max_heading_depth, len(block.heading_path))
                if length < len(block.text):
                    character_limit_reached = True
                    break
            if character_limit_reached:
                break

        block_count = len(block_lengths)
        structured = sum(
            kind_counts.get(kind, 0) for kind in ("heading", "list", "table", "code")
        )
        table_code = kind_counts.get("table", 0) + kind_counts.get("code", 0)
        language_units = cjk_count + latin_word_count
        warnings = [
            f"{count} document(s) could not be analyzed ({reason})."
            for reason, count in sorted(warning_counts.items())
        ]
        return {
            "document_count": len(documents),
            "sampled_document_count": len(sampled),
            "analyzed_document_count": analyzed_documents,
            "skipped_document_count": skipped_documents,
            "visual_document_count": visual_documents,
            "sampled_character_count": analyzed_characters,
            "character_limit_reached": character_limit_reached,
            "document_limit_reached": len(documents) > len(sampled),
            "block_count": block_count,
            "block_kind_counts": dict(sorted(kind_counts.items())),
            "median_block_characters": _percentile(block_lengths, 0.5),
            "p95_block_characters": _percentile(block_lengths, 0.95),
            "max_block_characters": max(block_lengths, default=0),
            "short_block_ratio": _ratio(
                sum(1 for value in block_lengths if value < 700), block_count
            ),
            "long_block_ratio": _ratio(
                sum(1 for value in block_lengths if value >= 1200), block_count
            ),
            "structured_block_ratio": _ratio(structured, block_count),
            "table_code_ratio": _ratio(table_code, block_count),
            "heading_depth_max": max_heading_depth,
            "exact_term_density_per_1000": round(
                exact_term_count * 1000 / max(1, analyzed_characters), 4
            ),
            "cjk_ratio": _ratio(cjk_count, language_units),
            "latin_ratio": _ratio(latin_word_count, language_units),
            "bilingual": bool(cjk_count >= 20 and latin_word_count >= 20),
            "warnings": warnings[:20],
        }

    def _recommend(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        profile = snapshot["corpus_profile"]
        requirements = snapshot["requirements"]
        objective = snapshot["objective"]
        current = snapshot["current_profile"]
        embedding = current["embedding"]
        embedding_degraded = bool(embedding.get("degraded")) or str(
            embedding.get("provider") or "hash"
        ) == "hash"
        rerank_capabilities = self.rag_service.reranker.capabilities()
        rerank_ready = bool(
            rerank_capabilities.get("api_configured")
            or rerank_capabilities.get("llm_configured")
        )

        warnings = list(profile.get("warnings") or [])
        insufficient_reasons: list[str] = []
        if int(profile.get("analyzed_document_count") or 0) == 0:
            insufficient_reasons.append("No text document could be analyzed.")
        if int(profile.get("sampled_character_count") or 0) < 500:
            insufficient_reasons.append(
                "The analyzed corpus is too small for a defensible strategy recommendation."
            )
        if embedding_degraded and (
            requirements["semantic_rewrite"] or requirements["cross_language"]
        ) and not requirements["exact_terms"]:
            insufficient_reasons.append(
                "Semantic or cross-language routing requires a real embedding provider; hash fallback is insufficient."
            )
        if profile.get("visual_document_count"):
            warnings.append(
                "Visual candidates are represented only by text that the current processor can parse; Router V1 does not change vision settings."
            )

        if insufficient_reasons:
            checksum = _checksum(
                {
                    "snapshot_hash": snapshot["snapshot_hash"],
                    "state": "insufficient_data",
                    "reasons": insufficient_reasons,
                }
            )
            return {
                "state": "insufficient_data",
                "profiles": [],
                "warnings": _dedupe(warnings),
                "insufficient_reasons": insufficient_reasons,
                "recommendation_checksum": checksum,
            }

        chunker, chunk_rules, chunk_reasons, confidence = self._select_chunker(
            profile, objective, requirements, current["chunker"]
        )
        retrieval, retrieval_rules, retrieval_reasons, retrieval_confidence = (
            self._select_retrieval(
                profile,
                objective,
                requirements,
                embedding_degraded=embedding_degraded,
            )
        )
        confidence = _lower_confidence(confidence, retrieval_confidence)
        if requirements["confusable_content"] and objective == "quality":
            if rerank_ready:
                warnings.append(
                    "Rerank is available but remains an evaluation-required alternative; the primary profile keeps it disabled."
                )
            else:
                warnings.append(
                    "Confusable content may benefit from rerank, but no rerank provider is ready."
                )
        if embedding_degraded:
            warnings.append(
                "Embedding is using deterministic hash fallback; vector and hybrid quality are not treated as semantic evidence."
            )
        warnings.append(
            "Score threshold remains 0 until a fixed evaluation set calibrates abstention."
        )

        profiles: list[dict[str, Any]] = []
        profiles.append(
            self._profile_payload(
                "primary",
                "Recommended starting profile",
                chunker,
                retrieval,
                confidence=confidence,
                reasons=[*chunk_reasons, *retrieval_reasons],
                rule_ids=[*chunk_rules, *retrieval_rules],
                warnings=warnings,
                current=current,
            )
        )

        alternative_chunker = self._alternative_chunker(chunker, profile)
        if alternative_chunker is not None:
            profiles.append(
                self._profile_payload(
                    "alternative_chunking",
                    "Chunking comparison candidate",
                    alternative_chunker,
                    retrieval,
                    confidence="low",
                    reasons=[
                        "Use as a controlled candidate when the primary chunking evidence is weak."
                    ],
                    rule_ids=["R1", "R2"],
                    warnings=[
                        "This alternative requires a fixed knowledge evaluation set before activation."
                    ],
                    current=current,
                )
            )
        if (
            len(profiles) < 3
            and requirements["confusable_content"]
            and objective == "quality"
            and rerank_ready
        ):
            rerank_retrieval = dict(retrieval)
            rerank_retrieval.update(
                {
                    "rerank_enabled": True,
                    "rerank_provider": "auto",
                    "rerank_top_n": int(retrieval["top_k"]),
                }
            )
            profiles.append(
                self._profile_payload(
                    "alternative_rerank",
                    "Rerank evaluation candidate",
                    chunker,
                    rerank_retrieval,
                    confidence="low",
                    reasons=[
                        "Confusable content and a quality objective justify testing rerank, not enabling it by default."
                    ],
                    rule_ids=["R8"],
                    warnings=[
                        "Provider latency, cost, and ranking benefit were not measured in Phase A."
                    ],
                    current=current,
                )
            )

        checksum = _checksum(
            {
                "snapshot_hash": snapshot["snapshot_hash"],
                "profiles": profiles,
                "warnings": _dedupe(warnings),
            }
        )
        return {
            "state": "ready",
            "profiles": profiles[:3],
            "warnings": _dedupe(warnings),
            "insufficient_reasons": [],
            "recommendation_checksum": checksum,
        }

    def _select_chunker(
        self,
        corpus: dict[str, Any],
        objective: str,
        requirements: dict[str, bool],
        current: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str], list[str], str]:
        long_ratio = float(corpus.get("long_block_ratio") or 0)
        short_ratio = float(corpus.get("short_block_ratio") or 0)
        table_code_ratio = float(corpus.get("table_code_ratio") or 0)
        p95 = int(corpus.get("p95_block_characters") or 0)

        if (
            long_ratio >= 0.2
            and requirements["long_context"]
            and objective == "quality"
        ):
            return (
                _parent_child_config(1800, 450),
                ["R2", "R6"],
                [
                    "Long processed blocks and a quality/long-context objective make parent expansion worth evaluating.",
                    "The current implementation builds parents within each processed block, not across whole chapters.",
                ],
                "low",
            )
        if table_code_ratio >= 0.08:
            return (
                _recursive_config(1000, 100),
                ["R6", "R7"],
                [
                    "Table/code structure is already preserved by the processor; a moderate recursive window limits destructive splitting."
                ],
                "medium",
            )
        if requirements["confusable_content"] or objective == "low_latency":
            return (
                _recursive_config(1000, 100),
                ["R6", "R9"],
                [
                    "A larger recursive window reduces chunk count while retaining a 10% boundary overlap."
                ],
                "low",
            )
        if short_ratio >= 0.8 and p95 <= 700:
            keep = dict(current)
            keep["strategy"] = str(keep.get("strategy") or "recursive_character")
            return (
                keep,
                ["R1"],
                [
                    "Most processed blocks are already short; changing chunk sizes may be a no-op, so the current chunker is preserved."
                ],
                "low",
            )
        return (
            _recursive_config(700, 70),
            ["R1", "R6"],
            [
                "The corpus has no strong hierarchical or structure-specific signal; Recursive 700/70 is a neutral benchmark starting point."
            ],
            "low",
        )

    def _select_retrieval(
        self,
        corpus: dict[str, Any],
        objective: str,
        requirements: dict[str, bool],
        *,
        embedding_degraded: bool,
    ) -> tuple[dict[str, Any], list[str], list[str], str]:
        exact_signal = bool(requirements["exact_terms"]) or float(
            corpus.get("exact_term_density_per_1000") or 0
        ) >= 1.0
        semantic_signal = bool(
            requirements["semantic_rewrite"] or requirements["cross_language"]
        )
        top_k = 10 if requirements["long_context"] else 5
        rules = ["R9" if top_k == 5 else "R10", "R11"]
        reasons: list[str] = []
        confidence = "low"
        if embedding_degraded:
            mode = "fulltext"
            weights = (0.0, 1.0)
            rules.append("R3" if exact_signal else "R4")
            reasons.append(
                "Hash embedding is not semantic evidence; FTS5 is the defensible offline retrieval channel."
            )
            if exact_signal:
                reasons.append(
                    "The corpus or requirement contains exact identifiers and terminology suited to full-text retrieval."
                )
                confidence = "medium"
        elif exact_signal and semantic_signal:
            mode = "hybrid"
            weights = (0.7, 0.3)
            rules.append("R5")
            reasons.append(
                "Exact-term and semantic requirements justify a Hybrid candidate with the existing 0.7/0.3 starting weights."
            )
        elif semantic_signal:
            mode = "vector"
            weights = (1.0, 0.0)
            rules.append("R5")
            reasons.append(
                "A real embedding provider and semantic requirement support a Vector starting candidate."
            )
        elif exact_signal:
            mode = "fulltext"
            weights = (0.0, 1.0)
            rules.append("R3")
            reasons.append(
                "Exact identifiers and terminology favor full-text retrieval."
            )
            confidence = "medium"
        else:
            mode = "hybrid"
            weights = (0.7, 0.3)
            rules.append("R5")
            reasons.append(
                "No dominant lexical or semantic signal was detected; Hybrid remains a low-confidence starting candidate."
            )

        if objective == "low_latency":
            top_k = 5
        return (
            RetrievalConfig.from_mapping(
                {
                    "mode": mode,
                    "vector_weight": weights[0],
                    "fulltext_weight": weights[1],
                    "top_k": top_k,
                    "score_threshold": 0.0,
                    "candidate_multiplier": 4,
                    "rerank_enabled": False,
                    "rerank_provider": "none",
                    "rerank_model": "",
                    "rerank_top_n": top_k,
                }
            ).payload(),
            rules,
            reasons,
            confidence,
        )

    def _alternative_chunker(
        self,
        primary: dict[str, Any],
        corpus: dict[str, Any],
    ) -> dict[str, Any] | None:
        if str(primary.get("strategy")) == "parent_child":
            return _recursive_config(1000, 100)
        if float(corpus.get("long_block_ratio") or 0) >= 0.2:
            return _parent_child_config(1800, 450)
        return None

    def _profile_payload(
        self,
        profile_id: str,
        title: str,
        chunker: dict[str, Any],
        retrieval: dict[str, Any],
        *,
        confidence: str,
        reasons: list[str],
        rule_ids: list[str],
        warnings: list[str],
        current: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "profile_id": profile_id,
            "title": title,
            "confidence": confidence,
            "chunker": json.loads(json.dumps(chunker)),
            "retrieval": json.loads(json.dumps(retrieval)),
            "reasons": _dedupe(reasons),
            "evidence": [
                _RULE_EVIDENCE[rule_id]
                for rule_id in _dedupe(rule_ids)
                if rule_id in _RULE_EVIDENCE
            ],
            "warnings": _dedupe(warnings),
            "diff": _profile_diff(current, chunker, retrieval),
        }
        payload["checksum"] = _checksum(
            {"chunker": payload["chunker"], "retrieval": payload["retrieval"]}
        )
        return payload

    def _current_state(
        self,
        record: dict[str, Any],
        metadata: dict[str, Any],
    ) -> str:
        kb_id = str(record.get("kb_id") or "")
        if kb_id not in metadata["knowledge_bases"]:
            return "stale"
        documents = self._documents_for_kb(metadata, kb_id)
        if self._corpus_hash(documents) != str(record.get("corpus_hash") or ""):
            return "stale"
        if metadata["pipeline_active_versions"].get(kb_id) != record.get(
            "target_version_id"
        ):
            return "stale"
        draft = self.rag_service._pipeline_draft_record(metadata, kb_id)
        stored_state = str(record.get("state") or "ready")
        if stored_state == "applied":
            return (
                "applied"
                if int(draft["version"])
                == int(record.get("applied_draft_version") or 0)
                else "stale"
            )
        if int(draft["version"]) != int(record.get("draft_version") or 0):
            return "stale"
        return stored_state

    def _documents_for_kb(
        self,
        metadata: dict[str, Any],
        kb_id: str,
    ) -> list[dict[str, Any]]:
        return sorted(
            [
                item
                for item in metadata["documents"].values()
                if isinstance(item, dict)
                and str(item.get("kb_id")) == kb_id
                and not item.get("deletion_status")
            ],
            key=lambda item: str(item.get("id") or ""),
        )

    def _corpus_hash(self, documents: list[dict[str, Any]]) -> str:
        return _checksum(
            [
                {
                    "document_id": str(item.get("id") or ""),
                    "content_hash": str(item.get("content_hash") or ""),
                    "size": int(item.get("size") or 0),
                }
                for item in documents
            ]
        )

    def _normalize_requirements(
        self,
        requirements: dict[str, Any] | None,
    ) -> dict[str, bool]:
        raw = dict(requirements or {})
        unknown = set(raw) - set(REQUIREMENT_KEYS)
        if unknown:
            raise RagStrategyValidationError(
                f"Unknown strategy requirements: {', '.join(sorted(unknown))}"
            )
        normalized: dict[str, bool] = {}
        for key in REQUIREMENT_KEYS:
            value = raw.get(key, False)
            if not isinstance(value, bool):
                raise RagStrategyValidationError(f"{key} must be a boolean.")
            normalized[key] = value
        return normalized

    def _payload(self, record: dict[str, Any], *, state: str) -> dict[str, Any]:
        payload = json.loads(json.dumps(record))
        payload["state"] = state
        return payload


def _recursive_config(size: int, overlap: int) -> dict[str, Any]:
    return {
        "strategy": "recursive_character",
        "chunk_size": size,
        "chunk_overlap": overlap,
        "separators": ["\n\n", "\n", "。", "！", "？", ". ", " ", ""],
        "parent_chunk_size": 1500,
        "parent_chunk_overlap": 100,
        "child_chunk_size": 400,
        "child_chunk_overlap": 50,
        "parent_separators": ["\n\n", "\n", "。", "！", "？", ". ", " ", ""],
        "child_separators": ["\n\n", "\n", "。", "！", "？", ". ", " ", ""],
    }


def _parent_child_config(parent_size: int, child_size: int) -> dict[str, Any]:
    return {
        "strategy": "parent_child",
        "chunk_size": 700,
        "chunk_overlap": 70,
        "separators": ["\n\n", "\n", "。", "！", "？", ". ", " ", ""],
        "parent_chunk_size": parent_size,
        "parent_chunk_overlap": max(1, round(parent_size * 0.1)),
        "child_chunk_size": child_size,
        "child_chunk_overlap": max(1, round(child_size * 0.1)),
        "parent_separators": ["\n\n", "\n", "。", "！", "？", ". ", " ", ""],
        "child_separators": ["\n\n", "\n", "。", "！", "？", ". ", " ", ""],
    }


def _profile_diff(
    current: dict[str, Any],
    chunker: dict[str, Any],
    retrieval: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for prefix, before, after in (
        ("chunker", dict(current.get("chunker") or {}), chunker),
        ("retrieval", dict(current.get("retrieval") or {}), retrieval),
    ):
        for key in sorted(after):
            if before.get(key) == after.get(key):
                continue
            rows.append(
                {
                    "field": f"{prefix}.{key}",
                    "current": before.get(key),
                    "recommended": after.get(key),
                }
            )
    return rows


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1))
    return int(ordered[index])


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _checksum(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def _lower_confidence(left: str, right: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return left if order.get(left, 0) <= order.get(right, 0) else right


_RULE_SUMMARIES = (
    (1, "heuristic", "Preserve current chunking when most structured blocks are short."),
    (2, "heuristic", "Treat parent-child as a long-context candidate, not a universal default."),
    (3, "evidence-backed", "Prefer full-text for exact identifiers and terminology."),
    (4, "evidence-backed", "Do not infer semantic quality from hash embeddings."),
    (5, "heuristic", "Use Hybrid weights only as an evaluation starting point."),
    (6, "evidence-backed", "Use modest overlap only when blocks actually split."),
    (7, "evidence-backed", "Preserve table and code structure before retrieval routing."),
    (8, "literature-only", "Rerank confusable content only as an evaluated candidate."),
    (9, "heuristic", "Use Top-K 5 for bounded single-fact or low-latency tasks."),
    (10, "heuristic", "Offer Top-K 10 for broader evidence with a context-cost warning."),
    (11, "evidence-backed", "Keep threshold zero until benchmark calibration."),
    (12, "evidence-backed", "Keep character and token units explicit."),
    (13, "deferred", "Do not emulate unsupported advanced chunking strategies."),
)

_RULE_EVIDENCE = {
    "R1": {
        "rule_id": "R1",
        "classification": "heuristic",
        "source": "EXP-SHORT-01",
    },
    "R2": {
        "rule_id": "R2",
        "classification": "heuristic",
        "source": "EXP-LONG-01",
    },
    "R3": {
        "rule_id": "R3",
        "classification": "evidence-backed",
        "source": "EXP-LEXICAL-01",
    },
    "R4": {
        "rule_id": "R4",
        "classification": "evidence-backed",
        "source": "EXP-HASH-01",
    },
    "R5": {
        "rule_id": "R5",
        "classification": "heuristic",
        "source": "ModelMirror existing retrieval default",
    },
    "R6": {
        "rule_id": "R6",
        "classification": "evidence-backed",
        "source": "EXP-OVERLAP-01",
    },
    "R7": {
        "rule_id": "R7",
        "classification": "evidence-backed",
        "source": "EXP-STRUCTURE-01",
    },
    "R8": {
        "rule_id": "R8",
        "classification": "literature-only",
        "source": "Contextual Retrieval literature",
    },
    "R9": {
        "rule_id": "R9",
        "classification": "heuristic",
        "source": "EXP-TOPK-01",
    },
    "R10": {
        "rule_id": "R10",
        "classification": "heuristic",
        "source": "target benchmark required",
    },
    "R11": {
        "rule_id": "R11",
        "classification": "evidence-backed",
        "source": "EXP-NOANSWER-01",
    },
    "R12": {
        "rule_id": "R12",
        "classification": "evidence-backed",
        "source": "current splitter contract",
    },
}
