from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
import posixpath
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal, Mapping
from urllib.parse import unquote, urlsplit

import yaml
from yaml.nodes import MappingNode
from yaml.tokens import AliasToken, AnchorToken


VALIDATOR_VERSION = "skill-package-v2.1"

MAX_FILES = 40
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 5 * 1024 * 1024
MAX_PATH_CHARS = 240
MAX_PATH_SEGMENT_BYTES = 255
ALLOWED_ROOTS = frozenset({"scripts", "references", "assets", "agents"})
SUPPORTED_FRONTMATTER_FIELDS = frozenset(
    {
        "name",
        "description",
        "license",
        "compatibility",
        "metadata",
        "allowed-tools",
    }
)

IssueSeverity = Literal["error", "warning"]


@dataclass(frozen=True, slots=True)
class SkillPackageIssue:
    code: str
    message: str
    severity: IssueSeverity = "error"
    path: str | None = None
    field: str | None = None
    line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "code": self.code,
                "message": self.message,
                "severity": self.severity,
                "path": self.path,
                "field": self.field,
                "line": self.line,
            }.items()
            if value is not None
        }


@dataclass(frozen=True, slots=True)
class SkillPackageV2:
    """A validated, UTF-8-only Skill package.

    ``allowed_tools`` is retained as package metadata. It never grants runtime
    permissions; ModelMirror's runtime policy remains authoritative.
    """

    root_name: str
    name: str
    description: str
    skill_markdown: str
    files: dict[str, str]
    content_digest: str
    file_count: int
    total_bytes: int
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, Any] | None = None
    allowed_tools: tuple[str, ...] = ()
    version: int = 2

    def to_dict(self, *, include_content: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "version": self.version,
            "root_name": self.root_name,
            "name": self.name,
            "description": self.description,
            "license": self.license,
            "compatibility": self.compatibility,
            "metadata": copy.deepcopy(self.metadata or {}),
            "allowed_tools": list(self.allowed_tools),
            "content_digest": self.content_digest,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
        }
        if include_content:
            result["skill_markdown"] = self.skill_markdown
            result["files"] = dict(self.files)
        else:
            result["file_paths"] = ["SKILL.md", *sorted(self.files)]
        return result


@dataclass(frozen=True, slots=True)
class SkillPackageValidationResult:
    valid: bool
    issues: tuple[SkillPackageIssue, ...]
    package: SkillPackageV2 | None
    file_count: int = 0
    total_bytes: int = 0
    validator_version: str = VALIDATOR_VERSION

    @property
    def content_digest(self) -> str | None:
        return self.package.content_digest if self.package else None

    def to_dict(self, *, include_package: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "valid": self.valid,
            "validator_version": self.validator_version,
            "issues": [issue.to_dict() for issue in self.issues],
            "content_digest": self.content_digest,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
        }
        if include_package and self.package is not None:
            result["package"] = self.package.to_dict()
        return result


class _DuplicateYamlKeyError(yaml.YAMLError):
    pass


class _StrictSafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _StrictSafeLoader, node: MappingNode, deep: bool = False
) -> dict[Any, Any]:
    if not isinstance(node, MappingNode):
        raise yaml.constructor.ConstructorError(
            None, None, "expected a mapping", node.start_mark
        )
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise _DuplicateYamlKeyError("duplicate mapping key")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]\n]*\]\(([^)\n]+)\)")
_FENCED_CODE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_INLINE_RESOURCE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9._+@/-])(?P<path>(?:scripts|references|assets|agents)/"
    r"[A-Za-z0-9][A-Za-z0-9._+@/-]*\.[A-Za-z0-9]{1,12}"
    r"(?:#[A-Za-z0-9._-]+)?)(?=$|[\s'\"),;:])"
)
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[/\\]")
_WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "clock$"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)
_JS_NON_CODE_RE = re.compile(
    r"//[^\n]*|/\*.*?\*/|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`",
    re.DOTALL,
)
_JS_MISSING_ASSIGNMENT_RE = re.compile(
    r"\b(?:const|let|var)\s+[A-Za-z_$][A-Za-z0-9_$]*\s*=(?!=|>)"
    r"\s*(?=[;,\n\}]|$)",
    re.MULTILINE,
)
_JS_MISSING_DECLARATION_NAME_RE = re.compile(
    r"\b(?:const|let|var)\s*=(?!=|>)"
)

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"
)
_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bgh[opurs]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b[A-Za-z0-9_-]*(?:api[_-]?key|access[_-]?key|secret(?:[_-]?key)?|password|passwd|"
    r"(?:auth|access|refresh|private)[_-]?token|client[_-]?secret|token)\b\s*[:=]\s*"
    r"(?P<value>[^\s,;#]+)"
)
_CREDENTIAL_URL_RE = re.compile(
    r"(?i)\b(?:https?|ssh|ftp)://[^\s/:@]+:[^\s/@]+@[^\s]+"
)
_PLACEHOLDER_PARTS = (
    "placeholder",
    "example",
    "changeme",
    "replace-me",
    "replace_me",
    "your-",
    "your_",
    "<",
    "${",
    "{{",
    "os.environ",
    "process.env",
    "getenv(",
    "redacted",
    "dummy",
    "test-only",
)


