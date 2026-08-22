from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable, Literal, Mapping


HOOK_MANIFEST_PATH = "hooks/manifest.json"
HOOK_MANIFEST_VERSION = "modelmirror-hook-manifest-v2"
HOOK_RESULT_VERSION = "modelmirror-hook-result-v1"
LEGACY_HOOK_MANIFEST_PATH = "modelmirror-hooks.json"

MAX_HOOKS_PER_SKILL = 12
MAX_HOOK_SKILLS_PER_NODE = 10
MAX_HOOKS_PER_EVENT = 20
MAX_HOOK_EVENT_BUDGET_SECONDS = 120
MAX_HOOK_OUTPUTS = 20
DEFAULT_HOOK_TIMEOUT_SECONDS = 15

HookEvent = Literal[
    "session_start", "pre_tool_use", "post_tool_use", "session_end"
]
HookMode = Literal["annotation", "validation", "guard"]
HookOutputType = Literal["annotation", "validation", "deny"]
HookSeverity = Literal["info", "warning"]

_HOOK_EVENTS = {
    "session_start",
    "pre_tool_use",
    "post_tool_use",
    "session_end",
}
_HOOK_MODES = {"annotation", "validation", "guard"}
_HOOK_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,79}$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,119}$")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class SkillHookContractError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "skill_hook_manifest_invalid",
        path: str = HOOK_MANIFEST_PATH,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = path


@dataclass(frozen=True, slots=True)
class SkillHookDefinitionV2:
    hook_id: str
    event: HookEvent
    mode: HookMode
    tool_names: tuple[str, ...]
    script_path: str
    purpose: str
    acceptance_checks: tuple[str, ...]
    timeout_seconds: int = DEFAULT_HOOK_TIMEOUT_SECONDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "hook_id": self.hook_id,
            "event": self.event,
            "mode": self.mode,
            "tool_names": list(self.tool_names),
            "script_path": self.script_path,
            "purpose": self.purpose,
            "acceptance_checks": list(self.acceptance_checks),
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True, slots=True)
class SkillHookManifestV2:
    version: str
    hooks: tuple[SkillHookDefinitionV2, ...]
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "hooks": [hook.to_dict() for hook in self.hooks],
        }


@dataclass(frozen=True, slots=True)
class SkillHookResultOutputV1:
    output_type: HookOutputType
    code: str
    message: str
    severity: HookSeverity | None = None
    passed: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": self.output_type,
            "code": self.code,
            "message": self.message,
        }
        if self.severity is not None:
            result["severity"] = self.severity
        if self.passed is not None:
            result["passed"] = self.passed
        return result


@dataclass(frozen=True, slots=True)
class SkillHookResultV1:
    version: str
    outputs: tuple[SkillHookResultOutputV1, ...]


