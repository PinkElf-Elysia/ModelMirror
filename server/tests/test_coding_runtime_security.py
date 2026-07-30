from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from server.coding_runtime.api import CodingTurnRequest, _public_event
from server.coding_runtime.models import CodingEvent, CodingEventKind
from server.coding_runtime.worker import (
    INTERNAL_GATEWAY_BASE_URL,
    WORKSPACE_PATH,
    build_opencode_config,
    create_acp_client,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _compose_service(name: str) -> str:
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        compose,
    )
    assert match is not None
    return match.group(1)


def test_container_isolation_uses_an_immutable_sanitized_source_snapshot() -> None:
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    service = _compose_service("coding-runtime")
    dockerfile = (
        REPOSITORY_ROOT / "server/coding_worker/Dockerfile"
    ).read_text(encoding="utf-8")
    dockerignore = (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "profiles:\n      - coding" in service
    assert "context: ." in service
    assert "dockerfile: server/coding_worker/Dockerfile" in service
    assert 'user: "65532:65532"' in service
    assert "read_only: true" in service
    assert "cap_drop:\n      - ALL" in service
    assert "no-new-privileges:true" in service
    assert ":/workspace" not in service
    assert "- coding_internal" in service
    assert "ports:" not in service
    assert "privileged:" not in service
    assert "COPY --chown=coding:coding . /workspace" in dockerfile
    assert all(
        pattern in dockerignore
        for pattern in (
            ".git",
            ".env",
            "**/*.key",
            "**/*.pem",
            "**/node_modules",
            "**/storage/**",
            "**/uploads/**",
        )
    )

    network = compose.split("  coding_internal:\n", maxsplit=1)[1]
    assert "internal: true" in network


def test_agent_configuration_fails_closed_for_write_shell_and_extensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODING_AGENT_MODEL", "deepseek/deepseek-v4-flash")
    monkeypatch.setenv("CODING_AGENT_GATEWAY_KEY", "test-only-key")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-cross")

    client = create_acp_client()
    config = json.loads(client._config.environment["OPENCODE_CONFIG_CONTENT"])
    permission = config["permission"]

    assert client._config.command == (
        "/usr/local/bin/opencode",
        "acp",
        "--cwd",
        WORKSPACE_PATH,
    )
    assert client._config.workspace == WORKSPACE_PATH
    assert client._config.process_cwd == WORKSPACE_PATH
    assert permission["*"] == "deny"
    assert permission["read"]["*"] == "allow"
    assert all(
        permission[name] == "allow" for name in ("list", "glob", "grep", "lsp")
    )
    assert all(
        permission[name] == "deny"
        for name in (
            "edit",
            "bash",
            "task",
            "webfetch",
            "websearch",
            "skill",
            "external_directory",
            "question",
            "todowrite",
        )
    )
    assert permission["read"]["**/.git/**"] == "deny"
    assert permission["read"]["**/.env"] == "deny"
    assert permission["read"]["**/*.key"] == "deny"
    assert config["plugin"] == []
    assert config["mcp"] == {}
    assert config["share"] == "disabled"
    assert config["autoupdate"] is False
    assert config["model"] == "modelmirror/deepseek/deepseek-v4-flash"
    assert "deepseek/deepseek-v4-flash" in config["provider"]["modelmirror"]["models"]
    assert config["provider"]["modelmirror"]["options"]["baseURL"] == (
        INTERNAL_GATEWAY_BASE_URL
    )
    assert "UNRELATED_SECRET" not in client._config.environment


def test_api_rejects_control_injection_and_only_exposes_sanitized_events() -> None:
    with pytest.raises(ValidationError):
        CodingTurnRequest.model_validate(
            {
                "prompt": "Explain this",
                "cwd": "C:\\private\\repo",
                "command": "git status",
                "provider": "other",
            }
        )

    event = CodingEvent(
        session_id="session",
        seq=1,
        kind=CodingEventKind.TOOL_STATUS,
        created_at=1.0,
        turn_id="turn",
        data={
            "tool_call_id": "tool-1",
            "title": "Read C:\\private\\repo and /workspace/server/main.py",
            "kind": "read",
            "status": "completed",
            "raw": "must-not-cross",
            "api_key": "must-not-cross",
        },
    )

    public = _public_event(event)
    serialized = json.dumps(public, ensure_ascii=False)
    assert "C:\\private" not in serialized
    assert "/workspace" not in serialized
    assert "must-not-cross" not in serialized
    assert set(public["data"]) == {"tool_call_id", "title", "kind", "status"}
