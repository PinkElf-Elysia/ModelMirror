from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .api import get_xpert_store
from .app_models import (
    XpertAppAccessGrant,
    XpertAppLimits,
    XpertAppPolicy,
)
from .app_store import (
    XpertAppAccessController,
    XpertAppAuthenticationError,
    XpertAppConflictError,
    XpertAppError,
    XpertAppNotFoundError,
    XpertAppQuotaError,
    XpertAppStore,
    XpertAppValidationError,
)
from .models import XpertConversationMessage, XpertRunRequest, XpertVersion
from .store import XpertNotFoundError, XpertStoreError

try:
    from server.xpert_runtime.execution_store import WorkflowExecutionStore
    from server.xpert_runtime.middleware_registry import runtime_middleware_registry
    from server.workflow_native.retry_policy import retry_enabled
except ModuleNotFoundError:
    from xpert_runtime.execution_store import WorkflowExecutionStore
    from xpert_runtime.middleware_registry import runtime_middleware_registry
    from workflow_native.retry_policy import retry_enabled


router = APIRouter(tags=["xpert-apps"])
_app_store: XpertAppStore | None = None
_access_controller: XpertAppAccessController | None = None
_runtime_callback: Callable[..., Awaitable[Any]] | None = None


class _XpertAppExecutionFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        run_id: str,
        provider_receipt: dict[str, Any] | None,
    ) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.provider_receipt = provider_receipt


class XpertAppCreateRequest(BaseModel):
    slug: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    starters: list[str] | None = Field(default=None, max_length=8)


class XpertAppUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    starters: list[str] | None = Field(default=None, max_length=8)
    policy: XpertAppPolicy | None = None
    limits: XpertAppLimits | None = None


class XpertAppDeployRequest(BaseModel):
    version: int = Field(ge=1)
    release_notes: str = Field(default="", max_length=1000)


class XpertAppApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    limits: XpertAppLimits | None = None
    expires_at: float | None = None


class OpenAIChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class OpenAIChatCompletionsRequest(BaseModel):
    model: str | None = Field(default=None, max_length=200)
    messages: list[OpenAIChatMessage] = Field(min_length=1, max_length=20)
    stream: bool = False


def get_xpert_app_store() -> XpertAppStore:
    global _app_store
    if _app_store is None:
        _app_store = XpertAppStore()
    return _app_store


def set_xpert_app_store_for_tests(store: XpertAppStore | None) -> None:
    global _app_store, _access_controller
    _app_store = store
    _access_controller = XpertAppAccessController(store) if store else None


def get_xpert_app_access_controller() -> XpertAppAccessController:
    global _access_controller
    if _access_controller is None:
        _access_controller = XpertAppAccessController(get_xpert_app_store())
    return _access_controller


def configure_xpert_app_runtime(callback: Callable[..., Awaitable[Any]]) -> None:
    global _runtime_callback
    _runtime_callback = callback


