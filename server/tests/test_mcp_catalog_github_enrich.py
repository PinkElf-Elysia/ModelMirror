from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "mcp_catalog_github_enrich.py"
SPEC = importlib.util.spec_from_file_location("mcp_catalog_github_enrich", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
enrich = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = enrich
SPEC.loader.exec_module(enrich)


def _candidate(index: int, category: str, source_id: str) -> dict[str, object]:
    return {
        "canonical_key": f"github.com/example/repo-{index}",
        "repo_name": f"example/repo-{index}",
        "repo_url": f"https://github.com/example/repo-{index}",
        "subpath": None,
        "name": f"Repo {index}",
        "category": category,
        "description": "MCP server install with npx",
        "sources": [{"source_id": source_id}],
        "review_status": "eligible",
        "score": 1000 - index,
        "score_components": {"test": 1},
        "github": {
            "stargazerCount": 1000 - index,
            "licenseInfo": {"spdxId": "MIT", "name": "MIT License"},
        },
    }


def test_query_uses_fixed_repository_fields_and_escaped_names() -> None:
    query = enrich.build_query(['owner/repo"quoted'])
    assert 'repository(owner: "owner", name: "repo\\"quoted")' in query
    assert "isArchived" in query
    assert "licenseInfo" in query
    assert "rateLimit" in query


def test_missing_repository_is_a_normal_hard_gate() -> None:
    assert enrich.hard_exclusion_reason(None) == "repository-not-found"


def test_metadata_filter_rejects_aggregators_routers_and_clients() -> None:
    candidate = {"name": "Meta", "description": "MCP Aggregator and Gateway"}
    metadata = {"description": "Manage your MCP connections with a GUI"}
    assert (
        enrich.non_server_metadata_reason(candidate, metadata)
        == "non-server-entry:aggregator-router-client-or-manager"
    )
    actual_server = {
        "name": "Search",
        "description": "MCP server for public web search",
    }
    assert enrich.non_server_metadata_reason(actual_server, {"description": "Read-only tools"}) is None


def test_hard_gates_reject_archived_unlicensed_and_inactive_repositories() -> None:
    baseline = {
        "isPrivate": False,
        "isArchived": False,
        "isDisabled": False,
        "isFork": False,
        "licenseInfo": {"spdxId": "MIT"},
        "pushedAt": "2026-08-01T00:00:00Z",
    }
    assert enrich.hard_exclusion_reason(baseline) is None
    assert enrich.hard_exclusion_reason({**baseline, "isArchived": True}) == "repository-archived"
    assert enrich.hard_exclusion_reason({**baseline, "licenseInfo": None}) == "license-undetermined"
    assert (
        enrich.hard_exclusion_reason(
            {**baseline, "licenseInfo": {"spdxId": "NOASSERTION"}}
        )
        == "license-undetermined"
    )
    assert (
        enrich.hard_exclusion_reason({**baseline, "pushedAt": "2024-01-01T00:00:00Z"})
        == "repository-inactive-over-12-months"
    )


def test_selection_enforces_source_category_and_count_constraints() -> None:
    categories = [f"category-{index}" for index in range(12)]
    candidates: list[dict[str, object]] = []
    for index in range(160):
        source_id = "awesome-mcp-zh" if index < 50 else "awesome-mcp-servers"
        candidates.append(_candidate(index, categories[index % len(categories)], source_id))
    selected = enrich.select_review_candidates(candidates)
    assert len(selected) == 100
    assert len({item["category"] for item in selected}) >= 10
    assert max(
        sum(item["category"] == category for item in selected) for category in categories
    ) <= 15
    assert max(
        sum(item["repo_name"] == repo_name for item in selected)
        for repo_name in {item["repo_name"] for item in selected}
    ) <= 2
    assert sum(
        any(source["source_id"] == "awesome-mcp-zh" for source in item["sources"])
        for item in selected
    ) >= 25
    assert sum(
        any(source["source_id"] == "awesome-mcp-servers" for source in item["sources"])
        for item in selected
    ) >= 25
    assert all(item["decision"] == "pending-human-review" for item in selected)
    assert all(item["proposed_availability"] == "planned" for item in selected)


def test_review_report_states_non_runtime_human_gate() -> None:
    candidate = _candidate(1, "通用工具", "awesome-mcp-servers")
    selected = enrich.select_review_candidates(
        [candidate],
        target_count=1,
        source_minimum=0,
        category_minimum=1,
    )
    payload = {
        "snapshot_date": "2026-08-09",
        "source_snapshots": [
            {
                "source_id": "awesome-mcp-servers",
                "commit": "a" * 40,
                "readme_sha256": "b" * 64,
            }
        ],
        "upstream_inventory_summary": {
            "parsed_entries": 1,
            "unique_repositories": 1,
            "eligible_new_candidates": 1,
        },
        "selection_policy": {
            "maximum_per_category": 15,
            "maximum_per_repository": 2,
        },
        "summary": {
            "selected": 1,
            "categories": 1,
            "by_source": {
                "awesome-mcp-zh": 0,
                "awesome-mcp-servers": 1,
            },
        },
        "candidates": selected,
    }
    report = enrich.render_review_report(payload)
    assert "候选尚未写入产品目录" in report
    assert "待人工复核" in report
    assert "[example/repo-1](https://github.com/example/repo-1)" in report
