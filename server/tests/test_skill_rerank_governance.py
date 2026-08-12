from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from server.skills import rerank_governance as rerank_governance_module
from server.skills.api import (
    router as skills_router,
    set_skill_rerank_governance_service_for_tests,
    set_skill_semantic_rerank_service_for_tests,
)
from server.skills.rerank_governance import (
    MAX_FEEDBACK_RECORDS,
    SkillRerankGovernanceConflict,
    SkillRerankGovernanceService,
    SkillRerankGovernanceStore,
    SkillRerankGovernanceUnavailable,
)
from server.skills.rerank_evaluation import SkillRerankEvaluator
from server.skills.semantic_rerank import SkillRerankRequest, SkillSearchIndexV1
from server.skills.semantic_rerank_service import (
    SkillSemanticRerankConfig,
    SkillSemanticRerankService,
)


def _configured_service(
    tmp_path: Path,
    handler,
) -> tuple[SkillRerankGovernanceService, SkillSemanticRerankService]:
    search_index = SkillSearchIndexV1()
    reranker = SkillSemanticRerankService(
        search_index=search_index,
        config=SkillSemanticRerankConfig(
            provider="api",
            router_mode="shadow",
            api_url="https://rerank.example/v1/rerank",
            api_model="synthetic-rerank-v1",
        ),
        transport=httpx.MockTransport(handler),
    )
    governance = SkillRerankGovernanceService(
        rerank_service=reranker,
        store=SkillRerankGovernanceStore(tmp_path / "governance.json"),
    )
    governance.configure_reranker()
    return governance, reranker


def _identity_handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    return httpx.Response(
        200,
        json={
            "model": "synthetic-rerank-v1",
            "results": [
                {"index": index, "score": 1 - index / max(1, len(payload["documents"]))}
                for index in range(len(payload["documents"]))
            ],
        },
    )


@pytest.mark.asyncio
async def test_feedback_is_explicit_revision_bound_and_secret_safe(tmp_path: Path) -> None:
    governance, reranker = _configured_service(tmp_path, _identity_handler)
    lexical = await reranker.search(
        SkillRerankRequest(
            query="PDF documents", scope="market", limit=6, semantic=True
        )
    )
    candidate = lexical.final_results[0]
    receipt = lexical.receipt.serialize()
    revision = governance.store.summary()["revision"]
    record = governance.record_feedback(
        expected_revision=revision,
        query="PDF documents",
        candidate_id=candidate["candidateId"],
        candidate_fingerprint=candidate["candidateFingerprint"],
        judgment="relevant",
        receipt=receipt,
    )
    assert record["query"] == "pdf documents"
    assert governance.store.summary()["feedbackCount"] == 1
    with pytest.raises(SkillRerankGovernanceConflict):
        governance.record_feedback(
            expected_revision=revision,
            query="PDF documents",
            candidate_id=candidate["candidateId"],
            candidate_fingerprint=candidate["candidateFingerprint"],
            judgment="relevant",
            receipt=receipt,
        )

    current = governance.store.summary()["revision"]
    with pytest.raises(Exception) as caught:
        governance.record_feedback(
            expected_revision=current,
            query="DIFY_" + 'API_KEY="' + "dify-live-secret-value" + '"',
            candidate_id=candidate["candidateId"],
            candidate_fingerprint=candidate["candidateFingerprint"],
            judgment="not_relevant",
            receipt=receipt,
        )
    assert getattr(caught.value, "code", "") == "skill_rerank_sensitive_input"
    assert "dify-live" not in (tmp_path / "governance.json").read_text(encoding="utf-8")


def test_feedback_retention_is_bounded_by_age_and_count(tmp_path: Path) -> None:
    store = SkillRerankGovernanceStore(tmp_path / "governance.json")
    now = 40 * 24 * 60 * 60
    state = store._empty_state()
    state["feedback"] = [
        {"feedbackId": "expired", "createdAt": 1},
        *(
            {"feedbackId": f"recent_{index}", "createdAt": now - index}
            for index in range(MAX_FEEDBACK_RECORDS + 5)
        ),
    ]
    original_now = rerank_governance_module._now
    rerank_governance_module._now = lambda: now
    try:
        clean = store._clean_copy(state)
    finally:
        rerank_governance_module._now = original_now
    assert len(clean["feedback"]) == MAX_FEEDBACK_RECORDS
    assert all(row["feedbackId"] != "expired" for row in clean["feedback"])


