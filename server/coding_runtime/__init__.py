"""Supplier-neutral contracts for the experimental Coding Agent runtime."""

from .acp_client import (
    AcpClient,
    AcpProcessConfig,
    AcpProcessExited,
    AcpProtocolError,
    AcpRequestTimeout,
)
from .adapter import CodingAgentAdapter, FakeCodingAgentAdapter
from .models import (
    CodingEvent,
    CodingEventKind,
    CodingSession,
    CodingSessionState,
    InvalidCodingSessionTransition,
)

__all__ = [
    "AcpClient",
    "AcpProcessConfig",
    "AcpProcessExited",
    "AcpProtocolError",
    "AcpRequestTimeout",
    "CodingAgentAdapter",
    "CodingEvent",
    "CodingEventKind",
    "CodingSession",
    "CodingSessionState",
    "FakeCodingAgentAdapter",
    "InvalidCodingSessionTransition",
]
