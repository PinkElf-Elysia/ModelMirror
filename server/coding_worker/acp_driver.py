from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .evaluation_driver import (
    EvaluationBrokerMcp,
    EvaluationDriverError,
    EvaluationDriverManifest,
    StandardEvaluationDriver,
    canonical_supplier_id,
    require_exact_keys,
    require_jsonrpc_method,
    safe_supplier_id,
)
from .harness_protocol import (
    HarnessBinding,
    HarnessEventEnvelope,
    HarnessEventKind,
    HarnessRequestKind,
    HarnessResponse,
    HarnessResponseOutcome,
)


ACP_PROTOCOL_VERSION = 1
ACP_SCHEMA_RELEASE = "schema-v1.19.0"
ACP_SCHEMA_SHA256 = "998c6427fa78bf6cd39f442bf164c6172234ebdf1c04298af57c40fa716ce267"
ACP_SDK_VERSION = "0.12.0"
ACP_SDK_WHEEL_SHA256 = "233626748034896214de118f5cf5a319484ad2186705fd595219afee92237ccc"
ACP_CAPABILITIES = {
    "steering": False,
    "interrupt": True,
    "usage": True,
    "checkpoint": False,
    "session_resume": True,
}


_UPDATE_KINDS = {
    "user_message_chunk": HarnessEventKind.MESSAGE,
    "agent_message_chunk": HarnessEventKind.MESSAGE,
    "tool_call": HarnessEventKind.TOOL_STARTED,
    "tool_call_update": HarnessEventKind.TOOL_COMPLETED,
    "plan": HarnessEventKind.PLAN,
    "usage_update": HarnessEventKind.USAGE,
}


