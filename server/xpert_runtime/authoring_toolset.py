from __future__ import annotations

import json
import re
from typing import Any, Literal

try:
    from server.skills.creator_quality import evaluate_creator_payload
    from server.skills.draft_store import SkillDraftValidationError
except ModuleNotFoundError:
    from skills.creator_quality import evaluate_creator_payload
    from skills.draft_store import SkillDraftValidationError

from .authoring_service import AuthoringService
from .authoring_store import (
    AuthoringProposalStore,
    AuthoringProposalValidationError,
)
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
                        "skill": self._skill_package_schema(require_complete=True),
                        "design": self._creator_design_schema(),
                        "creator_contract_version": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "authoring",
                read_only=False,
                parallel_safe=False,
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
                        "skill": self._skill_package_schema(require_complete=False),
                        "design": self._creator_design_schema(),
                        "creator_contract_version": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "authoring",
                read_only=False,
                parallel_safe=False,
            ),
            RuntimeTool(
                "skill_authoring_validate_proposal",
                "Validate a pending Skill proposal without applying it.",
                self._proposal_schema(),
                "authoring",
                read_only=False,
                parallel_safe=False,
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
                proposal_payload = body
                if self.kind == "skill" and source.get("creator_session_id"):
                    try:
                        body = self.service.skill_draft_store.validate_package(
                            name=str(body.get("name") or ""),
                            slug=str(body.get("slug") or ""),
                            description=str(body.get("description") or ""),
                            skill_markdown=str(
                                body.get("skill_markdown")
                                or body.get("SKILL.md")
                                or ""
                            ),
                            files=dict(body.get("files") or {}),
                        )
                    except SkillDraftValidationError as exc:
                        raise RuntimeToolError(
                            call.tool_name,
                            "The proposed Skill does not form a valid package.",
                            code="skill_package_invalid",
                        ) from exc
                    proposal_payload = self._creator_payload(
                        arguments,
                        body,
                        call.metadata,
                    )
                    self._require_creator_quality(call.tool_name, proposal_payload)
                proposal = self.service.proposal_store.create(
                    kind="xpert_create" if self.kind == "xpert" else "skill_create",
                    title=str(arguments.get("title") or ""),
                    payload=proposal_payload,
                    **source,
                )
                payload = AuthoringProposalStore.serialize(proposal)
            elif call.tool_name.endswith("_propose_update"):
                self._require_enabled(config, "allow_update", "Updating proposals is disabled.")
                creator_target = None
                skill_payload = dict(arguments.get("skill") or {})
                if self.kind == "skill" and source.get("creator_session_id"):
                    allowed_targets = {
                        value.strip()
                        for value in re.split(
                            r"[,\n]", str(config.get("allowed_draft_ids") or "")
                        )
                        if value.strip()
                    }
                    if len(allowed_targets) != 1:
                        raise RuntimeToolError(
                            call.tool_name,
                            "Dedicated Skill Creator update requires one server-bound draft.",
                            code="skill_creator_target_invalid",
                        )
                    target_id = next(iter(allowed_targets))
                    try:
                        creator_target = self.service.skill_draft_store.require(
                            target_id
                        )
                    except Exception as exc:
                        raise RuntimeToolError(
                            call.tool_name,
                            "The server-bound Skill Creator draft is unavailable.",
                            code="skill_creator_target_invalid",
                        ) from exc
                    if creator_target.creator_session_id != source.get(
                        "creator_session_id"
                    ):
                        raise RuntimeToolError(
                            call.tool_name,
                            "The server-bound draft belongs to another Creator session.",
                            code="skill_creator_target_invalid",
                        )
                    base_revision = creator_target.revision
                    base_digest = creator_target.content_digest
                    try:
                        skill_payload = self.service.skill_draft_store.validate_package(
                            name=str(skill_payload.get("name") or creator_target.name),
                            slug=str(skill_payload.get("slug") or creator_target.slug),
                            description=str(
                                skill_payload.get("description")
                                if "description" in skill_payload
                                else creator_target.description
                            ),
                            skill_markdown=str(
                                skill_payload.get("skill_markdown")
                                or skill_payload.get("SKILL.md")
                                or creator_target.skill_markdown
                            ),
                            files=dict(
                                skill_payload.get("files")
                                if "files" in skill_payload
                                else creator_target.files
                            ),
                        )
                    except SkillDraftValidationError as exc:
                        raise RuntimeToolError(
                            call.tool_name,
                            "The proposed Skill update does not form a valid package.",
                            code="skill_package_invalid",
                        ) from exc
                else:
                    target_id = str(
                        arguments.get("xpert_id") or arguments.get("draft_id") or ""
                    ).strip()
                    self._require_allowed_target(target_id, config, source)
                    base_revision = int(arguments.get("base_revision") or 0)
                    base_digest = None
                proposal_payload = (
                    {"patch": dict(arguments.get("patch") or {})}
                    if self.kind == "xpert"
                    else {"skill": skill_payload}
                )
                if self.kind == "skill" and source.get("creator_session_id"):
                    proposal_payload = self._creator_payload(
                        arguments,
                        skill_payload,
                        call.metadata,
                    )
                    self._require_creator_quality(call.tool_name, proposal_payload)
                proposal = self.service.proposal_store.create(
                    kind="xpert_update" if self.kind == "xpert" else "skill_update",
                    title=str(arguments.get("title") or ""),
                    payload=proposal_payload,
                    target_id=target_id,
                    base_revision=base_revision,
                    base_digest=base_digest,
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
        except AuthoringProposalValidationError as exc:
            raise RuntimeToolError(
                call.tool_name,
                str(exc)[:500],
                code=getattr(exc, "code", "authoring_validation"),
            ) from exc
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
        creator_session_id = (
            str(call.metadata.get("creator_session_id") or "").strip() or None
        )
        creator_session_revision = call.metadata.get("creator_session_revision")
        source_xpert_id = str(call.metadata.get("xpert_id") or "").strip() or None
        source_run_id = str(call.metadata.get("run_id") or "").strip() or None
        source_task_id = str(call.metadata.get("task_id") or "").strip() or None
        source_type = (
            "skill_creator"
            if creator_session_id
            else str(call.metadata.get("runtime_run_type") or "workflow")[:80]
        )
        source_id = (
            creator_session_id
            or str(
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
            "source_task_id": source_task_id,
            "creator_session_id": creator_session_id,
            "creator_session_revision": (
                int(creator_session_revision)
                if creator_session_revision is not None
                else None
            ),
            "actor_kind": "workflow_agent",
            "actor_id": (
                "skill-creator-assistant-v1" if creator_session_id else source_xpert_id
            ),
        }

    @staticmethod
    def _require_enabled(config: dict[str, Any], key: str, message: str) -> None:
        value = str(config.get(key, True)).strip().lower()
        if value not in {"1", "true", "yes", "on"}:
            raise RuntimeToolError("authoring", message, code="authoring_action_denied")

    @staticmethod
    def _creator_payload(
        arguments: dict[str, Any],
        skill: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        raw_requirement_ids = metadata.get("creator_requirement_ids") or []
        if not isinstance(raw_requirement_ids, (list, tuple)):
            raw_requirement_ids = []
        requirement_ids = list(
            dict.fromkeys(
                str(item or "").strip()
                for item in raw_requirement_ids
                if str(item or "").strip()
            )
        )
        return {
            "skill": dict(skill),
            "design": dict(arguments.get("design") or {}),
            "creator_contract_version": str(
                arguments.get("creator_contract_version") or ""
            ).strip(),
            # Server-owned IDs freeze which captured requirements the design must cover.
            "creator_requirement_ids": requirement_ids,
        }

    @staticmethod
    def _require_creator_quality(tool_name: str, payload: dict[str, Any]) -> None:
        report = evaluate_creator_payload(
            payload,
            requirement_ids=payload.get("creator_requirement_ids") or (),
        )
        if report.ready:
            return
        issue_codes = list(dict.fromkeys(issue.code for issue in report.issues))[:8]
        raise RuntimeToolError(
            tool_name,
            "Creator draft is not complete enough for review: "
            + ", ".join(issue_codes),
            code="skill_creator_draft_incomplete",
        )

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

    @staticmethod
    def _skill_package_schema(*, require_complete: bool) -> dict[str, Any]:
        """Return the explicit Workspace Skill package contract exposed to models."""

        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$",
                },
                "slug": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$",
                },
                "description": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 1024,
                },
                "skill_markdown": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 1_048_576,
                },
                "files": {
                    "type": "object",
                    "maxProperties": 39,
                    "additionalProperties": {
                        "type": "string",
                        "maxLength": 1_048_576,
                    },
                },
            },
            "additionalProperties": False,
        }
        if require_complete:
            schema["required"] = [
                "name",
                "slug",
                "description",
                "skill_markdown",
            ]
        return schema

    @staticmethod
    def _creator_design_schema() -> dict[str, Any]:
        """Design evidence required only by the trusted Skill Creator workflow."""

        design_item = {
            "type": "object",
            "required": ["id", "description"],
            "properties": {
                "id": {"type": "string", "minLength": 1, "maxLength": 80},
                "description": {
                    "type": "string",
                    "minLength": 8,
                    "maxLength": 1000,
                },
            },
            "additionalProperties": False,
        }
        return {
            "type": "object",
            "required": [
                "workflow_steps",
                "output_contract",
                "failure_modes",
                "resources",
                "assumptions",
                "requirement_coverage",
            ],
            "properties": {
                "workflow_steps": {
                    "type": "array",
                    "minItems": 4,
                    "maxItems": 10,
                    "items": design_item,
                },
                "output_contract": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": design_item,
                },
                "failure_modes": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": design_item,
                },
                "resources": {
                    "type": "array",
                    "maxItems": 6,
                    "items": {
                        "type": "object",
                        "required": ["path", "purpose", "used_by_steps"],
                        "properties": {
                            "path": {"type": "string", "minLength": 1},
                            "purpose": {"type": "string", "minLength": 8},
                            "used_by_steps": {
                                "type": "array",
                                "minItems": 1,
                                "items": {"type": "string", "minLength": 1},
                            },
                        },
                        "additionalProperties": False,
                    },
                },
                "assumptions": {
                    "type": "array",
                    "maxItems": 20,
                    "items": {"type": "string", "minLength": 1},
                },
                "requirement_coverage": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": {
                        "type": "object",
                        "required": ["requirement_id", "locations"],
                        "properties": {
                            "requirement_id": {"type": "string", "minLength": 1},
                            "locations": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "required": ["path", "section"],
                                    "properties": {
                                        "path": {"type": "string", "minLength": 1},
                                        "section": {"type": "string", "minLength": 1},
                                    },
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "additionalProperties": False,
                    },
                },
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