def _raise_app_error(exc: Exception) -> None:
    if isinstance(exc, XpertAppNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, XpertAppValidationError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, XpertAppConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


def _deployment_preflight(version: XpertVersion, policy: XpertAppPolicy) -> dict:
    try:
        from server.workflow_native.node_contracts import node_policy_service
        from server.workflow_native.r20_nodes import (
            WorkflowR20NodeError,
            validate_code_v2_config,
        )
    except ModuleNotFoundError:
        from workflow_native.node_contracts import node_policy_service
        from workflow_native.r20_nodes import (
            WorkflowR20NodeError,
            validate_code_v2_config,
        )

    issues: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    has_tool_call = False
    has_tool_policy = False
    has_handoff = False
    has_knowledge = False
    has_dynamic_knowledge_read = False
    has_dynamic_knowledge_write = False
    has_interactive_hitl = False
    has_sandbox_runtime = False
    has_browser_runtime = False
    has_client_runtime = False
    has_office_runtime = False
    has_private_automation_runtime = False
    has_knowledge_writer = False
    has_file_memory = False
    has_file_memory_writeback = False
    has_datax_runtime = False
    has_datax_write = False
    has_toolset_resource = False
    node_policy_issues: dict[str, dict[str, str]] = {}
    toolset_resource_issues: list[dict[str, str]] = []
    datax_project_ids: set[str] = set()
    datax_model_ids: set[str] = set()
    contract_forbidden_middleware: set[str] = set()
    hardcoded_forbidden = {
        "human_in_the_loop",
        "sandbox_files",
        "sandbox_shell",
        "skills_runtime",
        "browser_automation",
        "client_tools",
        "office_automation",
        "scheduler",
        "ralph_loop",
        "plugin_hooks",
        "knowledge_writer",
    }
    registry_forbidden = runtime_middleware_registry.app_forbidden_ids()
    for node in version.workflow.nodes:
        data = node.data if isinstance(node.data, dict) else {}
        kind = str(data.get("kind") or node.type)
        node_policy = node_policy_service.decision(kind, "app")
        if not node_policy.allowed and kind != "human_intervention":
            code = node_policy.code or "app_node_forbidden"
            node_policy_issues.setdefault(
                code,
                {
                    "code": code,
                    "message": node_policy.message
                    or f"Public Xpert Apps cannot deploy node kind: {kind}.",
                },
            )
        if retry_enabled(data):
            node_policy_issues.setdefault(
                "app_node_retries_forbidden",
                {
                    "code": "app_node_retries_forbidden",
                    "message": "Public Xpert Apps cannot deploy durable node retries.",
                },
            )
        if kind == "code":
            raw_contract_version = data.get("contractVersion")
            if isinstance(raw_contract_version, bool) or raw_contract_version != 2:
                node_policy_issues.setdefault(
                    "app_code_migration_required",
                    {
                        "code": "app_code_migration_required",
                        "message": (
                            "Legacy code nodes must be migrated to safe text processing "
                            "before App deployment."
                        ),
                    },
                )
            else:
                try:
                    validate_code_v2_config(data)
                except WorkflowR20NodeError:
                    node_policy_issues.setdefault(
                        "app_code_contract_invalid",
                        {
                            "code": "app_code_contract_invalid",
                            "message": (
                                "Safe text processing configuration is invalid and "
                                "must be corrected before App deployment."
                            ),
                        },
                    )
        if kind == "variable_aggregator" and data.get("contractVersion") != 2:
            node_policy_issues.setdefault(
                "app_variable_aggregator_migration_required",
                {
                    "code": "app_variable_aggregator_migration_required",
                    "message": (
                        "Legacy variable aggregator nodes must be migrated to "
                        "variable pack V2 before App deployment."
                    ),
                },
            )
        if kind == "iteration":
            if data.get("contractVersion") != 2:
                node_policy_issues.setdefault(
                    "app_iteration_migration_required",
                    {
                        "code": "app_iteration_migration_required",
                        "message": (
                            "Legacy iteration nodes must be migrated to batch "
                            "processing V2 before App deployment."
                        ),
                    },
                )
            elif str(data.get("mode") or "") == "workflow_map":
                node_policy_issues.setdefault(
                    "app_iteration_workflow_map_forbidden",
                    {
                        "code": "app_iteration_workflow_map_forbidden",
                        "message": (
                            "Fixed subworkflow batch mode is unavailable in public "
                            "Xpert Apps."
                        ),
                    },
                )
        if kind == "template_transform":
            node_policy_issues.setdefault(
                "app_template_transform_migration_required",
                {
                    "code": "app_template_transform_migration_required",
                    "message": (
                        "Template transform nodes must be migrated to variable_assign "
                        "before App deployment."
                    ),
                },
            )
        middleware_id = str(data.get("runtimeMiddlewareId") or "")
        if (
            kind == "runtime_middleware"
            and middleware_id in registry_forbidden
            and middleware_id not in hardcoded_forbidden
        ):
            contract_forbidden_middleware.add(middleware_id)
        if kind == "mcp_tool":
            has_tool_call = True
        if kind == "external_xpert":
            has_tool_call = True
        if kind == "toolset_resource":
            has_toolset_resource = True
            has_tool_call = True
            try:
                try:
                    from server.toolsets import get_toolset_service
                except ModuleNotFoundError:
                    from toolsets import get_toolset_service

                toolset_id = str(data.get("toolsetId") or "").strip()
                version_number = int(data.get("pinnedVersion") or 0)
                if not toolset_id or version_number < 1:
                    raise ValueError(
                        "App Toolset resources must pin a published version."
                    )
                toolset_service = get_toolset_service()
                toolset = toolset_service.store.get_toolset(toolset_id)
                toolset_version = toolset_service.store.get_version(
                    toolset.id,
                    version_number,
                )
                unsafe_tools = [
                    tool.exposed_name
                    for tool in toolset_version.tools
                    if tool.enabled
                    and not (
                        tool.read_only
                        and not tool.sensitive
                        and tool.public_app_allowed
                        and tool.memory_mode != "conversation"
                    )
                ]
                if unsafe_tools:
                    toolset_resource_issues.append(
                        {
                            "code": "app_toolset_tools_unsafe",
                            "message": (
                                f"Toolset {toolset.name} contains tools that are not "
                                "approved for public read-only use: "
                                + ", ".join(sorted(unsafe_tools))
                            ),
                        }
                    )
            except Exception as exc:
                toolset_resource_issues.append(
                    {
                        "code": "app_toolset_resource_invalid",
                        "message": str(exc)[:500],
                    }
                )
        if kind == "knowledge_base":
            has_knowledge = True
            has_dynamic_knowledge_read = True
            has_tool_call = True
        if kind in {"agent", "workflow_agent"} and data.get("toolMode") == "mcp_tools":
            dynamic_knowledge = kind == "workflow_agent" and (
                _truthy(data.get("knowledgeReadEnabled"))
                or _truthy(data.get("knowledgeWriteEnabled"))
            )
            dynamic_datax = False
            if kind == "workflow_agent":
                dynamic_datax = any(
                    str(bound.data.get("runtimeMiddlewareId") or "") == "datax_indicators"
                    and any(
                        edge.source == bound.id
                        and edge.target == node.id
                        and str(edge.targetHandle or "") == "middleware"
                        for edge in version.workflow.edges
                    )
                    for bound in version.workflow.nodes
                )
            if str(data.get("toolNames") or "").strip() or not (dynamic_knowledge or dynamic_datax):
                has_tool_call = True
        if kind == "workflow_agent":
            has_dynamic_knowledge_read = has_dynamic_knowledge_read or _truthy(
                data.get("knowledgeReadEnabled")
            )
            has_dynamic_knowledge_write = has_dynamic_knowledge_write or _truthy(
                data.get("knowledgeWriteEnabled")
            )
        if kind == "runtime_middleware" and data.get("runtimeMiddlewareId") == "tool_policy":
            has_tool_policy = True
        if kind == "human_intervention":
            has_interactive_hitl = True
        if (
            kind == "runtime_middleware"
            and data.get("runtimeMiddlewareId") == "human_in_the_loop"
        ):
            has_interactive_hitl = True
        if (
            kind == "runtime_middleware"
            and data.get("runtimeMiddlewareId")
            in {"sandbox_files", "sandbox_shell", "skills_runtime"}
        ):
            has_sandbox_runtime = True
        if (
            kind == "runtime_middleware"
            and data.get("runtimeMiddlewareId") == "browser_automation"
        ):
            has_browser_runtime = True
        if (
            kind == "runtime_middleware"
            and data.get("runtimeMiddlewareId") == "client_tools"
        ):
            has_client_runtime = True
        if (
            kind == "runtime_middleware"
            and data.get("runtimeMiddlewareId") == "office_automation"
        ):
            has_office_runtime = True
        if (
            kind == "runtime_middleware"
            and data.get("runtimeMiddlewareId") == "datax_indicators"
        ):
            has_datax_runtime = True
            config = data.get("runtimeMiddlewareConfig")
            config = config if isinstance(config, dict) else {}
            has_datax_write = has_datax_write or _truthy(config.get("allowProposals"))
            datax_project_ids.update(_scoped_values(config.get("projectIds")))
            datax_model_ids.update(_scoped_values(config.get("modelIds")))
        if kind == "runtime_middleware" and data.get("runtimeMiddlewareId") in {
            "scheduler",
            "ralph_loop",
            "plugin_hooks",
        }:
            has_private_automation_runtime = True
        if (
            kind == "runtime_middleware"
            and data.get("runtimeMiddlewareId") == "knowledge_writer"
        ):
            has_knowledge_writer = True
        if (
            kind == "runtime_middleware"
            and data.get("runtimeMiddlewareId") == "xpert_file_memory"
        ):
            has_file_memory = True
            config = data.get("runtimeMiddlewareConfig")
            config = config if isinstance(config, dict) else {}
            has_file_memory_writeback = has_file_memory_writeback or _truthy(
                config.get("writeback_enabled")
            )
        if kind in {"agent_task", "agent_handoff", "handoff_router"}:
            has_handoff = True
        if kind in {"knowledge_retrieval", "knowledge_citation"}:
            has_knowledge = True
    if has_tool_call and not policy.allow_tools:
        issues.append(
            {
                "code": "app_tools_not_allowed",
                "message": "This Xpert version uses tools, but App tool access is disabled.",
            }
        )
    if has_tool_call and policy.allow_tools and not has_tool_policy:
        issues.append(
            {
                "code": "app_tool_policy_required",
                "message": "Public tool access requires a tool_policy middleware node.",
            }
        )
    if has_handoff and not policy.allow_handoffs:
        issues.append(
            {
                "code": "app_handoffs_not_allowed",
                "message": "This Xpert version uses Handoff, but App Handoff access is disabled.",
            }
        )
    issues.extend(node_policy_issues.values())
    unsafe_prompts = [
        profile.name
        for profile in version.prompt_profiles
        if profile.source != "direct" or not profile.public_app_allowed
    ]
    if unsafe_prompts:
        issues.append(
            {
                "code": "app_prompt_profile_not_public",
                "message": (
                    "Public Xpert Apps require every bound Prompt Profile to be "
                    "direct and public_app_allowed: "
                    + ", ".join(sorted(unsafe_prompts))
                ),
            }
        )
    if has_toolset_resource:
        issues.extend(toolset_resource_issues)
    if has_dynamic_knowledge_read and not policy.allow_knowledge_read:
        issues.append(
            {
                "code": "app_knowledge_read_not_allowed",
                "message": (
                    "This Xpert version uses dynamic knowledge tools, but App knowledge read access is disabled."
                ),
            }
        )
    if has_dynamic_knowledge_write:
        issues.append(
            {
                "code": "app_knowledge_write_forbidden",
                "message": "Public Xpert Apps cannot deploy knowledge write proposal tools.",
            }
        )
    if has_datax_runtime and not policy.allow_datax_read:
        issues.append(
            {
                "code": "app_datax_read_not_allowed",
                "message": "This Xpert version uses Data X, but App Data X read access is disabled.",
            }
        )
    if has_datax_runtime and not has_tool_policy:
        issues.append(
            {
                "code": "app_datax_tool_policy_required",
                "message": "Public Data X access requires a tool_policy middleware node.",
            }
        )
    if has_datax_write:
        issues.append(
            {
                "code": "app_datax_write_forbidden",
                "message": "Public Xpert Apps cannot deploy Data X proposal tools.",
            }
        )
    if has_datax_runtime:
        try:
            try:
                from server.datax.api import get_datax_service
            except ModuleNotFoundError:
                from datax.api import get_datax_service

            datax_service = get_datax_service()
            for project_id in sorted(datax_project_ids):
                datax_service.get_project(project_id)
            for model_id in sorted(datax_model_ids):
                model = datax_service.get_model(model_id)
                if model.project_id not in datax_project_ids:
                    raise ValueError(
                        f"Data X model '{model_id}' is outside the configured project scope."
                    )
        except Exception as exc:
            issues.append(
                {
                    "code": "app_datax_scope_invalid",
                    "message": f"Data X deployment scope is invalid: {exc}",
                }
            )
    if has_interactive_hitl:
        issues.append(
            {
                "code": "app_interactive_hitl_forbidden",
                "message": "Public Xpert Apps cannot deploy interactive HITL workflows.",
            }
        )
    if has_sandbox_runtime:
        issues.append(
            {
                "code": "app_sandbox_forbidden",
                "message": "Public Xpert Apps cannot deploy Sandbox or Skill runtime middleware.",
            }
        )
    if has_browser_runtime:
        issues.append(
            {
                "code": "app_browser_forbidden",
                "message": "Public Xpert Apps cannot deploy browser automation middleware.",
            }
        )
    if has_client_runtime:
        issues.append(
            {
                "code": "app_client_tools_forbidden",
                "message": "Public Xpert Apps cannot use client_tools middleware.",
            }
        )
    if has_office_runtime:
        issues.append(
            {
                "code": "app_office_automation_forbidden",
                "message": "Public Xpert Apps cannot use Office automation middleware.",
            }
        )
    if has_private_automation_runtime:
        issues.append(
            {
                "code": "app_private_automation_forbidden",
                "message": (
                    "Public Xpert Apps cannot deploy scheduler, Ralph loop, or plugin hook middleware."
                ),
            }
        )
    if has_knowledge_writer:
        issues.append(
            {
                "code": "app_knowledge_writer_forbidden",
                "message": "Public Xpert Apps cannot create knowledge write proposals.",
            }
        )
    if has_file_memory and not policy.allow_xpert_memory:
        issues.append(
            {
                "code": "app_xpert_file_memory_not_allowed",
                "message": "This Xpert uses file memory, but App memory access is disabled.",
            }
        )
    if has_file_memory_writeback:
        issues.append(
            {
                "code": "app_xpert_file_memory_write_forbidden",
                "message": "Public Xpert Apps can only use Xpert file memory in read-only mode.",
            }
        )
    if contract_forbidden_middleware:
        issues.append(
            {
                "code": "app_middleware_contract_forbidden",
                "message": (
                    "Public Xpert Apps cannot deploy these private middleware: "
                    + ", ".join(sorted(contract_forbidden_middleware))
                    + "."
                ),
            }
        )
    if has_knowledge or has_dynamic_knowledge_read:
        warnings.append(
            {
                "code": "app_knowledge_visibility",
                "message": "Knowledge results used by this version may be visible to App callers.",
            }
        )
    return {"valid": not issues, "issues": issues, "warnings": warnings}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _scoped_values(value: Any) -> list[str]:
    if isinstance(value, list):
        values = value
    else:
        values = str(value or "").replace("\r", "\n").replace(",", "\n").split("\n")
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def _public_app_payload(app: Any, version: XpertVersion | None = None) -> dict:
    commands = []
    if version is not None:
        commands = [
            {
                "name": profile.name,
                "description": profile.description,
                "aliases": list(profile.aliases),
                "argument_hint": profile.argument_hint,
            }
            for profile in version.prompt_profiles
            if profile.source == "direct" and profile.public_app_allowed
        ]
    return {
        "slug": app.slug,
        "name": app.name,
        "description": app.description,
        "starters": list(app.starters),
        "version": app.pinned_version,
        "deployment_revision": app.deployment_revision,
        "visibility": app.visibility,
        "commands": commands,
    }


def _extract_credential(
    authorization: str | None,
    share_token: str | None,
) -> tuple[str, str]:
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip(), "api_key"
    if share_token and share_token.strip():
        return share_token.strip(), "share"
    raise XpertAppAuthenticationError("App credential is required.")


def _openai_error(message: str, *, status_code: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": "modelmirror_app_error",
                "param": None,
                "code": code,
            }
        },
    )