def skill_plugin_hook_v2_enabled() -> bool:
    return os.getenv("SKILL_PLUGIN_HOOK_V2_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def parse_hook_manifest(
    content: str | bytes,
    *,
    available_paths: Iterable[str] = (),
) -> SkillHookManifestV2:
    payload = _load_json_object(content, label="Hook manifest")
    _require_exact_keys(payload, {"version", "hooks"}, label="Hook manifest")
    if payload.get("version") != HOOK_MANIFEST_VERSION:
        raise SkillHookContractError("Hook manifest version is unsupported.")
    raw_hooks = payload.get("hooks")
    if not isinstance(raw_hooks, list) or not raw_hooks:
        raise SkillHookContractError("Hook manifest must declare at least one Hook.")
    if len(raw_hooks) > MAX_HOOKS_PER_SKILL:
        raise SkillHookContractError(
            f"A Skill can declare at most {MAX_HOOKS_PER_SKILL} Hooks."
        )
    known_paths = {_normalize_package_path(path) for path in available_paths}
    hooks: list[SkillHookDefinitionV2] = []
    seen_ids: set[str] = set()
    for index, raw_hook in enumerate(raw_hooks):
        if not isinstance(raw_hook, dict):
            raise SkillHookContractError(f"Hook {index + 1} must be an object.")
        hook_id = raw_hook.get("hook_id")
        if not isinstance(hook_id, str) or not _HOOK_ID_RE.fullmatch(hook_id):
            raise SkillHookContractError(f"Hook {index + 1} has an invalid hook_id.")
        if hook_id in seen_ids:
            raise SkillHookContractError("Hook IDs must be unique.")
        seen_ids.add(hook_id)
        event = raw_hook.get("event")
        mode = raw_hook.get("mode")
        if event not in _HOOK_EVENTS or mode not in _HOOK_MODES:
            raise SkillHookContractError(
                f"Hook '{hook_id}' has an invalid event or mode."
            )
        if mode == "guard" and event != "pre_tool_use":
            raise SkillHookContractError(
                f"Guard Hook '{hook_id}' must use pre_tool_use."
            )
        expected_keys = {
            "hook_id",
            "event",
            "mode",
            "script_path",
            "purpose",
            "acceptance_checks",
        }
        if event in {"pre_tool_use", "post_tool_use"}:
            expected_keys.add("tool_names")
        if "timeout_seconds" in raw_hook:
            expected_keys.add("timeout_seconds")
        _require_exact_keys(raw_hook, expected_keys, label=f"Hook {index + 1}")
        tool_names = (
            _tool_names(raw_hook.get("tool_names"), hook_id=hook_id)
            if event in {"pre_tool_use", "post_tool_use"}
            else ()
        )
        if event in {"pre_tool_use", "post_tool_use"} and not tool_names:
            raise SkillHookContractError(
                f"Tool Hook '{hook_id}' must name at least one exact tool."
            )
        script_path = _script_path(raw_hook.get("script_path"), hook_id=hook_id)
        if script_path not in known_paths:
            raise SkillHookContractError(
                f"Hook '{hook_id}' references a missing script.", path=script_path
            )
        purpose = _bounded_text(raw_hook.get("purpose"), label="purpose", limit=1000)
        checks = _bounded_text_list(
            raw_hook.get("acceptance_checks"),
            label="acceptance_checks",
            max_items=20,
            item_limit=500,
        )
        timeout = raw_hook.get("timeout_seconds", DEFAULT_HOOK_TIMEOUT_SECONDS)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 60:
            raise SkillHookContractError(
                f"Hook '{hook_id}' timeout_seconds must be between 1 and 60."
            )
        hooks.append(
            SkillHookDefinitionV2(
                hook_id=hook_id,
                event=event,
                mode=mode,
                tool_names=tool_names,
                script_path=script_path,
                purpose=purpose,
                acceptance_checks=checks,
                timeout_seconds=timeout,
            )
        )
    manifest_payload = {
        "version": HOOK_MANIFEST_VERSION,
        "hooks": [hook.to_dict() for hook in hooks],
    }
    return SkillHookManifestV2(
        version=HOOK_MANIFEST_VERSION,
        hooks=tuple(hooks),
        fingerprint=_sha256_json(manifest_payload),
    )


