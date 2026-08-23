from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .evaluation_driver import (
    EvaluationDriverError,
    EvaluationDriverManifest,
    StandardEvaluationDriver,
    canonical_supplier_id,
    require_exact_keys,
    require_jsonrpc_method,
)
from .harness_protocol import (
    HarnessBinding,
    HarnessEventEnvelope,
    HarnessEventKind,
    HarnessToolOwnership,
)


CODEX_APP_SERVER_VERSION = "0.149.0"
CODEX_PACKAGE_INTEGRITY = (
    "sha512-i4dryj2Y1j+00Mb5n+0n71EYnTK9/KDc2cdFo/dXD0d1oTog2bhUssKDEIOn"
    "KmnEf51P0Z/HJTWvTKw/UHyOvQ=="
)
CODEX_SCHEMA_SHA256 = "02a4c63a638fdae4a5f6c3ad32a41a377b642c66f3abc84f6fc47c7f3d6074df"
CODEX_ACP_ORACLE_VERSION = "1.6.2"
CODEX_ACP_ORACLE_INTEGRITY = (
    "sha512-2eF1mbs1gTqkZJSLYOun/pFDx37sYa7W63HOPezC37b/R8AYms5O1nfQu8lrqFSG"
    "DrwDZkASVORymLcqjCNqyA=="
)
CODEX_CAPABILITIES = {
    "steering": True,
    "interrupt": True,
    "usage": True,
    "checkpoint": False,
    "session_resume": True,
    "dynamic_tools": False,
}


_NATIVE_REQUESTS = {
    "thread/shellCommand",
    "command/exec",
    "command/exec/write",
    "command/exec/resize",
    "command/exec/terminate",
    "process/spawn",
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
    "item/tool/call",
    "mcpServer/tool/call",
    "mcpServer/resource/read",
    "mcpServer/elicitation/request",
    "mcpServer/oauth/login",
}

_NATIVE_PREFIXES = (
    "account/",
    "app/",
    "config/",
    "experimentalFeature/",
    "externalAgentConfig/",
    "fs/",
    "hooks/",
    "marketplace/",
    "mcpServer/",
    "plugin/",
    "process/",
    "skill/",
    "skills/",
    "web/",
)

_NATIVE_ITEM_TYPES = {
    "commandExecution",
    "fileChange",
    "mcpToolCall",
    "dynamicToolCall",
    "webSearch",
    "imageGeneration",
    "imageView",
}


class CodexNativeToolRejected(EvaluationDriverError):
    code = "evaluation_native_tool_rejected"