def _rate_headers(app: Any, grant: XpertAppAccessGrant) -> dict[str, str]:
    return {
        "X-RateLimit-Limit-Requests": str(grant.limits.requests_per_day),
        "X-RateLimit-Remaining-Requests": str(
            max(0, grant.limits.requests_per_day - grant.requests_today)
        ),
        "X-ModelMirror-App-Version": str(app.pinned_version or ""),
        "X-ModelMirror-App-Revision": str(app.deployment_revision),
        "X-ModelMirror-Key-Prefix": grant.credential_prefix,
    }


async def _iter_internal_events(response: Any) -> AsyncIterator[dict[str, Any]]:
    if isinstance(response, JSONResponse):
        try:
            payload = json.loads(bytes(response.body).decode("utf-8"))
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        message = payload.get("error") if isinstance(payload, dict) else None
        raise RuntimeError(str(message or "Xpert App could not start."))
    if not isinstance(response, StreamingResponse):
        raise RuntimeError("Xpert App returned an unsupported response.")
    buffer = ""
    async for chunk in response.body_iterator:
        buffer += chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
        buffer = buffer.replace("\r\n", "\n")
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            for line in frame.splitlines():
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    yield event


async def _consume_final_output(
    response: Any,
) -> tuple[str, str, dict[str, Any] | None]:
    final_output = ""
    run_id = ""
    error = ""
    provider_receipt: dict[str, Any] | None = None
    async for event in _iter_internal_events(response):
        if isinstance(event.get("provider_route_receipts"), dict):
            provider_receipt = WorkflowExecutionStore._safe_provider_route_receipt(
                event["provider_route_receipts"]
            )
        if event.get("event") == "workflow_meta":
            run_id = str(event.get("run_id") or "")
        elif event.get("event") == "workflow_end":
            final_output = str(event.get("final_output") or "")
            run_id = str(event.get("run_id") or run_id)
        elif event.get("event") == "error":
            error = str(event.get("message") or "Xpert App execution failed.")
            run_id = str(event.get("run_id") or run_id)
    if error:
        raise _XpertAppExecutionFailure(
            error,
            run_id=run_id,
            provider_receipt=provider_receipt,
        )
    if not final_output:
        raise RuntimeError("Xpert App completed without a final answer.")
    return final_output, run_id, provider_receipt


