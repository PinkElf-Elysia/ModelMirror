from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

try:
    from server.skills.application_receipts import SkillHookApplicationEvidenceV2
    from server.skills.hook_contract import (
        HOOK_MANIFEST_PATH,
        MAX_HOOK_EVENT_BUDGET_SECONDS,
        MAX_HOOKS_PER_EVENT,
        SkillHookContractError,
        SkillHookDefinitionV2,
        SkillHookManifestV2,
        parse_hook_manifest,
        parse_hook_result,
        skill_plugin_hook_v2_enabled,
    )
except ModuleNotFoundError as exc:  # Docker copies server/* into /app.
    if exc.name != "server":
        raise
    from skills.application_receipts import SkillHookApplicationEvidenceV2
    from skills.hook_contract import (
        HOOK_MANIFEST_PATH,
        MAX_HOOK_EVENT_BUDGET_SECONDS,
        MAX_HOOKS_PER_EVENT,
        SkillHookContractError,
        SkillHookDefinitionV2,
        SkillHookManifestV2,
        parse_hook_manifest,
        parse_hook_result,
        skill_plugin_hook_v2_enabled,
    )

from .core_middlewares import RuntimeMiddlewareSpec
from .interrupts import RuntimeMiddlewareFatalError
from .middleware import (
    AgentMiddleware,
    TOOL_REVALIDATE_METADATA_KEY,
)
from .models import (
    MiddlewareContext,
    ModelCallRequest,
    ToolCallRequest,
    ToolCallResponse,
)
from .plugin_hooks import _csv, _require_plugin_skill_trust
from .toolset import RuntimeToolCall, RuntimeToolError, RuntimeToolResult


HOOK_RUNTIME_MODE = "typed_v2"
HOOK_CONTEXT_VERSION = "modelmirror-hook-context-v1"
MAX_HOOK_CONTEXT_BYTES = 32 * 1024
MAX_SESSION_TASK_BYTES = 8 * 1024
MAX_CONTEXT_DEPTH = 4
MAX_CONTEXT_ARRAY_ITEMS = 50
MAX_CONTEXT_STRING_CHARS = 2_000

_CACHE_KEY = "_skill_hook_v2_cache"
_STAGED_KEY = "_skill_hook_v2_staged"
_ANNOTATIONS_KEY = "_skill_hook_v2_annotations"
_STATUS_EVENTS_KEY = "skill_hook_status_events"
_SESSION_END_FINALIZED_KEY = "_skill_hook_v2_session_end_finalized"
_SENSITIVE_KEY = re.compile(
    r"(?i)(?:api[_-]?key|authorization|credential|cookie|password|secret|token)"
)
_SECRET_TEXT = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._~+/=-]{8,}|sk-[A-Za-z0-9_-]{12,}|"
    r"(?:api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]{6,})"
)
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_EMBEDDED_ABSOLUTE_PATH = re.compile(
    r"(?i)(?:[A-Z]:[\\/][^\s\"']+|/(?:Users|home|var|tmp|etc|root)/[^\s\"']+)"
)


