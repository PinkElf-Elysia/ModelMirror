from __future__ import annotations

import hashlib
import json
import posixpath
import re
import unicodedata
import warnings
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Iterable, Literal, Mapping, Sequence
from urllib.parse import urlsplit

from .package_validation import (
    SkillPackageIssue,
    _check_file_directory_conflicts,
    _check_local_references,
    _check_static_syntax,
    _parse_skill_frontmatter,
    _scan_credentials,
    _validate_file_path,
    _validate_frontmatter,
    compute_skill_content_digest,
    scan_skill_package_credentials,
)


SKILL_TRUST_INDEX_VERSION = 1
SKILL_TRUST_SCANNER_VERSION = "skill-trust-scanner-v2"
SKILL_TRUST_SUMMARY_VERSION = 1

MAX_TRUST_FILES = 500
MAX_TRUST_FILE_BYTES = 10 * 1024 * 1024
MAX_TRUST_TOTAL_BYTES = 50 * 1024 * 1024
MAX_TRUST_DIRECTORY_DEPTH = 16
MAX_TRUST_PATH_CHARS = 240

RiskLevel = Literal["low", "medium", "high", "critical"]
TrustStatus = Literal["verified", "conditional", "blocked"]
InstallPolicy = Literal["allow", "confirm", "block"]
CompatibilityStatus = Literal["portable", "conditional", "unsupported"]
FindingSeverity = Literal["info", "warning", "error", "critical"]

_RISK_ORDER: dict[RiskLevel, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}

