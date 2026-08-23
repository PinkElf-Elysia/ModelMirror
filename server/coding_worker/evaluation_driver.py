from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .contracts import SAFE_ID, StrictModel
from .harness_protocol import (
    HarnessBinding,
    HarnessCapabilityMaturity,
    HarnessCapabilityState,
    HarnessDescriptor,
    HarnessEventEnvelope,
    HarnessEventKind,
    HarnessLifecycleKernel,
    HarnessPersistenceLevel,
    HarnessProtocolError,
    HarnessRequestKind,
    HarnessRequestRef,
    HarnessResponse,
    HarnessResponseOutcome,
    HarnessSessionRef,
    HarnessToolOwnership,
    HarnessTurnRef,
)


class EvaluationDriverError(RuntimeError):
    """An evaluation-only supplier frame failed the frozen V20 contract."""

    code = "evaluation_driver_protocol_invalid"


class EvaluationDriverManifest(StrictModel):
    """Deployment-owned executable and supply-chain binding.

    The manifest is intentionally not part of TaskSpec or any public API.  A
    deployment must materialize it after building the evaluation image so the
    final image digest can be verified before a supplier process is started.
    """

    profile: Literal["evaluation"] = "evaluation"
    driver_id: str
    protocol_id: str = Field(min_length=1, max_length=64)
    protocol_version: str = Field(min_length=1, max_length=64)
    implementation_version: str = Field(min_length=1, max_length=64)
    package_name: str = Field(min_length=1, max_length=128)
    package_version: str = Field(min_length=1, max_length=64)
    package_integrity: str = Field(min_length=16, max_length=256)
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    command: tuple[str, ...] = Field(min_length=1, max_length=16)
    command_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_ownership: HarnessToolOwnership
    persistence: HarnessPersistenceLevel
    production_route: Literal[False] = False

    @model_validator(mode="after")
    def validate_fixed_manifest(self) -> "EvaluationDriverManifest":
        if SAFE_ID.fullmatch(self.driver_id) is None:
            raise ValueError("evaluation driver id is invalid")
        if any(not value or "\x00" in value for value in self.command):
            raise ValueError("evaluation driver command is invalid")
        if not self.command[0].startswith("/"):
            raise ValueError("evaluation driver executable must be absolute")
        if self.command_sha256 != command_sha256(self.command):
            raise ValueError("evaluation driver command digest does not match")
        return self

    def attest(
        self,
        *,
        observed_image_digest: str,
        observed_command: Sequence[str],
    ) -> None:
        if observed_image_digest != self.image_digest:
            raise EvaluationDriverError("evaluation image digest does not match")
        if tuple(observed_command) != self.command:
            raise EvaluationDriverError("evaluation executable is not registered")
        if command_sha256(observed_command) != self.command_sha256:
            raise EvaluationDriverError("evaluation executable digest does not match")

    def descriptor(
        self,
        capabilities: Mapping[str, bool],
    ) -> HarnessDescriptor:
        return HarnessDescriptor(
            protocol_id=self.protocol_id,
            protocol_version=self.protocol_version,
            implementation_version=self.implementation_version,
            schema_sha256=self.schema_sha256,
            tool_ownership=self.tool_ownership,
            persistence=self.persistence,
            capabilities={
                name: HarnessCapabilityState(
                    supported=supported,
                    available=False,
                    maturity=HarnessCapabilityMaturity.EXPERIMENTAL,
                    reason="evaluation-only driver is not registered for production",
                )
                for name, supported in capabilities.items()
            },
        )