def validate_skill_package(
    *,
    root_name: Any,
    skill_markdown: Any,
    files: Any,
) -> SkillPackageValidationResult:
    """Validate untrusted Skill package content without executing it.

    Validation failures are returned as structured issues. The result only
    exposes package content when every blocking check has passed, which keeps
    detected credentials out of downstream snapshots and logs by default.
    """

    issues: list[SkillPackageIssue] = []
    clean_root = _validate_root_name(root_name, issues)

    markdown_text, markdown_bytes = _decode_utf8_text(
        skill_markdown, path="SKILL.md", issues=issues
    )
    raw_files = files if isinstance(files, Mapping) else None
    if raw_files is None:
        issues.append(
            SkillPackageIssue(
                code="package_files_type",
                message="Skill package files must be a path-to-text mapping.",
                field="files",
            )
        )
        raw_files = {}
    elif len(raw_files) + 1 > MAX_FILES:
        issues.append(
            SkillPackageIssue(
                code="package_file_count_exceeded",
                message=f"A Skill package can contain at most {MAX_FILES} files.",
                field="files",
            )
        )

    clean_files: dict[str, str] = {}
    content_bytes: dict[str, bytes] = {}
    seen_paths: dict[str, str] = {_path_identity("SKILL.md"): "SKILL.md"}
    if clean_root is not None:
        _scan_credentials("skill-root", clean_root, issues)
    if markdown_text is not None and markdown_bytes is not None:
        content_bytes["SKILL.md"] = markdown_bytes
        if not markdown_text.strip():
            issues.append(
                SkillPackageIssue(
                    code="skill_markdown_empty",
                    message="SKILL.md must not be empty.",
                    path="SKILL.md",
                )
            )
        if len(markdown_bytes) > MAX_FILE_BYTES:
            issues.append(
                SkillPackageIssue(
                    code="file_size_exceeded",
                    message=f"Each Skill file is limited to {MAX_FILE_BYTES} bytes.",
                    path="SKILL.md",
                )
            )

    sortable_items: list[tuple[str, Any]] = []
    for raw_path, raw_content in raw_files.items():
        sortable_items.append((raw_path if isinstance(raw_path, str) else "", (raw_path, raw_content)))
    sortable_items.sort(key=lambda item: item[0])
    for _, (raw_path, raw_content) in sortable_items:
        path_credential_issues: list[SkillPackageIssue] = []
        if isinstance(raw_path, str):
            _scan_credentials(None, raw_path, path_credential_issues)
        if path_credential_issues:
            issues.extend(path_credential_issues)
            continue
        path = _validate_file_path(raw_path, issues)
        if path is None:
            continue
        identity = _path_identity(path)
        previous = seen_paths.get(identity)
        if previous is not None:
            issues.append(
                SkillPackageIssue(
                    code="file_path_case_collision",
                    message="Skill package paths must be unique across case and Unicode normalization.",
                    path=path,
                    field="files",
                )
            )
            continue
        seen_paths[identity] = path
        text, encoded = _decode_utf8_text(raw_content, path=path, issues=issues)
        if text is None or encoded is None:
            continue
        if len(encoded) > MAX_FILE_BYTES:
            issues.append(
                SkillPackageIssue(
                    code="file_size_exceeded",
                    message=f"Each Skill file is limited to {MAX_FILE_BYTES} bytes.",
                    path=path,
                )
            )
        clean_files[path] = text
        content_bytes[path] = encoded

    _check_file_directory_conflicts(content_bytes, issues)
    total_bytes = sum(len(value) for value in content_bytes.values())
    attempted_file_count = 1 + len(raw_files)
    if total_bytes > MAX_TOTAL_BYTES:
        issues.append(
            SkillPackageIssue(
                code="package_size_exceeded",
                message=f"A Skill package is limited to {MAX_TOTAL_BYTES} bytes.",
            )
        )

    frontmatter: dict[str, Any] | None = None
    issues.extend(
        scan_skill_package_credentials(
            skill_markdown=markdown_text,
            files=clean_files,
        )
    )
    if markdown_text is not None:
        frontmatter = _parse_skill_frontmatter(markdown_text, issues)
    for path, text in clean_files.items():
        _check_static_syntax(path, text, issues)

    parsed = _validate_frontmatter(frontmatter, clean_root, issues)
    if markdown_text is not None:
        all_text = {"SKILL.md": markdown_text, **clean_files}
        _check_local_references(all_text, issues)

    blocking = any(issue.severity == "error" for issue in issues)
    if blocking or markdown_text is None or parsed is None or clean_root is None:
        return SkillPackageValidationResult(
            valid=False,
            issues=tuple(issues),
            package=None,
            file_count=attempted_file_count,
            total_bytes=total_bytes,
        )

    digest = compute_skill_content_digest(content_bytes)
    package = SkillPackageV2(
        root_name=clean_root,
        name=parsed["name"],
        description=parsed["description"],
        license=parsed["license"],
        compatibility=parsed["compatibility"],
        metadata=copy.deepcopy(parsed["metadata"]),
        allowed_tools=parsed["allowed_tools"],
        skill_markdown=markdown_text,
        files=dict(clean_files),
        content_digest=digest,
        file_count=len(content_bytes),
        total_bytes=total_bytes,
    )
    return SkillPackageValidationResult(
        valid=True,
        issues=tuple(issues),
        package=package,
        file_count=package.file_count,
        total_bytes=package.total_bytes,
    )


