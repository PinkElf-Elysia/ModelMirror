from __future__ import annotations

import ast
import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any, Awaitable, Callable

from .creator_resource_build import (
    MAX_RESOURCE_BYTES,
    MAX_SEGMENT_BYTES,
    MAX_SKILL_MARKDOWN_BYTES,
    RESOURCE_BUILD_VERSION,
    HookScriptTestReceipt,
    HookScriptTestResult,
    ResourceScriptTestReceipt,
    ResourceScriptTestResult,
    SkillResourceBuild,
    SkillResourceBuildHook,
    SkillResourceBuildItem,
)
from .creator_runtime import CREATOR_WORKFLOW_VERSION
from .creator_store import CREATOR_ASSISTANT_AGENT_ID, SkillCreatorValidationError
from .package_validation import validate_skill_package
from .hook_contract import (
    HOOK_MANIFEST_PATH,
    SkillHookContractError,
    parse_hook_manifest,
    parse_hook_result,
)

try:
    from server.xpert_runtime.sandbox_client import (
        SandboxClientError,
        SandboxClientProtocol,
    )
except ModuleNotFoundError:
    from xpert_runtime.sandbox_client import SandboxClientError, SandboxClientProtocol


RESOURCE_BUILDER_WORKFLOW_VERSION = "skill-creator-resource-builder-v1"
SKILL_AUTHORING_PROFILE = "skill_authoring_v1"
SCRIPT_TEST_TIMEOUT_SECONDS = 10
_LONG_REFERENCE_LINES = 100
_SCRIPT_SUFFIXES = {".py": "python", ".js": "node", ".mjs": "node", ".cjs": "node"}
_FORBIDDEN_PATH_PARTS = {"eval", "evals"}


@dataclass(frozen=True, slots=True)
class ResourceBuildWorkflowInvocation:
    workflow: dict[str, Any]
    inputs: dict[str, str]
    runtime_metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ResourceBuildGenerationRequest:
    build: SkillResourceBuild
    target_id: str
    segment_index: int


@dataclass(frozen=True, slots=True)
class ResourceBuildSegment:
    target_id: str
    segment_index: int
    content: str
    complete: bool
    script_tests: list[dict[str, Any]]


ResourceBuilderWorkflowRunner = Callable[[ResourceBuildWorkflowInvocation], Awaitable[str]]


class WorkflowCreatorResourceBuilder:
    """Generate exactly one server-selected resource segment per invocation."""

    def __init__(
        self,
        *,
        model_id: str,
        model_available: Callable[[], bool],
        runner: ResourceBuilderWorkflowRunner,
    ) -> None:
        self.model_id = str(model_id or "").strip()
        self.model_available = model_available
        self.runner = runner

    def available(self) -> bool:
        return bool(self.model_id and self.model_available())

    async def generate(self, request: ResourceBuildGenerationRequest) -> ResourceBuildSegment:
        invocation = build_resource_builder_invocation(request, model_id=self.model_id)
        output = await self.runner(invocation)
        return parse_resource_build_segment(
            output,
            expected_target_id=request.target_id,
            expected_segment_index=request.segment_index,
        )


