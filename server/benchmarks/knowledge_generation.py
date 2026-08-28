from __future__ import annotations

import copy
import hashlib
import json
import random
import re
from collections import defaultdict
from typing import Any

from .service import BenchmarkGenerationError


KNOWLEDGE_COVERAGE = (
    "exact_lexical",
    "factual_lookup",
    "paraphrase",
    "section_context",
    "cross_language",
    "multi_evidence",
    "confusable_content",
)
MAX_EVIDENCE_UNITS = 40
MAX_EVIDENCE_CHARS = 48_000
MAX_EVIDENCE_UNIT_CHARS = 1_200
KNOWLEDGE_PROMPT_CONTRACT_VERSION = "rag-gold-generation-prompt-v3"


class KnowledgeBenchmarkGenerationService:
    """Build targeted retrieval cases from one immutable knowledge version."""

    def __init__(self, *, rag_service: Any, evaluation_store: Any) -> None:
        self.rag_service = rag_service
        self.evaluation_store = evaluation_store

    def snapshot_target(
        self, reference: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]:
        kb_id = str(reference.get("kb_id") or "")
        version_id = str(reference.get("pipeline_version_id") or "")
        if not kb_id or not version_id:
            raise BenchmarkGenerationError(
                "Knowledge target requires kb_id and pipeline_version_id."
            )
        version = self.rag_service.get_pipeline_version(version_id)
        if str(version.get("kb_id") or "") != kb_id:
            raise BenchmarkGenerationError(
                "Knowledge pipeline version does not belong to the selected knowledge base."
            )
        if str(version.get("status") or "") not in {"ready", "active"}:
            raise BenchmarkGenerationError(
                "Knowledge benchmark generation requires a ready or active version."
            )
        if not version.get("vector_index_ready") or not version.get(
            "lexical_index_ready"
        ):
            raise BenchmarkGenerationError(
                "Knowledge version must have complete vector and full-text indexes."
            )

        selected_ids = {
            str(item)
            for item in list(reference.get("document_ids") or [])
            if str(item)
        }
        document_results = [
            dict(item)
            for item in list(version.get("document_results") or [])
            if isinstance(item, dict)
            and str(item.get("status") or "") == "completed"
        ]
        available_ids = {str(item.get("source_id") or "") for item in document_results}
        unknown_ids = sorted(selected_ids - available_ids)
        if unknown_ids:
            raise BenchmarkGenerationError(
                "Selected documents are not part of the fixed knowledge version: "
                + ", ".join(unknown_ids[:10])
            )
        if selected_ids:
            document_results = [
                item
                for item in document_results
                if str(item.get("source_id") or "") in selected_ids
            ]
        if not document_results:
            raise BenchmarkGenerationError(
                "Knowledge version exposes no completed documents in the selected scope."
            )

        corpus_evidence = self.rag_service.pipeline_corpus_evidence(version_id)
        corpus_checksum = str(corpus_evidence.get("corpus_snapshot_checksum") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", corpus_checksum):
            raise BenchmarkGenerationError(
                "Knowledge version canonical corpus evidence is unavailable."
            )
        canonical_documents = {
            str(item.get("document_id") or ""): dict(item)
            for item in list(corpus_evidence.get("documents") or [])
            if isinstance(item, dict) and str(item.get("document_id") or "")
        }
        evidence: list[dict[str, Any]] = []
        for document in sorted(
            document_results,
            key=lambda item: (
                str(item.get("filename") or "").casefold(),
                str(item.get("source_id") or ""),
            ),
        ):
            document_id = str(document.get("source_id") or "")
            canonical = canonical_documents.get(document_id)
            if not isinstance(canonical, dict):
                raise BenchmarkGenerationError(
                    "Knowledge version canonical document evidence is incomplete."
                )
            blocks = [
                dict(item)
                for item in list(canonical.get("source_blocks") or [])
                if isinstance(item, dict)
            ]
            blocks.sort(
                key=lambda item: (
                    int(item.get("block_index") or 0),
                    str(item.get("source_block_id") or ""),
                )
            )
            for block in blocks:
                source_block_id = str(block.get("source_block_id") or "")
                block_hash = str(block.get("block_hash") or "")
                # Preserve the canonical block byte-for-byte so anchor offsets remain
                # aligned with the immutable source-block coordinates.
                text = str(block.get("text") or "")
                if (
                    not source_block_id
                    or not re.fullmatch(r"[0-9a-f]{64}", block_hash)
                    or len(text.strip()) < 24
                ):
                    continue
                evidence_id = "evidence_" + hashlib.sha256(
                    f"{corpus_checksum}:{document_id}:{source_block_id}:{block_hash}".encode(
                        "utf-8"
                    )
                ).hexdigest()[:20]
                evidence.append(
                    {
                        "evidence_id": evidence_id,
                        "document_id": document_id,
                        "document_name": str(
                            block.get("document_name")
                            or canonical.get("document_name")
                            or document.get("filename")
                            or ""
                        )[:240],
                        "chunk_id": str(block.get("representative_chunk_id") or ""),
                        "source_block_id": source_block_id,
                        "block_hash": block_hash,
                        "block_index": int(block.get("block_index") or 0),
                        "start_char": int(block.get("start_char") or 0),
                        "end_char": int(block.get("end_char") or 0),
                        "chunk_type": str(block.get("block_type") or "canonical")[:80],
                        "page_number": block.get("page_number"),
                        "heading_path": [
                            str(item)[:160]
                            for item in list(block.get("heading_path") or [])[:12]
                        ],
                        "visual_kind": str(block.get("visual_kind") or "")[:80] or None,
                        "text": text,
                        "text_length": len(text),
                        "truncated": len(text) > MAX_EVIDENCE_UNIT_CHARS,
                    }
                )
        if not evidence:
            raise BenchmarkGenerationError(
                "Knowledge version has no stable source_block evidence. Rebuild a V2 index before generation."
            )

        safe_documents = [
            {
                "document_id": str(item.get("source_id") or ""),
                "document_name": str(item.get("filename") or "")[:240],
                "content_hash": str(item.get("content_hash") or "")[:64],
                "chunk_count": int(item.get("chunk_count") or 0),
                "block_count": int(item.get("block_count") or 0),
            }
            for item in document_results
        ]
        checksum_payload = {
            "kb_id": kb_id,
            "version_id": version_id,
            "version": int(version.get("version") or 0),
            "processor_profile": version.get("processor_profile") or {},
            "retrieval_profile": version.get("retrieval_profile") or {},
            "embedding_profile": version.get("embedding_profile") or {},
            "documents": safe_documents,
            "evidence": [
                {
                    key: item.get(key)
                    for key in (
                        "evidence_id",
                        "document_id",
                        "source_block_id",
                        "block_hash",
                        "block_index",
                        "text_length",
                    )
                }
                for item in evidence
            ],
        }
        checksum = self._checksum(checksum_payload)
        warnings: list[str] = []
        if len(evidence) < 6:
            warnings.append(
                "The selected scope exposes fewer than six stable evidence blocks."
            )
        snapshot = {
            "target_kind": "knowledge_version",
            "target_id": version_id,
            "label": str(reference.get("label") or f"Knowledge v{version.get('version')}")[:160],
            "kb_id": kb_id,
            "pipeline_version_id": version_id,
            "version": int(version.get("version") or 0),
            "status": str(version.get("status") or ""),
            "checksum": checksum,
            "documents": safe_documents,
            "document_count": len(safe_documents),
            "evidence_count": len(evidence),
            "retrieval_profile": copy.deepcopy(version.get("retrieval_profile") or {}),
            "processor_profile": copy.deepcopy(version.get("processor_profile") or {}),
            "embedding_profile": copy.deepcopy(version.get("embedding_profile") or {}),
            "source_summary_hash": self._checksum(safe_documents),
            "corpus_snapshot_checksum": corpus_checksum,
            "warnings": warnings,
            "source": {
                "kind": "knowledge_version",
                "kb_id": kb_id,
                "pipeline_version_id": version_id,
                "document_ids": sorted(selected_ids),
            },
            "_evidence": evidence,
        }
        return snapshot, warnings

    def preflight(
        self,
        *,
        target_reference: dict[str, Any],
        requested_coverage: list[str],
        locales: list[str] | None = None,
    ) -> dict[str, Any]:
        snapshot, warnings = self.snapshot_target(target_reference)
        available = self.available_coverage(
            snapshot, locales=list(locales or ["zh-CN", "en-US"])
        )
        selected = requested_coverage or list(available)
        unavailable = sorted(set(selected) - set(available))
        issues = (
            [
                {
                    "code": "knowledge_coverage_unavailable",
                    "message": "Requested coverage is unavailable: "
                    + ", ".join(unavailable),
                }
            ]
            if unavailable
            else []
        )
        sampled = self._sample_evidence(snapshot, seed=0)
        return {
            "valid": not issues,
            "target": self.public_target(snapshot),
            "coverage": {
                "available": available,
                "recommended": available,
                "selected": selected,
            },
            "sampling": {
                "document_count": snapshot["document_count"],
                "stable_evidence_count": snapshot["evidence_count"],
                "sampled_evidence_count": len(sampled),
                "estimated_context_chars": sum(len(item["text"]) for item in sampled),
                "max_context_chars": MAX_EVIDENCE_CHARS,
            },
            "warnings": [
                *warnings,
                "Generation sends only the displayed sampled evidence to the selected model provider.",
            ],
            "issues": issues,
        }

    def available_coverage(
        self, snapshot: dict[str, Any], *, locales: list[str]
    ) -> list[str]:
        evidence = list(snapshot.get("_evidence") or [])
        available = ["exact_lexical", "factual_lookup", "paraphrase"]
        if any(item.get("heading_path") or item.get("chunk_type") == "child" for item in evidence):
            available.append("section_context")
        if len(set(locales)) > 1:
            available.append("cross_language")
        if len({str(item.get("document_id") or "") for item in evidence}) > 1:
            available.extend(["multi_evidence", "confusable_content"])
        return available

    def prepare_generation(
        self,
        *,
        snapshot: dict[str, Any],
        case_count: int,
        locales: list[str],
        requested_coverage: list[str],
        no_result_count: int,
        seed: int,
    ) -> dict[str, Any]:
        available = self.available_coverage(snapshot, locales=locales)
        selected = requested_coverage or list(available)
        unavailable = sorted(set(selected) - set(available))
        if unavailable:
            raise BenchmarkGenerationError(
                "Requested knowledge coverage is unavailable: " + ", ".join(unavailable)
            )
        sampled = self._sample_evidence(snapshot, seed=seed)
        if not sampled:
            raise BenchmarkGenerationError("Knowledge snapshot exposes no sampleable evidence.")
        evidence_by_id = {str(item["evidence_id"]): item for item in sampled}
        positives = case_count - no_result_count
        blueprints: list[dict[str, Any]] = []
        rng = random.Random(seed)
        evidence_wheel = list(sampled)
        rng.shuffle(evidence_wheel)
        for index in range(positives):
            coverage = selected[index % len(selected)]
            primary = evidence_wheel[index % len(evidence_wheel)]
            required = [primary]
            if coverage == "multi_evidence" and len(evidence_wheel) > 1:
                secondary = next(
                    (
                        item
                        for item in evidence_wheel
                        if item["document_id"] != primary["document_id"]
                    ),
                    evidence_wheel[(index + 1) % len(evidence_wheel)],
                )
                if secondary["evidence_id"] != primary["evidence_id"]:
                    required.append(secondary)
            blueprint = {
                "blueprint_id": f"knowledge-case-{index + 1:02d}",
                "query_type": coverage,
                "locale": locales[index % len(locales)],
                "difficulty": self._difficulty(index, positives),
                "required_evidence_ids": [item["evidence_id"] for item in required],
                "required_query_marker_groups": (
                    [self._query_markers(item) for item in required]
                    if coverage == "exact_lexical"
                    else []
                ),
                "expected_no_result": False,
            }
            if coverage == "confusable_content" and len(evidence_wheel) > 1:
                decoy = evidence_wheel[(index + 1) % len(evidence_wheel)]
                if decoy["evidence_id"] != primary["evidence_id"]:
                    blueprint["context_evidence_ids"] = [decoy["evidence_id"]]
            blueprints.append(blueprint)
        for index in range(no_result_count):
            context = evidence_wheel[(positives + index) % len(evidence_wheel)]
            blueprints.append(
                {
                    "blueprint_id": f"knowledge-case-{positives + index + 1:02d}",
                    "query_type": "no_result",
                    "locale": locales[(positives + index) % len(locales)],
                    "difficulty": "adversarial",
                    "required_evidence_ids": [],
                    "context_evidence_ids": [context["evidence_id"]],
                    "expected_no_result": True,
                }
            )
        return {
            "selected_coverage": selected,
            "available_coverage": available,
            "evidence": sampled,
            "evidence_by_id": evidence_by_id,
            "blueprints": blueprints,
            "evidence_hash": self._checksum(
                [
                    {
                        "evidence_id": item["evidence_id"],
                        "chunk_id": item["chunk_id"],
                        "source_block_id": item["source_block_id"],
                        "block_hash": item["block_hash"],
                    }
                    for item in sampled
                ]
            ),
            "blueprint_hash": self._checksum(blueprints),
            "prompt_contract_hash": self._checksum(
                {
                    "contract_version": KNOWLEDGE_PROMPT_CONTRACT_VERSION,
                    "coverage": list(KNOWLEDGE_COVERAGE),
                    "long_copy_block_chars": 32,
                    "semantic_warning_chars": 12,
                    "other_warning_chars": 24,
                }
            ),
        }

    def generation_prompt(
        self,
        *,
        snapshot: dict[str, Any],
        context: dict[str, Any],
        case_count: int,
        locales: list[str],
        seed: int,
    ) -> tuple[str, str]:
        public_evidence = [
            {
                key: item.get(key)
                for key in (
                    "evidence_id",
                    "document_name",
                    "chunk_type",
                    "page_number",
                    "heading_path",
                    "visual_kind",
                    "text",
                    "truncated",
                )
            }
            for item in context["evidence"]
        ]
        contract = {
            "dataset": {
                "name": "short target-specific name",
                "description": "what retrieval behavior this set validates",
                "cases": [
                    {
                        "blueprint_id": "copy the server blueprint ID",
                        "query": "target-specific retrieval query",
                        "evidence_ids": ["copy required evidence IDs exactly"],
                        "anchor_quotes": [
                            {
                                "evidence_id": "required evidence ID",
                                "quote": "8-240 exact characters copied from that evidence",
                            }
                        ],
                        "rationale": "short public reason",
                    }
                ],
                "assumptions": ["short public assumption"],
            }
        }
        system = (
            "You create deterministic retrieval benchmark questions for one fixed "
            "knowledge index. Return JSON only. Every positive question must be answerable "
            "from exactly the server-assigned evidence and must be specific to that "
            "document content. Copy a short exact anchor quote for every required evidence "
            "ID so the server can verify grounding. Do not invent IDs, facts, references, "
            "paths, credentials, or hidden reasoning. Questions must not reveal the answer "
            "or copy a long anchor verbatim. Paraphrase and cross-language cases must remain "
            "semantically grounded. no_result blueprints must ask a plausible nearby question "
            "whose answer is absent from all provided evidence; return no evidence IDs or quotes."
        )
        user = (
            f"Create exactly {case_count} cases in blueprint order. Seed={seed}. "
            f"Locales={locales}. Keep each query between 3 and 4000 characters. "
            "For positive cases evidence_ids must exactly equal required_evidence_ids. "
            "When required_query_marker_groups is non-empty, the query must contain at "
            "least one exact token from every corresponding entry. Paraphrase and "
            "cross-language cases intentionally omit marker requirements and must use "
            "semantic grounding plus the exact anchor quote contract instead. "
            "For no-result cases evidence_ids and anchor_quotes must be empty.\n\n"
            f"Fixed target summary:\n{json.dumps(self.public_target(snapshot), ensure_ascii=False)}\n\n"
            f"Server blueprints:\n{json.dumps(context['blueprints'], ensure_ascii=False)}\n\n"
            f"Sampled evidence:\n{json.dumps(public_evidence, ensure_ascii=False)}\n\n"
            f"JSON contract:\n{json.dumps(contract, ensure_ascii=False)}"
        )
        return system, user

    def repair_prompt(
        self,
        raw: str,
        error: str,
        *,
        snapshot: dict[str, Any],
        context: dict[str, Any],
        case_count: int,
        locales: list[str],
        seed: int,
    ) -> tuple[str, str]:
        system, user = self.generation_prompt(
            snapshot=snapshot,
            context=context,
            case_count=case_count,
            locales=locales,
            seed=seed,
        )
        return (
            system + " Repair the invalid response once and return the complete JSON object.",
            user
            + "\n\nValidation error:\n"
            + str(error)[:1_000]
            + "\n\nInvalid response:\n"
            + str(raw)[:12_000],
        )

    def parse_generated_cases(
        self,
        raw: str,
        *,
        snapshot: dict[str, Any],
        context: dict[str, Any],
        expected_count: int,
    ) -> dict[str, Any]:
        payload = self._extract_json(raw)
        dataset = payload.get("dataset")
        if not isinstance(dataset, dict):
            raise BenchmarkGenerationError("Generator output is missing dataset.")
        raw_cases = dataset.get("cases")
        if not isinstance(raw_cases, list) or len(raw_cases) != expected_count:
            raise BenchmarkGenerationError(
                f"Generator must return exactly {expected_count} cases."
            )
        blueprints = list(context["blueprints"])
        evidence_by_id = dict(context["evidence_by_id"])
        seen_queries: set[str] = set()
        cases: list[dict[str, Any]] = []
        targeting: list[dict[str, Any]] = []
        for index, raw_case in enumerate(raw_cases):
            if not isinstance(raw_case, dict):
                raise BenchmarkGenerationError(f"Case {index + 1} must be an object.")
            blueprint = blueprints[index]
            if str(raw_case.get("blueprint_id") or "") != blueprint["blueprint_id"]:
                raise BenchmarkGenerationError(
                    f"Case {index + 1} must use blueprint {blueprint['blueprint_id']}."
                )
            query = str(raw_case.get("query") or "").strip()
            if len(query) < 3 or len(query) > 4_000:
                raise BenchmarkGenerationError(
                    f"Case {index + 1} query must contain 3-4000 characters."
                )
            query_key = self._normalize_text(query)
            if query_key in seen_queries:
                raise BenchmarkGenerationError("Generated queries must be unique.")
            seen_queries.add(query_key)
            expected_no_result = bool(blueprint["expected_no_result"])
            evidence_ids = [
                str(item) for item in list(raw_case.get("evidence_ids") or []) if str(item)
            ]
            required_ids = list(blueprint.get("required_evidence_ids") or [])
            if evidence_ids != required_ids:
                raise BenchmarkGenerationError(
                    f"Case {index + 1} evidence IDs do not match the server blueprint."
                )
            if not expected_no_result:
                marker_groups = list(blueprint.get("required_query_marker_groups") or [])
                for marker_group in marker_groups:
                    markers = [str(item) for item in list(marker_group or []) if str(item)]
                    if not markers or not any(
                        self._normalize_text(marker) in query_key for marker in markers
                    ):
                        raise BenchmarkGenerationError(
                            f"Case {index + 1} query is not specific to every assigned evidence block."
                        )
                warning_threshold = (
                    12
                    if str(blueprint.get("query_type") or "")
                    in {"paraphrase", "cross_language"}
                    else 24
                )
                leakage_warning = False
                for evidence_id in required_ids:
                    evidence = evidence_by_id.get(evidence_id)
                    evidence_key = self._normalize_text(
                        str((evidence or {}).get("text") or "")
                    )
                    if self._has_common_window(query_key, evidence_key, 32):
                        raise BenchmarkGenerationError(
                            f"Case {index + 1} copies too much source text into the query."
                        )
                    leakage_warning = leakage_warning or self._has_common_window(
                        query_key, evidence_key, warning_threshold
                    )
            else:
                leakage_warning = False
            quotes = list(raw_case.get("anchor_quotes") or [])
            if expected_no_result:
                if evidence_ids or quotes:
                    raise BenchmarkGenerationError(
                        "No-result cases cannot contain evidence IDs or quotes."
                    )
                references: list[dict[str, Any]] = []
                review_status = "pending"
            else:
                quote_by_id: dict[str, str] = {}
                for quote_item in quotes:
                    if not isinstance(quote_item, dict):
                        continue
                    evidence_id = str(quote_item.get("evidence_id") or "")
                    quote = str(quote_item.get("quote") or "").strip()
                    if evidence_id in quote_by_id:
                        raise BenchmarkGenerationError("Anchor evidence may only be quoted once.")
                    quote_by_id[evidence_id] = quote
                if set(quote_by_id) != set(required_ids):
                    raise BenchmarkGenerationError(
                        f"Case {index + 1} must quote every required evidence ID exactly once."
                    )
                references = []
                for ref_index, evidence_id in enumerate(required_ids):
                    evidence = evidence_by_id.get(evidence_id)
                    if not isinstance(evidence, dict):
                        raise BenchmarkGenerationError("Generated evidence ID is unavailable.")
                    quote = quote_by_id[evidence_id]
                    evidence_text = str(evidence["text"])
                    normalized_quote = self._normalize_text(quote)
                    local_anchor_start = evidence_text.find(quote)
                    if (
                        len(quote) < 8
                        or len(quote) > 240
                        or local_anchor_start < 0
                    ):
                        raise BenchmarkGenerationError(
                            f"Case {index + 1} anchor quote is not present in its fixed evidence."
                        )
                    if len(normalized_quote) >= 32 and normalized_quote in query_key:
                        raise BenchmarkGenerationError(
                            f"Case {index + 1} leaks a long evidence quote into the query."
                        )
                    anchor_start = int(evidence.get("start_char") or 0) + local_anchor_start
                    anchor_end = anchor_start + len(quote)
                    anchor_hash = self._checksum(
                        {
                            "contract_version": "rag-anchor-v1",
                            "document_id": evidence["document_id"],
                            "source_block_id": evidence["source_block_id"],
                            "block_hash": evidence["block_hash"],
                            "anchor_start": anchor_start,
                            "anchor_end": anchor_end,
                        }
                    )
                    references.append(
                        {
                            "reference_id": f"ref_{blueprint['blueprint_id']}_{ref_index + 1}",
                            "document_id": evidence["document_id"],
                            "chunk_id": evidence["chunk_id"],
                            "source_block_id": evidence["source_block_id"],
                            "source_block_hash": evidence["block_hash"],
                            "anchor_start": anchor_start,
                            "anchor_end": anchor_end,
                            "anchor_hash": anchor_hash,
                            "page_number": evidence.get("page_number"),
                            "match_mode": "source_block",
                            "relevance": 3 if ref_index == 0 else 2,
                        }
                    )
                review_status = "pending"
            rationale = str(raw_case.get("rationale") or "").strip()[:500]
            context_refs = [
                {
                    "document_id": evidence_by_id[evidence_id]["document_id"],
                    "chunk_id": evidence_by_id[evidence_id]["chunk_id"],
                    "source_block_id": evidence_by_id[evidence_id]["source_block_id"],
                    "source_block_hash": evidence_by_id[evidence_id]["block_hash"],
                    "page_number": evidence_by_id[evidence_id].get("page_number"),
                }
                for evidence_id in blueprint.get("context_evidence_ids") or []
                if evidence_id in evidence_by_id
            ]
            tags = [
                "generated",
                str(blueprint["query_type"]),
                str(blueprint["locale"]),
                str(blueprint["difficulty"]),
            ]
            if expected_no_result:
                tags.extend(["corpus_near", "hard_negative"])
            cases.append(
                {
                    "query": query,
                    "expected_refs": references,
                    "expected_no_result": expected_no_result,
                    "review_status": review_status,
                    "tags": tags,
                    "notes": rationale,
                    "targeting": {
                        "blueprint_id": blueprint["blueprint_id"],
                        "query_type": blueprint["query_type"],
                        "locale": blueprint["locale"],
                        "difficulty": blueprint["difficulty"],
                        "evidence_ids": required_ids,
                        "context_refs": context_refs,
                        "full_corpus_verification": (
                            self._verify_no_result_query(snapshot, query)
                            if expected_no_result
                            else None
                        ),
                        "leakage_warning": (
                            {
                                "threshold": warning_threshold,
                                "reason_required": True,
                            }
                            if leakage_warning
                            else None
                        ),
                    },
                }
            )
            targeting.append(
                {
                    "blueprint_id": blueprint["blueprint_id"],
                    "query_type": blueprint["query_type"],
                    "difficulty": blueprint["difficulty"],
                    "reference_count": len(references),
                }
            )
        return {
            "name": str(dataset.get("name") or "Targeted knowledge benchmark").strip()[:160],
            "description": str(dataset.get("description") or "").strip()[:1_000],
            "cases": cases,
            "assumptions": [
                str(item)[:500]
                for item in list(dataset.get("assumptions") or [])
                if str(item).strip()
            ][:20],
            "targeting": targeting,
        }

    def public_target(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(snapshot.get(key))
            for key in (
                "target_kind",
                "target_id",
                "label",
                "kb_id",
                "pipeline_version_id",
                "version",
                "status",
                "checksum",
                "documents",
                "document_count",
                "evidence_count",
                "retrieval_profile",
                "processor_profile",
                "embedding_profile",
                "source_summary_hash",
                "corpus_snapshot_checksum",
                "warnings",
                "source",
            )
        }

    def _sample_evidence(
        self, snapshot: dict[str, Any], *, seed: int
    ) -> list[dict[str, Any]]:
        evidence = [dict(item) for item in list(snapshot.get("_evidence") or [])]
        by_document: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in evidence:
            by_document[str(item.get("document_id") or "")].append(item)
        rng = random.Random(seed)
        document_ids = sorted(by_document)
        rng.shuffle(document_ids)
        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        strata = (("head", 0.0), ("middle", 0.5), ("tail", 1.0))
        for document_id in document_ids:
            values = by_document[document_id]
            values.sort(
                key=lambda item: (
                    int(item.get("block_index") or 0),
                    str(item.get("source_block_id") or ""),
                )
            )
            for label, position in strata:
                index = round((len(values) - 1) * position)
                item = values[index]
                evidence_id = str(item.get("evidence_id") or "")
                if evidence_id in selected_ids:
                    continue
                selected.append({**item, "sample_stratum": label})
                selected_ids.add(evidence_id)
                if len(selected) >= MAX_EVIDENCE_UNITS:
                    break
            if len(selected) >= MAX_EVIDENCE_UNITS:
                break
        remaining: list[dict[str, Any]] = []
        for document_id in document_ids:
            for item in by_document[document_id]:
                if str(item.get("evidence_id") or "") not in selected_ids:
                    remaining.append(item)
        rng.shuffle(remaining)
        for item in remaining:
            selected.append({**item, "sample_stratum": "body"})
            if len(selected) >= MAX_EVIDENCE_UNITS:
                break
        bounded: list[dict[str, Any]] = []
        chars = 0
        for item in selected:
            text = str(item.get("text") or "")[:MAX_EVIDENCE_UNIT_CHARS]
            if chars + len(text) > MAX_EVIDENCE_CHARS:
                break
            bounded.append({**item, "text": text})
            chars += len(text)
        return bounded

    def _verify_no_result_query(
        self, snapshot: dict[str, Any], query: str
    ) -> dict[str, Any]:
        query_terms = self._verification_terms(query)
        matches: list[dict[str, Any]] = []
        evidence = [dict(item) for item in list(snapshot.get("_evidence") or [])]
        for item in evidence:
            text_terms = self._verification_terms(str(item.get("text") or ""))
            overlap = len(query_terms & text_terms)
            coverage = overlap / max(1, len(query_terms))
            matches.append(
                {
                    "document_id": str(item.get("document_id") or ""),
                    "source_block_id": str(item.get("source_block_id") or ""),
                    "source_block_hash": str(item.get("block_hash") or ""),
                    "lexical_query_coverage": round(coverage, 6),
                }
            )
        matches.sort(
            key=lambda item: (
                -float(item["lexical_query_coverage"]),
                item["document_id"],
                item["source_block_id"],
            )
        )
        return {
            "contract_version": "rag-no-result-verification-v1",
            "completed": True,
            "method": "full_corpus_lexical_scan_v1",
            "query_hash": self._checksum(self._normalize_text(query)),
            "corpus_snapshot_checksum": str(
                snapshot.get("corpus_snapshot_checksum") or ""
            ),
            "scanned_document_count": len(
                {str(item.get("document_id") or "") for item in evidence}
            ),
            "scanned_source_block_count": len(evidence),
            "top_matches": matches[:5],
        }

    @staticmethod
    def _difficulty(index: int, total: int) -> str:
        if total <= 2 or index < max(1, total // 5):
            return "basic"
        if index >= total - max(1, total // 4):
            return "adversarial"
        return "intermediate"

    @classmethod
    def _query_markers(cls, evidence: dict[str, Any]) -> list[str]:
        text = str(evidence.get("text") or "")
        candidates: list[str] = []
        candidates.extend(
            re.findall(r"\b(?:[A-Z][A-Za-z0-9_-]{2,}|\d+(?:[.,]\d+)*)\b", text)
        )
        candidates.extend(re.findall(r"[\u3400-\u9fff]{3,8}", text))
        filename = str(evidence.get("document_name") or "").rsplit(".", 1)[0]
        candidates.extend(re.findall(r"[A-Za-z0-9_-]{3,}|[\u3400-\u9fff]{2,8}", filename))
        markers: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            marker = str(candidate).strip()
            key = cls._normalize_text(marker)
            if len(key) < 2 or key in seen:
                continue
            seen.add(key)
            markers.append(marker[:40])
            if len(markers) >= 6:
                break
        if not markers:
            normalized = re.sub(r"\s+", " ", text).strip()
            marker = normalized[:8].strip()
            if len(cls._normalize_text(marker)) >= 2:
                markers.append(marker)
        return markers

    @staticmethod
    def _checksum(value: Any) -> str:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _normalize_text(value: str) -> str:
        return re.sub(r"[^\w\u3400-\u9fff]+", "", str(value).casefold())

    @staticmethod
    def _verification_terms(value: str) -> set[str]:
        text = str(value or "").casefold()
        terms = set(re.findall(r"[a-z0-9_]{2,}", text))
        for run in re.findall(r"[\u3400-\u9fff]+", text):
            if len(run) == 1:
                terms.add(run)
                continue
            for size in (2, 3):
                if len(run) < size:
                    continue
                terms.update(
                    run[index : index + size]
                    for index in range(len(run) - size + 1)
                )
        return terms

    @staticmethod
    def _has_common_window(left: str, right: str, size: int) -> bool:
        if size <= 0 or len(left) < size or len(right) < size:
            return False
        shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
        return any(
            shorter[index : index + size] in longer
            for index in range(len(shorter) - size + 1)
        )

    @staticmethod
    def _extract_json(raw: str) -> dict[str, Any]:
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise BenchmarkGenerationError("Generator did not return a JSON object.")
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise BenchmarkGenerationError(
                f"Generator returned invalid JSON: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise BenchmarkGenerationError("Generator output must be a JSON object.")
        return value
