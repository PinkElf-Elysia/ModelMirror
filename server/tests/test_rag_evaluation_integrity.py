from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from server.model_router.workload_control import PROVIDER_WORKLOAD_CONTRACT_VERSION
from server.rag.evaluation import (
    EvaluationPromotionError,
    KnowledgeEvaluationStore,
    evaluation_runtime_code_fingerprint,
    formal_execution_preflight_reasons,
    qualify_formal_execution_integrity,
    seal_execution_manifest,
    validate_formal_run_admission,
    _published_gold_checksum,
)
from server.rag.evaluation_executor import KnowledgeEvaluationExecutor


def _target(version_id: str, *, mode: str = "vector") -> dict[str, Any]:
    vector_required = mode in {"vector", "hybrid"}
    embedding = {
        "provider": "openai_compatible" if vector_required else "none",
        "model": "bge-m3" if vector_required else "",
        "dimension": 1024 if vector_required else 0,
        "degraded": False,
        "ready": True,
        "reason": None,
        "access_mode": "managed" if vector_required else "not_applicable",
        "status": "ready" if vector_required else "not_applicable",
        "embedding_space_fingerprint": "e" * 64 if vector_required else "",
    }
    retrieval = {
        "mode": mode,
        "top_k": 3,
        "rerank_enabled": False,
        "rerank_provider": "none",
        "rerank_model": "",
        "rerank_top_n": 0,
    }
    return {
        "target_id": version_id,
        "version_id": version_id,
        "corpus_snapshot_hash": "c" * 64,
        "version_evidence": {
            "schema_version": "rag-version-evidence-v1",
            "kb_id": "kb-a",
            "version_id": version_id,
            "version_fingerprint": ("a" if version_id == "baseline" else "b") * 64,
            "configuration_fingerprint": ("1" if version_id == "baseline" else "2") * 64,
            "source_manifest_fingerprint": "d" * 64,
            "processor": {"mode": "general", "fingerprint": "c" * 64},
            "embedding": {"effective": embedding},
            "retrieval": retrieval,
            "index_contract": {
                "contract_version": "rag-index-contract-v3",
                "index_schema_version": 3,
                "retrieval_mode": mode,
                "vector": {
                    "required": vector_required,
                    "embedding_space_fingerprint": embedding[
                        "embedding_space_fingerprint"
                    ],
                    "dimension": embedding["dimension"],
                    "distance_contract": "cosine_v1" if vector_required else "not_applicable",
                },
                "lexical": {
                    "required": mode in {"fulltext", "hybrid"},
                    "backend": "sqlite_fts5",
                },
            },
            "vector_backend_readiness": {
                "configured_backend": "chroma",
                "effective_backend": "chroma",
                "ready": True,
                "reason_code": None,
                "distance_contract": "cosine_v1",
            },
            "runtime_vector_backend_readiness": {
                "configured_backend": "chroma",
                "effective_backend": "chroma",
                "ready": True,
                "reason_code": None,
                "distance_contract": "cosine_v1",
            },
        },
    }


def _gold() -> dict[str, Any]:
    gold = {
        "version_id": "evalsetver-v2",
        "kb_id": "kb-a",
        "version": 1,
        "benchmark_contract_version": "rag-gold-v2",
        "cases": [{"case_id": "case-a", "query": "q", "expected_refs": []}],
        "corpus_snapshot": {"checksum": "c" * 64},
    }
    gold["checksum"] = _published_gold_checksum(gold)
    return gold