def compute_skill_content_digest(files: Mapping[str, str | bytes]) -> str:
    """Hash canonical paths and exact UTF-8 bytes with unambiguous framing."""

    encoded_files: list[tuple[bytes, bytes]] = []
    for path, content in files.items():
        path_bytes = str(path).encode("utf-8")
        content_bytes = content if isinstance(content, bytes) else content.encode("utf-8")
        encoded_files.append((path_bytes, content_bytes))
    encoded_files.sort(key=lambda item: item[0])
    digest = hashlib.sha256(b"modelmirror-skill-package-v2\0")
    for path_bytes, content_bytes in encoded_files:
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(content_bytes).to_bytes(8, "big"))
        digest.update(content_bytes)
    return digest.hexdigest()


def compute_package_digest(
    skill_markdown: str | bytes, files: Mapping[str, str | bytes]
) -> str:
    """Compute the one canonical digest for a Skill package's content."""

    if any(_path_identity(str(path)) == _path_identity("SKILL.md") for path in files):
        raise ValueError("files must not contain SKILL.md")
    return compute_skill_content_digest({"SKILL.md": skill_markdown, **dict(files)})


# Descriptive compatibility alias for callers that include the domain in names.
compute_skill_package_digest = compute_package_digest


def scan_skill_package_credentials(
    *,
    skill_markdown: Any = None,
    files: Any = None,
) -> tuple[SkillPackageIssue, ...]:
    """Scan complete or partial package content without returning raw values."""

    issues: list[SkillPackageIssue] = []
    markdown_text = _credential_scan_text(skill_markdown)
    if markdown_text is not None:
        _scan_credentials("SKILL.md", markdown_text, issues)
    if isinstance(files, Mapping):
        sortable: list[tuple[str, Any, Any]] = []
        for raw_path, raw_content in files.items():
            sort_key = raw_path if isinstance(raw_path, str) else ""
            sortable.append((sort_key, raw_path, raw_content))
        for _, raw_path, raw_content in sorted(sortable, key=lambda item: item[0]):
            if isinstance(raw_path, str):
                _scan_credentials(None, raw_path, issues)
            content = _credential_scan_text(raw_content)
            if content is None:
                continue
            path = _credential_issue_path(raw_path)
            _scan_credentials(path, content, issues)
    return tuple(issues)


