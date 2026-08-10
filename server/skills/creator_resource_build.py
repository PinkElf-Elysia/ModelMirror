from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Mapping

from .creator_resource_plan import SkillResourcePlan
from .creator_store import (
    SkillCreatorConflictError,
    SkillCreatorNotFoundError,
    SkillCreatorStorageError,
    SkillCreatorValidationError,
)
from .package_validation import scan_skill_package_credentials


RESOURCE_BUILD_VERSION = "skill-resource-build-v1"
ResourceBuildState = Literal[
    "planned",
    "generating",
    "awaiting_review",
    "accepted",
    "revision_requested",
    "failed",
    "stale",
]
ResourceArtifactState = Literal[
    "planned",
    "generating",
    "awaiting_review",
    "accepted",
    "revision_requested",
    "failed",
]
ResourceBuildPhase = Literal["resources", "skill_markdown", "proposal"]

MAX_BUILD_RESOURCES = 20
MAX_SEGMENT_BYTES = 8 * 1024
MAX_RESOURCE_BYTES = 24 * 1024
MAX_SKILL_MARKDOWN_BYTES = 20 * 1024
MAX_PACKAGE_BYTES = 160 * 1024
MAX_SCRIPT_TESTS = 3
MAX_SCRIPT_FIXTURES_PER_TEST = 8
MAX_SCRIPT_FIXTURE_BYTES_TOTAL = 64 * 1024
MAX_BUILD_SCRIPT_FIXTURE_BYTES = 160 * 1024
SKILL_AUTHORING_PROFILE = "skill_authoring_v1"

_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,79}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_PACKAGE_NAMES = {
    "readme.md",
    "installation_guide.md",
    "quick_reference.md",
    "changelog.md",
    "_user_meta.json",
    "user-meta.json",
}


@dataclass(frozen=True, slots=True)
class ResourceScriptFixture:
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class ResourceScriptTest:
    test_id: str
    args: list[str]
    fixtures: list[ResourceScriptFixture]
    expected_exit_code: int
    stdout_contains: list[str]
    stderr_contains: list[str]


@dataclass(frozen=True, slots=True)
class ResourceScriptTestResult:
    test_id: str
    passed: bool
    exit_code: int
    stdout_sha256: str
    stderr_sha256: str
    duration_ms: float
    issues: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ResourceScriptTestReceipt:
    receipt_id: str
    script_digest: str
    profile: str
    passed: bool
    results: list[ResourceScriptTestResult]
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class SkillResourceBuildItem:
    resource_id: str
    spec_digest: str
    kind: Literal["script", "reference", "asset"]
    action: Literal["keep", "create", "update", "delete"]
    path: str
    purpose: str
    source_ids: list[str]
    used_by_steps: list[str]
    depends_on: list[str]
    acceptance_checks: list[str]
    state: ResourceArtifactState
    attempt: int = 1
    repair_count: int = 0
    chunks: list[str] = field(default_factory=list)
    content: str | None = None
    content_digest: str | None = None
    base_content: str | None = None
    base_digest: str | None = None
    script_tests: list[ResourceScriptTest] = field(default_factory=list)
    script_receipt: ResourceScriptTestReceipt | None = None
    validation_issues: list[dict[str, Any]] = field(default_factory=list)
    feedback: str = ""


