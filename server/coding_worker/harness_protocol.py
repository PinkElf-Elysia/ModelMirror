from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from .contracts import SAFE_ID, SAFE_ROUTE, StrictModel


class HarnessProtocolError(RuntimeError):
    """A private harness message violated the frozen lifecycle contract."""


class HarnessCapabilityMaturity(StrEnum):
    STABLE = "stable"
    EXPERIMENTAL = "experimental"


class HarnessPersistenceLevel(StrEnum):
    NONE = "none"
    SESSION_RESUME = "session_resume"
    DURABLE_CHECKPOINT = "durable_checkpoint"


class HarnessToolOwnership(StrEnum):
    BROKER_ONLY = "broker_only"
    NONE = "none"
    HARNESS_NATIVE = "harness_native"
    UNKNOWN = "unknown"


class HarnessRequestKind(StrEnum):
    BROKER_TOOL = "broker_tool"
    APPROVAL = "approval"
    USER_INPUT = "user_input"


class HarnessResponseOutcome(StrEnum):
    COMPLETED = "completed"
    APPROVED = "approved"
    DECLINED = "declined"
    CANCELLED = "cancelled"
    FAILED = "failed"


class HarnessEventKind(StrEnum):
    MESSAGE = "message"
    PLAN = "plan"
    TODO = "todo"
    USAGE = "usage"
    REQUEST = "request"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TURN_COMPLETED = "turn_completed"
    ERROR = "error"


class HarnessCapabilityState(StrictModel):
    supported: bool = False
    available: bool = False
    maturity: HarnessCapabilityMaturity = HarnessCapabilityMaturity.STABLE
    reason: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_availability(self) -> "HarnessCapabilityState":
        if self.available and not self.supported:
            raise ValueError("available harness capability must be supported")
        if not self.available and self.reason is None:
            raise ValueError("unavailable harness capability requires a reason")
        return self


class HarnessDescriptor(StrictModel):
    protocol_id: str = Field(min_length=1, max_length=64)
    protocol_version: str = Field(min_length=1, max_length=64)
    implementation_version: str = Field(min_length=1, max_length=64)
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_ownership: HarnessToolOwnership
    persistence: HarnessPersistenceLevel
    capabilities: dict[str, HarnessCapabilityState] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_capability_names(self) -> "HarnessDescriptor":
        if any(SAFE_ID.fullmatch(name) is None for name in self.capabilities):
            raise ValueError("harness capability name is invalid")
        return self

    def capability(self, name: str) -> HarnessCapabilityState:
        if SAFE_ID.fullmatch(name) is None:
            raise ValueError("harness capability name is invalid")
        declared = self.capabilities.get(name)
        if declared is not None:
            return declared
        return HarnessCapabilityState(
            supported=False,
            available=False,
            maturity=HarnessCapabilityMaturity.EXPERIMENTAL,
            reason="capability was not declared",
        )


class HarnessDescriptorObservation(StrictModel):
    """A stable descriptor observed from one concrete sidecar generation."""

    descriptor: HarnessDescriptor
    sidecar_generation: str = Field(pattern=r"^[0-9a-f]{32}$")


class HarnessBinding(StrictModel):
    task_id: str
    route_id: str
    slot_id: str
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    driver_generation: int = Field(ge=1)
    descriptor: HarnessDescriptor

    @model_validator(mode="after")
    def validate_identifiers(self) -> "HarnessBinding":
        if SAFE_ID.fullmatch(self.task_id) is None:
            raise ValueError("harness task id is invalid")
        if SAFE_ROUTE.fullmatch(self.route_id) is None:
            raise ValueError("harness route id is invalid")
        if SAFE_ID.fullmatch(self.slot_id) is None:
            raise ValueError("harness slot id is invalid")
        return self


class HarnessSessionRef(StrictModel):
    binding: HarnessBinding
    session_id: str

    @model_validator(mode="after")
    def validate_session_id(self) -> "HarnessSessionRef":
        if SAFE_ID.fullmatch(self.session_id) is None:
            raise ValueError("harness session id is invalid")
        return self


class HarnessTurnRef(StrictModel):
    session: HarnessSessionRef
    turn_id: str

    @model_validator(mode="after")
    def validate_turn_id(self) -> "HarnessTurnRef":
        if SAFE_ID.fullmatch(self.turn_id) is None:
            raise ValueError("harness turn id is invalid")
        return self