class EvaluationBrokerMcp(StrictModel):
    """Trusted deployment projection for the one task-bound Broker MCP."""

    name: Literal["modelmirror-broker"] = "modelmirror-broker"
    url: str

    @model_validator(mode="after")
    def validate_loopback_endpoint(self) -> "EvaluationBrokerMcp":
        parsed = urlsplit(self.url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed.port is None
            or parsed.path != "/mcp"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("evaluation Broker MCP must use a fixed loopback endpoint")
        return self

    def acp_config(self) -> dict[str, Any]:
        return {
            "type": "http",
            "name": self.name,
            "url": self.url,
            "headers": [],
        }


class StandardEvaluationDriver:
    """Shared exact-correlation adapter for evaluation-only standard drivers."""

    def __init__(
        self,
        *,
        manifest: EvaluationDriverManifest,
        binding: HarnessBinding,
        observed_image_digest: str,
        observed_command: Sequence[str],
        capabilities: Mapping[str, bool],
    ) -> None:
        manifest.attest(
            observed_image_digest=observed_image_digest,
            observed_command=observed_command,
        )
        descriptor = manifest.descriptor(capabilities)
        if binding.descriptor != descriptor:
            raise EvaluationDriverError("evaluation binding descriptor does not match")
        self.manifest = manifest
        self.binding = binding
        self.kernel = HarnessLifecycleKernel()
        self.descriptor = descriptor
        self.initialized = False
        self.session: HarnessSessionRef | None = None
        self.turn: HarnessTurnRef | None = None
        self.sequence = 0
        self._pending: dict[str, HarnessRequestRef] = {}

    def initialize_protocol(self) -> None:
        if self.initialized:
            raise EvaluationDriverError("evaluation connection was already initialized")
        self._kernel_call(self.kernel.initialize, self.descriptor)
        self.initialized = True

    def open_session(self, supplier_session_id: object) -> HarnessSessionRef:
        if self.session is not None:
            raise EvaluationDriverError("evaluation session was already opened")
        session = HarnessSessionRef(
            binding=self.binding,
            session_id=safe_supplier_id("session", supplier_session_id),
        )
        self._kernel_call(self.kernel.open_session, session)
        self.session = session
        return session

    def start_turn(self, supplier_turn_id: object) -> HarnessTurnRef:
        session = self.require_session()
        if self.turn is not None:
            raise EvaluationDriverError("evaluation session already has an active turn")
        turn = HarnessTurnRef(
            session=session,
            turn_id=safe_supplier_id("turn", supplier_turn_id),
        )
        self._kernel_call(self.kernel.start_turn, turn)
        self.turn = turn
        return turn

    def emit(
        self,
        *,
        supplier_event_id: object,
        kind: HarnessEventKind,
        payload: Mapping[str, Any] | None = None,
        request: HarnessRequestRef | None = None,
    ) -> HarnessEventEnvelope:
        session = self.require_session()
        self.sequence += 1
        envelope = HarnessEventEnvelope(
            event_id=safe_supplier_id("event", supplier_event_id),
            sequence=self.sequence,
            session=session,
            turn=self.turn,
            request=request,
            kind=kind,
            payload=dict(payload or {}),
        )
        self._kernel_call(self.kernel.accept_event, envelope)
        if kind is HarnessEventKind.TURN_COMPLETED:
            self.turn = None
        return envelope

    def request(
        self,
        *,
        supplier_request_id: object,
        kind: HarnessRequestKind,
        payload: Mapping[str, Any] | None = None,
    ) -> HarnessEventEnvelope:
        turn = self.require_turn()
        request_key = canonical_supplier_id(supplier_request_id)
        if request_key in self._pending:
            raise EvaluationDriverError("evaluation request id was replayed")
        request = HarnessRequestRef(
            turn=turn,
            request_id=safe_supplier_id("request", supplier_request_id),
            kind=kind,
        )
        event = self.emit(
            supplier_event_id=f"request:{request_key}",
            kind=HarnessEventKind.REQUEST,
            payload=payload,
            request=request,
        )
        self._pending[request_key] = request
        return event

    def resolve(
        self,
        *,
        supplier_request_id: object,
        outcome: HarnessResponseOutcome,
        payload: Mapping[str, Any] | None = None,
    ) -> HarnessResponse:
        request_key = canonical_supplier_id(supplier_request_id)
        request = self._pending.pop(request_key, None)
        if request is None:
            raise EvaluationDriverError("evaluation request is not pending")
        response = HarnessResponse(
            ref=request,
            outcome=outcome,
            payload=dict(payload or {}),
        )
        self._kernel_call(self.kernel.resolve_request, response)
        return response

    def steer(self) -> None:
        self._kernel_call(self.kernel.steer, self.require_turn())

    def interrupt(self) -> None:
        turn = self.require_turn()
        self._kernel_call(self.kernel.interrupt, turn)
        self.turn = None

    def resume(self, binding: HarnessBinding) -> HarnessSessionRef:
        previous = self.require_session()
        if self.turn is not None:
            raise EvaluationDriverError("active turn must stop before evaluation resume")
        if binding.descriptor != previous.binding.descriptor:
            raise EvaluationDriverError("evaluation descriptor changed during resume")
        resumed = HarnessSessionRef(binding=binding, session_id=previous.session_id)
        self._kernel_call(self.kernel.resume_session, previous, resumed)
        self.binding = binding
        self.session = resumed
        self.sequence = 0
        return resumed

    def close(self) -> None:
        if self._pending:
            raise EvaluationDriverError("pending evaluation request must settle before close")
        session = self.require_session()
        self._kernel_call(self.kernel.close_session, session)
        self.session = None

    def require_session(self) -> HarnessSessionRef:
        if self.session is None:
            raise EvaluationDriverError("evaluation session is not open")
        return self.session

    def require_turn(self) -> HarnessTurnRef:
        if self.turn is None:
            raise EvaluationDriverError("evaluation turn is not active")
        return self.turn

    @staticmethod
    def _kernel_call(call: Any, *args: Any) -> Any:
        try:
            return call(*args)
        except (HarnessProtocolError, ValueError) as exc:
            raise EvaluationDriverError(
                "evaluation frame violated the Harness lifecycle"
            ) from exc


def command_sha256(command: Sequence[str]) -> str:
    payload = json.dumps(
        list(command), ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonical_supplier_id(value: object) -> str:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise EvaluationDriverError("supplier correlation id is invalid")
    text = str(value)
    if not text or len(text) > 256:
        raise EvaluationDriverError("supplier correlation id is invalid")
    return text


def safe_supplier_id(prefix: str, value: object) -> str:
    text = canonical_supplier_id(value)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    safe = f"{prefix}_{digest}"
    if SAFE_ID.fullmatch(safe) is None:  # pragma: no cover - fixed prefix/hash
        raise EvaluationDriverError("normalized supplier id is invalid")
    return safe


def require_jsonrpc_method(
    frame: Mapping[str, Any],
    method: str,
    *,
    notification: bool | None = None,
) -> tuple[object | None, Mapping[str, Any]]:
    if frame.get("method") != method:
        raise EvaluationDriverError("supplier method does not match the lifecycle")
    if frame.get("jsonrpc", "2.0") != "2.0":
        raise EvaluationDriverError("supplier frame is not JSON-RPC 2.0")
    request_id = frame.get("id")
    if notification is True and request_id is not None:
        raise EvaluationDriverError("supplier notification unexpectedly has an id")
    if notification is False and request_id is None:
        raise EvaluationDriverError("supplier request omitted its id")
    params = frame.get("params", {})
    if not isinstance(params, Mapping):
        raise EvaluationDriverError("supplier params are invalid")
    return request_id, params


def require_exact_keys(
    payload: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    keys = set(payload)
    optional = optional or set()
    if not required.issubset(keys) or not keys.issubset(required | optional):
        raise EvaluationDriverError("supplier payload contains unavailable fields")
