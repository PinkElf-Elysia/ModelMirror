from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "literature_acceptance.py"
SPEC = importlib.util.spec_from_file_location("literature_acceptance", SCRIPT)
assert SPEC and SPEC.loader
literature_acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(literature_acceptance)

FIXTURE_SCRIPT = Path(__file__).parents[2] / "scripts" / "acceptance.py"
FIXTURE_SPEC = importlib.util.spec_from_file_location("fixture_acceptance", FIXTURE_SCRIPT)
assert FIXTURE_SPEC and FIXTURE_SPEC.loader
fixture_acceptance = importlib.util.module_from_spec(FIXTURE_SPEC)
FIXTURE_SPEC.loader.exec_module(fixture_acceptance)


def test_collection_acceptance_requires_explicit_upstream_egress_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_RESEARCH_ACCEPTANCE_COLLECTION_ID", "pilot")

    def fake_request(method: str, path: str, payload=None):
        if path.endswith("/zotero/status"):
            return 200, {"config": {"configured": True}}, {}
        if path.endswith("/zotero/sync"):
            return 200, {"success": True}, {}
        if path.endswith("/pilot/index"):
            return 200, {"status": "completed"}, {}
        return 200, {
            "collections": [
                {
                    "id": "pilot",
                    "is_public": False,
                    "agent_enabled": True,
                    "indexed_document_count": 3,
                }
            ]
        }, {}

    monkeypatch.setattr(literature_acceptance, "request", fake_request)
    with pytest.raises(literature_acceptance.AcceptanceFailure, match="egress gates"):
        literature_acceptance.prepare_collection()


def test_output_acceptance_recomputes_every_artifact_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"verified artifact"
    digest = hashlib.sha256(content).hexdigest()

    def fake_request(method: str, path: str, payload=None):
        if path.endswith("/sources"):
            return 200, {"integrityStatus": "verified", "sources": [{"url": "https://openalex.org/W1"}]}, {}
        if path.endswith("/review"):
            return 200, {"integrityStatus": "verified", "markdown": "# Review"}, {}
        return 200, content, {"X-Content-SHA256": digest}

    monkeypatch.setattr(literature_acceptance, "request", fake_request)
    hashes = literature_acceptance.verify_outputs("rp_" + "a" * 32)
    assert set(hashes) == literature_acceptance.ARTIFACTS
    assert set(hashes.values()) == {digest}


def test_acceptance_control_url_uses_custom_loopback_port_and_rejects_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AI_RESEARCH_ACCEPTANCE_CONTROL_URL", raising=False)
    monkeypatch.setenv("AI_RESEARCH_CONTROL_PORT", "8890")
    assert literature_acceptance.loopback_control_url() == "http://127.0.0.1:8890"

    monkeypatch.setenv("AI_RESEARCH_ACCEPTANCE_CONTROL_URL", "https://example.com:8890")
    with pytest.raises(RuntimeError, match="HTTP loopback"):
        literature_acceptance.loopback_control_url()


def test_fixture_acceptance_origin_is_loopback_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_RESEARCH_CONTROL_PORT", "8890")
    assert fixture_acceptance.loopback_url(
        "AI_RESEARCH_ACCEPTANCE_CONTROL_URL", "AI_RESEARCH_CONTROL_PORT", 8790
    ) == "http://127.0.0.1:8890"

    monkeypatch.setenv("AI_RESEARCH_ACCEPTANCE_CONTROL_URL", "http://127.0.0.1:8890/path")
    with pytest.raises(RuntimeError, match="HTTP loopback"):
        fixture_acceptance.loopback_url(
            "AI_RESEARCH_ACCEPTANCE_CONTROL_URL", "AI_RESEARCH_CONTROL_PORT", 8790
        )