def _credential_scan_text(value: Any) -> str | None:
    if isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            return None
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None
    return None


def _credential_issue_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 512:
        return None
    if any(ord(character) < 32 for character in value):
        return None
    path_issues: list[SkillPackageIssue] = []
    _scan_credentials(None, value, path_issues)
    if path_issues:
        return None
    return value


def _validate_root_name(value: Any, issues: list[SkillPackageIssue]) -> str | None:
    if not isinstance(value, str):
        issues.append(
            SkillPackageIssue(
                code="root_name_type",
                message="Skill root directory name must be text.",
                field="root_name",
            )
        )
        return None
    root = value.strip()
    if root != value or not _SKILL_NAME_RE.fullmatch(root) or len(root) > 64:
        issues.append(
            SkillPackageIssue(
                code="root_name_invalid",
                message="Skill root directory must be 1-64 lowercase letters, digits, or hyphen-separated words.",
                field="root_name",
            )
        )
        return None
    if _is_windows_reserved_segment(root):
        issues.append(
            SkillPackageIssue(
                code="root_name_windows_reserved",
                message="Skill root directory uses a Windows-reserved name.",
                field="root_name",
            )
        )
        return None
    return root


def _decode_utf8_text(
    value: Any,
    *,
    path: str,
    issues: list[SkillPackageIssue],
) -> tuple[str | None, bytes | None]:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="strict"), value
        except UnicodeDecodeError:
            issues.append(
                SkillPackageIssue(
                    code="file_invalid_utf8",
                    message="Skill package files must contain valid UTF-8 text.",
                    path=path,
                )
            )
            return None, None
    if not isinstance(value, str):
        issues.append(
            SkillPackageIssue(
                code="file_content_type",
                message="Skill package files must contain UTF-8 text.",
                path=path,
            )
        )
        return None, None
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        issues.append(
            SkillPackageIssue(
                code="file_invalid_utf8",
                message="Skill package files must contain valid UTF-8 text.",
                path=path,
            )
        )
        return None, None
    return value, encoded


def _validate_file_path(
    value: Any, issues: list[SkillPackageIssue]
) -> str | None:
    if not isinstance(value, str):
        issues.append(
            SkillPackageIssue(
                code="file_path_type",
                message="Skill package paths must be text.",
                field="files",
            )
        )
        return None
    if (
        not value
        or value != value.strip()
        or "\\" in value
        or value.startswith("/")
        or _WINDOWS_DRIVE_RE.match(value)
        or "\x00" in value
        or any(ord(character) < 32 for character in value)
    ):
        issues.append(
            SkillPackageIssue(
                code="file_path_unsafe",
                message="Skill package path is unsafe or non-canonical.",
                field="files",
            )
        )
        return None
    if len(value) > MAX_PATH_CHARS:
        issues.append(
            SkillPackageIssue(
                code="file_path_too_long",
                message=f"Skill package paths are limited to {MAX_PATH_CHARS} characters.",
                field="files",
            )
        )
        return None
    normalized = posixpath.normpath(value)
    path = PurePosixPath(value)
    if (
        normalized != value
        or normalized in {".", ".."}
        or ".." in path.parts
        or any(not part or part.startswith(".") for part in path.parts)
        or any(":" in part for part in path.parts)
        or not path.parts
        or path.parts[0] not in ALLOWED_ROOTS
    ):
        issues.append(
            SkillPackageIssue(
                code="file_path_unsafe",
                message="Skill package path is unsafe or non-canonical.",
                field="files",
            )
        )
        return None
    if any(
        part.endswith((".", " "))
        or len(part.encode("utf-8")) > MAX_PATH_SEGMENT_BYTES
        or _is_windows_reserved_segment(part)
        for part in path.parts
    ):
        issues.append(
            SkillPackageIssue(
                code="file_path_windows_unsafe",
                message="Skill package path is not portable to Windows filesystems.",
                field="files",
            )
        )
        return None
    if path.parts[0] == "agents" and value != "agents/openai.yaml":
        issues.append(
            SkillPackageIssue(
                code="agents_file_unsupported",
                message="Only agents/openai.yaml is supported under agents/.",
                path=value,
            )
        )
        return None
    return value