def _admission(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str = "vector",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    monkeypatch.setattr(
        "server.rag.evaluation.qualify_formal_evidence",
        lambda _snapshot: {"qualified": True, "status": "qualified"},
    )
    targets = [_target("baseline", mode=mode), _target("candidate", mode=mode)]
    admitted = validate_formal_run_admission(
        _gold(),
        targets,
        baseline_version_id="baseline",
    )
    return targets, admitted


def _integrity_run(
    targets: list[dict[str, Any]],
    admitted: dict[str, Any],
    case_result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "run_id": "run-a",
        "kb_id": "kb-a",
        "eval_set_id": "evalset-a",
        "eval_set_revision": 1,
        "eval_set_version_id": _gold()["version_id"],
        "run_mode": "formal",
        "metric_contract_version": "rag-metrics-v2",
        "baseline_version_id": "baseline",
        "execution_manifest": admitted["execution_manifest"],
        "targets": targets,
        "eval_set_snapshot": _gold(),
        "case_ids": ["case-a"],
        "comparability": admitted["comparability"],
        "case_results": {
            target["target_id"]: {"case-a": deepcopy(case_result)}
            for target in targets
        },
        "target_results": [
            {
                "version_id": target["version_id"],
                "metrics": {"error_count": 0},
            }
            for target in targets
        ],
    }


def _runtime_projection(admitted: dict[str, Any]) -> dict[str, Any]:
    targets: dict[str, dict[str, Any]] = {}
    for declared in admitted["execution_manifest"]["targets"]:
        version_id = str(declared["version_id"])
        targets[version_id] = {
            "kb_id": declared["kb_id"],
            "version_fingerprint": declared["version_fingerprint"],
            "configuration_fingerprint": declared["configuration_fingerprint"],
            "source_manifest_fingerprint": declared["source_manifest_fingerprint"],
            "corpus_snapshot_hash": declared["corpus_snapshot_hash"],
            "corpus_snapshot_status": "current",
            "embedding": deepcopy(declared["embedding"]),
            "retrieval": deepcopy(declared["retrieval"]),
            "index_contract": deepcopy(declared["index_contract"]),
            "vector_backend_readiness": deepcopy(
                declared["vector_backend_readiness"]
            ),
            "runtime_vector_backend_readiness": deepcopy(
                declared["runtime_vector_backend_readiness"]
            ),
        }
    return {"kb_exists": True, "targets": targets}


def test_formal_admission_rejects_degraded_hash_and_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets, _ = _admission(monkeypatch)

    hash_target = deepcopy(targets)
    hash_target[1]["version_evidence"]["embedding"]["effective"]["provider"] = "hash"
    with pytest.raises(ValueError, match="production embedding"):
        validate_formal_run_admission(
            _gold(), hash_target, baseline_version_id="baseline"
        )

    degraded_target = deepcopy(targets)
    degraded_target[1]["version_evidence"]["embedding"]["effective"]["degraded"] = True
    with pytest.raises(ValueError, match="production embedding"):
        validate_formal_run_admission(
            _gold(), degraded_target, baseline_version_id="baseline"
        )

    mismatched_target = deepcopy(targets)
    mismatched_target[1]["version_evidence"]["index_contract"]["vector"][
        "dimension"
    ] = 768
    with pytest.raises(ValueError, match="index identity"):
        validate_formal_run_admission(
            _gold(), mismatched_target, baseline_version_id="baseline"
        )

    legacy_route = deepcopy(targets)
    legacy_route[1]["version_evidence"]["embedding"]["effective"][
        "access_mode"
    ] = "legacy"
    with pytest.raises(ValueError, match="production embedding"):
        validate_formal_run_admission(
            _gold(), legacy_route, baseline_version_id="baseline"
        )

    missing_build_backend = deepcopy(targets)
    missing_build_backend[1]["version_evidence"][
        "vector_backend_readiness"
    ] = {}
    with pytest.raises(ValueError, match="index identity"):
        validate_formal_run_admission(
            _gold(), missing_build_backend, baseline_version_id="baseline"
        )


def test_fulltext_formal_accepts_not_applicable_embedding_and_requires_zero_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets, admitted = _admission(monkeypatch, mode="fulltext")
    case_result = {
        "status": "succeeded",
        "execution_mode": "local_non_model",
        "provider_route_receipts": None,
        "fallback_reason_codes": [],
        "retrieval_receipt": {
            "mode": "fulltext",
            "top_k": 3,
            "embedding_provider": "none",
            "embedding_model": "",
            "embedding_dimension": 0,
            "embedding_space_fingerprint": "",
            "rerank_applied": False,
            "promotion_eligible": True,
            "promotion_ineligibility_reasons": [],
        },
    }
    run = _integrity_run(targets, admitted, case_result)

    assert qualify_formal_execution_integrity(run)["qualified"] is True
    run["case_results"]["candidate"]["case-a"][
        "provider_route_receipts"
    ] = {
        "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
        "routing_mode": "managed_required",
        "status": "passed",
        "call_count": 1,
        "reason_codes": [],
        "calls": [
            {
                "operation": "embedding_vectors",
                "model_id": "bge-m3",
                "provider_kind": "openai_compatible",
                "actual_model": "bge-m3",
                "status": "passed",
                "dispatched": True,
            }
        ],
    }
    rejected = qualify_formal_execution_integrity(run)
    assert rejected["qualified"] is False
    assert "fulltext_embedding_call_detected" in rejected["reason_codes"]


def test_fulltext_formal_allows_verified_rerank_without_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "server.rag.evaluation.qualify_formal_evidence",
        lambda _snapshot: {"qualified": True, "status": "qualified"},
    )
    targets = [
        _target("baseline", mode="fulltext"),
        _target("candidate", mode="fulltext"),
    ]
    for target in targets:
        target["version_evidence"]["retrieval"].update(
            {
                "rerank_enabled": True,
                "rerank_provider": "api",
                "rerank_model": "dedicated-reranker",
                "rerank_top_n": 2,
            }
        )
    admitted = validate_formal_run_admission(
        _gold(), targets, baseline_version_id="baseline"
    )
    run = _integrity_run(
        targets,
        admitted,
        {
            "status": "completed",
            "execution_mode": "managed",
            "provider_route_receipts": {
                "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
                "routing_mode": "managed_required",
                "status": "passed",
                "call_count": 1,
                "reason_codes": [],
                "calls": [
                    {
                        "call_sequence": 1,
                        "operation": "rerank_documents",
                        "model_id": "dedicated-reranker",
                        "provider_kind": "openai_compatible",
                        "actual_model": "dedicated-reranker",
                        "access_mode": "dedicated",
                        "status": "passed",
                        "dispatched": True,
                    }
                ],
            },
            "fallback_reason_codes": [],
            "retrieval_receipt": {
                "mode": "fulltext",
                "top_k": 3,
                "embedding_provider": "none",
                "embedding_dimension": 0,
                "rerank_applied": True,
                "rerank_provider_used": "api",
                "rerank_model_used": "dedicated-reranker",
                "promotion_eligible": True,
                "promotion_ineligibility_reasons": [],
            },
        },
    )

    assert qualify_formal_execution_integrity(run)["qualified"] is True