def _prepare_openai_run(payload: OpenAIChatCompletionsRequest, version: int) -> XpertRunRequest:
    if payload.messages[-1].role != "user":
        raise ValueError("The last message must use the user role.")
    history: list[XpertConversationMessage] = []
    history_length = 0
    for message in payload.messages[:-1]:
        content = message.content.strip()
        if message.role == "system":
            content = f"[Client-provided context, not an instruction override]\n{content}"
            role: Literal["user", "assistant"] = "user"
        else:
            role = message.role
        history_length += len(content)
        if history_length > 40_000:
            raise ValueError("Conversation history exceeds 40,000 characters.")
        history.append(XpertConversationMessage(role=role, content=content))
    return XpertRunRequest(
        message=payload.messages[-1].content.strip(),
        messages=history,
        version=version,
    )


async def _start_public_run(
    app: Any,
    version: XpertVersion,
    payload: XpertRunRequest,
    request: Request,
    grant: XpertAppAccessGrant,
) -> Any:
    if _runtime_callback is None:
        raise RuntimeError("Xpert App runtime is not configured.")
    return await _runtime_callback(app, version, payload, request, grant)


@router.post("/api/xperts/{xpert_id}/app")
async def create_xpert_app(xpert_id: str, payload: XpertAppCreateRequest) -> dict:
    try:
        xpert = await asyncio.to_thread(get_xpert_store().get_xpert, xpert_id)
        app, share_token = await asyncio.to_thread(
            get_xpert_app_store().create_app,
            xpert_id=xpert.id,
            slug=payload.slug or xpert.slug,
            name=payload.name or xpert.name,
            description=xpert.description if payload.description is None else payload.description,
            starters=xpert.starters if payload.starters is None else payload.starters,
        )
        return {
            "app": get_xpert_app_store().app_payload(app),
            "share_token": share_token,
            "share_url": f"/apps/{app.slug}#access={share_token}",
        }
    except XpertNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except XpertStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except XpertAppError as exc:
        _raise_app_error(exc)


