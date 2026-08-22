"""Operator smoke for a published Hub contract through Runtime HITL.

This harness intentionally has no public HTTP tool-call endpoint.  It exercises
the same provider and middleware used by AI Runtime, records one explicit local
operator decision, performs exactly one real call, then revokes the contract and
proves that the live Hub session and Runtime tool disappear immediately.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from server.mcp.hub import (
    HubSocketBridge,
    MCPHubService,
    MCPHubStore,
    arguments_digest,
)
from server.mcp.hub_review import MCPHubReviewService, MCPHubReviewStore
from server.xpert_runtime import (
    CapabilityRegistry,
    MiddlewareContext,
    MiddlewarePipeline,
    RuntimeApprovalStore,
    RuntimeInterrupt,
    RuntimeMiddlewareSpec,
    RuntimeToolCall,
    build_human_in_the_loop_middleware,
    run_tool_with_runtime,
)
from server.xpert_runtime.hub_toolset import HubMCPToolsetProvider


def _hub_approval(
    service: MCPHubService,
    candidate: dict[str, Any],
    tool: Any,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return {
        "candidate_id": candidate["candidate_id"],
        "tenant_id": service.tenant_id,
        "owner_id": service.owner_id,
        "server_name": candidate["server_name"],
        "version": candidate["version"],
        "origin": candidate["origin"],
        "schema_digest": tool.metadata["hub_schema_digest"],
        "tool_schema_digest": tool.metadata["hub_tool_schema_digest"],
        "contract_id": tool.metadata["hub_contract_id"],
        "contract_fingerprint": tool.metadata["hub_contract_fingerprint"],
        "arguments_digest": arguments_digest(arguments),
    }


async def run(storage_dir: Path, server_name: str, upstream_tool: str) -> dict[str, Any]:
    store = MCPHubStore(storage_dir)
    hub = MCPHubService(
        store,
        tenant_id=os.getenv("MODELMIRROR_DEFAULT_TENANT_ID", "local"),
        owner_id=os.getenv("MODELMIRROR_DEFAULT_OWNER_ID", "local"),
        bridge=HubSocketBridge(),
    )
    review_store = MCPHubReviewStore(store)
    review = MCPHubReviewService(
        hub,
        review_store,
        signing_key=os.getenv("MCP_HUB_CONTRACT_SIGNING_KEY", ""),
    )
    hub.contract_registry = review.contracts
    hub.set_review_service(review)
    provider = HubMCPToolsetProvider(hub)

    candidates = [
        item
        for item in store.list_candidates(hub.tenant_id, hub.owner_id)
        if item["server_name"] == server_name and item["state"] == "active"
    ]
    if len(candidates) != 1:
        raise RuntimeError(f"active_candidate_count:{len(candidates)}")
    candidate = candidates[0]
    tools = [
        item
        for item in await provider.list_tools()
        if item.metadata.get("hub_candidate_id") == candidate["candidate_id"]
        and item.metadata.get("hub_upstream_tool_name") == upstream_tool
    ]
    if len(tools) != 1:
        raise RuntimeError(f"runtime_tool_count:{len(tools)}")
    tool = tools[0]
    if not (
        tool.requires_approval
        and tool.sensitive
        and not tool.read_only
        and not tool.parallel_safe
        and tool.metadata.get("retry_on_failure") is False
    ):
        raise RuntimeError("runtime_policy_not_fail_closed")

    approval_store = RuntimeApprovalStore(storage_dir / "runtime-approval-smoke")
    smoke_execution_id = uuid.uuid4().hex
    task_id = f"hub-runtime-smoke-task-{smoke_execution_id}"
    run_id = f"hub-runtime-smoke-run-{smoke_execution_id}"
    capabilities = CapabilityRegistry()
    capabilities.register("mcp_tools", provider)
    pipeline = MiddlewarePipeline(
        [
            build_human_in_the_loop_middleware(
                RuntimeMiddlewareSpec(
                    node_id="hub-runtime-smoke-hitl",
                    middleware_id="human_in_the_loop",
                    config={"interrupt_on_tools": "*", "allow_edit": False},
                ),
                approval_store,
            )
        ]
    )
    context = MiddlewareContext(
        task_id=task_id,
        trace_id=run_id,
        metadata={
            "run_id": run_id,
            "node_id": "hub-runtime-smoke-agent",
            "node_title": "Hub Runtime smoke",
        },
    )
    arguments: dict[str, Any] = {}
    hub_approval = _hub_approval(hub, candidate, tool, arguments)
    base_call = RuntimeToolCall(
        tool_name=tool.name,
        arguments=arguments,
        metadata={
            "iteration": 1,
            "tool_input_schema": dict(tool.input_schema),
            "hub_approval": hub_approval,
        },
    )

    try:
        await run_tool_with_runtime(base_call, capabilities, pipeline, context)
    except RuntimeInterrupt as interrupted:
        pending = approval_store.require(interrupted.approval_id)
    else:
        raise RuntimeError("runtime_call_skipped_hitl")
    if pending.status != "pending" or pending.allowed_decisions != ["approve", "reject"]:
        raise RuntimeError("runtime_approval_policy_mismatch")
    if "Registry 收录不代表安全认证" not in pending.description:
        raise RuntimeError("runtime_registry_warning_missing")
    decided = approval_store.decide(
        pending.approval_id,
        revision=pending.revision,
        decision="approve",
        operator="local-preview-operator",
    )
    result = await run_tool_with_runtime(
        RuntimeToolCall(
            tool_name=tool.name,
            arguments=arguments,
            metadata={
                **dict(base_call.metadata),
                "resolved_approval": asdict(decided),
            },
        ),
        capabilities,
        pipeline,
        context,
    )
    if result.is_error:
        raise RuntimeError("runtime_representative_call_failed")
    connected_before_revoke = hub.get_candidate(candidate["candidate_id"])["connected"]
    if not connected_before_revoke:
        raise RuntimeError("runtime_session_not_connected")

    contract_id = str(tool.metadata["hub_contract_id"])
    revoked = await review.revoke(contract_id, "independent preview acceptance")
    connected_after_revoke = hub.get_candidate(candidate["candidate_id"])["connected"]
    remaining_tools = [
        item
        for item in await provider.list_tools()
        if item.metadata.get("hub_candidate_id") == candidate["candidate_id"]
    ]
    if connected_after_revoke or remaining_tools:
        raise RuntimeError("contract_revocation_not_immediate")

    output = result.output or ""
    return {
        "server_name": candidate["server_name"],
        "candidate_id": candidate["candidate_id"],
        "runtime_tool": tool.name,
        "upstream_tool": upstream_tool,
        "approval_id": decided.approval_id,
        "approval_decision": decided.decision,
        "provider_call_count": 1,
        "retry_on_failure": False,
        "result_bytes": len(output.encode("utf-8")),
        "result_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "connected_before_revoke": connected_before_revoke,
        "connected_after_revoke": connected_after_revoke,
        "revoked_contract_id": revoked["contract_id"],
        "disconnected_candidates": revoked["disconnected_candidates"],
        "runtime_tools_after_revoke": len(remaining_tools),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--storage-dir", type=Path, required=True)
    parser.add_argument("--server-name", required=True)
    parser.add_argument("--upstream-tool", required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.storage_dir, args.server_name, args.upstream_tool))
    print("runtime_approval_proof=" + json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
