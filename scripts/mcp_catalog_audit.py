#!/usr/bin/env python3
"""Build a deterministic, non-runtime MCP catalog source inventory.

The upstream awesome lists are discovery inputs only. This tool deliberately
parses only their server implementation sections, normalizes GitHub repository
identity, merges cross-list duplicates, and marks obvious non-server entries as
excluded. It never changes the executable MCP catalog.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import unquote, urlsplit


SNAPSHOT_DATE = "2026-08-11"


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    repository: str
    commit: str
    readme_sha256: str
    section_start: str
    section_end: str

    @property
    def source_url(self) -> str:
        return f"https://github.com/{self.repository}/tree/{self.commit}"


SOURCE_SPECS = {
    "awesome-mcp-zh": SourceSpec(
        source_id="awesome-mcp-zh",
        repository="yzfly/Awesome-MCP-ZH",
        commit="b29e114d95fa26338b092423fd1ede1e5598e4df",
        readme_sha256="854802528cb508a6f6d00e2d142b57a44bc5393bfd4321ddd96e1e9a2b10b51a",
        section_start="## MCP 服务器精选列表",
        section_end="## MCP 更多玩法",
    ),
    "awesome-mcp-servers": SourceSpec(
        source_id="awesome-mcp-servers",
        repository="punkpeye/awesome-mcp-servers",
        commit="cbcdf8f7700cfe4c0ef9aeb232f64aeebe8a184c",
        readme_sha256="d7012abf5a5019f2ff0b66dff3832b2b0c1e8c9dd672f382f3ae677d3b878874",
        section_start="## Server Implementations",
        section_end="## Frameworks",
    ),
}


CURRENT_CATEGORIES = (
    "浏览器与网页",
    "开发与代码",
    "版本控制",
    "数据库",
    "文件与存储",
    "数据分析",
    "效率与协作",
    "多媒体",
    "电商经营",
    "知识与记忆",
    "安全分析",
    "地理与出行",
    "通用工具",
    "云平台与运维",
    "搜索与研究",
    "通讯与协作",
    "金融与市场",
    "社交与内容",
)


EN_CATEGORY_MAP = {
    "aerospace-and-astrodynamics": "搜索与研究",
    "agreements--coordination": "效率与协作",
    "art-and-culture": "多媒体",
    "architecture-and-design": "开发与代码",
    "bio": "搜索与研究",
    "browser-automation": "浏览器与网页",
    "cloud-platforms": "云平台与运维",
    "code-execution": "开发与代码",
    "coding-agents": "开发与代码",
    "command-line": "开发与代码",
    "communication": "通讯与协作",
    "conversational-ai": "通用工具",
    "cryptography": "安全分析",
    "customer-data-platforms": "数据分析",
    "databases": "数据库",
    "data-platforms": "数据分析",
    "developer-tools": "开发与代码",
    "delivery": "云平台与运维",
    "data-science-tools": "数据分析",
    "data-visualization": "数据分析",
    "embedded-system": "通用工具",
    "education": "知识与记忆",
    "e-commerce": "电商经营",
    "environment-and-nature": "搜索与研究",
    "file-systems": "文件与存储",
    "finance--fintech": "金融与市场",
    "gaming": "通用工具",
    "health-and-wellness": "搜索与研究",
    "home-automation": "通用工具",
    "industrial--iot": "通用工具",
    "knowledge--memory": "知识与记忆",
    "legal": "搜索与研究",
    "location-services": "地理与出行",
    "marketing": "社交与内容",
    "monitoring": "云平台与运维",
    "multimedia-process": "多媒体",
    "os-automation": "通用工具",
    "podcasts": "社交与内容",
    "product-management": "效率与协作",
    "real-estate": "搜索与研究",
    "research": "搜索与研究",
    "RAG": "知识与记忆",
    "search": "搜索与研究",
    "security": "安全分析",
    "social-media": "社交与内容",
    "spirituality-and-esoterica": "通用工具",
    "sports": "通用工具",
    "support-and-service-management": "效率与协作",
    "translation-services": "通讯与协作",
    "speech-to-text": "多媒体",
    "text-to-speech": "多媒体",
    "travel-and-transportation": "地理与出行",
    "version-control": "版本控制",
    "workplace-and-productivity": "效率与协作",
    "other-tools-and-integrations": "通用工具",
}


ZH_CATEGORY_KEYWORDS = (
    ("浏览器", "浏览器与网页"),
    ("版本控制", "版本控制"),
    ("数据库", "数据库"),
    ("文件系统", "文件与存储"),
    ("数据分析", "数据分析"),
    ("电商", "电商经营"),
    ("知识", "知识与记忆"),
    ("安全", "安全分析"),
    ("地理", "地理与出行"),
    ("云平台", "云平台与运维"),
    ("搜索", "搜索与研究"),
    ("通讯", "通讯与协作"),
    ("金融", "金融与市场"),
    ("社交", "社交与内容"),
    ("多媒体", "多媒体"),
    ("效率", "效率与协作"),
    ("开发", "开发与代码"),
    ("命令行", "开发与代码"),
)


EXCLUDED_CATEGORY_KEYS = {"aggregators"}
EXCLUDED_TEXT_PATTERNS = (
    re.compile(r"\b(?:mcp|agent|skill)\s+(?:directory|marketplace|registry|catalog)\b", re.I),
    re.compile(r"\bsearch(?:es)?\s+(?:for\s+)?(?:mcp\s+)?servers\b", re.I),
    re.compile(r"\b(?:aggregate|aggregates|aggregating)\s+(?:multiple\s+)?mcp\s+servers\b", re.I),
    re.compile(r"\bmcp\s+(?:client|sdk|framework|inspector)\b", re.I),
    re.compile(r"(?:目录|市场|注册中心|客户端|开发框架|调试工具).{0,12}MCP|MCP.{0,12}(?:目录|市场|注册中心|客户端|开发框架|调试工具)", re.I),
)


MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
HTML_ANCHOR_RE = re.compile(r'<a\s+name="([^"]+)"[^>]*></a>', re.I)
REPO_NAME_RE = re.compile(r'["\']?repoName["\']?\s*:\s*"([^"]+)"')
REPO_URL_RE = re.compile(r'["\']?repoUrl["\']?\s*:\s*"([^"]+)"')


@dataclass(frozen=True)
class SourceRef:
    source_id: str
    commit: str
    section: str
    line: int


@dataclass
class Candidate:
    canonical_key: str
    repo_name: str
    repo_url: str
    subpath: str | None
    name: str
    category: str
    description: str
    upstream_categories: list[str]
    sources: list[SourceRef]
    existing_catalog_match: bool
    prefilter_status: str
    prefilter_reason: str | None

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["sources"] = [asdict(ref) for ref in self.sources]
        return data


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_snapshot(text: str, spec: SourceSpec) -> None:
    actual = sha256_text(text)
    if actual != spec.readme_sha256:
        raise ValueError(
            f"{spec.source_id} README SHA-256 mismatch: expected "
            f"{spec.readme_sha256}, got {actual}"
        )


def extract_section(text: str, spec: SourceSpec) -> list[tuple[int, str]]:
    lines = text.splitlines()
    try:
        start = lines.index(spec.section_start)
        end = lines.index(spec.section_end, start + 1)
    except ValueError as exc:
        raise ValueError(f"{spec.source_id} server section boundary drift") from exc
    return [(index + 1, lines[index]) for index in range(start + 1, end)]


def _clean_link_label(value: str) -> str:
    value = re.sub(r"[*_`]", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    return " ".join(value.split()).strip()


def _clean_description(value: str) -> str:
    value = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", value)
    value = MARKDOWN_LINK_RE.sub(lambda match: match.group(1), value)
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"[*_`]", "", value)
    return " ".join(value.split()).strip(" -|")


def canonicalize_github_url(url: str) -> tuple[str, str, str | None] | None:
    parsed = urlsplit(unquote(url.strip()))
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "github.com",
        "www.github.com",
    }:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    owner = parts[0]
    repo = parts[1].removesuffix(".git")
    if not owner or not repo:
        return None
    repo_name = f"{owner}/{repo}"
    repo_url = f"https://github.com/{repo_name}"
    subpath: str | None = None
    if len(parts) >= 5 and parts[2] in {"tree", "blob"}:
        subpath = "/".join(parts[4:]).rstrip("/") or None
    canonical_key = f"github.com/{repo_name.lower()}"
    if subpath:
        canonical_key = f"{canonical_key}#{subpath.lower()}"
    return canonical_key, repo_url, subpath


def _heading_key(line: str) -> tuple[str, str]:
    anchor = HTML_ANCHOR_RE.search(line)
    display = re.sub(r"^###\s+", "", line)
    display = re.sub(r"<[^>]+>", "", display)
    display = " ".join(display.split()).strip()
    if anchor:
        return anchor.group(1), display
    return display, display


def map_category(source_id: str, heading_key: str, heading_display: str) -> str:
    if source_id == "awesome-mcp-servers":
        try:
            return EN_CATEGORY_MAP[heading_key]
        except KeyError as exc:
            raise ValueError(f"unmapped upstream category: {heading_key}") from exc
    for token, category in ZH_CATEGORY_KEYWORDS:
        if token in heading_display:
            return category
    if "其他" in heading_display or "体育" in heading_display or "艺术" in heading_display:
        return "通用工具"
    raise ValueError(f"unmapped upstream category: {heading_display}")


def _prefilter_reason(category_key: str, name: str, description: str) -> str | None:
    if category_key in EXCLUDED_CATEGORY_KEYS:
        return "non-server-category:aggregator"
    searchable = f"{name} {description}"
    for pattern in EXCLUDED_TEXT_PATTERNS:
        if pattern.search(searchable):
            return "non-server-entry:directory-client-or-framework"
    return None


def parse_source(text: str, spec: SourceSpec, *, verify_hash: bool = True) -> list[Candidate]:
    if verify_hash:
        verify_snapshot(text, spec)
    section = extract_section(text, spec)
    candidates: list[Candidate] = []
    category_key = ""
    category_display = ""
    for line_number, line in section:
        if line.startswith("### "):
            category_key, category_display = _heading_key(line)
            continue
        if not category_key:
            continue
        if spec.source_id == "awesome-mcp-zh":
            if not line.lstrip().startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if not cells or cells[0].startswith("---") or cells[0] in {"名称", "项目"}:
                continue
            matches = MARKDOWN_LINK_RE.findall(cells[0])
            description = _clean_description(cells[1] if len(cells) > 1 else "")
        else:
            if not re.match(r"^\s*[-*]\s+", line):
                continue
            matches = MARKDOWN_LINK_RE.findall(line)
            description = _clean_description(line)
        github_match = next(
            ((label, url) for label, url in matches if canonicalize_github_url(url)),
            None,
        )
        if not github_match:
            continue
        name, url = github_match
        normalized = canonicalize_github_url(url)
        assert normalized is not None
        canonical_key, repo_url, subpath = normalized
        repo_name = "/".join(repo_url.split("/")[-2:])
        reason = _prefilter_reason(category_key, name, description)
        category = (
            "通用工具"
            if category_key in EXCLUDED_CATEGORY_KEYS
            else map_category(spec.source_id, category_key, category_display)
        )
        candidates.append(
            Candidate(
                canonical_key=canonical_key,
                repo_name=repo_name,
                repo_url=repo_url,
                subpath=subpath,
                name=_clean_link_label(name),
                category=category,
                description=description,
                upstream_categories=[category_display],
                sources=[
                    SourceRef(
                        source_id=spec.source_id,
                        commit=spec.commit,
                        section=category_display,
                        line=line_number,
                    )
                ],
                existing_catalog_match=False,
                prefilter_status="excluded" if reason else "eligible",
                prefilter_reason=reason,
            )
        )
    return candidates


def current_catalog_keys(catalog_text: str) -> set[str]:
    keys: set[str] = set()
    for repo_name in REPO_NAME_RE.findall(catalog_text):
        normalized = canonicalize_github_url(f"https://github.com/{repo_name}")
        if normalized:
            keys.add(normalized[0].split("#", 1)[0])
    for repo_url in REPO_URL_RE.findall(catalog_text):
        normalized = canonicalize_github_url(repo_url)
        if normalized:
            keys.add(normalized[0].split("#", 1)[0])
    return keys


def current_catalog_keys_many(catalog_texts: Iterable[str]) -> set[str]:
    keys: set[str] = set()
    for catalog_text in catalog_texts:
        keys.update(current_catalog_keys(catalog_text))
    return keys


def merge_candidates(
    candidates: Iterable[Candidate], existing_keys: set[str]
) -> list[Candidate]:
    merged: dict[str, Candidate] = {}
    for candidate in candidates:
        previous = merged.get(candidate.canonical_key)
        if previous is None:
            candidate.existing_catalog_match = (
                candidate.canonical_key.split("#", 1)[0] in existing_keys
            )
            if candidate.existing_catalog_match:
                candidate.prefilter_status = "excluded"
                candidate.prefilter_reason = "existing-catalog-entry"
            merged[candidate.canonical_key] = candidate
            continue
        source_ids = {ref.source_id for ref in previous.sources}
        previous.sources.extend(
            ref for ref in candidate.sources if ref.source_id not in source_ids
        )
        previous.sources.sort(key=lambda ref: (ref.source_id, ref.line))
        previous.upstream_categories = sorted(
            set(previous.upstream_categories + candidate.upstream_categories)
        )
        if len(candidate.description) > len(previous.description):
            previous.description = candidate.description
        if (
            previous.prefilter_reason
            and not candidate.prefilter_reason
            and not previous.existing_catalog_match
        ):
            previous.prefilter_status = "eligible"
            previous.prefilter_reason = None
            previous.category = candidate.category
            previous.name = candidate.name
    return sorted(merged.values(), key=lambda item: item.canonical_key)


def build_inventory(
    zh_text: str,
    en_text: str,
    current_catalog_text: str | Sequence[str],
    *,
    verify_hashes: bool = True,
) -> dict[str, object]:
    parsed = [
        *parse_source(zh_text, SOURCE_SPECS["awesome-mcp-zh"], verify_hash=verify_hashes),
        *parse_source(
            en_text,
            SOURCE_SPECS["awesome-mcp-servers"],
            verify_hash=verify_hashes,
        ),
    ]
    catalog_texts = (
        [current_catalog_text]
        if isinstance(current_catalog_text, str)
        else list(current_catalog_text)
    )
    existing_keys = current_catalog_keys_many(catalog_texts)
    candidates = merge_candidates(parsed, existing_keys)
    eligible = [item for item in candidates if item.prefilter_status == "eligible"]
    excluded = [item for item in candidates if item.prefilter_status == "excluded"]
    by_source = {
        source_id: sum(
            1
            for item in eligible
            if any(ref.source_id == source_id for ref in item.sources)
        )
        for source_id in SOURCE_SPECS
    }
    return {
        "schema_version": 1,
        "snapshot_date": SNAPSHOT_DATE,
        "purpose": "discovery-audit-only",
        "runtime_catalog_changed": False,
        "sources": [asdict(spec) | {"source_url": spec.source_url} for spec in SOURCE_SPECS.values()],
        "summary": {
            "parsed_entries": len(parsed),
            "unique_repositories": len(candidates),
            "eligible_new_candidates": len(eligible),
            "excluded_candidates": len(excluded),
            "eligible_by_source": by_source,
        },
        "existing_catalog_repo_names": sorted(
            {
                repo_name
                for catalog_text in catalog_texts
                for repo_name in REPO_NAME_RE.findall(catalog_text)
            },
            key=str.lower,
        ),
        "existing_catalog_repo_roots": sorted(existing_keys),
        "candidates": [item.to_dict() for item in candidates],
    }


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--awesome-mcp-zh", required=True, type=Path)
    parser.add_argument("--awesome-mcp-servers", required=True, type=Path)
    parser.add_argument(
        "--current-catalog",
        required=True,
        type=Path,
        action="append",
        help="Existing catalog source; repeat to exclude every prior catalog generation.",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--skip-snapshot-hash-check",
        action="store_true",
        help="Only for parser fixture development; never use for a published inventory.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = build_inventory(
        args.awesome_mcp_zh.read_text(encoding="utf-8"),
        args.awesome_mcp_servers.read_text(encoding="utf-8"),
        [path.read_text(encoding="utf-8") for path in args.current_catalog],
        verify_hashes=not args.skip_snapshot_hash_check,
    )
    write_json(args.output, payload)
    summary = payload["summary"]
    assert isinstance(summary, dict)
    print(
        "mcp_catalog_audit=ok "
        f"unique={summary['unique_repositories']} "
        f"eligible={summary['eligible_new_candidates']} "
        f"excluded={summary['excluded_candidates']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
