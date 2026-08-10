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
    ResourceScriptTestReceipt,
    ResourceScriptTestResult,
    SkillResourceBuild,
    SkillResourceBuildItem,
)
from .creator_runtime import CREATOR_WORKFLOW_VERSION
from .creator_store import CREATOR_ASSISTANT_AGENT_ID, SkillCreatorValidationError
from .package_validation import validate_skill_package

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
        "correct each issue instead of repeating the rejected result."
    )
    if target_id == "SKILL.md":
        specifics = (
            " Write SKILL.md last with strict frontmatter, then copy skill.name and "
            "skill.description from the frozen input context verbatim into "
            "frontmatter; never paraphrase, fold, or otherwise rewrite either value. Then write "
            "sections: Purpose and scope, Inputs and preconditions, Workflow with at least four "
            "numbered executable steps, Output contract, Failure and degradation, Quality checks, "
            "and Resources. Reference every accepted resource by exact path. For multiple or long "
            "references include a bounded rg or sandbox_search_files command. Explain when scripts "
            "run and when assets are copied or rendered. Do not add README/eval/evals/user-meta."
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