def test_vector_formal_rejects_provider_fallback_and_actual_model_tampering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets, admitted = _admission(monkeypatch)
    version_id = "candidate"
    case_result = {
        "status": "completed",
        "execution_mode": "managed",
        "provider_route_receipts": {
            "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
            "routing_mode": "managed_required",
            "status": "passed",
            "call_count": 1,
            "reason_codes": [],
            "calls": [
                {
                    "call_sequence": 1,
                    "operation": "embedding_vectors",
                    "model_id": "bge-m3",
                    "provider_kind": "openai_compatible",
                    "actual_model": "bge-m3",
                    "status": "passed",
                    "dispatched": True,
                }
            ],
        },
        "fallback_reason_codes": [],
        "retrieval_receipt": {
            "mode": "vector",
            "top_k": 3,
            "embedding_provider": "openai_compatible",
            "embedding_model": "bge-m3",
            "embedding_dimension": 1024,
            "embedding_space_fingerprint": "e" * 64,
            "rerank_applied": False,
            "promotion_eligible": True,
            "promotion_ineligibility_reasons": [],
        },
    }
    run = _integrity_run(targets, admitted, case_result)

    assert qualify_formal_execution_integrity(run)["qualified"] is True
    candidate_case = run["case_results"][version_id]["case-a"]
    candidate_case["provider_route_receipts"]["calls"][0]["actual_model"] = "other"
    mismatched = qualify_formal_execution_integrity(run)
    assert mismatched["qualified"] is False
    assert f"embedding_model_mismatch:{version_id}:case-a" in mismatched[
        "reason_codes"
    ]

    candidate_case["provider_route_receipts"]["calls"][0]["actual_model"] = "bge-m3"
    candidate_case["fallback_reason_codes"] = ["local_non_model_fallback"]
    fallback = qualify_formal_execution_integrity(run)
    assert fallback["qualified"] is False
    assert f"provider_fallback_used:{version_id}:case-a" in fallback["reason_codes"]


def test_vector_formal_accepts_valid_openrouter_provider_local_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = [_target("baseline"), _target("candidate")]
    for target in targets:
        target["version_evidence"]["embedding"]["effective"][
            "model"
        ] = "provider/bge-m3"
    monkeypatch.setattr(
        "server.rag.evaluation.qualify_formal_evidence",
        lambda _snapshot: {"qualified": True, "status": "qualified"},
    )
    admitted = validate_formal_run_admission(
        _gold(), targets, baseline_version_id="baseline"
    )
    case_result = {
        "status": "completed",
        "execution_mode": "managed",
        "provider_route_receipts": {
            "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
            "routing_mode": "managed_required",
            "status": "passed",
            "call_count": 1,
            "reason_codes": [],
            "calls": [
                {
                    "operation": "embedding_vectors",
                    "model_id": "provider/bge-m3",
                    "provider_kind": "openrouter",
                    "actual_model": "bge-m3",
                    "status": "passed",
                    "dispatched": True,
                }
            ],
        },
        "fallback_reason_codes": [],
        "retrieval_receipt": {
            "mode": "vector",
            "top_k": 3,
            "embedding_provider": "openai_compatible",
            "embedding_model": "provider/bge-m3",
            "embedding_dimension": 1024,
            "embedding_space_fingerprint": "e" * 64,
            "rerank_applied": False,
            "promotion_eligible": True,
            "promotion_ineligibility_reasons": [],
        },
    }
    run = _integrity_run(targets, admitted, case_result)
    assert qualify_formal_execution_integrity(run)["qualified"] is True


