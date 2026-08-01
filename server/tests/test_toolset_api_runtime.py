from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from server.toolsets.api_compiler import compile_openapi
from server.toolsets.credentials import CredentialStore
from server.toolsets.http_executor import SafeAPIExecutor
from server.toolsets.service import PublishedToolsetProvider, ToolsetService
from server.toolsets.store import ToolsetStore, ToolsetValidationError
from server.xpert_runtime.toolset import RuntimeToolCall


OPENAPI_TEXT = json.dumps(
    {
        "openapi": "3.0.3",
        "servers": [{"url": "https://api.example.test/v1"}],
        "paths": {
            "/items/{item_id}": {
                "get": {
                    "operationId": "get_item",
                    "parameters": [
                        {
                            "name": "item_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                },
                "patch": {
                    "operationId": "update_item",
                    "parameters": [
                        {
                            "name": "item_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                    "additionalProperties": False,
                                }
                            }
                        },
                    },
                },
            }
        },
    }
)


class UnusedMCPManager:
    async def disconnect(self, session_id: str) -> None:
        return None


async def allow_test_url(url: str, network_policy: str) -> None:
    assert url.startswith("https://")
    assert network_policy in {"public_only", "trusted_private"}


def _service(
    tmp_path: Path,
    handler: Any,
) -> tuple[ToolsetService, CredentialStore]:
    storage = tmp_path / "toolsets"
    credentials = CredentialStore(storage)
    executor = SafeAPIExecutor(
        credentials,
        transport=httpx.MockTransport(handler),
        url_validator=allow_test_url,
    )
    return (
        ToolsetService(
            ToolsetStore(storage),
            credentials,
            UnusedMCPManager(),  # type: ignore[arg-type]
            api_executor=executor,
        ),
        credentials,
    )


@pytest.mark.asyncio
async def test_api_toolset_uses_encrypted_auth_and_fixed_version(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={"path": request.url.path, "authorization": request.headers.get("x-api-key")},
        )

    service, credentials = _service(tmp_path, handler)
    credential, _ = credentials.create(
        name="Catalog API key",
        kind="provider_key",
        value="private-catalog-key",
    )
    created = service.store.create_toolset(
        kind="openapi",
        name="Catalog",
        connection={
            "api_auth": {
                "auth_type": "api_key",
                "api_key_name": "X-API-Key",
                "credential_id": credential.credential_id,
            },
            "tool_prefix": "catalog",
        },
    )
    imported = await service.import_spec(
        created.id,
        document_text=OPENAPI_TEXT,
    )
    enabled = service.store.update_tool(
        created.id,
        "get_item",
        revision=imported.revision,
        patch={"enabled": True},
    )
    version = await service.publish(
        created.id,
        expected_revision=enabled.revision,
    )

    provider = PublishedToolsetProvider(service)
    resources = [{"toolset_id": created.id, "pinned_version": version.version}]
    result = await provider.call_tool(
        RuntimeToolCall(
            tool_name="catalog_get_item",
            arguments={"path": {"item_id": "item/1"}},
            metadata={"toolset_resources": resources},
        )
    )

    assert result.is_error is False
    assert requests[-1].url.raw_path == b"/v1/items/item%2F1"
    assert requests[-1].headers["X-API-Key"] == "private-catalog-key"
    persisted = (tmp_path / "toolsets" / "toolsets.json").read_text("utf-8")
    assert "private-catalog-key" not in persisted

    changed_document = json.loads(OPENAPI_TEXT)
    changed_document["paths"]["/items/{item_id}"]["get"]["summary"] = (
        "Changed draft description"
    )
    await service.import_spec(
        created.id,
        document_text=json.dumps(changed_document),
    )
    assert service.store.get_version(created.id, 1).schema_hash == version.schema_hash


@pytest.mark.asyncio
async def test_mutating_api_test_requires_explicit_confirmation(
    tmp_path: Path,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"ok": True})

    service, _ = _service(tmp_path, handler)
    created = service.store.create_toolset(kind="openapi", name="Writer")
    imported = await service.import_spec(
        created.id,
        document_text=OPENAPI_TEXT,
    )

    with pytest.raises(ToolsetValidationError, match="explicit confirmation"):
        await service.test_tool(
            created.id,
            "update_item",
            {"path": {"item_id": "1"}, "body": {"name": "new"}},
        )
    assert calls == 0

    result = await service.test_tool(
        created.id,
        "update_item",
        {"path": {"item_id": "1"}, "body": {"name": "new"}},
        confirm_mutating=True,
    )
    assert result.is_error is False
    assert calls == 1


@pytest.mark.asyncio
async def test_executor_rejects_cross_origin_redirect_with_credentials(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"Location": "https://other.example.test/steal"},
        )

    service, credentials = _service(tmp_path, handler)
    credential, _ = credentials.create(
        name="Bearer",
        kind="provider_key",
        value="sensitive-token",
    )
    compiled = compile_openapi(json.loads(OPENAPI_TEXT))
    connection = service.store.create_toolset(
        kind="openapi",
        name="Redirect",
        connection={
            "api_base_url": compiled.base_url,
            "api_auth": {
                "auth_type": "bearer",
                "credential_id": credential.credential_id,
            },
        },
    ).connection

    with pytest.raises(Exception, match="Cross-origin HTTP redirects"):
        await service.api_executor.execute(
            connection,
            compiled.tools[0],
            {"path": {"item_id": "1"}},
        )
    assert len(requests) == 1
