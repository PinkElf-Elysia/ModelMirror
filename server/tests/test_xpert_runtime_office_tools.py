from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.xpert_runtime import (
    OFFICE_DELETE_TOOL_NAMES,
    OFFICE_EXCEL_TOOL_NAMES,
    OFFICE_MUTATING_TOOL_NAMES,
    OFFICE_POWERPOINT_TOOL_NAMES,
    OFFICE_TOOLS,
    OFFICE_TOOL_REQUIREMENTS,
    OFFICE_WORD_TOOL_NAMES,
    ClientToolConnectionManager,
    ClientToolStore,
    OfficeToolsetProvider,
    RuntimeInterrupt,
    RuntimeToolCall,
    RuntimeToolError,
    SandboxWorkspaceStore,
    client_tool_schema_hash,
)
from server.xpert_runtime import client_tool_api


def pair_office_host(
    store: ClientToolStore,
    *,
    office_app: str = "word",
):
    pairing, code = store.create_pairing(name="Office Host", host_type="office")
    tools = [
        tool for tool in OFFICE_TOOLS if tool.name.startswith(f"office_{office_app}_")
    ]
    schemas = {tool.name: client_tool_schema_hash(tool) for tool in tools}
    host, token = store.consume_pairing(
        code,
        version="1.0.0",
        host_type="office",
        office_app=office_app,
        capabilities=[{"name": tool.name} for tool in tools],
        schema_hashes=schemas,
        document_binding={
            "bound": True,
            "binding_id": "document-binding",
            "title": "Quarterly plan.docx",
        },
        requirement_sets=["WordApi 1.3"],
    )
    store.connect_host(
        host.host_id,
        connection_id="office-connection",
        version="1.0.0",
        host_type="office",
        office_app=office_app,
        capabilities=[{"name": tool.name} for tool in tools],
        schema_hashes=schemas,
        document_binding={
            "bound": True,
            "binding_id": "document-binding",
            "title": "Quarterly plan.docx",
        },
        requirement_sets=["WordApi 1.3"],
    )
    return host, token


def office_call(
    host_id: str,
    tool_name: str,
    arguments: dict | None = None,
    **config,
) -> RuntimeToolCall:
    return RuntimeToolCall(
        tool_name,
        arguments or {},
        {
            "task_id": "task-office",
            "run_id": "run-office",
            "node_id": "office-agent",
            "iteration": 1,
            "tool_call_id": f"call-{tool_name}",
            "office_automation_config": {
                "clientHostId": host_id,
                "host": "all",
                "allowDeletes": False,
                "allowImageInsert": False,
                "timeoutSeconds": 1800,
                "requireBoundDocument": True,
                **config,
            },
        },
    )


def test_office_catalog_has_exactly_22_bounded_tools() -> None:
    names = {tool.name for tool in OFFICE_TOOLS}

    assert len(OFFICE_TOOLS) == 22
    assert names == (
        OFFICE_POWERPOINT_TOOL_NAMES
        | OFFICE_WORD_TOOL_NAMES
        | OFFICE_EXCEL_TOOL_NAMES
    )
    assert len(OFFICE_POWERPOINT_TOOL_NAMES) == 9
    assert len(OFFICE_WORD_TOOL_NAMES) == 6
    assert len(OFFICE_EXCEL_TOOL_NAMES) == 7
    assert OFFICE_DELETE_TOOL_NAMES <= OFFICE_MUTATING_TOOL_NAMES
    for tool in OFFICE_TOOLS:
        assert tool.provider == "office"
        assert tool.input_schema["type"] == "object"
        assert tool.input_schema["additionalProperties"] is False
        assert len(client_tool_schema_hash(tool)) == 64
        assert OFFICE_TOOL_REQUIREMENTS[tool.name]