def test_run_projection_is_read_only_and_fails_closed_on_missing_or_tampered_refs(
    tmp_path: Path,
) -> None:
    current = {
        "kb_exists": True,
        "code_fingerprint": "f" * 64,
        "targets": {
            "candidate": {
                "version_fingerprint": "b" * 64,
                "configuration_fingerprint": "2" * 64,
                "source_manifest_fingerprint": "d" * 64,
                "corpus_snapshot_hash": "c" * 64,
            }
        },
    }
    current_code_fingerprint = ["f" * 64]
    store = KnowledgeEvaluationStore(
        tmp_path / "evaluations.json",
        reproducibility_resolver=lambda _run: deepcopy(current),
        code_fingerprint_resolver=lambda: current_code_fingerprint[0],
    )
    evaluation_set = store.create_set("kb-a", "integrity")
    evaluation_set = store.add_cases(
        evaluation_set["eval_set_id"],
        expected_revision=1,
        cases=[{"query": "q", "expected_refs": [{"document_id": "doc-a"}]}],
    )
    manifest = seal_execution_manifest(
        {
            "contract_version": "rag-eval-v2",
            "metric_contract_version": "rag-metrics-v2",
            "evaluation_version_id": None,
            "evaluation_checksum": None,
            "corpus_snapshot_hash": "c" * 64,
            "evaluator_code_fingerprint": "f" * 64,
            "targets": [
                {
                    "version_id": "candidate",
                    "version_fingerprint": "b" * 64,
                    "configuration_fingerprint": "2" * 64,
                    "source_manifest_fingerprint": "d" * 64,
                    "corpus_snapshot_hash": "c" * 64,
                }
            ],
        }
    )
    run = store.create_run(
        evaluation_set=evaluation_set,
        targets=[{"target_id": "candidate", "version_id": "candidate"}],
        baseline_version_id=None,
        ks=[5],
        gate_policy=store.get_gate_policy("kb-a"),
        run_mode="diagnostic",
        execution_manifest=manifest,
        comparability={"comparable": True, "same_corpus": True},
        evidence_qualification={"qualified": True},
    )

    first = store.get_run(run["run_id"])
    assert first["reproducibility_status"] == "current"
    persisted_before = (tmp_path / "evaluations.json").read_bytes()

    current["targets"].pop("candidate")
    orphaned = store.get_run(run["run_id"])
    assert orphaned["reproducibility_status"] == "orphaned"
    assert "pipeline_version_missing:candidate" in orphaned["reproducibility_reasons"]
    assert (tmp_path / "evaluations.json").read_bytes() == persisted_before

    current["targets"]["candidate"] = {
        "version_fingerprint": "b" * 64,
        "configuration_fingerprint": "9" * 64,
        "source_manifest_fingerprint": "d" * 64,
        "corpus_snapshot_hash": "c" * 64,
    }
    drifted = store.get_run(run["run_id"])
    assert drifted["reproducibility_status"] == "unreproducible"
    assert "configuration_fingerprint_mismatch:candidate" in drifted[
        "reproducibility_reasons"
    ]

    current["targets"]["candidate"]["configuration_fingerprint"] = "2" * 64
    current["targets"]["candidate"]["corpus_snapshot_hash"] = "9" * 64
    corpus_drifted = store.get_run(run["run_id"])
    assert corpus_drifted["reproducibility_status"] == "unreproducible"
    assert "corpus_snapshot_hash_mismatch:candidate" in corpus_drifted[
        "reproducibility_reasons"
    ]

    current["targets"]["candidate"]["corpus_snapshot_status"] = "unreproducible"
    snapshot_unavailable = store.get_run(run["run_id"])
    assert snapshot_unavailable["reproducibility_status"] == "unreproducible"
    assert "corpus_snapshot_unreproducible:candidate" in snapshot_unavailable[
        "reproducibility_reasons"
    ]
    current["targets"]["candidate"].pop("corpus_snapshot_status")

    current["targets"]["candidate"]["corpus_snapshot_hash"] = "c" * 64
    current["kb_exists"] = False
    missing_kb = store.get_run(run["run_id"])
    assert missing_kb["reproducibility_status"] == "orphaned"
    assert "knowledge_base_missing" in missing_kb["reproducibility_reasons"]

    current["kb_exists"] = True
    current_code_fingerprint[0] = "9" * 64
    code_drifted = store.get_run(run["run_id"])
    assert code_drifted["reproducibility_status"] == "unreproducible"
    assert "evaluator_code_fingerprint_mismatch" in code_drifted[
        "reproducibility_reasons"
    ]

    current_code_fingerprint[0] = "f" * 64
    storage_path = tmp_path / "evaluations.json"
    persisted = json.loads(storage_path.read_text(encoding="utf-8"))
    persisted["runs"][run["run_id"]]["execution_manifest"]["targets"][0][
        "configuration_fingerprint"
    ] = "8" * 64
    storage_path.write_text(json.dumps(persisted), encoding="utf-8")
    checksum_tampered = store.get_run(run["run_id"])
    assert checksum_tampered["reproducibility_status"] == "unreproducible"
    assert "execution_manifest_checksum_invalid" in checksum_tampered[
        "reproducibility_reasons"
    ]


def test_old_passed_gate_cannot_bypass_reproducibility_or_execution_integrity(
    tmp_path: Path,
) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    evaluation_set = store.create_set("kb-a", "legacy")
    evaluation_set = store.add_cases(
        evaluation_set["eval_set_id"],
        expected_revision=1,
        cases=[{"query": "q", "expected_refs": [{"document_id": "doc-a"}]}],
    )
    run = store.create_run(
        evaluation_set=evaluation_set,
        targets=[{"target_id": "candidate", "version_id": "candidate"}],
        baseline_version_id=None,
        ks=[5],
        gate_policy=store.get_gate_policy("kb-a"),
        run_mode="formal",
        execution_manifest=seal_execution_manifest(
            {
                "contract_version": "rag-eval-v2",
                "metric_contract_version": "rag-metrics-v2",
                "evaluator_code_fingerprint": "f" * 64,
                "targets": [],
            }
        ),
        comparability={"comparable": True, "same_corpus": True},
        evidence_qualification={"qualified": True},
    )
    store.complete_run(
        run["run_id"],
        [
            {
                "version_id": "candidate",
                "metrics": {"error_count": 0},
                "promotion_gate": {"passed": True},
            }
        ],
    )

    with pytest.raises(EvaluationPromotionError, match="reproducible"):
        store.assert_promotion_allowed(
            kb_id="kb-a",
            version_id="candidate",
            evaluation_run_id=run["run_id"],
            require_passed_run=True,
        )


