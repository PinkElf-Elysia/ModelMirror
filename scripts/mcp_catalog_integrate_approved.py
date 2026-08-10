#!/usr/bin/env python3
"""Integrate the approved dual-source MCP review list into the static catalog.

This generator is intentionally offline.  It consumes the committed review
snapshot, records the human approval decision, and emits display-only frontend
records plus non-executable backend manifest metadata.  It never emits a
command, endpoint, credential value, tool schema, or feature enablement.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = ROOT / "docs" / "mcp-catalog-expansion" / "review-candidates.json"
REPORT_PATH = ROOT / "docs" / "MCP_CATALOG_EXPANSION_REVIEW.md"
FRONTEND_PATH = ROOT / "client" / "src" / "data" / "mcpCatalogExpansionV2.generated.ts"
BACKEND_PATH = ROOT / "server" / "mcp" / "catalog_expansion_v2.py"

SNAPSHOT_DATE = "2026-08-09"
WAVE = 12
APPROVAL_REASON = (
    "用户已批准进入第二阶段产品目录；当前仅作为 planned 展示，"
    "尚未完成固定工具契约、隔离策略与真实代表调用验收。"
)

DESKTOP_REPOSITORIES = {
    "bx33661/wireshark-mcp",
    "bytedance/ui-tars-desktop",
    "carterlasalle/mac_messages_mcp",
    "codergamester/mcp-unity",
    "coding-solo/godot-mcp",
    "diivi/aseprite-mcp",
    "ivanmurzak/unity-mcp",
    "lpigeon/ros-mcp-server",
    "mrexodia/ida-pro-mcp",
    "radareorg/radare2-mcp",
    "samuelgursky/davinci-resolve-mcp",
    "wonderwhy-er/desktopcommandermcp",
    "zinja-coder/jadx-ai-mcp",
}

REMOTE_CATEGORIES = {
    "浏览器与网页",
    "效率与协作",
    "地理与出行",
    "云平台与运维",
    "搜索与研究",
    "通讯与协作",
    "金融与市场",
    "社交与内容",
}

HIGH_RISK_CATEGORIES = {
    "浏览器与网页",
    "数据库",
    "效率与协作",
    "安全分析",
    "云平台与运维",
    "通讯与协作",
    "金融与市场",
    "社交与内容",
}


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not result or len(result) > 80:
        raise ValueError(f"invalid generated catalog id: {result!r}")
    return result


def _catalog_id(candidate: dict[str, Any]) -> str:
    repo_name = str(candidate["repo_name"])
    owner, repo = repo_name.split("/", 1)
    parts = [owner, repo]
    subpath = candidate.get("subpath")
    if isinstance(subpath, str) and subpath and not subpath.startswith("docs/"):
        parts.append(subpath.rsplit("/", 1)[-1])
    return _slug("-".join(parts))


def _humanize(value: str) -> str:
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    words = re.sub(r"[._-]+", " ", value).split()
    rendered: list[str] = []
    acronyms = {
        "api": "API",
        "aws": "AWS",
        "db": "DB",
        "evm": "EVM",
        "git": "Git",
        "ida": "IDA",
        "jadx": "JADX",
        "mcp": "MCP",
        "mysql": "MySQL",
        "ros": "ROS",
        "ui": "UI",
    }
    for word in words:
        rendered.append(acronyms.get(word.lower(), word[:1].upper() + word[1:]))
    return " ".join(rendered)


def _display_name(candidate: dict[str, Any]) -> str:
    raw_name = str(candidate.get("name") or "").strip()
    if not raw_name or "/" in raw_name or "@" in raw_name:
        raw_name = str(candidate.get("subpath") or "").rsplit("/", 1)[-1]
    if not raw_name:
        raw_name = str(candidate["repo_name"]).rsplit("/", 1)[-1]
    name = _humanize(raw_name)
    if "mcp" not in name.lower():
        name += " MCP"
    return name


def _classify(candidate: dict[str, Any]) -> dict[str, Any]:
    repo_key = str(candidate["repo_name"]).lower()
    category = str(candidate["category"])
    if repo_key in DESKTOP_REPOSITORIES:
        return {
            "connection_kind": "desktop-bridge",
            "risk": "critical",
            "requirements": ["desktop-host", "external-runtime", "system-permission"],
            "required_capabilities": ["versioned-desktop-bridge", "per-app-consent"],
            "limitations": [
                "仅完成双源目录身份核验；尚无可信桌面宿主、实例所有权证明或逐应用授权。",
                "当前没有命令、端点、工具或连接入口，环境功能开关也不能使该条目可执行。",
            ],
        }
    if category == "数据库":
        return {
            "connection_kind": "sandboxed-stdio",
            "risk": "high",
            "requirements": ["database-credentials", "external-runtime", "system-permission"],
            "required_capabilities": [
                "database-read-only-policy",
                "database-target-validation",
                "query-limits",
            ],
            "limitations": [
                "仅完成双源目录身份核验；尚未冻结数据库目标、原生只读模式、查询上限与凭据作用域。",
                "当前没有命令、端点、凭据槽、工具或连接入口，环境功能开关也不能使该条目可执行。",
            ],
        }
    if category in REMOTE_CATEGORIES:
        return {
            "connection_kind": "remote-mcp",
            "risk": "high" if category in HIGH_RISK_CATEGORIES else "medium",
            "requirements": ["external-runtime", "remote-transport"],
            "required_capabilities": [
                "fixed-saas-contract",
                "fixed-egress-policy",
                "encrypted-credential-binding",
            ],
            "limitations": [
                "仅完成双源目录身份核验；尚未冻结远程传输、服务域名、凭据槽、工具副作用与限流策略。",
                "当前没有命令、端点、凭据槽、工具或连接入口，环境功能开关也不能使该条目可执行。",
            ],
        }
    if category == "安全分析":
        return {
            "connection_kind": "sandboxed-stdio",
            "risk": "high",
            "requirements": ["external-runtime", "system-permission"],
            "required_capabilities": [
                "ephemeral-code-sandbox",
                "scoped-filesystem",
                "terminal-action-approval",
            ],
            "limitations": [
                "仅完成双源目录身份核验；尚未冻结输入范围、只读工具子集、进程隔离与终止性操作审批。",
                "当前没有命令、端点、工具或连接入口，环境功能开关也不能使该条目可执行。",
            ],
        }
    return {
        "connection_kind": "sandboxed-stdio",
        "risk": "medium",
        "requirements": ["external-runtime", "system-permission"],
        "required_capabilities": [
            "maintained-upstream-contract",
            "scoped-filesystem",
            "resource-limits",
        ],
        "limitations": [
            "仅完成双源目录身份核验；尚未冻结上游版本、工具 Schema、文件范围与资源上限。",
            "当前没有命令、端点、工具或连接入口，环境功能开关也不能使该条目可执行。",
        ],
    }


def build_approved_payload(source: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(source)
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 100:
        raise ValueError("approved review snapshot must contain exactly 100 candidates")
    ids: set[str] = set()
    for rank, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict) or candidate.get("rank") != rank:
            raise ValueError("approved candidates must retain deterministic rank order")
        catalog_id = _catalog_id(candidate)
        if catalog_id in ids:
            raise ValueError(f"duplicate generated catalog id: {catalog_id}")
        ids.add(catalog_id)
        candidate["catalog_id"] = catalog_id
        candidate["decision"] = "approved"
        candidate["proposed_availability"] = "planned"
        candidate["decision_reason"] = APPROVAL_REASON
    payload["purpose"] = "approved-catalog-expansion"
    payload["runtime_catalog_changed"] = True
    payload["runtime_execution_changed"] = False
    payload["approval"] = {
        "approved_at": SNAPSHOT_DATE,
        "approved_count": 100,
        "availability": {"planned": 100, "blocked": 0},
        "execution_boundary": "display-and-non-executable-manifest-only",
    }
    return payload


def _frontend_record(candidate: dict[str, Any]) -> dict[str, Any]:
    classification = _classify(candidate)
    github = candidate.get("github")
    if not isinstance(github, dict):
        raise ValueError(f"candidate lacks GitHub metadata: {candidate.get('catalog_id')}")
    repo_name = str(github.get("nameWithOwner") or candidate["repo_name"])
    repo_url = str(github.get("url") or candidate["repo_url"])
    language_info = github.get("primaryLanguage")
    language = (
        str(language_info.get("name"))
        if isinstance(language_info, dict) and language_info.get("name")
        else "Unknown"
    )
    license_info = github.get("licenseInfo")
    license_spdx = (
        str(license_info.get("spdxId"))
        if isinstance(license_info, dict) and license_info.get("spdxId")
        else "Unknown"
    )
    name = _display_name(candidate)
    source_ids = sorted({str(item["source_id"]) for item in candidate["sources"]})
    return {
        "id": candidate["catalog_id"],
        "name": name,
        "repoName": repo_name,
        "repoUrl": repo_url,
        "category": candidate["category"],
        "description": (
            f"{name} 面向“{candidate['category']}”场景提供 MCP 能力；"
            "本轮只纳入经双源与仓库硬门禁核验的目录资料，尚未接入模镜运行时。"
        ),
        "readmeSummary": (
            f"{repo_name} 已通过公开仓库、{license_spdx} 许可证与最近维护时间硬门禁；"
            "上游工具、传输与安全契约仍待逐项冻结。"
        ),
        "stars": int(github.get("stargazerCount") or 0),
        "language": language,
        "verifiedAt": SNAPSHOT_DATE,
        "tags": [str(candidate["category"]), language, license_spdx],
        "requirements": classification["requirements"],
        "usageExamples": [
            f"查看 {name} 的上游用途和当前适配状态",
            "等待固定工具契约与安全验收后再进行连接测试",
        ],
        "sources": source_ids,
        "adaptation": {
            "wave": WAVE,
            "availability": "planned",
            "connectionKind": classification["connection_kind"],
            "risk": classification["risk"],
            "requiredCapabilities": classification["required_capabilities"],
            "limitations": classification["limitations"],
        },
    }


def render_frontend(payload: dict[str, Any]) -> str:
    records = [_frontend_record(item) for item in payload["candidates"]]
    body = json.dumps(records, ensure_ascii=False, indent=2)
    return (
        "// Generated by scripts/mcp_catalog_integrate_approved.py; do not edit manually.\n"
        "// These records are descriptive only and intentionally contain no execution config.\n\n"
        f"export const mcpCatalogExpansionV2 = {body} as const;\n"
    )


def render_backend(payload: dict[str, Any]) -> str:
    lines = [
        '"""Generated non-executable manifests for the approved MCP catalog expansion."""',
        "",
        "from __future__ import annotations",
        "",
        "from dataclasses import dataclass",
        "",
        "",
        "@dataclass(frozen=True, slots=True)",
        "class CatalogExpansionV2Adapter:",
        "    project_id: str",
        "    connection_kind: str",
        "    risk: str",
        "    required_capabilities: tuple[str, ...]",
        "    limitations: tuple[str, ...]",
        "",
        "",
        "CATALOG_EXPANSION_V2_ADAPTERS = (",
    ]
    for candidate in payload["candidates"]:
        classification = _classify(candidate)
        lines.extend(
            [
                "    CatalogExpansionV2Adapter(",
                f"        project_id={candidate['catalog_id']!r},",
                f"        connection_kind={classification['connection_kind']!r},",
                f"        risk={classification['risk']!r},",
                f"        required_capabilities={tuple(classification['required_capabilities'])!r},",
                f"        limitations={tuple(classification['limitations'])!r},",
                "    ),",
            ]
        )
    lines.extend([")", "", "CATALOG_EXPANSION_V2_IDS = tuple(", "    item.project_id for item in CATALOG_EXPANSION_V2_ADAPTERS", ")", ""])
    return "\n".join(lines)


def render_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# MCP 双源目录扩充批准清单",
        "",
        f"审查快照日期：{payload['snapshot_date']}",
        "",
        "> 100 项已经批准进入产品目录，但全部保持 `planned` 且不可执行。",
        "> 本清单不包含命令、端点、凭据槽、工具 Schema 或功能开关默认启用。",
        "",
        "## 固定来源",
        "",
    ]
    for source in payload["source_snapshots"]:
        lines.append(
            f"- `{source['source_id']}`：`{source['commit']}`，README SHA-256 `{source['readme_sha256']}`"
        )
    lines.extend(
        [
            "",
            "## 结果",
            "",
            f"- 上游解析记录：{payload['upstream_inventory_summary']['parsed_entries']}",
            f"- 唯一仓库/子包：{payload['upstream_inventory_summary']['unique_repositories']}",
            f"- 结构预筛后的新候选：{payload['upstream_inventory_summary']['eligible_new_candidates']}",
            f"- 批准纳入目录：{summary['selected']}",
            f"- 覆盖分类：{summary['categories']}",
            f"- 中文源命中：{summary['by_source']['awesome-mcp-zh']}",
            f"- 英文源命中：{summary['by_source']['awesome-mcp-servers']}",
            "- 新增执行能力：0",
            "",
            "硬门禁：公开仓库存在，未归档/禁用/私有/派生，许可证 SPDX 明确，且最近 12 个月有推送。每个分类最多 15 项，每个仓库最多 2 项。",
            "",
            "## 已批准的 100 项",
            "",
            "| 排名 | 目录 ID | 仓库 | 分类 | Stars | 许可证 | 来源 | 状态 |",
            "| ---: | --- | --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for item in payload["candidates"]:
        github = item["github"]
        license_spdx = github["licenseInfo"]["spdxId"]
        source_ids = ", ".join(sorted(source["source_id"] for source in item["sources"]))
        lines.append(
            f"| {item['rank']} | `{item['catalog_id']}` | "
            f"[{github['nameWithOwner']}]({github['url']}) | {item['category']} | "
            f"{github['stargazerCount']} | {license_spdx} | {source_ids} | 已批准 · planned |"
        )
    lines.extend(
        [
            "",
            "## 执行边界",
            "",
            "这 100 项仅扩充前端发现目录和后端非执行 manifest。每项均为 `planned`、`executable=false`，没有命令、端点、凭据槽、工具策略或 Sidecar。后续若要变为 ready，仍须逐项目冻结上游版本与 Schema，并通过对应隔离、凭据、审批、限流和真实代表调用验收。",
            "",
        ]
    )
    return "\n".join(lines)


def _write_or_check(path: Path, content: str, *, check: bool) -> None:
    if check:
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            raise SystemExit(f"generated file is stale: {path.relative_to(ROOT)}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
    payload = build_approved_payload(source)
    review_content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    _write_or_check(REVIEW_PATH, review_content, check=args.check)
    _write_or_check(FRONTEND_PATH, render_frontend(payload), check=args.check)
    _write_or_check(BACKEND_PATH, render_backend(payload), check=args.check)
    _write_or_check(REPORT_PATH, render_report(payload), check=args.check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
