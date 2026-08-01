from __future__ import annotations

import json
import re
from typing import Any, Literal

from .authoring_service import AuthoringService
from .authoring_store import AuthoringProposalStore
from .capabilities import CapabilityRegistry
from .toolset import RuntimeTool, RuntimeToolCall, RuntimeToolError, RuntimeToolResult


AuthoringToolsetKind = Literal["xpert", "skill"]


class AuthoringToolsetProvider:
    """Proposal-only tools for private Xpert and Skill self-authoring."""

    def __init__(self, service: AuthoringService, kind: AuthoringToolsetKind) -> None:
        self.service = service
        self.kind = kind

    async def list_tools(self) -> list[RuntimeTool]:
        if self.kind == "xpert":
            return [
                RuntimeTool(
                    "xpert_authoring_catalog",
                    "List safe Xpert summaries available for authoring decisions.",
                    {"type": "object", "properties": {}},
                    "authoring",
                ),
                RuntimeTool(
                    "xpert_authoring_get_draft",
                    "Read one explicitly allowed Xpert draft and its revision.",
                    {
                        "type": "object",
                        "required": ["xpert_id"],
                        "properties": {"xpert_id": {"type": "string"}},
                        "additionalProperties": False,
                    },
                    "authoring",
                ),
                RuntimeTool(
                    "xpert_authoring_propose_create",
                    "Propose a new Xpert draft. This never publishes it.",
                    {
                        "type": "object",
                        "required": ["title", "xpert"],
                        "properties": {
                            "title": {"type": "string", "maxLength": 200},
                            "xpert": {"type": "object"},
                        },
                        "additionalProperties": False,
                    },
                    "authoring",
                ),
                RuntimeTool(
                    "xpert_authoring_propose_update",
                    "Propose changes to an allowed Xpert draft at a fixed base revision.",
                    {
                        "type": "object",
                        "required": ["title", "xpert_id", "base_revision", "patch"],
                        "properties": {
                            "title": {"type": "string", "maxLength": 200},
                            "xpert_id": {"type": "string"},
                            "base_revision": {"type": "integer", "minimum": 1},
                            "patch": {"type": "object"},
                        },
                        "additionalProperties": False,
                    },
                    "authoring",
                ),
                RuntimeTool(
                    "xpert_authoring_validate_proposal",
                    "Validate a pending Xpert proposal without applying it.",
                    self._proposal_schema(),
                    "authoring",
                ),
            ]
        return [
            RuntimeTool(
                "skill_authoring_catalog",
                "List Workspace Skill draft summaries.",
                {"type": "object", "properties": {}},
                "authoring",
            ),
            RuntimeTool(
                "skill_authoring_get_draft",
                "Read one explicitly allowed Workspace Skill draft.",
                {
                    "type": "object",
                    "required": ["draft_id"],
                    "properties": {"draft_id": {"type": "string"}},
                    "additionalProperties": False,
                },
                "authoring",
            ),
            RuntimeTool(
                "skill_authoring_propose_create",
                "Propose a Workspace Skill draft. This never installs it.",
                {
                    "type": "object",
                    "required": ["title", "skill"],
                    "properties": {
                        "title": {"type": "string", "maxLength": 200},
                        "skill": {"type": "object"},
                    },
                    "additionalProperties": False,
                },
                "authoring",
            ),
            RuntimeTool(
                "skill_authoring_propose_update",
                "Propose changes to an allowed Skill draft at a fixed revision.",
                {
                    "type": "object",
                    "required": ["title", "draft_id", "base_revision", "skill"],
                    "properties": {
                        "title": {"type": "string", "maxLength": 200},
                        "draft_id": {"type": "string"},
                        "base_revision": {"type": "integer", "minimum": 1},
                        "skill": {"type": "object"},
                    },
                    "additionalProperties": False,
                },
                "authoring",
            ),
            RuntimeTool(
                "skill_authoring_validate_proposal",
                "Validate a pending Skill proposal without applying it.",
                self._proposal_schema(),
                "authoring",
            ),
        ]

    async def find_tool(self, tool_name: str) -> RuntimeTool | None:
        return next((tool for tool in await self.list_tools() if tool.name == tool_name), None)

    async def call_tool(self, call: RuntimeToolCall) -> RuntimeToolResult:
        if str(call.metadata.get("runtime_run_type") or "") == "xpert_app":
            raise RuntimeToolError(
                call.tool_name,
                "Public Xpert Apps cannot use authoring tools.",
                code="authoring_app_denied",
            )
        if await self.find_tool(call.tool_name) is None:
            raise RuntimeToolError(call.tool_name, "Authoring tool not found.", code="tool_not_found")
        source = self._source(call)
        config = dict(
            call.metadata.get(
                "xpert_authoring_config" if self.kind == "xpert" else "skill_creator_config"
            )
            or {}
        )
        arguments = dict(call.arguments or {})
        try:
            if call.tool_name.endswith("_catalog"):
                payload = self._catalog()
            elif call.tool_name.endswith("_get_draft"):
                payload = self._get_draft(arguments, config, source)
            elif call.tool_name.endswith("_propose_create"):
                self._require_enabled(config, "allow_create", "Creating proposals is disabled.")
                body = dict(arguments.get("xpert") or arguments.get("skill") or {})
                proposal = self.service.proposal_store.create(
                    kind="xpert_create" if self.kind == "xpert" else "skill_create",
                    title=str(arguments.get("title") or ""),
                    payload=body,
                    **source,
                )
                payload = AuthoringProposalStore.serialize(proposal)
            elif call.tool_name.endswith("_propose_update"):
                self._require_enabled(config, "allow_update", "Updating proposals is disabled.")
                target_id = str(
                    arguments.get("xpert_id") or arguments.get("draft_id") or ""
                ).strip()
                self._require_allowed_target(target_id, config, source)
                proposal = self.service.proposal_store.create(
                    kind="xpert_update" if self.kind == "xpert" else "skill_update",
                    title=str(arguments.get("title") or ""),
                    payload=(
                        {"patch": dict(arguments.get("patch") or {})}
                        if self.kind == "xpert"
                        else {"skill": dict(arguments.get("skill") or {})}
                    ),
                    target_id=target_id,
                    base_revision=int(arguments.get("base_revision") or 0),
                    **source,
                )
                payload = AuthoringProposalStore.serialize(proposal)
            else:
                proposal_id = str(arguments.get("proposal_id") or "").strip()
                proposal = self.service.proposal_store.require(proposal_id)
                if proposal.source_id != source["source_id"] and proposal.source_xpert_id != source.get("source_xpert_id"):
                    raise RuntimeToolError(
                        call.tool_name,
                        "Proposal belongs to another authoring source.",
                        code="authoring_scope_denied",
                    )
                proposal = self.service.validate(
                    proposal_id, revision=int(arguments.get("revision") or 0)
                )
                payload = AuthoringProposalStore.serialize(proposal)
        except RuntimeToolError:
            raise
        except Exception as exc:
            raise RuntimeToolError(
                call.tool_name, str(exc)[:500], code="authoring_error"
            ) from exc
        output = json.dumps(payload, ensure_ascii=False)
        return RuntimeToolResult(
            output=output,
            metadata={
                "content_types": ["text"],
                "authoring_kind": self.kind,
                "output_length": len(output),
            },
        )

    def _catalog(self) -> Any:
        if self.kind == "xpert":
            return [
                {
                    "id": item.id,
                    "slug": item.slug,
                    "name": item.name,
                    "description": item.description,
                    "tags": item.tags,
                    "status": item.status,
                    "draft_revision": item.draft_revision,
                    "published_version": item.published_version,
                    "updated_at": item.updated_at,
                }
                for item in self.service.xpert_store.list_xperts(limit=200)
            ]
        return [
            {
                "draft_id": item.draft_id,
                "slug": item.slug,
                "name": item.name,
                "description": item.description,
                "status": item.status,
                "revision": item.revision,
                "file_count": 1 + len(item.files),
                "updated_at": item.updated_at,
            }
            for item in self.service.skill_draft_store.list(limit=200)
        ]

    def _get_draft(
        self, arguments: dict[str, Any], config: dict[str, Any], source: dict[str, Any]
    ) -> Any:
        target_id = str(
            arguments.get("xpert_id") or arguments.get("draft_id") or ""
        ).strip()
        self._require_allowed_target(target_id, config, source)
        if self.kind == "xpert":
            item = self.service.xpert_store.get_xpert(target_id)
            return {
                "id": item.id,
                "name": item.name,
                "slug": item.slug,
                "description": item.description,
                "tags": item.tags,
                "starters": item.starters,
                "draft_revision": item.draft_revision,
                "draft": item.draft.model_dump(mode="json"),
            }
        item = self.service.skill_draft_store.require(target_id)
        return self.service.skill_draft_store.serialize(item, include_content=True)

    def _require_allowed_target(
        self, target_id: str, config: dict[str, Any], source: dict[str, Any]
    ) -> None:
        allowed_key = "allowed_xpert_ids" if self.kind == "xpert" else "allowed_draft_ids"
        allowed = {
            value.strip()
            for value in re.split(r"[,\n]", str(config.get(allowed_key) or ""))
            if value.strip()
        }
        if self.kind == "xpert" and source.get("source_xpert_id"):
            allowed.add(str(source["source_xpert_id"]))
        if target_id not in allowed:
            raise RuntimeToolError(
                f"{self.kind}_authoring_get_draft",
                "Target is not in this Agent's explicit authoring scope.",
                code="authoring_scope_denied",
            )

    @staticmethod
    def _source(call: RuntimeToolCall) -> dict[str, Any]:
        source_xpert_id = str(call.metadata.get("xpert_id") or "").strip() or None
        source_run_id = str(call.metadata.get("run_id") or "").strip() or None
        source_type = str(call.metadata.get("runtime_run_type") or "workflow")[:80]
        source_id = (
            str(
                call.metadata.get("goal_id")
                or call.metadata.get("handoff_id")
                or call.metadata.get("conversation_id")
                or source_run_id
                or "workflow"
            ).strip()
            or "workflow"
        )
        return {
            "source_type": source_type,
            "source_id": source_id,
            "source_xpert_id": source_xpert_id,
            "source_run_id": source_run_id,
        }

    @staticmethod
    def _require_enabled(config: dict[str, Any], key: str, message: str) -> None:
        value = str(config.get(key, True)).strip().lower()
        if value not in {"1", "true", "yes", "on"}:
            raise RuntimeToolError("authoring", message, code="authoring_action_denied")

    @staticmethod
    def _proposal_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["proposal_id", "revision"],
            "properties": {
                "proposal_id": {"type": "string"},
                "revision": {"type": "integer", "minimum": 1},
            },
            "additionalProperties": False,
        }


def register_authoring_toolset_capabilities(
    registry: CapabilityRegistry,
    xpert_provider: AuthoringToolsetProvider,
    skill_provider: AuthoringToolsetProvider,
) -> None:
    registry.register(
        "xpert_authoring_tools",
        xpert_provider,
        description="Proposal-only private Xpert draft authoring tools.",
        metadata={"provider": "authoring", "app_forbidden": True},
    )
    registry.register(
        "skill_creator_tools",
        skill_provider,
        description="Proposal-only private Workspace Skill authoring tools.",
        metadata={"provider": "authoring", "app_forbidden": True},
    )