class CodexAppServerHarnessDriver(StandardEvaluationDriver):
    """Stable-subset Codex App Server adapter for conformance evaluation.

    Codex 0.149.0 cannot yet attest a Broker-only execution path.  This
    adapter therefore normalizes message/plan/usage/turn lifecycles but rejects
    every native side-effect surface before an operation or approval can be
    created.  Its descriptor is permanently unavailable to production routes.
    """

    def __init__(
        self,
        *,
        manifest: EvaluationDriverManifest,
        binding: HarnessBinding,
        observed_image_digest: str,
        observed_command: Sequence[str],
    ) -> None:
        if (
            manifest.protocol_id != "codex-app-server"
            or manifest.protocol_version != CODEX_APP_SERVER_VERSION
            or manifest.schema_sha256 != CODEX_SCHEMA_SHA256
        ):
            raise EvaluationDriverError("Codex evaluation manifest is incompatible")
        if manifest.tool_ownership is not HarnessToolOwnership.UNKNOWN:
            raise EvaluationDriverError(
                "Codex evaluation ownership must remain unknown"
            )
        super().__init__(
            manifest=manifest,
            binding=binding,
            observed_image_digest=observed_image_digest,
            observed_command=observed_command,
            capabilities=CODEX_CAPABILITIES,
        )
        self._supplier_thread_id: str | None = None
        self._supplier_turn_id: str | None = None
        self._items: dict[str, str] = {}

    def initialize(self, frame: Mapping[str, Any]) -> None:
        _, params = require_jsonrpc_method(frame, "initialize", notification=False)
        require_exact_keys(
            params,
            required={"clientInfo", "capabilities"},
        )
        client_info = params["clientInfo"]
        capabilities = params["capabilities"]
        if not isinstance(client_info, Mapping) or not isinstance(
            capabilities, Mapping
        ):
            raise EvaluationDriverError("Codex initialize payload is invalid")
        require_exact_keys(
            client_info, required={"name", "version"}, optional={"title"}
        )
        if not all(
            isinstance(client_info.get(key), str) and client_info.get(key)
            for key in ("name", "version")
        ):
            raise EvaluationDriverError("Codex client identity is invalid")
        if set(capabilities) != {"experimentalApi"} or capabilities.get(
            "experimentalApi"
        ) is not False:
            raise EvaluationDriverError("Codex experimental API is unavailable")
        self.initialize_protocol()

    def open(self, frame: Mapping[str, Any], *, supplier_thread_id: object) -> None:
        _, params = require_jsonrpc_method(frame, "thread/start", notification=False)
        require_exact_keys(
            params,
            required={"model", "cwd", "approvalPolicy", "sandbox"},
        )
        if (
            params["model"] != "controlled-route"
            or params["cwd"] != "/workspace"
            or params["approvalPolicy"] != "never"
            or params["sandbox"] != "read-only"
        ):
            raise EvaluationDriverError("Codex thread is not deployment controlled")
        self._supplier_thread_id = canonical_supplier_id(supplier_thread_id)
        self.open_session(supplier_thread_id)

    def start_turn(
        self,
        frame: Mapping[str, Any],
        *,
        supplier_turn_id: object,
    ) -> None:
        _, params = require_jsonrpc_method(frame, "turn/start", notification=False)
        require_exact_keys(
            params,
            required={"threadId", "input"},
            optional={"clientUserMessageId"},
        )
        self._require_thread(params["threadId"])
        self._require_text_input(params["input"])
        self._supplier_turn_id = canonical_supplier_id(supplier_turn_id)
        super().start_turn(supplier_turn_id)

    def event(self, frame: Mapping[str, Any]) -> HarnessEventEnvelope | None:
        method = frame.get("method")
        if not isinstance(method, str):
            raise EvaluationDriverError("Codex event method is invalid")
        self.reject_native_method(method)
        _, params = require_jsonrpc_method(frame, method, notification=True)
        if method == "turn/started":
            self._require_correlated_params(params, require_turn=True)
            return self.emit(
                supplier_event_id=f"turn-started:{self.sequence + 1}",
                kind=HarnessEventKind.MESSAGE,
                payload={"state": "turn_started"},
            )
        if method == "turn/completed":
            self._require_correlated_params(params, require_turn=True)
            if self._items:
                raise EvaluationDriverError(
                    "Codex turn completed with unfinished item lifecycles"
                )
            result = self.emit(
                supplier_event_id=f"turn-completed:{self.sequence + 1}",
                kind=HarnessEventKind.TURN_COMPLETED,
                payload={"status": self._turn_status(params)},
            )
            self._supplier_turn_id = None
            return result
        if method == "turn/plan/updated":
            self._require_correlated_params(params, require_turn=True)
            plan = params.get("plan")
            if not isinstance(plan, list):
                raise EvaluationDriverError("Codex plan event is invalid")
            return self.emit(
                supplier_event_id=f"plan:{self.sequence + 1}",
                kind=HarnessEventKind.PLAN,
                payload={
                    "plan": [
                        {
                            "step": str(item.get("step") or "")[:512],
                            "status": str(item.get("status") or "")[:32],
                        }
                        for item in plan[:64]
                        if isinstance(item, Mapping)
                    ]
                },
            )
        if method == "thread/tokenUsage/updated":
            self._require_thread(params.get("threadId"))
            usage = params.get("tokenUsage") or params.get("usage")
            if not isinstance(usage, Mapping):
                raise EvaluationDriverError("Codex usage event is invalid")
            return self.emit(
                supplier_event_id=f"usage:{self.sequence + 1}",
                kind=HarnessEventKind.USAGE,
                payload={
                    key: value
                    for key, value in usage.items()
                    if key in {"inputTokens", "outputTokens", "totalTokens"}
                    and isinstance(value, int)
                    and not isinstance(value, bool)
                    and value >= 0
                },
            )
        if method == "item/started":
            return self._item_started(params)
        if method == "item/completed":
            return self._item_completed(params)
        if method in {"item/agentMessage/delta", "item/plan/delta"}:
            return self._item_delta(method, params)
        if method.startswith("item/reasoning/"):
            self._require_correlated_params(params, require_turn=True)
            return None
        raise EvaluationDriverError("Codex event is unavailable")

    def server_request(self, frame: Mapping[str, Any]) -> HarnessEventEnvelope:
        method = frame.get("method")
        if not isinstance(method, str):
            raise EvaluationDriverError("Codex server request method is invalid")
        self.reject_native_method(method)
        require_jsonrpc_method(frame, method, notification=False)
        # request_user_input and dynamic tool calls are experimental in the
        # stable 0.149.0 schema.  V20 never opts into experimentalApi.
        raise EvaluationDriverError("Codex server request is unavailable")

    def steer_turn(self, frame: Mapping[str, Any]) -> None:
        _, params = require_jsonrpc_method(frame, "turn/steer", notification=False)
        require_exact_keys(
            params,
            required={"threadId", "expectedTurnId", "input"},
            optional={"clientUserMessageId"},
        )
        self._require_correlated_params(params, require_turn=True, turn_key="expectedTurnId")
        self._require_text_input(params["input"])
        self.steer()

    def interrupt_turn(self, frame: Mapping[str, Any]) -> None:
        _, params = require_jsonrpc_method(
            frame, "turn/interrupt", notification=False
        )
        require_exact_keys(params, required={"threadId", "turnId"})
        self._require_correlated_params(params, require_turn=True)
        self.interrupt()
        self._supplier_turn_id = None
        self._items.clear()

    def resume_session(
        self,
        frame: Mapping[str, Any],
        *,
        resumed_binding: HarnessBinding,
    ) -> None:
        _, params = require_jsonrpc_method(
            frame, "thread/resume", notification=False
        )
        require_exact_keys(params, required={"threadId"})
        self._require_thread(params["threadId"])
        self.resume(resumed_binding)

    def close_session(self, frame: Mapping[str, Any]) -> None:
        _, params = require_jsonrpc_method(
            frame, "thread/unsubscribe", notification=False
        )
        require_exact_keys(params, required={"threadId"})
        self._require_thread(params["threadId"])
        self.close()

    @staticmethod
    def reject_native_method(method: str) -> None:
        if method in _NATIVE_REQUESTS or method.startswith(_NATIVE_PREFIXES):
            raise CodexNativeToolRejected(
                "Codex native side-effect surface is unavailable"
            )

    def _item_started(self, params: Mapping[str, Any]) -> HarnessEventEnvelope | None:
        self._require_correlated_params(params, require_turn=True)
        item = params.get("item")
        if not isinstance(item, Mapping):
            raise EvaluationDriverError("Codex item is invalid")
        item_id = canonical_supplier_id(item.get("id"))
        item_type = str(item.get("type") or "")
        if item_type in _NATIVE_ITEM_TYPES:
            raise CodexNativeToolRejected("Codex native item is unavailable")
        if item_id in self._items:
            raise EvaluationDriverError("Codex item start was replayed")
        self._items[item_id] = item_type
        if item_type == "reasoning":
            return None
        if item_type not in {"agentMessage", "plan"}:
            raise EvaluationDriverError("Codex item type is unavailable")
        return self.emit(
            supplier_event_id=f"item-started:{item_id}",
            kind=(
                HarnessEventKind.PLAN
                if item_type == "plan"
                else HarnessEventKind.MESSAGE
            ),
            payload={"item_id": item_id[:128], "state": "started"},
        )

    def _item_delta(
        self, method: str, params: Mapping[str, Any]
    ) -> HarnessEventEnvelope:
        self._require_correlated_params(params, require_turn=True)
        item_id = canonical_supplier_id(params.get("itemId"))
        expected = "plan" if method == "item/plan/delta" else "agentMessage"
        if self._items.get(item_id) != expected:
            raise EvaluationDriverError("Codex item delta is out of order")
        text = params.get("delta")
        if not isinstance(text, str):
            raise EvaluationDriverError("Codex item delta is invalid")
        return self.emit(
            supplier_event_id=f"item-delta:{item_id}:{self.sequence + 1}",
            kind=(
                HarnessEventKind.PLAN
                if expected == "plan"
                else HarnessEventKind.MESSAGE
            ),
            payload={"item_id": item_id[:128], "delta": text[:16384]},
        )

    def _item_completed(
        self, params: Mapping[str, Any]
    ) -> HarnessEventEnvelope | None:
        self._require_correlated_params(params, require_turn=True)
        item = params.get("item")
        if not isinstance(item, Mapping):
            raise EvaluationDriverError("Codex completed item is invalid")
        item_id = canonical_supplier_id(item.get("id"))
        item_type = str(item.get("type") or "")
        if self._items.pop(item_id, None) != item_type:
            raise EvaluationDriverError("Codex item completion is out of order")
        if item_type == "reasoning":
            return None
        if item_type not in {"agentMessage", "plan"}:
            raise EvaluationDriverError("Codex completed item type is unavailable")
        return self.emit(
            supplier_event_id=f"item-completed:{item_id}",
            kind=(
                HarnessEventKind.PLAN
                if item_type == "plan"
                else HarnessEventKind.MESSAGE
            ),
            payload={"item_id": item_id[:128], "state": "completed"},
        )

    def _require_thread(self, value: object) -> None:
        if canonical_supplier_id(value) != self._supplier_thread_id:
            raise EvaluationDriverError("Codex frame targets another thread")

    def _require_correlated_params(
        self,
        params: Mapping[str, Any],
        *,
        require_turn: bool,
        turn_key: str = "turnId",
    ) -> None:
        self._require_thread(params.get("threadId"))
        if require_turn:
            supplier_turn = params.get(turn_key)
            if supplier_turn is None and turn_key == "turnId":
                turn = params.get("turn")
                if isinstance(turn, Mapping):
                    supplier_turn = turn.get("id")
            if canonical_supplier_id(supplier_turn) != self._supplier_turn_id:
                raise EvaluationDriverError("Codex frame targets another turn")

    @staticmethod
    def _require_text_input(value: object) -> None:
        if not isinstance(value, list) or not value:
            raise EvaluationDriverError("Codex turn input is invalid")
        for item in value:
            if (
                not isinstance(item, Mapping)
                or set(item) != {"type", "text"}
                or item.get("type") != "text"
                or not isinstance(item.get("text"), str)
            ):
                raise EvaluationDriverError(
                    "Codex local file, Skill and media inputs are unavailable"
                )

    @staticmethod
    def _turn_status(params: Mapping[str, Any]) -> str:
        turn = params.get("turn")
        if not isinstance(turn, Mapping):
            raise EvaluationDriverError("Codex completed turn is invalid")
        return str(turn.get("status") or "")[:64]