class _FakeService:
    def __init__(self, retrieval: dict[str, Any]) -> None:
        self.retrieval = retrieval
        self.calls: list[dict[str, Any]] = []

    async def query_pipeline_version(self, version_id: str, question: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"version_id": version_id, "question": question, **kwargs})
        return deepcopy(self.retrieval)

    @staticmethod
    def _safe_pipeline_error(exc: Exception) -> str:
        return str(exc)


@pytest.mark.asyncio
async def test_formal_uses_version_top_k_and_persists_top_level_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _FakeService(
        {
            "sources": [],
            "warnings": [],
            "execution_mode": "managed",
            "provider_route_receipts": {
                "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
                "routing_mode": "managed_required",
                "status": "passed",
                "call_count": 1,
                "reason_codes": [],
                "calls": [
                    {
                        "call_sequence": 1,
                        "operation": "embedding_vectors",
                        "model_id": "bge-m3",
                        "provider_kind": "openai_compatible",
                        "actual_model": "bge-m3",
                        "status": "passed",
                        "dispatched": True,
                    }
                ],
            },
            "fallback_reason_codes": [],
            "retrieval": {
                "mode": "vector",
                "top_k": 3,
                "embedding_provider": "openai_compatible",
                "embedding_model": "bge-m3",
                "embedding_dimension": 1024,
                "embedding_space_fingerprint": "e" * 64,
                "rerank_applied": False,
                "promotion_eligible": True,
                "promotion_ineligibility_reasons": [],
            },
        }
    )
    targets, admitted = _admission(monkeypatch)
    runtime_projection = _runtime_projection(admitted)
    store = KnowledgeEvaluationStore(
        tmp_path / "evaluations.json",
        reproducibility_resolver=lambda _run: deepcopy(runtime_projection),
    )
    evaluation_set = store.create_set("kb-a", "top-k")
    evaluation_version = {
        **_gold(),
        "eval_set_id": evaluation_set["eval_set_id"],
        "source_revision": evaluation_set["revision"],
    }
    with store._lock:
        data = store._read_unlocked()
        data["versions"][evaluation_version["version_id"]] = deepcopy(
            evaluation_version
        )
        store._write_unlocked(data)
    run = store.create_run(
        evaluation_set=evaluation_set,
        evaluation_set_version=evaluation_version,
        targets=targets,
        baseline_version_id="baseline",
        ks=[1, 5, 10],
        gate_policy=store.get_gate_policy("kb-a"),
        run_mode="formal",
        execution_manifest=admitted["execution_manifest"],
        comparability=admitted["comparability"],
        evidence_qualification={"qualified": True},
    )
    executor = KnowledgeEvaluationExecutor(service, store)
    assert await executor.run_once() is True

    assert service.calls[0]["top_k"] == 3
    stored = store.get_run(run["run_id"])
    case_id = "case-a"
    result = stored["case_results"]["candidate"][case_id]
    assert result["execution_mode"] == "managed"
    assert result["provider_route_receipts"]["calls"][0]["actual_model"] == "bge-m3"
    assert result["fallback_reason_codes"] == []

    service.calls.clear()
    tampered = store.create_run(
        evaluation_set=evaluation_set,
        evaluation_set_version=evaluation_version,
        targets=targets,
        baseline_version_id="baseline",
        ks=[1, 5, 10],
        gate_policy=store.get_gate_policy("kb-a"),
        run_mode="formal",
        execution_manifest=admitted["execution_manifest"],
        comparability=admitted["comparability"],
        evidence_qualification={"qualified": True},
    )
    with store._lock:
        data = store._read_unlocked()
        data["runs"][tampered["run_id"]]["execution_manifest"]["targets"][0][
            "retrieval"
        ]["top_k"] = 10
        store._write_unlocked(data)
    assert await executor.run_once() is True
    assert service.calls == []
    failed = store.get_run(tampered["run_id"], project_reproducibility=False)
    assert failed["status"] == "failed"
    assert "Formal evaluation preflight failed" in str(failed["error"])


