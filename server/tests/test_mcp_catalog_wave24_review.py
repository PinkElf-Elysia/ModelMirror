from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REVIEW_PATH = ROOT / "docs" / "mcp-catalog-expansion-wave24" / "review-candidates.json"
OLD_REVIEW_PATH = ROOT / "docs" / "mcp-catalog-expansion" / "review-candidates.json"
AUDIT_PATH = ROOT / "scripts" / "mcp_catalog_audit.py"
ENRICH_PATH = ROOT / "scripts" / "mcp_catalog_github_enrich.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit = _load_module("mcp_catalog_audit_wave24", AUDIT_PATH)
enrich = _load_module("mcp_catalog_github_enrich_wave24", ENRICH_PATH)


def _payload(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_wave24_review_tracks_only_accepted_runtime_promotions_and_frozen_sources() -> None:
    payload = _payload(REVIEW_PATH)
    assert payload["snapshot_date"] == "2026-08-11"
    assert payload["purpose"] == "adaptation-classification"
    assert payload["runtime_catalog_changed"] is True
    assert payload["runtime_execution_changed"] is True
    assert payload["approval"] == {
        "status": "approved",
        "approved_at": "2026-08-12",
        "scope": "static-catalog-plus-accepted-wave25a-wave25b-runtime",
    }
    sources = {source["source_id"]: source for source in payload["source_snapshots"]}
    assert sources["awesome-mcp-zh"]["commit"] == "b29e114d95fa26338b092423fd1ede1e5598e4df"
    assert sources["awesome-mcp-zh"]["readme_sha256"] == (
        "854802528cb508a6f6d00e2d142b57a44bc5393bfd4321ddd96e1e9a2b10b51a"
    )
    assert sources["awesome-mcp-servers"]["commit"] == (
        "cbcdf8f7700cfe4c0ef9aeb232f64aeebe8a184c"
    )
    assert sources["awesome-mcp-servers"]["readme_sha256"] == (
        "d7012abf5a5019f2ff0b66dff3832b2b0c1e8c9dd672f382f3ae677d3b878874"
    )


def test_wave24_review_meets_deterministic_quota_and_hard_gates() -> None:
    payload = _payload(REVIEW_PATH)
    candidates = payload["candidates"]
    assert len(candidates) == 100
    assert payload["summary"]["selected"] == 100
    category_counts = Counter(item["category"] for item in candidates)
    repository_counts = Counter(item["repo_name"].lower() for item in candidates)
    assert len(category_counts) >= 10
    assert max(category_counts.values()) <= 15
    assert max(repository_counts.values()) <= 2
    for source_id in ("awesome-mcp-zh", "awesome-mcp-servers"):
        assert sum(
            any(source["source_id"] == source_id for source in item["sources"])
            for item in candidates
        ) >= 25
    cutoff = dt.date(2025, 8, 11)
    for item in candidates:
        github = item["github"]
        assert github["isPrivate"] is False
        assert github["isArchived"] is False
        assert github["isDisabled"] is False
        assert github["isFork"] is False
        assert github["licenseInfo"]["spdxId"] not in {"", "NONE", "NOASSERTION", "OTHER"}
        assert dt.date.fromisoformat(github["pushedAt"][:10]) >= cutoff
        assert enrich.non_server_metadata_reason(item, github) is None


def test_wave24_review_has_no_prior_catalog_identity_or_runtime_surface() -> None:
    payload = _payload(REVIEW_PATH)
    candidates = payload["candidates"]
    old_candidates = _payload(OLD_REVIEW_PATH)["candidates"]
    old_keys = {item["canonical_key"] for item in old_candidates}
    old_redirects = {item["github"]["nameWithOwner"].lower() for item in old_candidates}
    existing_keys = audit.current_catalog_keys_many(
        [
            (ROOT / "client" / "src" / "data" / "mcpProjects.ts").read_text(encoding="utf-8"),
            (ROOT / "client" / "src" / "data" / "mcpCatalogExpansionV2.generated.ts").read_text(
                encoding="utf-8"
            ),
        ]
    )
    forbidden_keys = {
        "allowlist",
        "command",
        "credential_slots",
        "credentials",
        "endpoint",
        "env",
        "executable",
        "headers",
        "server_command",
        "tool_policy",
        "tools",
    }

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(child) for child in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(child) for child in value))
        return set()

    assert Counter(item["proposed_availability"] for item in candidates) == {
        "ready": 4,
        "planned": 42,
        "blocked": 54,
    }
    assert all(
        item["decision"] in {"accepted-ready", "deferred-planned", "blocked"}
        for item in candidates
    )
    assert all(item["decision_reason_code"] for item in candidates)
    assert all(item["decision_reason"] for item in candidates)
    assert all(item["adaptation_wave"] in {21, 24, 25, 26, 27} for item in candidates)
    assert not ({item["canonical_key"] for item in candidates} & old_keys)
    assert not ({item["github"]["nameWithOwner"].lower() for item in candidates} & old_redirects)
    assert all(item["canonical_key"].split("#", 1)[0] not in existing_keys for item in candidates)
    assert not (keys(candidates) & forbidden_keys)
    excluded = {
        "agiletec-inc/airis-mcp-gateway",
        "dagger/container-use",
        "glifxyz/glif-mcp-server",
        "Panniantong/Agent-Reach",
        "txn2/kubefwd",
        "xpaysh/awesome-x402",
    }
    assert not ({item["repo_name"] for item in candidates} & excluded)


def test_wave24_generated_outputs_are_current() -> None:
    from scripts.mcp_catalog_integrate_wave24 import (
        BACKEND_PATH,
        FRONTEND_PATH,
        REPORT_PATH,
        build_approved_payload,
        render_backend,
        render_frontend,
        render_report,
    )

    payload = build_approved_payload(_payload(REVIEW_PATH))
    assert FRONTEND_PATH.read_text(encoding="utf-8") == render_frontend(payload)
    assert BACKEND_PATH.read_text(encoding="utf-8") == render_backend(payload)
    assert REPORT_PATH.read_text(encoding="utf-8") == render_report(payload)
