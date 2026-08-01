from __future__ import annotations

from types import SimpleNamespace

import pytest

from server.xperts.features import (
    deterministic_memory_reply,
    gateway_audio_endpoint,
    normalize_file_extensions,
    parse_conversation_enrichment,
    validate_selected_files,
)


def test_file_policy_normalizes_extensions_and_limits_selected_assets() -> None:
    assets = [
        SimpleNamespace(filename="brief.MD", extension=".md"),
        SimpleNamespace(filename="notes.txt", extension=".txt"),
    ]

    assert normalize_file_extensions(["TXT", ".md"]) == {".txt", ".md"}
    validate_selected_files(
        assets,
        enabled=True,
        max_files=2,
        allowed_extensions=["txt", ".md"],
    )

    with pytest.raises(ValueError, match="at most 1"):
        validate_selected_files(
            assets,
            enabled=True,
            max_files=1,
            allowed_extensions=[".txt", ".md"],
        )
    with pytest.raises(ValueError, match="does not allow"):
        validate_selected_files(
            assets[:1],
            enabled=False,
            max_files=2,
            allowed_extensions=[".md"],
        )
    with pytest.raises(ValueError, match="not allowed"):
        validate_selected_files(
            [SimpleNamespace(filename="payload.pdf", extension=".pdf")],
            enabled=True,
            max_files=2,
            allowed_extensions=[".txt"],
        )


def test_conversation_enrichment_accepts_bounded_strict_json() -> None:
    title, suggestions = parse_conversation_enrichment(
        """
        {"title":"Launch readiness","suggestions":[
          "List the blockers",
          "List the blockers",
          "Draft the rollout",
          "Prepare a rollback"
        ]}
        """,
        suggestion_limit=2,
    )

    assert title == "Launch readiness"
    assert suggestions == ["List the blockers", "Draft the rollout"]
    assert parse_conversation_enrichment("not json", suggestion_limit=3) == ("", [])


def test_deterministic_memory_reply_requires_exact_high_confidence_match() -> None:
    memories = [
        SimpleNamespace(
            memory_id="memory-low",
            status="active",
            title="Project codename",
            summary="The codename is Aurora.",
            content="The codename is Aurora.",
            tags=["codename"],
            confidence=0.7,
        ),
        SimpleNamespace(
            memory_id="memory-high",
            status="active",
            title="Preferred response format",
            summary="Use concise bullet points.",
            content="Use concise bullet points.",
            tags=["response style"],
            confidence=0.98,
        ),
    ]

    matched = deterministic_memory_reply(
        "Preferred response format",
        memories,
        min_confidence=0.92,
    )
    assert matched == (
        "memory-high",
        "Use concise bullet points.",
        0.98,
    )
    assert (
        deterministic_memory_reply(
            "What should we do next?",
            memories,
            min_confidence=0.92,
        )
        is None
    )


def test_gateway_audio_endpoint_reuses_openai_compatible_base() -> None:
    assert gateway_audio_endpoint(
        "https://gateway.example/v1/chat/completions",
        "audio/speech",
    ) == "https://gateway.example/v1/audio/speech"
    assert gateway_audio_endpoint(
        "https://gateway.example/openai/v1/chat/completions?ignored=yes",
        "/audio/transcriptions",
    ) == "https://gateway.example/openai/v1/audio/transcriptions"

    with pytest.raises(ValueError, match="gateway URL"):
        gateway_audio_endpoint("file:///tmp/gateway", "audio/speech")
