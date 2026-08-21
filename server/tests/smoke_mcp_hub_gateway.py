"""Fixed acceptance helper for the first MCP Hub remote bridge.

This script is intentionally not a general MCP client. It contains one
Registry-reviewed anonymous endpoint and only prints fixed contract evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import time
import uuid
from typing import Any

from server.mcp.hub import (
    HubSocketBridge,
    HubUnknownOutcomeError,
    MCPHubService,
    MCPHubStore,
    arguments_digest,
    normalize_registry_entry,
)


QT_DOCS_ENTRY: dict[str, Any] = {
    "server": {
        "name": "io.qt.qt-docs-mcp/qt-documentation",
        "version": "0.2.0",
        "title": "Qt Documentation",
        "description": "Search public Qt documentation.",
        "remotes": [
            {
                "type": "streamable-http",
                "url": "https://qt-docs-mcp.qt.io/mcp",
            }
        ],
    },
    "_meta": {
        "io.modelcontextprotocol.registry/official": {
            "status": "active",
            "isLatest": True,
        }
    },
}

TIMEOUT_ENTRY: dict[str, Any] = {
    "server": {
        "name": "io.modelmirror.acceptance/hub-timeout",
        "version": "1.0.0",
        "title": "ModelMirror Hub Timeout Acceptance",
        "description": "Fixed controlled TLS timeout fixture.",
        "remotes": [
            {
                "type": "streamable-http",
                "url": "https://hub-timeout.modelmirror.test/mcp",
            }
        ],
    },
    "_meta": {
        "io.modelcontextprotocol.registry/official": {
            "status": "active",
            "isLatest": True,
        }
    },
}


class DisconnectAcceptanceBridge(HubSocketBridge):
    async def call(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        # This marker is emitted only after MCPHubService has rechecked Schema
        # and durably recorded the execution ledger as started.
        print("disconnect_window=ready", flush=True)
        await asyncio.sleep(30)
        return await super().call(session_id, tool_name, arguments)


async def run(mode: str) -> None:
    store = MCPHubStore()
    entry = TIMEOUT_ENTRY if mode == "timeout-call" else QT_DOCS_ENTRY
    store.replace_snapshot(
        "hub_sync_accept",
        [normalize_registry_entry(entry)],
        "acceptance",
    )
    service = MCPHubService(
        store,
        tenant_id="acceptance",
        owner_id="acceptance",
        bridge=(
            DisconnectAcceptanceBridge()
            if mode == "disconnect-call"
            else None
        ),
    )
    await service.start()
    try:
        server = service.get_server(entry["server"]["name"], entry["server"]["version"])
        candidate = service.create_candidate(
            server["server_name"],
            server["version"],
            server["remotes"][0]["remote_id"],
        )
        candidate = await service.preflight(candidate["candidate_id"])
        print("preflight_state=" + candidate["state"])
        print("origin=" + candidate["origin"])
        print("schema_digest=" + candidate["schema_digest"])
        print("tools=" + ",".join(item["name"] for item in candidate["tools"]))
        if mode == "preflight":
            return

        if mode == "timeout-call":
            service.reviewed_contracts[
                (
                    candidate["server_name"],
                    candidate["version"],
                    "https://hub-timeout.modelmirror.test/mcp",
                )
            ] = {
                "schema_digest": candidate["schema_digest"],
                "tool_schema_digests": {
                    item["name"]: item["schema_digest"]
                    for item in candidate["tools"]
                },
            }

        candidate = await service.activate(
            candidate["candidate_id"], candidate["schema_digest"]
        )
        expected_tool = "slow_read" if mode == "timeout-call" else "qt_documentation_search"
        runtime = next(item for item in service.runtime_tools() if item["upstream_tool_name"] == expected_tool)
        arguments: dict[str, Any] = {
            "query": "fixed-timeout" if mode == "timeout-call" else "QTimer singleShot"
        }
        approval = {
            "approval_id": str(uuid.uuid4()),
            "status": "decided",
            "decision": "approve",
            "tool_name": runtime["name"],
            "metadata": {
                "hub_approval": {
                    "candidate_id": candidate["candidate_id"],
                    "tenant_id": service.tenant_id,
                    "owner_id": service.owner_id,
                    "server_name": candidate["server_name"],
                    "version": candidate["version"],
                    "origin": candidate["origin"],
                    "schema_digest": candidate["schema_digest"],
                    "tool_schema_digest": runtime["tool_schema_digest"],
                    "arguments_digest": arguments_digest(arguments),
                }
            },
        }
        started = time.monotonic()
        try:
            result = await service.execute(
                candidate_id=candidate["candidate_id"],
                runtime_tool_name=runtime["name"],
                upstream_tool_name=runtime["upstream_tool_name"],
                arguments=arguments,
                approval=approval,
            )
        except HubUnknownOutcomeError:
            if mode not in {"disconnect-call", "timeout-call"}:
                raise
            elapsed = time.monotonic() - started
            if mode == "timeout-call" and not 18 <= elapsed <= 28:
                raise RuntimeError("timeout_acceptance_elapsed_out_of_bounds")
            candidate = service.get_candidate(candidate["candidate_id"])
            print("unknown_outcome=ok")
            print("candidate_state=" + candidate["state"])
            if mode == "timeout-call":
                print(f"timeout_elapsed_seconds={elapsed:.3f}")
            try:
                await service.execute(
                    candidate_id=candidate["candidate_id"],
                    runtime_tool_name=runtime["name"],
                    upstream_tool_name=runtime["upstream_tool_name"],
                    arguments=arguments,
                    approval=approval,
                )
            except HubUnknownOutcomeError:
                print("retry_old_operation=denied")
            else:
                raise RuntimeError("unknown_operation_retry_was_not_denied")
            return
        if mode == "disconnect-call":
            raise RuntimeError("disconnect_acceptance_did_not_fail_closed")
        print("approved_call=ok")
        print("result_items=" + str(len(result.get("content") or [])))
        print("retry_on_failure=false")
    finally:
        await service.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("preflight", "approved-call", "disconnect-call", "timeout-call"),
        default="preflight",
    )
    args = parser.parse_args()
    asyncio.run(run(args.mode))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
