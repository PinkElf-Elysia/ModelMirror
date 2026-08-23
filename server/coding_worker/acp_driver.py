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
    HarnessPersistenceLevel,
    HarnessRequestKind,
    HarnessResponse,
    HarnessResponseOutcome,
    HarnessToolOwnership,
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

    @classmethod
    def validate_manifest(cls, manifest: EvaluationDriverManifest) -> None:
        if (
            manifest.protocol_id != "acp"
            or manifest.protocol_version != "1.19"
            or manifest.implementation_version != ACP_SDK_VERSION
            or manifest.package_name != "agent-client-protocol"
            or manifest.package_version != ACP_SDK_VERSION
            or manifest.package_integrity != f"sha256:{ACP_SDK_WHEEL_SHA256}"
            or manifest.schema_sha256 != ACP_SCHEMA_SHA256
            or manifest.tool_ownership is not HarnessToolOwnership.BROKER_ONLY
            or manifest.persistence is not HarnessPersistenceLevel.SESSION_RESUME
        ):
            raise EvaluationDriverError("ACP evaluation manifest is incompatible")

    def __init__(
        self,
        *,
        manifest: EvaluationDriverManifest,
        binding: HarnessBinding,
        broker_mcp: EvaluationBrokerMcp,
        observed_image_digest: str,
        observed_command: Sequence[str],
    ) -> None:
        self.validate_manifest(manifest)
        super().__init__(
            manifest=manifest,
            binding=binding,
            observed_image_digest=observed_image_digest,
            observed_command=observed_command,
            capabilities=ACP_CAPABILITIES,
        )
        self.broker_mcp = broker_mcp
        self._prompt_request_id: str | None = None
        self._permission_options: dict[
            str, tuple[object, frozenset[str]]
        ] = {}

    def initialize(self, frame: Mapping[str, Any]) -> None:
        _, params = require_jsonrpc_method(frame, "initialize", notification=False)
        self._require_acp_keys(
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
        self._require_acp_keys(params, required={"sessionId", "prompt"})
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
        self._require_acp_keys(params, required={"sessionId", "update"})
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
        self._require_acp_keys(
            params,
            required={"sessionId", "toolCall", "options"},
        )
        self._require_supplier_session(params["sessionId"])
        tool_call = params["toolCall"]
        options = params["options"]
        if not isinstance(tool_call, Mapping) or not isinstance(options, list):
            raise EvaluationDriverError("ACP permission request is invalid")
        if (
            not options
            or len(options) > 16
            or any(not isinstance(option, Mapping) for option in options)
        ):
            raise EvaluationDriverError("ACP permission options are invalid")
        option_ids: list[str] = []
        for option in options:
            try:
                self._require_acp_keys(
                    option,
                    required={"optionId", "name", "kind"},
                )
            except EvaluationDriverError as exc:
                raise EvaluationDriverError(
                    "ACP permission options are invalid"
                ) from exc
            option_id = option.get("optionId")
            name = option.get("name")
            option_kind = option.get("kind")
            if (
                not isinstance(option_id, str)
                or not option_id
                or len(option_id) > 128
                or not isinstance(name, str)
                or len(name) > 512
                or option_kind
                not in {
                    "allow_once",
                    "allow_always",
                    "reject_once",
                    "reject_always",
                }
            ):
                raise EvaluationDriverError("ACP permission options are invalid")
            option_ids.append(option_id)
        if len(set(option_ids)) != len(option_ids):
            raise EvaluationDriverError("ACP permission option id was repeated")
        assert request_id is not None
        event = self.request(
            supplier_request_id=request_id,
            kind=HarnessRequestKind.APPROVAL,
            payload={
                "tool_call_id": str(tool_call.get("toolCallId") or "")[:128],
                "title": str(tool_call.get("title") or "")[:256],
                "kind": str(tool_call.get("kind") or "")[:64],
                "option_ids": option_ids,
            },
        )
        self._permission_options[canonical_supplier_id(request_id)] = (
            request_id,
            frozenset(option_ids),
        )
        return event

    def reply_permission(self, frame: Mapping[str, Any]) -> HarnessResponse:
        request_id = frame.get("id")
        result = frame.get("result")
        if request_id is None or not isinstance(result, Mapping):
            raise EvaluationDriverError("ACP permission reply is invalid")
        self._require_acp_keys(result, required={"outcome"})
        outcome = result.get("outcome")
        if not isinstance(outcome, Mapping):
            raise EvaluationDriverError("ACP permission outcome is invalid")
        request_key = canonical_supplier_id(request_id)
        registered = self._permission_options.get(request_key)
        if registered is None:
            raise EvaluationDriverError("evaluation request is not pending")
        _, offered_options = registered
        outcome_kind = outcome.get("outcome")
        if outcome_kind == "selected":
            self._require_acp_keys(
                outcome, required={"outcome", "optionId"}
            )
            option_id = outcome.get("optionId")
            if not isinstance(option_id, str) or option_id not in offered_options:
                raise EvaluationDriverError(
                    "ACP permission option was not offered"
                )
            response_outcome = HarnessResponseOutcome.APPROVED
            response_payload = {"option_id": option_id[:128]}
        elif outcome_kind == "cancelled":
            self._require_acp_keys(outcome, required={"outcome"})
            response_outcome = HarnessResponseOutcome.CANCELLED
            response_payload = {"option_id": ""}
        else:
            raise EvaluationDriverError("ACP permission outcome is invalid")
        response = self.resolve(
            supplier_request_id=request_id,
            outcome=response_outcome,
            payload=response_payload,
        )
        self._permission_options.pop(request_key)
        return response

    def complete_turn(self, frame: Mapping[str, Any]) -> HarnessEventEnvelope:
        request_id = frame.get("id")
        result = frame.get("result")
        if (
            self._prompt_request_id is None
            or canonical_supplier_id(request_id) != self._prompt_request_id
            or not isinstance(result, Mapping)
        ):
            raise EvaluationDriverError("ACP prompt response is not correlated")
        self._require_acp_keys(result, required={"stopReason"})
        stop_reason = str(result["stopReason"])
        self._prompt_request_id = None
        return self.emit(
            supplier_event_id=f"prompt-complete:{request_id}",
            kind=HarnessEventKind.TURN_COMPLETED,
            payload={"stop_reason": stop_reason[:64]},
        )

    def cancel_turn(
        self, frame: Mapping[str, Any]
    ) -> tuple[HarnessResponse, ...]:
        _, params = require_jsonrpc_method(
            frame, "session/cancel", notification=True
        )
        self._require_acp_keys(params, required={"sessionId"})
        self._require_supplier_session(params["sessionId"])
        cancelled: list[HarnessResponse] = []
        for request_key, (supplier_request_id, _) in tuple(
            self._permission_options.items()
        ):
            cancelled.append(
                self.resolve(
                    supplier_request_id=supplier_request_id,
                    outcome=HarnessResponseOutcome.CANCELLED,
                )
            )
            self._permission_options.pop(request_key)
        self._prompt_request_id = None
        self.interrupt()
        return tuple(cancelled)

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
        self._require_acp_keys(params, required={"sessionId"})
        self._require_supplier_session(params["sessionId"])
        self.close()

    def _require_workspace_and_broker(
        self, params: Mapping[str, Any], *, resume: bool
    ) -> None:
        required = {"cwd", "mcpServers"}
        if resume:
            required.add("sessionId")
        self._require_acp_keys(
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

    @staticmethod
    def _require_acp_keys(
        payload: Mapping[str, Any],
        *,
        required: set[str],
        optional: set[str] | None = None,
    ) -> None:
        require_exact_keys(
            payload,
            required=required,
            optional=set(optional or ()) | {"_meta"},
        )
        metadata = payload.get("_meta")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise EvaluationDriverError("ACP metadata is invalid")

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
