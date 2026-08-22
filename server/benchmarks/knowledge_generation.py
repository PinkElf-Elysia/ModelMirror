from __future__ import annotations

import copy
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from typing import Any

from .service import BenchmarkGenerationError


KNOWLEDGE_COVERAGE = (
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
FORMAL_GOLD_CASE_COUNT = 42
FORMAL_GOLD_POSITIVE_COUNT = 30
FORMAL_GOLD_NEGATIVE_COUNT = 12
FORMAL_GOLD_MIN_EVIDENCE = 18
FORMAL_GOLD_MAX_DOCUMENT_CASES = 12
GOLD_V2_EVIDENCE_POLICY = "content-source-block-v1"


class KnowledgeBenchmarkGenerationService:
    """Build targeted retrieval cases from one immutable knowledge version."""

    evidence_policy_version = GOLD_V2_EVIDENCE_POLICY

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

        evidence: list[dict[str, Any]] = []
        seen_blocks: set[tuple[str, str]] = set()
        for document in sorted(
            document_results,
            key=lambda item: (
                str(item.get("filename") or "").casefold(),
                str(item.get("source_id") or ""),
            ),
        ):
            document_id = str(document.get("source_id") or "")
            chunks = self.rag_service.vector_store.list_document_chunks(
                f"{version_id}_{document_id}"
            )
            ordered = sorted(
                chunks,
                key=lambda item: (
                    0
                    if str(item.chunk_type) in {"parent", "standard"}
                    else 1,
                    int(item.chunk_index),
                    str(item.chunk_id),
                ),
            )
            for chunk in ordered:
                chunk_type = str(chunk.chunk_type or "").strip().casefold()
                if chunk_type == "heading":
                    continue
                source_block_id = str(chunk.source_block_id or "")
                text = str(chunk.text or "").strip()
                if not source_block_id or len(text) < 24:
                    continue
                block_key = (document_id, source_block_id)
                if block_key in seen_blocks:
                    continue
                seen_blocks.add(block_key)
                evidence_id = "evidence_" + hashlib.sha256(
                    f"{version_id}:{chunk.chunk_id}".encode("utf-8")
                ).hexdigest()[:20]
                evidence.append(
                    {
                        "evidence_id": evidence_id,
                        "document_id": document_id,
                        "document_name": str(chunk.document_name or document.get("filename") or "")[:240],
                        "chunk_id": str(chunk.chunk_id),
                        "source_block_id": source_block_id,
                        "chunk_type": chunk_type or "standard",
                        "page_number": chunk.page_number,
                        "heading_path": [str(item)[:160] for item in chunk.heading_path[:12]],
                        "visual_kind": str(chunk.visual_kind or "")[:80] or None,
                        "text": text[:MAX_EVIDENCE_UNIT_CHARS],
                        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
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
        corpus_snapshot = {
            "knowledge_base_id": kb_id,
            "documents": sorted(
                [
                    {
                        "document_id": item["document_id"],
                        "content_hash": item["content_hash"],
                    }
                    for item in safe_documents
                ],
                key=lambda item: item["document_id"],
            ),
            "source_blocks": sorted(
                [
                    {
                        "document_id": item["document_id"],
                        "source_block_id": item["source_block_id"],
                        "content_hash": item["content_hash"],
                    }
                    for item in evidence
                ],
                key=lambda item: (item["document_id"], item["source_block_id"]),
            ),
        }
        corpus_snapshot_hash = self._checksum(corpus_snapshot)
        corpus_snapshot_builder = getattr(
            self.rag_service, "pipeline_corpus_snapshot", None
        )
        if callable(corpus_snapshot_builder):
            corpus_identity = corpus_snapshot_builder(
                version_id,
                document_ids=[item["document_id"] for item in safe_documents],
            )
            corpus_snapshot = copy.deepcopy(corpus_identity["corpus_snapshot"])
            corpus_snapshot_hash = str(corpus_identity["corpus_snapshot_hash"])
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
                        "chunk_id",
                        "source_block_id",
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
            "corpus_snapshot": corpus_snapshot,
            "corpus_snapshot_hash": corpus_snapshot_hash,
            "documents": safe_documents,
            "document_count": len(safe_documents),
            "evidence_count": len(evidence),
            "retrieval_profile": copy.deepcopy(version.get("retrieval_profile") or {}),
            "processor_profile": copy.deepcopy(version.get("processor_profile") or {}),
            "embedding_profile": copy.deepcopy(version.get("embedding_profile") or {}),
            "source_summary_hash": self._checksum(safe_documents),
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
        generation_purpose: str = "general",
        case_count: int = 12,
        no_result_count: int = 0,
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
        if generation_purpose == "strategy_tuning":
            issues.extend(
                self._formal_generation_issues(
                    snapshot=snapshot,
                    available=available,
                    selected=selected,
                    locales=list(locales or ["zh-CN", "en-US"]),
                    case_count=case_count,
                    no_result_count=no_result_count,
                )
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
        available = ["factual_lookup", "paraphrase"]
        if any(item.get("heading_path") or item.get("chunk_type") == "child" for item in evidence):
            available.append("section_context")
        if len(set(locales)) > 1:
            available.append("cross_language")
        if len({str(item.get("document_id") or "") for item in evidence}) > 1:
            available.extend(["multi_evidence", "confusable_content"])
        return available

    @staticmethod
    def _formal_generation_issues(
        *,
        snapshot: dict[str, Any],
        available: list[str],
        selected: list[str],
        locales: list[str],
        case_count: int,
        no_result_count: int,
    ) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []

        def add(code: str, message: str) -> None:
            issues.append({"code": code, "message": message})

        if case_count != FORMAL_GOLD_CASE_COUNT or no_result_count != FORMAL_GOLD_NEGATIVE_COUNT:
            add(
                "formal_case_matrix",
                "rag-gold-v2 requires exactly 30 positive and 12 hard-negative cases.",
            )
        if set(locales) != {"zh-CN", "en-US"} or len(locales) != 2:
            add(
                "formal_locale_matrix",
                "rag-gold-v2 requires both zh-CN and en-US locales.",
            )
        if set(selected) != set(KNOWLEDGE_COVERAGE) or len(selected) != len(
            KNOWLEDGE_COVERAGE
        ):
            add(
                "formal_coverage_matrix",
                "rag-gold-v2 requires all six positive coverage types exactly once.",
            )
        if set(KNOWLEDGE_COVERAGE) - set(available):
            add(
                "formal_coverage_unavailable",
                "The selected corpus cannot support every rag-gold-v2 coverage type.",
            )
        document_count = int(snapshot.get("document_count") or 0)
        if document_count < 3 or document_count > 35:
            add(
                "formal_document_count",
                "rag-gold-v2 requires 3-35 selected documents so every document can be covered without exceeding the 40% share limit.",
            )
        evidence = list(snapshot.get("_evidence") or [])
        if len(evidence) < FORMAL_GOLD_MIN_EVIDENCE:
            add(
                "formal_evidence_count",
                "rag-gold-v2 requires at least 18 stable source blocks for the two-use cap.",
            )
        blocks_by_document = Counter(
            str(item.get("document_id") or "")
            for item in evidence
            if item.get("document_id")
        )
        usable_reference_capacity = sum(
            min(count * 2, FORMAL_GOLD_MAX_DOCUMENT_CASES)
            for count in blocks_by_document.values()
        )
        if usable_reference_capacity < FORMAL_GOLD_POSITIVE_COUNT + 5:
            add(
                "formal_evidence_capacity",
                "Stable source blocks are too concentrated by document to allocate 30 positives and five cross-document evidence pairs within publication limits.",
            )
        return issues

    def prepare_generation(
        self,
        *,
        snapshot: dict[str, Any],
        case_count: int,
        locales: list[str],
        requested_coverage: list[str],
        no_result_count: int,
        seed: int,
        generation_purpose: str = "general",
    ) -> dict[str, Any]:
        available = self.available_coverage(snapshot, locales=locales)
        selected = requested_coverage or list(available)
        unavailable = sorted(set(selected) - set(available))
        if unavailable:
            raise BenchmarkGenerationError(
                "Requested knowledge coverage is unavailable: " + ", ".join(unavailable)
            )
        if generation_purpose == "strategy_tuning":
            formal_issues = self._formal_generation_issues(
                snapshot=snapshot,
                available=available,
                selected=selected,
                locales=locales,
                case_count=case_count,
                no_result_count=no_result_count,
            )
            if formal_issues:
                raise BenchmarkGenerationError(
                    "Formal rag-gold-v2 generation is not qualified: "
                    + "; ".join(item["message"] for item in formal_issues)
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
        formal_allocation = generation_purpose == "strategy_tuning"
        evidence_position = {
            str(item["evidence_id"]): position
            for position, item in enumerate(evidence_wheel)
        }
        block_use: Counter[str] = Counter()
        document_case_use: Counter[str] = Counter()
        blocks_by_document: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in evidence_wheel:
            blocks_by_document[str(item["document_id"])].append(item)

        def remaining_blocks(document_id: str) -> list[dict[str, Any]]:
            limit = 2 if formal_allocation else positives
            return [
                item
                for item in blocks_by_document[document_id]
                if block_use[str(item["evidence_id"])] < limit
            ]

        def choose_block(document_id: str) -> dict[str, Any]:
            candidates = remaining_blocks(document_id)
            if not candidates:
                raise BenchmarkGenerationError(
                    "Formal rag-gold-v2 evidence allocation exhausted a source document."
                )
            chosen = min(
                candidates,
                key=lambda item: (
                    block_use[str(item["evidence_id"])],
                    evidence_position[str(item["evidence_id"])],
                ),
            )
            block_use[str(chosen["evidence_id"])] += 1
            return chosen

        def candidate_documents(*, excluding: str | None = None) -> list[str]:
            limit = FORMAL_GOLD_MAX_DOCUMENT_CASES if formal_allocation else positives
            return [
                document_id
                for document_id in blocks_by_document
                if document_id != excluding
                and document_case_use[document_id] < limit
                and remaining_blocks(document_id)
            ]

        for index in range(positives):
            coverage = selected[index % len(selected)]
            documents = candidate_documents()
            if not documents:
                raise BenchmarkGenerationError(
                    "Formal rag-gold-v2 evidence allocation cannot satisfy document share limits."
                )
            primary_document = min(
                documents,
                key=lambda document_id: (
                    document_case_use[document_id],
                    -len(remaining_blocks(document_id)),
                    document_id,
                ),
            )
            primary = choose_block(primary_document)
            required = [primary]
            document_case_use[primary_document] += 1
            if coverage == "multi_evidence" and len(evidence_wheel) > 1:
                secondary_documents = candidate_documents(excluding=primary_document)
                if not secondary_documents:
                    raise BenchmarkGenerationError(
                        "Formal rag-gold-v2 multi-evidence allocation requires capacity in a second document."
                    )
                secondary_document = min(
                    secondary_documents,
                    key=lambda document_id: (
                        document_case_use[document_id],
                        -len(remaining_blocks(document_id)),
                        document_id,
                    ),
                )
                required.append(choose_block(secondary_document))
                document_case_use[secondary_document] += 1
            blueprint = {
                "blueprint_id": f"knowledge-case-{index + 1:02d}",
                "query_type": coverage,
                "locale": locales[index % len(locales)],
                "difficulty": self._difficulty(index, positives),
                "required_evidence_ids": [item["evidence_id"] for item in required],
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
                    }
                    for item in sampled
                ]
            ),
            "blueprint_hash": self._checksum(blueprints),
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
            "or copy source wording merely to satisfy a lexical marker. Paraphrase and "
            "cross-language cases must remain semantically grounded. no_result blueprints "
            "must ask a plausible nearby question "
            "whose answer is absent from all provided evidence; return no evidence IDs or quotes."
        )
        user = (
            f"Create exactly {case_count} cases in blueprint order. Seed={seed}. "
            f"Locales={locales}. Keep each query between 3 and 4000 characters. "
            "For positive cases evidence_ids must exactly equal required_evidence_ids. "
            "Do not force exact source markers into semantic, paraphrase, or cross-language queries. "
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
            quotes = list(raw_case.get("anchor_quotes") or [])
            if expected_no_result:
                if evidence_ids or quotes:
                    raise BenchmarkGenerationError(
                        "No-result cases cannot contain evidence IDs or quotes."
                    )
                references: list[dict[str, Any]] = []
                review_status = "pending"
                leakage = {
                    "max_normalized_copy": 0,
                    "warning_threshold": None,
                    "warning": False,
                    "blocked": False,
                }
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
                    normalized_quote = self._normalize_text(quote)
                    if len(quote) < 8 or len(quote) > 240 or normalized_quote not in self._normalize_text(
                        str(evidence["text"])
                    ):
                        raise BenchmarkGenerationError(
                            f"Case {index + 1} anchor quote is not present in its fixed evidence."
                        )
                    references.append(
                        {
                            "reference_id": f"ref_{blueprint['blueprint_id']}_{ref_index + 1}",
                            "document_id": evidence["document_id"],
                            "chunk_id": evidence["chunk_id"],
                            "source_block_id": evidence["source_block_id"],
                            "page_number": evidence.get("page_number"),
                            "match_mode": "source_block",
                            "relevance": 3 if ref_index == 0 else 2,
                        }
                    )
                max_copy = max(
                    (
                        self._max_normalized_copy_length(
                            query_key,
                            self._normalize_text(
                                str(evidence_by_id[evidence_id]["text"])
                            ),
                        )
                        for evidence_id in required_ids
                    ),
                    default=0,
                )
                warning_threshold = (
                    12
                    if str(blueprint["query_type"]) in {"paraphrase", "cross_language"}
                    else 24
                )
                if max_copy >= 32:
                    raise BenchmarkGenerationError(
                        f"Case {index + 1} copies at least 32 normalized source characters."
                    )
                leakage = {
                    "max_normalized_copy": max_copy,
                    "warning_threshold": warning_threshold,
                    "warning": max_copy >= warning_threshold,
                    "blocked": False,
                }
                review_status = "pending"
            rationale = str(raw_case.get("rationale") or "").strip()[:500]
            context_refs = [
                {
                    "document_id": evidence_by_id[evidence_id]["document_id"],
                    "chunk_id": evidence_by_id[evidence_id]["chunk_id"],
                    "source_block_id": evidence_by_id[evidence_id]["source_block_id"],
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
                        "leakage": leakage,
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
                "corpus_snapshot_hash",
                "documents",
                "document_count",
                "evidence_count",
                "retrieval_profile",
                "processor_profile",
                "embedding_profile",
                "source_summary_hash",
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
        for values in by_document.values():
            values.sort(
                key=lambda item: (
                    0 if item.get("chunk_type") == "child" else 1,
                    int(item.get("page_number") or 0),
                    str(item.get("chunk_id") or ""),
                )
            )
        selected: list[dict[str, Any]] = []
        index = 0
        while len(selected) < MAX_EVIDENCE_UNITS:
            progressed = False
            for document_id in document_ids:
                values = by_document[document_id]
                if index < len(values):
                    selected.append(values[index])
                    progressed = True
                    if len(selected) >= MAX_EVIDENCE_UNITS:
                        break
            if not progressed:
                break
            index += 1
        bounded: list[dict[str, Any]] = []
        chars = 0
        for item in selected:
            text = str(item.get("text") or "")[:MAX_EVIDENCE_UNIT_CHARS]
            if chars + len(text) > MAX_EVIDENCE_CHARS:
                break
            bounded.append({**item, "text": text})
            chars += len(text)
        return bounded

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
    def _max_normalized_copy_length(left: str, right: str) -> int:
        """Return the longest shared substring length, capped at the blocker boundary."""

        if not left or not right:
            return 0
        shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
        low = 0
        high = min(32, len(shorter))
        while low < high:
            probe = (low + high + 1) // 2
            windows = {
                shorter[index : index + probe]
                for index in range(len(shorter) - probe + 1)
            }
            matched = any(
                longer[index : index + probe] in windows
                for index in range(len(longer) - probe + 1)
            )
            if matched:
                low = probe
            else:
                high = probe - 1
        return low

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
