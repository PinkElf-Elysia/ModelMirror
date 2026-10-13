from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SERVER_SNAPSHOT = ROOT / "server" / "data" / "agents.json"
CLIENT_SNAPSHOT = ROOT / "client" / "src" / "data" / "agents.ts"
UPSTREAM_COMMIT = "e5a6c0a730878aaa871ec5b95859aec2c2728475"
FORBIDDEN_SCENARIOS = {
    "构建未来，一个 commit 一个脚印。",
    "不走寻常路的专家。",
    "让产品好看、好用、有惊喜。",
    "一个真实互动一个粉丝地增长。",
}
GENERIC_CAPABILITIES = {
    "角色",
    "个性",
    "记忆",
    "经验",
    "原则",
    "工作流程",
    "沟通风格",
    "学习与记忆",
    "成功指标",
}


def _client_agents() -> list[dict[str, object]]:
    source = CLIENT_SNAPSHOT.read_text(encoding="utf-8")
    marker = "export const agents: AgentProfile[] = "
    start = source.index(marker) + len(marker)
    end = source.index(";\n\nexport const agentDepartments", start)
    return json.loads(source[start:end])


def test_agent_catalog_snapshot_is_complete_and_consistent() -> None:
    server_agents = json.loads(SERVER_SNAPSHOT.read_text(encoding="utf-8"))
    client_agents = _client_agents()

    assert server_agents == client_agents
    assert len(server_agents) == 277
    assert len({item["id"] for item in server_agents}) == 277
    assert len({item["department"] for item in server_agents}) == 20


def test_agent_catalog_uses_task_language_and_commit_pinned_sources() -> None:
    agents = json.loads(SERVER_SNAPSHOT.read_text(encoding="utf-8"))

    assert all(item["scenarios"] not in FORBIDDEN_SCENARIOS for item in agents)
    assert all(len(item["capabilities"]) >= 2 for item in agents)
    assert all(
        not (set(item["capabilities"]) & GENERIC_CAPABILITIES) for item in agents
    )
    assert all(
        f"/blob/{UPSTREAM_COMMIT}/" in item["sourceUrl"] for item in agents
    )
    assert all(item["source"] in {"原创", "翻译"} for item in agents)

    ui_designer = next(item for item in agents if item["id"] == "design-ui-designer")
    assert ui_designer["capabilities"] == ["视觉设计", "组件库", "设计系统"]
    assert ui_designer["scenarios"] == "界面设计、品牌一致性"


def test_company_department_preserves_existing_role_identity() -> None:
    agents = json.loads(SERVER_SNAPSHOT.read_text(encoding="utf-8"))
    company = {item["id"]: item for item in agents if item["department"] == "公司经营部"}
    assert set(company) == {
        "chief-executive-officer", "chief-technology-officer", "chief-product-officer",
        "chief-marketing-officer", "chief-operating-officer", "chief-financial-officer",
        "specialized-chief-of-staff",
    }
    assert company["specialized-chief-of-staff"]["sourcePath"] == "company/chief-of-staff.md"
    assert company["chief-financial-officer"]["sourcePath"] == "company/chief-financial-officer.md"
    assert len({item["sourcePath"] for item in agents}) == 277
    assert all(item["prompt"].strip() for item in agents)


def test_client_department_totals_and_provenance_match_snapshot() -> None:
    source = CLIENT_SNAPSHOT.read_text(encoding="utf-8")
    agents = _client_agents()
    counts = json.loads(source.split("export const agentDepartmentCounts: Record<string, number> = ", 1)[1].split(";", 1)[0])
    assert counts == {department: sum(item["department"] == department for item in agents) for department in counts}
    assert sum(counts.values()) == len(agents)
    assert counts["公司经营部"] == 7
    provenance = source.split("export const AGENCY_AGENTS_UPSTREAM = ", 1)[1].split("} as const", 1)[0]
    assert f'commit: "{UPSTREAM_COMMIT}"' in provenance
    assert int(re.search(r"count: (\d+)", provenance).group(1)) == len(agents)
