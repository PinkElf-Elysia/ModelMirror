#!/usr/bin/env python3
"""Integrate the approved Wave 24 review list and later accepted adaptations.

The generator is intentionally offline. Ready entries only record their
reviewed classification; executable contracts and allowlists remain explicit
in the catalog and sidecar modules. Planned/blocked entries never gain a
command, endpoint, credential slot, tool schema, or feature enablement.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = ROOT / "docs" / "mcp-catalog-expansion-wave24" / "review-candidates.json"
REPORT_PATH = ROOT / "docs" / "MCP_CATALOG_EXPANSION_WAVE24_REVIEW.md"
FRONTEND_PATH = ROOT / "client" / "src" / "data" / "mcpCatalogExpansionV3.generated.ts"
BACKEND_PATH = ROOT / "server" / "mcp" / "catalog_expansion_v3.py"

SNAPSHOT_DATE = "2026-08-13"
NON_EXECUTABLE_LIMITATION = (
    "当前仅登记产品身份与适配判定；没有命令、端点、凭据槽、工具策略、运行镜像或默认 allowlist，"
    "任何功能开关都不能使该条目可执行。"
)


def _group(
    *,
    availability: str,
    wave: int,
    reason: str,
    connection_kind: str,
    risk: str,
    requirements: tuple[str, ...],
    capabilities: tuple[str, ...],
    ids: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "availability": availability,
        "wave": wave,
        "reason": reason,
        "connection_kind": connection_kind,
        "risk": risk,
        "requirements": requirements,
        "capabilities": capabilities,
        "ids": frozenset(ids),
    }


DECISION_GROUPS: dict[str, dict[str, Any]] = {
    "ready-wave25-public-read": _group(
        availability="ready",
        wave=25,
        reason=(
            "已冻结上游版本、许可证、固定公共 Host、只读工具 Schema、限流和输出上限，"
            "并通过真实代表调用、拒写、超时、重启、清理与用户验收。"
        ),
        connection_kind="sandboxed-stdio",
        risk="medium",
        requirements=(),
        capabilities=(
            "fixed-egress-policy",
            "read-only-tool-policy",
            "schema-drift-recovery",
            "provider-rate-limit",
        ),
        ids=(
            "coinpaprika-dexpaprika-mcp",
            "pab1it0-chess-mcp",
            "rishijatia-fantasy-pl-mcp",
            "yuna0x0-anilist-mcp",
        ),
    ),
    "planned-wave25-anonymous-public-read-contract": _group(
        availability="planned",
        wave=25,
        reason=(
            "可继续评估匿名固定域名的公开搜索或元数据读取；在冻结上游版本、固定 Host、"
            "只读工具 Schema、限流与输出上限并完成真实代表调用前保持 planned。"
        ),
        connection_kind="sandboxed-stdio",
        risk="medium",
        requirements=(),
        capabilities=(
            "fixed-egress-policy",
            "read-only-tool-policy",
            "schema-drift-recovery",
            "provider-rate-limit",
        ),
        ids=(
            "childrentime-reactuse",
            "tonnode-mcp",
            "karanb192-reddit-mcp-buddy",
            "openaccountants-openaccountants",
            "king-of-the-grackles-reddit-research-mcp",
            "patsnap-patent-literature-search-mcp",
        ),
    ),
    "ready-wave29-arxiv-public-read": _group(
        availability="ready",
        wave=29,
        reason=(
            "已冻结 arXiv LaTeX v0.2.2、官方 export.arxiv.org、四项只读 Schema、"
            "内存源包解析与硬资源上限，并通过真实代表调用、超时、清理和用户验收。"
        ),
        connection_kind="sandboxed-stdio",
        risk="medium",
        requirements=(),
        capabilities=(
            "fixed-egress-policy",
            "read-only-tool-policy",
            "schema-drift-recovery",
            "archive-parser-limits",
        ),
        ids=("takashiishida-arxiv-latex-mcp",),
    ),
    "blocked-provider-data-terms": _group(
        availability="blocked",
        wave=29,
        reason=(
            "上游能力依赖第三方金融数据抓取，无法证明服务条款允许在本产品中稳定转发；"
            "为避免把非官方数据源包装成受支持 API，保持 blocked。"
        ),
        connection_kind="sandboxed-stdio",
        risk="high",
        requirements=("provider-supported-api",),
        capabilities=("data-license-provenance", "provider-terms-review"),
        ids=("narumiruna-yfinance-mcp",),
    ),
    "blocked-wave25-public-backend-requires-embedded-credential": _group(
        availability="blocked",
        wave=25,
        reason=(
            "固定 mcp-nixos v3.0.0 的核心 NixOS 搜索后端对匿名访问返回 401，"
            "上游源码内置了 Basic Auth 凭据。本批不复制这些值、不新增凭据槽、"
            "不更换后端冒充原产品，因此转为 blocked。"
        ),
        connection_kind="sandboxed-stdio",
        risk="high",
        requirements=("token", "remote-transport"),
        capabilities=(
            "provider-supported-anonymous-api",
            "credential-provenance-review",
        ),
        ids=("utensils-mcp-nixos",),
    ),
    "planned-wave26-token-readonly-preflight": _group(
        availability="planned",
        wave=26,
        reason=(
            "可继续评估固定凭据槽与账号资源作用域内的只读子集；在服务端加密凭据、固定出口、"
            "真实只读账号预检和代表调用完成前保持 planned。"
        ),
        connection_kind="sandboxed-stdio",
        risk="high",
        requirements=("token", "account-binding", "remote-transport"),
        capabilities=(
            "encrypted-credential-binding",
            "fixed-egress-policy",
            "read-only-tool-policy",
            "real-account-readonly-preflight",
        ),
        ids=(
            "ergut-mcp-bigquery-server",
            "pspdfkit-nutrient-dws-mcp-server",
            "chanmeng666-server-google-news",
            "polygon-io-mcp-polygon",
            "isnow890-naver-search-mcp",
        ),
    ),
    "ready-wave26a-calculator": _group(
        availability="ready",
        wave=26,
        reason=(
            "已冻结单一 calculate 工具、断网数值 AST、复杂度与结果上限，"
            "并通过真实 UDS、超时回收、默认拒绝和用户验收。"
        ),
        connection_kind="sandboxed-stdio",
        risk="medium",
        requirements=(),
        capabilities=(
            "network-disabled",
            "bounded-runtime-surface",
            "schema-drift-recovery",
            "resource-limits",
        ),
        ids=("githejie-mcp-server-calculator",),
    ),
    "planned-wave26-offline-file-or-deterministic-artifact": _group(
        availability="planned",
        wave=26,
        reason=(
            "可继续评估断网、封存输入、确定性处理与服务端复制产物的文件子集；"
            "在路径隔离、资源限额、超时、清理和真实镜像验收前保持 planned。"
        ),
        connection_kind="sandboxed-stdio",
        risk="medium",
        requirements=("sealed-workspace", "external-runtime"),
        capabilities=(
            "scoped-filesystem",
            "network-disabled",
            "artifact-cleanup",
            "resource-limits",
        ),
        ids=(
            "modelscope-funasr",
            "mckinsey-vizro",
            "openfate-ai-bazi-mcp",
            "healthchainai-healthchain",
            "sunriseapps-imagesorcery-mcp",
            "frowningdev-django-orm-lens",
            "the-momentum-apple-health-mcp-server",
            "yusufkaraaslan-skill-seekers",
            "zinja-coder-apktool-mcp-server",
        ),
    ),
    "blocked-license-runtime-dependency": _group(
        availability="blocked",
        wave=29,
        reason=(
            "PDFMux 1.8.7 本身为 MIT，但固定运行依赖 PyMuPDF 与 pymupdf4llm "
            "均要求 AGPL-3.0 或 Artifex 商业许可；当前没有商业许可授权，不能纳入分发镜像。"
        ),
        connection_kind="sandboxed-stdio",
        risk="high",
        requirements=("commercial-runtime-license",),
        capabilities=("redistributable-runtime-dependencies",),
        ids=("nameetp-pdfmux",),
    ),
    "blocked-license-metadata-conflict": _group(
        availability="blocked",
        wave=29,
        reason=(
            "固定发布物的许可证元数据与仓库声明不一致，当前无法形成可复现的再分发边界；"
            "等待上游统一许可证信息后再重新评估。"
        ),
        connection_kind="sandboxed-stdio",
        risk="high",
        requirements=("license-provenance",),
        capabilities=("consistent-release-license-metadata",),
        ids=("aimino-tech-opendocswork-mcp",),
    ),
    "planned-wave27-native-readonly-data-service": _group(
        availability="planned",
        wave=27,
        reason=(
            "可继续评估固定协议、结构化目标和原生只读账号下的 describe/list/search/read-query 子集；"
            "DSN、管理操作、写查询和动态端点保持禁止，真实服务验收前保持 planned。"
        ),
        connection_kind="sandboxed-stdio",
        risk="high",
        requirements=("database-credentials", "external-runtime"),
        capabilities=(
            "fixed-database-target",
            "native-read-only-role",
            "read-only-query-policy",
            "query-and-output-limits",
        ),
        ids=(
            "dbt-labs-dbt-mcp",
            "planetscale-cli",
            "snowflake-labs-mcp",
            "bintocher-mcp-superset",
            "chroma-core-chroma-mcp",
            "confluentinc-mcp-confluent",
            "traceloop-opentelemetry-mcp-server",
        ),
    ),
    "ready-wave28-greptimedb-readonly": _group(
        availability="ready",
        wave=28,
        reason=(
            "已冻结 GreptimeDB v0.5.1、固定数据库/表/列、只读工具 Schema 与服务端生成查询，"
            "并通过真实服务代表调用、超时、断开、清理和用户验收。"
        ),
        connection_kind="sandboxed-stdio",
        risk="high",
        requirements=("database-credentials", "external-runtime"),
        capabilities=(
            "fixed-database-target",
            "native-read-only-role",
            "read-only-query-policy",
            "query-and-output-limits",
        ),
        ids=("greptimeteam-greptimedb-mcp-server",),
    ),
    "ready-wave30-victoriametrics-readonly": _group(
        availability="ready",
        wave=30,
        reason=(
            "已冻结 VictoriaMetrics MCP v1.20.2、固定指标只读工具 Schema、固定目标和查询/输出上限，"
            "并通过真实服务代表调用、超时、断开、重启、清理与用户验收。"
        ),
        connection_kind="sandboxed-stdio",
        risk="high",
        requirements=("database-credentials", "external-runtime"),
        capabilities=(
            "fixed-database-target",
            "native-read-only-role",
            "read-only-query-policy",
            "query-and-output-limits",
        ),
        ids=("victoriametrics-community-mcp-victoriametrics",),
    ),
    "planned-wave21-stateful-foundation-required": _group(
        availability="planned",
        wave=21,
        reason=(
            "该产品依赖项目级持久化、保留/导出/删除、容量或模型费用配额及一次性写审批；"
            "Wave 21 基础完成前保持 planned，当前不创建运行时。"
        ),
        connection_kind="sandboxed-stdio",
        risk="high",
        requirements=("external-runtime",),
        capabilities=(
            "project-scoped-persistence",
            "retention-export-delete-policy",
            "storage-and-model-cost-quota",
            "one-shot-write-approval",
        ),
        ids=(
            "supermemoryai-supermemory",
            "nkapila6-mcp-local-rag",
            "kzino-vorim-mcp-server",
            "chemiguel23-memorymesh",
            "codeabra-iai-personal-memory-engine",
            "mnemox-ai-tradememory-protocol",
            "riponcm-projectmem",
        ),
    ),
    "blocked-superseded-existing-controlled-capability": _group(
        availability="blocked",
        wave=24,
        reason=(
            "该实现与已存在的受控浏览器、搜索、文件或代码索引能力重复，且不会提供更窄、更可验证的边界；"
            "转为 blocked/superseded，不创建第二套运行时。"
        ),
        connection_kind="sandboxed-stdio",
        risk="medium",
        requirements=("external-runtime",),
        capabilities=("maintained-upstream-contract", "bounded-runtime-surface"),
        ids=(
            "executeautomation-mcp-playwright",
            "muvon-octocode",
            "secretiveshell-mcp-searxng",
            "repowise-dev-repowise",
            "bgauryy-octocode-mcp",
            "mark3labs-mcp-filesystem-server",
            "zubeidhendricks-youtube-mcp-server",
            "anaisbetts-mcp-youtube",
        ),
    ),
    "blocked-arbitrary-command-code-or-target": _group(
        availability="blocked",
        wave=24,
        reason=(
            "上游产品身份依赖任意命令、代码、包、仓库或网络目标；收窄后无法保持产品身份，"
            "因此不接入通用执行器并保持 blocked。"
        ),
        connection_kind="sandboxed-stdio",
        risk="high",
        requirements=("external-runtime", "system-permission"),
        capabilities=("ephemeral-code-sandbox", "terminal-action-approval"),
        ids=(
            "yepcode-mcp-server-js",
            "tumf-mcp-shell-server",
            "zcaceres-fetch-mcp",
            "flytohub-flyto-core",
            "pydantic-pydantic-ai",
            "rusiaaman-wcgw",
        ),
    ),
    "blocked-desktop-browser-or-device-control": _group(
        availability="blocked",
        wave=24,
        reason=(
            "上游依赖桌面宿主、浏览器自动化、IDE/模拟器、设备控制或任意宿主路径；"
            "这些能力在多租户桌面宿主边界完成前继续冻结。"
        ),
        connection_kind="desktop-bridge",
        risk="high",
        requirements=("desktop-host", "system-permission"),
        capabilities=("trusted-desktop-host", "per-user-process-isolation"),
        ids=(
            "fradser-mcp-server-apple-reminders",
            "joshuayoes-ios-simulator-mcp",
            "callstackincubator-agent-device",
            "higangssh-homebutler",
            "anki-mcp-anki-mcp-desktop",
            "lvcidpsyche-auto-browser",
            "writerslogic-scrivener-mcp",
            "kunwar-shah-claudex",
            "jtang613-ghidrassistmcp",
            "nakaokarei-swift-mcp-gui",
            "ndthanhdev-mcp-browser-kit",
            "yurineko73-godot-mcp-native",
            "ai-xiaodao-ai-browser-mcp",
        ),
    ),
    "blocked-account-cloud-write-or-management": _group(
        availability="blocked",
        wave=24,
        reason=(
            "上游核心工具包含账号发布、资源写入、基础设施管理、通讯发送或金融操作；"
            "即使未来补齐 OAuth/多租户，只读之外的能力仍保持 blocked。"
        ),
        connection_kind="remote-mcp",
        risk="high",
        requirements=("oauth", "account-binding", "remote-transport"),
        capabilities=("authenticated-user-context", "tenant-isolation", "write-approval"),
        ids=(
            "rohitg00-kubectl-mcp-server",
            "zcaceres-gtasks-mcp",
            "weibaohui-k8m",
            "zenml-io-mcp-zenml",
            "apollographql-apollo-mcp-server",
            "eat-pray-ai-yutu",
            "freema-openclaw-mcp",
            "korotovsky-slack-mcp-server",
            "mobilereality-mdma",
            "gomarble-ai-facebook-ads-mcp-server",
            "rashidazarang-airtable-mcp",
            "xeroapi-xero-mcp-server",
            "agenticmail-agenticmail",
            "openmf-mcp-mifosx",
            "overpod-mcp-telegram",
            "portainer-portainer-mcp",
            "quackbackio-quackback",
            "stape-io-google-tag-manager-mcp-server",
            "workopia-workopia-mcp",
            "yuna0x0-hackmd-mcp",
        ),
    ),
    "blocked-paid-generation-transaction-or-wallet": _group(
        availability="blocked",
        wave=24,
        reason=(
            "上游核心能力会触发付费生成、账号余额、交易、钱包或外部模型执行；"
            "该类高后果副作用不纳入本阶段适配。"
        ),
        connection_kind="remote-mcp",
        risk="high",
        requirements=("token", "account-binding", "remote-transport"),
        capabilities=("billing-guard", "transaction-approval", "unknown-outcome-recovery"),
        ids=(
            "apinetwork-piapi-mcp-server",
            "jau123-meigen-ai-design-mcp",
            "runapi-ai-mcp",
            "scottcjn-rustchain-mcp",
            "stabgan-openrouter-mcp-multimodal",
        ),
    ),
    "blocked-dynamic-integration-or-security-control-plane": _group(
        availability="blocked",
        wave=24,
        reason=(
            "上游是动态集成/安全控制面，工具、目标或运行服务会随外部配置扩张；"
            "无法冻结为固定产品契约，保持 blocked。"
        ),
        connection_kind="remote-mcp",
        risk="high",
        requirements=("external-runtime", "system-permission"),
        capabilities=("fixed-control-plane", "tenant-isolation", "bounded-runtime-surface"),
        ids=("klavis-ai-klavis", "mariocandela-beelzebub"),
    ),
}


def _slug(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


def _catalog_id(candidate: dict[str, Any]) -> str:
    repo_name = str(candidate.get("repo_name") or "")
    parts = repo_name.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"invalid repository identity: {repo_name!r}")
    return f"{_slug(parts[0])}-{_slug(parts[1])}"


def _display_name(candidate: dict[str, Any]) -> str:
    raw = str(candidate.get("name") or "").strip()
    if raw and "/" not in raw and not raw.endswith(")"):
        return raw
    return str(candidate["repo_name"]).split("/", 1)[1].replace("_", " ").replace("-", " ").title()


def _decision_index() -> dict[str, tuple[str, dict[str, Any]]]:
    index: dict[str, tuple[str, dict[str, Any]]] = {}
    for reason_code, group in DECISION_GROUPS.items():
        for project_id in group["ids"]:
            if project_id in index:
                raise ValueError(f"duplicate decision id: {project_id}")
            index[project_id] = (reason_code, group)
    return index


def build_approved_payload(source: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(source)
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 100:
        raise ValueError("Wave 24 review snapshot must contain exactly 100 candidates")
    decision_index = _decision_index()
    generated_ids: set[str] = set()
    for expected_rank, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict) or candidate.get("rank") != expected_rank:
            raise ValueError("Wave 24 candidates must retain deterministic rank order")
        project_id = _catalog_id(candidate)
        if project_id in generated_ids:
            raise ValueError(f"duplicate Wave 24 catalog id: {project_id}")
        generated_ids.add(project_id)
        decision = decision_index.get(project_id)
        if decision is None:
            raise ValueError(f"missing Wave 24 decision for {project_id}")
        reason_code, group = decision
        decision = {
            "ready": "accepted-ready",
            "planned": "deferred-planned",
            "blocked": "blocked",
        }[group["availability"]]
        candidate.update(
            {
                "catalog_id": project_id,
                "decision": decision,
                "proposed_availability": group["availability"],
                "decision_reason_code": reason_code,
                "decision_reason": group["reason"],
                "adapter_version": "",
                "adaptation_wave": group["wave"],
            }
        )
    missing = set(decision_index) - generated_ids
    if missing:
        raise ValueError(f"Wave 24 decisions reference unknown ids: {sorted(missing)}")
    availability = {
        status: sum(item["proposed_availability"] == status for item in candidates)
        for status in ("ready", "planned", "blocked")
    }
    if availability != {"ready": 8, "planned": 34, "blocked": 58}:
        raise ValueError(f"unexpected Wave 24 classification: {availability}")
    payload.update(
        {
            "purpose": "adaptation-classification",
            "runtime_catalog_changed": True,
            "runtime_execution_changed": True,
            "approval": {
                "status": "approved",
                "approved_at": SNAPSHOT_DATE,
                "scope": "static-catalog-plus-accepted-wave25-wave30-runtime",
            },
            "adaptation": {
                "classified_at": SNAPSHOT_DATE,
                "classified_count": 100,
                "availability": availability,
                "ready_boundary": "per-adapter-runtime-evidence-and-user-acceptance",
                "non_ready_boundary": "no-command-endpoint-credential-tool-policy-or-allowlist",
            },
        }
    )
    return payload


def _frontend_record(candidate: dict[str, Any]) -> dict[str, Any]:
    reason_code, group = _decision_index()[candidate["catalog_id"]]
    github = candidate.get("github") or {}
    language = ((github.get("primaryLanguage") or {}).get("name") or "Unknown")
    license_id = ((github.get("licenseInfo") or {}).get("spdxId") or "NOASSERTION")
    availability = group["availability"]
    limitations = [group["reason"]]
    if availability != "ready":
        limitations.append(NON_EXECUTABLE_LIMITATION)
    return {
        "id": candidate["catalog_id"],
        "name": _display_name(candidate),
        "repoName": candidate["repo_name"],
        "repoUrl": candidate["repo_url"],
        "category": candidate["category"],
        "description": str(candidate.get("description") or "").strip(),
        "readmeSummary": (
            f"{candidate['repo_name']} 已通过 Wave 24 双源公开仓库、许可证和维护门禁；"
            f"当前判定为 {availability}，原因码 {reason_code}。"
        ),
        "stars": int(github.get("stargazerCount") or 0),
        "language": language,
        "verifiedAt": SNAPSHOT_DATE,
        "tags": [candidate["category"], language, license_id],
        "requirements": list(group["requirements"]),
        "usageExamples": [
            f"查看 {_display_name(candidate)} 的上游用途和当前适配判定",
            "完成对应安全门槛后再进行隔离连接和代表调用验收",
        ],
        "sources": sorted({item["source_id"] for item in candidate["sources"]}),
        "adaptation": {
            "wave": group["wave"],
            "availability": availability,
            "connectionKind": group["connection_kind"],
            "risk": group["risk"],
            "requiredCapabilities": list(group["capabilities"]),
            "limitations": limitations,
        },
    }


def render_frontend(payload: dict[str, Any]) -> str:
    records = [_frontend_record(candidate) for candidate in payload["candidates"]]
    body = json.dumps(records, ensure_ascii=False, indent=2)
    return (
        "// Generated by scripts/mcp_catalog_integrate_wave24.py; do not edit manually.\n"
        "// These records contain classification only; runtime contracts remain server-owned.\n\n"
        f"export const mcpCatalogExpansionV3 = {body} as const;\n"
    )


def _py(value: Any) -> str:
    if isinstance(value, tuple):
        if not value:
            return "()"
        inner = ", ".join(repr(item) for item in value)
        return f"({inner},)" if len(value) == 1 else f"({inner})"
    return repr(value)


def render_backend(payload: dict[str, Any]) -> str:
    lines = [
        '"""Generated classifications for the approved Wave 24 expansion."""',
        "",
        "from __future__ import annotations",
        "",
        "from dataclasses import dataclass",
        "",
        "",
        "@dataclass(frozen=True, slots=True)",
        "class CatalogExpansionV3Adapter:",
        "    project_id: str",
        "    availability: str",
        "    decision_reason_code: str",
        "    adaptation_wave: int",
        "    connection_kind: str",
        "    risk: str",
        "    required_capabilities: tuple[str, ...]",
        "    limitations: tuple[str, ...]",
        "",
        "",
        "CATALOG_EXPANSION_V3_ADAPTERS = (",
    ]
    decision_index = _decision_index()
    for candidate in payload["candidates"]:
        reason_code, group = decision_index[candidate["catalog_id"]]
        lines.extend(
            [
                "    CatalogExpansionV3Adapter(",
                f"        project_id={candidate['catalog_id']!r},",
                f"        availability={group['availability']!r},",
                f"        decision_reason_code={reason_code!r},",
                f"        adaptation_wave={group['wave']},",
                f"        connection_kind={group['connection_kind']!r},",
                f"        risk={group['risk']!r},",
                f"        required_capabilities={_py(group['capabilities'])},",
                f"        limitations={_py((group['reason'],) if group['availability'] == 'ready' else (group['reason'], NON_EXECUTABLE_LIMITATION))},",
                "    ),",
            ]
        )
    lines.extend([")", ""])
    return "\n".join(lines)


def render_report(payload: dict[str, Any]) -> str:
    availability = payload["adaptation"]["availability"]
    lines = [
        "# MCP 目录扩充 Wave 24 判定",
        "",
        f"快照日期：{payload['snapshot_date']}",
        "",
        "## 结论",
        "",
        "- 用户已批准本次固定 100 项清单进入静态产品目录。",
        f"- 判定：`{availability['ready']} ready / {availability['planned']} planned / {availability['blocked']} blocked`。",
        "- Wave 24 首次导入不创建 ready；后续只有完成真实运行证据与用户验收的精确 ID 才能晋级。",
        "- Wave 25–30 已完成验收的八项已晋级；命令、工具策略与 allowlist 仍由服务端显式冻结，不由生成器产生。",
        "- 与原有 200 项合并后产品目录总数保持 300。",
        "",
        "## 固定来源",
        "",
    ]
    for source in payload["source_snapshots"]:
        lines.append(
            f"- `{source['repository']}@{source['commit']}`，README SHA256 `{source['readme_sha256']}`。"
        )
    lines.extend(
        [
            "",
            "## 判定清单",
            "",
            "| 排名 | 项目 | 类别 | 状态 | 目标批次/原因码 |",
            "| ---: | --- | --- | --- | --- |",
        ]
    )
    for candidate in payload["candidates"]:
        lines.append(
            f"| {candidate['rank']} | `{candidate['catalog_id']}` | {candidate['category']} | "
            f"{candidate['proposed_availability']} | Wave {candidate['adaptation_wave']} / "
            f"`{candidate['decision_reason_code']}` |"
        )
    lines.extend(
        [
            "",
            "## 回退",
            "",
            "Wave 25–30 回退时移除对应精确 allowlist/runtime contract 并将其恢复为 planned/blocked；目录导入本身没有数据迁移。",
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
    payload = build_approved_payload(source)
    review_text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    _write_or_check(REVIEW_PATH, review_text, check=args.check)
    _write_or_check(REPORT_PATH, render_report(payload), check=args.check)
    _write_or_check(FRONTEND_PATH, render_frontend(payload), check=args.check)
    _write_or_check(BACKEND_PATH, render_backend(payload), check=args.check)
    print(json.dumps(payload["adaptation"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
