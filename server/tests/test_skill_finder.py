from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from server.skills.finder import (
    SkillFinder,
    SkillFinderError,
    SkillRuntimeIndexError,
)
from server.skills.skill_manager import InstalledSkill


ROOT = Path(__file__).resolve().parents[2]
INDEX_PATH = ROOT / "server" / "skills" / "data" / "skill_runtime_index.json"


class StubSkillManager:
    def __init__(self, skills: list[InstalledSkill] | None = None) -> None:
        self.skills = list(skills or [])

    def list_installed_skills(self) -> list[InstalledSkill]:
        return list(self.skills)


def installed_skill(
    *,
    skill_id: str,
    name: str,
    description: str,
    repo_url: str,
    sub_path: str,
    source_ref: str | None,
) -> InstalledSkill:
    return InstalledSkill(
        skill_id=skill_id,
        name=name,
        description=description,
        repo_url=repo_url,
        sub_path=sub_path,
        source_ref=source_ref,
        installed_at=1_700_000_000.0,
    )


def test_runtime_index_is_complete_and_search_is_explainable() -> None:
    finder = SkillFinder(index_path=INDEX_PATH, skill_manager=StubSkillManager())
    assert len(finder.candidates()) == 4_333
    for query in (
        "提取 PDF 合同",
        "分析 Excel 销售表格",
        "为 React 网页编写 Playwright 自动化测试",
        "audit postgres database security",
        "制定 SEO 营销增长计划",
    ):
        result = finder.find(query)
        assert 0 < len(result["results"]) <= 6
        assert result["catalogFingerprint"] == finder.fingerprint
        assert result["trustCatalogFingerprint"] == finder._load_index()["catalogFingerprint"]
        assert all(item["trust"]["trustFingerprint"] for item in result["results"])
        assert all(item["reasons"] for item in result["results"])
        assert all(
            reason["origin"] in {"direct", "expanded"}
            for item in result["results"]
            for reason in item["reasons"]
        )
    assert finder.find("量子引力弦理论")["results"] == []


def test_router_search_excludes_suspicious_catalog_candidates() -> None:
    finder = SkillFinder(index_path=INDEX_PATH, skill_manager=StubSkillManager())
    excluded = next(
        candidate
        for candidate in finder.candidates()
        if candidate["sourceType"] == "catalog"
        and not candidate["trust"]["routerEligible"]
    )

    regular = finder.find(excluded["name"], limit=6)
    routed = finder.find(
        excluded["name"],
        limit=6,
        router_eligible_only=True,
    )

    assert excluded["candidateId"] in {
        item["candidateId"] for item in regular["results"]
    }
    assert excluded["candidateId"] not in {
        item["candidateId"] for item in routed["results"]
    }
    assert all(item["trust"]["routerEligible"] for item in routed["results"])


def test_installed_only_skill_can_be_found_and_resolved() -> None:
    skill = installed_skill(
        skill_id="workspace-invoice-review",
        name="内部发票复核",
        description="检查内部发票字段、税额和审批流程",
        repo_url="workspace://draft/invoice-review",
        sub_path="",
        source_ref=None,
    )
    finder = SkillFinder(index_path=INDEX_PATH, skill_manager=StubSkillManager([skill]))
    result = finder.find("复核内部发票税额", active_skill_ids={skill.skill_id})
    match = next(item for item in result["results"] if item["candidateId"] == "installed:workspace-invoice-review")
    assert match["availability"] == "active"
    resolved = finder.resolve(match["candidateId"], match["candidateFingerprint"])
    assert resolved["installedSkillId"] == skill.skill_id
    assert resolved["installSource"] is None


