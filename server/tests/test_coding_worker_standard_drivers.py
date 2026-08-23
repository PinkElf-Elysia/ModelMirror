from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft7Validator, Draft202012Validator

from server.coding_worker.acp_driver import (
    ACP_CAPABILITIES,
    ACP_SCHEMA_SHA256,
    ACP_SDK_VERSION,
    ACP_SDK_WHEEL_SHA256,
    AcpV1HarnessDriver,
)
from server.coding_worker.codex_app_server_driver import (
    CODEX_ACP_ORACLE_INTEGRITY,
    CODEX_ACP_ORACLE_VERSION,
    CODEX_APP_SERVER_VERSION,
    CODEX_CAPABILITIES,
    CODEX_PACKAGE_INTEGRITY,
    CODEX_SCHEMA_SHA256,
    CodexAppServerHarnessDriver,
    CodexNativeToolRejected,
)
from server.coding_worker.evaluation_driver import (
    EvaluationBrokerMcp,
    EvaluationDriverError,
    EvaluationDriverManifest,
    command_sha256,
)
from server.coding_worker.harness_protocol import (
    HarnessBinding,
    HarnessEventKind,
    HarnessPersistenceLevel,
    HarnessResponseOutcome,
    HarnessToolOwnership,
)


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = ROOT / "server/tests/fixtures/coding_worker_v20_schemas"
IMAGE_DIGEST = "sha256:" + "a" * 64
ACP_COMMAND = ("/usr/local/bin/python", "-m", "modelmirror_acp_fixture_agent")
CODEX_COMMAND = ("/usr/local/bin/codex", "app-server")


def _manifest(driver: str) -> EvaluationDriverManifest:
    if driver == "acp":
        command = ACP_COMMAND
        return EvaluationDriverManifest(
            driver_id="acp_v1",
            protocol_id="acp",
            protocol_version="1.19",
            implementation_version=ACP_SDK_VERSION,
            package_name="agent-client-protocol",
            package_version=ACP_SDK_VERSION,
            package_integrity=f"sha256:{ACP_SDK_WHEEL_SHA256}",
            schema_sha256=ACP_SCHEMA_SHA256,
            image_digest=IMAGE_DIGEST,
            command=command,
            command_sha256=command_sha256(command),
            tool_ownership=HarnessToolOwnership.BROKER_ONLY,
            persistence=HarnessPersistenceLevel.SESSION_RESUME,
        )
    command = CODEX_COMMAND
    return EvaluationDriverManifest(
        driver_id="codex_app_server",
        protocol_id="codex-app-server",
        protocol_version=CODEX_APP_SERVER_VERSION,
        implementation_version=CODEX_APP_SERVER_VERSION,
        package_name="@openai/codex",
        package_version=CODEX_APP_SERVER_VERSION,
        package_integrity=CODEX_PACKAGE_INTEGRITY,
        schema_sha256=CODEX_SCHEMA_SHA256,
        image_digest=IMAGE_DIGEST,
        command=command,
        command_sha256=command_sha256(command),
        tool_ownership=HarnessToolOwnership.UNKNOWN,
        persistence=HarnessPersistenceLevel.SESSION_RESUME,
    )


def _binding(
    manifest: EvaluationDriverManifest,
    capabilities: dict[str, bool],
    *,
    generation: int = 1,
) -> HarnessBinding:
    return HarnessBinding(
        task_id="task_fixture",
        route_id="coding/evaluation",
        slot_id="slot_a",
        binding_sha256="b" * 64,
        driver_generation=generation,
        descriptor=manifest.descriptor(capabilities),
    )


def _acp_driver() -> AcpV1HarnessDriver:
    manifest = _manifest("acp")
    return AcpV1HarnessDriver(
        manifest=manifest,
        binding=_binding(manifest, ACP_CAPABILITIES),
        broker_mcp=EvaluationBrokerMcp(
            url="http://127.0.0.1:8765/mcp"
        ),
        observed_image_digest=IMAGE_DIGEST,
        observed_command=ACP_COMMAND,
    )


def _codex_driver() -> CodexAppServerHarnessDriver:
    manifest = _manifest("codex")
    return CodexAppServerHarnessDriver(
        manifest=manifest,
        binding=_binding(manifest, CODEX_CAPABILITIES),
        observed_image_digest=IMAGE_DIGEST,
        observed_command=CODEX_COMMAND,
    )


def _acp_frame(method: str, params: dict[str, Any], request_id: int | None = 1):
    frame: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
    if request_id is not None:
        frame["id"] = request_id
    return frame


def _codex_frame(method: str, params: dict[str, Any], request_id: int | None = 1):
    frame: dict[str, Any] = {"method": method, "params": params}
    if request_id is not None:
        frame["id"] = request_id
    return frame


