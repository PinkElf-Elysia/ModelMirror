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
WAVE = 13

READY_DECISIONS = {
    "brave-brave-search-mcp-server": {
        "reason_code": "ready-official-read-only-token-contract",
        "reason": (
            "官方 Brave Search MCP Server v2.1.0 已锁定；仅开放网页搜索与地点搜索两个"
            "只读工具，并复用固定出口、加密凭据和 Schema 漂移阻断。"
        ),
        "adapter_version": "2.1.0",
        "wave": 13,
    },
    "blazickjp-arxiv-mcp-server": {
        "reason_code": "ready-native-read-only-metadata-facade",
        "reason": (
            "arxiv-mcp-server v0.6.2 的公开元数据契约已锁定；仅开放论文搜索与摘要读取，"
            "下载、全文读取、本地缓存、提醒和导出工具均不可发现、不可调用。"
        ),
        "adapter_version": "0.6.2-compatible-native-v1",
        "wave": 14,
    },
    "kagisearch-kagimcp": {
        "reason_code": "ready-official-native-read-only-api-facade",
        "reason": (
            "官方 kagimcp v1.0.2 的 Search/Extract 契约已锁定；原生兼容层仅向 kagi.com "
            "发送固定 Bearer API 请求，并拒绝携带凭据型查询参数的提取 URL。"
        ),
        "adapter_version": "1.0.2-compatible-native-v1",
        "wave": 14,
    },
    "fatwang2-search1api-mcp": {
        "reason_code": "ready-official-native-discovery-only-facade",
        "reason": (
            "官方 search1api-mcp v0.5.3 契约已锁定；原生兼容层仅开放搜索、新闻与"
            "趋势发现，强制关闭结果抓取，并固定 Search1API 出口与保守限流。"
        ),
        "adapter_version": "0.5.3-compatible-native-v1",
        "wave": 15,
    },
    "livetennisapi-livetennisapi-mcp": {
        "reason_code": "ready-official-native-free-read-only-facade",
        "reason": (
            "官方 livetennisapi-mcp v1.4.0 契约已锁定；原生兼容层仅开放 FREE 层"
            "实时比分、赛程、球员与赛事目录，并剔除赔率、预测和模型字段。"
        ),
        "adapter_version": "1.4.0-compatible-native-v1",
        "wave": 15,
    },
}

BLOCKED_DECISION_GROUPS = {
    "blocked-arbitrary-browser-or-url-surface": {
        "0xmassi-webclaw",
        "co-browser-browser-use-mcp-server",
        "eyalzh-browser-control-mcp",
        "bytedance-ui-tars-desktop-browser",
    },
    "blocked-arbitrary-command-or-code-execution": {
        "sipyourdrink-ltd-bernstein",
        "wonderwhy-er-desktopcommandermcp",
        "oraios-serena",
        "ckreiling-mcp-server-docker",
        "datalayer-jupyter-mcp-server",
        "g0t4-mcp-server-commands",
    },
    "blocked-desktop-host-instance-unverified": {
        "mrexodia-ida-pro-mcp",
        "samuelgursky-davinci-resolve-mcp",
        "codergamester-mcp-unity",
        "coding-solo-godot-mcp",
        "radareorg-radare2-mcp",
        "diivi-aseprite-mcp",
        "ivanmurzak-unity-mcp",
        "carterlasalle-mac-messages-mcp",
        "zinja-coder-jadx-ai-mcp",
    },
    "blocked-privileged-infrastructure-write": {
        "flux159-mcp-server-kubernetes",
        "skyhook-io-radar",
        "alexei-led-k8s-mcp-server",
        "alexei-led-aws-mcp-server",
        "nwiizo-tfmcp",
    },
    "blocked-broad-account-or-message-write": {
        "chigwell-telegram-mcp",
        "taylorwilsdon-google-workspace-mcp",
        "inditextech-mcp-teams-server",
        "tuanle96-mcp-odoo",
        "xing5-mcp-google-sheets",
        "integromat-make-mcp-server",
        "line-line-bot-mcp-server",
    },
    "blocked-financial-or-transactional-write": {
        "flox-foundation-flox-mcp",
        "mcpdotdirect-evm-mcp-server",
        "yolfinance-yolfi-agent",
    },
    "blocked-social-publishing-or-session-reuse": {
        "xquik-dev-x-twitter-scraper",
        "caol64-wenyan-mcp",
        "taisly-agent",
    },
    "blocked-physical-device-control": {
        "stack-chan-stack-chan",
        "lpigeon-ros-mcp-server",
    },
    "blocked-not-an-executable-mcp-server": {
        "nteract-semiotic",
        "public-ui-kolibri",
        "emiliaprotocol-emilia-protocol",
    },
    "blocked-superseded-unbounded-third-party": {
        "suekou-mcp-notion-server",
        "alexander-zuev-supabase-mcp-server",
    },
}