class AcpV1HarnessDriver(StandardEvaluationDriver):
    """ACP v1.19 evaluation adapter with a single fixed Broker MCP.

    The class consumes frames already transported by the isolated ACP sidecar.
    It never launches an executable, reads a workspace or performs a tool
    action.  Tool-call UI updates and permission requests are normalized for
    conformance only; the platform Broker remains the sole side-effect owner.
    """

    def __init__(
        self,
        *,
        manifest: EvaluationDriverManifest,
        binding: HarnessBinding,
        broker_mcp: EvaluationBrokerMcp,
        observed_image_digest: str,
        observed_command: Sequence[str],
    ) -> None:
        if manifest.protocol_id != "acp" or manifest.protocol_version != "1.19":
            raise EvaluationDriverError("ACP evaluation manifest is incompatible")
        if manifest.schema_sha256 != ACP_SCHEMA_SHA256:
            raise EvaluationDriverError("ACP evaluation schema digest is incompatible")
        super().__init__(
            manifest=manifest,
            binding=binding,
            observed_image_digest=observed_image_digest,
            observed_command=observed_command,
            capabilities=ACP_CAPABILITIES,
        )
        self.broker_mcp = broker_mcp
        self._prompt_request_id: str | None = None

    def initialize(self, frame: Mapping[str, Any]) -> None:
        _, params = require_jsonrpc_method(frame, "initialize", notification=False)
        require_exact_keys(
            params,
            required={"protocolVersion"},
            optional={"clientCapabilities", "clientInfo"},
        )
        if params["protocolVersion"] != ACP_PROTOCOL_VERSION:
            raise EvaluationDriverError("ACP protocol version is incompatible")
        capabilities = params.get("clientCapabilities", {})
        if not isinstance(capabilities, Mapping) or capabilities:
            raise EvaluationDriverError("ACP client capabilities are not allowed")
        self.initialize_protocol()

    def open(self, frame: Mapping[str, Any], *, supplier_session_id: object) -> None:
        _, params = require_jsonrpc_method(frame, "session/new", notification=False)
        self._require_workspace_and_broker(params, resume=False)
        self.open_session(supplier_session_id)

    def start_turn(
        self,
        frame: Mapping[str, Any],
        *,
        platform_turn_id: str,
    ) -> None:
        request_id, params = require_jsonrpc_method(
            frame, "session/prompt", notification=False
        )
        require_exact_keys(params, required={"sessionId", "prompt"})
        self._require_supplier_session(params["sessionId"])
        prompt = params["prompt"]
        if not isinstance(prompt, list) or not prompt:
            raise EvaluationDriverError("ACP prompt is invalid")
        for item in prompt:
            if (
                not isinstance(item, Mapping)
                or set(item) != {"type", "text"}
                or item.get("type") != "text"
                or not isinstance(item.get("text"), str)
            ):
                raise EvaluationDriverError("ACP evaluation accepts text prompts only")
        super().start_turn(platform_turn_id)
        assert request_id is not None
        self._prompt_request_id = canonical_supplier_id(request_id)

    def update(self, frame: Mapping[str, Any]) -> HarnessEventEnvelope | None:
        _, params = require_jsonrpc_method(
            frame, "session/update", notification=True
        )
        require_exact_keys(params, required={"sessionId", "update"})
        self._require_supplier_session(params["sessionId"])
        update = params["update"]
        if not isinstance(update, Mapping):
            raise EvaluationDriverError("ACP session update is invalid")
        update_kind = update.get("sessionUpdate")
        if update_kind == "agent_thought_chunk":
            # Public ledgers never persist supplier reasoning content.
            return None
        kind = _UPDATE_KINDS.get(str(update_kind))
        if kind is None:
            raise EvaluationDriverError("ACP session update is unavailable")
        if update_kind == "tool_call_update":
            status = str(update.get("status") or "")
            kind = (
                HarnessEventKind.TOOL_COMPLETED
                if status in {"completed", "failed"}
                else HarnessEventKind.TOOL_STARTED
            )
        payload = self._safe_update_payload(update_kind=str(update_kind), update=update)
        return self.emit(
            supplier_event_id=f"session-update:{self.sequence + 1}",
            kind=kind,
            payload=payload,
        )

    def request_permission(self, frame: Mapping[str, Any]) -> HarnessEventEnvelope:
        request_id, params = require_jsonrpc_method(
            frame, "session/request_permission", notification=False
        )
        require_exact_keys(
            params,
            required={"sessionId", "toolCall", "options"},
        )
        self._require_supplier_session(params["sessionId"])
        tool_call = params["toolCall"]
        options = params["options"]
        if not isinstance(tool_call, Mapping) or not isinstance(options, list):
            raise EvaluationDriverError("ACP permission request is invalid")
        if not options or any(not isinstance(option, Mapping) for option in options):
            raise EvaluationDriverError("ACP permission options are invalid")
        assert request_id is not None
        return self.request(
            supplier_request_id=request_id,
            kind=HarnessRequestKind.APPROVAL,
            payload={
                "tool_call_id": str(tool_call.get("toolCallId") or "")[:128],
                "title": str(tool_call.get("title") or "")[:256],
                "kind": str(tool_call.get("kind") or "")[:64],
                "option_ids": [
                    str(option.get("optionId") or "")[:128]
                    for option in options[:16]
                ],
            },
        )

    def reply_permission(self, frame: Mapping[str, Any]) -> HarnessResponse:
        request_id = frame.get("id")
        result = frame.get("result")
        if request_id is None or not isinstance(result, Mapping):
            raise EvaluationDriverError("ACP permission reply is invalid")
        outcome = result.get("outcome")
        if not isinstance(outcome, Mapping):
            raise EvaluationDriverError("ACP permission outcome is invalid")
        selected = outcome.get("outcome") == "selected"
        return self.resolve(
            supplier_request_id=request_id,
            outcome=(
                HarnessResponseOutcome.APPROVED
                if selected
                else HarnessResponseOutcome.DECLINED
            ),
            payload={
                "option_id": str(outcome.get("optionId") or "")[:128]
                if selected
                else "",
            },
        )

    def complete_turn(self, frame: Mapping[str, Any]) -> HarnessEventEnvelope:
        request_id = frame.get("id")
        result = frame.get("result")
        if (
            self._prompt_request_id is None
            or canonical_supplier_id(request_id) != self._prompt_request_id
            or not isinstance(result, Mapping)
        ):
            raise EvaluationDriverError("ACP prompt response is not correlated")
        require_exact_keys(result, required={"stopReason"})
        stop_reason = str(result["stopReason"])
        self._prompt_request_id = None
        return self.emit(
            supplier_event_id=f"prompt-complete:{request_id}",
            kind=HarnessEventKind.TURN_COMPLETED,
            payload={"stop_reason": stop_reason[:64]},
        )

    def cancel_turn(self, frame: Mapping[str, Any]) -> None:
        _, params = require_jsonrpc_method(
            frame, "session/cancel", notification=True
        )
        require_exact_keys(params, required={"sessionId"})
        self._require_supplier_session(params["sessionId"])
        self._prompt_request_id = None
        self.interrupt()

    def resume_session(
        self,
        frame: Mapping[str, Any],
        *,
        resumed_binding: HarnessBinding,
    ) -> None:
        _, params = require_jsonrpc_method(
            frame, "session/resume", notification=False
        )
        self._require_supplier_session(params.get("sessionId"))
        self._require_workspace_and_broker(params, resume=True)
        self.resume(resumed_binding)

    def close_session(self, frame: Mapping[str, Any]) -> None:
        _, params = require_jsonrpc_method(
            frame, "session/close", notification=False
        )
        require_exact_keys(params, required={"sessionId"})
        self._require_supplier_session(params["sessionId"])
        self.close()

    def _require_workspace_and_broker(
        self, params: Mapping[str, Any], *, resume: bool
    ) -> None:
        required = {"cwd", "mcpServers"}
        if resume:
            required.add("sessionId")
        require_exact_keys(
            params,
            required=required,
            optional={"additionalDirectories"},
        )
        if params["cwd"] != "/workspace":
            raise EvaluationDriverError("ACP workspace is not deployment controlled")
        additional = params.get("additionalDirectories")
        if additional is not None and additional not in ([], ()):
            raise EvaluationDriverError("ACP additional directories are unavailable")
        if params["mcpServers"] != [self.broker_mcp.acp_config()]:
            raise EvaluationDriverError("ACP arbitrary MCP configuration is unavailable")

    def _require_supplier_session(self, value: object) -> None:
        session = self.require_session()
        if safe_supplier_id("session", value) != session.session_id:
            raise EvaluationDriverError("ACP frame targets another session")

    @staticmethod
    def _safe_update_payload(
        *, update_kind: str, update: Mapping[str, Any]
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"update": update_kind}
        if update_kind in {"user_message_chunk", "agent_message_chunk"}:
            content = update.get("content")
            if isinstance(content, Mapping) and content.get("type") == "text":
                payload["text"] = str(content.get("text") or "")[:16384]
        elif update_kind in {"tool_call", "tool_call_update"}:
            payload.update(
                {
                    "tool_call_id": str(update.get("toolCallId") or "")[:128],
                    "title": str(update.get("title") or "")[:256],
                    "status": str(update.get("status") or "")[:64],
                }
            )
        elif update_kind == "plan":
            entries = update.get("entries")
            if isinstance(entries, list):
                payload["entries"] = [
                    {
                        "content": str(item.get("content") or "")[:512],
                        "status": str(item.get("status") or "")[:32],
                    }
                    for item in entries[:64]
                    if isinstance(item, Mapping)
                ]
        elif update_kind == "usage_update":
            for name in ("used", "size", "cost"):
                value = update.get(name)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    payload[name] = value
        return payload