def _is_windows_reserved_segment(segment: str) -> bool:
    basename = segment.rstrip(". ").split(".", 1)[0].casefold()
    return basename in _WINDOWS_RESERVED_NAMES


def _path_identity(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def _check_file_directory_conflicts(
    content: Mapping[str, bytes], issues: list[SkillPackageIssue]
) -> None:
    identities = {_path_identity(path): path for path in content}
    for path in sorted(content):
        parts = PurePosixPath(path).parts
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if _path_identity(parent) in identities:
                issues.append(
                    SkillPackageIssue(
                        code="file_directory_conflict",
                        message="A package path cannot be both a file and a directory.",
                        path=path,
                    )
                )
                break


def _parse_skill_frontmatter(
    markdown: str, issues: list[SkillPackageIssue]
) -> dict[str, Any] | None:
    lines = markdown.splitlines()
    if not lines or lines[0] != "---":
        issues.append(
            SkillPackageIssue(
                code="frontmatter_missing",
                message="SKILL.md must begin with YAML frontmatter.",
                path="SKILL.md",
            )
        )
        return None
    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line == "---"),
        None,
    )
    if closing_index is None:
        issues.append(
            SkillPackageIssue(
                code="frontmatter_unterminated",
                message="SKILL.md YAML frontmatter must end with a standalone --- line.",
                path="SKILL.md",
            )
        )
        return None
    if not "\n".join(lines[closing_index + 1 :]).strip():
        issues.append(
            SkillPackageIssue(
                code="skill_body_empty",
                message="SKILL.md must contain instructions after its frontmatter.",
                path="SKILL.md",
            )
        )
    yaml_text = "\n".join(lines[1:closing_index])
    try:
        parsed = _strict_yaml_load(yaml_text)
    except _DuplicateYamlKeyError:
        issues.append(
            SkillPackageIssue(
                code="frontmatter_duplicate_key",
                message="SKILL.md frontmatter contains a duplicate YAML key.",
                path="SKILL.md",
            )
        )
        return None
    except (yaml.YAMLError, RecursionError):
        issues.append(
            SkillPackageIssue(
                code="frontmatter_invalid_yaml",
                message="SKILL.md frontmatter is not valid safe YAML.",
                path="SKILL.md",
            )
        )
        return None
    if not isinstance(parsed, dict):
        issues.append(
            SkillPackageIssue(
                code="frontmatter_type",
                message="SKILL.md frontmatter must be a YAML mapping.",
                path="SKILL.md",
            )
        )
        return None
    if any(not isinstance(key, str) for key in parsed):
        issues.append(
            SkillPackageIssue(
                code="frontmatter_key_type",
                message="SKILL.md frontmatter keys must be text.",
                path="SKILL.md",
            )
        )
        return None
    return parsed