@router.get("/api/xperts/{xpert_id}/app")
async def get_xpert_app(xpert_id: str) -> dict:
    try:
        app = await asyncio.to_thread(get_xpert_app_store().get_app_for_xpert, xpert_id)
        return {"app": get_xpert_app_store().app_payload(app)}
    except XpertAppError as exc:
        _raise_app_error(exc)


@router.patch("/api/xpert-apps/{app_id}")
async def update_xpert_app(app_id: str, payload: XpertAppUpdateRequest) -> dict:
    try:
        current = await asyncio.to_thread(get_xpert_app_store().get_app, app_id)
        if current.status == "active" and current.pinned_version and payload.policy:
            version = await asyncio.to_thread(
                get_xpert_store().get_version,
                current.xpert_id,
                current.pinned_version,
            )
            preflight = _deployment_preflight(version, payload.policy)
            if not preflight["valid"]:
                raise HTTPException(status_code=422, detail=preflight)
        app = await asyncio.to_thread(
            get_xpert_app_store().update_app,
            app_id,
            payload.model_dump(exclude_unset=True, mode="json"),
        )
        return {"app": get_xpert_app_store().app_payload(app)}
    except XpertAppError as exc:
        _raise_app_error(exc)


@router.post("/api/xpert-apps/{app_id}/deploy")
async def deploy_xpert_app(app_id: str, payload: XpertAppDeployRequest) -> dict:
    try:
        app = await asyncio.to_thread(get_xpert_app_store().get_app, app_id)
        version = await asyncio.to_thread(
            get_xpert_store().get_version,
            app.xpert_id,
            payload.version,
        )
        preflight = _deployment_preflight(version, app.policy)
        if not preflight["valid"]:
            raise HTTPException(status_code=422, detail=preflight)
        app = await asyncio.to_thread(
            get_xpert_app_store().deploy_app,
            app_id,
            version=payload.version,
            release_notes=payload.release_notes,
        )
        return {"app": get_xpert_app_store().app_payload(app), "preflight": preflight}
    except XpertNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except XpertStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except XpertAppError as exc:
        _raise_app_error(exc)


