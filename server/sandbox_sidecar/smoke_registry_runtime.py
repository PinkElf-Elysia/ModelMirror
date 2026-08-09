"""Isolated real-call acceptance for the public Terraform Registry adapter."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult


EXPECTED_TOOLS = {
    "get_latest_provider_version",
    "get_provider_capabilities",
    "get_provider_details",
    "search_modules",
    "get_module_details",
    "get_latest_module_version",
}
BLOCKED_TOOL_NAMES = {
    "apply",
    "destroy",
    "plan",
    "run",
    "create_workspace",
    "create_run",
    "private_registry",
}


def _decode_result(result: CallToolResult) -> Any:
    if result.isError:
        raise RuntimeError("terraform_registry_tool_failed")
    structured = result.structuredContent
    if isinstance(structured, dict) and "result" in structured:
        return structured["result"]
    texts = [
        str(item.text)
        for item in result.content
        if getattr(item, "type", "") == "text"
    ]
    if not texts:
        raise RuntimeError("terraform_registry_tool_result_missing")
    text = "\n".join(texts)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


async def _call(session: ClientSession, name: str, arguments: dict[str, Any]) -> Any:
    result = await session.call_tool(name, arguments)
    return _decode_result(result)


async def run(socket_path: Path, proxy_path: Path) -> None:
    if not socket_path.is_absolute() or not proxy_path.is_absolute():
        raise RuntimeError("runtime_smoke_paths_must_be_absolute")
    handshake = base64.urlsafe_b64encode(
        json.dumps(
            {"settings": {}, "credentials": {}},
            separators=(",", ":"),
        ).encode("utf-8")
    ).decode("ascii")
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "MCP_TOKEN_SOCKET_PATH": str(socket_path),
        "MCP_TOKEN_HANDSHAKE_B64": handshake,
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(proxy_path), "terraform-mcp"],
        env=env,
        cwd=Path("/tmp"),
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = {tool.name for tool in listed.tools}
            if names != EXPECTED_TOOLS or names & BLOCKED_TOOL_NAMES:
                raise RuntimeError("terraform_registry_tool_contract_drift")
            try:
                await session.call_tool("apply", {})
            except McpError:
                pass
            else:
                raise RuntimeError("terraform_registry_blocked_tool_was_callable")

            provider_input = {"namespace": "hashicorp", "name": "random"}
            provider_version = str(
                await _call(session, "get_latest_provider_version", provider_input)
            ).strip()
            if re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,79}", provider_version) is None:
                raise RuntimeError("terraform_registry_provider_version_invalid")

            capabilities = await _call(
                session,
                "get_provider_capabilities",
                {**provider_input, "version": "latest"},
            )
            if not isinstance(capabilities, dict):
                raise RuntimeError("terraform_registry_capabilities_invalid")
            categories = capabilities.get("capabilities")
            if not isinstance(categories, list) or not categories:
                raise RuntimeError("terraform_registry_capabilities_empty")
            provider_doc_id = ""
            for category in categories:
                examples = category.get("examples") if isinstance(category, dict) else None
                if not isinstance(examples, list):
                    continue
                for example in examples:
                    candidate = str(
                        example.get("provider_doc_id")
                        if isinstance(example, dict)
                        else ""
                    )
                    if candidate.isdigit():
                        provider_doc_id = candidate
                        break
                if provider_doc_id:
                    break
            if not provider_doc_id:
                raise RuntimeError("terraform_registry_provider_doc_id_missing")
            provider_doc = str(
                await _call(
                    session,
                    "get_provider_details",
                    {"provider_doc_id": provider_doc_id},
                )
            )
            if not provider_doc.strip() or len(provider_doc.encode("utf-8")) > 128 * 1024:
                raise RuntimeError("terraform_registry_provider_doc_invalid")

            modules = await _call(
                session,
                "search_modules",
                {"module_query": "vpc", "current_offset": 0},
            )
            module_items = modules.get("modules") if isinstance(modules, dict) else None
            if not isinstance(module_items, list) or not module_items:
                raise RuntimeError("terraform_registry_module_search_empty")
            module_id = str(module_items[0].get("module_id") or "")
            if len(module_id.split("/")) != 4:
                raise RuntimeError("terraform_registry_module_id_invalid")
            module = await _call(
                session,
                "get_module_details",
                {"module_id": module_id},
            )
            if not isinstance(module, dict) or module.get("module_id") != module_id.lower():
                raise RuntimeError("terraform_registry_module_details_invalid")
            module_version = str(
                await _call(
                    session,
                    "get_latest_module_version",
                    {
                        "module_publisher": "terraform-aws-modules",
                        "module_name": "vpc",
                        "module_provider": "aws",
                    },
                )
            ).strip()
            if not module_version:
                raise RuntimeError("terraform_registry_module_version_invalid")

    print("adapter=terraform-mcp")
    print("input.provider=hashicorp/random")
    print(f"result.provider_version={provider_version}")
    print(f"result.capability_categories={len(categories)}")
    print(f"result.provider_doc_bytes={len(provider_doc.encode('utf-8'))}")
    print("input.module_query=vpc")
    print(f"result.first_module_id={module_id}")
    print(f"result.module_inputs={len(module.get('inputs') or [])}")
    print(f"result.latest_module_version={module_version}")
    print("blocked_tools=apply,destroy,plan,hcp,tfe,private_registry")
    print("blocked_call.apply=denied")
    print("terraform_registry_runtime_smoke=ok")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--proxy", required=True)
    args = parser.parse_args()
    import asyncio

    asyncio.run(run(Path(args.socket), Path(args.proxy)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
