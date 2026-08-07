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
                "knowledge_retrieval": "planned",
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
        for spec in builtin_pack_specs():
            cases = [XpertEvaluationStore.normalize_case(case) for case in spec["cases"]]
            cls._validate_cases(cases)
            checksum = cls._checksum(cases)
            manifest = BenchmarkManifest.model_validate(
                {
                    **spec["manifest"],
                    "case_count": len(cases),
                    "checksum": checksum,
                }
            )
            if manifest.pack_id in packs:
                raise BenchmarkCatalogError(
                    f"Duplicate benchmark pack id: {manifest.pack_id}"
                )
            packs[manifest.pack_id] = BenchmarkPack(manifest=manifest, cases=cases)
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

    @staticmethod
    def _checksum(value: Any) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
