from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from server.evaluations import api as evaluation_api
from server.evaluations.executor import XpertEvaluationExecutor
from server.evaluations.metrics import (
    aggregate_evaluation_report,
    evaluate_case_metrics,
)
from server.evaluations.service import XpertEvaluationService
from server.evaluations.store import (
    EvaluationConflictError,
    XpertEvaluationStore,
)
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.xperts import XpertStore


class _EmptyStore:
    def get_version(self, *_args: Any, **_kwargs: Any) -> Any:
        raise KeyError("resource not found")


class _RagStub:
    def get_active_pipeline_version(self, kb_id: str) -> dict[str, Any] | None:
        if kb_id == "kb-ready":
            return {"version_id": "pipeline-v2"}
        return None


def _dataset_with_case(store: XpertEvaluationStore) -> tuple[dict[str, Any], dict[str, Any]]:
    dataset = store.create_dataset("Regression")
    dataset = store.put_cases(
        dataset["dataset_id"],
        revision=dataset["revision"],
        cases=[
            {
                "case_id": "case-one",
                "name": "Simple answer",
                "message": "Say hello",
                "expected": {
                    "contains": ["hello"],
                    "json_schema": {
                        "type": "object",
                        "required": ["answer"],
                        "properties": {"answer": {"type": "string"}},
                    },
                },
            }
        ],
    )
    version = store.publish_dataset(
        dataset["dataset_id"],
        revision=dataset["revision"],
    )
    return dataset, version


def _target(target_id: str = "candidate") -> dict[str, Any]:
    return {
        "target_id": target_id,
        "label": target_id.title(),
        "source": {
            "kind": "xpert_version",
            "xpert_id": "xpert-one",
            "version": 1,
        },
        "xpert": {
            "id": "xpert-one",
            "slug": "xpert-one",
            "name": "Xpert One",
            "description": "",
        },
        "workflow": {"id": "wf", "title": "Workflow", "nodes": [], "edges": []},
        "input_variable": "user_input",
        "history_variable": "conversation_history",
        "output_variable": "agent_output",
        "agent_config": {"max_concurrency": 2, "recursion_limit": 1000},
        "features": {},
        "prompt_profiles": [],
        "checksum": "checksum",
        "resources": {},
        "warnings": [],
    }


def test_dataset_versions_are_immutable_and_survive_reload(tmp_path: Path) -> None:
    store = XpertEvaluationStore(tmp_path)
    dataset, first = _dataset_with_case(store)
    changed = store.put_cases(
        dataset["dataset_id"],
        revision=dataset["revision"] + 1,
        cases=[
            {
                "case_id": "case-two",
                "message": "A later draft case",
                "expected": {"exact_answer": "later"},
            }
        ],
    )

    assert first["case_count"] == 1
    assert store.get_dataset_version(dataset["dataset_id"], 1)["cases"][0][
        "case_id"
    ] == "case-one"
    assert len(changed["cases"]) == 2
    restored = XpertEvaluationStore(tmp_path)
    assert restored.get_dataset_version(dataset["dataset_id"], 1)["checksum"] == first[
        "checksum"
    ]

    with pytest.raises(EvaluationConflictError):
        restored.put_cases(
            dataset["dataset_id"],
            revision=dataset["revision"],
            cases=[{"message": "stale"}],
        )


@pytest.mark.asyncio
async def test_metrics_and_baseline_comparison_are_stable() -> None:
    async def judge(
        _model_id: str,
        _message: str,
        _output: str,
        _rubric: str,
    ) -> dict[str, Any]:
        return {"score": 0.75, "passed": True, "reason": "Useful and concise."}

    result = await evaluate_case_metrics(
        case={
            "message": "Return JSON",
            "expected": {
                "contains": ["hello"],
                "json_schema": {
                    "type": "object",
                    "required": ["answer"],
                    "properties": {"answer": {"type": "string"}},
                },
                "citation_ids": ["cite-1"],
                "rubric": "The answer is concise.",
            },
        },
        output='{"answer":"hello"}',
        citations={"citation_ids": ["cite-1"]},
        judge=judge,
        judge_model_id="judge-model",
    )
    assert result["metric_count"] == 4
    assert result["score"] == pytest.approx(0.9375)

    report = aggregate_evaluation_report(
        [
            {
                "target_id": "baseline",
                "target_label": "Baseline",
                "case_id": "case-one",
                "repetition": 1,
                "status": "completed",
                "score": 0.5,
                "metrics": [],
                "latency_ms": 100,
                "usage": {},
            },
            {
                "target_id": "candidate",
                "target_label": "Candidate",
                "case_id": "case-one",
                "repetition": 1,
                "status": "completed",
                "score": 0.9,
                "metrics": [],
                "latency_ms": 80,
                "usage": {},
            },
        ],
        baseline_target_id="baseline",
    )
    assert report["comparisons"] == [
        {
            "target_id": "candidate",
            "baseline_target_id": "baseline",
            "score_delta": 0.4,
            "wins": 1,
            "ties": 0,
            "losses": 0,
        }
    ]


