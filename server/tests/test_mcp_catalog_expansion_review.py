from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REVIEW_PATH = ROOT / "docs" / "mcp-catalog-expansion" / "review-candidates.json"
ALLOWED_SOURCES = {"awesome-mcp-zh", "awesome-mcp-servers"}


def test_committed_approved_list_is_balanced_and_non_executable() -> None:
    payload = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
    candidates = payload["candidates"]
    assert payload["purpose"] == "approved-catalog-expansion"
    assert payload["runtime_catalog_changed"] is True
    assert payload["runtime_execution_changed"] is False
    assert payload["approval"] == {
        "approved_at": "2026-08-09",
        "approved_count": 100,
        "availability": {"planned": 100, "blocked": 0},
        "execution_boundary": "display-and-non-executable-manifest-only",
    }
    assert len(candidates) == 100
    assert [item["rank"] for item in candidates] == list(range(1, 101))
    assert len({item["canonical_key"] for item in candidates}) == 100
    assert len({item["catalog_id"] for item in candidates}) == 100
    assert all(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item["catalog_id"]) for item in candidates)
    assert len({item["category"] for item in candidates}) >= 10
    assert max(Counter(item["category"] for item in candidates).values()) <= 15
    assert max(Counter(item["repo_name"].lower() for item in candidates).values()) <= 2
    for source_id in ALLOWED_SOURCES:
        assert (
            sum(
                any(source["source_id"] == source_id for source in item["sources"])
                for item in candidates
            )
            >= 25
        )
    for item in candidates:
        assert item["decision"] == "approved"
        assert item["proposed_availability"] == "planned"
        assert "仅作为 planned 展示" in item["decision_reason"]
        assert item["github"]["licenseInfo"]["spdxId"] not in {
            "",
            "NOASSERTION",
            "OTHER",
            "NONE",
        }
        assert {source["source_id"] for source in item["sources"]} <= ALLOWED_SOURCES
        serialized = json.dumps(item, ensure_ascii=False).lower()
        assert "installcommand" not in serialized
        assert "server_command" not in serialized
        assert "credential" not in serialized
        assert "executable" not in serialized


def test_approved_catalog_generated_outputs_are_current() -> None:
    from scripts.mcp_catalog_integrate_approved import (
        BACKEND_PATH,
        FRONTEND_PATH,
        REPORT_PATH,
        build_approved_payload,
        render_backend,
        render_frontend,
        render_report,
    )

    payload = build_approved_payload(json.loads(REVIEW_PATH.read_text(encoding="utf-8")))
    assert FRONTEND_PATH.read_text(encoding="utf-8") == render_frontend(payload)
    assert BACKEND_PATH.read_text(encoding="utf-8") == render_backend(payload)
    assert REPORT_PATH.read_text(encoding="utf-8") == render_report(payload)
