from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REVIEW_PATH = ROOT / "docs" / "mcp-catalog-expansion" / "review-candidates.json"
ALLOWED_SOURCES = {"awesome-mcp-zh", "awesome-mcp-servers"}


def test_committed_review_list_is_balanced_and_non_executable() -> None:
    payload = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
    candidates = payload["candidates"]
    assert payload["purpose"] == "human-review-gate"
    assert payload["runtime_catalog_changed"] is False
    assert len(candidates) == 100
    assert [item["rank"] for item in candidates] == list(range(1, 101))
    assert len({item["canonical_key"] for item in candidates}) == 100
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
        assert item["decision"] == "pending-human-review"
        assert item["proposed_availability"] == "planned"
        assert item["decision_reason"] is None
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