BLOCKED_REASONS = {
    "blocked-arbitrary-browser-or-url-surface": (
        "上游开放任意 URL、浏览器状态或反自动化能力，无法复用已冻结的匿名单 Origin 浏览器契约。"
    ),
    "blocked-arbitrary-command-or-code-execution": (
        "上游核心能力是任意命令、代码、容器或仓库写入，不能降格为固定只读工具而保持产品身份。"
    ),
    "blocked-desktop-host-instance-unverified": (
        "上游依赖桌面应用插件或本机实例；当前没有可信宿主桥接、实例所有权证明和逐应用授权。"
    ),
    "blocked-privileged-infrastructure-write": (
        "上游可直接修改集群、云资源或 Terraform 状态；固定只读 facade 与强制审批尚不存在。"
    ),
    "blocked-broad-account-or-message-write": (
        "上游覆盖外部账号、消息或协作对象的广泛读写；多租户 OAuth、最小 scope 和目标审批尚未完成。"
    ),
    "blocked-financial-or-transactional-write": (
        "上游包含订单、链上交易或真实资金相关操作；本目录不开放交易执行与签名能力。"
    ),
    "blocked-social-publishing-or-session-reuse": (
        "上游依赖社交账号发布、已登录会话或平台写入；不满足无登录态复用与逐次写审批边界。"
    ),
    "blocked-physical-device-control": (
        "上游可控制机器人或物理设备；当前没有设备身份、急停、权限和现场安全边界。"
    ),
    "blocked-not-an-executable-mcp-server": (
        "固定仓库未证明存在可锁定、可独立运行并可发现工具的 MCP Server 产品身份。"
    ),
    "blocked-superseded-unbounded-third-party": (
        "该第三方实现提供的管理或写入面宽于目录中已存在的官方受控适配器，不再新增重复执行入口。"
    ),
}

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


def _blocked_reason_code(catalog_id: str) -> str | None:
    matches = [
        reason_code
        for reason_code, catalog_ids in BLOCKED_DECISION_GROUPS.items()
        if catalog_id in catalog_ids
    ]
    if len(matches) > 1:
        raise ValueError(f"multiple blocked decisions for {catalog_id}: {matches}")
    return matches[0] if matches else None


def _planned_reason(candidate: dict[str, Any]) -> tuple[str, str]:
    category = str(candidate["category"])
    if category == "数据库":
        return (
            "planned-read-only-data-facade",
            "保留为 planned；需要固定目标配置、原生只读账号、查询上限与逐工具 Schema 后才能接入。",
        )
    if category in {"搜索与研究", "地理与出行"}:
        return (
            "planned-fixed-egress-read-only-contract",
            "保留为 planned；需要锁定只读工具、固定出口、URL 参数边界、限流与代表调用。",
        )
    if category in {"文件与存储", "数据分析", "多媒体"}:
        return (
            "planned-scoped-file-or-artifact-contract",
            "保留为 planned；需要受控工作区、输入只读、输出产物登记、资源限额与文件格式验收。",
        )
    if category == "知识与记忆":
        return (
            "planned-stateful-resource-policy",
            "保留为 planned；需要项目级持久化边界、内容配额、写入审批、清理与重放语义。",
        )
    if category in {"效率与协作", "通讯与协作", "社交与内容"}:
        return (
            "planned-auth-scope-and-write-policy",
            "保留为 planned；需要多租户账号绑定、最小权限 scope、固定资源范围和写操作审批。",
        )
    if category in {"云平台与运维", "金融与市场"}:
        return (
            "planned-domain-read-only-facade",
            "保留为 planned；仅在能够证明固定只读子集、目标作用域与无交易/无变更边界后继续。",
        )
    return (
        "planned-fixed-sandbox-contract",
        "保留为 planned；需要锁定上游版本、工具 Schema、隔离范围、资源上限与真实代表调用。",
    )