def test_diagnostic_top_k_override_is_recorded_as_experimental_variable(
    tmp_path: Path,
) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    evaluation_set = store.create_set("kb-a", "diagnostic")
    evaluation_set = store.add_cases(
        evaluation_set["eval_set_id"],
        expected_revision=1,
        cases=[{"query": "q", "expected_refs": [{"document_id": "doc-a"}]}],
    )
    run = store.create_run(
        evaluation_set=evaluation_set,
        targets=[
            {
                "target_id": "candidate",
                "version_id": "candidate",
                "retrieval": {"top_k": 2},
            }
        ],
        baseline_version_id=None,
        ks=[1, 5],
        gate_policy=store.get_gate_policy("kb-a"),
    )

    assert run["execution_manifest"]["experimental_variables"] == [
        {
            "target_id": "candidate",
            "retrieval_override": {"top_k": 2},
        }
    ]


@pytest.mark.asyncio
async def test_failed_provider_receipt_is_sanitized_and_persisted(
    tmp_path: Path,
) -> None:
    class ReceiptError(RuntimeError):
        code = "provider_embedding_timeout"
        receipt = {
            "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
            "routing_mode": "managed_required",
            "status": "failed",
            "call_count": 1,
            "reason_codes": ["provider_embedding_timeout"],
            "secret": "must-not-persist",
            "calls": [
                {
                    "operation": "embedding_vectors",
                    "model_id": "bge-m3",
                    "provider_kind": "openai_compatible",
                    "status": "failed",
                    "dispatched": True,
                    "error_code": "provider_embedding_timeout",
                    "authorization": "must-not-persist",
                }
            ],
        }

    class FailingService(_FakeService):
        async def query_pipeline_version(
            self, version_id: str, question: str, **kwargs: Any
        ) -> dict[str, Any]:
            self.calls.append({"version_id": version_id, "question": question, **kwargs})
            raise ReceiptError("redacted failure")

    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    evaluation_set = store.create_set("kb-a", "failed-receipt")
    evaluation_set = store.add_cases(
        evaluation_set["eval_set_id"],
        expected_revision=1,
        cases=[{"query": "q", "expected_refs": [{"document_id": "doc-a"}]}],
    )
    run = store.create_run(
        evaluation_set=evaluation_set,
        targets=[{"target_id": "candidate", "version_id": "candidate"}],
        baseline_version_id=None,
        ks=[5],
        gate_policy=store.get_gate_policy("kb-a"),
    )
    executor = KnowledgeEvaluationExecutor(FailingService({}), store)
    assert await executor.run_once() is True
    stored = store.get_run(run["run_id"], project_reproducibility=False)
    case_id = evaluation_set["cases"][0]["case_id"]
    case = stored["case_results"]["candidate"][case_id]
    assert case["fallback_reason_codes"] == ["provider_embedding_timeout"]
    assert case["provider_route_receipts"]["calls"][0]["operation"] == "embedding_vectors"
    serialized = json.dumps(case["provider_route_receipts"], sort_keys=True)
    assert "must-not-persist" not in serialized
    assert "authorization" not in serialized


def test_diagnostic_without_target_manifest_is_not_current_and_missing_version_is_orphaned(
    tmp_path: Path,
) -> None:
    current = {"kb_exists": True, "targets": {"candidate": {}}}
    store = KnowledgeEvaluationStore(
        tmp_path / "evaluations.json",
        reproducibility_resolver=lambda _run: deepcopy(current),
    )
    dataset = store.create_set("kb-a", "diagnostic")
    run = store.create_run(
        evaluation_set=dataset,
        targets=[{"target_id": "candidate", "version_id": "candidate"}],
        baseline_version_id=None,
        ks=[5],
        gate_policy=store.get_gate_policy("kb-a"),
    )
    assert run["reproducibility_status"] == "unreproducible"
    current["targets"] = {}
    assert store.get_run(run["run_id"])["reproducibility_status"] == "orphaned"
    assert store.list_runs("kb-a")[0]["reproducibility_status"] == "orphaned"


def test_projection_recomputes_published_gold_checksum(tmp_path: Path) -> None:
    store = KnowledgeEvaluationStore(
        tmp_path / "evaluations.json",
        reproducibility_resolver=lambda _run: {"kb_exists": True, "targets": {}},
    )
    dataset = store.create_set("kb-a", "checksum")
    version = {
        **dataset,
        "version_id": "gold-v2",
        "version": 1,
        "source_revision": 1,
        "benchmark_contract_version": "rag-gold-v2",
    }
    version["checksum"] = _published_gold_checksum(version)
    run = store.create_run(
        evaluation_set=dataset,
        evaluation_set_version=version,
        targets=[{"target_id": "candidate", "version_id": "candidate"}],
        baseline_version_id=None,
        ks=[5],
        gate_policy=store.get_gate_policy("kb-a"),
    )
    persisted = json.loads(store.path.read_text(encoding="utf-8"))
    persisted["versions"][version["version_id"]] = {
        **version,
        "cases": [{"case_id": "tampered", "query": "changed after publication"}],
    }
    store.path.write_text(json.dumps(persisted), encoding="utf-8")
    projected = store.get_run(run["run_id"])
    assert "published_gold_checksum_invalid" in projected["reproducibility_reasons"]