def test_office_host_is_persistent_and_old_hosts_default_to_chrome(tmp_path: Path) -> None:
    store = ClientToolStore(tmp_path / "runtime")
    office, _token = pair_office_host(store)
    payload = store.serialize_host(office)

    assert payload["host_type"] == "office"
    assert payload["office_app"] == "word"
    assert payload["document_binding"]["binding_id"] == "document-binding"
    assert "Quarterly plan.docx" in json.dumps(payload)

    restored = ClientToolStore(tmp_path / "runtime")
    assert restored.require_host(office.host_id).host_type == "office"

    snapshot_path = tmp_path / "runtime" / "client_tools.json"
    snapshot = json.loads(snapshot_path.read_text("utf-8"))
    snapshot["hosts"][0].pop("host_type", None)
    snapshot["hosts"][0].pop("office_app", None)
    snapshot["hosts"][0].pop("document_binding", None)
    snapshot_path.write_text(json.dumps(snapshot), "utf-8")
    migrated = ClientToolStore(tmp_path / "runtime")
    assert migrated.require_host(office.host_id).host_type == "chrome"


@pytest.mark.asyncio
async def test_office_provider_filters_host_and_requires_document_binding(
    tmp_path: Path,
) -> None:
    store = ClientToolStore(tmp_path)
    host, _token = pair_office_host(store, office_app="word")
    provider = OfficeToolsetProvider(store)

    tools = await provider.list_tools_for_host(
        host.host_id,
        set(),
        require_bound_tab=True,
    )
    assert {tool.name for tool in tools} == OFFICE_WORD_TOOL_NAMES

    with pytest.raises(RuntimeInterrupt):
        await provider.prepare_dispatch(
            office_call(host.host_id, "office_word_snapshot")
        )

    store.unbind_host(host.host_id)
    assert await provider.list_tools_for_host(
        host.host_id,
        set(),
        require_bound_tab=True,
    ) == []


@pytest.mark.asyncio
async def test_office_provider_enforces_host_delete_and_image_policy(
    tmp_path: Path,
) -> None:
    store = ClientToolStore(tmp_path)
    word, _token = pair_office_host(store, office_app="word")
    provider = OfficeToolsetProvider(store)

    with pytest.raises(RuntimeToolError, match="outside the configured host scope"):
        await provider.prepare_dispatch(
            office_call(
                word.host_id,
                "office_word_snapshot",
                host="excel",
            )
        )

    ppt_store = ClientToolStore(tmp_path / "ppt")
    ppt, _token = pair_office_host(ppt_store, office_app="powerpoint")
    ppt_provider = OfficeToolsetProvider(ppt_store)
    delete_call = office_call(
        ppt.host_id,
        "office_powerpoint_delete_slide",
        {"slideIndex": 1, "confirm": True},
    )
    with pytest.raises(RuntimeToolError, match="allowDeletes"):
        await ppt_provider.prepare_dispatch(delete_call)

    approved_delete = office_call(
        ppt.host_id,
        "office_powerpoint_delete_slide",
        {"slideIndex": 1, "confirm": True},
        allowDeletes=True,
    )
    with pytest.raises(RuntimeInterrupt):
        await ppt_provider.prepare_dispatch(approved_delete)

    image_call = office_call(
        ppt.host_id,
        "office_powerpoint_insert_image",
        {"artifact_id": "artifact-1"},
    )
    with pytest.raises(RuntimeToolError, match="image insertion is disabled"):
        await ppt_provider.prepare_dispatch(image_call)


