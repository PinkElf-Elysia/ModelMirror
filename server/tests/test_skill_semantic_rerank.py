from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from server.skills.finder import MAX_RECALL_RESULTS, MAX_RESULTS, SkillFinder
from server.skills.rerank_evaluation import SkillRerankEvaluator
from server.skills.semantic_rerank import (
    MAX_SEMANTIC_DOCUMENT_CHARACTERS,
    SkillRerankRequest,
    SkillSearchIndexError,
    SkillSearchIndexV1,
)


ROOT = Path(__file__).resolve().parents[2]
SEARCH_INDEX_PATH = ROOT / "server" / "skills" / "data" / "skill_search_index.json"
RUNTIME_INDEX_PATH = ROOT / "server" / "skills" / "data" / "skill_runtime_index.json"
TRUST_INDEX_PATH = ROOT / "server" / "skills" / "data" / "skill_trust_index.json"
CLIENT_SUMMARY_PATH = ROOT / "client" / "src" / "data" / "skillSearchIndex.generated.json"
EVALUATION_PATH = ROOT / "server" / "skills" / "data" / "skill_rerank_eval_v1.json"


def test_search_index_covers_market_and_binds_runtime_trust_and_client_summary() -> None:
    index = SkillSearchIndexV1()
    payload = index._load()
    candidates = index.candidates()
    runtime = SkillFinder(index_path=RUNTIME_INDEX_PATH)._load_index()

    assert len(candidates) == 4_735
    assert len(runtime["candidates"]) == 4_333
    assert len(index.candidates(scope="router")) < len(runtime["candidates"])
    assert {candidate["targetType"] for candidate in candidates} == {"project", "member"}
    assert {candidate["kind"] for candidate in candidates} == {"skill", "skillset"}
    assert {candidate["installStatus"] for candidate in candidates} == {
        "ready",
        "pending",
        "reference",
    }
    assert payload["directoryFingerprint"] == runtime["catalogFingerprint"]
    assert sum(bool(candidate["runtimeCandidateFingerprint"]) for candidate in candidates) == len(
        runtime["candidates"]
    )
    assert all(
        0 < len(candidate["semanticDocument"]) <= MAX_SEMANTIC_DOCUMENT_CHARACTERS
        for candidate in candidates
    )
    forbidden_keys = {"files", "skillMarkdown", "installedSkillId", "trust", "receipt"}
    assert all(not (forbidden_keys & set(candidate)) for candidate in candidates)
    assert all(
        not any(
            marker in candidate["semanticDocument"]
            for marker in ("trustFingerprint", "receiptId", "packageDigest")
        )
        for candidate in candidates
    )


def test_search_index_fails_closed_on_content_or_projection_drift(tmp_path: Path) -> None:
    search = json.loads(SEARCH_INDEX_PATH.read_text(encoding="utf-8"))
    search["candidates"][0]["name"] = "tampered"
    tampered_search = tmp_path / "search.json"
    tampered_search.write_text(json.dumps(search, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SkillSearchIndexError):
        SkillSearchIndexV1(index_path=tampered_search).candidates()

    summary = json.loads(CLIENT_SUMMARY_PATH.read_text(encoding="utf-8"))
    summary["candidateCount"] -= 1
    tampered_summary = tmp_path / "summary.json"
    tampered_summary.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SkillSearchIndexError):
        SkillSearchIndexV1(client_summary_path=tampered_summary).candidates()

    stale = json.loads(SEARCH_INDEX_PATH.read_text(encoding="utf-8"))
    stale["directoryFingerprint"] = "0" * 64
    stale["fingerprint"] = "0" * 64
    stale_path = tmp_path / "stale.json"
    stale_path.write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SkillSearchIndexError):
        SkillSearchIndexV1(index_path=stale_path).candidates()


def test_lexical_contract_keeps_24_recall_candidates_and_six_public_results() -> None:
    index = SkillSearchIndexV1()
    outcome = index.lexical_search(
        SkillRerankRequest(query="PDF document processing", semantic=True)
    )

    assert len(outcome.lexical_results) == MAX_RECALL_RESULTS
    assert len(outcome.final_results) == MAX_RESULTS
    assert outcome.status == "lexical_fallback"
    assert outcome.warnings == ("semantic_rerank_not_configured",)
    assert outcome.receipt.provider == "none"
    assert outcome.receipt.model is None
    assert outcome.receipt.fallback_reason == "provider_disabled"
    assert len(outcome.receipt.query_hash) == 64
    assert "PDF document processing" not in json.dumps(
        outcome.receipt.serialize(), ensure_ascii=False
    )
    assert outcome.receipt.lexical_ranks[:6] == outcome.receipt.final_ranks
    assert all("semanticScore" not in item for item in outcome.final_results)

    with pytest.raises(ValueError):
        SkillRerankRequest(query="PDF", limit=7)
    with pytest.raises(ValueError):
        SkillRerankRequest(query="x" * 501)