@pytest.mark.asyncio
async def test_lexical_fallback_cannot_be_recorded_as_semantic_feedback(
    tmp_path: Path,
) -> None:
    governance = SkillRerankGovernanceService(
        rerank_service=SkillSemanticRerankService(
            search_index=SkillSearchIndexV1(),
            config=SkillSemanticRerankConfig(provider="none", router_mode="shadow"),
        ),
        store=SkillRerankGovernanceStore(tmp_path / "governance.json"),
    )
    outcome = await governance.search_market(
        SkillRerankRequest(
            query="PDF documents", scope="market", limit=6, semantic=True
        )
    )
    candidate = outcome.final_results[0]
    with pytest.raises(Exception) as caught:
        governance.record_feedback(
            expected_revision=governance.store.summary()["revision"],
            query="PDF documents",
            candidate_id=candidate["candidateId"],
            candidate_fingerprint=candidate["candidateFingerprint"],
            judgment="relevant",
            receipt=outcome.receipt.serialize(),
        )
    assert getattr(caught.value, "code", "") == "skill_rerank_feedback_not_semantic"
    assert governance.store.summary()["feedbackCount"] == 0


def test_top_level_corruption_disables_governance_and_keeps_lexical_fallback(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "governance.json"
    snapshot.write_text("{broken", encoding="utf-8")
    store = SkillRerankGovernanceStore(snapshot)
    reranker = SkillSemanticRerankService(
        config=SkillSemanticRerankConfig(provider="none", router_mode="shadow")
    )
    governance = SkillRerankGovernanceService(rerank_service=reranker, store=store)
    governance.configure_reranker()
    assert governance.status()["governanceAvailable"] is False
    assert governance.effective_router_mode() == "off"
    with pytest.raises(SkillRerankGovernanceUnavailable):
        store.clear_feedback(expected_revision=1)
    assert snapshot.read_text(encoding="utf-8") == "{broken"


@pytest.mark.asyncio
async def test_market_search_falls_back_when_governance_store_is_corrupt(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "governance.json"
    snapshot.write_text("{broken", encoding="utf-8")
    governance, _ = _configured_service(tmp_path, _identity_handler)
    governance.store = SkillRerankGovernanceStore(snapshot)
    governance.configure_reranker()

    outcome = await governance.search_market(
        SkillRerankRequest(
            query="PDF documents", scope="market", limit=6, semantic=True
        )
    )
    assert outcome.status == "lexical_fallback"
    assert outcome.receipt.fallback_reason == "skill_rerank_governance_unavailable"
    assert snapshot.read_text(encoding="utf-8") == "{broken"


@pytest.mark.asyncio
async def test_actual_provider_identity_change_revokes_promotion(tmp_path: Path) -> None:
    governance, reranker = _configured_service(tmp_path, _identity_handler)
    binding = governance._binding()
    evaluation = governance.store.create_evaluation(
        {
            **binding,
            "provider": "api",
            "model": "synthetic-rerank-v1",
            "eligibleForPromotion": True,
            "status": "completed",
        },
        expected_revision=governance.store.summary()["revision"],
    )
    evaluation = governance.store.update_evaluation(
        evaluation["evaluationId"],
        expected_revision=evaluation["revision"],
        changes={"status": "completed", "eligibleForPromotion": True},
    )
    governance.store.promote(
        evaluation=evaluation,
        expected_revision=governance.store.summary()["revision"],
        receipt={
            "provider": "api",
            "model": "synthetic-rerank-v1",
            **binding,
        },
    )
    assert governance.effective_router_mode() == "on"

    def changed_model(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "different-model",
                "results": [
                    {"index": index, "score": 1 - index / len(payload["documents"])}
                    for index in range(len(payload["documents"]))
                ],
            },
        )

    reranker.transport = httpx.MockTransport(changed_model)
    case = next(
        case
        for case in SkillRerankEvaluator(search_index=reranker.search_index).load_cases()[
            "cases"
        ]
        if case["scope"] == "router" and case["kind"] == "positive"
    )
    lexical = reranker.search_index.lexical_search(
        SkillRerankRequest(query=case["query"], scope="router", limit=6)
    )
    outcome = await reranker.rerank_router_results(
        query=case["query"], lexical_results=list(lexical.lexical_results)
    )
    assert outcome.status == "shadow"
    assert "semantic_router_identity_changed" in outcome.warnings
    assert governance.effective_router_mode() == "shadow"
    assert governance.store.summary()["policy"]["promotion"] is None


@pytest.mark.asyncio
async def test_governance_http_contract_uses_current_revision(tmp_path: Path) -> None:
    governance, reranker = _configured_service(tmp_path, _identity_handler)
    set_skill_semantic_rerank_service_for_tests(reranker)
    set_skill_rerank_governance_service_for_tests(governance)
    app = FastAPI()
    app.include_router(skills_router)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            status = await client.get("/api/skills/rerank/policy")
            assert status.status_code == 200
            revision = status.json()["governanceRevision"]
            search = await client.post(
                "/api/skills/search",
                json={"query": "PDF documents", "semantic": True, "limit": 6},
            )
            assert search.status_code == 200
            body = search.json()
            candidate = body["finalResults"][0]
            feedback = await client.post(
                "/api/skills/rerank/feedback",
                json={
                    "expected_revision": revision,
                    "query": "PDF documents",
                    "candidate_id": candidate["candidateId"],
                    "candidate_fingerprint": candidate["candidateFingerprint"],
                    "judgment": "relevant",
                    "receipt": body["receipt"],
                },
            )
            assert feedback.status_code == 200
            stale = await client.delete(
                f"/api/skills/rerank/feedback?expected_revision={revision}"
            )
            assert stale.status_code == 409
            assert stale.json()["detail"]["code"] == "skill_rerank_revision_conflict"
    finally:
        set_skill_rerank_governance_service_for_tests(None)
        set_skill_semantic_rerank_service_for_tests(None)


def test_provider_endpoint_change_invalidates_promotion(tmp_path: Path) -> None:
    governance, reranker = _configured_service(tmp_path, _identity_handler)
    binding = governance._binding()
    evaluation = governance.store.create_evaluation(
        {
            **binding,
            "provider": "api",
            "model": "synthetic-rerank-v1",
            "eligibleForPromotion": True,
            "status": "completed",
        },
        expected_revision=governance.store.summary()["revision"],
    )
    evaluation = governance.store.update_evaluation(
        evaluation["evaluationId"],
        expected_revision=evaluation["revision"],
        changes={"status": "completed", "eligibleForPromotion": True},
    )
    governance.store.promote(
        evaluation=evaluation,
        expected_revision=governance.store.summary()["revision"],
        receipt={"provider": "api", "model": "synthetic-rerank-v1", **binding},
    )
    assert governance.effective_router_mode() == "on"

    reranker.config = SkillSemanticRerankConfig(
        provider="api",
        router_mode="shadow",
        api_url="https://rerank-backup.example/v1/rerank",
        api_model="synthetic-rerank-v1",
    )
    assert governance.effective_router_mode() == "shadow"
    assert "semantic_provider_changed" in governance.status()["policyReasons"]


@pytest.mark.asyncio
async def test_shadow_receipt_never_stores_raw_query(tmp_path: Path) -> None:
    governance, reranker = _configured_service(tmp_path, _identity_handler)
    query = next(
        case["query"]
        for case in SkillRerankEvaluator(search_index=reranker.search_index).load_cases()[
            "cases"
        ]
        if case["scope"] == "router" and case["kind"] == "positive"
    )
    lexical = reranker.search_index.lexical_search(
        SkillRerankRequest(query=query, scope="router", limit=6)
    )
    revision_before = governance.store.summary()["revision"]
    outcome = await reranker.rerank_router_results(
        query=query,
        lexical_results=list(lexical.lexical_results),
    )
    assert outcome.status == "shadow"
    stored = (tmp_path / "governance.json").read_text(encoding="utf-8")
    assert query not in stored
    assert outcome.receipt.query_hash in stored
    assert governance.store.summary()["revision"] == revision_before


@pytest.mark.asyncio
async def test_router_off_does_not_pollute_shadow_statistics(tmp_path: Path) -> None:
    governance, reranker = _configured_service(tmp_path, _identity_handler)
    reranker.config = SkillSemanticRerankConfig(
        provider="api",
        router_mode="off",
        api_url="https://rerank.example/v1/rerank",
        api_model="synthetic-rerank-v1",
    )
    case = next(
        case
        for case in SkillRerankEvaluator(search_index=reranker.search_index).load_cases()[
            "cases"
        ]
        if case["scope"] == "router" and case["kind"] == "positive"
    )
    lexical = reranker.search_index.lexical_search(
        SkillRerankRequest(query=case["query"], scope="router", limit=6)
    )
    outcome = await reranker.rerank_router_results(
        query=case["query"], lexical_results=list(lexical.lexical_results)
    )
    assert outcome.status == "lexical"
    assert governance.store.shadow_summary()["sampleCount"] == 0


@pytest.mark.asyncio
async def test_fixed_gold_evaluation_can_promote_and_rollback(tmp_path: Path) -> None:
    search_index = SkillSearchIndexV1()
    evaluator = SkillRerankEvaluator(search_index=search_index)
    cases = evaluator.load_cases()["cases"]
    relevant_by_query = {
        case["query"]: {
            row["candidateId"]
            for row in case["judgments"]
            if row["relevance"] > 0
        }
        for case in cases
    }
    document_to_candidate = {
        candidate["semanticDocument"]: candidate["candidateId"]
        for candidate in search_index.candidates()
    }

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        relevant = relevant_by_query.get(payload["query"], set())
        ranked = sorted(
            range(len(payload["documents"])),
            key=lambda index: (
                document_to_candidate.get(payload["documents"][index]) not in relevant,
                index,
            ),
        )
        return httpx.Response(
            200,
            json={
                "model": "synthetic-rerank-v1",
                "results": [
                    {"index": index, "score": 1 - rank / max(1, len(ranked))}
                    for rank, index in enumerate(ranked)
                ],
            },
        )

    governance, reranker = _configured_service(tmp_path, handler)
    started = governance.start_evaluation(
        expected_revision=governance.store.summary()["revision"],
        schedule=False,
    )
    finished = await governance.run_evaluation_now(started["evaluationId"])
    assert finished["status"] == "completed"
    assert finished["semantic"]["providerSuccessRate"] == 1.0
    assert finished["semantic"]["policyViolationCount"] == 0
    assert finished["eligibleForPromotion"] is True

    policy = governance.promote(
        evaluation_id=finished["evaluationId"],
        expected_revision=governance.store.summary()["revision"],
        confirmed=True,
    )
    assert policy["mode"] == "on"
    assert governance.effective_router_mode() == "on"

    lexical = reranker.search_index.lexical_search(
        SkillRerankRequest(query=cases[0]["query"], scope="router", limit=6)
    )
    actual = await reranker.rerank_router_results(
        query=cases[0]["query"], lexical_results=list(lexical.lexical_results)
    )
    assert actual.status == "semantic"
    assert actual.receipt.final_ranks == actual.receipt.proposed_ranks

    rolled_back = governance.rollback(
        expected_revision=governance.store.summary()["revision"]
    )
    assert rolled_back["mode"] == "shadow"
    assert governance.effective_router_mode() == "shadow"


def test_promotion_gates_enforce_all_hard_thresholds() -> None:
    baseline = {
        "caseCount": 60,
        "recallAt24": 1.0,
        "mrrAt6": 0.4,
        "nDCGAt6": 0.5,
        "nearMissFalsePositiveRate": 0.1,
    }
    semantic = {
        "recallAt24": 1.0,
        "mrrAt6": 0.43,
        "nDCGAt6": 0.5,
        "nearMissFalsePositiveRate": 0.1,
        "policyViolationCount": 0,
        "providerSuccessRate": 0.95,
        "p95DurationMs": 3_000,
        "providerIdentities": [{"provider": "api", "model": "m"}],
    }
    gates = SkillRerankGovernanceService._promotion_gates(
        baseline=baseline, semantic=semantic, case_count=60
    )
    assert all(gate["passed"] for gate in gates)
    semantic["providerSuccessRate"] = 0.949
    gates = SkillRerankGovernanceService._promotion_gates(
        baseline=baseline, semantic=semantic, case_count=60
    )
    assert next(gate for gate in gates if gate["code"] == "provider_success_rate")[
        "passed"
    ] is False