def parse_hook_result(
    content: str | bytes,
    *,
    hook_event: HookEvent,
    hook_mode: HookMode,
) -> SkillHookResultV1:
    if hook_event not in _HOOK_EVENTS or hook_mode not in _HOOK_MODES:
        raise SkillHookContractError(
            "Hook result boundary is invalid.", code="skill_hook_result_invalid"
        )
    payload = _load_json_object(
        content, label="Hook result", code="skill_hook_result_invalid"
    )
    _require_exact_keys(
        payload,
        {"version", "outputs"},
        label="Hook result",
        code="skill_hook_result_invalid",
    )
    if payload.get("version") != HOOK_RESULT_VERSION:
        raise SkillHookContractError(
            "Hook result version is unsupported.", code="skill_hook_result_invalid"
        )
    raw_outputs = payload.get("outputs")
    if not isinstance(raw_outputs, list) or len(raw_outputs) > MAX_HOOK_OUTPUTS:
        raise SkillHookContractError(
            f"Hook result outputs must be a list of at most {MAX_HOOK_OUTPUTS} items.",
            code="skill_hook_result_invalid",
        )
    outputs: list[SkillHookResultOutputV1] = []
    seen_codes: set[str] = set()
    for index, raw_output in enumerate(raw_outputs):
        if not isinstance(raw_output, dict):
            raise SkillHookContractError(
                f"Hook output {index + 1} must be an object.",
                code="skill_hook_result_invalid",
            )
        output_type = raw_output.get("type")
        expected_keys = {
            "annotation": {"type", "code", "severity", "message"},
            "validation": {"type", "code", "passed", "message"},
            "deny": {"type", "code", "message"},
        }.get(output_type)
        if expected_keys is None:
            raise SkillHookContractError(
                f"Hook output {index + 1} has an unknown type.",
                code="skill_hook_result_invalid",
            )
        _require_exact_keys(
            raw_output,
            expected_keys,
            label=f"Hook output {index + 1}",
            code="skill_hook_result_invalid",
        )
        code_value = raw_output.get("code")
        if not isinstance(code_value, str) or not _CODE_RE.fullmatch(code_value):
            raise SkillHookContractError(
                f"Hook output {index + 1} has an invalid code.",
                code="skill_hook_result_invalid",
            )
        if code_value in seen_codes:
            raise SkillHookContractError(
                "Hook output codes must be unique.", code="skill_hook_result_invalid"
            )
        seen_codes.add(code_value)
        message = _bounded_text(
            raw_output.get("message"),
            label="message",
            limit=1000,
            code="skill_hook_result_invalid",
        )
        severity: HookSeverity | None = None
        passed: bool | None = None
        if output_type == "annotation":
            if hook_mode != "annotation":
                raise SkillHookContractError(
                    "Only annotation Hooks can return annotations.",
                    code="skill_hook_result_invalid",
                )
            severity_value = raw_output.get("severity")
            if severity_value not in {"info", "warning"}:
                raise SkillHookContractError(
                    "Hook annotation severity is invalid.",
                    code="skill_hook_result_invalid",
                )
            severity = severity_value
        elif output_type == "validation":
            if hook_mode not in {"validation", "guard"}:
                raise SkillHookContractError(
                    "Only validation or guard Hooks can return validations.",
                    code="skill_hook_result_invalid",
                )
            passed_value = raw_output.get("passed")
            if not isinstance(passed_value, bool):
                raise SkillHookContractError(
                    "Hook validation passed must be boolean.",
                    code="skill_hook_result_invalid",
                )
            passed = passed_value
        elif hook_mode != "guard" or hook_event != "pre_tool_use":
            raise SkillHookContractError(
                "Deny outputs are only valid for guard pre_tool_use Hooks.",
                code="skill_hook_result_invalid",
            )
        outputs.append(
            SkillHookResultOutputV1(
                output_type=output_type,
                code=code_value,
                message=message,
                severity=severity,
                passed=passed,
            )
        )
    if hook_mode in {"validation", "guard"} and not outputs:
        raise SkillHookContractError(
            "Validation and guard Hooks must return a typed decision.",
            code="skill_hook_result_invalid",
        )
    return SkillHookResultV1(version=HOOK_RESULT_VERSION, outputs=tuple(outputs))


def hook_capability_projection(
    content: str | bytes | None,
    *,
    available_paths: Iterable[str] = (),
) -> dict[str, Any]:
    if content is None:
        return {
            "available": False,
            "manifestVersion": None,
            "manifestFingerprint": None,
            "hookCount": 0,
            "events": [],
            "modes": [],
            "contractValid": False,
            "runnable": False,
            "errorCode": None,
        }
    try:
        manifest = parse_hook_manifest(content, available_paths=available_paths)
    except SkillHookContractError as exc:
        return {
            "available": True,
            "manifestVersion": None,
            "manifestFingerprint": None,
            "hookCount": 0,
            "events": [],
            "modes": [],
            "contractValid": False,
            "runnable": False,
            "errorCode": exc.code,
        }
    return {
        "available": True,
        "manifestVersion": manifest.version,
        "manifestFingerprint": manifest.fingerprint,
        "hookCount": len(manifest.hooks),
        "events": sorted({hook.event for hook in manifest.hooks}),
        "modes": sorted({hook.mode for hook in manifest.hooks}),
        "contractValid": True,
        "runnable": skill_plugin_hook_v2_enabled(),
        "errorCode": None,
    }