def _validate_frontmatter(
    frontmatter: dict[str, Any] | None,
    root_name: str | None,
    issues: list[SkillPackageIssue],
) -> dict[str, Any] | None:
    if frontmatter is None:
        return None
    for key in sorted(frontmatter):
        if key not in SUPPORTED_FRONTMATTER_FIELDS:
            issues.append(
                SkillPackageIssue(
                    code="frontmatter_field_unsupported",
                    message="Unsupported frontmatter fields are ignored by ModelMirror.",
                    severity="warning",
                    path="SKILL.md",
                    field=key,
                )
            )

    name = frontmatter.get("name")
    if not isinstance(name, str):
        issues.append(
            SkillPackageIssue(
                code="frontmatter_name_type",
                message="Frontmatter name must be text.",
                path="SKILL.md",
                field="name",
            )
        )
    elif not _SKILL_NAME_RE.fullmatch(name) or len(name) > 64:
        issues.append(
            SkillPackageIssue(
                code="frontmatter_name_invalid",
                message="Frontmatter name must be 1-64 lowercase letters, digits, or hyphen-separated words.",
                path="SKILL.md",
                field="name",
            )
        )
    elif root_name is not None and name != root_name:
        issues.append(
            SkillPackageIssue(
                code="skill_name_root_mismatch",
                message="Frontmatter name must exactly match the Skill root directory.",
                path="SKILL.md",
                field="name",
            )
        )

    description = frontmatter.get("description")
    if not isinstance(description, str):
        issues.append(
            SkillPackageIssue(
                code="frontmatter_description_type",
                message="Frontmatter description must be text.",
                path="SKILL.md",
                field="description",
            )
        )
    elif not description.strip() or description != description.strip() or len(description) > 1024:
        issues.append(
            SkillPackageIssue(
                code="frontmatter_description_invalid",
                message="Frontmatter description must contain 1-1024 trimmed characters.",
                path="SKILL.md",
                field="description",
            )
        )

    license_value = _optional_text_field(frontmatter, "license", issues)
    compatibility = _optional_text_field(frontmatter, "compatibility", issues)
    metadata = frontmatter.get("metadata", {})
    if not isinstance(metadata, dict) or not _is_json_safe(metadata):
        issues.append(
            SkillPackageIssue(
                code="frontmatter_metadata_type",
                message="Frontmatter metadata must be a JSON-compatible mapping with text keys.",
                path="SKILL.md",
                field="metadata",
            )
        )
        metadata = {}

    allowed_tools = _parse_allowed_tools(frontmatter.get("allowed-tools"), issues)
    if any(issue.severity == "error" for issue in issues):
        return None
    return {
        "name": name,
        "description": description,
        "license": license_value,
        "compatibility": compatibility,
        "metadata": copy.deepcopy(metadata),
        "allowed_tools": allowed_tools,
    }


def _optional_text_field(
    frontmatter: Mapping[str, Any],
    field: str,
    issues: list[SkillPackageIssue],
) -> str | None:
    value = frontmatter.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        issues.append(
            SkillPackageIssue(
                code=f"frontmatter_{field}_type",
                message=f"Frontmatter {field} must be non-empty trimmed text.",
                path="SKILL.md",
                field=field,
            )
        )
        return None
    return value


def _parse_allowed_tools(
    value: Any, issues: list[SkillPackageIssue]
) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        tools = tuple(part for part in value.split() if part)
    elif isinstance(value, list) and all(
        isinstance(item, str) and item.strip() == item and item for item in value
    ):
        tools = tuple(value)
    else:
        issues.append(
            SkillPackageIssue(
                code="frontmatter_allowed_tools_type",
                message="Frontmatter allowed-tools must be text or a list of non-empty text values.",
                path="SKILL.md",
                field="allowed-tools",
            )
        )
        return ()
    if not tools or any(len(tool) > 128 for tool in tools):
        issues.append(
            SkillPackageIssue(
                code="frontmatter_allowed_tools_invalid",
                message="Frontmatter allowed-tools entries must contain 1-128 characters.",
                path="SKILL.md",
                field="allowed-tools",
            )
        )
        return ()
    return tools


def _is_json_safe(value: Any, *, depth: int = 0) -> bool:
    if depth > 20:
        return False
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_safe(item, depth=depth + 1) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_safe(item, depth=depth + 1)
            for key, item in value.items()
        )
    return False


def _strict_yaml_load(text: str) -> Any:
    for token in yaml.scan(text, Loader=_StrictSafeLoader):
        if isinstance(token, (AliasToken, AnchorToken)):
            raise yaml.YAMLError("YAML aliases and anchors are not supported")
    return yaml.load(text, Loader=_StrictSafeLoader)


def _load_strict_yaml(text: str) -> bool:
    try:
        _strict_yaml_load(text)
    except (yaml.YAMLError, TypeError, ValueError, RecursionError):
        return False
    return True