class SkillHookRuntimeError(RuntimeMiddlewareFatalError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class _LoadedHook:
    skill_id: str
    version_id: str
    root: Path
    manifest: SkillHookManifestV2
    manifest_digest: str
    definition: SkillHookDefinitionV2
    script_digest: str
    command: str


def plugin_hook_runtime_mode(spec: RuntimeMiddlewareSpec) -> str:
    raw = str(spec.config.get("hook_mode") or "").strip()
    return raw or "legacy_argv"


def build_plugin_hooks_v2_middleware(
    spec: RuntimeMiddlewareSpec,
    *,
    skill_manager: Any,
    sandbox_provider: Any,
    application_observer: Any,
) -> AgentMiddleware:
    if not skill_plugin_hook_v2_enabled():
        raise SkillHookRuntimeError(
            "Typed Skill Hook runtime is disabled.",
            code="skill_hook_v2_disabled",
        )
    if "fail_closed" in spec.config:
        raise SkillHookRuntimeError(
            "typed_v2 does not accept the legacy fail_closed setting.",
            code="skill_hook_manifest_invalid",
        )
    skill_ids = tuple(dict.fromkeys(_csv(spec.config.get("skill_ids"))))
    if not skill_ids:
        raise SkillHookRuntimeError(
            "Typed Skill Hook middleware requires at least one Skill.",
            code="skill_hook_manifest_invalid",
        )
    if len(skill_ids) > 10:
        raise SkillHookRuntimeError(
            "Typed Skill Hook middleware exceeds the Skill limit.",
            code="skill_hook_budget_exceeded",
        )
    execution_lock = asyncio.Lock()

    async def run_event(
        event: str,
        context: MiddlewareContext,
        *,
        request: ToolCallRequest | None = None,
        response: ToolCallResponse | None = None,
        state: dict[str, Any] | None = None,
    ) -> None:
        async with execution_lock:
            loaded = _matching_hooks(
                skill_manager,
                skill_ids,
                event=event,
                tool_name=request.tool_name if request is not None else None,
                metadata=context.metadata,
            )
            if len(loaded) > MAX_HOOKS_PER_EVENT or sum(
                item.definition.timeout_seconds for item in loaded
            ) > MAX_HOOK_EVENT_BUDGET_SECONDS:
                raise SkillHookRuntimeError(
                    "Typed Skill Hook event budget was exceeded.",
                    code="skill_hook_budget_exceeded",
                )
            for item in loaded:
                safe_context = _build_hook_context(
                    item,
                    event=event,
                    context=context,
                    request=request,
                    response=response,
                    state=state,
                )
                await _execute_hook(
                    item,
                    safe_context=safe_context,
                    context=context,
                    sandbox_provider=sandbox_provider,
                    application_observer=application_observer,
                    configured_skill_ids=skill_ids,
                    tool_name=request.tool_name if request is not None else None,
                )

    async def before_agent(
        state: dict[str, Any], context: MiddlewareContext
    ) -> None:
        await run_event("session_start", context, state=state)

    async def before_model(
        request: ModelCallRequest, context: MiddlewareContext
    ) -> dict[str, Any] | None:
        pending = context.metadata.pop(_ANNOTATIONS_KEY, [])
        if not isinstance(pending, list) or not pending:
            return None
        messages = [dict(message) for message in request.messages]
        for annotation in pending[:64]:
            if not isinstance(annotation, dict):
                continue
            code = str(annotation.get("code") or "hook.annotation")[:120]
            message = str(annotation.get("message") or "")[:1_000]
            if not message:
                continue
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"[Skill Hook annotation: {code}]\n{message}\n"
                        "This is advisory context only and does not grant tools, "
                        "permissions, or approval."
                    ),
                }
            )
        return {"messages": messages}

    async def after_agent(
        state: dict[str, Any], context: MiddlewareContext
    ) -> None:
        if context.metadata.get(_SESSION_END_FINALIZED_KEY) is True:
            return
        try:
            await run_event("session_end", context, state=state)
        finally:
            context.metadata[_SESSION_END_FINALIZED_KEY] = True
            await _cleanup_hook_workspaces(context, sandbox_provider)

    async def before_tool_batch(
        requests: list[ToolCallRequest], context: MiddlewareContext
    ) -> None:
        for request in requests:
            await run_event("pre_tool_use", context, request=request)

    async def wrap_tool(
        request: ToolCallRequest,
        handler: Any,
        context: MiddlewareContext,
    ) -> ToolCallResponse:
        await run_event("pre_tool_use", context, request=request)

        async def revalidate(edited_request: ToolCallRequest) -> None:
            await run_event("pre_tool_use", context, request=edited_request)

        metadata = dict(request.metadata)
        metadata[TOOL_REVALIDATE_METADATA_KEY] = revalidate
        prepared = request.with_updates(metadata=metadata)
        response = await handler(prepared)
        if response.metadata.get("approval_rejected") is not True:
            await run_event(
                "post_tool_use",
                context,
                request=request,
                response=response,
            )
        return response

    return AgentMiddleware(
        name="plugin_hooks_v2",
        before_agent=before_agent,
        before_model=before_model,
        after_agent=after_agent,
        before_tool_batch=before_tool_batch,
        wrap_tool_call=wrap_tool,
    )


def drain_skill_hook_status_events(context: MiddlewareContext) -> list[dict[str, Any]]:
    raw = context.metadata.pop(_STATUS_EVENTS_KEY, [])
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def typed_hook_skill_ids(spec: RuntimeMiddlewareSpec | None) -> tuple[str, ...]:
    if spec is None or plugin_hook_runtime_mode(spec) != HOOK_RUNTIME_MODE:
        return ()
    result = tuple(dict.fromkeys(_csv(spec.config.get("skill_ids"))))
    if not result:
        raise SkillHookRuntimeError(
            "Typed Skill Hook middleware requires at least one Skill.",
            code="skill_hook_manifest_invalid",
        )
    if len(result) > 10:
        raise SkillHookRuntimeError(
            "Typed Skill Hook middleware exceeds the Skill limit.",
            code="skill_hook_budget_exceeded",
        )
    return result


async def _cleanup_hook_workspaces(
    context: MiddlewareContext, sandbox_provider: Any
) -> None:
    cleanup = getattr(sandbox_provider, "cleanup_skill_hook_workspace", None)
    staged = context.metadata.get(_STAGED_KEY)
    if not callable(cleanup) or not isinstance(staged, dict):
        return
    workspace_ids = {
        str(item.get("workspace_id") or "")
        for item in staged.values()
        if isinstance(item, dict) and str(item.get("workspace_id") or "").strip()
    }
    for workspace_id in sorted(workspace_ids):
        try:
            await cleanup(workspace_id)
        except Exception:
            context.metadata.setdefault("middleware_warnings", []).append(
                "plugin_hooks_v2 workspace cleanup failed"
            )
    context.metadata.pop(_STAGED_KEY, None)


