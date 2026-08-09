from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from server.benchmarks import api as benchmark_api
from server.benchmarks.catalog import BenchmarkCatalog
from server.evaluations.store import XpertEvaluationStore


EXPECTED_PACK_COUNTS = {
    "mm-agent-instruction-bilingual-v1": 20,
    "mm-agent-structured-json-bilingual-v1": 16,
    "mm-agent-multiturn-bilingual-v1": 16,
    "mm-agent-abstention-bilingual-v1": 12,
}

EXPECTED_KNOWLEDGE_PACK_COUNTS = {
    "modelmirror-rag-foundation-bilingual-v1": (12, 40),
}


def test_builtin_agent_catalog_is_stable_bilingual_and_deterministic() -> None:
    catalog = BenchmarkCatalog()
    items = catalog.list_packs(kind="agent_response")

    assert {item["pack_id"]: item["case_count"] for item in items} == EXPECTED_PACK_COUNTS
    assert sum(item["case_count"] for item in items) == 64
    assert all(item["locales"] == ["zh-CN", "en"] for item in items)
    assert all(len(item["checksum"]) == 64 for item in items)
    assert catalog.capabilities()["core_metrics"] == [
        "contains",
        "exact_match",
        "json_schema",
    ]

    for item in items:
        pack = catalog.get_pack(item["pack_id"])
        assert pack.manifest.checksum == item["checksum"]
        assert pack.manifest.source.startswith("ModelMirror-authored")
        assert pack.manifest.license == "LicenseRef-ModelMirror-Project"
        locales = {locale for case in pack.cases for locale in case.get("tags", [])}
        assert {"zh-CN", "en"}.issubset(locales)
        for case in pack.cases:
            expected = case["expected"]
            assert not expected.get("rubric")
            assert not expected.get("citation_ids")
            assert set(case.get("weights") or {}).issubset(
                {"exact_match", "contains", "json_schema"}
            )


def test_builtin_knowledge_catalog_is_locked_bilingual_and_safe() -> None:
    catalog = BenchmarkCatalog()
    items = catalog.list_packs(kind="knowledge_retrieval")

    assert {
        item["pack_id"]: (item["document_count"], item["case_count"])
        for item in items
    } == EXPECTED_KNOWLEDGE_PACK_COUNTS
    item = items[0]
    assert item["locales"] == ["zh-CN", "en"]
    assert item["metric_policy"] == {
        "mode": "advisory",
        "min_recall_at_5": 0.70,
        "min_citation_coverage": 0.70,
        "min_no_result_accuracy": 0.80,
    }
    pack = catalog.get_pack(item["pack_id"])
    assert sum(bool(case.get("expected_no_result")) for case in pack.cases) == 6
    categories = [str(case["tags"][0]) for case in pack.cases]
    assert {name: categories.count(name) for name in set(categories)} == {
        "fact": 12,
        "paraphrase": 8,
        "parent_child": 8,
        "cross_language": 6,
        "no_answer": 6,
    }

    payload = catalog.pack_payload(item["pack_id"])
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert "anchor_phrase" not in serialized
    assert "# 极光设备运维手册" not in serialized
    assert "stored_path" not in serialized


def test_catalog_instantiation_atomically_publishes_immutable_v1(tmp_path: Path) -> None:
    catalog = BenchmarkCatalog()
    store = XpertEvaluationStore(tmp_path)
    dataset = catalog.instantiate(
        "mm-agent-instruction-bilingual-v1",
        store=store,
    )

    assert dataset["origin"] == "catalog"
    assert dataset["published_version"] == 1
    assert dataset["case_count"] == 20
    assert dataset["catalog_ref"]["pack_id"] == "mm-agent-instruction-bilingual-v1"
    assert dataset["calibration"]["status"] == "calibrated"
    first = store.get_dataset_version(dataset["dataset_id"], 1)
    assert first["checksum"] == dataset["catalog_ref"]["checksum"]
    assert first["origin"] == "catalog"

    changed = store.put_cases(
        dataset["dataset_id"],
        revision=dataset["revision"],
        cases=[
            {
                "case_id": "local-extra",
                "message": "Output exactly local.",
                "expected": {"exact_answer": "local"},
            }
        ],
    )
    assert changed["calibration"]["status"] == "stale"
    assert len(changed["cases"]) == 21
    assert store.get_dataset_version(dataset["dataset_id"], 1)["case_count"] == 20

    restored = XpertEvaluationStore(tmp_path)
    restored_dataset = restored.dataset_payload(
        restored.require_dataset(dataset["dataset_id"]), include_cases=False
    )
    assert restored_dataset["origin"] == "catalog"
    assert restored_dataset["catalog_ref"] == dataset["catalog_ref"]


def test_legacy_dataset_defaults_to_manual_origin(tmp_path: Path) -> None:
    payload = {
        "schema_version": 1,
        "datasets": {
            "legacy": {
                "dataset_id": "legacy",
                "name": "Legacy",
                "description": "",
                "status": "draft",
                "revision": 1,
                "published_version": None,
                "cases": [],
                "versions": [],
                "created_at": 1,
                "updated_at": 1,
            }
        },
        "runs": {},
    }
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "xpert_evaluations.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    item = XpertEvaluationStore(tmp_path).list_datasets()[0]
    assert item["origin"] == "manual"
    assert item["catalog_ref"] == {}
    assert item["provenance"] == {}
    assert item["coverage"] == {}
    assert item["calibration"]["status"] == "pending"


@pytest.mark.asyncio
async def test_benchmark_api_returns_catalog_and_instantiates_dataset(
    tmp_path: Path,
) -> None:
    app = FastAPI()
    app.include_router(benchmark_api.router)
    benchmark_api.configure_benchmarks(XpertEvaluationStore(tmp_path))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        catalog_response = await client.get(
            "/api/benchmarks/catalog", params={"kind": "agent_response"}
        )
        assert catalog_response.status_code == 200
        assert catalog_response.json()["total"] == 4

        detail_response = await client.get(
            "/api/benchmarks/catalog/mm-agent-structured-json-bilingual-v1"
        )
        assert detail_response.status_code == 200
        assert detail_response.json()["case_count"] == 16

        create_response = await client.post(
            "/api/benchmarks/catalog/mm-agent-multiturn-bilingual-v1/instantiate",
            json={},
        )
        assert create_response.status_code == 200
        created = create_response.json()
        assert created["published_version"] == 1
        assert created["case_count"] == 16

    serialized = json.dumps(
        {
            "catalog": catalog_response.json(),
            "detail": detail_response.json(),
            "created": created,
        }
    ).lower()
    for forbidden in (
        "stored_path",
        "api_key",
        "openrouter_api_key",
        "embedding",
        "runtime store",
    ):
        assert forbidden not in serialized