def _initialize_acp(driver: AcpV1HarnessDriver) -> None:
    driver.initialize(
        _acp_frame(
            "initialize",
            {"protocolVersion": 1, "clientCapabilities": {}},
        )
    )
    driver.open(
        _acp_frame(
            "session/new",
            {
                "cwd": "/workspace",
                "mcpServers": [driver.broker_mcp.acp_config()],
            },
            2,
        ),
        supplier_session_id="acp-session-1",
    )


def _initialize_codex(driver: CodexAppServerHarnessDriver) -> None:
    driver.initialize(
        _codex_frame(
            "initialize",
            {
                "clientInfo": {
                    "name": "modelmirror-evaluation",
                    "version": "20",
                },
                "capabilities": {"experimentalApi": False},
            },
        )
    )
    driver.open(
        _codex_frame(
            "thread/start",
            {
                "model": "controlled-route",
                "cwd": "/workspace",
                "approvalPolicy": "never",
                "sandbox": "read-only",
            },
            2,
        ),
        supplier_thread_id="codex-thread-1",
    )


def _start_codex_turn(
    driver: CodexAppServerHarnessDriver,
    *,
    supplier_turn_id: str = "codex-turn-1",
) -> None:
    driver.start_turn(
        _codex_frame(
            "turn/start",
            {
                "threadId": "codex-thread-1",
                "input": [{"type": "text", "text": "Inspect the fixture."}],
            },
            3,
        ),
        supplier_turn_id=supplier_turn_id,
    )


def test_deployment_manifest_and_broker_endpoint_fail_closed() -> None:
    manifest = _manifest("acp")
    assert manifest.production_route is False
    manifest.attest(
        observed_image_digest=IMAGE_DIGEST,
        observed_command=ACP_COMMAND,
    )
    with pytest.raises(EvaluationDriverError, match="image digest"):
        manifest.attest(
            observed_image_digest="sha256:" + "c" * 64,
            observed_command=ACP_COMMAND,
        )
    with pytest.raises(EvaluationDriverError, match="not registered"):
        manifest.attest(
            observed_image_digest=IMAGE_DIGEST,
            observed_command=("/usr/local/bin/other",),
        )
    with pytest.raises(ValueError, match="command digest"):
        EvaluationDriverManifest.model_validate(
            {**manifest.model_dump(mode="json"), "command_sha256": "d" * 64}
        )
    for url in (
        "https://127.0.0.1:8765/mcp",
        "http://10.0.0.1:8765/mcp",
        "http://user:secret@127.0.0.1:8765/mcp",
        "http://127.0.0.1:8765/other",
    ):
        with pytest.raises(ValueError, match="fixed loopback"):
            EvaluationBrokerMcp(url=url)


def test_acp_v1_full_lifecycle_normalizes_without_native_side_effects() -> None:
    driver = _acp_driver()
    _initialize_acp(driver)
    driver.start_turn(
        _acp_frame(
            "session/prompt",
            {
                "sessionId": "acp-session-1",
                "prompt": [{"type": "text", "text": "Inspect."}],
            },
            3,
        ),
        platform_turn_id="platform-turn-1",
    )
    message = driver.update(
        _acp_frame(
            "session/update",
            {
                "sessionId": "acp-session-1",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "Inspecting."},
                },
            },
            None,
        )
    )
    assert message is not None and message.kind is HarnessEventKind.MESSAGE
    assert message.payload == {
        "update": "agent_message_chunk",
        "text": "Inspecting.",
    }
    assert driver.update(
        _acp_frame(
            "session/update",
            {
                "sessionId": "acp-session-1",
                "update": {
                    "sessionUpdate": "agent_thought_chunk",
                    "content": {"type": "text", "text": "private reasoning"},
                },
            },
            None,
        )
    ) is None
    approval = driver.request_permission(
        _acp_frame(
            "session/request_permission",
            {
                "sessionId": "acp-session-1",
                "toolCall": {
                    "toolCallId": "tool-1",
                    "title": "Run check",
                    "kind": "execute",
                    "rawInput": {"secret": "must-not-persist"},
                },
                "options": [
                    {"optionId": "allow", "name": "Allow", "kind": "allow_once"}
                ],
            },
            4,
        )
    )
    assert approval.kind is HarnessEventKind.REQUEST
    assert "secret" not in json.dumps(approval.payload)
    response = driver.reply_permission(
        {"jsonrpc": "2.0", "id": 4, "result": {"outcome": {"outcome": "selected", "optionId": "allow"}}}
    )
    assert response.outcome is HarnessResponseOutcome.APPROVED
    with pytest.raises(EvaluationDriverError, match="not pending"):
        driver.reply_permission(
            {"jsonrpc": "2.0", "id": 4, "result": {"outcome": {"outcome": "selected", "optionId": "allow"}}}
        )
    driver.cancel_turn(
        _acp_frame(
            "session/cancel", {"sessionId": "acp-session-1"}, None
        )
    )
    resumed = _binding(_manifest("acp"), ACP_CAPABILITIES, generation=2)
    driver.resume_session(
        _acp_frame(
            "session/resume",
            {
                "sessionId": "acp-session-1",
                "cwd": "/workspace",
                "mcpServers": [driver.broker_mcp.acp_config()],
            },
            5,
        ),
        resumed_binding=resumed,
    )
    driver.close_session(
        _acp_frame("session/close", {"sessionId": "acp-session-1"}, 6)
    )