def _matching_hooks(
    skill_manager: Any,
    skill_ids: Iterable[str],
    *,
    event: str,
    tool_name: str | None,
    metadata: dict[str, Any],
) -> list[_LoadedHook]:
    result: list[_LoadedHook] = []
    bindings = metadata.get("skill_version_bindings")
    bindings = bindings if isinstance(bindings, dict) else {}
    for skill_id in skill_ids:
        version_id = str(bindings.get(skill_id) or "").strip()
        if not version_id:
            raise SkillHookRuntimeError(
                "Typed Skill Hook has no immutable version binding.",
                code="skill_hook_contract_stale",
            )
        try:
            _require_plugin_skill_trust(
                skill_manager,
                skill_id,
                metadata,
                check_runtime=False,
            )
            root = Path(
                skill_manager.get_skill_directory(
                    skill_id, version_id=version_id
                )
            ).resolve()
            available_paths: list[str] = []
            for path in root.rglob("*"):
                if path.is_symlink():
                    raise SkillHookContractError(
                        "Hook package contains an unsafe link."
                    )
                if path.is_file():
                    available_paths.append(path.relative_to(root).as_posix())
            manifest_path = (root / HOOK_MANIFEST_PATH).resolve()
            if (
                root not in manifest_path.parents
                or not manifest_path.is_file()
                or manifest_path.is_symlink()
            ):
                raise SkillHookContractError("Hook manifest is unavailable.")
            manifest_bytes = manifest_path.read_bytes()
            manifest = parse_hook_manifest(
                manifest_bytes, available_paths=available_paths
            )
            manifest_digest = _sha256_bytes(manifest_bytes)
        except SkillHookRuntimeError:
            raise
        except Exception as exc:
            raise SkillHookRuntimeError(
                "Typed Skill Hook manifest could not be verified.",
                code=str(
                    getattr(exc, "code", "") or "skill_hook_manifest_invalid"
                ),
            ) from exc
        for definition in manifest.hooks:
            if definition.event != event:
                continue
            if tool_name is not None and tool_name not in definition.tool_names:
                continue
            script_path = root.joinpath(
                *PurePosixPath(definition.script_path).parts
            )
            try:
                resolved_script = script_path.resolve(strict=True)
                resolved_script.relative_to(root)
                if script_path.is_symlink() or not resolved_script.is_file():
                    raise OSError("unsafe script")
                script_bytes = resolved_script.read_bytes()
            except (OSError, ValueError) as exc:
                raise SkillHookRuntimeError(
                    "Typed Skill Hook script is unavailable.",
                    code="skill_hook_contract_stale",
                ) from exc
            command = "python" if definition.script_path.endswith(".py") else "node"
            try:
                _require_plugin_skill_trust(
                    skill_manager,
                    skill_id,
                    metadata,
                    commands={command},
                )
            except Exception as exc:
                raise SkillHookRuntimeError(
                    "Typed Skill Hook runtime is incompatible.",
                    code=str(
                        getattr(exc, "code", "") or "skill_runtime_incompatible"
                    ),
                ) from exc
            result.append(
                _LoadedHook(
                    skill_id=skill_id,
                    version_id=version_id,
                    root=root,
                    manifest=manifest,
                    manifest_digest=manifest_digest,
                    definition=definition,
                    script_digest=_sha256_bytes(script_bytes),
                    command=command,
                )
            )
    return result