def build_resource_builder_invocation(
    request: ResourceBuildGenerationRequest,
    *,
    model_id: str,
) -> ResourceBuildWorkflowInvocation:
    build = request.build
    target = _target(build, request.target_id)
    bound_hooks = [
        asdict(hook)
        for hook in build.hooks
        if hook.action != "delete"
        and (
            request.target_id == "SKILL.md"
            or hook.script_resource_id == request.target_id
        )
    ]
    if bound_hooks:
        target = {**target, "bound_hooks": bound_hooks}
    dependency_ids = set(target.get("depends_on") or [])
    accepted_resources = {
        item.path: item.content
        for item in build.resources
        if item.state == "accepted"
        and item.action != "delete"
        and item.content is not None
        and (request.target_id == "SKILL.md" or item.resource_id in dependency_ids)
    }
    context = {
        "resource_build_version": RESOURCE_BUILD_VERSION,
        "build_id": build.build_id,
        "target": target,
        "segment_index": request.segment_index,
        "existing_segments": (
            list(target.get("chunks") or []) if request.target_id != "SKILL.md" else list(build.skill_chunks)
        ),
        "skill": {
            "name": build.skill_name,
            "description": build.skill_description,
            "workflow_steps": build.workflow_steps,
            "output_contract": build.output_contract,
            "failure_modes": build.failure_modes,
        },
        "accepted_resource_index": [
            {"path": path, "sha256": _sha256(content), "size_bytes": len(content.encode("utf-8"))}
            for path, content in sorted(accepted_resources.items())
        ],
        "accepted_resource_content": accepted_resources,
        "confirmed_hooks": bound_hooks,
        "generation_limits": {
            "segment_bytes_max": MAX_SEGMENT_BYTES,
            "resource_bytes_max": 24 * 1024,
            "skill_markdown_bytes_max": 20 * 1024,
            "script_test_count": [1, 3],
            "script_fixture_count_per_test_max": 8,
            "script_languages": ["python", "javascript"],
            "assets_utf8_text_only": True,
        },
    }
    context_text = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(context_text.encode("utf-8")) > 240_000:
        raise SkillCreatorValidationError(
            "Creator resource build context is too large.",
            code="skill_creator_context_too_large",
        )
    workflow = {
        "id": f"skill-resource-build-{build.build_id}",
        "title": "Skill Creator resource build",
        "nodes": [
            {"id": "builder-input", "type": "input", "data": {"kind": "input", "variableName": "creator_request"}},
            {
                "id": "builder-agent",
                "type": "workflow_agent",
                "data": {
                    "kind": "workflow_agent",
                    "agentName": CREATOR_ASSISTANT_AGENT_ID,
                    "modelId": str(model_id or "").strip(),
                    "agentStrategy": "function_calling",
                    "rolePrompt": _builder_prompt(request.target_id, target),
                    "taskInput": "{{creator_request}}",
                    "outputVariable": "resource_segment",
                    "toolMode": "none",
                    "toolNames": "",
                    "maxIterations": "1",
                    "maxToolCalls": "1",
                    "maxToolConcurrency": "1",
                    "parallelToolCalls": "false",
                    "retryOnFailure": "false",
                    "temperature": "0.1",
                },
            },
            {"id": "builder-output", "type": "output", "data": {"kind": "output", "outputVariable": "resource_segment"}},
        ],
        "edges": [
            {"id": "builder-input-agent", "source": "builder-input", "target": "builder-agent"},
            {"id": "builder-agent-output", "source": "builder-agent", "target": "builder-output"},
        ],
    }
    return ResourceBuildWorkflowInvocation(
        workflow=workflow,
        inputs={"creator_request": context_text},
        runtime_metadata={
            "creator_session_id": build.session_id,
            "assistant_agent_id": CREATOR_ASSISTANT_AGENT_ID,
            "creator_workflow_version": CREATOR_WORKFLOW_VERSION,
            "creator_phase": "resource_build",
            "resource_builder_workflow_version": RESOURCE_BUILDER_WORKFLOW_VERSION,
            "resource_build_id": build.build_id,
            "resource_target_id": request.target_id,
        },
    )


