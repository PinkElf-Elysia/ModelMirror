from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .credentials import (
    CredentialNotFoundError,
    CredentialStoreError,
    CredentialUnavailableError,
)
from .service import ToolsetService
from .store import (
    ToolsetConflictError,
    ToolsetNotFoundError,
    ToolsetValidationError,
)


router = APIRouter(tags=["toolsets"])
_service: ToolsetService | None = None


def configure_toolsets(service: ToolsetService) -> None:
    global _service
    _service = service


def get_toolset_service() -> ToolsetService:
    if _service is None:
        raise RuntimeError("Toolset service is not configured.")
    return _service


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, ToolsetNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ToolsetConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, CredentialNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, CredentialUnavailableError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(
        exc,
        (ToolsetValidationError, CredentialStoreError, ValueError, TypeError),
    ):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail="Toolset operation failed.")


class ToolsetCreateRequest(BaseModel):
    kind: Literal["mcp", "openapi", "odata", "builtin"] = "mcp"
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    privacy_policy: str = Field(default="", max_length=4000)
    disclaimer: str = Field(default="", max_length=4000)
    connection: dict[str, Any] = Field(default_factory=dict)


class ToolsetPatchRequest(BaseModel):
    revision: int = Field(ge=1)
    patch: dict[str, Any]


class ToolPatchRequest(BaseModel):
    revision: int = Field(ge=1)
    patch: dict[str, Any]


class ToolTestRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    confirm_mutating: bool = False


class PublishRequest(BaseModel):
    revision: int = Field(ge=1)
    release_notes: str = Field(default="", max_length=2000)


class APISpecImportRequest(BaseModel):
    source_type: Literal["text", "url"] = "text"
    document: str = Field(default="", max_length=5 * 1024 * 1024)
    source_url: str = Field(default="", max_length=2048)
    base_url: str = Field(default="", max_length=2048)
    source_label: str = Field(default="", max_length=300)


class CredentialCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    kind: Literal["header", "environment", "provider_key", "generic"] = "generic"
    value: str = Field(min_length=1, max_length=20_000)


class CredentialRotateRequest(BaseModel):
    value: str = Field(min_length=1, max_length=20_000)


class ProviderInstanceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    credential_id: str = Field(default="", max_length=160)
    tags: list[str] = Field(default_factory=list, max_length=20)


@router.get("/api/toolsets")
async def list_toolsets(status: str | None = None) -> dict[str, Any]:
    service = get_toolset_service()
    return {
        "version": "modelmirror-toolsets-v1",
        "toolsets": [
            item.model_dump(mode="json")
            for item in service.store.list_toolsets(status=status)
        ],
    }


@router.get("/api/toolsets/providers")
async def list_builtin_toolset_providers() -> dict[str, Any]:
    return {
        "version": "modelmirror-builtin-tool-providers-v1",
        "providers": await get_toolset_service().builtin_provider_catalog(),
    }


@router.post(
    "/api/toolsets/providers/{provider_id}/instances",
    status_code=201,
)
async def create_builtin_provider_instance(
    provider_id: str,
    request: ProviderInstanceCreateRequest,
) -> dict[str, Any]:
    try:
        service = get_toolset_service()
        provider = service.builtin_providers.get_provider(provider_id)
        if not provider.get("instance_creatable"):
            raise ToolsetValidationError(
                "This builtin Provider is available through its existing Runtime binding."
            )
        item = await service.configure_builtin_provider(
            provider_id,
            name=request.name,
            description=request.description,
            credential_id=request.credential_id,
            tags=request.tags,
        )
        return item.model_dump(mode="json")
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/api/toolsets", status_code=201)
async def create_toolset(request: ToolsetCreateRequest) -> dict[str, Any]:
    try:
        item = get_toolset_service().store.create_toolset(
            name=request.name,
            kind=request.kind,
            description=request.description,
            tags=request.tags,
            privacy_policy=request.privacy_policy,
            disclaimer=request.disclaimer,
            connection=request.connection,
        )
        return item.model_dump(mode="json")
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/api/toolsets/resources")
async def list_toolset_resources() -> dict[str, Any]:
    items = get_toolset_service().store.list_toolsets()
    return {
        "version": "modelmirror-toolset-resources-v2",
        "tabs": [
            {"id": "all", "label": "全部"},
            {"id": "mcp", "label": "MCP"},
            {"id": "builtin", "label": "内置工具"},
            {"id": "api", "label": "API"},
            {"id": "skill", "label": "Skill"},
        ],
        "summary": {
            "toolset_count": len(items),
            "connected_count": sum(
                item.runtime_status in {"connected", "ready"} for item in items
            ),
            "published_count": sum(item.status == "published" for item in items),
            "enabled_tool_count": sum(
                tool.enabled for item in items for tool in item.tools
            ),
        },
        "toolsets": [
            {
                "id": item.id,
                "kind": item.kind,
                "title": item.name,
                "description": item.description,
                "status": item.runtime_status,
                "transport": (
                    item.connection.transport if item.kind == "mcp" else item.kind
                ),
                "published_version": item.published_version,
                "tool_count": sum(tool.enabled for tool in item.tools),
                "tags": list(item.tags),
                "primary_action": {
                    "label": "管理 Toolset",
                    "href": "/toolsets",
                },
            }
            for item in items
        ],
        "warnings": [],
    }