def test_acp_rejects_arbitrary_mcp_paths_and_unstable_updates() -> None:
    for mcp_servers in (
        [],
        [
            {
                "name": "arbitrary",
                "command": "/bin/sh",
                "args": ["-c", "whoami"],
                "env": [],
            }
        ],
    ):
        driver = _acp_driver()
        driver.initialize(
            _acp_frame("initialize", {"protocolVersion": 1}, 1)
        )
        with pytest.raises(EvaluationDriverError, match="arbitrary MCP"):
            driver.open(
                _acp_frame(
                    "session/new",
                    {"cwd": "/workspace", "mcpServers": mcp_servers},
                    2,
                ),
                supplier_session_id="session-1",
            )

    driver = _acp_driver()
    _initialize_acp(driver)
    driver.start_turn(
        _acp_frame(
            "session/prompt",
            {
                "sessionId": "acp-session-1",
                "prompt": [{"type": "text", "text": "Inspect."}],
            },
            3,
        ),
        platform_turn_id="turn-1",
    )
    with pytest.raises(EvaluationDriverError, match="unavailable"):
        driver.update(
            _acp_frame(
                "session/update",
                {
                    "sessionId": "acp-session-1",
                    "update": {"sessionUpdate": "plan_update"},
                },
                None,
            )
        )


def test_codex_stable_lifecycle_enforces_item_order_and_exact_turns() -> None:
    driver = _codex_driver()
    _initialize_codex(driver)
    _start_codex_turn(driver)
    started = driver.event(
        _codex_frame(
            "turn/started",
            {
                "threadId": "codex-thread-1",
                "turn": {"id": "codex-turn-1", "status": "inProgress", "items": []},
            },
            None,
        )
    )
    assert started is not None and started.payload == {"state": "turn_started"}
    item_started = driver.event(
        _codex_frame(
            "item/started",
            {
                "threadId": "codex-thread-1",
                "turnId": "codex-turn-1",
                "startedAtMs": 1,
                "item": {"id": "item-1", "type": "agentMessage", "text": ""},
            },
            None,
        )
    )
    assert item_started is not None
    delta = driver.event(
        _codex_frame(
            "item/agentMessage/delta",
            {
                "threadId": "codex-thread-1",
                "turnId": "codex-turn-1",
                "itemId": "item-1",
                "delta": "Done.",
            },
            None,
        )
    )
    assert delta is not None and delta.payload["delta"] == "Done."
    driver.event(
        _codex_frame(
            "item/completed",
            {
                "threadId": "codex-thread-1",
                "turnId": "codex-turn-1",
                "completedAtMs": 2,
                "item": {"id": "item-1", "type": "agentMessage", "text": "Done."},
            },
            None,
        )
    )
    completed = driver.event(
        _codex_frame(
            "turn/completed",
            {
                "threadId": "codex-thread-1",
                "turn": {"id": "codex-turn-1", "status": "completed", "items": []},
            },
            None,
        )
    )
    assert completed is not None
    assert completed.kind is HarnessEventKind.TURN_COMPLETED

    _start_codex_turn(driver, supplier_turn_id="codex-turn-2")
    with pytest.raises(EvaluationDriverError, match="another turn"):
        driver.steer_turn(
            _codex_frame(
                "turn/steer",
                {
                    "threadId": "codex-thread-1",
                    "expectedTurnId": "stale-turn",
                    "input": [{"type": "text", "text": "Continue."}],
                },
                5,
            )
        )
    driver.steer_turn(
        _codex_frame(
            "turn/steer",
            {
                "threadId": "codex-thread-1",
                "expectedTurnId": "codex-turn-2",
                "input": [{"type": "text", "text": "Continue."}],
            },
            6,
        )
    )
    driver.interrupt_turn(
        _codex_frame(
            "turn/interrupt",
            {"threadId": "codex-thread-1", "turnId": "codex-turn-2"},
            7,
        )
    )
    resumed = _binding(_manifest("codex"), CODEX_CAPABILITIES, generation=2)
    driver.resume_session(
        _codex_frame(
            "thread/resume", {"threadId": "codex-thread-1"}, 8
        ),
        resumed_binding=resumed,
    )
    driver.close_session(
        _codex_frame(
            "thread/unsubscribe", {"threadId": "codex-thread-1"}, 9
        )
    )


