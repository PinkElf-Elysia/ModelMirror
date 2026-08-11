"""Isolated UDS acceptance for Wave-19A rate-limit and timeout handling."""

from __future__ import annotations

import base64
import json
import time

from . import smoke_database_wave19a_live as live


ADAPTER_ID = "pab1it0-prometheus-mcp-server"


def _configuration() -> dict[str, object]:
    encoded = live.CONFIGURATION_B64
    live.CONFIGURATION_B64 = ""
    if not encoded:
        raise RuntimeError("wave19a_failure_configuration_missing")
    try:
        value = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("wave19a_failure_configuration_invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeError("wave19a_failure_configuration_invalid")
    return value


def _assert_redacted_tool_error(response: dict[str, object], label: str) -> None:
    result = response.get("result")
    if not isinstance(result, dict) or result.get("isError") is not True:
        raise RuntimeError(f"wave19a_{label}_not_rejected")
    encoded = json.dumps(result, ensure_ascii=False)
    for forbidden in (
        "rate_limit_probe",
        "timeout_probe",
        "database_rate_limited",
        "database_upstream_timeout",
        "http://",
        "https://",
    ):
        if forbidden in encoded:
            raise RuntimeError(f"wave19a_{label}_error_not_redacted")


def main() -> None:
    client = live.RpcClient(ADAPTER_ID, _configuration())
    try:
        initialized = client.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "modelmirror-wave19a-failure-smoke", "version": "1"},
            },
        )
        live._assert_success(initialized, "initialize")
        client.notify("notifications/initialized")

        limited = client.request(
            "tools/call",
            {"name": "execute_query", "arguments": {"query": "rate_limit_probe"}},
        )
        _assert_redacted_tool_error(limited, "rate_limit")

        recovered = client.request(
            "tools/call",
            {"name": "execute_query", "arguments": {"query": "up"}},
        )
        live._assert_success(recovered, "rate_limit_recovery")

        started = time.monotonic()
        timed_out = client.request(
            "tools/call",
            {"name": "execute_query", "arguments": {"query": "timeout_probe"}},
        )
        elapsed = time.monotonic() - started
        _assert_redacted_tool_error(timed_out, "timeout")
        if not 10.0 <= elapsed <= 16.0:
            raise RuntimeError("wave19a_timeout_duration_invalid")

        recovered = client.request(
            "tools/call",
            {"name": "execute_query", "arguments": {"query": "up"}},
        )
        live._assert_success(recovered, "timeout_recovery")
    finally:
        client.close()

    print(
        json.dumps(
            {
                "ok": True,
                "adapter_id": ADAPTER_ID,
                "rate_limit": "redacted_and_recovered",
                "timeout": "redacted_and_recovered",
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
