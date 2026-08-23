from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import threading
from dataclasses import asdict
from typing import Any, Protocol

from .creator_quality import (
    CREATOR_CONTRACT_VERSION,
    build_session_requirements,
    evaluate_creator_payload,
)
from .creator_resource_build import (
    MAX_SEGMENT_BYTES,
    HookScriptTestReceipt,
    SkillResourceBuild,
    SkillResourceBuildStore,
)
from .creator_resource_build_runtime import (
    ResourceBuildGenerationRequest,
    ResourceBuildSegment,
    SandboxCreatorScriptRunner,
    validate_final_resource_package,
    validate_resource_content,
)
from .creator_resource_plan import SkillResourcePlan
from .creator_resource_service import SkillCreatorResourcePlanningService
from .creator_service import SkillCreatorService
from .creator_store import (
    SkillCreatorConflictError,
    SkillCreatorSession,
    SkillCreatorValidationError,
)
from .draft_store import WorkspaceSkillDraft
from .hook_contract import (
    DEFAULT_HOOK_TIMEOUT_SECONDS,
    HOOK_MANIFEST_PATH,
    HOOK_MANIFEST_VERSION,
    SkillHookDefinitionV2,
    parse_hook_manifest,
    skill_plugin_hook_v2_enabled,
)

try:
    from server.xpert_runtime.authoring_store import (
        AuthoringProposal,
        AuthoringProposalError,
        AuthoringProposalStore,
    )
except ModuleNotFoundError:
    from xpert_runtime.authoring_store import (
        AuthoringProposal,
        AuthoringProposalError,
        AuthoringProposalStore,
    )


RESOURCE_BUILD_SERVICE_VERSION = "resource-authoring-build-v1"


class ResourceBuilderExecutor(Protocol):
    def available(self) -> bool: ...

    async def generate(self, request: ResourceBuildGenerationRequest) -> ResourceBuildSegment: ...