def parse_resource_build_segment(
    value: Any,
    *,
    expected_target_id: str,
    expected_segment_index: int,
) -> ResourceBuildSegment:
    if not isinstance(value, str):
        raise SkillCreatorValidationError("Resource builder returned non-text output.", code="skill_creator_resource_builder_invalid")
    text = value.strip()
    try:
        payload = _decode_resource_build_json(text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise SkillCreatorValidationError("Resource builder did not return valid JSON.", code="skill_creator_resource_builder_invalid") from exc
    if not isinstance(payload, dict) or payload.get("resource_build_version") != RESOURCE_BUILD_VERSION:
        raise SkillCreatorValidationError("Resource builder contract is invalid.", code="skill_creator_resource_builder_invalid")
    if payload.get("target_id") != expected_target_id or payload.get("segment_index") != expected_segment_index:
        raise SkillCreatorValidationError("Resource builder changed the frozen target.", code="skill_creator_resource_builder_target_changed")
    content = payload.get("content")
    target_budget = (
        MAX_SKILL_MARKDOWN_BYTES
        if expected_target_id == "SKILL.md"
        else MAX_RESOURCE_BYTES
    )
    if not isinstance(content, str) or not content or len(content.encode("utf-8")) > target_budget:
        raise SkillCreatorValidationError(
            "Resource builder output is empty or exceeds the frozen target budget.",
            code="skill_creator_resource_segment_invalid",
        )
    tests = payload.get("script_tests") or []
    if not isinstance(tests, list):
        raise SkillCreatorValidationError("Resource builder returned invalid script tests.", code="skill_creator_resource_builder_invalid")
    return ResourceBuildSegment(
        target_id=expected_target_id,
        segment_index=expected_segment_index,
        content=content,
        complete=bool(payload.get("complete")),
        script_tests=[dict(item) for item in tests if isinstance(item, dict)],
    )


def _decode_resource_build_json(text: str) -> Any:
    """Decode one versioned segment without repairing or guessing model output."""

    if not text:
        raise ValueError("empty resource builder output")
    try:
        return json.loads(text)
    except (ValueError, json.JSONDecodeError):
        pass

    decoder = json.JSONDecoder()
    candidates: dict[str, dict[str, Any]] = {}
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            candidate, _end = decoder.raw_decode(text[index:])
        except (ValueError, json.JSONDecodeError):
            continue
        if not isinstance(candidate, dict):
            continue
        if candidate.get("resource_build_version") != RESOURCE_BUILD_VERSION:
            continue
        fingerprint = json.dumps(
            candidate,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        candidates[fingerprint] = candidate

    if len(candidates) != 1:
        raise ValueError("resource builder output must contain one versioned JSON object")
    return next(iter(candidates.values()))


def validate_resource_content(item: SkillResourceBuildItem) -> list[dict[str, Any]]:
    content = item.content or ""
    issues: list[dict[str, Any]] = []
    if item.kind == "reference" and len(content.splitlines()) > _LONG_REFERENCE_LINES:
        head = "\n".join(content.splitlines()[:40]).lower()
        if not re.search(r"(?m)^#{1,3}\s+(table of contents|contents|目录|目次)\s*$", head):
            issues.append(_issue("skill_creator_reference_toc_missing", "References longer than 100 lines require a table of contents.", item.path))
    if item.kind == "script":
        suffix = PurePosixPath(item.path).suffix.lower()
        if suffix not in _SCRIPT_SUFFIXES:
            issues.append(_issue("skill_creator_script_language_unsupported", "Generated scripts must be Python or JavaScript.", item.path))
        duplicate_entrypoint = len(re.findall(r"(?m)^#!", content)) > 1
        if suffix == ".py":
            duplicate_entrypoint = duplicate_entrypoint or len(
                re.findall(
                    r"(?m)^\s*if\s+__name__\s*==\s*['\"]__main__['\"]\s*:",
                    content,
                )
            ) > 1
        if duplicate_entrypoint:
            issues.append(
                _issue(
                    "skill_creator_script_duplicate_entrypoint",
                    "Generated scripts must contain exactly one complete program, not concatenated revisions.",
                    item.path,
                )
            )
        if not re.search(r"(?i)(usage|argparse|process\.argv|sys\.argv)", content):
            issues.append(_issue("skill_creator_script_cli_missing", "Generated scripts require an explicit command-line interface.", item.path))
        if not re.search(r"(?i)(sys\.exit|process\.exit|raise\s+SystemExit|returncode|exit code|catch\s*\(|except\s+)", content):
            issues.append(_issue("skill_creator_script_failure_missing", "Generated scripts require conservative non-zero failure behavior.", item.path))
        if suffix == ".py":
            try:
                ast.parse(content)
            except (SyntaxError, ValueError, RecursionError):
                issues.append(_issue("python_syntax_invalid", "Generated Python script has invalid syntax.", item.path))
    if item.kind == "asset" and not re.search(r"(?i)(template|placeholder|copy|render|replace|模板|占位|复制|渲染|替换)", content):
        issues.append(_issue("skill_creator_asset_contract_missing", "Text assets must be usable templates or boilerplate.", item.path))
    probe = validate_skill_package(
        root_name="resource-probe",
        skill_markdown=(
            "---\nname: resource-probe\n"
            "description: Validate one generated resource. Use when the Creator checks a file; do not use elsewhere.\n"
            "---\n\n# Resource probe\n\nRead `" + item.path + "`.\n"
        ),
        files={item.path: content},
    )
    issues.extend(
        issue.to_dict()
        for issue in probe.issues
        if issue.path == item.path
    )
    return issues


def validate_final_resource_package(build: SkillResourceBuild) -> list[dict[str, Any]]:
    if not build.skill_markdown:
        return [_issue("skill_creator_skill_markdown_missing", "SKILL.md is missing.", "SKILL.md")]
    files = {}
    issues: list[dict[str, Any]] = []
    for item in build.resources:
        path = PurePosixPath(item.path)
        if path.name.lower() == "readme.md" or any(part.lower() in _FORBIDDEN_PATH_PARTS for part in path.parts) or path.name.lower() in {"_user_meta.json", "user-meta.json"}:
            issues.append(_issue("skill_creator_resource_path_forbidden", "README, eval data, and user metadata do not belong in a Skill package.", item.path))
        if item.action == "delete":
            continue
        if item.state != "accepted" or item.content is None:
            issues.append(_issue("skill_creator_resource_unaccepted", "Every active resource must be accepted before SKILL.md.", item.path))
            continue
        files[item.path] = item.content
        if item.path not in build.skill_markdown:
            issues.append(_issue("skill_creator_resource_unreferenced", "Every resource must be referenced directly from SKILL.md.", item.path))
        else:
            nearby_mentions = [
                build.skill_markdown[max(0, match.start() - 180): match.end() + 180]
                for match in re.finditer(re.escape(item.path), build.skill_markdown)
            ]
            if item.kind == "asset" and not any(
                re.search(r"(?i)(copy|render|template|复制|渲染|模板)", nearby)
                for nearby in nearby_mentions
            ):
                issues.append(_issue("skill_creator_asset_usage_missing", "SKILL.md must explain when an asset is copied or rendered.", item.path))
            if item.kind == "script" and not any(
                re.search(r"(?i)(run|execute|python|node|运行|执行)", nearby)
                for nearby in nearby_mentions
            ):
                issues.append(_issue("skill_creator_script_usage_missing", "SKILL.md must explain when a script is executed.", item.path))
        if item.kind == "script" and (
            item.script_receipt is None
            or not item.script_receipt.passed
            or item.script_receipt.script_digest != item.content_digest
        ):
            issues.append(_issue("skill_creator_script_receipt_missing", "Every script requires a passing digest-bound offline test receipt.", item.path))
        issues.extend(validate_resource_content(item))
        if item.kind == "reference":
            linked_references = {
                match
                for match in re.findall(r"references/[A-Za-z0-9._/-]+", item.content or "")
                if match.rstrip("`.,):]") != item.path
            }
            if linked_references:
                issues.append(_issue("skill_creator_reference_chain_forbidden", "References must be directly reachable from SKILL.md rather than through nested reference chains.", item.path))
            skill_paragraphs = _substantive_paragraphs(build.skill_markdown)
            copied = skill_paragraphs & _substantive_paragraphs(item.content or "")
            if copied:
                issues.append(_issue("skill_creator_reference_duplicate_body", "References must not duplicate large SKILL.md passages.", item.path))
    references = [item for item in build.resources if item.kind == "reference" and item.action != "delete"]
    if (len(references) >= 2 or any(len((item.content or "").splitlines()) > _LONG_REFERENCE_LINES for item in references)) and not re.search(r"(?i)(\brg\b|sandbox_search_files)", build.skill_markdown):
        issues.append(_issue("skill_creator_reference_search_missing", "Multiple or long references require a bounded rg or sandbox_search_files example in SKILL.md.", "SKILL.md"))
    level_two_headings = [
        re.sub(r"\s+", " ", match).strip().casefold()
        for match in re.findall(r"(?m)^##\s+(.+?)\s*#*\s*$", build.skill_markdown)
    ]
    duplicate_headings = sorted(
        heading for heading in set(level_two_headings)
        if level_two_headings.count(heading) > 1
    )
    if duplicate_headings:
        issues.append(
            _issue(
                "skill_creator_skill_markdown_duplicate_section",
                "SKILL.md 的每个二级章节只能出现一次，请重新生成重复章节。",
                "SKILL.md",
            )
        )
    known_section_markers = re.finditer(
        r"(?i)##\s+(?:purpose|scope|inputs?|preconditions?|workflow|output|failure|"
        r"degradation|quality|resources?|用途|范围|输入|前置条件|流程|步骤|输出|失败|"
        r"降级|质量|资源)\b",
        build.skill_markdown,
    )
    if any(match.start() > 0 and build.skill_markdown[match.start() - 1] != "\n" for match in known_section_markers):
        issues.append(
            _issue(
                "skill_creator_skill_markdown_heading_boundary_invalid",
                "SKILL.md 的二级章节必须从新行开始，请重新生成粘连的章节。",
                "SKILL.md",
            )
        )
    active_hooks = [hook for hook in build.hooks if hook.action != "delete"]
    if active_hooks:
        if not build.hook_manifest or not build.hook_manifest_digest:
            issues.append(
                _issue(
                    "skill_creator_hook_manifest_missing",
                    "Confirmed Hooks require a deterministic manifest.",
                    HOOK_MANIFEST_PATH,
                )
            )
        else:
            files[HOOK_MANIFEST_PATH] = build.hook_manifest
            try:
                parse_hook_manifest(
                    build.hook_manifest,
                    available_paths=files.keys(),
                )
            except SkillHookContractError as exc:
                issues.append(_issue(exc.code, str(exc), HOOK_MANIFEST_PATH))
        for hook in active_hooks:
            receipt = hook.test_receipt
            if (
                receipt is None
                or not receipt.passed
                or receipt.hook_spec_digest != hook.spec_digest
                or receipt.manifest_digest != build.hook_manifest_digest
            ):
                issues.append(
                    _issue(
                        "skill_creator_hook_receipt_missing",
                        "Every active Hook requires a passing digest-bound offline receipt.",
                        HOOK_MANIFEST_PATH,
                    )
                )
        if not re.search(r"(?i)(hook|运行前|运行后|调用前|调用后|会话开始|会话结束)", build.skill_markdown):
            issues.append(
                _issue(
                    "skill_creator_hook_guidance_missing",
                    "SKILL.md must explain the confirmed Hook boundaries and failure behavior.",
                    "SKILL.md",
                )
            )
        if not _has_hook_advisory_guidance(build.skill_markdown):
            issues.append(
                _issue(
                    "skill_creator_hook_advisory_guidance_missing",
                    (
                        "SKILL.md must explain what the Agent does when a request does not "
                        "trigger the Hook event."
                    ),
                    "SKILL.md",
                )
            )
    validation = validate_skill_package(
        root_name=build.skill_name,
        skill_markdown=build.skill_markdown,
        files=files,
    )
    issues.extend(issue.to_dict() for issue in validation.issues)
    if validation.valid and validation.package is not None:
        if validation.package.name != build.skill_name:
            issues.append(_issue(
                "skill_package_name_mismatch",
                "SKILL.md frontmatter name must exactly match the confirmed resource plan.",
                "SKILL.md",
            ))
        if validation.package.description != build.skill_description:
            issues.append(_issue(
                "skill_package_description_mismatch",
                "SKILL.md frontmatter description must exactly match the confirmed resource plan.",
                "SKILL.md",
            ))
    return issues[:40]


class SandboxCreatorScriptRunner:
    """Run planned script fixtures in the profile-bound offline Sidecar."""

    def __init__(self, client: SandboxClientProtocol) -> None:
        self.client = client

    async def health(self) -> dict[str, Any]:
        try:
            response = await self.client.health(required_profile=SKILL_AUTHORING_PROFILE)
        except SandboxClientError as exc:
            raise SkillCreatorValidationError(
                "Skill authoring Sandbox sidecar is unavailable.",
                code="skill_creator_sandbox_unavailable",
            ) from exc
        profile = dict(response.get("profiles", {}).get(SKILL_AUTHORING_PROFILE) or {})
        if profile.get("network_policy") != "container_network_none_required" or profile.get("read_only_roots") != ["inputs", "skills"] or profile.get("writable_roots") != ["work", ".tmp"]:
            raise SkillCreatorValidationError("Sandbox authoring profile attestation failed.", code="skill_creator_sandbox_profile_invalid")
        return response

    async def run(self, item: SkillResourceBuildItem) -> ResourceScriptTestReceipt:
        if item.kind != "script" or item.content is None or item.content_digest is None:
            raise SkillCreatorValidationError("Script content is not ready for testing.", code="skill_creator_script_not_ready")
        command = _SCRIPT_SUFFIXES.get(PurePosixPath(item.path).suffix.lower())
        if command is None:
            raise SkillCreatorValidationError("Unsupported script language.", code="skill_creator_script_language_unsupported")
        await self.health()
        results: list[ResourceScriptTestResult] = []
        for test in item.script_tests:
            workspace_id = f"skill-authoring-{uuid.uuid4().hex}"
            created = await self.client.request({"action": "ensure_workspace", "workspace_id": workspace_id, "profile": SKILL_AUTHORING_PROFILE})
            capability = str(created.get("provisioning_capability") or "")
            auth = {"workspace_id": workspace_id, "profile": SKILL_AUTHORING_PROFILE, "provisioning_capability": capability}
            try:
                script_name = PurePosixPath(item.path).name
                script_path = f"skills/authoring-resource/{script_name}"
                await self.client.request({**auth, "action": "seed_file", "path": script_path, "content": item.content, "operation_id": "seed-script"})
                for index, fixture in enumerate(test.fixtures):
                    await self.client.request({**auth, "action": "seed_file", "path": fixture.path, "content": fixture.content, "operation_id": f"seed-fixture-{index}"})
                await self.client.request({**auth, "action": "seal_workspace"})
                response = await self.client.request({
                    **auth,
                    "action": "shell",
                    "argv": [command, f"../{script_path}", *test.args],
                    "timeout_seconds": SCRIPT_TEST_TIMEOUT_SECONDS,
                    "operation_id": "run-script",
                })
                stdout = str(response.get("stdout") or "")
                stderr = str(response.get("stderr") or "")
                exit_code = int(response.get("exit_code") or 0)
                test_issues = []
                if exit_code != test.expected_exit_code:
                    test_issues.append("unexpected_exit_code")
                if any(expected not in stdout for expected in test.stdout_contains):
                    test_issues.append("stdout_assertion_failed")
                if any(expected not in stderr for expected in test.stderr_contains):
                    test_issues.append("stderr_assertion_failed")
                results.append(ResourceScriptTestResult(
                    test_id=test.test_id,
                    passed=not test_issues,
                    exit_code=exit_code,
                    stdout_sha256=_sha256(stdout),
                    stderr_sha256=_sha256(stderr),
                    duration_ms=float(response.get("duration_ms") or 0),
                    issues=test_issues,
                ))
            except SandboxClientError as exc:
                results.append(ResourceScriptTestResult(
                    test_id=test.test_id,
                    passed=False,
                    exit_code=-1,
                    stdout_sha256=_sha256(""),
                    stderr_sha256=_sha256(""),
                    duration_ms=0,
                    issues=[str(exc.code)[:120]],
                ))
            finally:
                try:
                    await self.client.request({**auth, "action": "cleanup_workspace"})
                except Exception:
                    pass
        return ResourceScriptTestReceipt(
            receipt_id=f"script_receipt_{uuid.uuid4().hex}",
            script_digest=item.content_digest,
            profile=SKILL_AUTHORING_PROFILE,
            passed=bool(results) and all(result.passed for result in results),
            results=results,
        )

    async def run_hook(
        self,
        item: SkillResourceBuildItem,
        hook: SkillResourceBuildHook,
        *,
        manifest_digest: str,
    ) -> HookScriptTestReceipt:
        """Exercise a frozen Hook entry through its real file-based CLI contract."""

        if item.kind != "script" or item.content is None or item.content_digest is None:
            raise SkillCreatorValidationError(
                "Hook script content is not ready for testing.",
                code="skill_creator_hook_script_not_ready",
            )
        command = _SCRIPT_SUFFIXES.get(PurePosixPath(item.path).suffix.lower())
        if command is None:
            raise SkillCreatorValidationError(
                "Unsupported Hook script language.",
                code="skill_creator_script_language_unsupported",
            )
        await self.health()
        cases = _hook_authoring_contexts(hook)
        results: list[HookScriptTestResult] = []
        for case_id, context_payload in cases:
            workspace_id = f"skill-hook-authoring-{uuid.uuid4().hex}"
            created = await self.client.request(
                {
                    "action": "ensure_workspace",
                    "workspace_id": workspace_id,
                    "profile": SKILL_AUTHORING_PROFILE,
                }
            )
            capability = str(created.get("provisioning_capability") or "")
            auth = {
                "workspace_id": workspace_id,
                "profile": SKILL_AUTHORING_PROFILE,
                "provisioning_capability": capability,
            }
            raw_result = ""
            result_types: list[str] = []
            issues: list[str] = []
            duration_ms = 0.0
            try:
                script_name = PurePosixPath(item.path).name
                script_path = f"skills/authoring-resource/{script_name}"
                context_path = f"inputs/{case_id}.json"
                await self.client.request(
                    {
                        **auth,
                        "action": "seed_file",
                        "path": script_path,
                        "content": item.content,
                        "operation_id": f"seed-hook-script-{case_id}",
                    }
                )
                await self.client.request(
                    {
                        **auth,
                        "action": "seed_file",
                        "path": context_path,
                        "content": json.dumps(
                            context_payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        "operation_id": f"seed-hook-context-{case_id}",
                    }
                )
                await self.client.request({**auth, "action": "seal_workspace"})
                response = await self.client.request(
                    {
                        **auth,
                        "action": "shell",
                        "argv": [
                            command,
                            f"../{script_path}",
                            "--context",
                            f"../{context_path}",
                            "--result",
                            f"{case_id}-result.json",
                        ],
                        "timeout_seconds": SCRIPT_TEST_TIMEOUT_SECONDS,
                        "operation_id": f"run-hook-{case_id}",
                    }
                )
                duration_ms = float(response.get("duration_ms") or 0)
                if int(response.get("exit_code") or 0) != 0:
                    issues.append("unexpected_exit_code")
                read_result = await self.client.request(
                    {
                        **auth,
                        "action": "read_file",
                        "path": f"work/{case_id}-result.json",
                    }
                )
                raw_result = str(read_result.get("content") or "")
                parsed = parse_hook_result(
                    raw_result,
                    hook_event=hook.event,
                    hook_mode=hook.mode,
                )
                result_types = [output.output_type for output in parsed.outputs]
                if hook.mode == "annotation" and "annotation" not in result_types:
                    issues.append("annotation_output_missing")
                elif hook.event in {"pre_tool_use", "post_tool_use"}:
                    validations = [
                        output
                        for output in parsed.outputs
                        if output.output_type == "validation"
                    ]
                    if hook.mode == "validation":
                        expected_passed = case_id == "safe"
                        if not any(
                            output.passed is expected_passed for output in validations
                        ):
                            issues.append(
                                "validation_pass_missing"
                                if expected_passed
                                else "validation_failure_missing"
                            )
                    elif hook.mode == "guard":
                        if case_id == "safe" and (
                            "deny" in result_types
                            or not any(output.passed is True for output in validations)
                        ):
                            issues.append("guard_allow_missing")
                        if case_id == "boundary" and "deny" not in result_types:
                            issues.append("guard_deny_missing")
            except SkillHookContractError as exc:
                issues.append(str(exc.code)[:120])
            except SandboxClientError as exc:
                issues.append(str(exc.code)[:120])
            except Exception:
                issues.append("skill_hook_execution_failed")
            finally:
                try:
                    await self.client.request({**auth, "action": "cleanup_workspace"})
                except Exception:
                    pass
            results.append(
                HookScriptTestResult(
                    case_id=case_id,
                    passed=not issues,
                    result_types=result_types,
                    result_digest=_sha256(raw_result),
                    duration_ms=duration_ms,
                    issues=issues,
                )
            )
        return HookScriptTestReceipt(
            receipt_id=f"hook_receipt_{uuid.uuid4().hex}",
            hook_id=hook.hook_id,
            hook_spec_digest=hook.spec_digest,
            script_digest=item.content_digest,
            manifest_digest=manifest_digest,
            profile=SKILL_AUTHORING_PROFILE,
            passed=bool(results) and all(item.passed for item in results),
            results=results,
        )


def _hook_authoring_contexts(
    hook: SkillResourceBuildHook,
) -> list[tuple[str, dict[str, Any]]]:
    base = {
        "version": "modelmirror-hook-context-v1",
        "event": hook.event,
        "skill": {
            "skill_id": "authoring-fixture",
            "version_id": "immutable-fixture-v1",
            "manifest_digest": "0" * 64,
        },
        "hook": {"hook_id": hook.hook_id, "mode": hook.mode},
    }
    if hook.event in {"pre_tool_use", "post_tool_use"}:
        tool_name = hook.tool_names[0]
        release_path = (
            "work/release/release-notes.md"
            if tool_name == "sandbox_write_file"
            else "release/release-notes.md"
        )
        unsafe_path = (
            "work/release/../unsafe.exe"
            if tool_name == "sandbox_write_file"
            else "release/../unsafe.exe"
        )
        safe = {
            **base,
            "tool": {
                "name": tool_name,
                **(
                    {
                        "arguments": {
                            "name": "release-notes.md",
                            "path": release_path,
                            "content_type": "text/markdown",
                        }
                    }
                    if hook.event == "pre_tool_use"
                    else {
                        "success": True,
                        "content_types": ["text/markdown"],
                        "output_length": 120,
                        "output_digest": "1" * 64,
                        "artifact_paths": [release_path],
                    }
                ),
            },
        }
        if hook.mode == "annotation":
            return [("annotation", safe)]
        unsafe = {
            **base,
            "tool": {
                "name": tool_name,
                **(
                    {
                        "arguments": {
                            "name": unsafe_path,
                            "path": unsafe_path,
                            "content_type": "application/octet-stream",
                        }
                    }
                    if hook.event == "pre_tool_use"
                    else {
                        "success": False,
                        "content_types": ["application/octet-stream"],
                        "output_length": 0,
                        "output_digest": "2" * 64,
                        "artifact_paths": [],
                    }
                ),
            },
        }
        return [("safe", safe), ("boundary", unsafe)]
    session = {
        **base,
        "session": (
            {
                "task_input": "Prepare one bounded synthetic result.",
                "runtime_kind": "workflow",
                "capabilities": {},
            }
            if hook.event == "session_start"
            else {
                "status": "completed",
                "output_length": 120,
                "output_digest": "3" * 64,
            }
        ),
    }
    return [("session", session)]


def _target(build: SkillResourceBuild, target_id: str) -> dict[str, Any]:
    if target_id == "SKILL.md":
        return {
            "target_id": "SKILL.md",
            "kind": "skill_markdown",
            "path": "SKILL.md",
            "feedback": build.skill_feedback,
            "validation_issues": build.skill_validation_issues,
        }
    for item in build.resources:
        if item.resource_id == target_id:
            return asdict(item)
    raise SkillCreatorValidationError("Resource build target was not found.", code="skill_creator_resource_not_found")


def _builder_prompt(target_id: str, target: dict[str, Any]) -> str:
    kind = str(target.get("kind") or "")
    common = (
        "You are ModelMirror's fixed private Skill Creator resource builder. The server has "
        "already frozen the target, path, dependencies, segment index, and limits. Generate "
        "only that target. Do not invent domain facts: use accepted sources, state assumptions, "
        "or implement a conservative fail-closed behavior. Continue existing_segments without "
        "repeating them. One JSON result only. Each segment is at most 8 KiB. Set complete=false "
        "when more content is necessary. Honor target feedback. If validation_issues are present, "
        "correct each issue instead of repeating the rejected result. Use Simplified Chinese "
        "for human-readable guidance by default, even when source evidence is English. Use "
        "another primary language only when the frozen Skill definition explicitly requests it. "
        "Preserve code, commands, identifiers, paths, proper nouns, and fixed enum values."
    )
    if target_id == "SKILL.md":
        specifics = (
            " Write SKILL.md last with strict frontmatter, then copy skill.name and "
            "skill.description from the frozen input context verbatim into "
            "frontmatter; never paraphrase, fold, or otherwise rewrite either value. Then write "
            "sections: Purpose and scope, Inputs and preconditions, Workflow with at least four "
            "numbered executable steps, Output contract, Failure and degradation, Quality checks, "
            "and Resources. Reference every accepted resource by exact path. Quality checks must "
            "verify the final deliverable against the output contract and confirmed success criteria; "
            "resource navigation or path checks alone are not sufficient. For multiple or long "
            "references include a bounded rg or sandbox_search_files command. Explain when scripts "
            "run and when assets are copied or rendered. If confirmed_hooks is non-empty, add a "
            "Hook section explaining each exact event boundary, affected tool names, annotation or "
            "blocking behavior, failure degradation, and that Hooks receive no network, Shell, or "
            "extra Agent tool permission. The Runtime, not the Agent, automatically executes each "
            "Hook script at its frozen event boundary. Never instruct the Agent to locate, stage, "
            "shell, or manually run a Hook script, and never report that the script is missing when "
            "answering an advisory request. For a request that does not itself trigger the affected "
            "tool, explain the frozen deterministic rule directly and ask only for genuinely missing "
            "inputs. When an advisory request supplies a path, apply path and extension rules to the "
            "path string and return the verdict without checking whether the file exists or reading "
            "Sandbox files, unless the frozen Skill purpose explicitly requires content inspection. "
            "The final package validator requires an explicit advisory/outside-event sentence; do "
            "not imply that every user question must execute the Hook script. "
            "Distinguish advisory guidance from enforcement performed by the Runtime Hook. "
            "Describe only checks actually implemented by the accepted script. Return one "
            "complete document from the opening frontmatter through the final line; never append "
            "a correction or repeat a section heading. Do not add README/eval/evals/user-meta."
        )
    elif kind == "reference":
        specifics = " Write focused factual guidance, not a copy of SKILL.md. If the final file exceeds 100 lines, include a concise table of contents near the top."
    elif kind == "script":
        specifics = (
            " Write a deterministic Python or JavaScript CLI with explicit arguments, stable "
            "UTF-8 output, conservative validation, and non-zero failure exit. On the final "
            "response return exactly one complete program: never append a prior draft, repeat "
            "a shebang, or repeat a main entry point. "
            "segment include one to three offline script_tests in the required JSON field. "
            "The Sidecar does not pipe fixture content to stdin: a script may support stdin, "
            "but every generated offline test must pass its fixture through an explicit UTF-8 "
            "file-path argument that the script actually reads. "
            "Each test uses fixtures as a JSON array of at most eight objects shaped exactly "
            "{\"path\":\"case.txt\",\"content\":\"UTF-8 text\"}. Fixture paths are rooted "
            "under inputs/; arguments run from work/, so refer to a fixture as "
            "../inputs/<path>."
        )
        if target.get("bound_hooks"):
            specifics += (
                " This script is also a confirmed typed Hook implementation. It must accept only "
                "the server-appended option-value pairs --context <path> --result <path>; parse "
                "both named options instead of assuming fixed argv positions or a three-item argv. "
                "Read strict UTF-8 JSON from context. The root contains version, event, skill, "
                "hook, and an event payload. hook is an object shaped exactly like "
                '{"hook_id":"check_release","mode":"guard"}; read the ID from '
                "context['hook']['hook_id'] and never compare context['hook'] itself to a string. "
                "For pre_tool_use the payload shape is exactly like "
                '{"tool":{"name":"sandbox_write_file","arguments":{"path":"work/release/example.md"}}}; '
                "read the path from context['tool']['arguments']['path'], never from flat "
                "tool_name or tool_args fields. For post_tool_use, tool contains name, success, "
                "content_types, output_length, output_digest, and artifact_paths. For session "
                "events, read the session object instead of tool. Atomically write a result whose "
                "root has exactly version and outputs. Use "
                '{"version":"modelmirror-hook-result-v1","outputs":[{"type":"validation",'
                '"code":"release_allowed","passed":true,"message":"Allowed."}]} for an allowed '
                "validation or guard call; guard + pre_tool_use rejects with "
                '{"version":"modelmirror-hook-result-v1","outputs":[{"type":"deny",'
                '"code":"release_denied","message":"Denied."}]}. Build a rejected guard output '
                "as a fresh deny object with exactly type, code, and message; never mutate a "
                "validation object into deny and leave passed behind. Annotation outputs use exactly "
                "type, code, severity, and message. Validation outputs use exactly type, code, "
                "passed, and message. Deny outputs use exactly type, code, and message. "
                "Honor every bound_hooks event/mode/tool boundary. Annotation returns annotation "
                "outputs, validation returns typed passed decisions, and guard returns a passed "
                "validation for an allowed call or a deny for a rejected pre_tool_use call. Do not "
                "broaden a Hook beyond the condition stated in its purpose: when a directory, "
                "artifact class, or other scope makes the rule inapplicable, return an explicit "
                "passed validation instead of enforcing the scoped rule on every matching tool. "
                "sandbox_write_file exposes the actual Sandbox-relative path and only writes under "
                "work/; therefore a user-facing release/ scope is represented as work/release/ in "
                "Hook context. Treat the leading work/ as the Sandbox transport root when applying "
                "the frozen directory rule, and never let it make an in-scope path bypass validation. "
                "Do not "
                "print the typed result to stdout, modify tool "
                "arguments, request permissions, use network access, or depend on host paths."
            )
    else:
        specifics = " Write a reusable UTF-8 template or boilerplate. Make placeholders and copy/render instructions explicit; do not use the asset as a knowledge reference."
    schema = (
        f' Return exactly {{"resource_build_version":"{RESOURCE_BUILD_VERSION}",'
        f'"target_id":{json.dumps(target_id)},"segment_index":<current integer>,'
        '"content":"segment text","complete":true|false,"script_tests":[]}. '
        "Only a completed script may populate script_tests. Every test must use this exact "
        'shape: {"test_id":"case_1","args":["../inputs/case.txt"],'
        '"fixtures":[{"path":"case.txt","content":"UTF-8 text"}],'
        '"expected_exit_code":0,"stdout_contains":["expected text"],'
        '"stderr_contains":[]}. args, fixtures, stdout_contains, and stderr_contains are always '
        "JSON arrays, including when empty. A completed script defines one to three tests; "
        "each test has at most 16 non-empty args, at most eight fixtures, and at most ten "
        "non-empty strings in each stdout_contains or stderr_contains array. Keep assertions "
        "minimal and observable instead of listing every output line."
    )
    return common + specifics + schema


def _issue(code: str, message: str, path: str) -> dict[str, Any]:
    return {"code": code, "message": message, "path": path, "severity": "error"}


def _has_hook_advisory_guidance(markdown: str) -> bool:
    return bool(
        re.search(
            r"(?i)(advisory|without triggering|does not (?:itself )?trigger|"
            r"outside (?:the )?(?:hook|event)|未触发|不触发|非.{0,8}事件|"
            r"咨询|仅询问|只询问)",
            markdown,
        )
    )


def _substantive_paragraphs(value: str) -> set[str]:
    return {
        re.sub(r"\s+", " ", paragraph).strip().lower()
        for paragraph in re.split(r"\n\s*\n", value)
        if len(re.sub(r"\s+", " ", paragraph).strip()) >= 200
    }


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "RESOURCE_BUILDER_WORKFLOW_VERSION",
    "SKILL_AUTHORING_PROFILE",
    "ResourceBuildGenerationRequest",
    "ResourceBuildSegment",
    "ResourceBuildWorkflowInvocation",
    "SandboxCreatorScriptRunner",
    "WorkflowCreatorResourceBuilder",
    "build_resource_builder_invocation",
    "parse_resource_build_segment",
    "validate_final_resource_package",
    "validate_resource_content",
]
