from __future__ import annotations

import pytest

from ai_research_worker.protocol import ProtocolError, parse_request


def test_protocol_accepts_only_four_fixed_actions() -> None:
    assert parse_request({"protocolVersion": 1, "action": "health"}).action == "health"
    start = parse_request(
        {
            "protocolVersion": 1,
            "action": "start",
            "runId": "ar0_safe-1",
            "caseId": "task_error",
        }
    )
    assert start.case_id == "task_error"

    for payload in (
        {"protocolVersion": 2, "action": "health"},
        {"protocolVersion": 1, "action": "exec", "runId": "ar0_safe"},
        {
            "protocolVersion": 1,
            "action": "start",
            "runId": "../escape",
            "caseId": "success",
        },
        {
            "protocolVersion": 1,
            "action": "start",
            "runId": "ar0_safe",
            "caseId": "success",
            "command": "id",
        },
        {
            "protocolVersion": 1,
            "action": "start",
            "runId": "ar0_safe",
            "caseId": "unknown",
        },
    ):
        with pytest.raises(ProtocolError):
            parse_request(payload)