class HarnessRequestRef(StrictModel):
    turn: HarnessTurnRef
    request_id: str
    kind: HarnessRequestKind

    @model_validator(mode="after")
    def validate_request_id(self) -> "HarnessRequestRef":
        if SAFE_ID.fullmatch(self.request_id) is None:
            raise ValueError("harness request id is invalid")
        return self


class HarnessEventEnvelope(StrictModel):
    event_id: str
    sequence: int = Field(ge=1)
    session: HarnessSessionRef
    turn: HarnessTurnRef | None = None
    request: HarnessRequestRef | None = None
    kind: HarnessEventKind
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_correlations(self) -> "HarnessEventEnvelope":
        if SAFE_ID.fullmatch(self.event_id) is None:
            raise ValueError("harness event id is invalid")
        if self.turn is not None and self.turn.session != self.session:
            raise ValueError("harness event turn belongs to another session")
        if self.request is not None:
            if self.turn is None or self.request.turn != self.turn:
                raise ValueError("harness event request belongs to another turn")
            if self.kind is not HarnessEventKind.REQUEST:
                raise ValueError("harness request reference requires a request event")
        if self.kind is HarnessEventKind.REQUEST and self.request is None:
            raise ValueError("harness request event omitted its request reference")
        if self.kind is HarnessEventKind.TURN_COMPLETED and self.turn is None:
            raise ValueError("turn completion requires a turn reference")
        return self


class HarnessRequest(StrictModel):
    ref: HarnessRequestRef
    payload: dict[str, Any] = Field(default_factory=dict)


class HarnessResponse(StrictModel):
    ref: HarnessRequestRef
    outcome: HarnessResponseOutcome
    payload: dict[str, Any] = Field(default_factory=dict)


class HarnessCheckpoint(StrictModel):
    checkpoint_id: str
    session: HarnessSessionRef
    persistence: HarnessPersistenceLevel
    workspace_tree_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_checkpoint(self) -> "HarnessCheckpoint":
        if SAFE_ID.fullmatch(self.checkpoint_id) is None:
            raise ValueError("harness checkpoint id is invalid")
        if self.persistence is HarnessPersistenceLevel.NONE:
            raise ValueError("non-persistent harness cannot issue a checkpoint")
        return self