@dataclass(frozen=True, slots=True)
class SkillResourceBuild:
    build_id: str
    session_id: str
    revision: int
    digest: str
    state: ResourceBuildState
    phase: ResourceBuildPhase
    session_revision: int
    plan_id: str
    plan_revision: int
    plan_digest: str
    draft_id: str | None
    draft_revision: int | None
    draft_digest: str | None
    skill_name: str
    skill_description: str
    workflow_steps: list[dict[str, str]]
    output_contract: list[str]
    failure_modes: list[str]
    resources: list[SkillResourceBuildItem]
    current_resource_id: str | None = None
    skill_chunks: list[str] = field(default_factory=list)
    skill_markdown: str | None = None
    skill_markdown_digest: str | None = None
    skill_attempt: int = 1
    skill_repair_count: int = 0
    skill_validation_issues: list[dict[str, Any]] = field(default_factory=list)
    skill_feedback: str = ""
    requirement_coverage: list[dict[str, Any]] = field(default_factory=list)
    proposal_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class SkillResourceBuildStore:
    """Atomic current-state records for resumable Creator resource builds."""

    SCHEMA_VERSION = 1
    MAX_BUILDS = 300

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        runtime_dir = os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
        self.storage_dir = Path(storage_dir or runtime_dir or package_dir / "storage")
        self.snapshot_path = self.storage_dir / "skill_creator_resource_builds.json"
        self._lock = threading.RLock()
        self._builds: dict[str, SkillResourceBuild] = {}
        self._session_index: dict[str, str] = {}
        self._quarantine: list[dict[str, Any]] = []
        self._load_error: str | None = None
        self._load()

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def current_for_session(self, session_id: str) -> SkillResourceBuild | None:
        clean = self._required_text(session_id, "session_id", 200)
        with self._lock:
            self._ensure_readable_unlocked()
            build_id = self._session_index.get(clean)
            return copy.deepcopy(self._builds.get(build_id)) if build_id else None

    def require(self, build_id: str) -> SkillResourceBuild:
        clean = self._required_text(build_id, "build_id", 200)
        with self._lock:
            self._ensure_readable_unlocked()
            item = self._builds.get(clean)
            if item is None:
                raise SkillCreatorNotFoundError(f"Resource build not found: {clean}")
            return copy.deepcopy(item)

    def create(
        self,
        *,
        plan: SkillResourcePlan,
        existing_files: Mapping[str, str] | None = None,
        previous_build: SkillResourceBuild | None = None,
    ) -> SkillResourceBuild:
        if plan.state != "confirmed":
            raise SkillCreatorValidationError(
                "Only a confirmed resource plan can start a build.",
                code="skill_creator_resource_plan_unconfirmed",
            )
        if len(plan.resources) > MAX_BUILD_RESOURCES:
            raise SkillCreatorValidationError(
                "Resource build contains too many planned resources.",
                code="skill_creator_resource_build_limit",
            )
        with self._lock:
            self._ensure_writable_unlocked()
            current_id = self._session_index.get(plan.session_id)
            current = self._builds.get(current_id) if current_id else None
            if current is not None:
                if (
                    current.plan_id == plan.plan_id
                    and current.plan_revision == plan.revision
                    and current.plan_digest == plan.digest
                ):
                    return copy.deepcopy(current)
                if current.state not in {"accepted", "stale", "failed"}:
                    raise SkillCreatorConflictError(
                        "This Creator session already has an active resource build."
                    )
        files = dict(existing_files or {})
        reusable: dict[str, SkillResourceBuildItem] = {}
        reusable_script_receipts: dict[str, SkillResourceBuildItem] = {}
        if (
            previous_build is not None
            and previous_build.session_id == plan.session_id
            and previous_build.state in {"accepted", "stale", "failed"}
        ):
            reusable_script_receipts = {
                item.path: item
                for item in previous_build.resources
                if item.kind == "script"
                and item.content is not None
                and item.content_digest is not None
                and item.script_receipt is not None
                and item.script_receipt.passed
                and item.script_receipt.script_digest == item.content_digest
            }
        if (
            previous_build is not None
            and previous_build.session_id == plan.session_id
            and previous_build.session_revision == plan.session_revision
            and previous_build.draft_id == plan.draft_id
            and previous_build.draft_revision == plan.draft_revision
            and previous_build.draft_digest == plan.draft_digest
            and previous_build.state in {"accepted", "stale", "failed"}
        ):
            reusable = {
                item.resource_id: item
                for item in previous_build.resources
                if item.state == "accepted" and item.content is not None
            }
        resources: list[SkillResourceBuildItem] = []
        for planned in plan.resources:
            base_content = files.get(planned.path)
            if planned.action in {"keep", "update", "delete"} and base_content is None:
                raise SkillCreatorConflictError(
                    f"Planned existing resource is missing: {planned.path}"
                )
            if planned.action == "create" and base_content is not None:
                raise SkillCreatorConflictError(
                    f"Planned new resource already exists: {planned.path}"
                )
            if base_content is not None:
                self._validate_resource_bytes(planned.path, base_content)
            previous = reusable.get(planned.resource_id)
            may_reuse = bool(
                previous is not None
                and previous.spec_digest == planned.spec_digest
                and previous.kind == planned.kind
                and previous.action == planned.action
                and previous.path == planned.path
            )
            state: ResourceArtifactState = "accepted" if planned.action in {"keep", "delete"} or may_reuse else "planned"
            active_content = (
                previous.content
                if may_reuse and previous is not None
                else (base_content if planned.action == "keep" else None)
            )
            active_digest = self._sha256(active_content) if active_content is not None else None
            reused_tests = list(previous.script_tests) if may_reuse and previous else []
            reused_receipt = previous.script_receipt if may_reuse and previous else None
            if planned.action == "keep" and planned.kind == "script":
                receipt_source = reusable_script_receipts.get(planned.path)
                if (
                    receipt_source is not None
                    and receipt_source.content_digest == active_digest
                ):
                    reused_tests = list(receipt_source.script_tests)
                    reused_receipt = receipt_source.script_receipt
                if (
                    reused_receipt is None
                    or not reused_receipt.passed
                    or reused_receipt.script_digest != active_digest
                ):
                    raise SkillCreatorValidationError(
                        "A kept script requires a reusable digest-bound test receipt; plan it as update to retest.",
                        code="skill_creator_script_receipt_required",
                    )
            resources.append(
                SkillResourceBuildItem(
                    resource_id=planned.resource_id,
                    spec_digest=planned.spec_digest,
                    kind=planned.kind,
                    action=planned.action,
                    path=planned.path,
                    purpose=planned.purpose,
                    source_ids=list(planned.source_ids),
                    used_by_steps=list(planned.used_by_steps),
                    depends_on=list(planned.depends_on),
                    acceptance_checks=list(planned.acceptance_checks),
                    state=state,
                    content=active_content,
                    content_digest=active_digest,
                    base_content=base_content,
                    base_digest=(self._sha256(base_content) if base_content is not None else None),
                    script_tests=reused_tests,
                    script_receipt=reused_receipt,
                )
            )
        self._ensure_package_budget(resources, skill_markdown=None)
        with self._lock:
            self._ensure_writable_unlocked()
            current_id = self._session_index.get(plan.session_id)
            current = self._builds.get(current_id) if current_id else None
            if current is not None:
                if (
                    current.plan_id == plan.plan_id
                    and current.plan_revision == plan.revision
                    and current.plan_digest == plan.digest
                ):
                    return copy.deepcopy(current)
                if current.state not in {"accepted", "stale", "failed"}:
                    raise SkillCreatorConflictError(
                        "This Creator session already has an active resource build."
                    )
            if len(self._builds) >= self.MAX_BUILDS:
                raise SkillCreatorValidationError(
                    "Skill Creator resource build limit reached.",
                    code="skill_creator_resource_build_limit",
                )
            now = time.time()
            build = self._build(
                build_id=f"skillbuild_{uuid.uuid4().hex}",
                session_id=plan.session_id,
                revision=1,
                state="planned",
                phase="resources" if self._has_pending_resources(resources) else "skill_markdown",
                session_revision=plan.session_revision,
                plan_id=plan.plan_id,
                plan_revision=plan.revision,
                plan_digest=plan.digest,
                draft_id=plan.draft_id,
                draft_revision=plan.draft_revision,
                draft_digest=plan.draft_digest,
                skill_name=plan.skill_name,
                skill_description=plan.skill_description,
                workflow_steps=[asdict(item) for item in plan.workflow_steps],
                output_contract=list(plan.output_contract),
                failure_modes=list(plan.failure_modes),
                resources=resources,
                created_at=now,
                updated_at=now,
            )
            return self._publish_unlocked(build)

    def claim_next(
        self, build_id: str, *, expected_revision: int, expected_digest: str
    ) -> SkillResourceBuild:
        with self._lock:
            current = self._require_match_unlocked(
                build_id, expected_revision=expected_revision, expected_digest=expected_digest
            )
            if current.state == "awaiting_review":
                raise SkillCreatorConflictError(
                    "The assembled resource must be reviewed before generation continues."
                )
            if current.state in {"accepted", "stale"}:
                raise SkillCreatorConflictError("This resource build cannot continue.")
            if current.state == "generating":
                return copy.deepcopy(current)
            values = asdict(current)
            values["revision"] = current.revision + 1
            values["updated_at"] = time.time()
            if current.phase == "resources":
                selected = self._next_resource(current.resources)
                if selected is None:
                    if self._has_pending_resources(current.resources):
                        raise SkillCreatorConflictError(
                            "Resource dependencies are not ready for generation."
                        )
                    values["phase"] = "skill_markdown"
                    values["state"] = "generating"
                    values["current_resource_id"] = None
                else:
                    values["state"] = "generating"
                    values["current_resource_id"] = selected.resource_id
                    for resource in values["resources"]:
                        if resource["resource_id"] == selected.resource_id:
                            resource["state"] = "generating"
                            break
            elif current.phase == "skill_markdown":
                values["state"] = "generating"
                values["current_resource_id"] = None
            else:
                raise SkillCreatorConflictError("The final package is already assembled.")
            return self._publish_unlocked(self._decode(values, verify_digest=False))

    def append_segment(
        self,
        build_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        target_id: str,
        segment_index: int,
        content: str,
        complete: bool,
        script_tests: list[Mapping[str, Any]] | None = None,
    ) -> SkillResourceBuild:
        segment = str(content or "")
        if not segment:
            raise SkillCreatorValidationError(
                "Generated resource segment is empty.",
                code="skill_creator_resource_segment_invalid",
            )
        if len(segment.encode("utf-8")) > MAX_SEGMENT_BYTES:
            raise SkillCreatorValidationError(
                "Generated resource segment exceeds 8 KiB.",
                code="skill_creator_resource_segment_too_large",
            )
        with self._lock:
            current = self._require_match_unlocked(
                build_id, expected_revision=expected_revision, expected_digest=expected_digest
            )
            if current.state != "generating":
                raise SkillCreatorConflictError("Resource build is not generating a segment.")
            values = asdict(current)
            if current.phase == "resources":
                if target_id != current.current_resource_id:
                    raise SkillCreatorConflictError("Generated segment target changed.")
                resource = self._resource_dict(values, target_id)
                chunks = list(resource["chunks"])
                if int(segment_index) != len(chunks):
                    raise SkillCreatorConflictError("Generated resource segments are out of order.")
                self._reject_credentials(path=resource["path"], content=segment)
                chunks.append(segment)
                assembled = "".join(chunks)
                self._validate_resource_bytes(resource["path"], assembled)
                resource["chunks"] = chunks
                if complete:
                    resource["content"] = assembled
                    resource["content_digest"] = self._sha256(assembled)
                    resource["script_tests"] = [
                        asdict(item) for item in self._normalize_script_tests(
                            script_tests or [], required=resource["kind"] == "script"
                        )
                    ]
                    resource["script_receipt"] = None
                    resource["state"] = "awaiting_review"
                    values["state"] = "awaiting_review"
                else:
                    values["state"] = "generating"
            elif current.phase == "skill_markdown":
                if target_id != "SKILL.md":
                    raise SkillCreatorConflictError("Generated SKILL.md target changed.")
                chunks = list(values["skill_chunks"])
                if int(segment_index) != len(chunks):
                    raise SkillCreatorConflictError("Generated SKILL.md segments are out of order.")
                self._reject_credentials(path="SKILL.md", content=segment)
                chunks.append(segment)
                assembled = "".join(chunks)
                if len(assembled.encode("utf-8")) > MAX_SKILL_MARKDOWN_BYTES:
                    raise SkillCreatorValidationError(
                        "Generated SKILL.md exceeds 20 KiB.",
                        code="skill_creator_skill_markdown_too_large",
                    )
                values["skill_chunks"] = chunks
                if complete:
                    values["skill_markdown"] = assembled
                    values["skill_markdown_digest"] = self._sha256(assembled)
                    values["state"] = "awaiting_review"
                else:
                    values["state"] = "generating"
            else:
                raise SkillCreatorConflictError("The resource build is already finalized.")
            values["revision"] = current.revision + 1
            values["updated_at"] = time.time()
            candidate = self._decode(values, verify_digest=False)
            self._ensure_package_budget(candidate.resources, candidate.skill_markdown)
            return self._publish_unlocked(candidate)

    def record_validation(
        self,
        build_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        target_id: str,
        issues: list[Mapping[str, Any]],
        script_receipt: ResourceScriptTestReceipt | None = None,
    ) -> SkillResourceBuild:
        clean_issues = self._issues(issues)
        with self._lock:
            current = self._require_match_unlocked(
                build_id, expected_revision=expected_revision, expected_digest=expected_digest
            )
            if current.state != "awaiting_review":
                raise SkillCreatorConflictError("Generated content is not awaiting validation.")
            values = asdict(current)
            valid = not clean_issues and (
                script_receipt is None or script_receipt.passed
            )
            if current.phase == "resources":
                if target_id != current.current_resource_id:
                    raise SkillCreatorConflictError("Resource validation target changed.")
                resource = self._resource_dict(values, target_id)
                resource["validation_issues"] = clean_issues
                resource["script_receipt"] = (
                    asdict(script_receipt) if script_receipt is not None else None
                )
                if valid:
                    resource["state"] = "awaiting_review"
                    values["state"] = "awaiting_review"
                elif int(resource["repair_count"]) < 1:
                    resource["repair_count"] = int(resource["repair_count"]) + 1
                    resource["attempt"] = int(resource["attempt"]) + 1
                    resource["chunks"] = []
                    resource["content"] = None
                    resource["content_digest"] = None
                    resource["script_tests"] = []
                    resource["script_receipt"] = None
                    resource["state"] = "planned"
                    values["state"] = "planned"
                    values["current_resource_id"] = None
                else:
                    resource["state"] = "failed"
                    values["state"] = "failed"
            elif current.phase == "skill_markdown":
                if target_id != "SKILL.md":
                    raise SkillCreatorConflictError("SKILL.md validation target changed.")
                values["skill_validation_issues"] = clean_issues
                if valid:
                    values["state"] = "awaiting_review"
                elif int(values["skill_repair_count"]) < 1:
                    values["skill_repair_count"] = int(values["skill_repair_count"]) + 1
                    values["skill_attempt"] = int(values["skill_attempt"]) + 1
                    values["skill_chunks"] = []
                    values["skill_markdown"] = None
                    values["skill_markdown_digest"] = None
                    values["state"] = "planned"
                else:
                    values["state"] = "failed"
            else:
                raise SkillCreatorConflictError("The resource build is already finalized.")
            values["revision"] = current.revision + 1
            values["updated_at"] = time.time()
            return self._publish_unlocked(self._decode(values, verify_digest=False))

    def record_generation_error(
        self,
        build_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        target_id: str,
        code: str,
        message: str,
    ) -> SkillResourceBuild:
        """Persist a model-contract failure and consume the one internal repair."""

        with self._lock:
            current = self._require_match_unlocked(
                build_id,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
            )
            if current.state != "generating":
                raise SkillCreatorConflictError("Resource build is not generating content.")
            values = asdict(current)
            issue_path = "SKILL.md"
            if current.phase == "resources":
                issue_path = self._resource_dict(values, target_id)["path"]
            issue = self._issues(
                [
                    {
                        "code": str(
                            code or "skill_creator_resource_builder_invalid"
                        )[:120],
                        "message": str(
                            message
                            or "Generated resource did not satisfy the frozen contract."
                        )[:500],
                        "path": issue_path,
                        "severity": "error",
                    }
                ]
            )
            if current.phase == "resources":
                if target_id != current.current_resource_id:
                    raise SkillCreatorConflictError("Generated resource target changed.")
                resource = self._resource_dict(values, target_id)
                resource["validation_issues"] = issue
                if int(resource["repair_count"]) < 1:
                    resource["repair_count"] = int(resource["repair_count"]) + 1
                    resource["attempt"] = int(resource["attempt"]) + 1
                    resource["chunks"] = []
                    resource["content"] = None
                    resource["content_digest"] = None
                    resource["script_tests"] = []
                    resource["script_receipt"] = None
                    resource["state"] = "planned"
                    values["state"] = "planned"
                    values["current_resource_id"] = None
                else:
                    resource["state"] = "failed"
                    values["state"] = "failed"
            elif current.phase == "skill_markdown":
                if target_id != "SKILL.md":
                    raise SkillCreatorConflictError("Generated SKILL.md target changed.")
                values["skill_validation_issues"] = issue
                if int(values["skill_repair_count"]) < 1:
                    values["skill_repair_count"] = int(values["skill_repair_count"]) + 1
                    values["skill_attempt"] = int(values["skill_attempt"]) + 1
                    values["skill_chunks"] = []
                    values["skill_markdown"] = None
                    values["skill_markdown_digest"] = None
                    values["state"] = "planned"
                else:
                    values["state"] = "failed"
            else:
                raise SkillCreatorConflictError("The resource build is already finalized.")
            values["revision"] = current.revision + 1
            values["updated_at"] = time.time()
            return self._publish_unlocked(self._decode(values, verify_digest=False))

    def review_resource(
        self,
        build_id: str,
        *,
        resource_id: str,
        expected_revision: int,
        expected_digest: str,
        decision: Literal["accept", "revise"],
        feedback: str = "",
    ) -> SkillResourceBuild:
        if decision not in {"accept", "revise"}:
            raise SkillCreatorValidationError("Invalid resource review decision.")
        clean_feedback = str(feedback or "").strip()
        if decision == "revise" and not clean_feedback:
            raise SkillCreatorValidationError(
                "Revision feedback is required.",
                code="skill_creator_resource_feedback_required",
            )
        if clean_feedback:
            self._reject_credentials(path="feedback", content=clean_feedback)
        with self._lock:
            current = self._require_match_unlocked(
                build_id, expected_revision=expected_revision, expected_digest=expected_digest
            )
            if current.phase != "resources" or current.current_resource_id != resource_id:
                raise SkillCreatorConflictError("Resource review target changed.")
            values = asdict(current)
            resource = self._resource_dict(values, resource_id)
            if resource["state"] not in {"awaiting_review", "failed"}:
                raise SkillCreatorConflictError("Resource is not ready for review.")
            if decision == "accept":
                if resource["validation_issues"]:
                    raise SkillCreatorValidationError(
                        "A resource with validation failures cannot be accepted."
                    )
                receipt = resource.get("script_receipt")
                if resource["kind"] == "script" and (
                    not isinstance(receipt, dict)
                    or not receipt.get("passed")
                    or receipt.get("script_digest") != resource.get("content_digest")
                ):
                    raise SkillCreatorValidationError(
                        "A generated script requires a passing digest-bound test receipt.",
                        code="skill_creator_script_receipt_required",
                    )
                resource["state"] = "accepted"
                resource["feedback"] = ""
                values["current_resource_id"] = None
                if self._has_pending_resource_dicts(values["resources"]):
                    values["state"] = "planned"
                else:
                    values["phase"] = "skill_markdown"
                    values["state"] = "planned"
            else:
                resource["attempt"] = int(resource["attempt"]) + 1
                resource["repair_count"] = 0
                resource["chunks"] = []
                resource["content"] = None
                resource["content_digest"] = None
                resource["script_tests"] = []
                resource["script_receipt"] = None
                resource["validation_issues"] = []
                resource["feedback"] = clean_feedback[:4_000]
                resource["state"] = "revision_requested"
                values["current_resource_id"] = None
                values["state"] = "revision_requested"
            values["revision"] = current.revision + 1
            values["updated_at"] = time.time()
            return self._publish_unlocked(self._decode(values, verify_digest=False))

    def review_skill_markdown(
        self,
        build_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        decision: Literal["accept", "revise"],
        feedback: str = "",
        requirement_coverage: list[Mapping[str, Any]] | None = None,
    ) -> SkillResourceBuild:
        if decision not in {"accept", "revise"}:
            raise SkillCreatorValidationError("Invalid SKILL.md review decision.")
        clean_feedback = str(feedback or "").strip()
        if decision == "revise" and not clean_feedback:
            raise SkillCreatorValidationError(
                "SKILL.md revision feedback is required.",
                code="skill_creator_resource_feedback_required",
            )
        if clean_feedback:
            self._reject_credentials(path="feedback", content=clean_feedback)
        with self._lock:
            current = self._require_match_unlocked(
                build_id, expected_revision=expected_revision, expected_digest=expected_digest
            )
            if current.phase != "skill_markdown" or current.state not in {
                "awaiting_review", "failed"
            }:
                raise SkillCreatorConflictError("SKILL.md is not ready for review.")
            values = asdict(current)
            if decision == "accept":
                if current.skill_validation_issues:
                    raise SkillCreatorValidationError(
                        "SKILL.md with validation failures cannot be accepted."
                    )
                values["state"] = "accepted"
                values["phase"] = "proposal"
                values["requirement_coverage"] = copy.deepcopy(
                    list(requirement_coverage or current.requirement_coverage)
                )
                values["skill_feedback"] = ""
            else:
                prior_feedback = current.skill_feedback.strip()
                cumulative_feedback = (
                    f"{prior_feedback}\n\nAdditional revision requirements:\n{clean_feedback}"
                    if prior_feedback and clean_feedback not in prior_feedback
                    else (prior_feedback or clean_feedback)
                )
                if len(cumulative_feedback) > 12_000:
                    raise SkillCreatorValidationError(
                        "Cumulative SKILL.md revision feedback is too large.",
                        code="skill_creator_resource_feedback_too_large",
                    )
                values["skill_attempt"] = current.skill_attempt + 1
                values["skill_repair_count"] = 0
                values["skill_chunks"] = []
                values["skill_markdown"] = None
                values["skill_markdown_digest"] = None
                values["skill_validation_issues"] = []
                values["skill_feedback"] = cumulative_feedback
                values["state"] = "revision_requested"
            values["revision"] = current.revision + 1
            values["updated_at"] = time.time()
            return self._publish_unlocked(self._decode(values, verify_digest=False))

    def record_proposal(
        self,
        build_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        proposal_id: str,
    ) -> SkillResourceBuild:
        with self._lock:
            current = self._require_match_unlocked(
                build_id, expected_revision=expected_revision, expected_digest=expected_digest
            )
            if current.phase != "proposal" or current.state != "accepted":
                raise SkillCreatorConflictError("Final package has not been accepted.")
            if current.proposal_id:
                if current.proposal_id != proposal_id:
                    raise SkillCreatorConflictError("Resource build already created another proposal.")
                return copy.deepcopy(current)
            values = asdict(current)
            values["proposal_id"] = self._required_text(proposal_id, "proposal_id", 200)
            values["revision"] = current.revision + 1
            values["updated_at"] = time.time()
            return self._publish_unlocked(self._decode(values, verify_digest=False))

    def mark_stale(self, build_id: str) -> SkillResourceBuild:
        with self._lock:
            current = self._require_unlocked(build_id)
            if current.state == "stale":
                return copy.deepcopy(current)
            values = asdict(current)
            values["state"] = "stale"
            values["revision"] = current.revision + 1
            values["updated_at"] = time.time()
            return self._publish_unlocked(self._decode(values, verify_digest=False))

    def recover_interrupted(self) -> list[str]:
        recovered: list[str] = []
        with self._lock:
            self._ensure_writable_unlocked()
            previous = copy.deepcopy(self._builds)
            for build_id, current in list(self._builds.items()):
                if current.state != "generating":
                    continue
                values = asdict(current)
                values["revision"] = current.revision + 1
                values["state"] = "planned"
                values["updated_at"] = time.time()
                if current.phase == "resources" and current.current_resource_id:
                    resource = self._resource_dict(values, current.current_resource_id)
                    if resource["state"] == "generating":
                        resource["state"] = "planned"
                        resource["chunks"] = []
                        resource["content"] = None
                        resource["content_digest"] = None
                        resource["script_tests"] = []
                        resource["script_receipt"] = None
                    values["current_resource_id"] = None
                elif current.phase == "skill_markdown":
                    values["skill_chunks"] = []
                    values["skill_markdown"] = None
                    values["skill_markdown_digest"] = None
                self._builds[build_id] = self._decode(values, verify_digest=False)
                recovered.append(build_id)
            if recovered:
                try:
                    self._save_unlocked()
                except BaseException:
                    self._builds = previous
                    raise
        return recovered

    def requeue_interrupted(
        self,
        build_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
    ) -> SkillResourceBuild:
        """Release one transiently failed generation claim without consuming repair budget."""

        with self._lock:
            current = self._require_match_unlocked(
                build_id,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
            )
            if current.state != "generating":
                return copy.deepcopy(current)
            values = asdict(current)
            values["revision"] = current.revision + 1
            values["state"] = "planned"
            values["updated_at"] = time.time()
            if current.phase == "resources" and current.current_resource_id:
                resource = self._resource_dict(values, current.current_resource_id)
                if resource["state"] == "generating":
                    resource["state"] = "planned"
                    resource["chunks"] = []
                    resource["content"] = None
                    resource["content_digest"] = None
                    resource["script_tests"] = []
                    resource["script_receipt"] = None
                values["current_resource_id"] = None
            elif current.phase == "skill_markdown":
                values["skill_chunks"] = []
                values["skill_markdown"] = None
                values["skill_markdown_digest"] = None
            return self._publish_unlocked(self._decode(values, verify_digest=False))

    @staticmethod
    def serialize(item: SkillResourceBuild) -> dict[str, Any]:
        return asdict(item)

    @staticmethod
    def active_files(item: SkillResourceBuild) -> dict[str, str]:
        result: dict[str, str] = {}
        for resource in item.resources:
            if resource.action == "delete":
                continue
            if resource.state != "accepted" or resource.content is None:
                raise SkillCreatorConflictError("Resource package is not fully accepted.")
            result[resource.path] = resource.content
        return result

    @staticmethod
    def _next_resource(resources: list[SkillResourceBuildItem]) -> SkillResourceBuildItem | None:
        accepted = {item.resource_id for item in resources if item.state == "accepted"}
        for item in resources:
            if item.state in {"planned", "revision_requested"} and set(item.depends_on) <= accepted:
                return item
        return None

    @staticmethod
    def _has_pending_resources(resources: list[SkillResourceBuildItem]) -> bool:
        return any(item.state != "accepted" for item in resources)

    @staticmethod
    def _has_pending_resource_dicts(resources: list[dict[str, Any]]) -> bool:
        return any(item.get("state") != "accepted" for item in resources)

    def _require_match_unlocked(
        self, build_id: str, *, expected_revision: int, expected_digest: str
    ) -> SkillResourceBuild:
        self._ensure_writable_unlocked()
        current = self._require_unlocked(build_id)
        if (
            current.revision != int(expected_revision)
            or current.digest != self._digest(expected_digest, "expected_digest")
        ):
            raise SkillCreatorConflictError("Resource build changed. Reload it first.")
        return current

    def _require_unlocked(self, build_id: str) -> SkillResourceBuild:
        clean = self._required_text(build_id, "build_id", 200)
        current = self._builds.get(clean)
        if current is None:
            raise SkillCreatorNotFoundError(f"Resource build not found: {clean}")
        return current

    @staticmethod
    def _resource_dict(values: dict[str, Any], resource_id: str) -> dict[str, Any]:
        for resource in values["resources"]:
            if resource["resource_id"] == resource_id:
                return resource
        raise SkillCreatorNotFoundError(f"Resource not found in build: {resource_id}")

    def _publish_unlocked(self, item: SkillResourceBuild) -> SkillResourceBuild:
        self._ensure_writable_unlocked()
        previous = copy.deepcopy(self._builds)
        previous_index = dict(self._session_index)
        self._builds[item.build_id] = item
        self._session_index[item.session_id] = item.build_id
        try:
            self._save_unlocked()
        except BaseException:
            self._builds = previous
            self._session_index = previous_index
            raise
        return copy.deepcopy(item)

    def _build(self, **values: Any) -> SkillResourceBuild:
        item = SkillResourceBuild(digest="", **values)
        payload = asdict(item)
        payload.pop("digest", None)
        digest = self._sha256(self._canonical_json(payload))
        return replace(item, digest=digest)

    def _decode(self, raw: Mapping[str, Any], *, verify_digest: bool = True) -> SkillResourceBuild:
        values = copy.deepcopy(dict(raw))
        resources: list[SkillResourceBuildItem] = []
        for record in list(values.get("resources") or []):
            tests = [
                ResourceScriptTest(
                    **{
                        **test,
                        "fixtures": [ResourceScriptFixture(**item) for item in test.get("fixtures", [])],
                    }
                )
                for test in record.get("script_tests", [])
            ]
            receipt_raw = record.get("script_receipt")
            receipt = None
            if isinstance(receipt_raw, dict):
                receipt = ResourceScriptTestReceipt(
                    **{
                        **receipt_raw,
                        "results": [ResourceScriptTestResult(**item) for item in receipt_raw.get("results", [])],
                    }
                )
            resources.append(
                SkillResourceBuildItem(
                    **{
                        **record,
                        "script_tests": tests,
                        "script_receipt": receipt,
                    }
                )
            )
        supplied_digest = values.pop("digest", None)
        values["resources"] = resources
        item = self._build(**values)
        if verify_digest and supplied_digest != item.digest:
            raise ValueError("resource build digest mismatch")
        self._validate_loaded(item)
        return item

    def _validate_loaded(self, item: SkillResourceBuild) -> None:
        if item.state not in {
            "planned", "generating", "awaiting_review", "accepted",
            "revision_requested", "failed", "stale",
        } or item.phase not in {"resources", "skill_markdown", "proposal"}:
            raise ValueError("invalid resource build state")
        if item.revision < 1 or len(item.resources) > MAX_BUILD_RESOURCES:
            raise ValueError("invalid resource build revision")
        self._digest(item.plan_digest, "plan_digest")
        if item.draft_digest is not None:
            self._digest(item.draft_digest, "draft_digest")
        resource_ids = {resource.resource_id for resource in item.resources}
        if len(resource_ids) != len(item.resources):
            raise ValueError("duplicate resource build item id")
        resource_paths: set[str] = set()
        for resource in item.resources:
            if resource.kind not in {"script", "reference", "asset"}:
                raise ValueError("invalid resource kind")
            if resource.action not in {"keep", "create", "update", "delete"}:
                raise ValueError("invalid resource action")
            if resource.state not in {
                "planned", "generating", "awaiting_review", "accepted",
                "revision_requested", "failed",
            }:
                raise ValueError("invalid resource state")
            self._validate_resource_path(resource.kind, resource.path)
            folded_path = resource.path.casefold()
            if folded_path in resource_paths:
                raise ValueError("duplicate resource build path")
            resource_paths.add(folded_path)
            if resource.resource_id in resource.depends_on or any(
                dependency not in resource_ids for dependency in resource.depends_on
            ):
                raise ValueError("invalid resource dependency")
            self._digest(resource.spec_digest, "spec_digest")
            if resource.content is not None:
                self._validate_resource_bytes(resource.path, resource.content)
                if resource.content_digest != self._sha256(resource.content):
                    raise ValueError("resource content digest mismatch")
            elif resource.state == "accepted" and resource.action != "delete":
                raise ValueError("accepted resource content is missing")
            if resource.kind != "script" and (
                resource.script_tests or resource.script_receipt is not None
            ):
                raise ValueError("non-script resource contains script test state")
            normalized_tests = self._normalize_script_tests(
                [asdict(test) for test in resource.script_tests],
                required=resource.kind == "script" and resource.content is not None,
            )
            if normalized_tests != resource.script_tests:
                raise ValueError("script tests are not canonical")
            if resource.script_receipt is not None:
                receipt = resource.script_receipt
                expected_passed = bool(receipt.results) and all(
                    result.passed for result in receipt.results
                )
                if (
                    resource.kind != "script"
                    or receipt.profile != SKILL_AUTHORING_PROFILE
                    or receipt.script_digest != resource.content_digest
                    or receipt.passed != expected_passed
                    or {result.test_id for result in receipt.results}
                    != {test.test_id for test in resource.script_tests}
                ):
                    raise ValueError("script receipt digest mismatch")
        unresolved = {
            resource.resource_id: set(resource.depends_on)
            for resource in item.resources
        }
        while unresolved:
            ready = {
                resource_id
                for resource_id, dependencies in unresolved.items()
                if not (dependencies & unresolved.keys())
            }
            if not ready:
                raise ValueError("resource dependency cycle")
            for resource_id in ready:
                unresolved.pop(resource_id)
        if item.current_resource_id is not None and item.current_resource_id not in resource_ids:
            raise ValueError("current resource id is missing")
        if item.phase == "resources":
            if item.state in {"generating", "awaiting_review", "failed"} and item.current_resource_id is None:
                raise ValueError("active resource target is missing")
            if item.state in {"planned", "revision_requested"} and item.current_resource_id is not None:
                raise ValueError("inactive resource target is set")
        else:
            if item.current_resource_id is not None:
                raise ValueError("non-resource phase has a resource target")
            if any(resource.state != "accepted" for resource in item.resources):
                raise ValueError("final phases require accepted resources")
        if item.phase == "proposal" and (
            item.state not in {"accepted", "stale"} or item.skill_markdown is None
        ):
            raise ValueError("proposal phase is incomplete")
        build_fixture_bytes = sum(
            len(fixture.content.encode("utf-8"))
            for resource in item.resources
            for test in resource.script_tests
            for fixture in test.fixtures
        )
        if build_fixture_bytes > MAX_BUILD_SCRIPT_FIXTURE_BYTES:
            raise ValueError("resource build script fixtures exceed 160 KiB")
        self._ensure_package_budget(item.resources, item.skill_markdown)
        self._reject_credentials(
            path="SKILL.md",
            content=item.skill_markdown or "",
            files={
                resource.path: resource.content
                for resource in item.resources
                if resource.content is not None
            },
        )

    def _normalize_script_tests(
        self, value: list[Mapping[str, Any]], *, required: bool
    ) -> list[ResourceScriptTest]:
        if not isinstance(value, list) or len(value) > MAX_SCRIPT_TESTS:
            raise SkillCreatorValidationError("A script may define at most three offline tests.")
        if required and not value:
            raise SkillCreatorValidationError(
                "Generated scripts require one to three offline tests.",
                code="skill_creator_script_tests_required",
            )
        result: list[ResourceScriptTest] = []
        seen: set[str] = set()
        fixture_bytes_total = 0
        for index, raw in enumerate(value):
            if not isinstance(raw, Mapping):
                raise SkillCreatorValidationError("Invalid script test case.")
            test_id = str(raw.get("test_id") or f"case_{index + 1}").strip()
            if not _IDENTIFIER_RE.fullmatch(test_id) or test_id in seen:
                raise SkillCreatorValidationError("Script test IDs must be unique identifiers.")
            seen.add(test_id)
            raw_args = raw.get("args", [])
            if not isinstance(raw_args, list):
                raise SkillCreatorValidationError(
                    "Script test args must be a JSON array of strings.",
                    code="skill_creator_script_test_contract_invalid",
                )
            args = [str(item) for item in raw_args]
            if len(args) > 16 or any(not item or len(item) > 500 or "\x00" in item or "\n" in item for item in args):
                raise SkillCreatorValidationError("Script test arguments are invalid.")
            for argument_index, argument in enumerate(args):
                self._reject_credentials(
                    path=f"script-tests/{test_id}/arg-{argument_index}.txt",
                    content=argument,
                )
            fixtures: list[ResourceScriptFixture] = []
            raw_fixtures = raw.get("fixtures", [])
            if (
                not isinstance(raw_fixtures, list)
                or len(raw_fixtures) > MAX_SCRIPT_FIXTURES_PER_TEST
            ):
                raise SkillCreatorValidationError(
                    "Script test fixtures must be a JSON array with at most eight objects.",
                    code="skill_creator_script_test_contract_invalid",
                )
            fixture_paths: set[str] = set()
            for fixture in raw_fixtures:
                if not isinstance(fixture, Mapping):
                    raise SkillCreatorValidationError("Invalid script test fixture.")
                path = self._relative_path(fixture.get("path"), root="inputs")
                if path.casefold() in fixture_paths:
                    raise SkillCreatorValidationError("Script test fixture paths must be unique.")
                fixture_paths.add(path.casefold())
                raw_content = fixture.get("content")
                if not isinstance(raw_content, str):
                    raise SkillCreatorValidationError("Script test fixture content must be UTF-8 text.")
                content = raw_content
                content_bytes = len(content.encode("utf-8"))
                fixture_bytes_total += content_bytes
                if content_bytes > MAX_RESOURCE_BYTES:
                    raise SkillCreatorValidationError("Script test fixture exceeds 24 KiB.")
                if fixture_bytes_total > MAX_SCRIPT_FIXTURE_BYTES_TOTAL:
                    raise SkillCreatorValidationError("Script test fixtures exceed 64 KiB in total.")
                self._reject_credentials(path=path, content=content)
                fixtures.append(ResourceScriptFixture(path=path, content=content))
            try:
                expected_exit_code = int(raw.get("expected_exit_code", 0))
            except (TypeError, ValueError, OverflowError) as exc:
                raise SkillCreatorValidationError(
                    "Script test expected_exit_code must be an integer.",
                    code="skill_creator_script_test_contract_invalid",
                ) from exc
            if not -255 <= expected_exit_code <= 255:
                raise SkillCreatorValidationError("Script test exit code is invalid.")
            raw_stdout_contains = raw.get("stdout_contains", [])
            raw_stderr_contains = raw.get("stderr_contains", [])
            if not isinstance(raw_stdout_contains, list):
                raise SkillCreatorValidationError(
                    "Script test stdout_contains must be a JSON array of strings.",
                    code="skill_creator_script_test_contract_invalid",
                )
            if not isinstance(raw_stderr_contains, list):
                raise SkillCreatorValidationError(
                    "Script test stderr_contains must be a JSON array of strings.",
                    code="skill_creator_script_test_contract_invalid",
                )
            stdout_contains = self._text_list(raw_stdout_contains, 10, 500)
            stderr_contains = self._text_list(raw_stderr_contains, 10, 500)
            for stream_name, expected_values in (
                ("stdout", stdout_contains),
                ("stderr", stderr_contains),
            ):
                for value_index, expected_value in enumerate(expected_values):
                    self._reject_credentials(
                        path=(
                            f"script-tests/{test_id}/{stream_name}-{value_index}.txt"
                        ),
                        content=expected_value,
                    )
            result.append(
                ResourceScriptTest(
                    test_id=test_id,
                    args=args,
                    fixtures=fixtures,
                    expected_exit_code=expected_exit_code,
                    stdout_contains=stdout_contains,
                    stderr_contains=stderr_contains,
                )
            )
        return result

    @staticmethod
    def _issues(value: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for raw in list(value or [])[:20]:
            if not isinstance(raw, Mapping):
                continue
            result.append(
                {
                    "code": str(raw.get("code") or "resource_validation")[:120],
                    "message": str(raw.get("message") or "Resource validation failed.")[:500],
                    "path": str(raw.get("path") or "")[:240] or None,
                    "severity": "error",
                }
            )
        return result

    @staticmethod
    def _validate_resource_bytes(path: str, content: str) -> None:
        suffix = PurePosixPath(path).suffix.lower()
        if suffix not in {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".js", ".mjs", ".cjs", ".csv", ".tsv", ".html", ".css"}:
            raise SkillCreatorValidationError("Resource must be a supported UTF-8 text file.")
        if len(content.encode("utf-8")) > MAX_RESOURCE_BYTES:
            raise SkillCreatorValidationError(
                "Resource exceeds 24 KiB.", code="skill_creator_resource_too_large"
            )

    @staticmethod
    def _validate_resource_path(kind: str, path: str) -> None:
        root = {"script": "scripts", "reference": "references", "asset": "assets"}[kind]
        normalized = SkillResourceBuildStore._relative_path(path, root=root)
        if normalized != path or forbidden_resource_path(path):
            raise SkillCreatorValidationError(
                "Resource path does not match its planned type.",
                code="skill_creator_resource_path_invalid",
            )

    @staticmethod
    def _ensure_package_budget(
        resources: list[SkillResourceBuildItem], skill_markdown: str | None
    ) -> None:
        total = len((skill_markdown or "").encode("utf-8"))
        total += sum(
            len((item.content or "").encode("utf-8"))
            for item in resources
            if item.action != "delete"
        )
        if total > MAX_PACKAGE_BYTES:
            raise SkillCreatorValidationError(
                "Generated Skill package exceeds 160 KiB.",
                code="skill_creator_resource_package_too_large",
            )

    @staticmethod
    def _reject_credentials(
        *, path: str, content: str, files: Mapping[str, str] | None = None
    ) -> None:
        issues = scan_skill_package_credentials(
            skill_markdown=content if path == "SKILL.md" else None,
            files=dict(files or ({path: content} if content else {})),
        )
        if issues:
            raise SkillCreatorValidationError(
                "Generated resource contains blocked credential material.",
                code="skill_credentials_blocked",
            )

    @staticmethod
    def _relative_path(value: Any, *, root: str) -> str:
        raw = str(value or "").strip().replace("\\", "/")
        pure = PurePosixPath(raw)
        if (
            not raw or pure.is_absolute() or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise SkillCreatorValidationError("Unsafe relative path.")
        return f"{root}/{pure.as_posix()}" if pure.parts[0] != root else pure.as_posix()

    @staticmethod
    def _text_list(value: Any, maximum: int, item_maximum: int) -> list[str]:
        if not isinstance(value, list) or len(value) > maximum:
            raise SkillCreatorValidationError("Invalid text list.")
        result = [str(item or "").strip() for item in value]
        if any(not item or len(item) > item_maximum for item in result):
            raise SkillCreatorValidationError("Invalid text list item.")
        return result

    @staticmethod
    def _required_text(value: Any, field_name: str, maximum: int) -> str:
        clean = str(value or "").strip()
        if not clean or len(clean) > maximum:
            raise SkillCreatorValidationError(f"Invalid {field_name}.")
        return clean

    @staticmethod
    def _digest(value: Any, field_name: str) -> str:
        clean = str(value or "").strip().lower()
        if not _DIGEST_RE.fullmatch(clean):
            raise SkillCreatorValidationError(f"Invalid {field_name}.")
        return clean

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if hasattr(value, "__dataclass_fields__"):
            return asdict(value)
        if isinstance(value, Mapping):
            return {str(key): SkillResourceBuildStore._jsonable(item) for key, item in value.items()}
        if isinstance(value, list):
            return [SkillResourceBuildStore._jsonable(item) for item in value]
        return value

    def _load(self) -> None:
        if not self.snapshot_path.exists():
            return
        try:
            raw = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            if (
                not isinstance(raw, dict)
                or raw.get("version") != self.SCHEMA_VERSION
                or not isinstance(raw.get("items"), list)
                or not isinstance(raw.get("quarantine", []), list)
            ):
                raise ValueError("invalid resource build snapshot")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._load_error = f"skill_resource_build_store_corrupt: {str(exc)[:300]}"
            return
        self._quarantine = [
            dict(item) for item in raw.get("quarantine", []) if isinstance(item, dict)
        ][:500]
        for index, record in enumerate(raw["items"]):
            try:
                item = self._decode(record)
                if item.build_id in self._builds or item.session_id in self._session_index:
                    raise ValueError("duplicate resource build mapping")
                self._builds[item.build_id] = item
                self._session_index[item.session_id] = item.build_id
            except Exception as exc:
                self._quarantine.append(self._quarantine_record(record, index, exc))
        if len(self._quarantine) != len(raw.get("quarantine", [])):
            try:
                self._save_unlocked()
            except OSError as exc:
                self._load_error = f"skill_resource_build_store_rewrite_failed: {str(exc)[:300]}"

    def _save_unlocked(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.SCHEMA_VERSION,
            "items": [asdict(item) for item in sorted(self._builds.values(), key=lambda value: value.created_at)],
            "quarantine": self._quarantine[-500:],
        }
        temporary = self.snapshot_path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.snapshot_path)

    def _ensure_readable_unlocked(self) -> None:
        if self._load_error:
            raise SkillCreatorStorageError(self._load_error)

    def _ensure_writable_unlocked(self) -> None:
        self._ensure_readable_unlocked()

    @staticmethod
    def _quarantine_record(record: Any, index: int, exc: Exception) -> dict[str, Any]:
        try:
            encoded = json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
        except Exception:
            encoded = repr(type(record)).encode("utf-8")
        return {
            "index": index,
            "record_sha256": hashlib.sha256(encoded).hexdigest(),
            "size_bytes": len(encoded),
            "reason": type(exc).__name__,
        }


def forbidden_resource_path(path: str) -> bool:
    pure = PurePosixPath(str(path or "").replace("\\", "/"))
    lowered = pure.as_posix().casefold()
    return bool(
        pure.name.casefold() in _FORBIDDEN_PACKAGE_NAMES
        or lowered.startswith("eval/")
        or lowered.startswith("evals/")
        or "user-meta" in lowered
    )


__all__ = [
    "MAX_BUILD_RESOURCES",
    "MAX_PACKAGE_BYTES",
    "MAX_RESOURCE_BYTES",
    "MAX_SEGMENT_BYTES",
    "MAX_SKILL_MARKDOWN_BYTES",
    "RESOURCE_BUILD_VERSION",
    "ResourceScriptFixture",
    "ResourceScriptTest",
    "ResourceScriptTestReceipt",
    "ResourceScriptTestResult",
    "SkillResourceBuild",
    "SkillResourceBuildItem",
    "SkillResourceBuildStore",
    "forbidden_resource_path",
]