def test_local_import_router_candidate_is_bound_to_trust_fingerprint() -> None:
    eligible = InstalledSkill(
        skill_id="local-audit",
        name="本地审计报告",
        description="整理本地审计记录并生成报告",
        repo_url="local-import://skillimport_" + "a" * 32,
        sub_path="",
        installed_at=1_700_000_000.0,
        source_kind="local_import",
        source_id="skillimport_" + "a" * 32,
        source_revision=1,
        content_digest="b" * 64,
        trust_state="receipt_matched",
        trust_receipt_id="trust_local_" + "c" * 32,
        trust_fingerprint="d" * 64,
        trust_risk_level="medium",
        trust_status="conditional",
        trust_install_policy="confirm",
        trust_compatibility_status="conditional",
        trust_router_eligible=True,
        trust_package_digest="b" * 64,
    )
    excluded = InstalledSkill(
        **{
            **eligible.__dict__,
            "skill_id": "local-obfuscated",
            "name": "本地混淆脚本",
            "repo_url": "local-import://skillimport_" + "e" * 32,
            "source_id": "skillimport_" + "e" * 32,
            "trust_fingerprint": "f" * 64,
            "trust_router_eligible": False,
        }
    )
    finder = SkillFinder(
        index_path=INDEX_PATH,
        skill_manager=StubSkillManager([eligible, excluded]),
    )

    routed = finder.find("本地审计报告", router_eligible_only=True)
    match = next(
        item
        for item in routed["results"]
        if item["candidateId"] == "installed:local-audit"
    )
    assert match["trust"]["trustFingerprint"] == "d" * 64
    assert match["trust"]["routerEligible"] is True
    assert "installed:local-obfuscated" not in {
        item["candidateId"]
        for item in finder.find("本地混淆脚本", router_eligible_only=True)["results"]
    }

    changed = InstalledSkill(
        **{**eligible.__dict__, "trust_fingerprint": "1" * 64}
    )
    changed_candidate = next(
        item
        for item in SkillFinder(
            index_path=INDEX_PATH,
            skill_manager=StubSkillManager([changed]),
        ).candidates()
        if item["candidateId"] == "installed:local-audit"
    )
    assert changed_candidate["candidateFingerprint"] != match["candidateFingerprint"]


def test_catalog_candidate_reports_exact_stale_and_missing_install_state() -> None:
    base = SkillFinder(index_path=INDEX_PATH, skill_manager=StubSkillManager())
    candidate = base._load_index()["candidates"][0]
    source = candidate["installSource"]
    query = candidate["name"]

    missing = next(
        item
        for item in base.find(query)["results"]
        if item["candidateId"] == candidate["candidateId"]
    )
    assert missing["availability"] == "missing"

    stale_skill = installed_skill(
        skill_id="catalog-stale",
        name=candidate["name"],
        description=candidate["description"],
        repo_url=source["repoUrl"],
        sub_path=source["subPath"],
        source_ref="0" * 40,
    )
    stale_finder = SkillFinder(
        index_path=INDEX_PATH,
        skill_manager=StubSkillManager([stale_skill]),
    )
    stale = next(
        item
        for item in stale_finder.find(query)["results"]
        if item["candidateId"] == candidate["candidateId"]
    )
    assert stale["availability"] == "stale"

    exact_skill = installed_skill(
        skill_id="catalog-exact",
        name=candidate["name"],
        description=candidate["description"],
        repo_url=source["repoUrl"],
        sub_path=source["subPath"],
        source_ref=source["verifiedCommit"],
    )
    exact_finder = SkillFinder(
        index_path=INDEX_PATH,
        skill_manager=StubSkillManager([exact_skill]),
    )
    exact = next(
        item
        for item in exact_finder.find(query, active_skill_ids={exact_skill.skill_id})["results"]
        if item["candidateId"] == candidate["candidateId"]
    )
    assert exact["availability"] == "active"


def test_candidate_and_index_fingerprints_fail_closed(tmp_path: Path) -> None:
    payload = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    payload["candidates"][0]["name"] = "tampered"
    target = tmp_path / "runtime-index.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SkillRuntimeIndexError):
        SkillFinder(index_path=target).find("PDF")

    finder = SkillFinder(index_path=INDEX_PATH)
    candidate = finder._load_index()["candidates"][0]
    with pytest.raises(SkillFinderError) as caught:
        finder.resolve(candidate["candidateId"], "f" * 64)
    assert caught.value.code == "skill_candidate_stale"


def test_python_and_typescript_matcher_keep_the_same_golden_order() -> None:
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
        installStatus: 'ready',
        publisher: candidate.publisher,
        sourceGroup: candidate.sourceGroup,
        pathTerms: candidate.pathTerms,
    parentNames: candidate.parentNames,
    stableNameOrder: candidate.stableNameOrder,
      }));
      console.log(JSON.stringify(queries.map((query) =>
        findSkillsForNeed(query, candidates).map((match) => match.project.id)
      )));
    """
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(INDEX_PATH), json.dumps(queries, ensure_ascii=False)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    typescript_results = json.loads(completed.stdout)
    python_results = [
        [item["candidateId"] for item in SkillFinder(index_path=INDEX_PATH).find(query)["results"]]
        for query in queries
    ]
    assert python_results == typescript_results