def _check_static_syntax(
    path: str, content: str, issues: list[SkillPackageIssue]
) -> None:
    suffix = PurePosixPath(path).suffix.lower()
    valid = True
    code = ""
    message = ""
    line: int | None = None
    if suffix == ".py":
        try:
            ast.parse(content, filename=path)
        except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
            valid = False
            code = "python_syntax_invalid"
            message = "Python file failed static syntax validation."
            line = exc.lineno if isinstance(exc, SyntaxError) else None
    elif suffix == ".json":
        try:
            json.loads(content)
        except (json.JSONDecodeError, UnicodeError, RecursionError):
            valid = False
            code = "json_syntax_invalid"
            message = "JSON file failed static syntax validation."
    elif suffix in {".yaml", ".yml"}:
        if not _load_strict_yaml(content):
            valid = False
            code = "yaml_syntax_invalid"
            message = "YAML file failed strict safe syntax validation."
    elif suffix in {".js", ".mjs", ".cjs"}:
        valid, line = _javascript_lexically_valid(content)
        if valid:
            line = _javascript_obvious_syntax_issue_line(content)
            valid = line is None
        code = "javascript_syntax_invalid"
        message = "JavaScript file failed conservative static syntax validation."
    if not valid:
        issues.append(
            SkillPackageIssue(
                code=code,
                message=message,
                path=path,
                line=line,
            )
        )


def _javascript_lexically_valid(content: str) -> tuple[bool, int | None]:
    """Conservatively check JS delimiters/comments/strings without execution."""

    matching = {")": "(", "]": "[", "}": "{"}
    stack: list[tuple[str, int]] = []
    state = "normal"
    escaped = False
    regex_class = False
    line = 1
    state_line = 1
    previous_significant: str | None = None
    index = 0
    while index < len(content):
        character = content[index]
        following = content[index + 1] if index + 1 < len(content) else ""
        if character == "\n":
            line += 1
        if state == "line_comment":
            if character == "\n":
                state = "normal"
            index += 1
            continue
        if state == "block_comment":
            if character == "*" and following == "/":
                state = "normal"
                index += 2
                continue
            index += 1
            continue
        if state in {"single", "double", "template"}:
            delimiter = {"single": "'", "double": '"', "template": "`"}[state]
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == delimiter:
                state = "normal"
                previous_significant = delimiter
            elif character == "\n" and state != "template":
                return False, state_line
            index += 1
            continue
        if state == "regex":
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == "[":
                regex_class = True
            elif character == "]":
                regex_class = False
            elif character == "/" and not regex_class:
                state = "normal"
                previous_significant = "/"
            elif character == "\n":
                return False, state_line
            index += 1
            continue

        if character == "/" and following == "/":
            state = "line_comment"
            index += 2
            continue
        if character == "/" and following == "*":
            state = "block_comment"
            state_line = line
            index += 2
            continue
        if character in {"'", '"', "`"}:
            state = {"'": "single", '"': "double", "`": "template"}[character]
            state_line = line
            escaped = False
            index += 1
            continue
        if character == "/" and (
            previous_significant is None
            or previous_significant in "=(:,![{;?&|+-*%^~<>"
        ):
            state = "regex"
            state_line = line
            escaped = False
            regex_class = False
            index += 1
            continue
        if character in "([{":
            stack.append((character, line))
        elif character in ")]}":
            if not stack or stack[-1][0] != matching[character]:
                return False, line
            stack.pop()
        if not character.isspace():
            previous_significant = character
        index += 1
    if state in {"single", "double", "template", "block_comment", "regex"}:
        return False, state_line
    if stack:
        return False, stack[-1][1]
    return True, None


def _javascript_obvious_syntax_issue_line(content: str) -> int | None:
    def mask(match: re.Match[str]) -> str:
        value = match.group(0)
        replacement = ["\n" if character == "\n" else " " for character in value]
        if not value.startswith(("//", "/*")):
            for index, character in enumerate(value):
                if character != "\n":
                    replacement[index] = "0"
                    break
        return "".join(replacement)

    visible_code = _JS_NON_CODE_RE.sub(mask, content)
    matches = [
        match
        for pattern in (
            _JS_MISSING_ASSIGNMENT_RE,
            _JS_MISSING_DECLARATION_NAME_RE,
        )
        if (match := pattern.search(visible_code)) is not None
    ]
    if not matches:
        return None
    first = min(matches, key=lambda match: match.start())
    return visible_code.count("\n", 0, first.start()) + 1