async def _execute_hook(
    item: _LoadedHook,
    *,
    safe_context: dict[str, Any],
    context: MiddlewareContext,
    sandbox_provider: Any,
    application_observer: Any,
    configured_skill_ids: Iterable[str],
    tool_name: str | None,
) -> None:
    context_bytes = _json_bytes(safe_context)
    context_digest = _sha256_bytes(context_bytes)
    if safe_context.get("truncated") is True and item.definition.mode != "annotation":
        await _record_failed_evidence(
            application_observer,
            context=context,
            item=item,
            context_digest=context_digest,
            result_digest=_sha256_bytes(b"{}"),
            tool_name=tool_name,
            code="skill_hook_context_invalid",
        )
        await _emit_status(
            context,
            item,
            "failed",
            tool_name=tool_name,
            code="skill_hook_context_invalid",
        )
        raise SkillHookRuntimeError(
            "Typed Skill Hook context exceeded the safe limit.",
            code="skill_hook_context_invalid",
        )
    execution_key = _sha256_json(
        {
            "task_id": context.task_id,
            "run_id": context.metadata.get("run_id"),
            "node_id": context.metadata.get("node_id"),
            "skill_id": item.skill_id,
            "version_id": item.version_id,
            "hook_id": item.definition.hook_id,
            "event": item.definition.event,
            "manifest_digest": item.manifest_digest,
            "script_digest": item.script_digest,
            "context_digest": context_digest,
        }
    )
    cached = _cached_outcome(context, execution_key)
    if cached is None:
        cached = _receipt_outcome(
            application_observer,
            context=context,
            item=item,
            context_digest=context_digest,
        )
    if cached is not None:
        _cache_outcome(context, execution_key, cached)
        _raise_cached_block(cached, item)
        return

    await _emit_status(context, item, "planned", tool_name=tool_name)
    result_digest = _sha256_bytes(b"{}")
    operation_id = f"hook:{execution_key[:40]}"
    work_root = f"work/.modelmirror-hooks/{operation_id.replace(':', '_')}"
    context_path = f"{work_root}/context.json"
    result_path = f"{work_root}/result.json"
    shell_context_path = context_path.removeprefix("work/")
    shell_result_path = result_path.removeprefix("work/")
    evidence: list[SkillHookApplicationEvidenceV2] = []
    outcome: dict[str, Any] = {"block_code": None, "block_message": None}
    cleanup_error: Exception | None = None
    hook_workspace: dict[str, str] | None = None
    try:
        hook_workspace = await _stage_skill_once(
            item,
            context=context,
            sandbox_provider=sandbox_provider,
            configured_skill_ids=configured_skill_ids,
        )
        await _verify_staged_script(
            item,
            context=context,
            sandbox_provider=sandbox_provider,
            configured_skill_ids=configured_skill_ids,
            operation_id=operation_id,
            hook_workspace=hook_workspace,
        )
        await _emit_status(context, item, "running", tool_name=tool_name)
        await _sandbox_call(
            sandbox_provider,
            context,
            configured_skill_ids,
            RuntimeToolCall(
                tool_name="sandbox_write_file",
                arguments={
                    "path": context_path,
                    "content": context_bytes.decode("utf-8"),
                },
                metadata=_sandbox_metadata(
                    context,
                    configured_skill_ids,
                    item.command,
                    f"{operation_id}:context",
                ),
            ),
            hook_workspace=hook_workspace,
        )
        await _sandbox_call(
            sandbox_provider,
            context,
            configured_skill_ids,
            RuntimeToolCall(
                tool_name="sandbox_write_file",
                arguments={"path": result_path, "content": "{}"},
                metadata=_sandbox_metadata(
                    context,
                    configured_skill_ids,
                    item.command,
                    f"{operation_id}:result-init",
                ),
            ),
            hook_workspace=hook_workspace,
        )
        shell_result = await _sandbox_call(
            sandbox_provider,
            context,
            configured_skill_ids,
            RuntimeToolCall(
                tool_name="sandbox_shell",
                arguments={
                    "argv": [
                        item.command,
                        f"../skills/{hook_workspace['skill_alias']}/{item.definition.script_path}",
                        "--context",
                        shell_context_path,
                        "--result",
                        shell_result_path,
                    ],
                    "cwd": "work",
                    "timeout_seconds": item.definition.timeout_seconds,
                },
                metadata=_sandbox_metadata(
                    context,
                    configured_skill_ids,
                    item.command,
                    f"{operation_id}:execute",
                ),
            ),
            hook_workspace=hook_workspace,
        )
        shell_payload = _runtime_output_object(shell_result)
        if int(shell_payload.get("exit_code", 1)) != 0:
            raise SkillHookRuntimeError(
                "Typed Skill Hook script failed.",
                code="skill_hook_execution_failed",
            )
        read_result = await _sandbox_call(
            sandbox_provider,
            context,
            configured_skill_ids,
            RuntimeToolCall(
                tool_name="sandbox_read_file",
                arguments={"path": result_path, "max_chars": 200_000},
                metadata=_sandbox_metadata(
                    context,
                    configured_skill_ids,
                    item.command,
                    f"{operation_id}:result-read",
                ),
            ),
            hook_workspace=hook_workspace,
        )
        result_payload = _runtime_output_object(read_result)
        raw_result = result_payload.get("content")
        if not isinstance(raw_result, str):
            raise SkillHookRuntimeError(
                "Typed Skill Hook did not write a result.",
                code="skill_hook_result_invalid",
            )
        result_bytes = raw_result.encode("utf-8")
        result_digest = _sha256_bytes(result_bytes)
        parsed = parse_hook_result(
            result_bytes,
            hook_event=item.definition.event,
            hook_mode=item.definition.mode,
        )
        outputs = list(parsed.outputs)
        if not outputs and item.definition.mode == "annotation":
            evidence.append(
                _hook_evidence(
                    item,
                    context_digest=context_digest,
                    result_digest=result_digest,
                    code="hook_completed",
                    result_type="annotation",
                    verified=True,
                )
            )
        for output in outputs:
            result_type = "annotation"
            status = "annotated"
            if output.output_type == "deny":
                result_type = "denied"
                status = "denied"
                outcome = {
                    "block_code": "skill_hook_denied",
                    "block_message": "Typed Skill Hook denied the tool call.",
                }
            elif output.output_type == "validation":
                result_type = (
                    "validation_passed" if output.passed else "validation_failed"
                )
                status = "validated" if output.passed else "failed"
                if output.passed is False:
                    outcome = {
                        "block_code": "skill_hook_validation_failed",
                        "block_message": (
                            "Typed Skill Hook validation failed after the tool "
                            "already executed; the side effect was not rolled back."
                            if item.definition.event == "post_tool_use"
                            else "Typed Skill Hook validation failed."
                        ),
                    }
            evidence.append(
                _hook_evidence(
                    item,
                    context_digest=context_digest,
                    result_digest=result_digest,
                    code=output.code,
                    result_type=result_type,
                    verified=True,
                )
            )
            await _emit_status(
                context,
                item,
                status,
                tool_name=tool_name,
                code=output.code,
            )
            if (
                output.output_type == "annotation"
                and item.definition.event in {"session_start", "post_tool_use"}
            ):
                context.metadata.setdefault(_ANNOTATIONS_KEY, []).append(
                    {"code": output.code, "message": output.message}
                )
        _record_evidence(
            application_observer,
            context=context,
            item=item,
            evidence=evidence,
            tool_name=tool_name,
        )
        _cache_outcome(context, execution_key, outcome)
        if not outcome.get("block_code"):
            await _emit_status(
                context,
                item,
                "completed",
                tool_name=tool_name,
            )
    except Exception as exc:
        outcome["error_in_flight"] = True
        if isinstance(exc, SkillHookRuntimeError) and exc.code in {
            "skill_hook_denied",
            "skill_hook_validation_failed",
        }:
            raise
        code = str(getattr(exc, "code", "") or "skill_hook_execution_failed")
        if code not in {
            "skill_hook_context_invalid",
            "skill_hook_result_invalid",
            "skill_hook_contract_stale",
            "skill_hook_execution_failed",
            "skill_hook_evidence_unavailable",
            "skill_runtime_incompatible",
        }:
            code = "skill_hook_execution_failed"
        await _record_failed_evidence(
            application_observer,
            context=context,
            item=item,
            context_digest=context_digest,
            result_digest=result_digest,
            tool_name=tool_name,
            code=code,
        )
        await _emit_status(
            context, item, "failed", tool_name=tool_name, code=code
        )
        if item.definition.mode == "annotation":
            context.metadata.setdefault("middleware_warnings", []).append(
                f"plugin_hooks_v2 {item.definition.hook_id} failed: {code}"
            )
            return
        raise SkillHookRuntimeError(
            "Typed Skill Hook execution could not be verified.", code=code
        ) from exc
    finally:
        if hook_workspace is not None:
            for suffix, path in (
                ("wipe-context", context_path),
                ("wipe-result", result_path),
            ):
                try:
                    await _sandbox_call(
                        sandbox_provider,
                        context,
                        configured_skill_ids,
                        RuntimeToolCall(
                            tool_name="sandbox_write_file",
                            arguments={"path": path, "content": "{}"},
                            metadata=_sandbox_metadata(
                                context,
                                configured_skill_ids,
                                item.command,
                                f"{operation_id}:{suffix}",
                            ),
                        ),
                        hook_workspace=hook_workspace,
                    )
                except Exception as exc:  # Mode policy is applied below.
                    cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            await _emit_status(
                context,
                item,
                "failed",
                tool_name=tool_name,
                code="skill_hook_execution_failed",
            )
            if item.definition.mode == "annotation":
                context.metadata.setdefault("middleware_warnings", []).append(
                    "plugin_hooks_v2 cleanup failed: skill_hook_execution_failed"
                )
            elif not outcome.get("block_code") and not outcome.get(
                "error_in_flight"
            ):
                raise SkillHookRuntimeError(
                    "Typed Skill Hook cleanup failed.",
                    code="skill_hook_execution_failed",
                ) from cleanup_error
    _raise_cached_block(outcome, item)


