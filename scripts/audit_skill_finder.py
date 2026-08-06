from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.skills.finder import SkillFinder, SkillFinderError, SkillRuntimeIndexError
from server.skills.skill_manager import InstalledSkill


INDEX_PATH = ROOT / "server" / "skills" / "data" / "skill_runtime_index.json"
QUERIES = [
    "提取 PDF 合同",
    "分析 Excel 销售表格",
    "为 React 网页编写 Playwright 自动化测试",
    "audit postgres database security",
    "制定 SEO 营销增长计划",
]


class StubSkillManager:
    def __init__(self, skills: list[InstalledSkill] | None = None) -> None:
        self.skills = list(skills or [])

    def list_installed_skills(self) -> list[InstalledSkill]:
        return list(self.skills)


finder = SkillFinder(index_path=INDEX_PATH, skill_manager=StubSkillManager())
assert len(finder.candidates()) == 4_333
for query in QUERIES:
    result = finder.find(query)
    assert 0 < len(result["results"]) <= 6, query
    assert all(item["reasons"] for item in result["results"]), query
assert finder.find("量子引力弦理论")["results"] == []

local_skill = InstalledSkill(
    skill_id="workspace-invoice-review",
    name="内部发票复核",
    description="检查内部发票字段、税额和审批流程",
    repo_url="workspace://draft/invoice-review",
    sub_path="",
    source_ref=None,
    installed_at=1_700_000_000.0,
)
local_finder = SkillFinder(
    index_path=INDEX_PATH,
    skill_manager=StubSkillManager([local_skill]),
)
local_match = next(
    item
    for item in local_finder.find(
        "复核内部发票税额", active_skill_ids={local_skill.skill_id}
    )["results"]
    if item["candidateId"] == "installed:workspace-invoice-review"
)
assert local_match["availability"] == "active"
assert (
    local_finder.resolve(
        local_match["candidateId"], local_match["candidateFingerprint"]
    )["installedSkillId"]
    == local_skill.skill_id
)

candidate = finder._load_index()["candidates"][0]
try:
    finder.resolve(candidate["candidateId"], "f" * 64)
except SkillFinderError as exc:
    assert exc.code == "skill_candidate_stale"
else:
    raise AssertionError("stale candidate fingerprint was accepted")

tampered = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
tampered["candidates"][0]["name"] = "tampered"
with tempfile.TemporaryDirectory(prefix="modelmirror-skill-finder-") as temp_dir:
    target = Path(temp_dir) / "runtime-index.json"
    target.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    try:
        SkillFinder(index_path=target).find("PDF")
    except SkillRuntimeIndexError:
        pass
    else:
        raise AssertionError("tampered runtime index was accepted")

node_script = r"""
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
    [
        "node",
        "--input-type=module",
        "-e",
        node_script,
        str(INDEX_PATH),
        json.dumps(QUERIES, ensure_ascii=False),
    ],
    cwd=ROOT,
    check=True,
    capture_output=True,
    text=True,
    encoding="utf-8",
)
typescript_results = json.loads(completed.stdout)
python_results = [
    [item["candidateId"] for item in finder.find(query)["results"]]
    for query in QUERIES
]
assert python_results == typescript_results, {
    "python": python_results,
    "typescript": typescript_results,
}

print(
    "Skill Finder audit passed: 4,333 catalog candidates, installed-only discovery, "
    "fingerprint fail-closed, and TypeScript/Python golden parity"
)
