#!/usr/bin/env python3
"""Enrich a parsed MCP inventory with read-only GitHub repository metadata.

This is an explicit maintainer command, not a CI/network dependency. It invokes
the authenticated ``gh api graphql`` client in bounded batches and writes only
public repository metadata. Candidate selection remains review-only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence


SNAPSHOT_DATE = dt.date(2026, 8, 9)
ACTIVE_SINCE = SNAPSHOT_DATE - dt.timedelta(days=365)
TARGET_COUNT = 100
SOURCE_MINIMUM = 25
CATEGORY_MAXIMUM = 15
CATEGORY_MINIMUM = 10
REPOSITORY_MAXIMUM = 2
BATCH_SIZE = 40


MCP_EVIDENCE_RE = re.compile(r"\b(?:model context protocol|mcp server)\b", re.I)
INSTALL_EVIDENCE_RE = re.compile(r"\b(?:npx|npm|pipx?|uvx|docker|stdio|streamable|sse)\b", re.I)
NON_SERVER_METADATA_PATTERNS = (
    re.compile(r"\bmcp\s+(?:aggregator|router)\b", re.I),
    re.compile(r"\b(?:manage|manages|managing)\s+(?:your\s+)?mcp\s+(?:connections|servers)\b", re.I),
    re.compile(r"\b(?:testing|test|inspector for)\s+mcp\s+servers\b", re.I),
    re.compile(r"\b(?:install|manage)\s+and\s+manage\s+mcp\s+servers\b", re.I),
    re.compile(r"\bweb\s+ui\s+to\s+(?:install\s+and\s+)?manage\s+mcp\b", re.I),
)


def _repo_parts(repo_name: str) -> tuple[str, str]:
    owner, separator, name = repo_name.partition("/")
    if not separator or not owner or not name or "/" in name:
        raise ValueError(f"invalid GitHub repository name: {repo_name}")
    return owner, name


def build_query(repo_names: Sequence[str]) -> str:
    fields: list[str] = []
    for index, repo_name in enumerate(repo_names):
        owner, name = _repo_parts(repo_name)
        owner_json = json.dumps(owner)
        name_json = json.dumps(name)
        fields.append(
            f"r{index}: repository(owner: {owner_json}, name: {name_json}) {{"
            " nameWithOwner url isArchived isDisabled isPrivate isFork "
            " stargazerCount pushedAt updatedAt description homepageUrl "
            " owner { __typename } primaryLanguage { name } licenseInfo { spdxId name } "
            " defaultBranchRef { target { ... on Commit { oid } } } "
            " latestRelease { tagName publishedAt }"
            " }"
        )
    return "query ModelMirrorMcpCatalogAudit {" + " ".join(fields) + " rateLimit { remaining cost } }"


def query_batch(repo_names: Sequence[str]) -> tuple[dict[str, object], dict[str, int]]:
    query = build_query(repo_names)
    completed = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    # GitHub returns exit code 1 when any repository alias is missing, while
    # still returning valid public metadata for every other alias. Treat the
    # missing alias as a normal not-found hard-gate instead of discarding the
    # entire bounded batch. Raw stderr is intentionally never emitted.
    if not completed.stdout.strip():
        raise RuntimeError("GitHub GraphQL metadata request failed")
    payload = json.loads(completed.stdout)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("GitHub GraphQL response did not contain data")
    rate_limit = data.pop("rateLimit", {})
    if not isinstance(rate_limit, dict):
        rate_limit = {}
    results = {
        repo_name.lower(): data.get(f"r{index}")
        for index, repo_name in enumerate(repo_names)
    }
    return results, {
        "remaining": int(rate_limit.get("remaining", -1)),
        "cost": int(rate_limit.get("cost", -1)),
    }


def _parse_date(value: object) -> dt.date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def hard_exclusion_reason(metadata: object) -> str | None:
    if not isinstance(metadata, dict):
        return "repository-not-found"
    if metadata.get("isPrivate"):
        return "repository-private"
    if metadata.get("isArchived"):
        return "repository-archived"
    if metadata.get("isDisabled"):
        return "repository-disabled"
    if metadata.get("isFork"):
        return "repository-fork"
    license_info = metadata.get("licenseInfo")
    spdx_id = license_info.get("spdxId") if isinstance(license_info, dict) else None
    if not spdx_id or spdx_id in {"NOASSERTION", "OTHER", "NONE"}:
        return "license-undetermined"
    pushed_at = _parse_date(metadata.get("pushedAt"))
    if pushed_at is None or pushed_at < ACTIVE_SINCE:
        return "repository-inactive-over-12-months"
    return None


def non_server_metadata_reason(candidate: dict[str, object], metadata: object) -> str | None:
    if not isinstance(metadata, dict):
        return None
    searchable = " ".join(
        str(value or "")
        for value in (
            candidate.get("name"),
            candidate.get("description"),
            metadata.get("description"),
        )
    )
    if any(pattern.search(searchable) for pattern in NON_SERVER_METADATA_PATTERNS):
        return "non-server-entry:aggregator-router-client-or-manager"
    return None


def score_candidate(candidate: dict[str, object], metadata: dict[str, object]) -> tuple[int, dict[str, int]]:
    stars = int(metadata.get("stargazerCount") or 0)
    pushed_at = _parse_date(metadata.get("pushedAt")) or dt.date.min
    age_days = max(0, (SNAPSHOT_DATE - pushed_at).days)
    source_count = len(candidate.get("sources") or [])
    description = " ".join(
        str(value or "")
        for value in (candidate.get("description"), metadata.get("description"))
    )
    components = {
        "cross_source": 15 if source_count >= 2 else 8,
        "popularity": min(25, max(0, int(math.log10(stars + 1) * 7))),
        "maintenance": 20 if age_days <= 90 else 15 if age_days <= 180 else 10,
        "license": 15,
        "organization_owner": 5
        if isinstance(metadata.get("owner"), dict)
        and metadata["owner"].get("__typename") == "Organization"
        else 2,
        "mcp_evidence": 10 if MCP_EVIDENCE_RE.search(description) else 4,
        "install_clarity": 10 if INSTALL_EVIDENCE_RE.search(description) else 3,
    }
    return sum(components.values()), components


def enrich_inventory(
    inventory: dict[str, object],
    metadata_by_repo: dict[str, object],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    candidates = inventory.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("inventory candidates must be a list")
    for raw_candidate in candidates:
        if not isinstance(raw_candidate, dict) or raw_candidate.get("prefilter_status") != "eligible":
            continue
        candidate = dict(raw_candidate)
        repo_name = str(candidate["repo_name"])
        metadata = metadata_by_repo.get(repo_name.lower())
        reason = hard_exclusion_reason(metadata) or non_server_metadata_reason(candidate, metadata)
        candidate["github"] = metadata
        candidate["review_status"] = "excluded" if reason else "eligible"
        candidate["review_reason"] = reason
        if reason is None:
            assert isinstance(metadata, dict)
            score, components = score_candidate(candidate, metadata)
            candidate["score"] = score
            candidate["score_components"] = components
        else:
            candidate["score"] = 0
            candidate["score_components"] = {}
        result.append(candidate)
    return sorted(
        result,
        key=lambda item: (-int(item["score"]), str(item["canonical_key"])),
    )


def _source_ids(candidate: dict[str, object]) -> set[str]:
    sources = candidate.get("sources") or []
    return {
        str(source.get("source_id"))
        for source in sources
        if isinstance(source, dict) and source.get("source_id")
    }


def select_review_candidates(
    candidates: Iterable[dict[str, object]],
    *,
    target_count: int = TARGET_COUNT,
    source_minimum: int = SOURCE_MINIMUM,
    category_maximum: int = CATEGORY_MAXIMUM,
    category_minimum: int = CATEGORY_MINIMUM,
    repository_maximum: int = REPOSITORY_MAXIMUM,
) -> list[dict[str, object]]:
    eligible = [item for item in candidates if item.get("review_status") == "eligible"]
    selected: list[dict[str, object]] = []
    selected_keys: set[str] = set()
    category_counts: dict[str, int] = {}
    repository_counts: dict[str, int] = {}

    def can_add(item: dict[str, object]) -> bool:
        key = str(item["canonical_key"])
        category = str(item["category"])
        repo_name = str(item["repo_name"]).lower()
        return (
            key not in selected_keys
            and category_counts.get(category, 0) < category_maximum
            and repository_counts.get(repo_name, 0) < repository_maximum
        )

    def add(item: dict[str, object]) -> None:
        key = str(item["canonical_key"])
        category = str(item["category"])
        repo_name = str(item["repo_name"]).lower()
        selected.append(item)
        selected_keys.add(key)
        category_counts[category] = category_counts.get(category, 0) + 1
        repository_counts[repo_name] = repository_counts.get(repo_name, 0) + 1

    for source_id in ("awesome-mcp-zh", "awesome-mcp-servers"):
        for item in eligible:
            if sum(source_id in _source_ids(chosen) for chosen in selected) >= source_minimum:
                break
            if source_id in _source_ids(item) and can_add(item):
                add(item)

    # Ensure category breadth before filling by score.
    represented = set(category_counts)
    for item in eligible:
        if len(represented) >= category_minimum:
            break
        category = str(item["category"])
        if category not in represented and can_add(item):
            add(item)
            represented.add(category)

    for item in eligible:
        if len(selected) >= target_count:
            break
        if can_add(item):
            add(item)

    if len(selected) != target_count:
        raise ValueError(f"only {len(selected)} candidates satisfy selection constraints")
    if len(category_counts) < category_minimum:
        raise ValueError("candidate selection does not meet category breadth")
    for source_id in ("awesome-mcp-zh", "awesome-mcp-servers"):
        count = sum(source_id in _source_ids(item) for item in selected)
        if count < source_minimum:
            raise ValueError(f"candidate selection does not meet {source_id} minimum")

    review_items: list[dict[str, object]] = []
    for rank, item in enumerate(selected, start=1):
        review_items.append(
            {
                "rank": rank,
                "canonical_key": item["canonical_key"],
                "repo_name": item["repo_name"],
                "repo_url": item["repo_url"],
                "subpath": item.get("subpath"),
                "name": item["name"],
                "category": item["category"],
                "description": item["description"],
                "sources": item["sources"],
                "score": item["score"],
                "score_components": item["score_components"],
                "github": item["github"],
                "decision": "pending-human-review",
                "proposed_availability": "planned",
                "decision_reason": None,
            }
        )
    return review_items


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def render_review_report(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    policy = payload["selection_policy"]
    sources = payload["source_snapshots"]
    candidates = payload["candidates"]
    assert isinstance(summary, dict)
    assert isinstance(policy, dict)
    assert isinstance(sources, list)
    assert isinstance(candidates, list)
    lines = [
        "# MCP 双源目录扩充候选审查",
        "",
        f"审查快照日期：{payload['snapshot_date']}",
        "",
        "> 本报告只用于人工审查。候选尚未写入产品目录，均不可安装、连接或执行。",
        "",
        "## 固定来源",
        "",
    ]
    for source in sources:
        assert isinstance(source, dict)
        lines.append(
            f"- `{source['source_id']}`：`{source['commit']}`，README SHA-256 "
            f"`{source['readme_sha256']}`"
        )
    lines.extend(
        [
            "",
            "## 结果",
            "",
            f"- 上游解析记录：{payload['upstream_inventory_summary']['parsed_entries']}",
            f"- 唯一仓库/子包：{payload['upstream_inventory_summary']['unique_repositories']}",
            f"- 结构预筛后的新候选：{payload['upstream_inventory_summary']['eligible_new_candidates']}",
            f"- 进入人工复核：{summary['selected']}",
            f"- 覆盖分类：{summary['categories']}",
            f"- 中文源命中：{summary['by_source']['awesome-mcp-zh']}",
            f"- 英文源命中：{summary['by_source']['awesome-mcp-servers']}",
            "",
            "硬门禁：公开仓库存在，未归档/禁用/私有/派生，许可证 SPDX 明确，且最近 12 个月有推送。"
            f" 每个分类最多 {policy['maximum_per_category']} 项，每个仓库最多 "
            f"{policy['maximum_per_repository']} 项。",
            "",
            "## 待人工复核的 100 项",
            "",
            "| 排名 | 仓库 | 分类 | 分数 | Stars | 许可证 | 来源 | 状态 |",
            "| ---: | --- | --- | ---: | ---: | --- | --- | --- |",
        ]
    )
    for item in candidates:
        assert isinstance(item, dict)
        github = item["github"]
        assert isinstance(github, dict)
        license_info = github["licenseInfo"]
        assert isinstance(license_info, dict)
        source_ids = ", ".join(
            str(source["source_id"])
            for source in item["sources"]
            if isinstance(source, dict)
        )
        lines.append(
            f"| {item['rank']} | [{item['repo_name']}]({item['repo_url']}) | "
            f"{item['category']} | {item['score']} | {github['stargazerCount']} | "
            f"{license_info['spdxId']} | {source_ids} | 待复核 |"
        )
    lines.extend(
        [
            "",
            "## 人工门禁",
            "",
            "逐项确认它确为 MCP Server、安装与传输契约明确、名称/用途/分类准确，并给出 "
            "`planned` 或有固定原因的 `blocked` 结论。任何失败项从候补池按同一规则递补；"
            "未经批准不得进入运行时目录。",
            "",
        ]
    )
    return "\n".join(lines)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--metadata-output", required=True, type=Path)
    parser.add_argument("--review-output", required=True, type=Path)
    parser.add_argument("--report-output", required=True, type=Path)
    parser.add_argument(
        "--metadata-input",
        type=Path,
        help="Optional prior enrichment output; only missing repositories are queried.",
    )
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.batch_size < 1 or args.batch_size > 50:
        raise ValueError("batch size must be between 1 and 50")
    inventory = _read_json(args.inventory)
    raw_candidates = inventory.get("candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError("inventory candidates must be a list")
    candidate_repo_names = {
        str(item["repo_name"])
        for item in raw_candidates
        if isinstance(item, dict) and item.get("prefilter_status") == "eligible"
    }
    existing_repo_names = {
        str(value)
        for value in inventory.get("existing_catalog_repo_names", [])
        if isinstance(value, str)
    }
    repo_names = sorted(
        {
            *candidate_repo_names,
            *existing_repo_names,
        },
        key=str.lower,
    )
    metadata: dict[str, object] = {}
    if args.metadata_input:
        cached_payload = _read_json(args.metadata_input)
        cached_candidates = cached_payload.get("candidates")
        if not isinstance(cached_candidates, list):
            raise ValueError("metadata input candidates must be a list")
        for item in cached_candidates:
            if isinstance(item, dict) and isinstance(item.get("repo_name"), str):
                metadata[item["repo_name"].lower()] = item.get("github")
    last_rate = {"remaining": -1, "cost": -1}
    pending_repo_names = [name for name in repo_names if name.lower() not in metadata]
    total_batches = math.ceil(len(pending_repo_names) / args.batch_size)
    for offset in range(0, len(pending_repo_names), args.batch_size):
        batch = pending_repo_names[offset : offset + args.batch_size]
        values, last_rate = query_batch(batch)
        metadata.update(values)
        batch_number = offset // args.batch_size + 1
        print(
            f"github_metadata_batch={batch_number}/{total_batches} "
            f"remaining={last_rate['remaining']} cost={last_rate['cost']}",
            file=sys.stderr,
            flush=True,
        )
    existing_canonical_names = {
        str(value["nameWithOwner"]).lower()
        for repo_name, value in metadata.items()
        if repo_name in {name.lower() for name in existing_repo_names}
        and isinstance(value, dict)
        and value.get("nameWithOwner")
    }
    enriched = enrich_inventory(inventory, metadata)
    for item in enriched:
        github = item.get("github")
        canonical_name = (
            str(github.get("nameWithOwner", "")).lower()
            if isinstance(github, dict)
            else ""
        )
        if canonical_name and canonical_name in existing_canonical_names:
            item["review_status"] = "excluded"
            item["review_reason"] = "existing-catalog-redirect-or-family"
            item["score"] = 0
            item["score_components"] = {}
    selected = select_review_candidates(enriched)
    metadata_payload = {
        "schema_version": 1,
        "snapshot_date": SNAPSHOT_DATE.isoformat(),
        "active_since": ACTIVE_SINCE.isoformat(),
        "purpose": "discovery-audit-only",
        "runtime_catalog_changed": False,
        "github_rate_limit_after": last_rate,
        "summary": {
            "queried_repositories": len(repo_names),
            "network_queries_this_run": len(pending_repo_names),
            "eligible_after_hard_gates": sum(
                item["review_status"] == "eligible" for item in enriched
            ),
            "excluded_after_hard_gates": sum(
                item["review_status"] == "excluded" for item in enriched
            ),
        },
        "candidates": enriched,
    }
    review_payload = {
        "schema_version": 1,
        "snapshot_date": SNAPSHOT_DATE.isoformat(),
        "purpose": "human-review-gate",
        "runtime_catalog_changed": False,
        "source_snapshots": inventory["sources"],
        "upstream_inventory_summary": inventory["summary"],
        "selection_policy": {
            "target_count": TARGET_COUNT,
            "minimum_per_source": SOURCE_MINIMUM,
            "maximum_per_category": CATEGORY_MAXIMUM,
            "maximum_per_repository": REPOSITORY_MAXIMUM,
            "minimum_categories": CATEGORY_MINIMUM,
            "hard_gates": [
                "public repository exists",
                "not archived, disabled, private, or a fork",
                "license SPDX identifier is known",
                f"pushed on or after {ACTIVE_SINCE.isoformat()}",
            ],
        },
        "summary": {
            "selected": len(selected),
            "categories": len({item["category"] for item in selected}),
            "by_source": {
                source_id: sum(source_id in _source_ids(item) for item in selected)
                for source_id in ("awesome-mcp-zh", "awesome-mcp-servers")
            },
        },
        "candidates": selected,
    }
    _write_json(args.metadata_output, metadata_payload)
    _write_json(args.review_output, review_payload)
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.write_text(
        render_review_report(review_payload),
        encoding="utf-8",
        newline="\n",
    )
    print(
        "mcp_catalog_github_enrich=ok "
        f"queried={len(repo_names)} selected={len(selected)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