def test_market_and_router_share_lexical_order_without_policy_bypass() -> None:
    index = SkillSearchIndexV1()
    market = index.lexical_search(SkillRerankRequest(query="PDF", scope="market"))
    router = index.lexical_search(SkillRerankRequest(query="PDF", scope="router"))
    router_candidates = {
        candidate["candidateId"]: candidate for candidate in index.candidates(scope="router")
    }

    assert market.final_results[0]["candidateId"] == "catalog:project:anthropic-pdf-skill"
    assert router.final_results[0]["candidateId"] == "catalog:project:anthropic-pdf-skill"
    assert all(item["candidateId"] in router_candidates for item in router.lexical_results)
    assert all(
        router_candidates[item["candidateId"]]["runtimeCandidateFingerprint"]
        for item in router.lexical_results
    )


def test_python_and_typescript_keep_the_same_market_recall_order() -> None:
    queries = [
        "提取 PDF 合同",
        "分析 Excel 销售表格",
        "为 React 网页编写 Playwright 自动化测试",
        "audit postgres database security",
        "制定 SEO 营销增长计划",
    ]
    script = r"""
      import fs from 'node:fs';
      import { findSkillsForNeed } from './client/src/data/skillNeedMatcher.ts';
      const index = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
      const queries = JSON.parse(process.argv[2]);
      const candidates = index.candidates.map((candidate) => ({
        id: candidate.candidateId,
        name: candidate.name,
        category: candidate.category,
        kind: candidate.kind,
        description: candidate.description,
        sourceDescription: candidate.sourceDescription,
        searchDescription: candidate.searchDescription,
        tags: candidate.tags,
        includedSkills: candidate.includedSkills,
        installStatus: candidate.installStatus,
        publisher: candidate.publisher,
        sourceGroup: candidate.sourceGroup,
        pathTerms: candidate.pathTerms,
        parentNames: candidate.parentNames,
        deprecated: candidate.deprecated,
        stableNameOrder: candidate.stableNameOrder,
      }));
      console.log(JSON.stringify(queries.map((query) =>
        findSkillsForNeed(query, candidates, 24).map((match) => match.project.id)
      )));
    """
    completed = subprocess.run(
        [
            "node",
            "--import",
            (ROOT / "scripts" / "typescript-module-register.mjs").as_uri(),
            "--input-type=module",
            "-e",
            script,
            str(SEARCH_INDEX_PATH),
            json.dumps(queries, ensure_ascii=False),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    typescript_results = json.loads(completed.stdout)
    python_results = [
        [
            item["candidateId"]
            for item in SkillSearchIndexV1()
            .lexical_search(SkillRerankRequest(query=query))
            .lexical_results
        ]
        for query in queries
    ]
    assert python_results == typescript_results


def test_generated_search_index_is_reproducible() -> None:
    script = r"""
      import fs from 'node:fs';
      import { buildSkillRuntimeIndex } from './scripts/skill-runtime-index.mjs';
      import { buildSkillSearchIndex } from './scripts/skill-search-index.mjs';
      import { loadSkillNeedCandidates } from './client/src/data/skillNeedCandidates.ts';
      import { loadSkillSetMemberIndex } from './client/src/data/skillSetMembers.ts';
      const [candidates, memberIndex] = await Promise.all([
        loadSkillNeedCandidates(), loadSkillSetMemberIndex()
      ]);
      const source = { candidates, memberIndexFingerprint: memberIndex.fingerprint };
      const trustIndex = JSON.parse(fs.readFileSync('server/skills/data/skill_trust_index.json', 'utf8'));
      const runtimeIndex = buildSkillRuntimeIndex({ ...source, trustIndex });
      const searchIndex = buildSkillSearchIndex({ ...source, runtimeIndex, trustIndex });
      console.log(JSON.stringify({ runtime: runtimeIndex.fingerprint, search: searchIndex.fingerprint }));
    """
    completed = subprocess.run(
        [
            "node",
            "--import",
            (ROOT / "scripts" / "typescript-module-register.mjs").as_uri(),
            "--input-type=module",
            "-e",
            script,
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    actual = json.loads(completed.stdout)
    expected = json.loads(SEARCH_INDEX_PATH.read_text(encoding="utf-8"))
    assert actual == {
        "runtime": expected["runtimeIndexFingerprint"],
        "search": expected["fingerprint"],
    }


def test_fixed_evaluation_set_is_versioned_complete_and_auditable(tmp_path: Path) -> None:
    evaluator = SkillRerankEvaluator(evaluation_path=EVALUATION_PATH)
    payload = evaluator.load_cases()
    report = evaluator.evaluate()

    assert len(payload["cases"]) == 71
    assert report.case_count == 71
    assert report.positive_case_count == 50
    assert report.near_miss_case_count == 21
    assert report.search_index_fingerprint == SkillSearchIndexV1().fingerprint
    assert report.recall_at_24 >= 0.9
    assert 0 <= report.mrr_at_6 <= 1
    assert 0 <= report.ndcg_at_6 <= 1
    assert 0 <= report.top_1 <= 1
    assert 0 <= report.near_miss_false_positive_rate <= 1
    assert report.policy_violation_count == 0

    stale = json.loads(EVALUATION_PATH.read_text(encoding="utf-8"))
    stale["searchIndexFingerprint"] = "0" * 64
    target = tmp_path / "skill_rerank_eval_v1.stale-test.json"
    target.write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SkillSearchIndexError):
        SkillRerankEvaluator(evaluation_path=target).load_cases()