def _scan_credentials(
    path: str | None, content: str, issues: list[SkillPackageIssue]
) -> None:
    emitted: set[str] = set()
    for line_number, line in enumerate(content.splitlines(), start=1):
        checks: list[tuple[str, bool, str]] = [
            (
                "credential_private_key",
                bool(_PRIVATE_KEY_RE.search(line)),
                "Private key material is not allowed in Skill packages.",
            ),
            (
                "credential_token",
                any(pattern.search(line) for pattern in _TOKEN_PATTERNS),
                "A high-confidence access token was detected in the Skill package.",
            ),
            (
                "credential_url",
                bool(_CREDENTIAL_URL_RE.search(line)),
                "A URL containing embedded credentials was detected.",
            ),
        ]
        assignment = _SECRET_ASSIGNMENT_RE.search(line)
        assignment_detected = False
        if assignment:
            candidate = assignment.group("value").strip("\"'`").lower()
            assignment_detected = (
                len(candidate) >= 8
                and not all(character in "x*-_" for character in candidate)
                and not any(part in candidate for part in _PLACEHOLDER_PARTS)
            )
        checks.append(
            (
                "credential_assignment",
                assignment_detected,
                "A hard-coded credential assignment was detected.",
            )
        )
        for code, matched, message in checks:
            if matched and code not in emitted:
                issues.append(
                    SkillPackageIssue(
                        code=code,
                        message=message,
                        path=path,
                        line=line_number,
                    )
                )
                emitted.add(code)


def _check_local_references(
    files: Mapping[str, str], issues: list[SkillPackageIssue]
) -> None:
    exact_paths = set(files)
    folded_paths = {_path_identity(path): path for path in exact_paths}
    emitted: set[tuple[str, str, str]] = set()
    for source_path in sorted(files):
        if PurePosixPath(source_path).suffix.lower() not in {".md", ".markdown"}:
            continue
        without_fences = _FENCED_CODE_RE.sub("", files[source_path])
        raw_targets = []
        for inline_match in _INLINE_CODE_RE.finditer(without_fences):
            inline_content = inline_match.group(0)[1:-1]
            raw_targets.extend(
                match.group("path")
                for match in _INLINE_RESOURCE_PATH_RE.finditer(inline_content)
            )
        scrubbed = _INLINE_CODE_RE.sub("", without_fences)
        raw_targets.extend(
            match.group(1).strip() for match in _MARKDOWN_LINK_RE.finditer(scrubbed)
        )
        for raw_target_value in raw_targets:
            raw_target = raw_target_value.strip()
            if raw_target.startswith("<") and ">" in raw_target:
                raw_target = raw_target[1 : raw_target.index(">")]
            else:
                raw_target = raw_target.split(maxsplit=1)[0]
            target = unquote(raw_target).strip()
            if not target or target.startswith("#"):
                continue
            if _WINDOWS_DRIVE_RE.match(target) or target.startswith(("/", "\\")):
                key = (source_path, target, "local_reference_unsafe")
                if key not in emitted:
                    issues.append(
                        SkillPackageIssue(
                            code="local_reference_unsafe",
                            message="Local Skill references must stay inside the package.",
                            path=source_path,
                        )
                    )
                    emitted.add(key)
                continue
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc:
                continue
            target_path = parsed.path
            if not target_path:
                continue
            resolved = posixpath.normpath(
                posixpath.join(posixpath.dirname(source_path), target_path)
            )
            if resolved == ".." or resolved.startswith("../"):
                code = "local_reference_unsafe"
                message = "Local Skill references must stay inside the package."
            elif resolved not in exact_paths:
                if _path_identity(resolved) in folded_paths:
                    code = "local_reference_case_mismatch"
                    message = "Local Skill reference casing must exactly match its package path."
                else:
                    code = "local_reference_missing"
                    message = "A local Skill reference does not exist in the package."
            else:
                continue
            key = (source_path, resolved, code)
            if key not in emitted:
                issues.append(
                    SkillPackageIssue(code=code, message=message, path=source_path)
                )
                emitted.add(key)


__all__ = [
    "ALLOWED_ROOTS",
    "MAX_FILE_BYTES",
    "MAX_FILES",
    "MAX_TOTAL_BYTES",
    "SUPPORTED_FRONTMATTER_FIELDS",
    "VALIDATOR_VERSION",
    "SkillPackageIssue",
    "SkillPackageV2",
    "SkillPackageValidationResult",
    "compute_package_digest",
    "compute_skill_content_digest",
    "compute_skill_package_digest",
    "scan_skill_package_credentials",
    "validate_skill_package",
]
