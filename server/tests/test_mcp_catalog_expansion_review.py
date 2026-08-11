from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REVIEW_PATH = ROOT / "docs" / "mcp-catalog-expansion" / "review-candidates.json"
ALLOWED_SOURCES = {"awesome-mcp-zh", "awesome-mcp-servers"}


def test_committed_approved_list_is_balanced_and_classified() -> None:
    payload = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
    candidates = payload["candidates"]
    assert payload["purpose"] == "adaptation-classification"
    assert payload["runtime_catalog_changed"] is True
    assert payload["runtime_execution_changed"] is True
    assert payload["adaptation"] == {
        "classified_at": "2026-08-09",
        "classified_count": 100,
        "availability": {"ready": 5, "planned": 51, "blocked": 44},
        "ready_boundary": "fixed-read-only-token-sidecar-contract",
        "non_ready_boundary": "no-command-endpoint-credential-or-tool-policy",
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
    assert Counter(item["proposed_availability"] for item in candidates) == {
        "ready": 5,
        "planned": 51,
        "blocked": 44,
    }
    ready = [item for item in candidates if item["proposed_availability"] == "ready"]
    assert [item["catalog_id"] for item in ready] == [
        "fatwang2-search1api-mcp",
        "blazickjp-arxiv-mcp-server",
        "kagisearch-kagimcp",
        "brave-brave-search-mcp-server",
        "livetennisapi-livetennisapi-mcp",
    ]
    ready_by_id = {item["catalog_id"]: item for item in ready}
    assert ready_by_id["brave-brave-search-mcp-server"]["adapter_version"] == "2.1.0"
    assert ready_by_id["brave-brave-search-mcp-server"]["adaptation_wave"] == 13
    assert ready_by_id["kagisearch-kagimcp"]["adapter_version"] == (
        "1.0.2-compatible-native-v1"
    )
    assert ready_by_id["kagisearch-kagimcp"]["adaptation_wave"] == 14
    assert ready_by_id["blazickjp-arxiv-mcp-server"]["adapter_version"] == (
        "0.6.2-compatible-native-v1"
    )
    assert ready_by_id["blazickjp-arxiv-mcp-server"]["adaptation_wave"] == 14
    assert ready_by_id["fatwang2-search1api-mcp"]["adapter_version"] == (
        "0.5.3-compatible-native-v1"
    )
    assert ready_by_id["fatwang2-search1api-mcp"]["adaptation_wave"] == 15
    assert ready_by_id["livetennisapi-livetennisapi-mcp"]["adapter_version"] == (
        "1.4.0-compatible-native-v1"
    )
    assert ready_by_id["livetennisapi-livetennisapi-mcp"]["adaptation_wave"] == 15
    for item in candidates:
        assert item["decision"] in {"adapted-ready", "deferred-planned", "blocked"}
        assert item["decision_reason_code"].startswith(
            ("ready-", "planned-", "blocked-")
        )
        assert item["decision_reason"]
        assert item["adaptation_wave"] in {13, 14, 15}
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
        assert "executable" not in item


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