async def _stage_skill_once(
    item: _LoadedHook,
    *,
    context: MiddlewareContext,
    sandbox_provider: Any,
    configured_skill_ids: Iterable[str],
) -> dict[str, str]:
    staged = context.metadata.setdefault(_STAGED_KEY, {})
    if not isinstance(staged, dict):
        raise SkillHookRuntimeError(
            "Typed Skill Hook stage state is invalid.",
            code="skill_hook_contract_stale",
        )
    key = f"{item.skill_id}:{item.version_id}:{item.manifest_digest}"
    existing = staged.get(key)
    if isinstance(existing, dict):
        return dict(existing)
    provision = getattr(sandbox_provider, "provision_skill_hook_workspace", None)
    if callable(provision):
        binding = await provision(
            skill_id=item.skill_id,
            version_id=item.version_id,
            package_root=item.root,
            task_id=str(context.task_id or ""),
            run_id=str(context.metadata.get("run_id") or ""),
            node_id=str(context.metadata.get("node_id") or ""),
        )
        if not isinstance(binding, dict):
            raise SkillHookRuntimeError(
                "Skill Hook workspace binding is invalid.",
                code="skill_hook_execution_failed",
            )
        normalized = {
            "workspace_id": str(binding.get("workspace_id") or ""),
            "skill_alias": str(binding.get("skill_alias") or ""),
        }
        if not all(normalized.values()):
            raise SkillHookRuntimeError(
                "Skill Hook workspace binding is incomplete.",
                code="skill_hook_execution_failed",
            )
        staged[key] = normalized
        return normalized
    result = await _sandbox_call(
        sandbox_provider,
        context,
        configured_skill_ids,
        RuntimeToolCall(
            tool_name="skill_stage",
            arguments={"skill_id": item.skill_id},
            metadata=_sandbox_metadata(
                context,
                configured_skill_ids,
                item.command,
                f"hook-stage:{_sha256_bytes(key.encode('utf-8'))[:40]}",
            ),
        ),
        hook_workspace=None,
    )
    if result.is_error:
        raise SkillHookRuntimeError(
            "Typed Skill Hook package could not be staged.",
            code="skill_hook_execution_failed",
        )
    fallback = {"workspace_id": "", "skill_alias": item.skill_id}
    staged[key] = fallback
    return fallback


