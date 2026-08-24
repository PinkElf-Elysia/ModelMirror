from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 65_536
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
ALLOWED_ACTIONS = frozenset({"health", "start", "status", "cancel"})
ALLOWED_CASES = frozenset({"success", "task_error", "long_running_cancel"})


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class WorkerRequest:
    action: str
    run_id: str | None = None
    case_id: str | None = None


def parse_request(payload: Any) -> WorkerRequest:
    if not isinstance(payload, dict):
        raise ProtocolError("request must be a JSON object")
    if set(payload) - {"protocolVersion", "action", "runId", "caseId"}:
        raise ProtocolError("request contains unsupported fields")
    if payload.get("protocolVersion") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocolVersion")
    action = payload.get("action")
    if action not in ALLOWED_ACTIONS:
        raise ProtocolError("unsupported action")
    run_id = payload.get("runId")
    case_id = payload.get("caseId")
    if action != "health":
        if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
            raise ProtocolError("invalid runId")
    elif run_id is not None or case_id is not None:
        raise ProtocolError("health accepts no run fields")
    if action == "start":
        if case_id not in ALLOWED_CASES:
            raise ProtocolError("invalid caseId")
    elif case_id is not None:
        raise ProtocolError("caseId is only valid for start")
    return WorkerRequest(action=action, run_id=run_id, case_id=case_id)


def response(*, ok: bool, **fields: Any) -> dict[str, Any]:
    return {"protocolVersion": PROTOCOL_VERSION, "ok": ok, **fields}
