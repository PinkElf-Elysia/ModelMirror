"""Operator smoke for a reviewed Catalog remote MCP through Runtime HITL.

The harness intentionally uses the same provider and human-in-the-loop
middleware as AI Runtime.  It has no credential arguments and records only
bounded, non-secret result evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path.cwd()))

try:
    from server import main as main_module
    from server.mcp.hub import arguments_digest
    from server.mcp.remote_review import RemoteTargetRefV1
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
except ModuleNotFoundError:
    import main as main_module  # type: ignore[no-redef]
    from mcp.hub import arguments_digest  # type: ignore[no-redef]
    from mcp.remote_review import RemoteTargetRefV1  # type: ignore[no-redef]
    from xpert_runtime import (  # type: ignore[no-redef]
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


def _remote_approval(
    service: Any,
    tool: Any,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    metadata = tool.metadata
    return {
        "target_type": "catalog_project",
        "target_id": metadata["remote_target_id"],
        "upstream_tool_name": metadata["remote_upstream_tool_name"],
        "tenant_id": service.tenant_id,
        "owner_id": service.owner_id,
        "version": metadata["remote_version"],
        "origin": metadata["remote_origin"],
        "source_digest": metadata["remote_source_digest"],
        "auth_context_digest": metadata["remote_auth_context_digest"],
        "arguments_digest": arguments_digest(arguments),
        "schema_digest": metadata["remote_schema_digest"],
        "tool_schema_digest": metadata["remote_tool_schema_digest"],
        "contract_id": metadata["remote_contract_id"],
        "contract_fingerprint": metadata["remote_contract_fingerprint"],
    }


async def run(
    approval_dir: Path,
    project_id: str,
    upstream_tool: str,
    *,
    arguments: dict[str, Any],
    operator: str,
) -> dict[str, Any]:
    service = main_module.mcp_remote_review_service
    provider = main_module.workflow_remote_mcp_provider
    tools = [
        item
        for item in await provider.list_tools()
        if item.provider == "mcp_remote"
        and item.metadata.get("remote_target_id") == project_id
        and item.metadata.get("remote_upstream_tool_name") == upstream_tool
    ]
    if len(tools) != 1:
        raise RuntimeError(f"runtime_tool_count:{len(tools)}")
    tool = tools[0]
    if not (
        tool.requires_approval
        and tool.sensitive
        and not tool.read_only
        and not tool.parallel_safe
        and not tool.public_app_allowed
        and tool.metadata.get("retry_on_failure") is False
    ):
        raise RuntimeError("runtime_policy_not_fail_closed")

    approval_store = RuntimeApprovalStore(approval_dir)
    execution_id = uuid.uuid4().hex
    task_id = f"catalog-runtime-smoke-task-{execution_id}"
    run_id = f"catalog-runtime-smoke-run-{execution_id}"
    capabilities = CapabilityRegistry()
    capabilities.register("mcp_tools", provider)
    pipeline = MiddlewarePipeline(
        [
            build_human_in_the_loop_middleware(
                RuntimeMiddlewareSpec(
                    node_id="catalog-runtime-smoke-hitl",
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
            "node_id": "catalog-runtime-smoke-agent",
            "node_title": "Catalog Runtime smoke",
        },
    )
    remote_approval = _remote_approval(service, tool, arguments)
    base_call = RuntimeToolCall(
        tool_name=tool.name,
        arguments=arguments,
        metadata={
            "iteration": 1,
            "tool_input_schema": dict(tool.input_schema),
            "remote_approval": remote_approval,
        },
    )

    try:
        await run_tool_with_runtime(base_call, capabilities, pipeline, context)
    except RuntimeInterrupt as interrupted:
        pending = approval_store.require(interrupted.approval_id)
    else:
        raise RuntimeError("runtime_call_skipped_hitl")
    if pending.status != "pending" or pending.allowed_decisions != [
        "approve",
        "reject",
    ]:
        raise RuntimeError("runtime_approval_policy_mismatch")
    if project_id not in pending.description or tool.name not in pending.description:
        raise RuntimeError("runtime_approval_identity_missing")

    decided = approval_store.decide(
        pending.approval_id,
        revision=pending.revision,
        decision="approve",
        operator=operator,
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

    target = RemoteTargetRefV1(
        target_type="catalog_project",
        target_id=project_id,
    )
    ledger = service.store.runtime_execution(
        decided.approval_id,
        tenant_id=service.tenant_id,
        owner_id=service.owner_id,
        target=target,
        contract_fingerprint=remote_approval["contract_fingerprint"],
        tool_name=tool.name,
        args_digest=remote_approval["arguments_digest"],
    )
    if ledger is None or ledger[0] != "completed":
        raise RuntimeError("runtime_ledger_not_completed")

    output = result.output or ""
    remaining = [
        item
        for item in await provider.list_tools()
        if item.provider == "mcp_remote"
        and item.metadata.get("remote_target_id") == project_id
    ]
    return {
        "project_id": project_id,
        "runtime_tool": tool.name,
        "upstream_tool": upstream_tool,
        "arguments_digest": remote_approval["arguments_digest"],
        "approval_id": decided.approval_id,
        "approval_decision": decided.decision,
        "ledger_state": ledger[0],
        "provider_call_count": 1,
        "retry_on_failure": False,
        "result_bytes": len(output.encode("utf-8")),
        "result_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "runtime_tools_after_call": len(remaining),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approval-dir", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--upstream-tool", required=True)
    parser.add_argument("--arguments-json", required=True)
    parser.add_argument("--operator", required=True)
    args = parser.parse_args()
    arguments = json.loads(args.arguments_json)
    if not isinstance(arguments, dict):
        parser.error("--arguments-json must decode to an object")
    proof = asyncio.run(
        run(
            args.approval_dir,
            args.project_id,
            args.upstream_tool,
            arguments=arguments,
            operator=args.operator,
        )
    )
    print(
        "catalog_runtime_approval_proof="
        + json.dumps(proof, ensure_ascii=False, sort_keys=True),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