async def _verify_staged_script(
    item: _LoadedHook,
    *,
    context: MiddlewareContext,
    sandbox_provider: Any,
    configured_skill_ids: Iterable[str],
    operation_id: str,
    hook_workspace: dict[str, str],
) -> None:
    workspace_path = (
        f"skills/{hook_workspace['skill_alias']}/{item.definition.script_path}"
    )
    result = await _sandbox_call(
        sandbox_provider,
        context,
        configured_skill_ids,
        RuntimeToolCall(
            tool_name="sandbox_read_file",
            arguments={"path": workspace_path, "max_chars": 200_000},
            metadata=_sandbox_metadata(
                context,
                configured_skill_ids,
                item.command,
                f"{operation_id}:script-verify",
            ),
        ),
        hook_workspace=hook_workspace,
    )
    digests = result.metadata.get("sandbox_accessed_digests")
    actual = str(digests.get(workspace_path) or "") if isinstance(digests, dict) else ""
    if actual != item.script_digest:
        raise SkillHookRuntimeError(
            "Staged Skill Hook script no longer matches its immutable version.",
            code="skill_hook_contract_stale",
        )


async def _sandbox_call(
    sandbox_provider: Any,
    context: MiddlewareContext,
    configured_skill_ids: Iterable[str],
    call: RuntimeToolCall,
    *,
    hook_workspace: dict[str, str] | None,
) -> RuntimeToolResult:
    try:
        call_hook_tool = getattr(sandbox_provider, "call_skill_hook_tool", None)
        workspace_id = str((hook_workspace or {}).get("workspace_id") or "")
        if workspace_id and callable(call_hook_tool):
            return await call_hook_tool(workspace_id, call)
        return await sandbox_provider.call_tool(call)
    except RuntimeToolError:
        raise
    except Exception as exc:
        raise SkillHookRuntimeError(
            "The isolated Skill Hook runtime is unavailable.",
            code="skill_hook_execution_failed",
        ) from exc


def _sandbox_metadata(
    context: MiddlewareContext,
    configured_skill_ids: Iterable[str],
    command: str,
    iteration: str,
) -> dict[str, Any]:
    source = context.metadata
    metadata = {
        key: source.get(key)
        for key in (
            "run_id",
            "node_id",
            "runtime_run_type",
            "xpert_id",
            "conversation_id",
            "goal_id",
            "goal_step_id",
            "handoff_id",
            "skill_trust_authorizations",
            "skill_version_bindings",
        )
        if source.get(key) is not None
    }
    metadata.update(
        {
            "task_id": context.task_id,
            "iteration": iteration,
            "skills_config": {
                "skill_ids": list(configured_skill_ids),
                "auto_discover": False,
            },
            "sandbox_config": {
                "allowed_commands": command,
                "timeout_seconds": 60,
            },
        }
    )
    return metadata


def _build_hook_context(
    item: _LoadedHook,
    *,
    event: str,
    context: MiddlewareContext,
    request: ToolCallRequest | None,
    response: ToolCallResponse | None,
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": HOOK_CONTEXT_VERSION,
        "event": event,
        "skill": {
            "skill_id": item.skill_id,
            "version_id": item.version_id,
            "manifest_digest": item.manifest_digest,
        },
        "hook": {
            "hook_id": item.definition.hook_id,
            "mode": item.definition.mode,
        },
    }
    if event == "session_start":
        task_input = _sanitize_text(
            str(context.metadata.get("hook_task_input") or ""),
            max_chars=MAX_SESSION_TASK_BYTES,
        )
        payload["session"] = {
            "task_input": task_input,
            "runtime_kind": str(
                context.metadata.get("runtime_run_type") or "workflow"
            )[:80],
            "capabilities": _sanitize_value(
                context.metadata.get("skill_runtime_environment") or {}, 1
            ),
        }
    elif event == "pre_tool_use" and request is not None:
        payload["tool"] = {
            "name": request.tool_name,
            "arguments": _sanitize_value(request.arguments, 1),
        }
    elif event == "post_tool_use" and request is not None and response is not None:
        payload["tool"] = {
            "name": request.tool_name,
            "success": not bool(response.metadata.get("is_error")),
            "content_types": _bounded_string_list(
                response.metadata.get("content_types"), limit=20
            ),
            "output_length": len(response.output or ""),
            "output_digest": _sha256_bytes((response.output or "").encode("utf-8")),
            "artifact_paths": _relative_artifact_paths(response.metadata),
        }
    elif event == "session_end":
        state = state or {}
        payload["session"] = {
            "status": str(state.get("status") or "completed")[:80],
            "output_length": max(0, int(state.get("output_length") or 0)),
            "output_digest": str(state.get("output_digest") or "")[:64] or None,
        }
    encoded = _json_bytes(payload)
    if len(encoded) <= MAX_HOOK_CONTEXT_BYTES:
        return payload
    return {
        "version": HOOK_CONTEXT_VERSION,
        "event": event,
        "skill": payload["skill"],
        "hook": payload["hook"],
        "truncated": True,
        "source_digest": _sha256_bytes(encoded),
        **(
            {"tool": {"name": request.tool_name}}
            if request is not None
            else {}
        ),
    }