# Only findings that prove malicious content or prevent exact, portable
# installation remain hard blocks. Other critical findings require an explicit
# local-console acknowledgement and are excluded from Agent Router discovery.
_HARD_BLOCK_FINDING_CODES = {
    "credential_path",
    "file_directory_conflict",
    "file_path_case_collision",
    "file_path_too_long",
    "file_path_type",
    "file_path_windows_unsafe",
    "package_file_count_exceeded",
    "package_files_type",
    "package_size_exceeded",
    "skill_markdown_empty",
    "trust_directory_tree_missing",
    "trust_download_execute_blocked",
    "trust_file_count_exceeded",
    "trust_file_size_exceeded",
    "trust_git_lfs_pointer_blocked",
    "trust_git_mode_unsupported",
    "trust_git_object_unsupported",
    "trust_gitlink_blocked",
    "trust_package_digest_unavailable",
    "trust_package_size_exceeded",
    "trust_path_collision",
    "trust_path_unsafe",
    "trust_scan_content_incomplete",
    "trust_skill_markdown_missing",
    "trust_source_ref_invalid",
    "trust_symlink_blocked",
}
_HARD_BLOCK_FINDING_PREFIXES = ("credential_",)
_SCRIPT_SUFFIXES = {".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript"}
_SHELL_SUFFIXES = {".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd"}
_ARCHIVE_SUFFIXES = {
    ".zip", ".tar", ".tgz", ".gz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war", ".whl",
}
_EXECUTABLE_SUFFIXES = {".exe", ".dll", ".com", ".msi", ".dylib", ".so", ".app", ".bin"}
_OPAQUE_EXTENSIONS = {
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".gif": "gif",
    ".webp": "webp",
    ".pdf": "pdf",
    ".woff": "woff",
    ".woff2": "woff2",
    ".mp3": "mp3",
    ".wav": "wav",
    ".mp4": "mp4",
}
_COMMAND_RE = re.compile(
    r"(?im)(?:^|\n)\s*(?:[$>]\s*)?(?P<line>python3?|node|rg|git|npm|npx|pnpm|yarn|pip3?|uv|curl|wget|bash|sh|powershell|pwsh|cmd)(?=\s+(?:[-./\w]))"
    r"|`\s*(?P<inline>python3?|node|rg|git|npm|npx|pnpm|yarn|pip3?|uv|curl|wget|bash|sh|powershell|pwsh|cmd)\b",
)
_NETWORK_RE = re.compile(
    r"\b(?:curl|wget|requests\.|urllib\.|fetch\s*\(|axios\.|httpx\.|WebFetch\b|network access|internet access|access the network|联网|网络访问)\b"
    r"|\b(?:download|fetch|request|query)\b[^\n]{0,100}https?://",
    re.IGNORECASE,
)
_CREDENTIAL_REQUIREMENT_RE = re.compile(
    r"\b(?:requires?|provide|configure|set|export|environment variable)\b[^\n]{0,100}\b(?:api[_ -]?key|access[_ -]?token|secret|credential|oauth|bearer token)\b"
    r"|\b[A-Z][A-Z0-9_]{2,}(?:API_KEY|TOKEN|SECRET|CREDENTIAL)\b|需要[^\n]{0,40}(?:登录凭据|密钥|令牌)",
    re.IGNORECASE,
)
_FILE_WRITE_RE = re.compile(
    r"\b(?:write_file|open\s*\([^\n]{0,80}['\"]w|writeText|writeFile|mkdir|save to|output directory|写入文件|保存到)\b",
    re.IGNORECASE,
)
_HOST_FS_RE = re.compile(
    r"(?:[A-Za-z]:[/\\](?:Users|Program Files|Windows)\b|/(?:home|Users|etc|var|opt)/|host filesystem|本机文件|宿主文件)",
    re.IGNORECASE,
)
_BROWSER_RE = re.compile(r"\b(?:playwright|selenium|puppeteer|browser automation|browser access|use (?:the )?browser|chrome extension)\b|浏览器(?:自动化|访问|控制)", re.IGNORECASE)
_MCP_RE = re.compile(r"\bMCP\b|model context protocol", re.IGNORECASE)
_DESKTOP_RE = re.compile(r"computer[_ -]?use|desktop control|GUI automation|桌面控制|电脑操作", re.IGNORECASE)
_DESTRUCTIVE_RE = re.compile(
    r"\b(?:rm\s+-rf|rmdir\s+/s|Remove-Item\s+-Recurse|format\s+[A-Za-z]:|DROP\s+(?:DATABASE|TABLE)|delete all)\b|删除全部|清空数据库",
    re.IGNORECASE,
)
_SECURITY_SENSITIVE_RE = re.compile(
    r"\b(?:pentest|exploit|credential dump|reverse shell|vulnerability scanner|malware|forensic)\b|渗透测试|漏洞利用|凭据转储",
    re.IGNORECASE,
)
_OBFUSCATION_RE = re.compile(
    r"\b(?:base64\s+-d|fromBase64|atob\s*\(|eval\s*\(|exec\s*\(|Invoke-Expression|IEX\s*\()",
    re.IGNORECASE,
)
_GIT_LFS_RE = re.compile(
    r"\Aversion https://git-lfs\.github\.com/spec/v1\r?\noid sha256:[0-9a-f]{64}\r?\nsize \d+\r?\n?\Z",
    re.IGNORECASE,
)
_ACTIVE_TEXT_RE = re.compile(r"<script\b|\bjavascript\s*:|\bon(?:load|error|click)\s*=", re.IGNORECASE)
_LONG_BASE64_RE = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{2048,}={0,2}(?![A-Za-z0-9+/])")
_DOWNLOAD_RE = re.compile(r"\b(?:curl|wget)\b|requests\.|urllib\.|fetch\s*\(|\b(?:download|fetch)\b[^\n]{0,100}https?://", re.IGNORECASE)
_EXECUTE_RE = re.compile(
    r"\b(?:bash|sh|powershell|pwsh|cmd|subprocess\.|os\.system|child_process|eval\s*\(|exec\s*\()",
    re.IGNORECASE,
)
_SHELL_API_RE = re.compile(
    r"\b(?:subprocess\.(?:run|Popen|call|check_call|check_output)|os\.system|child_process|execSync\s*\(|spawnSync?\s*\()",
    re.IGNORECASE,
)
_DEPENDENCY_KEYS = ("dependency", "dependencies", "requires", "requirements")


@dataclass(frozen=True, slots=True)
class SkillTrustFinding:
    code: str
    severity: FindingSeverity
    message: str
    path: str | None = None
    line: int | None = None
    field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "code": self.code,
                "severity": self.severity,
                "message": self.message,
                "path": self.path,
                "line": self.line,
                "field": self.field,
            }.items()
            if value is not None
        }


@dataclass(frozen=True, slots=True)
class SkillTrustTreeEntry:
    path: str
    mode: str
    object_type: str
    object_id: str
    size: int | None
    content: bytes | None = None


@dataclass(slots=True)
class _ScanState:
    risk_level: RiskLevel = "low"
    findings: list[SkillTrustFinding] = field(default_factory=list)

    def add(
        self,
        code: str,
        severity: FindingSeverity,
        message: str,
        *,
        risk: RiskLevel,
        path: str | None = None,
        line: int | None = None,
        field: str | None = None,
    ) -> None:
        finding = SkillTrustFinding(code, severity, message, path, line, field)
        if finding not in self.findings:
            self.findings.append(finding)
        if _RISK_ORDER[risk] > _RISK_ORDER[self.risk_level]:
            self.risk_level = risk


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_repo_url(value: str) -> str:
    return value.strip().removesuffix(".git").casefold()