def _load_json_object(
    content: str | bytes,
    *,
    label: str,
    code: str = "skill_hook_manifest_invalid",
) -> dict[str, Any]:
    try:
        text = content.decode("utf-8", errors="strict") if isinstance(content, bytes) else content
        if not isinstance(text, str):
            raise TypeError
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SkillHookContractError(
            f"{label} must be strict UTF-8 JSON.", code=code
        ) from exc
    if not isinstance(payload, dict):
        raise SkillHookContractError(f"{label} must be an object.", code=code)
    return payload


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _require_exact_keys(
    payload: Mapping[str, Any],
    expected: set[str],
    *,
    label: str,
    code: str = "skill_hook_manifest_invalid",
) -> None:
    if set(payload) != expected:
        raise SkillHookContractError(
            f"{label} fields do not match the fixed contract.", code=code
        )


def _normalize_package_path(value: str) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\\", "/")


def _script_path(value: Any, *, hook_id: str) -> str:
    if not isinstance(value, str):
        raise SkillHookContractError(f"Hook '{hook_id}' script_path is invalid.")
    normalized = posixpath.normpath(value)
    path = PurePosixPath(value)
    if (
        value != normalized
        or value.startswith("/")
        or "\\" in value
        or ".." in path.parts
        or any(not part or part.startswith(".") for part in path.parts)
        or not path.parts
        or path.parts[0] != "scripts"
        or path.suffix.casefold() not in {".py", ".js"}
    ):
        raise SkillHookContractError(f"Hook '{hook_id}' script_path is invalid.")
    return value


def _tool_names(value: Any, *, hook_id: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 64:
        raise SkillHookContractError(f"Hook '{hook_id}' tool_names is invalid.")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _TOOL_NAME_RE.fullmatch(item):
            raise SkillHookContractError(
                f"Hook '{hook_id}' must use exact tool names."
            )
        result.append(item)
    if len(set(result)) != len(result):
        raise SkillHookContractError(f"Hook '{hook_id}' repeats a tool name.")
    return tuple(sorted(result))


def _bounded_text(
    value: Any,
    *,
    label: str,
    limit: int,
    code: str = "skill_hook_manifest_invalid",
) -> str:
    if not isinstance(value, str) or value != value.strip() or not value or len(value) > limit:
        raise SkillHookContractError(f"Hook {label} is invalid.", code=code)
    return value


def _bounded_text_list(
    value: Any,
    *,
    label: str,
    max_items: int,
    item_limit: int,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > max_items:
        raise SkillHookContractError(f"Hook {label} is invalid.")
    result = tuple(
        _bounded_text(item, label=label, limit=item_limit) for item in value
    )
    if len(set(result)) != len(result):
        raise SkillHookContractError(f"Hook {label} contains duplicates.")
    return result


def _sha256_json(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "DEFAULT_HOOK_TIMEOUT_SECONDS",
    "HOOK_MANIFEST_PATH",
    "HOOK_MANIFEST_VERSION",
    "HOOK_RESULT_VERSION",
    "LEGACY_HOOK_MANIFEST_PATH",
    "MAX_HOOK_EVENT_BUDGET_SECONDS",
    "MAX_HOOK_OUTPUTS",
    "MAX_HOOK_SKILLS_PER_NODE",
    "MAX_HOOKS_PER_EVENT",
    "MAX_HOOKS_PER_SKILL",
    "SkillHookContractError",
    "SkillHookDefinitionV2",
    "SkillHookManifestV2",
    "SkillHookResultOutputV1",
    "SkillHookResultV1",
    "hook_capability_projection",
    "parse_hook_manifest",
    "parse_hook_result",
    "skill_plugin_hook_v2_enabled",
]