def _sanitize_value(value: Any, depth: int) -> Any:
    if depth > MAX_CONTEXT_DEPTH:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _sanitize_text(value, max_chars=MAX_CONTEXT_STRING_CHARS)
    if isinstance(value, list):
        return [
            _sanitize_value(item, depth + 1)
            for item in value[:MAX_CONTEXT_ARRAY_ITEMS]
        ]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key in sorted(value, key=lambda item: str(item))[:100]:
            key = str(raw_key)[:200]
            result[key] = (
                "[REDACTED]"
                if _SENSITIVE_KEY.search(key)
                else _sanitize_value(value[raw_key], depth + 1)
            )
        return result
    return _sanitize_text(str(value), max_chars=MAX_CONTEXT_STRING_CHARS)


def _sanitize_text(value: str, *, max_chars: int) -> str:
    clean = value.replace("\x00", "")
    if _WINDOWS_ABSOLUTE_PATH.match(clean) or clean.startswith("/"):
        return "[REDACTED_PATH]"
    clean = _EMBEDDED_ABSOLUTE_PATH.sub("[REDACTED_PATH]", clean)
    clean = _SECRET_TEXT.sub("[REDACTED]", clean)
    return _truncate_utf8(clean, max_chars)


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _relative_artifact_paths(metadata: dict[str, Any]) -> list[str]:
    candidates: list[Any] = []
    for key in ("file_output",):
        item = metadata.get(key)
        if isinstance(item, dict):
            candidates.extend([item.get("relative_path"), item.get("path")])
    raw_many = metadata.get("file_outputs")
    if isinstance(raw_many, list):
        for item in raw_many[:20]:
            if isinstance(item, dict):
                candidates.extend([item.get("relative_path"), item.get("path")])
    result: list[str] = []
    for raw in candidates:
        clean = str(raw or "").strip().replace("\\", "/")
        path = PurePosixPath(clean)
        if (
            clean
            and not path.is_absolute()
            and all(part not in {"", ".", ".."} for part in path.parts)
            and len(clean) <= 240
        ):
            result.append(path.as_posix())
    return sorted(set(result))[:20]


def _bounded_string_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:100] for item in value[:limit]]


def _record_evidence(
    application_observer: Any,
    *,
    context: MiddlewareContext,
    item: _LoadedHook,
    evidence: Iterable[SkillHookApplicationEvidenceV2],
    tool_name: str | None,
) -> None:
    try:
        receipt = application_observer.record(
            skill_id=item.skill_id,
            version_id=item.version_id,
            run_id=str(context.metadata.get("run_id") or "") or None,
            task_id=context.task_id,
            node_id=str(context.metadata.get("node_id") or "") or None,
            runtime_kind=str(
                context.metadata.get("runtime_run_type") or "workflow"
            ),
            policy="advisory",
            method="hook_execute",
            tool_name=tool_name,
            hook_evidence=list(evidence),
        )
        if receipt is None:
            raise RuntimeError("receipt disabled")
    except Exception as exc:
        raise SkillHookRuntimeError(
            "Typed Skill Hook evidence could not be persisted.",
            code="skill_hook_evidence_unavailable",
        ) from exc


async def _record_failed_evidence(
    application_observer: Any,
    *,
    context: MiddlewareContext,
    item: _LoadedHook,
    context_digest: str,
    result_digest: str,
    tool_name: str | None,
    code: str,
) -> None:
    try:
        application_observer.record(
            skill_id=item.skill_id,
            version_id=item.version_id,
            run_id=str(context.metadata.get("run_id") or "") or None,
            task_id=context.task_id,
            node_id=str(context.metadata.get("node_id") or "") or None,
            runtime_kind=str(
                context.metadata.get("runtime_run_type") or "workflow"
            ),
            policy="advisory",
            method="hook_execute",
            tool_name=tool_name,
            error_code=code,
            hook_evidence=[
                _hook_evidence(
                    item,
                    context_digest=context_digest,
                    result_digest=result_digest,
                    code=_failure_evidence_code(code),
                    result_type="failed",
                    verified=False,
                )
            ],
        )
    except Exception:
        return


def _failure_evidence_code(code: str) -> str:
    normalized = re.sub(r"[^a-z0-9.-]+", ".", str(code).strip().lower())
    normalized = normalized.strip(".-")[:120]
    return normalized if normalized and normalized[0].isalpha() else "hook.failed"


def _hook_evidence(
    item: _LoadedHook,
    *,
    context_digest: str,
    result_digest: str,
    code: str,
    result_type: str,
    verified: bool,
) -> SkillHookApplicationEvidenceV2:
    return SkillHookApplicationEvidenceV2(
        hook_id=item.definition.hook_id,
        hook_event=item.definition.event,
        hook_mode=item.definition.mode,
        manifest_digest=item.manifest_digest,
        script_digest=item.script_digest,
        context_digest=context_digest,
        result_digest=result_digest,
        code=code,
        result_type=result_type,  # type: ignore[arg-type]
        verified=verified,
    )