def _fulltext_integrity_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    targets, admitted = _admission(monkeypatch, mode="fulltext")
    case = {
        "status": "completed",
        "execution_mode": "local_non_model",
        "provider_route_receipts": None,
        "fallback_reason_codes": [],
        "source_count": 0,
        "ranking": [],
        "retrieval_receipt": {
            "mode": "fulltext",
            "top_k": 3,
            "embedding_provider": "none",
            "embedding_dimension": 0,
            "rerank_applied": False,
            "rerank_input_count": 0,
            "rerank_output_count": 0,
            "promotion_eligible": True,
            "promotion_ineligibility_reasons": [],
        },
    }
    return _integrity_run(targets, admitted, case)


def test_formal_no_candidates_skips_rerank_without_being_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _fulltext_integrity_run(monkeypatch)
    for target in run["execution_manifest"]["targets"]:
        target["rerank"] = {"enabled": True, "provider": "api", "model": "reranker", "top_n": 2}
    run["execution_manifest"] = seal_execution_manifest(run["execution_manifest"])
    assert qualify_formal_execution_integrity(run)["qualified"] is True


def test_formal_rejects_top_k_drift_and_empty_target_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _fulltext_integrity_run(monkeypatch)
    assert qualify_formal_execution_integrity(run)["qualified"] is True
    run["case_results"]["candidate"]["case-a"]["retrieval_receipt"]["top_k"] = 10
    assert qualify_formal_execution_integrity(run)["qualified"] is False
    run["targets"] = []
    assert qualify_formal_execution_integrity(run)["qualified"] is False
    missing_results = _fulltext_integrity_run(monkeypatch)
    missing_results["target_results"] = []
    assert qualify_formal_execution_integrity(missing_results)["qualified"] is False


def test_formal_rejects_unconfigured_extra_rerank_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets, admitted = _admission(monkeypatch)
    case = {
        "status": "completed",
        "execution_mode": "managed",
        "provider_route_receipts": {
            "contract_version": "modelmirror-provider-rag-route-receipts-v1",
            "routing_mode": "composed",
            "status": "passed",
            "call_count": 2,
            "reason_codes": [],
            "calls": [
                {
                    "operation": "embedding_vectors",
                    "model_id": "bge-m3",
                    "provider_kind": "openai_compatible",
                    "actual_model": "bge-m3",
                    "status": "passed",
                    "dispatched": True,
                },
                {
                    "operation": "rerank_documents",
                    "model_id": "unexpected-reranker",
                    "provider_kind": "openai_compatible",
                    "actual_model": "unexpected-reranker",
                    "access_mode": "dedicated",
                    "status": "passed",
                    "dispatched": True,
                },
            ],
        },
        "fallback_reason_codes": [],
        "retrieval_receipt": {
            "mode": "vector",
            "top_k": 3,
            "embedding_provider": "openai_compatible",
            "embedding_model": "bge-m3",
            "embedding_dimension": 1024,
            "embedding_space_fingerprint": "e" * 64,
            "rerank_applied": True,
            "promotion_eligible": True,
            "promotion_ineligibility_reasons": [],
        },
    }
    integrity = qualify_formal_execution_integrity(
        _integrity_run(targets, admitted, case)
    )
    assert integrity["qualified"] is False
    assert any(
        reason.startswith("unexpected_rerank_execution:")
        for reason in integrity["reason_codes"]
    )


def test_formal_accepts_actual_managed_rerank_protocol_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _fulltext_integrity_run(monkeypatch)
    for target in run["execution_manifest"]["targets"]:
        target["rerank"] = {"enabled": True, "provider": "api", "model": "reranker", "top_n": 2}
    run["execution_manifest"] = seal_execution_manifest(run["execution_manifest"])
    for case_map in run["case_results"].values():
        case = case_map["case-a"]
        case["execution_mode"] = "managed"
        case["provider_route_receipts"] = {
            "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
            "routing_mode": "managed_required",
            "status": "passed",
            "call_count": 1,
            "calls": [{"operation": "rerank_documents", "model_id": "reranker", "provider_kind": "openai_compatible", "actual_model": "reranker", "access_mode": "dedicated", "dispatched": True, "status": "passed"}],
            "reason_codes": [],
        }
        case["retrieval_receipt"].update({
            "rerank_applied": True,
            "rerank_input_count": 2,
            "rerank_output_count": 2,
            "rerank_provider_used": "managed",
            "rerank_model_used": "reranker",
            "rerank_provider_target_used": "managed_rerank_dedicated",
        })
    assert qualify_formal_execution_integrity(run)["qualified"] is True
    run["case_results"]["candidate"]["case-a"]["provider_route_receipts"]["calls"][0]["access_mode"] = "llm_json"
    assert qualify_formal_execution_integrity(run)["qualified"] is False