def test_safe_preflight_pins_knowledge_and_blocks_waiting_nodes(tmp_path: Path) -> None:
    xpert_store = XpertStore(tmp_path / "xperts")
    service = XpertEvaluationService(
        XpertEvaluationStore(tmp_path / "evaluations"),
        xpert_store=xpert_store,
        proposal_store=_EmptyStore(),
        prompt_preflight=lambda _xpert: None,
        toolset_store=_EmptyStore(),
        plugin_store=_EmptyStore(),
        rag_service=_RagStub(),
        context_store=_EmptyStore(),
    )
    workflow = NativeWorkflowDefinition.model_validate(
        {
            "id": "safe-check",
            "title": "Safe check",
            "nodes": [
                {
                    "id": "kb",
                    "type": "knowledge_base",
                    "data": {
                        "kind": "knowledge_base",
                        "knowledgeBaseId": "kb-ready",
                    },
                },
                {
                    "id": "human",
                    "type": "human_intervention",
                    "data": {"kind": "human_intervention"},
                },
            ],
            "edges": [],
        }
    )

    issues, _warnings, resources = service._safe_preflight(
        workflow,
        recursion_path=("root",),
    )
    assert any(item["code"] == "evaluation_unsafe_node" for item in issues)
    assert workflow.nodes[0].data["evaluationPinnedVersionId"] == "pipeline-v2"
    assert resources["knowledge_versions"] == [
        {"knowledge_base_id": "kb-ready", "version_id": "pipeline-v2"}
    ]


@pytest.mark.asyncio
async def test_executor_persists_results_and_resumes_unfinished_items(
    tmp_path: Path,
) -> None:
    store = XpertEvaluationStore(tmp_path)
    _dataset, version = _dataset_with_case(store)
    run = store.create_run(
        dataset_version=version,
        cases=version["cases"],
        baseline=None,
        candidates=[_target()],
        config={
            "budget": {
                "repetitions": 1,
                "max_concurrency": 2,
                "case_timeout_seconds": 30,
                "max_output_chars": 20_000,
            }
        },
        warnings=[],
    )

    async def runner(
        _target_payload: dict[str, Any],
        _case: dict[str, Any],
        _config: dict[str, Any],
        _parent_run_id: str | None,
    ) -> dict[str, Any]:
        return {
            "output": '{"answer":"hello"}',
            "citations": {},
            "usage": {"model_calls": 1, "tool_calls": 0, "estimated_tokens": 10},
        }

    claimed = store.claim_next_run()
    assert claimed is not None
    executor = XpertEvaluationExecutor(store, target_runner=runner)
    await executor._execute_run(claimed)
    completed = store.require_run(run["run_id"])
    assert completed["status"] == "completed"
    assert completed["items"][0]["status"] == "completed"
    assert completed["report"]["targets"][0]["model_calls"] == 1

    completed["status"] = "running"
    completed["items"][0]["status"] = "running"
    store._data["runs"][run["run_id"]] = completed
    store._save_unlocked()
    assert XpertEvaluationStore(tmp_path).recover_runs() == 1
    recovered = XpertEvaluationStore(tmp_path).require_run(run["run_id"])
    assert recovered["status"] == "queued"
    assert recovered["items"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_evaluation_api_exposes_safe_dataset_and_run_contract(
    tmp_path: Path,
) -> None:
    async def runner(
        _target_payload: dict[str, Any],
        _case: dict[str, Any],
        _config: dict[str, Any],
        _parent_run_id: str | None,
    ) -> dict[str, Any]:
        return {"output": "hello", "citations": {}, "usage": {}}

    evaluation_api._store = XpertEvaluationStore(tmp_path)
    evaluation_api._executor = XpertEvaluationExecutor(
        evaluation_api._store,
        target_runner=runner,
    )
    app = FastAPI()
    app.include_router(evaluation_api.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        capabilities = await client.get("/api/xpert-evaluations/capabilities")
        assert capabilities.status_code == 200
        assert capabilities.json()["safe_mode"] == "read_only_fail_closed"

        created = await client.post(
            "/api/xpert-evaluations/datasets",
            json={"name": "API dataset", "description": "Safe contract"},
        )
        assert created.status_code == 200
        dataset = created.json()
        cases = await client.post(
            f"/api/xpert-evaluations/datasets/{dataset['dataset_id']}/cases",
            json={
                "revision": dataset["revision"],
                "cases": [
                    {
                        "message": "Hello",
                        "expected": {"contains": ["hello"]},
                    }
                ],
            },
        )
        assert cases.status_code == 200
        published = await client.post(
            f"/api/xpert-evaluations/datasets/{dataset['dataset_id']}/publish",
            json={"revision": cases.json()["revision"], "release_notes": "v1"},
        )
        assert published.status_code == 200
        serialized = json.dumps(published.json(), ensure_ascii=False)
        assert "OPENROUTER_API_KEY" not in serialized
        assert "stored_path" not in serialized