def _decision(candidate: dict[str, Any]) -> dict[str, Any]:
    catalog_id = str(candidate["catalog_id"])
    ready = READY_DECISIONS.get(catalog_id)
    if ready is not None:
        return {
            "availability": "ready",
            "reason_code": str(ready["reason_code"]),
            "reason": str(ready["reason"]),
            "adapter_version": str(ready["adapter_version"]),
            "wave": int(ready["wave"]),
        }
    blocked_code = _blocked_reason_code(catalog_id)
    if blocked_code is not None:
        return {
            "availability": "blocked",
            "reason_code": blocked_code,
            "reason": BLOCKED_REASONS[blocked_code],
            "adapter_version": "blocked",
            "wave": WAVE,
        }
    reason_code, reason = _planned_reason(candidate)
    return {
        "availability": "planned",
        "reason_code": reason_code,
        "reason": reason,
        "adapter_version": "planned",
        "wave": WAVE,
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


def _classified_adaptation(candidate: dict[str, Any]) -> dict[str, Any]:
    classification = _classify(candidate)
    decision = _decision(candidate)
    availability = decision["availability"]
    if availability == "ready" and candidate["catalog_id"] == "brave-brave-search-mcp-server":
        classification.update(
            {
                "connection_kind": "sandboxed-stdio",
                "risk": "medium",
                "requirements": ["token"],
                "required_capabilities": [
                    "encrypted-credential-binding",
                    "credential-revocation-check",
                    "fixed-egress-policy",
                    "read-only-tool-policy",
                    "schema-drift-recovery",
                ],
                "limitations": [
                    "仅开放官方 v2.1.0 的 brave_web_search 与 brave_local_search；其余搜索、摘要和媒体工具不可发现、不可调用。",
                    "Brave API Key 仅由服务端加密库注入，出口固定为 api.search.brave.com；不接受命令、端点、Header 或环境变量。",
                ],
            }
        )
    elif availability == "ready" and candidate["catalog_id"] == "kagisearch-kagimcp":
        classification.update(
            {
                "connection_kind": "sandboxed-stdio",
                "risk": "medium",
                "requirements": ["token"],
                "required_capabilities": [
                    "encrypted-credential-binding",
                    "credential-revocation-check",
                    "fixed-egress-policy",
                    "read-only-tool-policy",
                    "schema-drift-recovery",
                ],
                "limitations": [
                    "仅开放官方 v1.0.2 的 kagi_search_fetch 与 kagi_extract；搜索结果上限降为 20，域名、Lens 和批量提取参数不开放。",
                    "Kagi API Key 仅由服务端加密库注入，出口固定为 kagi.com；提取 URL 必须为公网 HTTPS 且不能携带凭据型查询参数。",
                ],
            }
        )
    elif availability == "ready" and candidate["catalog_id"] == "blazickjp-arxiv-mcp-server":
        classification.update(
            {
                "connection_kind": "sandboxed-stdio",
                "risk": "low",
                "requirements": [],
                "required_capabilities": [
                    "fixed-egress-policy",
                    "read-only-tool-policy",
                    "schema-drift-recovery",
                    "provider-rate-limit",
                ],
                "limitations": [
                    "仅开放 v0.6.2 的 search_papers 与 get_abstract 元数据子集；每次最多 20 篇，并强制 3 秒请求间隔。",
                    "出口固定为 export.arxiv.org；下载、全文读取、本地缓存、提醒、语义索引、引用导出和文件资源均关闭。",
                ],
            }
        )
    elif availability == "ready" and candidate["catalog_id"] == "fatwang2-search1api-mcp":
        classification.update(
            {
                "connection_kind": "sandboxed-stdio",
                "risk": "medium",
                "requirements": ["token"],
                "required_capabilities": [
                    "encrypted-credential-binding",
                    "credential-revocation-check",
                    "fixed-egress-policy",
                    "read-only-tool-policy",
                    "schema-drift-recovery",
                    "provider-rate-limit",
                ],
                "limitations": [
                    "仅开放官方 v0.5.3 的 search、news 与 trending；crawl、sitemap、截图、结构化提取和任意页面抓取均关闭。",
                    "API Key 仅由服务端加密库注入，出口固定为 api.search1api.com；搜索与新闻强制 crawl_results=0，每次最多 20 条。",
                ],
            }
        )
    elif availability == "ready" and candidate["catalog_id"] == "livetennisapi-livetennisapi-mcp":
        classification.update(
            {
                "connection_kind": "sandboxed-stdio",
                "risk": "medium",
                "requirements": ["token"],
                "required_capabilities": [
                    "encrypted-credential-binding",
                    "credential-revocation-check",
                    "fixed-egress-policy",
                    "read-only-tool-policy",
                    "schema-drift-recovery",
                    "provider-rate-limit",
                ],
                "limitations": [
                    "仅开放官方 v1.4.0 对应 FREE 层的实时/即将开始比赛、当前比分、球员、赛程与赛事目录；历史、赔率、市场、预测、模型分析、统计和 WebSocket 均关闭。",
                    "响应按固定字段投影并移除 win_probability、danger、market、analysis 与 stats；API Key 仅由服务端加密库注入，出口固定为 api.livetennisapi.com。",
                ],
            }
        )
    elif availability == "blocked":
        classification["limitations"] = [
            decision["reason"],
            "该条目保持不可执行；没有命令、端点、凭据槽、工具策略或连接入口，功能开关不能绕过。",
        ]
    else:
        classification["limitations"] = [
            decision["reason"],
            *classification["limitations"],
        ]
    return {**classification, **decision}


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
        decision = _decision(candidate)
        candidate["decision"] = (
            "adapted-ready"
            if decision["availability"] == "ready"
            else "blocked"
            if decision["availability"] == "blocked"
            else "deferred-planned"
        )
        candidate["proposed_availability"] = decision["availability"]
        candidate["decision_reason_code"] = decision["reason_code"]
        candidate["decision_reason"] = decision["reason"]
        candidate["adapter_version"] = decision["adapter_version"]
        candidate["adaptation_wave"] = decision["wave"]
    unknown_blocked = {
        catalog_id
        for catalog_ids in BLOCKED_DECISION_GROUPS.values()
        for catalog_id in catalog_ids
        if catalog_id not in ids
    }
    if unknown_blocked:
        raise ValueError(f"blocked decisions reference unknown ids: {sorted(unknown_blocked)}")
    if set(READY_DECISIONS) - ids:
        raise ValueError("ready decisions reference unknown ids")
    availability = {
        status: sum(item["proposed_availability"] == status for item in candidates)
        for status in ("ready", "planned", "blocked")
    }
    if availability != {"ready": 5, "planned": 51, "blocked": 44}:
        raise ValueError(f"unexpected adaptation classification: {availability}")
    payload["purpose"] = "adaptation-classification"
    payload["runtime_catalog_changed"] = True
    payload["runtime_execution_changed"] = True
    payload["adaptation"] = {
        "classified_at": SNAPSHOT_DATE,
        "classified_count": 100,
        "availability": availability,
        "ready_boundary": "fixed-read-only-token-sidecar-contract",
        "non_ready_boundary": "no-command-endpoint-credential-or-tool-policy",
    }
    payload.pop("approval", None)
    return payload


def _frontend_record(candidate: dict[str, Any]) -> dict[str, Any]:
    classification = _classified_adaptation(candidate)
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
            + (
                "当前已通过固定只读运行时与工具契约验收。"
                if classification["availability"] == "ready"
                else "当前保持不可执行，等待后续安全边界完成。"
            )
        ),
        "readmeSummary": (
            f"{repo_name} 已通过公开仓库、{license_spdx} 许可证与最近维护时间硬门禁；"
            f"当前判定为 {classification['availability']}，原因码 {classification['reason_code']}。"
        ),
        "stars": int(github.get("stargazerCount") or 0),
        "language": language,
        "verifiedAt": SNAPSHOT_DATE,
        "tags": [str(candidate["category"]), language, license_spdx],
        "requirements": classification["requirements"],
        "usageExamples": [
            f"查看 {name} 的上游用途和当前适配判定",
            (
                "保存所需凭据后连接，并仅执行已审核的只读工具"
                if classification["availability"] == "ready"
                else "根据阻断或规划原因完成后续门槛后再进行连接测试"
            ),
        ],
        "sources": source_ids,
        "adaptation": {
            "wave": classification["wave"],
            "availability": classification["availability"],
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
        "    availability: str",
        "    decision_reason_code: str",
        "    adapter_version: str",
        "    adaptation_wave: int",
        "    connection_kind: str",
        "    risk: str",
        "    required_capabilities: tuple[str, ...]",
        "    limitations: tuple[str, ...]",
        "",
        "",
        "CATALOG_EXPANSION_V2_ADAPTERS = (",
    ]
    for candidate in payload["candidates"]:
        classification = _classified_adaptation(candidate)
        lines.extend(
            [
                "    CatalogExpansionV2Adapter(",
                f"        project_id={candidate['catalog_id']!r},",
                f"        availability={classification['availability']!r},",
                f"        decision_reason_code={classification['reason_code']!r},",
                f"        adapter_version={classification['adapter_version']!r},",
                f"        adaptation_wave={classification['wave']!r},",
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
        "# MCP 双源目录扩充适配判定",
        "",
        f"审查快照日期：{payload['snapshot_date']}",
        "",
        "> 100 项已经逐项归入 `ready`、`planned` 或 `blocked`。只有通过固定工具、隔离和代表调用门槛的条目可执行。",
        "> 非 ready 条目不包含命令、端点、凭据槽、工具策略或功能开关绕过路径。",
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
            f"- 完成适配判定：{summary['selected']}",
            f"- 覆盖分类：{summary['categories']}",
            f"- 中文源命中：{summary['by_source']['awesome-mcp-zh']}",
            f"- 英文源命中：{summary['by_source']['awesome-mcp-servers']}",
            f"- 本批状态：{payload['adaptation']['availability']['ready']} ready / {payload['adaptation']['availability']['planned']} planned / {payload['adaptation']['availability']['blocked']} blocked",
            "- 新增执行能力：3（Brave、Kagi 与 arXiv 固定只读子集）",
            "",
            "硬门禁：公开仓库存在，未归档/禁用/私有/派生，许可证 SPDX 明确，且最近 12 个月有推送。每个分类最多 15 项，每个仓库最多 2 项。",
            "",
            "## 100 项适配判定",
            "",
            "| 排名 | 目录 ID | 仓库 | 分类 | Stars | 许可证 | 状态 | 原因码 |",
            "| ---: | --- | --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for item in payload["candidates"]:
        github = item["github"]
        license_spdx = github["licenseInfo"]["spdxId"]
        lines.append(
            f"| {item['rank']} | `{item['catalog_id']}` | "
            f"[{github['nameWithOwner']}]({github['url']}) | {item['category']} | "
            f"{github['stargazerCount']} | {license_spdx} | {item['proposed_availability']} | "
            f"`{item['decision_reason_code']}` |"
        )
    lines.extend(
        [
            "",
            "## 执行边界",
            "",
            "官方 Brave Search v2.1.0 复用既有 Token Sidecar；Kagi v1.0.2 与 arxiv-mcp-server v0.6.2 通过固定原生兼容层，仅开放审核后的只读工具。三者均锁定出口、Schema 与输出上限；其余 97 项没有新增执行配置，53 项保留后续受控 facade 规划，44 项因明确安全、身份或宿主边界阻断。",
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
