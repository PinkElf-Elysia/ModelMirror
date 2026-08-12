from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "mcp_catalog_audit.py"
SPEC = importlib.util.spec_from_file_location("mcp_catalog_audit", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


def _spec(source_id: str, start: str, end: str) -> object:
    return audit.SourceSpec(
        source_id=source_id,
        repository="example/catalog",
        commit="a" * 40,
        readme_sha256="unused",
        section_start=start,
        section_end=end,
    )


def test_canonicalize_github_url_preserves_distinct_monorepo_subpath() -> None:
    assert audit.canonicalize_github_url(
        "https://github.com/Owner/Repo.git/tree/main/packages/server/?x=1#readme"
    ) == (
        "github.com/owner/repo#packages/server",
        "https://github.com/Owner/Repo",
        "packages/server",
    )
    assert audit.canonicalize_github_url("https://example.com/owner/repo") is None


def test_parser_stays_inside_server_sections_and_excludes_aggregators() -> None:
    text = """# Catalog
## Clients
- [Client](https://github.com/acme/client) - MCP client
## Server Implementations
### 🔗 <a name="aggregators"></a>Aggregators
- [Mux](https://github.com/acme/mux) - Aggregates multiple MCP servers.
### 📂 <a name="browser-automation"></a>Browser Automation
- [Browser](https://github.com/acme/browser) - Browser MCP server.
## Frameworks
- [Framework](https://github.com/acme/framework) - MCP framework
"""
    parsed = audit.parse_source(
        text,
        _spec("awesome-mcp-servers", "## Server Implementations", "## Frameworks"),
        verify_hash=False,
    )
    assert [item.repo_name for item in parsed] == ["acme/mux", "acme/browser"]
    assert parsed[0].prefilter_reason == "non-server-category:aggregator"
    assert parsed[1].category == "浏览器与网页"


def test_zh_parser_reads_only_first_table_link() -> None:
    text = """# 清单
## MCP 服务器精选列表
### 🔍 搜索
| 名称 | 简介 | 备注 |
| --- | --- | --- |
| [Alpha](https://github.com/acme/alpha) | 搜索服务器，另见 [文档](https://github.com/acme/docs) | Python |
## MCP 更多玩法
- [Outside](https://github.com/acme/outside)
"""
    parsed = audit.parse_source(
        text,
        _spec("awesome-mcp-zh", "## MCP 服务器精选列表", "## MCP 更多玩法"),
        verify_hash=False,
    )
    assert len(parsed) == 1
    assert parsed[0].repo_name == "acme/alpha"
    assert parsed[0].category == "搜索与研究"


def test_merge_combines_sources_and_excludes_current_catalog() -> None:
    left = audit.Candidate(
        canonical_key="github.com/acme/server",
        repo_name="acme/server",
        repo_url="https://github.com/acme/server",
        subpath=None,
        name="Server",
        category="通用工具",
        description="short",
        upstream_categories=["Other"],
        sources=[audit.SourceRef("awesome-mcp-servers", "a" * 40, "Other", 10)],
        existing_catalog_match=False,
        prefilter_status="eligible",
        prefilter_reason=None,
    )
    right = audit.Candidate(
        canonical_key="github.com/acme/server",
        repo_name="acme/server",
        repo_url="https://github.com/acme/server",
        subpath=None,
        name="服务器",
        category="搜索与研究",
        description="a longer Chinese description",
        upstream_categories=["搜索"],
        sources=[audit.SourceRef("awesome-mcp-zh", "b" * 40, "搜索", 20)],
        existing_catalog_match=False,
        prefilter_status="eligible",
        prefilter_reason=None,
    )
    merged = audit.merge_candidates([left, right], {"github.com/acme/server"})
    assert len(merged) == 1
    assert merged[0].existing_catalog_match is True
    assert merged[0].prefilter_reason == "existing-catalog-entry"
    assert [item.source_id for item in merged[0].sources] == [
        "awesome-mcp-servers",
        "awesome-mcp-zh",
    ]


def test_existing_repo_root_excludes_new_subpath_variant() -> None:
    candidate = audit.Candidate(
        canonical_key="github.com/acme/mono#packages/new-server",
        repo_name="acme/mono",
        repo_url="https://github.com/acme/mono",
        subpath="packages/new-server",
        name="New Server",
        category="通用工具",
        description="server",
        upstream_categories=["Other"],
        sources=[audit.SourceRef("awesome-mcp-servers", "a" * 40, "Other", 10)],
        existing_catalog_match=False,
        prefilter_status="eligible",
        prefilter_reason=None,
    )
    merged = audit.merge_candidates([candidate], {"github.com/acme/mono"})
    assert merged[0].prefilter_reason == "existing-catalog-entry"


def test_current_catalog_keys_many_reads_source_and_quoted_generated_fields() -> None:
    keys = audit.current_catalog_keys_many(
        [
            'repoName: "Owner/Source"\nrepoUrl: "https://github.com/Owner/Source"',
            '"repoName": "Owner/Generated",\n"repoUrl": "https://github.com/Owner/Generated"',
        ]
    )
    assert keys == {
        "github.com/owner/source",
        "github.com/owner/generated",
    }


def test_snapshot_hash_drift_fails_closed() -> None:
    spec = _spec("awesome-mcp-servers", "## Start", "## End")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        audit.parse_source("## Start\n## End\n", spec, verify_hash=True)


def test_all_upstream_categories_map_to_existing_catalog_categories() -> None:
    assert set(audit.EN_CATEGORY_MAP.values()) <= set(audit.CURRENT_CATEGORIES)
    assert set(category for _, category in audit.ZH_CATEGORY_KEYWORDS) <= set(
        audit.CURRENT_CATEGORIES
    )
