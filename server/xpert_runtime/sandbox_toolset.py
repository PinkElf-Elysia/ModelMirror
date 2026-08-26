from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Callable

try:
    from server.skills.finder import SkillFinder, SkillFinderError
    from server.skills.package_validation import compute_skill_content_digest
    from server.skills.semantic_rerank_service import (
        SkillSemanticRerankError,
        SkillSemanticRerankService,
    )
    from server.skills.trust_service import (
        SkillRuntimeEnvironment,
        SkillTrustError,
        SkillTrustService,
    )
except ModuleNotFoundError as exc:  # Docker image copies server/* directly into /app.
    if exc.name != "server":
        raise
    from skills.finder import SkillFinder, SkillFinderError
    from skills.package_validation import compute_skill_content_digest
    from skills.semantic_rerank_service import (
        SkillSemanticRerankError,
        SkillSemanticRerankService,
    )
    from skills.trust_service import (
        SkillRuntimeEnvironment,
        SkillTrustError,
        SkillTrustService,
    )

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
SKILL_STAGE_MAX_FILES = 500
SKILL_STAGE_MAX_FILE_BYTES = 10 * 1024 * 1024
SKILL_STAGE_MAX_TOTAL_BYTES = 50 * 1024 * 1024
_OPAQUE_SKILL_RESOURCE_SUFFIXES = frozenset(
    {
        ".gif",
        ".jpeg",
        ".jpg",
        ".mp3",
        ".mp4",
        ".pdf",
        ".png",
        ".wav",
        ".webp",
        ".woff",
        ".woff2",
    }
)