def normalize_sub_path(value: str) -> str:
    return value.strip().replace("\\", "/").strip("/")


def source_key(repo_url: str, sub_path: str, verified_commit: str) -> str:
    return "#".join(
        (normalize_repo_url(repo_url), normalize_sub_path(sub_path), verified_commit.strip().casefold())
    )


def receipt_id_for(repo_url: str, sub_path: str, verified_commit: str) -> str:
    return f"skill-trust-{hashlib.sha256(source_key(repo_url, sub_path, verified_commit).encode()).hexdigest()[:24]}"


def catalog_fingerprint_for(candidates: Iterable[Mapping[str, Any]]) -> str:
    payload = []
    for candidate in candidates:
        source = candidate["installSource"]
        payload.append(
            {
                "candidateId": candidate["candidateId"],
                "repoUrl": normalize_repo_url(str(source["repoUrl"])),
                "subPath": normalize_sub_path(str(source["subPath"])),
                "verifiedCommit": str(source["verifiedCommit"]).casefold(),
            }
        )
    payload.sort(key=lambda item: item["candidateId"])
    return sha256_json(payload)


def _safe_relative_path(path: str) -> bool:
    if (
        not path
        or path != path.strip()
        or "\\" in path
        or path.startswith("/")
        or "\x00" in path
        or len(path) > MAX_TRUST_PATH_CHARS
        or any(ord(character) < 32 for character in path)
    ):
        return False
    normalized = posixpath.normpath(path)
    parts = PurePosixPath(path).parts
    return (
        normalized == path
        and normalized not in {".", ".."}
        and ".." not in parts
        and len(parts) <= MAX_TRUST_DIRECTORY_DEPTH + 1
        and all(part and part not in {".", ".."} for part in parts)
    )