@pytest.mark.parametrize(
    "method",
    [
        "thread/shellCommand",
        "command/exec",
        "process/spawn",
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
        "mcpServer/tool/call",
        "web/search",
        "skill/read",
        "plugin/install",
        "account/login/start",
        "config/value/write",
    ],
)
def test_codex_native_side_effect_requests_are_rejected(method: str) -> None:
    driver = _codex_driver()
    _initialize_codex(driver)
    _start_codex_turn(driver)
    with pytest.raises(CodexNativeToolRejected):
        driver.server_request(
            _codex_frame(
                method,
                {"threadId": "codex-thread-1", "turnId": "codex-turn-1"},
                10,
            )
        )


@pytest.mark.parametrize("field", ["dynamicTools", "CodeModeOnly", "environments", "mcpServers"])
def test_codex_unproven_turn_extensions_are_unavailable(field: str) -> None:
    driver = _codex_driver()
    _initialize_codex(driver)
    params: dict[str, Any] = {
        "threadId": "codex-thread-1",
        "input": [{"type": "text", "text": "Inspect."}],
        field: [],
    }
    with pytest.raises(EvaluationDriverError, match="unavailable fields"):
        driver.start_turn(
            _codex_frame("turn/start", params, 3),
            supplier_turn_id="turn-1",
        )


def test_codex_reasoning_is_omitted_and_native_items_fail_closed() -> None:
    driver = _codex_driver()
    _initialize_codex(driver)
    _start_codex_turn(driver)
    reasoning = driver.event(
        _codex_frame(
            "item/started",
            {
                "threadId": "codex-thread-1",
                "turnId": "codex-turn-1",
                "startedAtMs": 1,
                "item": {"id": "reasoning-1", "type": "reasoning"},
            },
            None,
        )
    )
    assert reasoning is None
    with pytest.raises(CodexNativeToolRejected):
        driver.event(
            _codex_frame(
                "item/started",
                {
                    "threadId": "codex-thread-1",
                    "turnId": "codex-turn-1",
                    "startedAtMs": 2,
                    "item": {"id": "command-1", "type": "commandExecution"},
                },
                None,
            )
        )


def test_official_schema_bundles_are_exact_and_validate_stable_frames() -> None:
    acp_path = SCHEMA_ROOT / "acp-schema-v1.19.json"
    codex_path = SCHEMA_ROOT / "codex-app-server-0.149.0.schemas.json"
    assert hashlib.sha256(acp_path.read_bytes()).hexdigest() == ACP_SCHEMA_SHA256
    assert hashlib.sha256(codex_path.read_bytes()).hexdigest() == CODEX_SCHEMA_SHA256

    acp = json.loads(acp_path.read_text(encoding="utf-8"))
    acp_wrapper = {
        "$schema": acp["$schema"],
        "$defs": acp["$defs"],
        "$ref": "#/$defs/InitializeRequest",
    }
    Draft202012Validator(acp_wrapper).validate(
        {"protocolVersion": 1, "clientCapabilities": {}}
    )
    Draft202012Validator(
        {**acp_wrapper, "$ref": "#/$defs/NewSessionRequest"}
    ).validate(
        {
            "cwd": "/workspace",
            "mcpServers": [
                {
                    "type": "http",
                    "name": "modelmirror-broker",
                    "url": "http://127.0.0.1:8765/mcp",
                    "headers": [],
                }
            ],
        }
    )

    codex = json.loads(codex_path.read_text(encoding="utf-8"))
    definitions = codex["definitions"]

    def validate_codex(name: str, payload: dict[str, Any]) -> None:
        pointer = "/".join(
            part.replace("~", "~0").replace("/", "~1")
            for part in name.split("/")
        )
        Draft7Validator(
            {
                "$schema": codex["$schema"],
                "definitions": definitions,
                "$ref": f"#/definitions/{pointer}",
            }
        ).validate(payload)

    validate_codex(
        "InitializeParams",
        {
            "clientInfo": {
                "name": "modelmirror-evaluation",
                "version": "20",
            },
            "capabilities": {"experimentalApi": False},
        },
    )
    validate_codex(
        "v2/ThreadStartParams",
        {
            "model": "controlled-route",
            "cwd": "/workspace",
            "approvalPolicy": "never",
            "sandbox": "read-only",
        },
    )
    validate_codex(
        "v2/TurnSteerParams",
        {
            "threadId": "codex-thread-1",
            "expectedTurnId": "codex-turn-1",
            "input": [{"type": "text", "text": "Continue."}],
        },
    )
    assert CODEX_ACP_ORACLE_VERSION == "1.6.2"
    assert CODEX_ACP_ORACLE_INTEGRITY.startswith("sha512-")