@router.get("/api/xpert-apps/{app_id}/deployments")
async def list_xpert_app_deployments(app_id: str) -> dict:
    try:
        app = await asyncio.to_thread(get_xpert_app_store().get_app, app_id)
        return {"app_id": app.app_id, "items": [item.model_dump(mode="json") for item in reversed(app.deployments)]}
    except XpertAppError as exc:
        _raise_app_error(exc)


@router.post("/api/xpert-apps/{app_id}/disable")
async def disable_xpert_app(app_id: str) -> dict:
    try:
        app = await asyncio.to_thread(get_xpert_app_store().disable_app, app_id)
        return {"app": get_xpert_app_store().app_payload(app)}
    except XpertAppError as exc:
        _raise_app_error(exc)


@router.post("/api/xpert-apps/{app_id}/share-token/rotate")
async def rotate_xpert_app_share_token(app_id: str) -> dict:
    try:
        app, token = await asyncio.to_thread(
            get_xpert_app_store().rotate_share_token,
            app_id,
        )
        return {
            "app": get_xpert_app_store().app_payload(app),
            "share_token": token,
            "share_url": f"/apps/{app.slug}#access={token}",
        }
    except XpertAppError as exc:
        _raise_app_error(exc)


@router.get("/api/xpert-apps/{app_id}/keys")
async def list_xpert_app_keys(app_id: str) -> dict:
    try:
        app = await asyncio.to_thread(get_xpert_app_store().get_app, app_id)
        return {
            "app_id": app.app_id,
            "items": [get_xpert_app_store().key_payload(key) for key in app.api_keys],
        }
    except XpertAppError as exc:
        _raise_app_error(exc)


