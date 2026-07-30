from __future__ import annotations

import json
import os
import sys
import time
from typing import Any


MODE = os.getenv("FAKE_ACP_MODE", "normal")
PROMPT_REQUEST_ID: int | None = None


def send(frame: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(frame, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def result(request_id: int, payload: dict[str, Any]) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": payload})


def update(payload: dict[str, Any]) -> None:
    send(
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": "fake-session", "update": payload},
        }
    )


def handle(frame: dict[str, Any]) -> None:
    global PROMPT_REQUEST_ID

    method = frame.get("method")
    request_id = frame.get("id")
    params = frame.get("params", {})

    if method == "initialize":
        if MODE == "timeout":
            time.sleep(10)
            return
        assert params == {
            "protocolVersion": 1,
            "clientCapabilities": {},
            "clientInfo": {
                "name": "modelmirror-coding-runtime",
                "version": "1.0.0",
            },
        }
        result(
            request_id,
            {
                "protocolVersion": 1,
                "agentCapabilities": {},
                "agentInfo": {"name": "fake-acp", "version": "1.0"},
            },
        )
        return

    if method == "session/new":
        assert params == {
            "cwd": "/workspace",
            "additionalDirectories": [],
            "mcpServers": [],
        }
        result(request_id, {"sessionId": "fake-session"})
        return

    if method == "session/prompt":
        PROMPT_REQUEST_ID = request_id
        if MODE == "malformed":
            sys.stdout.write("{broken\n")
            sys.stdout.flush()
            return
        if MODE == "exit":
            raise SystemExit(9)
        if MODE == "cancel":
            update(
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "working"},
                }
            )
            return

        update(
            {
                "sessionUpdate": "plan",
                "entries": [
                    {
                        "content": "Inspect relevant files",
                        "priority": "high",
                        "status": "in_progress",
                    }
                ],
            }
        )
        update(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "tool-1",
                "title": "Read source",
                "kind": "read",
                "status": "pending",
            }
        )
        update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "tool-1",
                "title": "Read source",
                "kind": "read",
                "status": "completed",
                "rawOutput": "must not cross the adapter boundary",
            }
        )
        if MODE in {"permission", "permission-no-reject"}:
            options = [
                {
                    "optionId": "allow-once",
                    "name": "Allow",
                    "kind": "allow_once",
                }
            ]
            if MODE == "permission":
                options.append(
                    {
                        "optionId": "reject-once",
                        "name": "Reject",
                        "kind": "reject_once",
                    }
                )
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 900,
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": "fake-session",
                        "toolCall": {"toolCallId": "write-1"},
                        "options": options,
                    },
                }
            )
            return

        update(
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "Read-only answer"},
            }
        )
        result(request_id, {"stopReason": "end_turn"})
        return

    if method == "session/cancel":
        if PROMPT_REQUEST_ID is not None:
            result(PROMPT_REQUEST_ID, {"stopReason": "cancelled"})
            PROMPT_REQUEST_ID = None
        return

    if request_id == 900 and MODE in {"permission", "permission-no-reject"}:
        outcome = frame.get("result", {}).get("outcome", {})
        selected = outcome.get("optionId", outcome.get("outcome", "unknown"))
        update(
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": f"permission:{selected}"},
            }
        )
        if PROMPT_REQUEST_ID is not None:
            result(PROMPT_REQUEST_ID, {"stopReason": "end_turn"})
            PROMPT_REQUEST_ID = None


for raw_line in sys.stdin:
    try:
        decoded = json.loads(raw_line)
        assert isinstance(decoded, dict)
        handle(decoded)
    except SystemExit:
        raise
    except BaseException as exc:
        send(
            {
                "jsonrpc": "2.0",
                "id": decoded.get("id") if isinstance(decoded, dict) else 0,
                "error": {"code": -32000, "message": type(exc).__name__},
            }
        )