def test_office_capabilities_manifest_and_taskpane_are_safe(tmp_path: Path) -> None:
    store = ClientToolStore(tmp_path)
    client_tool_api.configure_runtime_client_tools(
        store,
        ClientToolConnectionManager(store),
    )
    app = FastAPI()
    app.include_router(client_tool_api.router)

    with TestClient(app) as client:
        capabilities = client.get("/api/runtime/office-host/capabilities")
        assert capabilities.status_code == 200
        assert len(capabilities.json()["tools"]) == 22
        image_tool = next(
            tool
            for tool in capabilities.json()["tools"]
            if tool["name"] == "office_powerpoint_insert_image"
        )
        assert {item["set"] for item in image_tool["requirements"]} == {
            "PowerPointApi",
            "ImageCoercion",
        }
        assert "api_key" not in json.dumps(capabilities.json()).lower()

        manifest = client.get("/api/runtime/office-host/manifest.xml")
        assert manifest.status_code == 200
        assert '<Host Name="Document"/>' in manifest.text
        assert '<Host Name="Workbook"/>' in manifest.text
        assert '<Host Name="Presentation"/>' in manifest.text
        assert "https://localhost:8443/taskpane.html" in manifest.text

    source = (
        Path(__file__).resolve().parents[1] / "office_addin" / "taskpane.js"
    ).read_text("utf-8")
    assert "Office.onReady" in source
    assert "Word.run" in source
    assert "Excel.run" in source
    assert "PowerPoint.run" in source
    assert "Office.CoercionType.Image" in source
    assert "addImage(" not in source
    assert "eval(" not in source
    assert "new Function" not in source


def test_office_image_artifact_is_limited_to_request_scope(tmp_path: Path) -> None:
    store = ClientToolStore(tmp_path / "client-runtime")
    sandbox_store = SandboxWorkspaceStore(
        tmp_path / "sandbox-runtime",
        workspace_root=tmp_path / "workspaces",
    )
    host, token = pair_office_host(store, office_app="powerpoint")
    workspace = sandbox_store.get_or_create_workspace(
        scope_type="conversation",
        scope_id="conversation-office",
        node_id="office-agent",
    )
    artifact_id = "artifact-office-image"
    relative_path = "artifacts/diagram.png"
    artifact_path = sandbox_store.workspace_root / workspace.workspace_id / relative_path
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    image_bytes = b"\x89PNG\r\n\x1a\nfixture"
    artifact_path.write_bytes(image_bytes)
    sandbox_store.register_artifact(
        artifact_id=artifact_id,
        workspace_id=workspace.workspace_id,
        filename="diagram.png",
        relative_path=relative_path,
        size_bytes=len(image_bytes),
        sha256=hashlib.sha256(image_bytes).hexdigest(),
    )
    request = store.create_request(
        operation_id="operation-office-image",
        tool_call_id="call-office-image",
        host_id=host.host_id,
        task_id="task-office",
        run_id="run-office",
        node_id="office-agent",
        scope_type="conversation",
        scope_id="conversation-office",
        tool_name="office_powerpoint_insert_image",
        arguments={"artifact_id": artifact_id},
        schema_hash=client_tool_schema_hash(
            next(
                tool
                for tool in OFFICE_TOOLS
                if tool.name == "office_powerpoint_insert_image"
            )
        ),
        timeout_seconds=1800,
    )
    client_tool_api.configure_runtime_client_tools(
        store,
        ClientToolConnectionManager(store),
        sandbox_store=sandbox_store,
    )
    app = FastAPI()
    app.include_router(client_tool_api.router)
    headers = {
        "Authorization": f"Bearer {token}",
        "X-ModelMirror-Client-Host-Id": host.host_id,
    }

    with TestClient(app) as client:
        response = client.get(
            f"/api/runtime/client-tool-requests/{request.request_id}/"
            f"input-artifacts/{artifact_id}",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.content == image_bytes

        request.scope_id = "another-conversation"
        response = client.get(
            f"/api/runtime/client-tool-requests/{request.request_id}/"
            f"input-artifacts/{artifact_id}",
            headers=headers,
        )
        assert response.status_code == 409


def test_office_compose_profile_is_hardened() -> None:
    compose = (
        Path(__file__).resolve().parents[2] / "docker-compose.yml"
    ).read_text("utf-8")
    office_block = compose.split("office-host:", 1)[1]

    assert "profiles:" in office_block and "- office" in office_block
    assert 'user: "101:101"' in office_block
    assert "read_only: true" in office_block
    assert "no-new-privileges:true" in office_block
    assert "cap_drop:" in office_block and "- ALL" in office_block
    assert "uid=101,gid=101,mode=0700" in office_block
    assert "8443:8443" in office_block