@router.post("/api/xpert-apps/{app_id}/keys")
async def create_xpert_app_key(app_id: str, payload: XpertAppApiKeyCreateRequest) -> dict:
    try:
        app, key, token = await asyncio.to_thread(
            get_xpert_app_store().create_api_key,
            app_id,
            name=payload.name,
            limits=payload.limits,
            expires_at=payload.expires_at,
        )
        return {
            "app": get_xpert_app_store().app_payload(app),
            "key": get_xpert_app_store().key_payload(key),
            "api_key": token,
        }
    except XpertAppError as exc:
        _raise_app_error(exc)


@router.delete("/api/xpert-apps/{app_id}/keys/{key_id}")
async def revoke_xpert_app_key(app_id: str, key_id: str) -> dict:
    try:
        key = await asyncio.to_thread(
            get_xpert_app_store().revoke_api_key,
            app_id,
            key_id,
        )
        return {"key": get_xpert_app_store().key_payload(key)}
    except XpertAppError as exc:
        _raise_app_error(exc)


async def _authorize_public_request(
    app_slug: str,
    authorization: str | None,
    share_token: str | None,
) -> tuple[Any, XpertAppAccessGrant]:
    credential, access_type = _extract_credential(authorization, share_token)
    controller = get_xpert_app_access_controller()
    grant = await controller.authorize(
        app_slug,
        credential,
        access_type=access_type,
    )
    try:
        app = await asyncio.to_thread(get_xpert_app_store().resolve_app, app_slug)
        return app, grant
    except Exception:
        await controller.release(grant)
        raise


@router.get("/api/apps/{app_slug}/manifest")
async def get_public_xpert_app_manifest(
    app_slug: str,
    authorization: str | None = Header(default=None),
    share_token: str | None = Header(default=None, alias="X-ModelMirror-App-Token"),
):
    grant: XpertAppAccessGrant | None = None
    try:
        app, grant = await _authorize_public_request(app_slug, authorization, share_token)
        version = await asyncio.to_thread(
            get_xpert_store().get_version,
            app.xpert_id,
            app.pinned_version,
        )
        return JSONResponse(
            content={"object": "xpert.app", **_public_app_payload(app, version)},
            headers=_rate_headers(app, grant),
        )
    except XpertAppAuthenticationError:
        return _openai_error("Invalid App credential.", status_code=401, code="invalid_api_key")
    except XpertAppQuotaError as exc:
        response = _openai_error(str(exc), status_code=429, code="rate_limit_exceeded")
        response.headers["Retry-After"] = "60"
        return response
    finally:
        if grant is not None:
            await get_xpert_app_access_controller().release(grant)