@pytest.mark.parametrize(
    "source_name",
    [
        "chat_control.py",
        "egress.py",
        "multimodal_control.py",
        "provider_catalog.py",
        "provider_chat.py",
        "repository.py",
        "schemas.py",
        "service.py",
    ],
)
def test_runtime_code_fingerprint_tracks_model_router_execution_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    source_name: str,
) -> None:
    dependency_path = (
        Path(__file__).resolve().parents[1] / "model_router" / source_name
    ).resolve()
    original_read_bytes = Path.read_bytes
    dependency_source = [b"dependency-variant-a"]

    def controlled_read_bytes(path: Path) -> bytes:
        if path.resolve() == dependency_path:
            return dependency_source[0]
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", controlled_read_bytes)
    evaluation_runtime_code_fingerprint.cache_clear()
    first = evaluation_runtime_code_fingerprint()
    dependency_source[0] = b"dependency-variant-b"
    evaluation_runtime_code_fingerprint.cache_clear()
    second = evaluation_runtime_code_fingerprint()
    evaluation_runtime_code_fingerprint.cache_clear()

    assert first != second


def test_formal_rejects_duplicate_internal_target_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _fulltext_integrity_run(monkeypatch)
    run["targets"][1]["target_id"] = run["targets"][0]["target_id"]

    assert "formal_target_ledger_invalid" in formal_execution_preflight_reasons(run)
    assert qualify_formal_execution_integrity(run)["qualified"] is False


def test_formal_rejects_boolean_provider_call_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _fulltext_integrity_run(monkeypatch)
    for target in run["execution_manifest"]["targets"]:
        target["rerank"] = {
            "enabled": True,
            "provider": "api",
            "model": "reranker",
            "top_n": 2,
        }
    run["execution_manifest"] = seal_execution_manifest(
        run["execution_manifest"]
    )
    for case_map in run["case_results"].values():
        case = case_map["case-a"]
        case["execution_mode"] = "managed"
        case["provider_route_receipts"] = {
            "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
            "routing_mode": "managed_required",
            "status": "passed",
            "call_count": True,
            "calls": [
                {
                    "operation": "rerank_documents",
                    "model_id": "reranker",
                    "provider_kind": "openai_compatible",
                    "actual_model": "reranker",
                    "access_mode": "dedicated",
                    "dispatched": True,
                    "status": "passed",
                }
            ],
            "reason_codes": [],
        }
        case["retrieval_receipt"].update(
            {
                "rerank_applied": True,
                "rerank_input_count": 2,
                "rerank_output_count": 2,
                "rerank_provider_used": "managed",
                "rerank_model_used": "reranker",
                "rerank_provider_target_used": "managed_rerank_dedicated",
            }
        )

    integrity = qualify_formal_execution_integrity(run)
    assert integrity["qualified"] is False
    assert any(
        reason.startswith("provider_receipt_invalid:")
        for reason in integrity["reason_codes"]
    )


def test_fulltext_formal_rejects_fabricated_zero_call_provider_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _fulltext_integrity_run(monkeypatch)
    for case_map in run["case_results"].values():
        case_map["case-a"]["provider_route_receipts"] = {
            "contract_version": "fabricated-contract",
            "routing_mode": "managed_required",
            "status": "passed",
            "call_count": 0,
            "calls": [],
            "reason_codes": [],
        }

    integrity = qualify_formal_execution_integrity(run)
    assert integrity["qualified"] is False
    assert any(
        reason.startswith("provider_receipt_invalid:")
        for reason in integrity["reason_codes"]
    )


@pytest.mark.parametrize("fabricated_receipt", [{}, "invalid-receipt"])
def test_fulltext_formal_rejects_any_present_provider_receipt(
    monkeypatch: pytest.MonkeyPatch,
    fabricated_receipt: object,
) -> None:
    run = _fulltext_integrity_run(monkeypatch)
    for case_map in run["case_results"].values():
        case_map["case-a"]["provider_route_receipts"] = fabricated_receipt

    integrity = qualify_formal_execution_integrity(run)
    assert integrity["qualified"] is False
    assert any(
        reason.startswith("provider_receipt_invalid:")
        for reason in integrity["reason_codes"]
    )


def test_managed_formal_requires_reason_code_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets, admitted = _admission(monkeypatch)
    case = {
        "status": "completed",
        "execution_mode": "managed",
        "provider_route_receipts": {
            "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
            "routing_mode": "managed_required",
            "status": "passed",
            "call_count": 1,
            "reason_codes": None,
            "calls": [
                {
                    "operation": "embedding_vectors",
                    "model_id": "bge-m3",
                    "provider_kind": "openai_compatible",
                    "actual_model": "bge-m3",
                    "status": "passed",
                    "dispatched": True,
                }
            ],
        },
        "fallback_reason_codes": [],
        "retrieval_receipt": {
            "mode": "vector",
            "top_k": 3,
            "embedding_provider": "openai_compatible",
            "embedding_model": "bge-m3",
            "embedding_dimension": 1024,
            "embedding_space_fingerprint": "e" * 64,
            "rerank_applied": False,
            "promotion_eligible": True,
            "promotion_ineligibility_reasons": [],
        },
    }

    integrity = qualify_formal_execution_integrity(
        _integrity_run(targets, admitted, case)
    )
    assert integrity["qualified"] is False
    assert any(
        reason.startswith("provider_receipt_invalid:")
        for reason in integrity["reason_codes"]
    )