class HarnessLifecycleKernel:
    """Deterministic fencing for normalized private harness lifecycles."""

    def __init__(self) -> None:
        self._descriptor: HarnessDescriptor | None = None
        self._sessions: dict[tuple[str, str], HarnessSessionRef] = {}
        self._turns: dict[tuple[str, str], HarnessTurnRef] = {}
        self._sequences: dict[tuple[str, str, int], int] = {}
        self._event_ids: set[tuple[object, ...]] = set()
        self._pending_requests: dict[tuple[object, ...], HarnessRequestRef] = {}
        self._resolved_requests: set[tuple[object, ...]] = set()

    def initialize(self, descriptor: HarnessDescriptor) -> None:
        if self._descriptor is not None:
            raise HarnessProtocolError("harness connection was already initialized")
        self._descriptor = descriptor

    def open_session(self, session: HarnessSessionRef) -> None:
        self._require_initialized()
        key = self._session_key(session)
        if key in self._sessions:
            raise HarnessProtocolError("harness session was already opened")
        if session.binding.descriptor != self._descriptor:
            raise HarnessProtocolError("harness session descriptor changed after initialize")
        self._sessions[key] = session

    def start_turn(self, turn: HarnessTurnRef) -> None:
        self._require_session(turn.session)
        key = self._session_key(turn.session)
        if key in self._turns:
            raise HarnessProtocolError("harness session already has an active turn")
        self._turns[key] = turn

    def accept_event(self, event: HarnessEventEnvelope) -> None:
        self._require_session(event.session)
        sequence_key = (
            event.session.binding.task_id,
            event.session.session_id,
            event.session.binding.driver_generation,
        )
        expected = self._sequences.get(sequence_key, 0) + 1
        if event.sequence != expected:
            raise HarnessProtocolError("harness event sequence is not contiguous")
        event_key = self._event_key(event)
        if event_key in self._event_ids:
            raise HarnessProtocolError("harness event was replayed")
        if event.turn is not None:
            self._require_turn(event.turn)
        if (
            event.kind is HarnessEventKind.TURN_COMPLETED
            and event.turn is not None
            and self._has_pending_for_turn(event.turn)
        ):
            raise HarnessProtocolError(
                "pending request must settle before harness turn completion"
            )
        if event.request is not None:
            if self._has_pending_for_turn(event.request.turn):
                raise HarnessProtocolError(
                    "pending request must settle before another harness request"
                )
            request_key = self._request_key(event.request)
            if (
                request_key in self._pending_requests
                or request_key in self._resolved_requests
            ):
                raise HarnessProtocolError("harness request id was replayed")
            self._pending_requests[request_key] = event.request
        self._sequences[sequence_key] = event.sequence
        self._event_ids.add(event_key)
        if event.kind is HarnessEventKind.TURN_COMPLETED:
            assert event.turn is not None
            self._turns.pop(self._session_key(event.turn.session), None)

    def resolve_request(self, response: HarnessResponse) -> None:
        request_key = self._request_key(response.ref)
        pending = self._pending_requests.get(request_key)
        if pending is None or pending != response.ref:
            raise HarnessProtocolError("harness request is not pending for this turn")
        self._require_turn(response.ref.turn)
        self._pending_requests.pop(request_key)
        self._resolved_requests.add(request_key)

    def steer(self, turn: HarnessTurnRef) -> None:
        self._require_turn(turn)

    def interrupt(self, turn: HarnessTurnRef) -> None:
        self._require_turn(turn)
        if self._has_pending_for_turn(turn):
            raise HarnessProtocolError(
                "pending request must settle before harness turn interruption"
            )
        self._turns.pop(self._session_key(turn.session), None)

    def resume_session(self, previous: HarnessSessionRef, resumed: HarnessSessionRef) -> None:
        self._require_initialized()
        self._require_session(previous)
        if (
            previous.binding.task_id != resumed.binding.task_id
            or previous.binding.route_id != resumed.binding.route_id
            or previous.binding.binding_sha256 != resumed.binding.binding_sha256
            or previous.session_id != resumed.session_id
            or resumed.binding.driver_generation <= previous.binding.driver_generation
            or resumed.binding.descriptor != self._descriptor
        ):
            raise HarnessProtocolError("harness resume binding is incompatible")
        previous_key = self._session_key(previous)
        if previous_key in self._turns:
            raise HarnessProtocolError("active turn must be interrupted before resume")
        if any(
            ref.turn.session == previous for ref in self._pending_requests.values()
        ):
            raise HarnessProtocolError(
                "pending request must settle before harness session resume"
            )
        self._sessions.pop(previous_key)
        self._sessions[self._session_key(resumed)] = resumed

    def close_session(self, session: HarnessSessionRef) -> None:
        self._require_session(session)
        key = self._session_key(session)
        if key in self._turns:
            raise HarnessProtocolError("active turn must finish before session close")
        if any(ref.turn.session == session for ref in self._pending_requests.values()):
            raise HarnessProtocolError("pending request must settle before session close")
        self._sessions.pop(key)

    def _require_initialized(self) -> None:
        if self._descriptor is None:
            raise HarnessProtocolError("harness connection is not initialized")

    def _require_session(self, session: HarnessSessionRef) -> None:
        self._require_initialized()
        if self._sessions.get(self._session_key(session)) != session:
            raise HarnessProtocolError("harness session binding is stale or cross-task")

    def _require_turn(self, turn: HarnessTurnRef) -> None:
        self._require_session(turn.session)
        if self._turns.get(self._session_key(turn.session)) != turn:
            raise HarnessProtocolError("harness turn is stale or not active")

    def _has_pending_for_turn(self, turn: HarnessTurnRef) -> bool:
        return any(ref.turn == turn for ref in self._pending_requests.values())

    @staticmethod
    def _session_key(session: HarnessSessionRef) -> tuple[str, str]:
        return session.binding.task_id, session.session_id

    @staticmethod
    def _correlation_key(session: HarnessSessionRef) -> tuple[object, ...]:
        binding = session.binding
        return (
            binding.task_id,
            binding.route_id,
            binding.slot_id,
            binding.binding_sha256,
            binding.driver_generation,
            session.session_id,
        )

    @classmethod
    def _event_key(cls, event: HarnessEventEnvelope) -> tuple[object, ...]:
        return (*cls._correlation_key(event.session), event.event_id)

    @classmethod
    def _request_key(cls, request: HarnessRequestRef) -> tuple[object, ...]:
        return (
            *cls._correlation_key(request.turn.session),
            request.turn.turn_id,
            request.kind.value,
            request.request_id,
        )