@router.post("/api/v1/xpert-apps/{app_slug}/chat/completions")
async def run_public_xpert_app(
    app_slug: str,
    payload: OpenAIChatCompletionsRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    share_token: str | None = Header(default=None, alias="X-ModelMirror-App-Token"),
):
    grant: XpertAppAccessGrant | None = None
    stream_owns_grant = False
    controller = get_xpert_app_access_controller()
    try:
        app, grant = await _authorize_public_request(app_slug, authorization, share_token)
        version = await asyncio.to_thread(
            get_xpert_store().get_version,
            app.xpert_id,
            app.pinned_version,
        )
        run_payload = _prepare_openai_run(payload, version.version)
        internal_response = await _start_public_run(
            app,
            version,
            run_payload,
            request,
            grant,
        )
        response_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        headers = _rate_headers(app, grant)
        if isinstance(internal_response, StreamingResponse):
            internal_run_id = internal_response.headers.get(
                "X-ModelMirror-Runtime-Run-Id"
            )
            if internal_run_id:
                headers["X-ModelMirror-Runtime-Run-Id"] = internal_run_id
        if not payload.stream:
            try:
                output, run_id, provider_receipt = await _consume_final_output(
                    internal_response
                )
            finally:
                await controller.release(grant)
                grant = None
            headers["X-ModelMirror-Runtime-Run-Id"] = run_id
            return JSONResponse(
                content={
                    "id": response_id,
                    "object": "chat.completion",
                    "created": created,
                    "model": f"xpert-app:{app.slug}:v{version.version}",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": output},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": None,
                    "modelmirror": {
                        "run_id": run_id,
                        "provider_route_receipts": provider_receipt,
                    },
                },
                headers=headers,
            )

        async def stream_openai() -> AsyncIterator[str]:
            run_id = ""
            completed = False
            provider_receipt: dict[str, Any] | None = None
            try:
                yield "data: " + json.dumps(
                    {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": f"xpert-app:{app.slug}:v{version.version}",
                        "choices": [
                            {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
                        ],
                    },
                    ensure_ascii=False,
                ) + "\n\n"
                async for event in _iter_internal_events(internal_response):
                    if isinstance(event.get("provider_route_receipts"), dict):
                        provider_receipt = (
                            WorkflowExecutionStore._safe_provider_route_receipt(
                                event["provider_route_receipts"]
                            )
                        )
                    if event.get("event") == "workflow_meta":
                        run_id = str(event.get("run_id") or run_id)
                    elif event.get("event") == "error":
                        error_payload = {
                            "error": {
                                "message": str(event.get("message") or "Xpert App execution failed."),
                                "type": "modelmirror_app_error",
                                "param": None,
                                "code": "app_execution_failed",
                            },
                            "modelmirror": {
                                "run_id": run_id,
                                "provider_route_receipts": provider_receipt,
                            },
                        }
                        yield "data: " + json.dumps(error_payload, ensure_ascii=False) + "\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    elif event.get("event") == "workflow_end":
                        completed = True
                        run_id = str(event.get("run_id") or run_id)
                        output = str(event.get("final_output") or "")
                        if output:
                            yield "data: " + json.dumps(
                                {
                                    "id": response_id,
                                    "object": "chat.completion.chunk",
                                    "created": created,
                                    "model": f"xpert-app:{app.slug}:v{version.version}",
                                    "choices": [
                                        {"index": 0, "delta": {"content": output}, "finish_reason": None}
                                    ],
                                    "modelmirror": {
                                        "run_id": run_id,
                                        "provider_route_receipts": provider_receipt,
                                    },
                                },
                                ensure_ascii=False,
                            ) + "\n\n"
                if not completed:
                    yield "data: " + json.dumps(
                        {
                            "error": {
                                "message": "Xpert App completed without a final answer.",
                                "type": "modelmirror_app_error",
                                "param": None,
                                "code": "app_execution_failed",
                            }
                        },
                        ensure_ascii=False,
                    ) + "\n\n"
                    yield "data: [DONE]\n\n"
                    return
                yield "data: " + json.dumps(
                    {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": f"xpert-app:{app.slug}:v{version.version}",
                        "choices": [
                            {"index": 0, "delta": {}, "finish_reason": "stop"}
                        ],
                        "modelmirror": {
                            "run_id": run_id,
                            "provider_route_receipts": provider_receipt,
                        },
                    },
                    ensure_ascii=False,
                ) + "\n\n"
                yield "data: [DONE]\n\n"
            finally:
                if grant is not None:
                    await controller.release(grant)

        stream_owns_grant = True
        return StreamingResponse(
            stream_openai(),
            media_type="text/event-stream",
            headers={**headers, "Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
    except ValueError as exc:
        return _openai_error(str(exc), status_code=400, code="invalid_request_error")
    except (XpertAppAuthenticationError, XpertAppNotFoundError):
        return _openai_error("Invalid App credential.", status_code=401, code="invalid_api_key")
    except XpertAppQuotaError as exc:
        response = _openai_error(str(exc), status_code=429, code="rate_limit_exceeded")
        response.headers["Retry-After"] = "60"
        return response
    except _XpertAppExecutionFailure as exc:
        response = _openai_error(
            str(exc),
            status_code=500,
            code="app_execution_failed",
        )
        payload = json.loads(bytes(response.body).decode("utf-8"))
        payload["modelmirror"] = {
            "run_id": exc.run_id,
            "provider_route_receipts": exc.provider_receipt,
        }
        return JSONResponse(status_code=response.status_code, content=payload)
    except (XpertNotFoundError, XpertStoreError, RuntimeError) as exc:
        return _openai_error(str(exc), status_code=500, code="app_execution_failed")
    finally:
        if grant is not None and not stream_owns_grant:
            await controller.release(grant)
