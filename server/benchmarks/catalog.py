from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

try:
    from server.evaluations.store import EvaluationStateError, XpertEvaluationStore
except ModuleNotFoundError:
    from evaluations.store import EvaluationStateError, XpertEvaluationStore

from .builtin_packs import builtin_pack_specs
from .knowledge_packs import builtin_knowledge_pack_specs
from .models import BenchmarkManifest, BenchmarkPack


class BenchmarkCatalogError(RuntimeError):
    pass


class BenchmarkPackNotFoundError(BenchmarkCatalogError):
    pass


class BenchmarkCatalog:
    VERSION = "modelmirror-benchmark-catalog-v1"
    ALLOWED_CORE_METRICS = {"exact_match", "contains", "json_schema"}

    def __init__(self) -> None:
        self._packs = self._load_builtin_packs()

    def capabilities(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for pack in self._packs.values():
            kind = pack.manifest.kind
            counts[kind] = counts.get(kind, 0) + 1
        return {
            "version": self.VERSION,
            "kinds": sorted(counts),
            "pack_counts": counts,
            "locales": ["en", "zh-CN"],
            "core_metrics": sorted(self.ALLOWED_CORE_METRICS),
            "catalog_editable": False,
            "instantiation": {
                "agent_response": "xpert_evaluation_dataset_v1",
                "knowledge_retrieval": "managed_rag_benchmark_v1",
            },
        }

    def list_packs(self, *, kind: str | None = None) -> list[dict[str, Any]]:
        items = [
            pack.manifest.model_dump(mode="json")
            for pack in self._packs.values()
            if kind is None or pack.manifest.kind == kind
        ]
        items.sort(key=lambda item: (item["kind"], item["pack_id"], item["version"]))
        return items

    def get_pack(self, pack_id: str) -> BenchmarkPack:
        pack = self._packs.get(pack_id)
        if pack is None:
            raise BenchmarkPackNotFoundError("Benchmark pack not found.")
        return pack.model_copy(deep=True)

    def pack_payload(self, pack_id: str) -> dict[str, Any]:
        pack = self.get_pack(pack_id)
        if pack.manifest.kind == "knowledge_retrieval":
            return {
                **pack.manifest.model_dump(mode="json"),
                "documents": [
                    {
                        "document_key": item["document_key"],
                        "filename": item["filename"],
                        "locale": item["locale"],
                        "title": item["title"],
                    }
                    for item in pack.documents
                ],
                "cases": [
                    {
                        "case_id": item["case_id"],
                        "query": item["query"],
                        "expected_no_result": bool(item.get("expected_no_result")),
                        "tags": copy.deepcopy(item.get("tags") or []),
                        "expected_document_keys": sorted(
                            {
                                str(reference.get("document_key") or "")
                                for reference in item.get("expected_refs", [])
                                if str(reference.get("document_key") or "")
                            }
                        ),
                    }
                    for item in pack.cases
                ],
            }
        return {
            **pack.manifest.model_dump(mode="json"),
            "cases": copy.deepcopy(pack.cases),
        }

    def instantiate(
        self,
        pack_id: str,
        *,
        store: XpertEvaluationStore,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        pack = self.get_pack(pack_id)
        if pack.manifest.kind != "agent_response":
            raise BenchmarkCatalogError(
                "This benchmark kind cannot be instantiated by Xpert Evaluator."
            )
        return store.instantiate_catalog_dataset(
            name=name or pack.manifest.name,
            description=(
                pack.manifest.description if description is None else description
            ),
            cases=copy.deepcopy(pack.cases),
            catalog_ref={
                "pack_id": pack.manifest.pack_id,
                "version": pack.manifest.version,
                "checksum": pack.manifest.checksum,
            },
            provenance={
                "source": pack.manifest.source,
                "license": pack.manifest.license,
                "locales": pack.manifest.locales,
            },
            coverage={
                "areas": pack.manifest.coverage,
                "difficulty": pack.manifest.difficulty,
                "metric_policy": pack.manifest.metric_policy,
                "target_requirements": pack.manifest.target_requirements,
            },
            release_notes=(
                f"Instantiated from {pack.manifest.pack_id} "
                f"v{pack.manifest.version}."
            ),
        )

    @classmethod
    def _load_builtin_packs(cls) -> dict[str, BenchmarkPack]:
        packs: dict[str, BenchmarkPack] = {}
        for spec in [*builtin_pack_specs(), *builtin_knowledge_pack_specs()]:
            kind = str(spec["manifest"].get("kind") or "")
            documents = copy.deepcopy(spec.get("documents") or [])
            if kind == "agent_response":
                cases = [XpertEvaluationStore.normalize_case(case) for case in spec["cases"]]
                cls._validate_cases(cases)
            else:
                cases = copy.deepcopy(spec["cases"])
                cls._validate_knowledge_pack(documents, cases)
            checksum = (
                cls._checksum(cases)
                if kind == "agent_response"
                else cls._checksum({"documents": documents, "cases": cases})
            )
            manifest = BenchmarkManifest.model_validate(
                {
                    **spec["manifest"],
                    "case_count": len(cases),
                    "document_count": len(documents),
                    "checksum": checksum,
                }
            )
            if manifest.pack_id in packs:
                raise BenchmarkCatalogError(
                    f"Duplicate benchmark pack id: {manifest.pack_id}"
                )
            packs[manifest.pack_id] = BenchmarkPack(
                manifest=manifest,
                cases=cases,
                documents=documents,
            )
        return packs

    @classmethod
    def _validate_cases(cls, cases: list[dict[str, Any]]) -> None:
        case_ids = [str(case.get("case_id") or "") for case in cases]
        if len(case_ids) != len(set(case_ids)):
            raise BenchmarkCatalogError("Benchmark case ids must be unique within a pack.")
        for case in cases:
            expected = dict(case.get("expected") or {})
            weights = dict(case.get("weights") or {})
            if expected.get("rubric") or expected.get("citation_ids"):
                raise BenchmarkCatalogError(
                    "Built-in agent packs may only use deterministic expectations."
                )
            if weights and not set(weights).issubset(cls.ALLOWED_CORE_METRICS):
                raise BenchmarkCatalogError("Unsupported core metric in benchmark pack.")
            if not any(
                expected.get(field) not in (None, [], {}, "")
                for field in ("exact_answer", "contains", "json_schema")
            ):
                raise EvaluationStateError(
                    "Every benchmark case requires a deterministic expectation."
                )

    @classmethod
    def _validate_knowledge_pack(
        cls,
        documents: list[dict[str, Any]],
        cases: list[dict[str, Any]],
    ) -> None:
        if not documents or len(documents) > 100:
            raise BenchmarkCatalogError("Knowledge benchmark corpus size is invalid.")
        document_keys = [str(item.get("document_key") or "") for item in documents]
        if any(not key for key in document_keys) or len(document_keys) != len(set(document_keys)):
            raise BenchmarkCatalogError("Knowledge benchmark document keys must be unique.")
        anchors = {
            str(anchor_key): (str(item["document_key"]), str(anchor_phrase))
            for item in documents
            for anchor_key, anchor_phrase in dict(item.get("anchors") or {}).items()
        }
        case_ids = [str(item.get("case_id") or "") for item in cases]
        if any(not case_id for case_id in case_ids) or len(case_ids) != len(set(case_ids)):
            raise BenchmarkCatalogError("Knowledge benchmark case ids must be unique.")
        for case in cases:
            references = list(case.get("expected_refs") or [])
            no_result = bool(case.get("expected_no_result"))
            if no_result == bool(references):
                raise BenchmarkCatalogError("Knowledge cases require exactly one expectation mode.")
            for reference in references:
                key = str(reference.get("anchor_key") or "")
                document_key = str(reference.get("document_key") or "")
                expected = anchors.get(key)
                if expected is None or expected[0] != document_key:
                    raise BenchmarkCatalogError("Knowledge Gold anchor is invalid.")
                if expected[1] != str(reference.get("anchor_phrase") or ""):
                    raise BenchmarkCatalogError("Knowledge Gold anchor phrase drifted.")

    @staticmethod
    def _checksum(value: Any) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