class SandboxToolsetProvider:
    """Runtime tools backed by the isolated sandbox sidecar and installed Skills."""

    def __init__(
        self,
        store: SandboxWorkspaceStore,
        client: SandboxClientProtocol,
        *,
        skill_manager: Any,
        skill_finder: SkillFinder | None = None,
        semantic_rerank_service: SkillSemanticRerankService | None = None,
        trust_service: SkillTrustService | None = None,
        context_store: Any | None = None,
    ) -> None:
        self.store = store
        self.client = client
        self.skill_manager = skill_manager
        self.skill_finder = skill_finder or SkillFinder(skill_manager=skill_manager)
        self.semantic_rerank_service = semantic_rerank_service
        self.trust_service = trust_service or getattr(
            skill_manager, "trust_service", None
        )
        self.context_store = context_store
        self._evaluation_overlay_resolver: Callable[[str], Any] | None = None
        self._evaluation_guard = threading.RLock()
        self._evaluation_workspaces: dict[str, dict[str, str]] = {}
        self._evaluation_usage: dict[str, dict[str, Any]] = {}
        self._skill_hook_token = object()
        self._skill_hook_guard = threading.RLock()
        self._skill_hook_provision_lock = asyncio.Lock()
        self._skill_hook_workspaces: dict[str, dict[str, str]] = {}

    def configure_skill_evaluation(
        self, overlay_resolver: Callable[[str], Any] | None
    ) -> None:
        """Bind the immutable Overlay resolver used only by Skill evaluations."""

        with self._evaluation_guard:
            self._evaluation_overlay_resolver = overlay_resolver

    async def provision_skill_evaluation_workspace(
        self,
        *,
        item_id: str,
        fixtures: list[dict[str, str]],
        overlay: Any | None,
        quota_bytes: int = 64 * 1024 * 1024,
    ) -> str:
        """Create and seed a fresh profile-bound workspace before model execution.

        The sidecar provisioning capability is retained only in this provider's
        process memory. It is never copied into Runtime metadata, checkpoints,
        prompts, or model-visible tool arguments.
        """

        clean_item_id = str(item_id or "").strip()
        if not clean_item_id:
            raise RuntimeToolError(
                "skill_evaluation",
                "Evaluation item id is required.",
                code="skill_evaluation_scope_invalid",
            )
        health = await self.client.health(required_profile="skill_evaluation_v1")
        self.require_skill_evaluation_attestation(health)
        workspace = self.store.get_or_create_workspace(
            scope_type="skill_evaluation",
            scope_id=f"{clean_item_id}:{uuid.uuid4().hex}",
            node_id="skill-evaluation-agent",
            quota_bytes=max(16 * 1024 * 1024, min(int(quota_bytes), 256 * 1024 * 1024)),
            expires_at=time.time() + 2 * 60 * 60,
            metadata={"evaluation_item_id": clean_item_id, "profile": "skill_evaluation_v1"},
        )
        response = await self.client.request(
            {
                "action": "ensure_workspace",
                "workspace_id": workspace.workspace_id,
                "profile": "skill_evaluation_v1",
            }
        )
        capability = str(response.get("provisioning_capability") or "")
        if not capability:
            raise RuntimeToolError(
                "skill_evaluation",
                "Sandbox sidecar did not return an evaluation provisioning capability.",
                code="sandbox_profile_capability_missing",
            )
        with self._evaluation_guard:
            self._evaluation_workspaces[workspace.workspace_id] = {
                "item_id": clean_item_id,
                "capability": capability,
            }
            self._evaluation_usage[clean_item_id] = {
                "skill_read": False,
                "skill_stage": False,
                "tool_names": set(),
            }
        try:
            seed_files: list[tuple[str, str]] = []
            for fixture in fixtures:
                if not isinstance(fixture, dict):
                    continue
                seed_files.append(
                    (
                        f"inputs/{str(fixture.get('path') or '').strip()}",
                        str(fixture.get("content") or ""),
                    )
                )
            if overlay is not None:
                package = dict(getattr(overlay, "package", {}) or {})
                seed_files.append(
                    (
                        "skills/evaluation-skill/SKILL.md",
                        str(package.get("skill_markdown") or ""),
                    )
                )
                for path, content in sorted(dict(package.get("files") or {}).items()):
                    seed_files.append(
                        (f"skills/evaluation-skill/{str(path)}", str(content))
                    )
            for index, (path, content) in enumerate(seed_files):
                await self.client.request(
                    {
                        "action": "seed_file",
                        "workspace_id": workspace.workspace_id,
                        "profile": "skill_evaluation_v1",
                        "provisioning_capability": capability,
                        "path": path,
                        "content": content,
                        "quota_bytes": workspace.quota_bytes,
                        "operation_id": f"seed:{clean_item_id}:{index}",
                    }
                )
            return workspace.workspace_id
        except BaseException:
            await self.cleanup_skill_evaluation_workspace(workspace.workspace_id)
            raise

    async def collect_skill_evaluation_manifest(
        self, workspace_id: str
    ) -> list[dict[str, Any]]:
        binding = self._evaluation_binding(workspace_id)
        response = await self.client.request(
            {
                "action": "collect_work_manifest",
                "workspace_id": workspace_id,
                "profile": "skill_evaluation_v1",
                "provisioning_capability": binding["capability"],
            }
        )
        files = response.get("files")
        if not isinstance(files, list):
            return []
        return [
            {
                "path": item.get("path"),
                "size": item.get("size_bytes", item.get("size", 0)),
                "sha256": item.get("sha256"),
                "preview": item.get("text_preview", item.get("preview")),
            }
            for item in files
            if isinstance(item, dict)
        ]

    async def cleanup_skill_evaluation_workspace(self, workspace_id: str) -> None:
        with self._evaluation_guard:
            binding = dict(self._evaluation_workspaces.get(workspace_id) or {})
        if binding:
            try:
                await self.client.request(
                    {
                        "action": "cleanup_workspace",
                        "workspace_id": workspace_id,
                        "profile": "skill_evaluation_v1",
                        "provisioning_capability": binding.get("capability"),
                    }
                )
            finally:
                with self._evaluation_guard:
                    self._evaluation_workspaces.pop(workspace_id, None)

    def consume_skill_evaluation_usage(self, item_id: str) -> dict[str, Any]:
        with self._evaluation_guard:
            raw = self._evaluation_usage.pop(str(item_id), None) or {}
        return {
            "skill_read": bool(raw.get("skill_read", False)),
            "skill_stage": bool(raw.get("skill_stage", False)),
            "tool_names": sorted(str(item) for item in raw.get("tool_names", set())),
        }

    async def provision_skill_hook_workspace(
        self,
        *,
        skill_id: str,
        version_id: str,
        package_root: str | Path,
        task_id: str,
        run_id: str,
        node_id: str,
    ) -> dict[str, str]:
        """Seed one immutable Hook Skill into the protected authoring profile."""

        health = await self.client.health(required_profile="skill_authoring_v1")
        self.require_skill_hook_attestation(health)
        clean_skill_id = str(skill_id or "").strip()
        clean_version_id = str(version_id or "").strip()
        root = Path(package_root).resolve(strict=True)
        if not clean_skill_id or not clean_version_id or not root.is_dir():
            raise RuntimeToolError(
                "skill_hook",
                "Skill Hook package binding is invalid.",
                code="skill_hook_contract_stale",
            )
        package_files: list[tuple[Path, bytes]] = []
        total_bytes = 0
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise RuntimeToolError(
                    "skill_hook",
                    "Skill Hook package contains an unsafe link.",
                    code="skill_hook_contract_stale",
                )
            if not path.is_file() or ".git" in path.parts:
                continue
            relative = path.relative_to(root)
            content = path.read_bytes()
            if len(content) > SKILL_STAGE_MAX_FILE_BYTES:
                raise RuntimeToolError(
                    "skill_hook",
                    "Skill Hook package file exceeds the runtime limit.",
                    code="skill_hook_contract_stale",
                )
            total_bytes += len(content)
            package_files.append((relative, content))
        if (
            not package_files
            or len(package_files) > SKILL_STAGE_MAX_FILES
            or total_bytes > SKILL_STAGE_MAX_TOTAL_BYTES
        ):
            raise RuntimeToolError(
                "skill_hook",
                "Skill Hook package exceeds the runtime limits.",
                code="skill_hook_contract_stale",
            )
        try:
            snapshot = self.skill_manager.lifecycle_store.require_version(
                clean_version_id
            )
            expected_digest = str(snapshot.package_digest or "").strip().lower()
        except Exception as exc:
            raise RuntimeToolError(
                "skill_hook",
                "Frozen Skill Hook version is unavailable.",
                code="skill_hook_contract_stale",
            ) from exc
        actual_digest = compute_skill_content_digest(
            {relative.as_posix(): content for relative, content in package_files}
        )
        if (
            str(getattr(snapshot, "skill_id", "") or "").strip()
            != clean_skill_id
            or actual_digest != expected_digest
        ):
            raise RuntimeToolError(
                "skill_hook",
                "Frozen Skill Hook package no longer matches its version.",
                code="skill_hook_contract_stale",
            )
        binding_key = hashlib.sha256(
            json.dumps(
                [
                    str(task_id or ""),
                    str(run_id or ""),
                    str(node_id or ""),
                    clean_skill_id,
                    clean_version_id,
                    actual_digest,
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        async with self._skill_hook_provision_lock:
            with self._skill_hook_guard:
                existing = next(
                    (
                        dict(item)
                        for item in self._skill_hook_workspaces.values()
                        if item.get("binding_key") == binding_key
                    ),
                    None,
                )
            if existing is not None:
                return {
                    "workspace_id": existing["workspace_id"],
                    "skill_alias": existing["skill_alias"],
                    "package_digest": actual_digest,
                }
            return await self._create_skill_hook_workspace(
                task_id=str(task_id or ""),
                run_id=str(run_id or ""),
                node_id=str(node_id or ""),
                skill_id=clean_skill_id,
                version_id=clean_version_id,
                actual_digest=actual_digest,
                binding_key=binding_key,
                package_files=package_files,
            )

    async def _create_skill_hook_workspace(
        self,
        *,
        task_id: str,
        run_id: str,
        node_id: str,
        skill_id: str,
        version_id: str,
        actual_digest: str,
        binding_key: str,
        package_files: list[tuple[Path, bytes]],
    ) -> dict[str, str]:
        workspace = self.store.get_or_create_workspace(
            scope_type="skill_hook",
            scope_id=(
                f"{task_id}:{run_id}:{node_id}:{skill_id}:"
                f"{version_id}:{uuid.uuid4().hex}"
            ),
            node_id=node_id,
            quota_bytes=64 * 1024 * 1024,
            expires_at=time.time() + 24 * 60 * 60,
            metadata={
                "skill_id": skill_id,
                "version_id": version_id,
                "package_digest": actual_digest,
                "profile": "skill_authoring_v1",
            },
        )
        response = await self.client.request(
            {
                "action": "ensure_workspace",
                "workspace_id": workspace.workspace_id,
                "profile": "skill_authoring_v1",
            }
        )
        capability = str(response.get("provisioning_capability") or "")
        if not capability:
            raise RuntimeToolError(
                "skill_hook",
                "Sandbox sidecar did not return a Hook provisioning capability.",
                code="skill_hook_execution_failed",
            )
        with self._skill_hook_guard:
            self._skill_hook_workspaces[workspace.workspace_id] = {
                "workspace_id": workspace.workspace_id,
                "capability": capability,
                "skill_id": skill_id,
                "version_id": version_id,
                "skill_alias": "authoring-resource",
                "binding_key": binding_key,
            }
        try:
            for index, (relative, content) in enumerate(package_files):
                await self.client.request(
                    {
                        "action": "seed_file",
                        "workspace_id": workspace.workspace_id,
                        "profile": "skill_authoring_v1",
                        "provisioning_capability": capability,
                        "path": f"skills/authoring-resource/{relative.as_posix()}",
                        "content_base64": base64.b64encode(content).decode("ascii"),
                        "quota_bytes": workspace.quota_bytes,
                        "operation_id": (
                            f"hook-seed:{hashlib.sha256(f'{actual_digest}:{index}'.encode()).hexdigest()[:40]}"
                        ),
                    }
                )
            await self.client.request(
                {
                    "action": "seal_workspace",
                    "workspace_id": workspace.workspace_id,
                    "profile": "skill_authoring_v1",
                    "provisioning_capability": capability,
                }
            )
            return {
                "workspace_id": workspace.workspace_id,
                "skill_alias": "authoring-resource",
                "package_digest": actual_digest,
            }
        except BaseException:
            with self._skill_hook_guard:
                self._skill_hook_workspaces.pop(workspace.workspace_id, None)
            try:
                await self.client.request(
                    {
                        "action": "cleanup_workspace",
                        "workspace_id": workspace.workspace_id,
                        "profile": "skill_authoring_v1",
                        "provisioning_capability": capability,
                    }
                )
            finally:
                raise

    async def call_skill_hook_tool(
        self, workspace_id: str, call: RuntimeToolCall
    ) -> RuntimeToolResult:
        self._skill_hook_binding(workspace_id)
        metadata = dict(call.metadata or {})
        metadata["skill_hook_workspace_id"] = workspace_id
        metadata["_skill_hook_internal_token"] = self._skill_hook_token
        return await self.call_tool(
            RuntimeToolCall(
                tool_name=call.tool_name,
                arguments=dict(call.arguments or {}),
                metadata=metadata,
            )
        )

    async def cleanup_skill_hook_workspace(self, workspace_id: str) -> None:
        binding = self._skill_hook_binding(workspace_id)
        try:
            await self.client.request(
                {
                    "action": "cleanup_workspace",
                    "workspace_id": workspace_id,
                    "profile": "skill_authoring_v1",
                    "provisioning_capability": binding["capability"],
                }
            )
        finally:
            with self._skill_hook_guard:
                self._skill_hook_workspaces.pop(workspace_id, None)

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
        trust_decision = self._candidate_trust_decision(
            call,
            candidate,
            ephemeral=True,
            allow_pending_confirmation=True,
        )
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
            "trust": trust_decision,
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
        is_skill_hook = self._is_skill_hook_call(call)
        if is_skill_hook and call.tool_name not in {
            "sandbox_read_file",
            "sandbox_write_file",
            "sandbox_shell",
        }:
            raise RuntimeToolError(
                call.tool_name,
                "Tool is outside the fixed Skill Hook runtime allowlist.",
                code="skill_hook_execution_failed",
            )
        workspace = self._workspace(call)
        is_evaluation = self._is_skill_evaluation(call)
        if is_evaluation:
            self._require_skill_evaluation_tool(call)
            binding = self._evaluation_binding(workspace.workspace_id)
            item_id = str(call.metadata.get("skill_evaluation_item_id") or "")
            if binding["item_id"] != item_id:
                raise RuntimeToolError(
                    call.tool_name,
                    "Skill evaluation workspace binding changed.",
                    code="skill_evaluation_scope_invalid",
                )
            with self._evaluation_guard:
                usage = self._evaluation_usage.setdefault(
                    item_id,
                    {"skill_read": False, "skill_stage": False, "tool_names": set()},
                )
                usage.setdefault("tool_names", set()).add(call.tool_name)
        elif not is_skill_hook:
            await self.client.request({"action": "ensure_workspace", "workspace_id": workspace.workspace_id})
        try:
            if not is_evaluation and not is_skill_hook:
                await self._stage_context_attachments(workspace, call)
            if call.tool_name == "skill_list":
                return self._skill_list(call)
            if call.tool_name == "skill_read":
                return self._skill_read(call)
            if call.tool_name == "skill_stage":
                return await self._skill_stage(workspace, call)
            if call.tool_name == "skill_find":
                return await self._skill_find_with_rerank(call)
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
        if self._is_skill_hook_call(call):
            workspace_id = str(metadata.get("skill_hook_workspace_id") or "").strip()
            binding = self._skill_hook_binding(workspace_id)
            workspace = self.store.get_workspace(workspace_id)
            if (
                workspace.scope_type != "skill_hook"
                or binding.get("workspace_id") != workspace.workspace_id
            ):
                raise RuntimeToolError(
                    call.tool_name,
                    "Skill Hook workspace binding changed.",
                    code="skill_hook_contract_stale",
                )
            return workspace
        if self._is_skill_evaluation(call):
            workspace_id = str(metadata.get("skill_evaluation_workspace_id") or "").strip()
            item_id = str(metadata.get("skill_evaluation_item_id") or "").strip()
            if not workspace_id or not item_id:
                raise RuntimeToolError(
                    call.tool_name,
                    "Skill evaluation workspace metadata is incomplete.",
                    code="skill_evaluation_scope_invalid",
                )
            workspace = self.store.get_workspace(workspace_id)
            if (
                workspace.scope_type != "skill_evaluation"
                or str(workspace.metadata.get("evaluation_item_id") or "") != item_id
            ):
                raise RuntimeToolError(
                    call.tool_name,
                    "Skill evaluation workspace does not match the current item.",
                    code="skill_evaluation_scope_invalid",
                )
            return workspace
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
        if self._is_skill_evaluation(call):
            binding = self._evaluation_binding(workspace.workspace_id)
            request.update(
                {
                    "profile": "skill_evaluation_v1",
                    "provisioning_capability": binding["capability"],
                }
            )
        elif self._is_skill_hook_call(call):
            binding = self._skill_hook_binding(workspace.workspace_id)
            request.update(
                {
                    "profile": "skill_authoring_v1",
                    "provisioning_capability": binding["capability"],
                }
            )
        config = call.metadata.get("sandbox_config")
        config = config if isinstance(config, dict) else {}
        if action in {"write_file", "shell", "publish_artifact"}:
            request["operation_id"] = operation_id
        if action == "write_file":
            request["quota_bytes"] = workspace.quota_bytes
        if action == "shell":
            allowed = (
                ["python", "python3", "node", "rg"]
                if self._is_skill_evaluation(call)
                else self._csv(config.get("allowed_commands"))
                or ["python", "python3", "node", "npm", "npx", "git", "rg"]
            )
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
            if action == "search_files":
                response = self._filter_binary_skill_search_matches(
                    workspace,
                    response,
                )
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
            metadata.update(
                self._sandbox_access_metadata(
                    workspace,
                    action=action,
                    response=response,
                )
            )
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
                try:
                    try:
                        from server.file_assets.output_service import get_file_output_service
                    except ModuleNotFoundError as exc:
                        if exc.name != "server":
                            raise
                        from file_assets.output_service import get_file_output_service

                    file_output = get_file_output_service().register_runtime_artifact(
                        self.store.artifact_path(artifact.artifact_id),
                        trusted_root=self.store.workspace_root,
                        producer_kind="sandbox",
                        producer_artifact_id=artifact.artifact_id,
                        filename=artifact.filename,
                        media_type=artifact.content_type,
                        runtime_metadata=dict(call.metadata),
                        expected_size=artifact.size_bytes,
                        expected_sha256=artifact.sha256,
                    )
                    if file_output is not None:
                        metadata["file_output"] = file_output.model_dump(mode="json")
                except Exception as exc:
                    metadata["file_output"] = {
                        "status": "failed",
                        "producer_kind": "sandbox",
                        "producer_artifact_id": artifact.artifact_id,
                        "display_name": artifact.filename,
                        "error_code": str(getattr(exc, "error_code", "output_registration_failed")),
                    }
            return RuntimeToolResult(output=output, metadata=metadata)
        except Exception as exc:
            self.store.fail_operation(operation_id, error=str(exc))
            raise

    @staticmethod
    def _is_utf8_text(content: bytes, *, path: str | Path | None = None) -> bool:
        if path is not None:
            suffix = PurePosixPath(str(path).replace("\\", "/")).suffix.lower()
            if suffix in _OPAQUE_SKILL_RESOURCE_SUFFIXES:
                return False
        if content.startswith(b"%PDF-"):
            return False
        if b"\x00" in content[:4096]:
            return False
        try:
            content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return False
        return True

    def _filter_binary_skill_search_matches(
        self,
        workspace: SandboxWorkspace,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        matches = response.get("matches")
        if not isinstance(matches, list):
            return response
        workspace_root = (
            self.store.workspace_root / workspace.workspace_id
        ).resolve()
        filtered: list[dict[str, Any]] = []
        for item in matches[:100]:
            if not isinstance(item, dict):
                continue
            normalized = str(item.get("path") or "").strip().replace("\\", "/")
            if not normalized.startswith("skills/"):
                filtered.append(item)
                continue
            relative = PurePosixPath(normalized)
            if (
                relative.is_absolute()
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                continue
            target = workspace_root.joinpath(*relative.parts)
            try:
                resolved = target.resolve(strict=True)
                resolved.relative_to(workspace_root)
                content = resolved.read_bytes()
            except (OSError, ValueError):
                continue
            if not target.is_symlink() and self._is_utf8_text(
                content,
                path=relative.as_posix(),
            ):
                filtered.append(item)
        return {**response, "matches": filtered}

    def _sandbox_access_metadata(
        self,
        workspace: SandboxWorkspace,
        *,
        action: str,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        if action not in {"read_file", "search_files"}:
            return {}
        raw_paths: list[Any] = []
        if action == "read_file":
            raw_paths.append(response.get("path"))
        else:
            matches = response.get("matches")
            if isinstance(matches, list):
                raw_paths.extend(
                    item.get("path")
                    for item in matches[:100]
                    if isinstance(item, dict)
                )
        workspace_root = (
            self.store.workspace_root / workspace.workspace_id
        ).resolve()
        paths: list[str] = []
        digests: dict[str, str] = {}
        text_paths: list[str] = []
        for raw_path in raw_paths:
            normalized = str(raw_path or "").strip().replace("\\", "/")
            relative = PurePosixPath(normalized)
            if (
                not normalized
                or relative.is_absolute()
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                continue
            clean_path = relative.as_posix()
            if not clean_path.startswith("skills/"):
                continue
            target = workspace_root.joinpath(*relative.parts)
            try:
                resolved = target.resolve(strict=True)
                resolved.relative_to(workspace_root)
            except (OSError, ValueError):
                continue
            if target.is_symlink() or not resolved.is_file():
                continue
            try:
                content = resolved.read_bytes()
            except OSError:
                continue
            if clean_path in digests:
                continue
            paths.append(clean_path)
            digests[clean_path] = hashlib.sha256(content).hexdigest()
            if self._is_utf8_text(content, path=clean_path):
                text_paths.append(clean_path)
        return {
            "sandbox_accessed_paths": paths,
            "sandbox_accessed_digests": dict(sorted(digests.items())),
            "sandbox_accessed_text_paths": text_paths,
        }

    def _verify_frozen_skill_package(
        self,
        call: RuntimeToolCall,
        *,
        skill_id: str,
        version_id: str | None,
        package_files: list[tuple[Path, Path, bytes]],
    ) -> None:
        if not version_id:
            return
        try:
            snapshot = self.skill_manager.lifecycle_store.require_version(version_id)
            expected_digest = str(snapshot.package_digest or "").strip().lower()
        except Exception as exc:
            raise RuntimeToolError(
                call.tool_name,
                "Frozen Skill version evidence is unavailable.",
                code="skill_application_contract_stale",
            ) from exc
        if (
            str(getattr(snapshot, "skill_id", "") or "").strip() != skill_id
            or not re.fullmatch(r"[0-9a-f]{64}", expected_digest)
        ):
            raise RuntimeToolError(
                call.tool_name,
                "Frozen Skill version evidence is invalid.",
                code="skill_application_contract_stale",
            )
        actual_digest = compute_skill_content_digest(
            {
                relative.as_posix(): content
                for _source, relative, content in package_files
            }
        )
        if actual_digest != expected_digest:
            raise RuntimeToolError(
                call.tool_name,
                "Frozen Skill package content no longer matches its version.",
                code="skill_application_contract_stale",
            )

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
        if self._is_skill_evaluation(call):
            self._require_evaluation_alias(call, skill_id)
            item_id = str(call.metadata.get("skill_evaluation_item_id") or "")
            with self._evaluation_guard:
                self._evaluation_usage.setdefault(item_id, {}).update(
                    {"skill_read": True}
                )
            overlay = self._evaluation_overlay(call)
            if overlay is None:
                return RuntimeToolResult(
                    output=(
                        "No prior Skill version exists for this baseline. Complete "
                        "the task using only the case prompt and offline workspace."
                    ),
                    metadata={
                        "content_types": ["text"],
                        "skill_id": "evaluation-skill",
                        "skill_evaluation_read": True,
                        "overlay_available": False,
                        "application_method": "skill_read",
                        "application_source_kind": "evaluation_baseline_empty",
                        "application_resource_paths": [],
                        "application_text_resource_paths": [],
                        "application_resource_digests": {},
                        "application_expected_resource_digests": {},
                    },
                )
            package = dict(getattr(overlay, "package", {}) or {})
            content = str(package.get("skill_markdown") or "")
            skill_markdown_digest = hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest()
            return RuntimeToolResult(
                output=content[:50_000],
                metadata={
                    "content_types": ["text"],
                    "skill_id": "evaluation-skill",
                    "skill_evaluation_read": True,
                    "overlay_available": True,
                    "overlay_id": getattr(overlay, "overlay_id", None),
                    "content_digest": getattr(overlay, "content_digest", None),
                    "application_method": "skill_read",
                    "application_source_kind": "evaluation_overlay",
                    "application_version_id": getattr(
                        overlay, "overlay_id", None
                    ),
                    "application_content_digest": getattr(
                        overlay, "content_digest", None
                    ),
                    "application_resource_paths": ["SKILL.md"],
                    "application_resource_digests": {
                        "SKILL.md": skill_markdown_digest
                    },
                    "application_expected_resource_digests": {
                        "SKILL.md": skill_markdown_digest
                    },
                    "truncated": len(content) > 50_000,
                },
            )
        self._require_enabled_skill(call, skill_id)
        version_id = self._skill_version_id(call, skill_id)
        if version_id:
            package_root = self.skill_manager.get_skill_directory(
                skill_id,
                version_id=version_id,
            )
            package_files: list[tuple[Path, Path, bytes]] = []
            for path in sorted(package_root.rglob("*")):
                if path.is_symlink():
                    raise RuntimeToolError(
                        call.tool_name,
                        "Skill package contains an unsafe link.",
                        code="skill_runtime_incompatible",
                    )
                if not path.is_file() or ".git" in path.parts:
                    continue
                package_files.append(
                    (path, path.relative_to(package_root), path.read_bytes())
                )
            self._verify_frozen_skill_package(
                call,
                skill_id=skill_id,
                version_id=version_id,
                package_files=package_files,
            )
            skill_markdown_bytes = next(
                (
                    content
                    for _source, relative, content in package_files
                    if relative.as_posix() == "SKILL.md"
                ),
                None,
            )
            if skill_markdown_bytes is None:
                raise RuntimeToolError(
                    call.tool_name,
                    "Frozen Skill package is missing SKILL.md.",
                    code="skill_application_contract_stale",
                )
            content = skill_markdown_bytes.decode("utf-8", errors="replace")
        else:
            content = self.skill_manager.get_skill_content(skill_id)
            try:
                package_root = self.skill_manager.get_skill_directory(skill_id)
                skill_markdown_bytes = (package_root / "SKILL.md").read_bytes()
            except Exception:
                skill_markdown_bytes = content.encode("utf-8")
        return RuntimeToolResult(
            output=content[:50_000],
            metadata={
                "content_types": ["text"],
                "skill_id": skill_id,
                "skill_version_id": version_id,
                "application_method": "skill_read",
                "application_version_id": version_id,
                "application_resource_paths": ["SKILL.md"],
                "application_resource_digests": {
                    "SKILL.md": hashlib.sha256(skill_markdown_bytes).hexdigest()
                },
                "application_expected_resource_digests": {
                    "SKILL.md": hashlib.sha256(skill_markdown_bytes).hexdigest()
                },
                "truncated": len(content) > 50_000,
            },
        )

    def _skill_find(
        self, call: RuntimeToolCall, *, recall: bool = False
    ) -> RuntimeToolResult:
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
            search = self.skill_finder.recall if recall else self.skill_finder.find
            result = search(
                need,
                limit=24 if recall else limit,
                active_skill_ids=self._active_skill_ids(call),
                router_eligible_only=True,
            )
        except SkillFinderError as exc:
            raise RuntimeToolError(call.tool_name, str(exc), code=exc.code) from exc
        for candidate in result.get("results", []):
            if not isinstance(candidate, dict):
                continue
            try:
                installed_skill_id = str(
                    candidate.get("installedSkillId") or ""
                ).strip()
                installed_item = (
                    self._installed_skill(installed_skill_id)
                    if installed_skill_id
                    else None
                )
                source_kind = str(
                    getattr(installed_item, "source_kind", "git")
                ) if installed_item is not None else "git"
                if installed_item is not None and source_kind == "local_import":
                    try:
                        resolver = getattr(
                            self.skill_manager, "trust_activation_decision", None
                        )
                        if not callable(resolver):
                            raise RuntimeToolError(
                                call.tool_name,
                                "Local Skill trust receipt is unavailable.",
                                code="skill_trust_receipt_missing",
                            )
                        decision = resolver(
                            installed_skill_id,
                            runtime_environment=self._trust_environment(call),
                        ).to_dict()
                    except RuntimeToolError:
                        raise
                    except Exception as exc:
                        code = str(
                            getattr(exc, "code", "")
                            or "skill_trust_receipt_missing"
                        )
                        raise RuntimeToolError(
                            call.tool_name,
                            str(exc),
                            code=code,
                        ) from exc
                elif installed_item is not None and source_kind != "git":
                    decision = {
                        "mode": getattr(self.trust_service, "mode", "off"),
                        "allowed": True,
                        "trustStatus": "not_applicable",
                        "reasonCodes": [],
                    }
                else:
                    decision = self._candidate_trust_decision(
                        call,
                        candidate,
                        skill_id=installed_skill_id or None,
                        allow_pending_confirmation=(
                            candidate.get("availability") in {"missing", "stale"}
                        ),
                    )
            except RuntimeToolError as exc:
                decision = {
                    "allowed": False,
                    "errorCode": exc.code,
                    "reasonCodes": [exc.code],
                }
            candidate["trustDecision"] = decision
            candidate["trustActionable"] = bool(decision.get("allowed", False))
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

    async def _skill_find_with_rerank(
        self, call: RuntimeToolCall
    ) -> RuntimeToolResult:
        if self.semantic_rerank_service is None:
            return self._skill_find(call)
        lexical = self._skill_find(call, recall=True)
        payload = json.loads(lexical.output)
        need = str(call.arguments.get("need") or "").strip()
        try:
            limit = max(1, min(int(call.arguments.get("limit") or 6), 6))
        except (TypeError, ValueError):
            limit = 6
        try:
            outcome = await self.semantic_rerank_service.rerank_router_results(
                query=need,
                lexical_results=payload.get("results") or [],
                limit=limit,
            )
            payload["results"] = list(outcome.final_results)
            return RuntimeToolResult(
                output=json.dumps(payload, ensure_ascii=False),
                metadata={
                    **lexical.metadata,
                    "result_count": len(payload["results"]),
                    "skill_ranking_status": outcome.status,
                    "skill_ranking_receipt": outcome.receipt.serialize(),
                    "skill_ranking_warnings": list(outcome.warnings),
                },
            )
        except SkillSemanticRerankError as exc:
            raise RuntimeToolError(
                call.tool_name,
                "Skill Managed Rerank failed closed before returning results.",
                code=exc.code,
            ) from exc
        except Exception as exc:
            fail_closed = getattr(
                self.semantic_rerank_service,
                "managed_errors_fail_closed",
                None,
            )
            if callable(fail_closed) and fail_closed():
                raise RuntimeToolError(
                    call.tool_name,
                    "Skill Managed Rerank failed closed before returning results.",
                    code="provider_rerank_internal_error",
                ) from exc
            # Search index/provider failures may never break the lexical Router.
            fallback = self._skill_find(call)
            return RuntimeToolResult(
                output=fallback.output,
                metadata={
                    **fallback.metadata,
                    "skill_ranking_status": "lexical_fallback",
                    "skill_ranking_warnings": ["semantic_rerank_unavailable"],
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
        installed_item = self._installed_skill(skill_id)
        if installed_item is None or str(
            getattr(installed_item, "source_kind", "git")
        ) == "git":
            self._candidate_trust_decision(call, candidate, skill_id=skill_id)
        self._require_skill_trust(call, skill_id)
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
        trust_decision = self._candidate_trust_decision(
            call,
            candidate,
            ephemeral=True,
        )
        action = "upgrade" if candidate.get("availability") == "stale" else "install"
        if candidate.get("availability") in {"active", "installed"}:
            raise RuntimeToolError(
                call.tool_name,
                "Skill is already installed at the verified commit; use skill_enable.",
                code="skill_already_installed",
            )
        try:
            installed = await asyncio.to_thread(
                self._install_catalog_skill,
                call,
                source,
                trust_decision,
            )
        except Exception as exc:
            raise RuntimeToolError(
                call.tool_name,
                f"Verified Skill {action} failed: {str(exc)[:500]}",
                code=str(getattr(exc, "code", "") or "skill_install_failed"),
            ) from exc
        payload = {
            "activated_skill_id": installed.skill_id,
            "candidate_id": candidate["candidateId"],
            "source_ref": installed.source_ref,
            "install_action": action,
            "trust_authorization": {
                "skill_id": installed.skill_id,
                "trust_fingerprint": trust_decision.get("trustFingerprint"),
            },
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
        if self._is_skill_evaluation(call):
            self._require_evaluation_alias(call, skill_id)
            item_id = str(call.metadata.get("skill_evaluation_item_id") or "")
            with self._evaluation_guard:
                self._evaluation_usage.setdefault(item_id, {}).update(
                    {"skill_stage": True}
                )
            overlay = self._evaluation_overlay(call)
            if overlay is None:
                return RuntimeToolResult(
                    output=json.dumps(
                        {
                            "skill_id": "evaluation-skill",
                            "workspace_id": workspace.workspace_id,
                            "files": [],
                            "available": False,
                        },
                        ensure_ascii=False,
                    ),
                    metadata={
                        "content_types": ["text"],
                        "skill_id": "evaluation-skill",
                        "file_count": 0,
                        "workspace_id": workspace.workspace_id,
                        "application_method": "skill_stage",
                        "application_source_kind": "evaluation_baseline_empty",
                        "application_resource_paths": [],
                        "application_resource_digests": {},
                        "application_expected_resource_digests": {},
                    },
                )
            package = dict(getattr(overlay, "package", {}) or {})
            package_resources = {
                "SKILL.md": str(package.get("skill_markdown") or ""),
                **{
                    str(path): str(content)
                    for path, content in dict(package.get("files") or {}).items()
                },
            }
            application_resource_paths = sorted(package_resources)
            application_resource_digests = {
                path: hashlib.sha256(content.encode("utf-8")).hexdigest()
                for path, content in sorted(package_resources.items())
            }
            files = [
                "skills/evaluation-skill/SKILL.md",
                *[
                    f"skills/evaluation-skill/{path}"
                    for path in sorted(dict(package.get("files") or {}))
                ],
            ]
            return RuntimeToolResult(
                output=json.dumps(
                    {
                        "skill_id": "evaluation-skill",
                        "workspace_id": workspace.workspace_id,
                        "files": files,
                        "available": True,
                    },
                    ensure_ascii=False,
                ),
                metadata={
                    "content_types": ["text"],
                    "skill_id": "evaluation-skill",
                    "file_count": len(files),
                    "workspace_id": workspace.workspace_id,
                    "application_method": "skill_stage",
                    "application_source_kind": "evaluation_overlay",
                    "application_version_id": getattr(
                        overlay, "overlay_id", None
                    ),
                    "application_content_digest": getattr(
                        overlay, "content_digest", None
                    ),
                    "application_resource_paths": application_resource_paths,
                    "application_text_resource_paths": application_resource_paths,
                    "application_resource_digests": application_resource_digests,
                    "application_expected_resource_digests": (
                        application_resource_digests
                    ),
                },
            )
        self._require_enabled_skill(call, skill_id)
        version_id = self._skill_version_id(call, skill_id)
        root = (
            self.skill_manager.get_skill_directory(skill_id, version_id=version_id)
            if version_id
            else self.skill_manager.get_skill_directory(skill_id)
        )
        operation_base = self._operation_id(call)
        package_files: list[tuple[Path, Path, bytes]] = []
        total = 0
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise RuntimeToolError(
                    call.tool_name,
                    "Skill package contains an unsafe link.",
                    code="skill_runtime_incompatible",
                )
            if not path.is_file() or ".git" in path.parts:
                continue
            relative = path.relative_to(root)
            content = path.read_bytes()
            if len(content) > SKILL_STAGE_MAX_FILE_BYTES:
                raise RuntimeToolError(
                    call.tool_name,
                    "Skill contains a file larger than 10 MiB.",
                    code="skill_runtime_incompatible",
                )
            total += len(content)
            package_files.append((path, relative, content))
            if (
                total > SKILL_STAGE_MAX_TOTAL_BYTES
                or len(package_files) > SKILL_STAGE_MAX_FILES
            ):
                raise RuntimeToolError(
                    call.tool_name,
                    "Skill package exceeds the 500-file or 50 MiB staging limit.",
                    code="skill_runtime_incompatible",
                )
        self._verify_frozen_skill_package(
            call,
            skill_id=skill_id,
            version_id=version_id,
            package_files=package_files,
        )
        workspace_root = (self.store.workspace_root / workspace.workspace_id).resolve()
        store_root = self.store.workspace_root.resolve()
        if workspace_root.parent != store_root:
            raise RuntimeToolError(
                call.tool_name,
                "Sandbox workspace path is invalid.",
                code="skill_runtime_incompatible",
            )
        current_usage = 0
        replaced_usage = 0
        target_prefix = workspace_root / "skills" / skill_id
        if workspace_root.exists():
            for existing in workspace_root.rglob("*"):
                if existing.is_symlink():
                    continue
                if existing.is_file():
                    size = existing.stat().st_size
                    current_usage += size
                    try:
                        existing.relative_to(target_prefix)
                    except ValueError:
                        pass
                    else:
                        replaced_usage += size
        projected_usage = current_usage - replaced_usage + total
        if projected_usage > workspace.quota_bytes:
            raise RuntimeToolError(
                call.tool_name,
                "Sandbox quota is insufficient to stage this Skill package.",
                code="skill_runtime_incompatible",
            )
        files: list[str] = []
        application_resource_paths: list[str] = []
        application_text_resource_paths: list[str] = []
        application_resource_digests: dict[str, str] = {}
        for _source, relative, content in package_files:
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
            relative_path = relative.as_posix()
            application_resource_paths.append(relative_path)
            if self._is_utf8_text(content, path=relative_path):
                application_text_resource_paths.append(relative_path)
            application_resource_digests[relative_path] = hashlib.sha256(
                content
            ).hexdigest()
        return RuntimeToolResult(
            output=json.dumps({"skill_id": skill_id, "workspace_id": workspace.workspace_id, "files": files}, ensure_ascii=False),
            metadata={
                "content_types": ["text"],
                "skill_id": skill_id,
                "skill_version_id": version_id,
                "file_count": len(files),
                "workspace_id": workspace.workspace_id,
                "application_method": "skill_stage",
                "application_version_id": version_id,
                "application_resource_paths": application_resource_paths,
                "application_text_resource_paths": application_text_resource_paths,
                "application_resource_digests": application_resource_digests,
                "application_expected_resource_digests": (
                    application_resource_digests
                ),
            },
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
        bindings = call.metadata.get("skill_version_bindings")
        bound = set(bindings) if isinstance(bindings, dict) else set()
        return (
            (installed if auto_discover else configured & installed)
            | (active & installed)
            | bound
        )

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
    def _skill_version_id(call: RuntimeToolCall, skill_id: str) -> str | None:
        value = call.metadata.get("skill_version_bindings")
        if not isinstance(value, dict):
            return None
        version_id = str(value.get(skill_id) or "").strip()
        return version_id or None

    @staticmethod
    def _denied_candidate_ids(call: RuntimeToolCall) -> set[str]:
        value = call.metadata.get("denied_skill_candidate_ids")
        return {str(item) for item in value} if isinstance(value, list) else set()

    def _require_enabled_skill(self, call: RuntimeToolCall, skill_id: str) -> None:
        if not skill_id or skill_id not in self._enabled_skills(call):
            raise RuntimeToolError(call.tool_name, "Skill is not enabled for this Agent.", code="skill_denied")
        self._require_skill_trust(call, skill_id)

    def _candidate_trust_decision(
        self,
        call: RuntimeToolCall,
        candidate: dict[str, Any],
        *,
        skill_id: str | None = None,
        ephemeral: bool = False,
        allow_pending_confirmation: bool = False,
    ) -> dict[str, Any]:
        trust = dict(candidate.get("trust") or {})
        if trust.get("routerEligible") is False:
            raise RuntimeToolError(
                call.tool_name,
                "This Skill requires manual installation and is excluded from Agent Router discovery.",
                code="skill_trust_policy_blocked",
            )
        if self.trust_service is None:
            return {"mode": "off", "allowed": True}
        fingerprint = (
            str(trust.get("trustFingerprint") or "").strip()
            if ephemeral
            else None
        )
        try:
            decision, _receipt = self.trust_service.candidate_decision(
                candidate,
                skill_id=skill_id,
                ephemeral_trust_fingerprint=fingerprint,
                allow_pending_confirmation=allow_pending_confirmation,
                require_router_eligible=True,
                environment=self._trust_environment(call),
            )
        except Exception as exc:
            code = str(
                getattr(exc, "code", "") or "skill_trust_receipt_missing"
            )
            raise RuntimeToolError(
                call.tool_name,
                str(exc),
                code=code,
            ) from exc
        return decision.to_dict()

    def _require_skill_trust(self, call: RuntimeToolCall, skill_id: str) -> None:
        require_activation = getattr(self.skill_manager, "require_activation", None)
        if not callable(require_activation):
            return
        try:
            activation_kwargs: dict[str, Any] = {
                "runtime_environment": self._trust_environment(call),
                "ephemeral_authorizations": self._trust_authorizations(call),
            }
            version_id = self._skill_version_id(call, skill_id)
            if version_id:
                activation_kwargs["version_id"] = version_id
            require_activation(skill_id, **activation_kwargs)
        except Exception as exc:
            code = str(getattr(exc, "code", "") or "skill_trust_receipt_missing")
            raise RuntimeToolError(
                call.tool_name,
                str(exc),
                code=code,
            ) from exc

    def _install_catalog_skill(
        self,
        call: RuntimeToolCall,
        source: dict[str, Any],
        trust_decision: dict[str, Any],
    ) -> Any:
        if self.trust_service is None:
            return self.skill_manager.install_skill(
                source["repoUrl"],
                source["subPath"],
                source["verifiedCommit"],
            )
        return self.skill_manager.install_skill(
            source["repoUrl"],
            source["subPath"],
            source["verifiedCommit"],
            ephemeral_trust_fingerprint=trust_decision.get(
                "trustFingerprint"
            ),
            runtime_environment=self._trust_environment(call),
        )

    def _installed_skill(self, skill_id: str) -> Any | None:
        getter = getattr(self.skill_manager, "get_installed_skill", None)
        if callable(getter):
            try:
                return getter(skill_id)
            except Exception:
                return None
        try:
            return next(
                (
                    item
                    for item in self.skill_manager.list_installed_skills()
                    if str(getattr(item, "skill_id", "")) == skill_id
                ),
                None,
            )
        except Exception:
            return None

    @staticmethod
    def _trust_environment(call: RuntimeToolCall) -> SkillRuntimeEnvironment:
        return SkillRuntimeEnvironment.from_metadata(call.metadata)

    @staticmethod
    def _trust_authorizations(call: RuntimeToolCall) -> dict[str, str]:
        raw = call.metadata.get("skill_trust_authorizations")
        if not isinstance(raw, dict):
            return {}
        return {
            str(skill_id): str(fingerprint)
            for skill_id, fingerprint in raw.items()
            if str(skill_id).strip() and str(fingerprint).strip()
        }

    @staticmethod
    def _is_skill_evaluation(call: RuntimeToolCall) -> bool:
        return str(call.metadata.get("runtime_run_type") or "") == "skill_evaluation"

    def _is_skill_hook_call(self, call: RuntimeToolCall) -> bool:
        return call.metadata.get("_skill_hook_internal_token") is self._skill_hook_token

    def _skill_hook_binding(self, workspace_id: str) -> dict[str, str]:
        clean = str(workspace_id or "").strip()
        with self._skill_hook_guard:
            binding = self._skill_hook_workspaces.get(clean)
            if binding is None:
                raise RuntimeToolError(
                    "skill_hook",
                    "Skill Hook workspace capability is unavailable.",
                    code="skill_hook_execution_failed",
                )
            return dict(binding)

    @staticmethod
    def _require_skill_evaluation_tool(call: RuntimeToolCall) -> None:
        allowed = {
            "skill_read",
            "skill_stage",
            "sandbox_list_files",
            "sandbox_read_file",
            "sandbox_search_files",
            "sandbox_write_file",
            "sandbox_shell",
        }
        if call.tool_name not in allowed:
            raise RuntimeToolError(
                call.tool_name,
                "Tool is outside the fixed Skill evaluation allowlist.",
                code="skill_evaluation_tool_denied",
            )
        if str(call.metadata.get("skill_evaluation_profile") or "") != "skill_evaluation_v1":
            raise RuntimeToolError(
                call.tool_name,
                "Skill evaluation isolation profile is missing.",
                code="sandbox_profile_unsupported",
            )

    @staticmethod
    def require_skill_evaluation_attestation(health: dict[str, Any]) -> None:
        profiles = health.get("profiles")
        profile = (
            profiles.get("skill_evaluation_v1")
            if isinstance(profiles, dict)
            else None
        )
        allowed = set(profile.get("allowed_commands") or []) if isinstance(profile, dict) else set()
        if (
            health.get("engine") != "modelmirror-sandbox-v1"
            or health.get("landlock_required") is not True
            or not isinstance(profile, dict)
            or profile.get("network_policy") != "container_network_none_required"
            or set(profile.get("read_only_roots") or []) != {"inputs", "skills"}
            or set(profile.get("writable_roots") or []) != {"work", ".tmp"}
            or set(profile.get("write_file_roots") or []) != {"work"}
            or allowed != {"python", "python3", "node", "rg"}
        ):
            raise RuntimeToolError(
                "skill_evaluation",
                "Sandbox sidecar cannot prove the required Skill evaluation isolation profile.",
                code="sandbox_profile_attestation_failed",
            )

    @staticmethod
    def require_skill_hook_attestation(health: dict[str, Any]) -> None:
        profiles = health.get("profiles")
        profile = (
            profiles.get("skill_authoring_v1")
            if isinstance(profiles, dict)
            else None
        )
        allowed = (
            set(profile.get("allowed_commands") or [])
            if isinstance(profile, dict)
            else set()
        )
        if (
            health.get("engine") != "modelmirror-sandbox-v1"
            or health.get("landlock_required") is not True
            or not isinstance(profile, dict)
            or profile.get("network_policy") != "container_network_none_required"
            or set(profile.get("read_only_roots") or []) != {"inputs", "skills"}
            or set(profile.get("writable_roots") or []) != {"work", ".tmp"}
            or set(profile.get("write_file_roots") or []) != {"work"}
            or allowed != {"python", "python3", "node", "rg"}
        ):
            raise RuntimeToolError(
                "skill_hook",
                "Sandbox sidecar cannot prove the required Skill Hook isolation profile.",
                code="sandbox_profile_attestation_failed",
            )

    @staticmethod
    def _require_evaluation_alias(call: RuntimeToolCall, skill_id: str) -> None:
        if skill_id != "evaluation-skill":
            raise RuntimeToolError(
                call.tool_name,
                "Skill evaluation tools only accept the fixed evaluation-skill alias.",
                code="skill_evaluation_alias_invalid",
            )

    def _evaluation_binding(self, workspace_id: str) -> dict[str, str]:
        with self._evaluation_guard:
            binding = self._evaluation_workspaces.get(str(workspace_id))
            if binding is None:
                raise RuntimeToolError(
                    "skill_evaluation",
                    "Skill evaluation workspace capability is unavailable.",
                    code="sandbox_profile_capability_missing",
                )
            return dict(binding)

    def _evaluation_overlay(self, call: RuntimeToolCall) -> Any | None:
        overlay_id = str(call.metadata.get("skill_evaluation_overlay_id") or "").strip()
        if not overlay_id:
            return None
        with self._evaluation_guard:
            resolver = self._evaluation_overlay_resolver
        if resolver is None:
            raise RuntimeToolError(
                call.tool_name,
                "Skill evaluation Overlay resolver is unavailable.",
                code="skill_evaluation_overlay_unavailable",
            )
        try:
            return resolver(overlay_id)
        except RuntimeToolError:
            raise
        except Exception as exc:
            raise RuntimeToolError(
                call.tool_name,
                "Skill evaluation Overlay no longer matches the frozen run.",
                code="skill_evaluation_overlay_unavailable",
            ) from exc

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
        hook_workspace_id = str(
            call.metadata.get("skill_hook_workspace_id") or ""
        ).strip()
        if hook_workspace_id:
            payload["skill_hook_workspace_id"] = hook_workspace_id
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