class SkillCreatorResourceBuildService:
    """Coordinate confirmed plans, segmented generation, review, and proposal creation."""

    VERSION = RESOURCE_BUILD_SERVICE_VERSION

    def __init__(
        self,
        creator_service: SkillCreatorService,
        planning_service: SkillCreatorResourcePlanningService,
        build_store: SkillResourceBuildStore,
        *,
        builder: ResourceBuilderExecutor | None = None,
        script_runner: SandboxCreatorScriptRunner | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.creator_service = creator_service
        self.planning_service = planning_service
        self.build_store = build_store
        self.builder = builder
        self.script_runner = script_runner
        self.enabled = (
            os.getenv("SKILL_CREATOR_RESOURCE_AUTHORING_ENABLED", "true").strip().lower()
            in {"1", "true", "yes", "on"}
            if enabled is None
            else bool(enabled)
        )
        self._locks_guard = threading.RLock()
        self._locks: dict[str, asyncio.Lock] = {}

    def status(self) -> dict[str, Any]:
        try:
            builder_available = bool(self.builder and self.builder.available())
        except Exception:
            builder_available = False
        return {
            "resource_build_version": self.VERSION,
            "resource_build_enabled": self.enabled,
            "resource_builder_available": self.enabled and builder_available,
            "script_sandbox_configured": self.enabled and self.script_runner is not None,
        }

    def require_enabled(self) -> None:
        self.planning_service.require_enabled()
        if not self.enabled:
            raise SkillCreatorValidationError(
                "Skill Creator resource build is disabled.",
                code="skill_creator_resource_authoring_disabled",
            )

    def current_projection(self, session_id: str) -> dict[str, Any] | None:
        self.require_enabled()
        session, draft = self.creator_service.get_session(session_id)
        build = self.build_store.current_for_session(session_id)
        if build is None:
            return None
        stale = self._is_stale(build, session=session, draft=draft)
        if stale and build.state != "stale":
            build = self.build_store.mark_stale(build.build_id)
        result = self.build_store.serialize(build)
        result["stale"] = stale
        return result

    async def start(
        self,
        session_id: str,
        *,
        plan_id: str,
        expected_session_revision: int,
        expected_plan_revision: int,
        expected_plan_digest: str,
    ) -> SkillResourceBuild:
        self.require_enabled()
        async with self._lock(session_id):
            session, draft = self.creator_service.get_session(session_id)
            self._require_session_revision(session, expected_session_revision)
            plan = self.planning_service.plan_store.require(plan_id)
            self._require_plan(plan, session=session, draft=draft, expected_revision=expected_plan_revision, expected_digest=expected_plan_digest)
            if plan.state != "confirmed":
                raise SkillCreatorConflictError("Confirm the resource plan before starting generation.")
            if plan.hooks and not skill_plugin_hook_v2_enabled():
                raise SkillCreatorValidationError(
                    "Skill Hook V2 authoring is disabled.",
                    code="skill_hook_v2_disabled",
                )
            if plan.hooks and self.script_runner is None:
                raise SkillCreatorValidationError(
                    "Hook authoring requires the Skill authoring Sandbox profile.",
                    code="skill_creator_sandbox_unavailable",
                )
            previous_build = self.build_store.current_for_session(session_id)
            if previous_build is not None and (
                previous_build.plan_id != plan.plan_id
                or previous_build.plan_revision != plan.revision
                or previous_build.plan_digest != plan.digest
            ):
                # A newly confirmed immutable plan supersedes every in-flight
                # artifact from the prior plan. Preserve the old build for
                # audit/reuse, but never let it block the replacement build.
                previous_build = self.build_store.mark_stale(previous_build.build_id)
            return self.build_store.create(
                plan=plan,
                existing_files=(draft.files if draft else {}),
                previous_build=previous_build,
            )

    async def next(
        self,
        build_id: str,
        *,
        expected_session_revision: int,
        expected_revision: int,
        expected_digest: str,
    ) -> SkillResourceBuild:
        self.require_enabled()
        initial = self.build_store.require(build_id)
        async with self._lock(initial.session_id):
            if (
                initial.revision != int(expected_revision)
                or initial.digest != str(expected_digest).lower()
            ):
                raise SkillCreatorConflictError(
                    "Resource build changed. Reload it first."
                )
            session, draft = self.creator_service.get_session(initial.session_id)
            self._require_session_revision(session, expected_session_revision)
            self._require_build_scope(initial, session=session, draft=draft)
            if initial.hooks and not skill_plugin_hook_v2_enabled():
                raise SkillCreatorValidationError(
                    "Skill Hook V2 authoring is disabled.",
                    code="skill_hook_v2_disabled",
                )
            if initial.hooks and self.script_runner is None:
                raise SkillCreatorValidationError(
                    "Hook authoring requires the Skill authoring Sandbox profile.",
                    code="skill_creator_sandbox_unavailable",
                )
            builder = self.builder
            try:
                available = bool(builder and builder.available())
            except Exception as exc:
                raise SkillCreatorValidationError("Resource builder status is unavailable.", code="skill_creator_resource_builder_failed") from exc
            if not available or builder is None:
                raise SkillCreatorValidationError("The Skill Creator model gateway is not configured.", code="model_gateway_unconfigured")
            prepared = await self._prepare_hooks_if_ready(initial)
            if prepared.phase == "resources" and prepared.state == "revision_requested" and all(
                item.state == "accepted" for item in prepared.resources
            ):
                return prepared
            current = self.build_store.claim_next(
                build_id,
                expected_revision=prepared.revision,
                expected_digest=prepared.digest,
            )
            validated = await self._generate_and_validate(current, builder=builder)
            # Static/test failures get exactly one server-controlled regeneration.
            if validated.state in {"planned", "revision_requested"}:
                repaired = self.build_store.claim_next(
                    build_id,
                    expected_revision=validated.revision,
                    expected_digest=validated.digest,
                )
                validated = await self._generate_and_validate(repaired, builder=builder)
            return validated

    async def _generate_and_validate(
        self, current: SkillResourceBuild, *, builder: ResourceBuilderExecutor
    ) -> SkillResourceBuild:
        """Assemble one frozen target and turn contract errors into one repair."""

        target_id, _ = self._segment_target(current)
        try:
            for _ in range(3):
                target_id, segment_index = self._segment_target(current)
                try:
                    segment = await builder.generate(
                        ResourceBuildGenerationRequest(
                            build=current,
                            target_id=target_id,
                            segment_index=segment_index,
                        )
                    )
                except asyncio.CancelledError:
                    # Client disconnects and task cancellation must not strand
                    # the immutable target in a permanent generating state.
                    self.build_store.requeue_interrupted(
                        current.build_id,
                        expected_revision=current.revision,
                        expected_digest=current.digest,
                    )
                    raise
                except (SkillCreatorConflictError, SkillCreatorValidationError):
                    raise
                except Exception as exc:
                    self.build_store.requeue_interrupted(
                        current.build_id,
                        expected_revision=current.revision,
                        expected_digest=current.digest,
                    )
                    raise SkillCreatorValidationError(
                        "The Skill Creator resource builder failed.",
                        code="skill_creator_resource_builder_failed",
                    ) from exc
                parts = self._split_utf8_segments(segment.content)
                if segment.segment_index + len(parts) > 3:
                    raise SkillCreatorValidationError(
                        "The generated target exceeded the three-segment limit.",
                        code="skill_creator_resource_segment_limit",
                    )
                for part_offset, part in enumerate(parts):
                    final_part = part_offset == len(parts) - 1
                    current = self.build_store.append_segment(
                        current.build_id,
                        expected_revision=current.revision,
                        expected_digest=current.digest,
                        target_id=segment.target_id,
                        segment_index=segment.segment_index + part_offset,
                        content=part,
                        complete=segment.complete and final_part,
                        script_tests=(segment.script_tests if final_part else []),
                    )
                if segment.complete:
                    return await self._validate_current(current)
            raise SkillCreatorValidationError(
                "The generated target exceeded the three-segment limit.",
                code="skill_creator_resource_segment_limit",
            )
        except SkillCreatorValidationError as exc:
            if exc.code in {
                "model_gateway_unconfigured",
                "skill_creator_resource_builder_failed",
                "skill_creator_sandbox_unavailable",
                "skill_creator_sandbox_profile_invalid",
            }:
                raise
            return self.build_store.record_generation_error(
                current.build_id,
                expected_revision=current.revision,
                expected_digest=current.digest,
                target_id=target_id,
                code=exc.code,
                message=str(exc),
            )

    def review_resource(
        self,
        build_id: str,
        *,
        resource_id: str,
        expected_session_revision: int,
        expected_revision: int,
        expected_digest: str,
        decision: str,
        feedback: str = "",
    ) -> SkillResourceBuild:
        self.require_enabled()
        current = self.build_store.require(build_id)
        session, draft = self.creator_service.get_session(current.session_id)
        self._require_session_revision(session, expected_session_revision)
        self._require_build_scope(current, session=session, draft=draft)
        return self.build_store.review_resource(
            build_id,
            resource_id=resource_id,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            decision=decision,  # type: ignore[arg-type]
            feedback=feedback,
        )

    async def edit_resource(
        self,
        build_id: str,
        *,
        resource_id: str,
        expected_session_revision: int,
        expected_revision: int,
        expected_digest: str,
        content: str,
    ) -> SkillResourceBuild:
        self.require_enabled()
        current = self.build_store.require(build_id)
        async with self._lock(current.session_id):
            session, draft = self.creator_service.get_session(current.session_id)
            self._require_session_revision(session, expected_session_revision)
            self._require_build_scope(current, session=session, draft=draft)
            edited = self.build_store.replace_resource_content(
                build_id,
                resource_id=resource_id,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
                content=content,
            )
            return await self._validate_current(edited, auto_repair=False)

    def finalize(
        self,
        build_id: str,
        *,
        expected_session_revision: int,
        expected_revision: int,
        expected_digest: str,
        decision: str,
        feedback: str = "",
    ) -> tuple[SkillResourceBuild, AuthoringProposal | None]:
        self.require_enabled()
        current = self.build_store.require(build_id)
        session, draft = self.creator_service.get_session(current.session_id)
        self._require_session_revision(session, expected_session_revision)
        self._require_build_scope(current, session=session, draft=draft)
        if current.phase == "proposal":
            if current.proposal_id:
                proposal = self.creator_service.authoring_service.proposal_store.require(
                    current.proposal_id
                )
                return current, proposal
            coverage = self._coverage(session)
            proposal = self._proposal(current, session=session, draft=draft, coverage=coverage)
            recorded = self.build_store.record_proposal(
                build_id,
                expected_revision=current.revision,
                expected_digest=current.digest,
                proposal_id=proposal.proposal_id,
            )
            return recorded, proposal
        if decision == "revise":
            return (
                self.build_store.review_skill_markdown(
                    build_id,
                    expected_revision=expected_revision,
                    expected_digest=expected_digest,
                    decision="revise",
                    feedback=feedback,
                ),
                None,
            )
        if decision != "accept":
            raise SkillCreatorValidationError("Invalid final resource build decision.")
        coverage = self._coverage(session)
        preflight_payload = self._proposal_payload(
            current, session=session, draft=draft, coverage=coverage
        )
        preflight = evaluate_creator_payload(
            preflight_payload,
            requirement_ids=preflight_payload["creator_requirement_ids"],
            resource_build=True,
        )
        if not preflight.ready:
            raise SkillCreatorValidationError(
                "The finalized Skill package did not pass the Creator draft gate: "
                + ", ".join(issue.code for issue in preflight.issues[:8]),
                code="skill_creator_resource_proposal_invalid",
            )
        accepted = self.build_store.review_skill_markdown(
            build_id,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            decision="accept",
            requirement_coverage=coverage,
        )
        proposal = self._proposal(accepted, session=session, draft=draft, coverage=coverage)
        recorded = self.build_store.record_proposal(
            build_id,
            expected_revision=accepted.revision,
            expected_digest=accepted.digest,
            proposal_id=proposal.proposal_id,
        )
        return recorded, proposal

    async def _validate_current(
        self,
        current: SkillResourceBuild,
        *,
        auto_repair: bool = True,
    ) -> SkillResourceBuild:
        if current.state != "awaiting_review":
            return current
        target_id, _ = self._segment_target(current)
        receipt = None
        if current.phase == "resources":
            item = next(item for item in current.resources if item.resource_id == target_id)
            issues = validate_resource_content(item)
            if item.kind == "script" and not issues:
                if self.script_runner is None:
                    issues.append({"code": "skill_creator_sandbox_unavailable", "message": "Script testing requires the Skill authoring Sandbox profile.", "path": item.path, "severity": "error"})
                else:
                    receipt = await self.script_runner.run(item)
                    if not receipt.passed:
                        issues.append({"code": "skill_creator_script_test_failed", "message": "Generated script failed its offline tests.", "path": item.path, "severity": "error"})
                    bound_hooks = [
                        hook
                        for hook in current.hooks
                        if hook.action != "delete"
                        and hook.script_resource_id == item.resource_id
                    ]
                    if receipt.passed and bound_hooks:
                        manifest, manifest_digest = self._hook_manifest(current)
                        for hook in bound_hooks:
                            hook_receipt = await self.script_runner.run_hook(
                                item,
                                hook,
                                manifest_digest=manifest_digest,
                            )
                            if not hook_receipt.passed:
                                issues.append(
                                    {
                                        "code": "skill_creator_hook_test_failed",
                                        "message": "Generated Hook script failed its typed offline contract tests.",
                                        "path": item.path,
                                        "severity": "error",
                                    }
                                )
        else:
            issues = validate_final_resource_package(current)
        return self.build_store.record_validation(
            current.build_id,
            expected_revision=current.revision,
            expected_digest=current.digest,
            target_id=target_id,
            issues=issues,
            script_receipt=receipt,
            auto_repair=auto_repair,
        )

    async def _prepare_hooks_if_ready(
        self, current: SkillResourceBuild
    ) -> SkillResourceBuild:
        if (
            current.phase != "resources"
            or any(item.state != "accepted" for item in current.resources)
            or not self.build_store.requires_hook_validation(current)
        ):
            return current
        if self.script_runner is None:
            raise SkillCreatorValidationError(
                "Hook authoring requires the Skill authoring Sandbox profile.",
                code="skill_creator_sandbox_unavailable",
            )
        manifest, manifest_digest = self._hook_manifest(current)
        resources = {item.resource_id: item for item in current.resources}
        receipts: list[HookScriptTestReceipt] = []
        issues: list[dict[str, Any]] = []
        for hook in current.hooks:
            if hook.action == "delete":
                continue
            script = resources.get(hook.script_resource_id)
            if script is None:
                issues.append(
                    {
                        "code": "skill_creator_hook_script_invalid",
                        "message": "Confirmed Hook script is unavailable.",
                        "path": HOOK_MANIFEST_PATH,
                        "severity": "error",
                    }
                )
                continue
            receipt = await self.script_runner.run_hook(
                script,
                hook,
                manifest_digest=manifest_digest,
            )
            receipts.append(receipt)
            if not receipt.passed:
                issues.append(
                    {
                        "code": "skill_creator_hook_test_failed",
                        "message": "A confirmed Hook did not satisfy the typed offline execution contract.",
                        "path": script.path,
                        "severity": "error",
                    }
                )
        return self.build_store.record_hook_validation(
            current.build_id,
            expected_revision=current.revision,
            expected_digest=current.digest,
            manifest=manifest,
            manifest_digest=manifest_digest,
            receipts=receipts,
            issues=issues,
        )

    @staticmethod
    def _hook_manifest(build: SkillResourceBuild) -> tuple[str, str]:
        resources = {item.resource_id: item for item in build.resources}
        definitions = []
        for hook in build.hooks:
            if hook.action == "delete":
                continue
            script = resources.get(hook.script_resource_id)
            if script is None:
                raise SkillCreatorValidationError(
                    "Confirmed Hook script is unavailable.",
                    code="skill_creator_hook_script_invalid",
                )
            definitions.append(
                SkillHookDefinitionV2(
                    hook_id=hook.hook_id,
                    event=hook.event,
                    mode=hook.mode,
                    tool_names=tuple(hook.tool_names),
                    script_path=script.path,
                    purpose=hook.purpose,
                    acceptance_checks=tuple(hook.acceptance_checks),
                    timeout_seconds=DEFAULT_HOOK_TIMEOUT_SECONDS,
                )
            )
        payload = {
            "version": HOOK_MANIFEST_VERSION,
            "hooks": [item.to_dict() for item in definitions],
        }
        manifest = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
        parse_hook_manifest(
            manifest,
            available_paths=[
                HOOK_MANIFEST_PATH,
                *(item.path for item in build.resources if item.action != "delete"),
            ],
        )
        return manifest, hashlib.sha256(manifest.encode("utf-8")).hexdigest()

    def _proposal(
        self,
        build: SkillResourceBuild,
        *,
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft | None,
        coverage: list[dict[str, Any]],
    ) -> AuthoringProposal:
        proposal_candidates = [
            *self.creator_service.authoring_service.proposal_store.list(
                source_type="skill_creator",
                source_id=session.session_id,
                limit=20,
            ),
            # PR2 previews used the build id as the proposal source. Keep those
            # immutable records discoverable while all new proposals use the
            # Creator session id required by session recovery.
            *self.creator_service.authoring_service.proposal_store.list(
                source_type="skill_creator",
                source_id=build.build_id,
                limit=20,
            ),
        ]
        proposals = [
            item
            for item in {candidate.proposal_id: candidate for candidate in proposal_candidates}.values()
            if isinstance(item.payload.get("creator_resource_build"), dict)
            and item.payload["creator_resource_build"].get("build_id") == build.build_id
        ]
        payload = self._proposal_payload(
            build, session=session, draft=draft, coverage=coverage
        )
        if proposals:
            existing = proposals[0]
            if existing.payload != payload:
                raise SkillCreatorConflictError("The finalized resource proposal changed. Reload the build.")
            return existing
        kind = "skill_update" if draft is not None else "skill_create"
        try:
            proposal = self.creator_service.authoring_service.proposal_store.create(
                kind=kind,
                title=f"Review resource-built Skill: {build.skill_name}",
                payload=payload,
                source_type="skill_creator",
                source_id=session.session_id,
                target_id=(draft.draft_id if draft else None),
                base_revision=(draft.revision if draft else None),
                base_digest=(draft.content_digest if draft else None),
                creator_session_id=session.session_id,
                creator_session_revision=session.session_revision,
                actor_kind="workflow_agent",
                actor_id="skill-creator-assistant-v1",
            )
            proposal = self.creator_service.authoring_service.validate(
                proposal.proposal_id, revision=proposal.revision
            )
        except AuthoringProposalError as exc:
            raise SkillCreatorValidationError(
                "The finalized Skill package could not form a review proposal.",
                code=str(getattr(exc, "code", "skill_creator_resource_proposal_invalid")),
            ) from exc
        if not proposal.validation.get("valid"):
            issue_codes = [str(item.get("code") or "") for item in proposal.validation.get("issues", []) if isinstance(item, dict)]
            raise SkillCreatorValidationError(
                "The finalized Skill package did not pass the Creator draft gate: " + ", ".join(issue_codes[:8]),
                code="skill_creator_resource_proposal_invalid",
            )
        return proposal

    @staticmethod
    def _proposal_payload(
        build: SkillResourceBuild,
        *,
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft | None,
        coverage: list[dict[str, Any]],
    ) -> dict[str, Any]:
        resources = [
            {
                "path": item.path,
                "purpose": item.purpose,
                "used_by_steps": list(item.used_by_steps),
            }
            for item in build.resources
            if item.action != "delete"
        ]
        hooks = [
            {
                "hook_id": item.hook_id,
                "event": item.event,
                "mode": item.mode,
                "tool_names": list(item.tool_names),
                "purpose": item.purpose,
                "script_resource_id": item.script_resource_id,
            }
            for item in build.hooks
            if item.action != "delete"
        ]
        workflow_steps = [
            {"id": str(item.get("step_id") or item.get("id") or f"step_{index + 1}"), "description": str(item.get("instruction") or item.get("description") or "")}
            for index, item in enumerate(build.workflow_steps)
        ]
        package_files = SkillResourceBuildStore.active_files(build)
        if draft is not None:
            package_files.update(
                {
                    path: content
                    for path, content in draft.files.items()
                    if path.startswith("agents/")
                }
            )
        return {
            "skill": {
                "name": build.skill_name,
                "slug": build.skill_name,
                "description": build.skill_description,
                "skill_markdown": build.skill_markdown,
                "files": package_files,
            },
            "design": {
                "workflow_steps": workflow_steps,
                "output_contract": [{"id": f"output_{index + 1}", "description": value} for index, value in enumerate(build.output_contract)],
                "failure_modes": [{"id": f"failure_{index + 1}", "description": value} for index, value in enumerate(build.failure_modes)],
                "resources": resources,
                "hooks": hooks,
                "assumptions": ["Use only the confirmed Creator definition, selected evidence, and accepted resource contents."],
                "requirement_coverage": copy.deepcopy(coverage),
            },
            "creator_contract_version": CREATOR_CONTRACT_VERSION,
            "creator_requirement_ids": [item.requirement_id for item in build_session_requirements(
                intent=session.intent,
                positive_examples=session.positive_examples,
                near_miss_examples=session.near_miss_examples,
                expected_output=session.expected_output,
                success_criteria=session.success_criteria,
            )],
            "creator_resource_build": {
                "version": RESOURCE_BUILD_SERVICE_VERSION,
                "build_id": build.build_id,
                "build_digest": build.digest,
                "plan_id": build.plan_id,
                "plan_digest": build.plan_digest,
                "hook_manifest_digest": build.hook_manifest_digest,
            },
        }

    @staticmethod
    def _coverage(session: SkillCreatorSession) -> list[dict[str, Any]]:
        result = []
        for requirement in build_session_requirements(
            intent=session.intent,
            positive_examples=session.positive_examples,
            near_miss_examples=session.near_miss_examples,
            expected_output=session.expected_output,
            success_criteria=session.success_criteria,
        ):
            section = "Output contract" if requirement.kind == "expected_output" else "Quality checks" if requirement.kind == "success_criterion" else "Purpose and scope"
            result.append({"requirement_id": requirement.requirement_id, "locations": [{"path": "SKILL.md", "section": section}]})
        return result

    @staticmethod
    def _segment_target(build: SkillResourceBuild) -> tuple[str, int]:
        if build.phase == "skill_markdown":
            return "SKILL.md", len(build.skill_chunks)
        target_id = str(build.current_resource_id or "")
        for item in build.resources:
            if item.resource_id == target_id:
                return target_id, len(item.chunks)
        raise SkillCreatorConflictError("Resource build has no active target.")

    @staticmethod
    def _split_utf8_segments(content: str) -> list[str]:
        """Mechanically split one complete model response without changing its bytes."""

        parts: list[str] = []
        current: list[str] = []
        current_bytes = 0
        for character in content:
            character_bytes = len(character.encode("utf-8"))
            if current and current_bytes + character_bytes > MAX_SEGMENT_BYTES:
                parts.append("".join(current))
                current = []
                current_bytes = 0
            current.append(character)
            current_bytes += character_bytes
        if current:
            parts.append("".join(current))
        return parts

    @staticmethod
    def _is_stale(build: SkillResourceBuild, *, session: SkillCreatorSession, draft: WorkspaceSkillDraft | None) -> bool:
        return bool(
            build.session_revision != session.session_revision
            or build.draft_id != (draft.draft_id if draft else None)
            or build.draft_revision != (draft.revision if draft else None)
            or build.draft_digest != (draft.content_digest if draft else None)
        )

    @classmethod
    def _require_build_scope(cls, build: SkillResourceBuild, *, session: SkillCreatorSession, draft: WorkspaceSkillDraft | None) -> None:
        if cls._is_stale(build, session=session, draft=draft):
            raise SkillCreatorConflictError("Resource build no longer matches the current Creator session and draft.")

    @staticmethod
    def _require_plan(
        plan: SkillResourcePlan,
        *,
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft | None,
        expected_revision: int,
        expected_digest: str,
    ) -> None:
        if (
            plan.session_id != session.session_id
            or plan.session_revision != session.session_revision
            or plan.draft_id != (draft.draft_id if draft else None)
            or plan.draft_revision != (draft.revision if draft else None)
            or plan.draft_digest != (draft.content_digest if draft else None)
            or plan.revision != int(expected_revision)
            or plan.digest != str(expected_digest).lower()
        ):
            raise SkillCreatorConflictError("Resource plan changed. Reload it before starting the build.")

    @staticmethod
    def _require_session_revision(session: SkillCreatorSession, expected: int) -> None:
        if session.session_revision != int(expected):
            raise SkillCreatorConflictError("Creator session changed. Reload it before continuing.")

    def _lock(self, session_id: str) -> asyncio.Lock:
        with self._locks_guard:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[session_id] = lock
            return lock


__all__ = [
    "RESOURCE_BUILD_SERVICE_VERSION",
    "ResourceBuilderExecutor",
    "SkillCreatorResourceBuildService",
]
