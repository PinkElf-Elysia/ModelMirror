from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

try:
    from server.skills.finder import SkillFinder, SkillFinderError
except ModuleNotFoundError as exc:  # Docker image copies server/* directly into /app.
    if exc.name != "server":
        raise
    from skills.finder import SkillFinder, SkillFinderError

from .capabilities import CapabilityRegistry
from .sandbox_client import SandboxClientError, SandboxClientProtocol
from .sandbox_store import SandboxWorkspace, SandboxWorkspaceStore
from .toolset import RuntimeTool, RuntimeToolCall, RuntimeToolError, RuntimeToolResult


SANDBOX_TOOL_NAMES = {
    "sandbox_list_files",
    "sandbox_read_file",
    "sandbox_write_file",
    "sandbox_search_files",
    "sandbox_shell",
    "sandbox_publish_artifact",
}
SKILL_TOOL_NAMES = {
    "skill_list",
    "skill_read",
    "skill_stage",
    "skill_find",
    "skill_enable",
    "skill_install",
}


class SandboxToolsetProvider:
    """Runtime tools backed by the isolated sandbox sidecar and installed Skills."""

    def __init__(
        self,
        store: SandboxWorkspaceStore,
        client: SandboxClientProtocol,
        *,
        skill_manager: Any,
        skill_finder: SkillFinder | None = None,
        context_store: Any | None = None,
    ) -> None:
        self.store = store
        self.client = client
        self.skill_manager = skill_manager
        self.skill_finder = skill_finder or SkillFinder(skill_manager=skill_manager)
        self.context_store = context_store

    async def list_tools(self) -> list[RuntimeTool]:
        return [
            RuntimeTool("sandbox_list_files", "List files in the current isolated workspace.", {"type": "object", "properties": {"path": {"type": "string"}}}, "sandbox"),
            RuntimeTool("sandbox_read_file", "Read a bounded UTF-8 text file from the workspace.", {"type": "object", "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer", "minimum": 1, "maximum": 200000}}, "required": ["path"]}, "sandbox"),
            RuntimeTool("sandbox_write_file", "Write UTF-8 text under work/ in the isolated workspace.", {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}, "sandbox"),
            RuntimeTool("sandbox_search_files", "Search bounded text files in the workspace.", {"type": "object", "properties": {"query": {"type": "string"}, "path": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": ["query"]}, "sandbox"),
            RuntimeTool("sandbox_shell", "Run an approved argv command in the offline isolated workspace.", {"type": "object", "properties": {"argv": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 128}, "cwd": {"type": "string"}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 300}}, "required": ["argv"]}, "sandbox"),
            RuntimeTool("sandbox_publish_artifact", "Publish one work/ file as a durable downloadable artifact.", {"type": "object", "properties": {"path": {"type": "string"}, "filename": {"type": "string"}}, "required": ["path"]}, "sandbox"),
            RuntimeTool("skill_list", "List Skills enabled for this Agent run.", {"type": "object", "properties": {}}, "skill"),
            RuntimeTool("skill_read", "Read the SKILL.md instructions for one enabled Skill.", {"type": "object", "properties": {"skill_id": {"type": "string"}}, "required": ["skill_id"]}, "skill"),
            RuntimeTool("skill_stage", "Copy an enabled Skill package into the isolated workspace.", {"type": "object", "properties": {"skill_id": {"type": "string"}}, "required": ["skill_id"]}, "skill"),
            RuntimeTool(
                "skill_find",
                "Search the verified local Skill catalog only when current capabilities are insufficient.",
                {"type": "object", "properties": {"need": {"type": "string", "minLength": 2, "maxLength": 500}, "limit": {"type": "integer", "minimum": 1, "maximum": 6}}, "required": ["need"]},
                "skill",
                read_only=True,
                parallel_safe=True,
            ),
            RuntimeTool(
                "skill_enable",
                "Activate an exact installed Skill candidate for this Agent run.",
                {"type": "object", "properties": {"candidate_id": {"type": "string"}, "candidate_fingerprint": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}, "required": ["candidate_id", "candidate_fingerprint"]},
                "skill",
                read_only=True,
                parallel_safe=False,
            ),
            RuntimeTool(
                "skill_install",
                "Request approval to install or upgrade one verified catalog Skill and activate it for this Agent run.",
                {"type": "object", "properties": {"candidate_id": {"type": "string"}, "candidate_fingerprint": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}, "required": ["candidate_id", "candidate_fingerprint"]},
                "skill",
                read_only=False,
                requires_approval=True,
                sensitive=True,
                parallel_safe=False,
            ),
        ]

    def prepare_call(self, call: RuntimeToolCall) -> None:
        """Resolve approval data before middleware persists a Skill install request."""

        if call.tool_name != "skill_install":
            return
        self._require_catalog_config(call, "catalog_install")
        self._require_install_budget(call)
        candidate = self._resolve_candidate(call)
        if candidate.get("sourceType") != "catalog":
            raise RuntimeToolError(
                call.tool_name,
                "Only verified catalog candidates can be installed.",
                code="skill_install_source_denied",
            )
        if candidate.get("availability") in {"active", "installed"}:
            raise RuntimeToolError(
                call.tool_name,
                "Skill is already installed at the verified commit; use skill_enable.",
                code="skill_already_installed",
            )
        source = dict(candidate.get("installSource") or {})
        call.metadata["skill_approval"] = {
            "candidate_id": candidate["candidateId"],
            "candidate_fingerprint": candidate["candidateFingerprint"],
            "name": candidate["name"],
            "repo_url": source["repoUrl"],
            "sub_path": source["subPath"],
            "current_sha": candidate.get("installedSourceRef"),
            "target_sha": source["verifiedCommit"],
            "install_action": (
                "upgrade" if candidate.get("availability") == "stale" else "install"
            ),
            "authorization_scope": "global_install_current_run_only",
        }
        call.metadata["approval_action_key"] = (
            f"{call.metadata.get('task_id')}:{call.metadata.get('node_id')}:"
            f"skill_install:{candidate['candidateId']}:{candidate['candidateFingerprint']}"
        )

    async def find_tool(self, tool_name: str) -> RuntimeTool | None:
        return next((tool for tool in await self.list_tools() if tool.name == tool_name), None)

    async def call_tool(self, call: RuntimeToolCall) -> RuntimeToolResult:
        if str(call.metadata.get("runtime_run_type") or "") == "xpert_app":
            raise RuntimeToolError(call.tool_name, "Xpert App cannot use Sandbox or Skill tools.", code="sandbox_app_denied")
        tool = await self.find_tool(call.tool_name)
        if tool is None:
            raise RuntimeToolError(call.tool_name, "Sandbox tool not found.", code="tool_not_found")
        workspace = self._workspace(call)
        await self.client.request({"action": "ensure_workspace", "workspace_id": workspace.workspace_id})
        try:
            await self._stage_context_attachments(workspace, call)
            if call.tool_name == "skill_list":
                return self._skill_list(call)
            if call.tool_name == "skill_read":
                return self._skill_read(call)
            if call.tool_name == "skill_stage":
                return await self._skill_stage(workspace, call)
            if call.tool_name == "skill_find":
                return self._skill_find(call)
            if call.tool_name == "skill_enable":
                return self._skill_enable(call)
            if call.tool_name == "skill_install":
                return await self._skill_install(call)
            return await self._sandbox_call(workspace, call)
        except RuntimeToolError:
            raise
        except SandboxClientError as exc:
            raise RuntimeToolError(call.tool_name, str(exc), code=exc.code) from exc
        except Exception as exc:
            raise RuntimeToolError(call.tool_name, str(exc), code="sandbox_tool_error") from exc

    def _workspace(self, call: RuntimeToolCall) -> SandboxWorkspace:
        metadata = call.metadata
        node_id = str(metadata.get("node_id") or "agent").strip()
        if metadata.get("conversation_id"):
            scope_type = "conversation"
            scope_id = f"{metadata.get('xpert_id') or 'xpert'}:{metadata.get('conversation_id')}"
            expires_at = None
        elif metadata.get("goal_id"):
            scope_type = "goal"
            scope_id = f"{metadata.get('goal_id')}:{metadata.get('goal_step_id') or node_id}"
            expires_at = None
        elif metadata.get("handoff_id"):
            scope_type = "handoff"
            scope_id = str(metadata.get("handoff_id"))
            expires_at = None
        else:
            scope_type = "workflow"
            scope_id = f"{metadata.get('task_id') or metadata.get('run_id') or 'task'}:{node_id}"
            expires_at = time.time() + 24 * 60 * 60
        config = metadata.get("sandbox_config")
        config = config if isinstance(config, dict) else {}
        quota_mb = max(16, min(int(config.get("quota_mb") or 256), 1024))
        return self.store.get_or_create_workspace(
            scope_type=scope_type,
            scope_id=scope_id,
            node_id=node_id,
            quota_bytes=quota_mb * 1024 * 1024,
            expires_at=expires_at,
            metadata={
                "xpert_id": metadata.get("xpert_id"),
                "conversation_id": metadata.get("conversation_id"),
                "goal_id": metadata.get("goal_id"),
                "handoff_id": metadata.get("handoff_id"),
            },
        )

    async def _sandbox_call(self, workspace: SandboxWorkspace, call: RuntimeToolCall) -> RuntimeToolResult:
        operation_id = self._operation_id(call)
        action_by_tool = {
            "sandbox_list_files": "list_files",
            "sandbox_read_file": "read_file",
            "sandbox_write_file": "write_file",
            "sandbox_search_files": "search_files",
            "sandbox_shell": "shell",
            "sandbox_publish_artifact": "publish_artifact",
        }
        action = action_by_tool[call.tool_name]
        request = {"action": action, "workspace_id": workspace.workspace_id, **dict(call.arguments or {})}
        config = call.metadata.get("sandbox_config")
        config = config if isinstance(config, dict) else {}
        if action in {"write_file", "shell", "publish_artifact"}:
            request["operation_id"] = operation_id
        if action == "write_file":
            request["quota_bytes"] = workspace.quota_bytes
        if action == "shell":
            allowed = self._csv(config.get("allowed_commands")) or ["python", "python3", "node", "npm", "npx", "git", "rg"]
            request["allowed_commands"] = allowed
            request["timeout_seconds"] = max(1, min(int(call.arguments.get("timeout_seconds") or config.get("timeout_seconds") or 60), 300))
        artifact_id = None
        if action == "publish_artifact":
            artifact_id = f"artifact_{hashlib.sha256(operation_id.encode()).hexdigest()[:24]}"
            request["artifact_id"] = artifact_id

        command_name = None
        if call.tool_name == "sandbox_shell":
            argv = call.arguments.get("argv")
            command_name = str(argv[0])[:100] if isinstance(argv, list) and argv else None
        self.store.start_operation(
            operation_id,
            workspace_id=workspace.workspace_id,
            tool_name=call.tool_name,
            command_name=command_name,
            metadata={"run_id": call.metadata.get("run_id"), "node_id": call.metadata.get("node_id")},
        )
        try:
            response = await self.client.request(request)
            output = json.dumps(response, ensure_ascii=False)
            self.store.complete_operation(
                operation_id,
                output_length=len(output),
                exit_code=response.get("exit_code") if isinstance(response.get("exit_code"), int) else None,
                metadata={"replayed": bool(response.get("replayed"))},
            )
            metadata: dict[str, Any] = {
                "content_types": ["text"],
                "workspace_id": workspace.workspace_id,
                "operation_id": operation_id,
                "replayed": bool(response.get("replayed")),
            }
            if artifact_id:
                artifact = self.store.register_artifact(
                    artifact_id=artifact_id,
                    workspace_id=workspace.workspace_id,
                    filename=str(response.get("filename") or Path(str(call.arguments.get("path") or "artifact")).name),
                    relative_path=str(response.get("path") or ""),
                    size_bytes=int(response.get("size_bytes") or 0),
                    sha256=str(response.get("sha256") or ""),
                    source_run_id=str(call.metadata.get("run_id") or "") or None,
                    source_node_id=str(call.metadata.get("node_id") or "") or None,
                )
                metadata["artifact_id"] = artifact.artifact_id
            return RuntimeToolResult(output=output, metadata=metadata)
        except Exception as exc:
            self.store.fail_operation(operation_id, error=str(exc))
            raise

    def _skill_list(self, call: RuntimeToolCall) -> RuntimeToolResult:
        enabled = self._enabled_skills(call)
        items = [
            {"skill_id": item.skill_id, "name": item.name, "description": item.description}
            for item in self.skill_manager.list_installed_skills()
            if item.skill_id in enabled
        ]
        return RuntimeToolResult(output=json.dumps(items, ensure_ascii=False), metadata={"content_types": ["text"], "skill_count": len(items)})

    def _skill_read(self, call: RuntimeToolCall) -> RuntimeToolResult:
        skill_id = str(call.arguments.get("skill_id") or "").strip()
        self._require_enabled_skill(call, skill_id)
        content = self.skill_manager.get_skill_content(skill_id)
        return RuntimeToolResult(output=content[:50_000], metadata={"content_types": ["text"], "skill_id": skill_id, "truncated": len(content) > 50_000})

    def _skill_find(self, call: RuntimeToolCall) -> RuntimeToolResult:
        self._require_catalog_config(call, "catalog_search")
        need = str(call.arguments.get("need") or "").strip()
        if len(need) < 2:
            raise RuntimeToolError(
                call.tool_name,
                "Skill need must contain at least two characters.",
                code="skill_find_invalid_need",
            )
        try:
            limit = max(1, min(int(call.arguments.get("limit") or 6), 6))
        except (TypeError, ValueError):
            limit = 6
        try:
            result = self.skill_finder.find(
                need,
                limit=limit,
                active_skill_ids=self._active_skill_ids(call),
            )
        except SkillFinderError as exc:
            raise RuntimeToolError(call.tool_name, str(exc), code=exc.code) from exc
        query_hash = hashlib.sha256(need.encode("utf-8")).hexdigest()
        return RuntimeToolResult(
            output=json.dumps(result, ensure_ascii=False),
            metadata={
                "content_types": ["text"],
                "skill_runtime_event": "find",
                "query_hash": query_hash,
                "result_count": len(result["results"]),
                "catalog_fingerprint": result["catalogFingerprint"],
                "ranker_version": result["rankerVersion"],
            },
        )

    def _skill_enable(self, call: RuntimeToolCall) -> RuntimeToolResult:
        self._require_catalog_config(call, "catalog_search")
        candidate = self._resolve_candidate(call)
        skill_id = str(candidate.get("installedSkillId") or "")
        if candidate.get("availability") not in {"active", "installed"} or not skill_id:
            raise RuntimeToolError(
                call.tool_name,
                "Skill is not installed at the candidate fingerprint.",
                code="skill_enable_requires_install",
            )
        source = dict(candidate.get("installSource") or {})
        source_ref = source.get("verifiedCommit") or candidate.get("installedSourceRef")
        payload = {
            "activated_skill_id": skill_id,
            "candidate_id": candidate["candidateId"],
            "source_ref": source_ref,
            "install_action": "none",
        }
        return RuntimeToolResult(
            output=json.dumps(payload, ensure_ascii=False),
            metadata={
                "content_types": ["text"],
                "skill_runtime_event": "enable",
                **payload,
            },
        )

    async def _skill_install(self, call: RuntimeToolCall) -> RuntimeToolResult:
        self._require_catalog_config(call, "catalog_install")
        candidate = self._resolve_candidate(call)
        if candidate.get("sourceType") != "catalog":
            raise RuntimeToolError(
                call.tool_name,
                "Installed-only Skills cannot be reinstalled by the Router.",
                code="skill_install_source_denied",
            )
        self._require_install_budget(call)
        source = dict(candidate["installSource"])
        action = "upgrade" if candidate.get("availability") == "stale" else "install"
        if candidate.get("availability") in {"active", "installed"}:
            raise RuntimeToolError(
                call.tool_name,
                "Skill is already installed at the verified commit; use skill_enable.",
                code="skill_already_installed",
            )
        try:
            installed = await asyncio.to_thread(
                self.skill_manager.install_skill,
                source["repoUrl"],
                source["subPath"],
                source["verifiedCommit"],
            )
        except Exception as exc:
            raise RuntimeToolError(
                call.tool_name,
                f"Verified Skill {action} failed: {str(exc)[:500]}",
                code="skill_install_failed",
            ) from exc
        payload = {
            "activated_skill_id": installed.skill_id,
            "candidate_id": candidate["candidateId"],
            "source_ref": installed.source_ref,
            "install_action": action,
        }
        return RuntimeToolResult(
            output=json.dumps(payload, ensure_ascii=False),
            metadata={
                "content_types": ["text"],
                "skill_runtime_event": action,
                "catalog_install_increment": 1,
                **payload,
            },
        )

    async def _skill_stage(self, workspace: SandboxWorkspace, call: RuntimeToolCall) -> RuntimeToolResult:
        skill_id = str(call.arguments.get("skill_id") or "").strip()
        self._require_enabled_skill(call, skill_id)
        root = self.skill_manager.get_skill_directory(skill_id)
        operation_base = self._operation_id(call)
        files: list[str] = []
        total = 0
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.is_file() or ".git" in path.parts:
                continue
            relative = path.relative_to(root)
            content = path.read_bytes()
            if len(content) > 2 * 1024 * 1024:
                raise RuntimeToolError(call.tool_name, "Skill contains a file larger than 2 MB.", code="skill_file_too_large")
            total += len(content)
            if total > 10 * 1024 * 1024 or len(files) >= 200:
                raise RuntimeToolError(call.tool_name, "Skill package exceeds the staging limit.", code="skill_package_too_large")
            destination = f"skills/{skill_id}/{relative.as_posix()}"
            await self.client.request(
                {
                    "action": "write_file",
                    "workspace_id": workspace.workspace_id,
                    "path": destination,
                    "content_base64": base64.b64encode(content).decode("ascii"),
                    "quota_bytes": workspace.quota_bytes,
                    "operation_id": f"{operation_base}:{len(files)}",
                }
            )
            files.append(destination)
        return RuntimeToolResult(
            output=json.dumps({"skill_id": skill_id, "workspace_id": workspace.workspace_id, "files": files}, ensure_ascii=False),
            metadata={"content_types": ["text"], "skill_id": skill_id, "file_count": len(files), "workspace_id": workspace.workspace_id},
        )

    async def _stage_context_attachments(self, workspace: SandboxWorkspace, call: RuntimeToolCall) -> None:
        if self.context_store is None:
            return
        config = call.metadata.get("sandbox_config")
        config = config if isinstance(config, dict) else {}
        if str(config.get("copy_attachments", True)).lower() in {"false", "0", "no"}:
            return
        xpert_id = str(call.metadata.get("file_owner_xpert_id") or call.metadata.get("xpert_id") or "").strip()
        conversation_id = str(call.metadata.get("file_conversation_id") or call.metadata.get("conversation_id") or "").strip() or None
        asset_ids = call.metadata.get("file_asset_ids")
        if not xpert_id or not isinstance(asset_ids, list):
            return
        for asset_id_raw in asset_ids[:5]:
            asset_id = str(asset_id_raw).strip()
            if not asset_id:
                continue
            asset = self.context_store.get_file(xpert_id, asset_id, conversation_id=conversation_id, include_archived=True)
            content = self.context_store.read_file_bytes(asset)
            safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(asset.filename).name)
            await self.client.request(
                {
                    "action": "write_file",
                    "workspace_id": workspace.workspace_id,
                    "path": f"inputs/{asset.asset_id[:8]}-{safe_name}",
                    "content_base64": base64.b64encode(content).decode("ascii"),
                    "quota_bytes": workspace.quota_bytes,
                    "operation_id": f"attachment:{asset.asset_id}",
                }
            )

    def _enabled_skills(self, call: RuntimeToolCall) -> set[str]:
        config = call.metadata.get("skills_config")
        config = config if isinstance(config, dict) else {}
        configured = set(self._csv(config.get("skill_ids")))
        auto_discover = str(config.get("auto_discover", False)).lower() in {"true", "1", "yes"}
        installed = {item.skill_id for item in self.skill_manager.list_installed_skills()}
        active = self._active_skill_ids(call)
        return (installed if auto_discover else configured & installed) | (active & installed)

    def _resolve_candidate(self, call: RuntimeToolCall) -> dict[str, Any]:
        candidate_id = str(call.arguments.get("candidate_id") or "").strip()
        candidate_fingerprint = str(
            call.arguments.get("candidate_fingerprint") or ""
        ).strip()
        if candidate_id in self._denied_candidate_ids(call):
            raise RuntimeToolError(
                call.tool_name,
                "User already rejected this Skill candidate in the current run.",
                code="skill_candidate_rejected",
            )
        try:
            return self.skill_finder.resolve_with_status(
                candidate_id,
                candidate_fingerprint,
                active_skill_ids=self._active_skill_ids(call),
            )
        except SkillFinderError as exc:
            raise RuntimeToolError(call.tool_name, str(exc), code=exc.code) from exc

    def _require_catalog_config(self, call: RuntimeToolCall, field: str) -> None:
        if not self._truthy(self._skills_config(call).get(field, False)):
            raise RuntimeToolError(
                call.tool_name,
                f"skills_runtime.{field} is disabled.",
                code="skill_catalog_disabled",
            )

    def _require_install_budget(self, call: RuntimeToolCall) -> None:
        try:
            install_count = max(
                0, int(call.metadata.get("catalog_install_count") or 0)
            )
        except (TypeError, ValueError):
            install_count = 0
        try:
            configured_limit = int(
                self._skills_config(call).get("max_catalog_installs") or 3
            )
        except (TypeError, ValueError):
            configured_limit = 3
        max_installs = max(1, min(configured_limit, 3))
        if install_count >= max_installs:
            raise RuntimeToolError(
                call.tool_name,
                f"This Agent run reached its catalog install limit ({max_installs}).",
                code="skill_install_limit",
            )

    @staticmethod
    def _skills_config(call: RuntimeToolCall) -> dict[str, Any]:
        config = call.metadata.get("skills_config")
        return dict(config) if isinstance(config, dict) else {}

    @staticmethod
    def _active_skill_ids(call: RuntimeToolCall) -> set[str]:
        value = call.metadata.get("active_skill_ids")
        return {str(item) for item in value} if isinstance(value, list) else set()

    @staticmethod
    def _denied_candidate_ids(call: RuntimeToolCall) -> set[str]:
        value = call.metadata.get("denied_skill_candidate_ids")
        return {str(item) for item in value} if isinstance(value, list) else set()

    def _require_enabled_skill(self, call: RuntimeToolCall, skill_id: str) -> None:
        if not skill_id or skill_id not in self._enabled_skills(call):
            raise RuntimeToolError(call.tool_name, "Skill is not enabled for this Agent.", code="skill_denied")

    @staticmethod
    def _csv(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [item.strip() for item in re.split(r"[,\n]+", str(value or "")) if item.strip()]

    @staticmethod
    def _truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "1", "yes", "on"}

    @staticmethod
    def _operation_id(call: RuntimeToolCall) -> str:
        payload = {
            "task_id": call.metadata.get("task_id"),
            "run_id": call.metadata.get("run_id"),
            "node_id": call.metadata.get("node_id"),
            "iteration": call.metadata.get("iteration"),
            "tool_name": call.tool_name,
            "arguments": call.arguments,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        return f"op:{digest[:40]}"


def register_sandbox_toolset_capability(
    capability_registry: CapabilityRegistry,
    provider: SandboxToolsetProvider,
) -> None:
    capability_registry.register(
        "sandbox_tools",
        provider,
        description="Offline isolated file, command, artifact, and installed Skill tools.",
        metadata={"provider": "sandbox", "network": "none"},
    )