@router.get("/api/toolsets/api-capabilities")
async def get_api_toolset_capabilities() -> dict[str, Any]:
    return {
        "version": "modelmirror-api-toolsets-v1",
        "formats": {
            "openapi": ["3.0", "3.1"],
            "odata": ["4.0"],
        },
        "imports": ["text", "file", "url"],
        "auth_types": [
            "none",
            "api_key",
            "bearer",
            "basic",
            "oauth2_client_credentials",
        ],
        "network_policies": ["public_only", "trusted_private"],
        "limits": {
            "document_bytes": 5 * 1024 * 1024,
            "response_bytes": 10 * 1024 * 1024,
            "redirects": 5,
        },
        "deferred": [
            "remote_refs",
            "multipart_upload",
            "oauth_browser_flows",
            "odata_batch",
        ],
    }


@router.post("/api/toolsets/{toolset_id}/import")
async def import_api_toolset(
    toolset_id: str,
    request: APISpecImportRequest,
) -> dict[str, Any]:
    try:
        service = get_toolset_service()
        if request.source_type == "url":
            item = await service.import_spec_from_url(
                toolset_id,
                url=request.source_url,
                base_url=request.base_url,
            )
        else:
            item = await service.import_spec(
                toolset_id,
                document_text=request.document,
                base_url=request.base_url,
                source_label=request.source_label or "pasted document",
            )
        return item.model_dump(mode="json")
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/api/toolsets/{toolset_id}/import-file")
async def import_api_toolset_file(
    toolset_id: str,
    file: UploadFile = File(...),
    base_url: str = Form(default=""),
) -> dict[str, Any]:
    try:
        payload = await file.read(5 * 1024 * 1024 + 1)
        if len(payload) > 5 * 1024 * 1024:
            raise ValueError("API specification file exceeds 5 MB.")
        try:
            document = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("API specification file must be UTF-8 text.") from exc
        item = await get_toolset_service().import_spec(
            toolset_id,
            document_text=document,
            base_url=base_url,
            source_label=file.filename or "uploaded document",
        )
        return item.model_dump(mode="json")
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/api/toolsets/{toolset_id}/refresh")
async def refresh_api_toolset(toolset_id: str) -> dict[str, Any]:
    try:
        item = await get_toolset_service().refresh_spec(toolset_id)
        return item.model_dump(mode="json")
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/api/toolsets/{toolset_id}/drift")
async def get_api_toolset_drift(toolset_id: str) -> dict[str, Any]:
    try:
        item = get_toolset_service().store.get_toolset(toolset_id)
        return {
            "toolset_id": item.id,
            "revision": item.revision,
            "spec_hash": item.connection.api_spec_hash,
            "report": dict(item.drift_report),
            "warnings": list(item.import_warnings),
        }
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/api/toolsets/{toolset_id}")
async def get_toolset(toolset_id: str) -> dict[str, Any]:
    try:
        return get_toolset_service().store.get_toolset(toolset_id).model_dump(
            mode="json"
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.patch("/api/toolsets/{toolset_id}")
async def patch_toolset(
    toolset_id: str,
    request: ToolsetPatchRequest,
) -> dict[str, Any]:
    try:
        item = get_toolset_service().store.update_toolset(
            toolset_id,
            revision=request.revision,
            patch=request.patch,
        )
        return item.model_dump(mode="json")
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/api/toolsets/{toolset_id}/connect")
async def connect_toolset(toolset_id: str) -> dict[str, Any]:
    try:
        item = await get_toolset_service().connect(toolset_id)
        return item.model_dump(mode="json")
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/api/toolsets/{toolset_id}/disconnect")
async def disconnect_toolset(toolset_id: str) -> dict[str, Any]:
    try:
        item = await get_toolset_service().disconnect(toolset_id)
        return item.model_dump(mode="json")
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/api/toolsets/{toolset_id}/tools")
async def list_toolset_tools(toolset_id: str) -> dict[str, Any]:
    try:
        item = get_toolset_service().store.get_toolset(toolset_id)
        return {
            "toolset_id": item.id,
            "revision": item.revision,
            "runtime_status": item.runtime_status,
            "tools": [tool.model_dump(mode="json") for tool in item.tools],
        }
    except Exception as exc:
        raise _error(exc) from exc


@router.patch("/api/toolsets/{toolset_id}/tools/{tool_name}")
async def patch_toolset_tool(
    toolset_id: str,
    tool_name: str,
    request: ToolPatchRequest,
) -> dict[str, Any]:
    try:
        item = get_toolset_service().store.update_tool(
            toolset_id,
            tool_name,
            revision=request.revision,
            patch=request.patch,
        )
        return item.model_dump(mode="json")
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/api/toolsets/{toolset_id}/tools/{tool_name}/test")
async def test_toolset_tool(
    toolset_id: str,
    tool_name: str,
    request: ToolTestRequest,
) -> dict[str, Any]:
    try:
        result = await get_toolset_service().test_tool(
            toolset_id,
            tool_name,
            request.arguments,
            confirm_mutating=request.confirm_mutating,
        )
        return {
            "output": result.output,
            "is_error": result.is_error,
            "metadata": result.metadata,
            "content_types": sorted(
                {
                    str(item.get("type") or "unknown")
                    for item in result.content
                    if isinstance(item, dict)
                }
            ),
        }
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/api/toolsets/{toolset_id}/publish")
async def publish_toolset(
    toolset_id: str,
    request: PublishRequest,
) -> dict[str, Any]:
    try:
        version = await get_toolset_service().publish(
            toolset_id,
            expected_revision=request.revision,
            release_notes=request.release_notes,
        )
        return version.model_dump(mode="json")
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/api/toolsets/{toolset_id}/versions")
async def list_toolset_versions(toolset_id: str) -> dict[str, Any]:
    try:
        item = get_toolset_service().store.get_toolset(toolset_id)
        return {
            "toolset_id": item.id,
            "published_version": item.published_version,
            "versions": [
                version.model_dump(mode="json") for version in item.versions
            ],
        }
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/api/toolsets/{toolset_id}/versions/{version}")
async def get_toolset_version(toolset_id: str, version: int) -> dict[str, Any]:
    try:
        return get_toolset_service().store.get_version(
            toolset_id,
            version,
        ).model_dump(mode="json")
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/api/runtime/credentials")
async def list_credentials() -> dict[str, Any]:
    return {
        "credentials": [
            item.model_dump(mode="json", exclude={"ciphertext"})
            for item in get_toolset_service().credentials.list()
        ]
    }


@router.post("/api/runtime/credentials", status_code=201)
async def create_credential(request: CredentialCreateRequest) -> dict[str, Any]:
    try:
        metadata, values = get_toolset_service().credentials.create(
            name=request.name,
            kind=request.kind,
            value=request.value,
        )
        return {
            **metadata.model_dump(mode="json", exclude={"ciphertext"}),
            "secret_value": values,
            "secret_value_visible_once": True,
        }
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/api/runtime/credentials/{credential_id}/rotate")
async def rotate_credential(
    credential_id: str,
    request: CredentialRotateRequest,
) -> dict[str, Any]:
    try:
        metadata, values = get_toolset_service().credentials.rotate(
            credential_id,
            value=request.value,
        )
        return {
            **metadata.model_dump(mode="json", exclude={"ciphertext"}),
            "secret_value": values,
            "secret_value_visible_once": True,
        }
    except Exception as exc:
        raise _error(exc) from exc


@router.delete("/api/runtime/credentials/{credential_id}")
async def revoke_credential(credential_id: str) -> dict[str, Any]:
    try:
        item = get_toolset_service().credentials.revoke(credential_id)
        return item.model_dump(mode="json", exclude={"ciphertext"})
    except Exception as exc:
        raise _error(exc) from exc