def _binary_kind(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    if content.startswith(b"%PDF-"):
        return "pdf"
    if content.startswith(b"wOFF"):
        return "woff"
    if content.startswith(b"wOF2"):
        return "woff2"
    if content.startswith(b"ID3") or (len(content) >= 2 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0):
        return "mp3"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WAVE":
        return "wav"
    if len(content) >= 12 and content[4:8] == b"ftyp":
        return "mp4"
    return None


def _looks_executable(content: bytes) -> bool:
    return content.startswith((b"MZ", b"\x7fELF", b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"))


def _looks_archive(content: bytes) -> bool:
    return content.startswith((b"PK\x03\x04", b"\x1f\x8b", b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07"))


def _first_line(text: str, pattern: re.Pattern[str]) -> int | None:
    for number, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            return number
    return None


def _flatten_declarations(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, str):
        values.extend(part.strip() for part in re.split(r"[,\n]", value) if part.strip())
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        for item in value:
            if isinstance(item, str) and item.strip():
                values.append(item.strip())
            elif isinstance(item, Mapping):
                for key, nested in item.items():
                    if str(key).strip():
                        values.append(str(key).strip())
                    if isinstance(nested, str) and nested.strip():
                        values.append(nested.strip())
    elif isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).strip():
                values.append(str(key).strip())
            if isinstance(nested, str) and nested.strip():
                values.append(nested.strip())
    sanitized = [_safe_metadata_label(item, fallback="<redacted-dependency>") for item in values]
    return sorted(set(sanitized), key=str.casefold)[:100]


def _contains_credentials(value: str) -> bool:
    issues: list[SkillPackageIssue] = []
    _scan_credentials(None, value, issues)
    return bool(issues)


def _safe_metadata_label(value: Any, *, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 200
        or "\n" in normalized
        or "\r" in normalized
        or any(ord(character) < 32 for character in normalized)
        or _contains_credentials(normalized)
    ):
        return fallback
    return normalized


def _convert_package_issue(issue: SkillPackageIssue) -> tuple[FindingSeverity, RiskLevel]:
    if issue.severity == "error":
        return "critical", "critical"
    return "warning", "medium"


def scan_skill_trust_receipt(
    *,
    repo_url: str,
    sub_path: str,
    verified_commit: str,
    directory_tree_sha: str | None,
    entries: Sequence[SkillTrustTreeEntry],
) -> dict[str, Any]:
    """Scan a fixed Git tree without executing or importing package content."""

    normalized_sub_path = normalize_sub_path(sub_path)
    state = _ScanState()
    text_files: dict[str, str] = {}
    raw_files: dict[str, bytes] = {}
    opaque_files: list[dict[str, Any]] = []
    scripts: list[dict[str, Any]] = []
    sensitive_paths: set[str] = set()
    portable_paths: set[str] = {"SKILL.md"}
    path_validation_issues: list[SkillPackageIssue] = []
    seen_identities: dict[str, str] = {}
    total_bytes = sum(entry.size or 0 for entry in entries if entry.object_type == "blob")
    content_complete = True
    metadata_only = False

    if not re.fullmatch(r"[0-9a-f]{40}", verified_commit.casefold()):
        state.add("trust_source_ref_invalid", "critical", "The verified Git commit is invalid.", risk="critical")
    if not directory_tree_sha or not re.fullmatch(r"[0-9a-f]{40}", directory_tree_sha.casefold()):
        state.add("trust_directory_tree_missing", "critical", "The Git directory tree SHA is unavailable.", risk="critical")
    if len(entries) > MAX_TRUST_FILES:
        state.add(
            "trust_file_count_exceeded", "critical", f"The package exceeds the {MAX_TRUST_FILES}-file trust scan limit.", risk="critical"
        )
        content_complete = False
        metadata_only = True
    if total_bytes > MAX_TRUST_TOTAL_BYTES:
        state.add(
            "trust_package_size_exceeded", "critical", "The package exceeds the 50 MiB trust scan limit.", risk="critical"
        )
        content_complete = False
        metadata_only = True

    for entry in sorted(entries, key=lambda item: item.path.encode("utf-8", "surrogatepass")):
        path = entry.path
        path_has_credentials = _contains_credentials(path)
        if path_has_credentials:
            sensitive_paths.add(path)
            state.add(
                "credential_path",
                "critical",
                "A package path contains credential-like material and was redacted.",
                risk="critical",
            )
        finding_path = None if path_has_credentials else path
        if not _safe_relative_path(path):
            state.add(
                "trust_path_unsafe",
                "critical",
                "A package path is unsafe or exceeds trust scan limits.",
                risk="critical",
                path=finding_path if len(path) <= 240 else None,
            )
            content_complete = False
            continue
        if path != "SKILL.md" and _validate_file_path(path, path_validation_issues) is not None:
            portable_paths.add(path)
        identity = unicodedata.normalize("NFC", path).casefold()
        if identity in seen_identities:
            state.add("trust_path_collision", "critical", "Package paths collide after case and Unicode normalization.", risk="critical", path=finding_path)
            content_complete = False
            continue
        seen_identities[identity] = path
        if entry.object_type == "commit" or entry.mode == "160000":
            state.add("trust_gitlink_blocked", "critical", "Git submodules are not allowed in third-party Skill packages.", risk="critical", path=finding_path)
            content_complete = False
            continue
        if entry.mode == "120000":
            state.add("trust_symlink_blocked", "critical", "Symbolic links are not allowed in third-party Skill packages.", risk="critical", path=finding_path)
            content_complete = False
            continue
        if entry.object_type != "blob":
            state.add("trust_git_object_unsupported", "critical", "The Git tree contains an unsupported object type.", risk="critical", path=finding_path)
            content_complete = False
            continue
        if entry.mode not in {"100644", "100755"}:
            state.add("trust_git_mode_unsupported", "critical", "The Git file mode is unsupported.", risk="critical", path=finding_path)
            content_complete = False
        if entry.mode == "100755":
            state.add("trust_executable_mode_blocked", "critical", "Executable Git files require explicit confirmation and are excluded from Agent Router discovery.", risk="critical", path=finding_path)
        if entry.size is None or entry.size > MAX_TRUST_FILE_BYTES:
            state.add("trust_file_size_exceeded", "critical", "A package file exceeds the 10 MiB trust scan limit.", risk="critical", path=finding_path)
            content_complete = False
            continue
        if entry.content is None or len(entry.content) != entry.size:
            if metadata_only:
                continue
            state.add("trust_scan_content_incomplete", "critical", "Package content was not completely available to the trust scanner.", risk="critical", path=finding_path)
            content_complete = False
            continue
        content = entry.content
        raw_files[path] = content
        suffix = PurePosixPath(path).suffix.casefold()
        if suffix in _EXECUTABLE_SUFFIXES or _looks_executable(content):
            state.add("trust_executable_binary_blocked", "critical", "Executable binary content requires explicit confirmation and is excluded from Agent Router discovery.", risk="critical", path=finding_path)
            continue
        if suffix in _ARCHIVE_SUFFIXES or _looks_archive(content):
            state.add("trust_archive_blocked", "critical", "Embedded archives require explicit confirmation and are excluded from Agent Router discovery.", risk="critical", path=finding_path)
            continue
        binary_kind = _binary_kind(content)
        expected_kind = _OPAQUE_EXTENSIONS.get(suffix)
        if binary_kind or expected_kind:
            if binary_kind != expected_kind:
                state.add("trust_opaque_magic_mismatch", "critical", "Opaque resource extension and file signature do not match; explicit confirmation is required and Agent Router discovery is disabled.", risk="critical", path=finding_path)
                continue
            opaque_files.append({"path": finding_path, "kind": binary_kind, "sizeBytes": len(content)})
            state.add("trust_opaque_resource", "warning", "The package contains a passive opaque resource that is not executed or parsed.", risk="medium", path=finding_path)
            continue
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            state.add("trust_unknown_binary_blocked", "critical", "Unknown binary content requires explicit confirmation and is excluded from Agent Router discovery.", risk="critical", path=finding_path)
            continue
        if "\x00" in text:
            state.add("trust_unknown_binary_blocked", "critical", "Unknown binary content requires explicit confirmation and is excluded from Agent Router discovery.", risk="critical", path=finding_path)
            continue
        text_files[path] = text
        if _GIT_LFS_RE.fullmatch(text):
            state.add(
                "trust_git_lfs_pointer_blocked",
                "critical",
                "Git LFS pointers do not contain the fixed resource bytes required for a complete trust scan.",
                risk="critical",
                path=finding_path,
            )
        if suffix in {".html", ".htm", ".svg", ".xml"} and _ACTIVE_TEXT_RE.search(text):
            state.add(
                "trust_active_text_blocked",
                "critical",
                "Active browser-executable text requires explicit confirmation and is excluded from Agent Router discovery.",
                risk="critical",
                path=finding_path,
            )
        if _LONG_BASE64_RE.search(text):
            state.add(
                "trust_encoded_payload_blocked",
                "critical",
                "Large encoded payloads require explicit confirmation and are excluded from Agent Router discovery.",
                risk="critical",
                path=finding_path,
            )
        if suffix in _SCRIPT_SUFFIXES:
            scripts.append({"path": finding_path, "language": _SCRIPT_SUFFIXES[suffix], "sizeBytes": len(content)})
            state.add("trust_local_script", "warning", "The package contains local Python or JavaScript code.", risk="medium", path=finding_path)
        elif suffix in _SHELL_SUFFIXES:
            state.add("trust_shell_script", "error", "The package declares a shell script requiring high-risk execution capability.", risk="high", path=finding_path)

    if "SKILL.md" not in text_files:
        state.add("trust_skill_markdown_missing", "critical", "The package does not contain a readable UTF-8 SKILL.md at its root.", risk="critical", path="SKILL.md")

    package_issues: list[SkillPackageIssue] = list(path_validation_issues)
    frontmatter: dict[str, Any] | None = None
    parsed: dict[str, Any] | None = None
    skill_markdown = text_files.get("SKILL.md")
    if skill_markdown is not None:
        root_name = PurePosixPath(normalized_sub_path).name if normalized_sub_path else None
        frontmatter = _parse_skill_frontmatter(skill_markdown, package_issues)
        if root_name is None and isinstance(frontmatter, Mapping) and isinstance(frontmatter.get("name"), str):
            root_name = str(frontmatter["name"])
        parsed = _validate_frontmatter(frontmatter, root_name, package_issues)
    portable_text_paths: dict[str, str] = {}
    for path, text in text_files.items():
        if path in portable_paths:
            portable_text_paths[path] = text
        # compile() may emit SyntaxWarning text from third-party files. The
        # deterministic syntax result is captured as structured issues; source
        # warnings must not spill into maintenance logs.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            _check_static_syntax(path, text, package_issues)
    _check_file_directory_conflicts(raw_files, package_issues)
    reference_files = {path: text for path, text in portable_text_paths.items()}
    reference_files.update(
        {item["path"]: "" for item in opaque_files if item["path"] in portable_paths}
    )
    _check_local_references(reference_files, package_issues)
    package_issues.extend(
        scan_skill_package_credentials(
            skill_markdown=skill_markdown,
            files={path: text for path, text in text_files.items() if path != "SKILL.md"},
        )
    )
    for issue in package_issues:
        severity, risk = _convert_package_issue(issue)
        state.add(
            issue.code,
            severity,
            issue.message,
            risk=risk,
            path=None if issue.path in sensitive_paths else issue.path,
            line=issue.line,
            field=None if issue.field and _contains_credentials(issue.field) else issue.field,
        )

    all_text = "\n".join(text_files[path] for path in sorted(text_files))
    commands = sorted({(match.group("line") or match.group("inline")).casefold() for match in _COMMAND_RE.finditer(all_text)})
    capabilities = {
        "network": bool(_NETWORK_RE.search(all_text)),
        "credentials": bool(_CREDENTIAL_REQUIREMENT_RE.search(all_text)),
        "fileWrite": bool(_FILE_WRITE_RE.search(all_text)),
        "hostFilesystem": bool(_HOST_FS_RE.search(all_text)),
        "browser": bool(_BROWSER_RE.search(all_text)),
        "mcp": bool(_MCP_RE.search(all_text)),
        "shell": (
            bool(_SHELL_SUFFIXES.intersection(PurePosixPath(path).suffix.casefold() for path in text_files))
            or any(command in {"bash", "sh", "powershell", "pwsh", "cmd"} for command in commands)
            or bool(_SHELL_API_RE.search(all_text))
        ),
        "packageManager": any(command in {"npm", "npx", "pnpm", "yarn", "pip", "pip3", "uv"} for command in commands),
        "desktopControl": bool(_DESKTOP_RE.search(all_text)),
        "destructive": bool(_DESTRUCTIVE_RE.search(all_text)),
        "securitySensitive": bool(_SECURITY_SENSITIVE_RE.search(all_text)),
    }
    capability_messages = {
        "network": ("trust_network_required", "The Skill declares network access."),
        "credentials": ("trust_credentials_required", "The Skill declares credential or token requirements."),
        "hostFilesystem": ("trust_host_filesystem_required", "The Skill declares host filesystem access."),
        "browser": ("trust_browser_required", "The Skill declares browser automation or browser access."),
        "mcp": ("trust_mcp_required", "The Skill declares MCP capability."),
        "shell": ("trust_shell_required", "The Skill declares shell execution."),
        "packageManager": ("trust_package_manager_required", "The Skill declares package-manager execution."),
        "desktopControl": ("trust_desktop_control_required", "The Skill declares desktop-control capability."),
        "destructive": ("trust_destructive_capability", "The Skill contains destructive operational instructions."),
        "securitySensitive": ("trust_security_sensitive", "The Skill declares sensitive security capabilities."),
    }
    if capabilities["fileWrite"]:
        state.add("trust_sandbox_write_required", "warning", "The Skill declares file-writing behavior.", risk="medium")
    for key, (code, message) in capability_messages.items():
        if capabilities[key]:
            state.add(code, "error", message, risk="high")
    if _OBFUSCATION_RE.search(all_text):
        state.add("trust_obfuscated_command_blocked", "critical", "Obfuscated or dynamic code patterns require explicit confirmation and are excluded from Agent Router discovery.", risk="critical", line=_first_line(all_text, _OBFUSCATION_RE))
    if any(_DOWNLOAD_RE.search(text) and _EXECUTE_RE.search(text) for text in text_files.values()):
        state.add("trust_download_execute_blocked", "critical", "Dynamic download-and-execute behavior is not allowed.", risk="critical")

    dependencies: list[str] = []
    if isinstance(frontmatter, Mapping):
        for key in _DEPENDENCY_KEYS:
            if key in frontmatter:
                dependencies.extend(_flatten_declarations(frontmatter[key]))
    dependencies = sorted(set(dependencies), key=str.casefold)
    if dependencies:
        state.add("trust_nonstandard_dependencies", "warning", "The Skill declares non-standard dependency metadata.", risk="medium", path="SKILL.md")

    allowed_tools = [
        _safe_metadata_label(tool, fallback="<redacted-tool>")
        for tool in (parsed.get("allowed_tools", ()) if parsed else ())
    ]
    for tool in allowed_tools:
        normalized = tool.casefold()
        if normalized in {"write", "edit", "sandbox_write_file"}:
            state.add("trust_tool_write_declared", "warning", "allowed-tools declares file mutation; this metadata does not grant permission.", risk="medium", path="SKILL.md", field="allowed-tools")
        elif any(token in normalized for token in ("bash", "shell", "browser", "computer", "mcp", "webfetch")):
            state.add("trust_tool_high_risk_declared", "error", "allowed-tools declares a high-risk capability; this metadata does not grant permission.", risk="high", path="SKILL.md", field="allowed-tools")
        elif normalized not in {"read", "grep", "glob", "skill_read", "skill_stage", "sandbox_read_file", "sandbox_search_files", "sandbox_list_files"}:
            state.add("trust_tool_unknown", "error", "allowed-tools contains an unknown runtime capability declaration.", risk="high", path="SKILL.md", field="allowed-tools")

    package_digest = compute_skill_content_digest(raw_files) if content_complete and len(raw_files) == len([entry for entry in entries if entry.object_type == "blob" and entry.mode not in {"120000", "160000"}]) else None
    if package_digest is None:
        state.add("trust_package_digest_unavailable", "critical", "The exact raw-byte package digest could not be computed.", risk="critical")

    findings = sorted(
        state.findings,
        key=lambda item: (
            -{"critical": 3, "error": 2, "warning": 1, "info": 0}[item.severity],
            item.code,
            item.path or "",
            item.line or 0,
        ),
    )
    finding_codes = {item.code for item in findings}
    blocked = any(
        code in _HARD_BLOCK_FINDING_CODES
        or any(code.startswith(prefix) for prefix in _HARD_BLOCK_FINDING_PREFIXES)
        for code in finding_codes
    )
    router_eligible = (
        not blocked
        and state.risk_level != "critical"
        and "trust_destructive_capability" not in finding_codes
        and "trust_tool_unknown" not in finding_codes
    )
    trust_status: TrustStatus = "blocked" if blocked else "verified" if state.risk_level == "low" else "conditional"
    install_policy: InstallPolicy = "block" if blocked else "allow" if state.risk_level == "low" else "confirm"
    compatibility: CompatibilityStatus = "unsupported" if blocked else "portable" if state.risk_level == "low" else "conditional"
    receipt_id = receipt_id_for(repo_url, normalized_sub_path, verified_commit)
    receipt_payload: dict[str, Any] = {
        "receiptId": receipt_id,
        "source": {
            "repoUrl": repo_url,
            "subPath": normalized_sub_path,
            "verifiedCommit": verified_commit.casefold(),
        },
        "directoryTreeSha": directory_tree_sha.casefold() if directory_tree_sha else None,
        "packageDigest": package_digest,
        "scannerVersion": SKILL_TRUST_SCANNER_VERSION,
        "riskLevel": state.risk_level,
        "trustStatus": trust_status,
        "installPolicy": install_policy,
        "compatibilityStatus": compatibility,
        "routerEligible": router_eligible,
        "summary": {
            "fileCount": len(entries),
            "totalBytes": total_bytes,
            "textFileCount": len(text_files),
            "scriptCount": len(scripts),
            "opaqueResourceCount": len(opaque_files),
        },
        "scripts": scripts,
        "opaqueResources": opaque_files,
        "license": _safe_metadata_label(parsed.get("license"), fallback="declared-redacted") if parsed and parsed.get("license") else None,
        "allowedTools": allowed_tools,
        "dependencies": dependencies,
        "commands": commands,
        "capabilities": capabilities,
        "findings": [finding.to_dict() for finding in findings],
    }
    receipt_payload["trustFingerprint"] = sha256_json(receipt_payload)
    return receipt_payload


def build_skill_trust_index(
    *,
    candidates: Sequence[Mapping[str, Any]],
    receipts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    catalog_fingerprint = catalog_fingerprint_for(candidates)
    receipt_by_source: dict[str, Mapping[str, Any]] = {}
    receipt_by_id: dict[str, Mapping[str, Any]] = {}
    for receipt in receipts:
        source = receipt["source"]
        key = source_key(source["repoUrl"], source["subPath"], source["verifiedCommit"])
        if key in receipt_by_source or receipt["receiptId"] in receipt_by_id:
            raise ValueError("Skill trust receipts contain a duplicate source or receipt ID.")
        fingerprint_payload = {key: value for key, value in receipt.items() if key != "trustFingerprint"}
        if sha256_json(fingerprint_payload) != receipt.get("trustFingerprint"):
            raise ValueError("Skill trust receipt fingerprint is invalid.")
        receipt_by_source[key] = receipt
        receipt_by_id[str(receipt["receiptId"])] = receipt

    candidate_receipts: dict[str, str] = {}
    for candidate in candidates:
        candidate_id = str(candidate["candidateId"])
        if candidate_id in candidate_receipts:
            raise ValueError("Skill trust index contains a duplicate candidate ID.")
        source = candidate["installSource"]
        key = source_key(source["repoUrl"], source["subPath"], source["verifiedCommit"])
        receipt = receipt_by_source.get(key)
        if receipt is None:
            raise ValueError(f"Skill trust receipt is missing for {candidate['candidateId']}.")
        candidate_receipts[candidate_id] = str(receipt["receiptId"])
    unused = set(receipt_by_source) - {
        source_key(c["installSource"]["repoUrl"], c["installSource"]["subPath"], c["installSource"]["verifiedCommit"])
        for c in candidates
    }
    if unused:
        raise ValueError("Skill trust index contains receipts that do not map to catalog candidates.")

    ordered_receipts = [receipt_by_id[key] for key in sorted(receipt_by_id)]
    payload: dict[str, Any] = {
        "version": SKILL_TRUST_INDEX_VERSION,
        "scannerVersion": SKILL_TRUST_SCANNER_VERSION,
        "catalogFingerprint": catalog_fingerprint,
        "candidateReceipts": {key: candidate_receipts[key] for key in sorted(candidate_receipts)},
        "receipts": ordered_receipts,
    }
    payload["fingerprint"] = sha256_json(payload)
    return payload


def build_skill_trust_summary(index: Mapping[str, Any]) -> dict[str, Any]:
    summaries = []
    for receipt in index["receipts"]:
        summaries.append(
            {
                key: receipt.get(key)
                for key in (
                    "receiptId", "trustFingerprint", "riskLevel", "trustStatus", "installPolicy", "compatibilityStatus", "routerEligible", "summary",
                )
            }
        )
    payload: dict[str, Any] = {
        "version": SKILL_TRUST_SUMMARY_VERSION,
        "scannerVersion": index["scannerVersion"],
        "catalogFingerprint": index["catalogFingerprint"],
        "trustIndexFingerprint": index["fingerprint"],
        "candidateReceipts": index["candidateReceipts"],
        "receipts": summaries,
    }
    payload["fingerprint"] = sha256_json(payload)
    return payload


def build_skill_trust_report(index: Mapping[str, Any]) -> dict[str, Any]:
    def distribution(field: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for receipt in index["receipts"]:
            key = str(receipt[field])
            result[key] = result.get(key, 0) + 1
        return dict(sorted(result.items()))

    source_hosts: dict[str, int] = {}
    finding_codes: dict[str, int] = {}
    capability_counts: dict[str, int] = {}
    dependency_count = 0
    dependency_receipt_count = 0
    license_declared_count = 0
    for receipt in index["receipts"]:
        host = urlsplit(str(receipt["source"]["repoUrl"])).hostname or "unknown"
        source_hosts[host] = source_hosts.get(host, 0) + 1
        dependencies = receipt.get("dependencies") or []
        dependency_count += len(dependencies)
        dependency_receipt_count += bool(dependencies)
        license_declared_count += bool(receipt.get("license"))
        for capability, required in (receipt.get("capabilities") or {}).items():
            if required:
                capability_counts[capability] = capability_counts.get(capability, 0) + 1
        for finding in receipt.get("findings") or []:
            code = str(finding["code"])
            finding_codes[code] = finding_codes.get(code, 0) + 1
    return {
        "version": 1,
        "scannerVersion": index["scannerVersion"],
        "catalogFingerprint": index["catalogFingerprint"],
        "trustIndexFingerprint": index["fingerprint"],
        "candidateCount": len(index["candidateReceipts"]),
        "uniqueReceiptCount": len(index["receipts"]),
        "riskDistribution": distribution("riskLevel"),
        "trustDistribution": distribution("trustStatus"),
        "compatibilityDistribution": distribution("compatibilityStatus"),
        "routerEligibleCount": sum(bool(receipt.get("routerEligible")) for receipt in index["receipts"]),
        "routerExcludedCount": sum(not bool(receipt.get("routerEligible")) for receipt in index["receipts"]),
        "dependencyDeclarationCount": dependency_count,
        "dependencyReceiptCount": dependency_receipt_count,
        "licenseDeclaredCount": license_declared_count,
        "licenseMissingCount": len(index["receipts"]) - license_declared_count,
        "capabilityCounts": dict(sorted(capability_counts.items())),
        "sourceHosts": dict(sorted(source_hosts.items())),
        "findingCodes": dict(sorted(finding_codes.items())),
    }


__all__ = [
    "MAX_TRUST_DIRECTORY_DEPTH",
    "MAX_TRUST_FILE_BYTES",
    "MAX_TRUST_FILES",
    "MAX_TRUST_PATH_CHARS",
    "MAX_TRUST_TOTAL_BYTES",
    "SKILL_TRUST_INDEX_VERSION",
    "SKILL_TRUST_SCANNER_VERSION",
    "SKILL_TRUST_SUMMARY_VERSION",
    "SkillTrustFinding",
    "SkillTrustTreeEntry",
    "build_skill_trust_index",
    "build_skill_trust_report",
    "build_skill_trust_summary",
    "catalog_fingerprint_for",
    "receipt_id_for",
    "scan_skill_trust_receipt",
    "sha256_json",
    "source_key",
]