def _receipt_outcome(
    application_observer: Any,
    *,
    context: MiddlewareContext,
    item: _LoadedHook,
    context_digest: str,
) -> dict[str, Any] | None:
    store = getattr(application_observer, "store", None)
    if store is None or not hasattr(store, "list_receipts"):
        return None
    try:
        receipts = store.list_receipts(
            run_id=str(context.metadata.get("run_id") or "") or None,
            task_id=context.task_id,
            skill_id=item.skill_id,
        )
    except Exception as exc:
        raise SkillHookRuntimeError(
            "Typed Skill Hook evidence is unavailable.",
            code="skill_hook_evidence_unavailable",
        ) from exc
    matches = [
        evidence
        for receipt in receipts
        if receipt.version_id == item.version_id
        for evidence in receipt.hook_evidence
        if evidence.hook_id == item.definition.hook_id
        and evidence.hook_event == item.definition.event
        and evidence.hook_mode == item.definition.mode
        and evidence.manifest_digest == item.manifest_digest
        and evidence.script_digest == item.script_digest
        and evidence.context_digest == context_digest
        and evidence.verified
    ]
    if not matches:
        return None
    outcomes = {
        (
            "skill_hook_denied"
            if evidence.result_type == "denied"
            else (
                "skill_hook_validation_failed"
                if evidence.result_type == "validation_failed"
                else None
            )
        )
        for evidence in matches
    }
    blocking = {item for item in outcomes if item}
    if len(blocking) > 1:
        raise SkillHookRuntimeError(
            "Typed Skill Hook evidence conflicts.",
            code="skill_hook_evidence_unavailable",
        )
    block_code = next(iter(blocking), None)
    return {
        "block_code": block_code,
        "block_message": (
            "Typed Skill Hook denied the tool call."
            if block_code == "skill_hook_denied"
            else (
                "Typed Skill Hook validation failed."
                if block_code
                else None
            )
        ),
    }


def _cached_outcome(
    context: MiddlewareContext, execution_key: str
) -> dict[str, Any] | None:
    cache = context.metadata.get(_CACHE_KEY)
    if not isinstance(cache, dict):
        return None
    item = cache.get(execution_key)
    return dict(item) if isinstance(item, dict) else None


def _cache_outcome(
    context: MiddlewareContext, execution_key: str, outcome: dict[str, Any]
) -> None:
    cache = context.metadata.setdefault(_CACHE_KEY, {})
    if not isinstance(cache, dict):
        raise SkillHookRuntimeError(
            "Typed Skill Hook cache state is invalid.",
            code="skill_hook_contract_stale",
        )
    cache[execution_key] = dict(outcome)


def _raise_cached_block(outcome: dict[str, Any], item: _LoadedHook) -> None:
    code = str(outcome.get("block_code") or "")
    if not code:
        return
    raise SkillHookRuntimeError(
        str(outcome.get("block_message") or "Typed Skill Hook blocked execution."),
        code=code,
    )


async def _emit_status(
    context: MiddlewareContext,
    item: _LoadedHook,
    status: str,
    *,
    tool_name: str | None = None,
    code: str | None = None,
) -> None:
    payload = {
        "event": "skill_hook_status",
        "status": status,
        "task_id": context.task_id,
        "run_id": str(context.metadata.get("run_id") or "") or None,
        "node_id": str(context.metadata.get("node_id") or "") or None,
        "skill_id": item.skill_id,
        "version_id": item.version_id,
        "hook_id": item.definition.hook_id,
        "hook_event": item.definition.event,
        "hook_mode": item.definition.mode,
        "tool_name": tool_name,
        "code": code,
    }
    context.metadata.setdefault(_STATUS_EVENTS_KEY, []).append(payload)
    store = context.store
    if store is None or not hasattr(store, "record_event"):
        return
    try:
        await store.record_event(
            "skill_hook_status",
            task_id=context.task_id,
            trace_id=context.trace_id,
            payload={key: value for key, value in payload.items() if key != "event"},
            severity=("error" if status in {"denied", "failed"} else "info"),
        )
    except Exception:
        context.metadata.setdefault("middleware_warnings", []).append(
            "plugin_hooks_v2 event persistence failed"
        )


def _runtime_output_object(result: RuntimeToolResult) -> dict[str, Any]:
    try:
        payload = json.loads(result.output or "{}")
    except json.JSONDecodeError as exc:
        raise SkillHookRuntimeError(
            "Sandbox returned an invalid Hook response.",
            code="skill_hook_execution_failed",
        ) from exc
    if not isinstance(payload, dict):
        raise SkillHookRuntimeError(
            "Sandbox returned an invalid Hook response.",
            code="skill_hook_execution_failed",
        )
    return payload


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_json_bytes(value))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


__all__ = [
    "HOOK_RUNTIME_MODE",
    "SkillHookRuntimeError",
    "build_plugin_hooks_v2_middleware",
    "drain_skill_hook_status_events",
    "plugin_hook_runtime_mode",
    "typed_hook_skill_ids",
]
