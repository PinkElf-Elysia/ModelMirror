from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


MAX_PROJECT_CHECKS = 8
MAX_COMMAND_ARGS = 64
MAX_COMMAND_BYTES = 8 * 1024
MAX_COMMAND_NAME_CHARS = 80
MAX_COMMAND_PURPOSE_CHARS = 240
MIN_COMMAND_TIMEOUT_SECONDS = 1
MAX_COMMAND_TIMEOUT_SECONDS = 300
MAX_PACK_MANIFEST_BYTES = 64 * 1024
MAX_PACK_PATHS = 16
PACK_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_HASH_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_DIRECT_SHELLS = frozenset(
    {"bash", "sh", "dash", "zsh", "fish", "cmd", "powershell", "pwsh"}
)
_DEPENDENCY_INPUT_NAMES = frozenset(
    {
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "package.json",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "pnpm-lock.yaml",
        "yarn.lock",
    }
)
_NODE_SCRIPT_ORDER = ("test", "typecheck", "lint", "build")
_NODE_SCRIPT_LABELS = {
    "test": "运行页面测试",
    "typecheck": "检查页面类型",
    "lint": "检查页面规范",
    "build": "检查页面构建",
}


class CommandContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ProjectCommandKind(StrEnum):
    TEST = "test"
    BUILD = "build"
    LINT = "lint"
    TYPECHECK = "typecheck"
    CUSTOM = "custom"


class ProjectCommandOrigin(StrEnum):
    AUTO = "auto"
    MANIFEST = "manifest"
    AGENT = "agent"


@dataclass(frozen=True, slots=True)
class ProjectCommand:
    command_id: str
    name: str
    kind: ProjectCommandKind
    argv: tuple[str, ...]
    cwd: str
    timeout_seconds: int
    origin: ProjectCommandOrigin

    def to_internal_dict(self) -> dict[str, Any]:
        return {
            "id": self.command_id,
            "name": self.name,
            "kind": self.kind.value,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "timeout_seconds": self.timeout_seconds,
            "origin": self.origin.value,
        }

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.command_id,
            "name": self.name,
            "kind": self.kind.value,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True, slots=True)
class ProjectVerificationConfig:
    auto: bool = True
    runner_pack: str | None = None
    commands: tuple[ProjectCommand, ...] = ()

    def to_internal_dict(self) -> dict[str, Any]:
        return {
            "auto": self.auto,
            "runner_pack": self.runner_pack,
            "commands": [item.to_internal_dict() for item in self.commands],
        }


@dataclass(frozen=True, slots=True)
class RunnerPackManifest:
    pack_id: str
    platform: str
    python_version: str
    node_version: str
    inputs: tuple[tuple[str, str], ...]
    python_paths: tuple[str, ...]
    node_modules: tuple[tuple[str, str], ...]
    bin_paths: tuple[str, ...]
    fingerprint: str


def parse_project_verification(value: Any) -> ProjectVerificationConfig:
    if value is None:
        return ProjectVerificationConfig()
    if not isinstance(value, dict) or not set(value).issubset(
        {"auto", "runner_pack", "commands"}
    ):
        raise CommandContractError(
            "project_verification_invalid",
            "Project verification config is invalid",
        )
    auto = value.get("auto", True)
    if type(auto) is not bool:
        raise CommandContractError(
            "project_verification_invalid",
            "Project verification auto flag is invalid",
        )
    raw_pack = value.get("runner_pack")
    if raw_pack is not None and (
        not isinstance(raw_pack, str) or PACK_ID_PATTERN.fullmatch(raw_pack) is None
    ):
        raise CommandContractError("runner_pack_invalid", "Runner pack id is invalid")
    raw_commands = value.get("commands", [])
    if not isinstance(raw_commands, list) or len(raw_commands) > MAX_PROJECT_CHECKS:
        raise CommandContractError(
            "project_commands_invalid",
            "Project verification command count is invalid",
        )
    commands = tuple(
        normalize_project_command(item, origin=ProjectCommandOrigin.MANIFEST)
        for item in raw_commands
    )
    _require_unique_commands(commands)
    return ProjectVerificationConfig(
        auto=auto,
        runner_pack=raw_pack,
        commands=commands,
    )


def normalize_project_command(
    value: Any,
    *,
    origin: ProjectCommandOrigin,
) -> ProjectCommand:
    if not isinstance(value, dict) or set(value) != {
        "name",
        "kind",
        "argv",
        "cwd",
        "timeout_seconds",
    }:
        raise CommandContractError("project_command_invalid", "Project command is invalid")
    name_limit = (
        MAX_COMMAND_PURPOSE_CHARS
        if origin is ProjectCommandOrigin.AGENT
        else MAX_COMMAND_NAME_CHARS
    )
    name = _normalize_text(value["name"], name_limit, "project_command_invalid")
    try:
        kind = ProjectCommandKind(value["kind"])
    except (TypeError, ValueError) as exc:
        raise CommandContractError(
            "project_command_invalid",
            "Project command kind is invalid",
        ) from exc
    if origin is ProjectCommandOrigin.AGENT and kind is not ProjectCommandKind.CUSTOM:
        raise CommandContractError("project_command_invalid", "Agent command kind is invalid")
    argv = normalize_argv(value["argv"])
    cwd = normalize_command_cwd(value["cwd"])
    timeout_seconds = value["timeout_seconds"]
    if (
        type(timeout_seconds) is not int
        or not MIN_COMMAND_TIMEOUT_SECONDS
        <= timeout_seconds
        <= MAX_COMMAND_TIMEOUT_SECONDS
    ):
        raise CommandContractError(
            "project_command_timeout_invalid",
            "Project command timeout is invalid",
        )
    canonical = {
        "name": name,
        "kind": kind.value,
        "argv": list(argv),
        "cwd": cwd,
        "timeout_seconds": timeout_seconds,
        "origin": origin.value,
    }
    command_id = "command-" + hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()[:24]
    return ProjectCommand(
        command_id=command_id,
        name=name,
        kind=kind,
        argv=argv,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        origin=origin,
    )


def normalize_agent_command(
    *,
    argv: Any,
    cwd: Any,
    purpose: Any,
    timeout_seconds: Any,
) -> ProjectCommand:
    normalized_cwd = cwd
    if isinstance(cwd, str):
        if cwd == "/workspace":
            normalized_cwd = "."
        elif cwd.startswith("/workspace/"):
            normalized_cwd = cwd.removeprefix("/workspace/")
    return normalize_project_command(
        {
            "name": purpose,
            "kind": ProjectCommandKind.CUSTOM.value,
            "argv": argv,
            "cwd": normalized_cwd,
            "timeout_seconds": timeout_seconds,
        },
        origin=ProjectCommandOrigin.AGENT,
    )


def normalize_argv(value: Any) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > MAX_COMMAND_ARGS
        or not all(isinstance(item, str) for item in value)
    ):
        raise CommandContractError("command_argv_invalid", "Command argv is invalid")
    argv: list[str] = []
    total_bytes = 0
    for item in value:
        if (
            not item
            or item != unicodedata.normalize("NFC", item)
            or any(_is_control(character) for character in item)
        ):
            raise CommandContractError("command_argv_invalid", "Command argv is invalid")
        try:
            total_bytes += len(item.encode("utf-8", errors="strict"))
        except UnicodeEncodeError as exc:
            raise CommandContractError("command_argv_invalid", "Command argv is invalid") from exc
        if _looks_like_absolute_path(item) or item == ".." or item.startswith("../"):
            raise CommandContractError("command_path_invalid", "Command path is invalid")
        argv.append(item)
    if total_bytes > MAX_COMMAND_BYTES:
        raise CommandContractError("command_argv_invalid", "Command argv is too large")
    executable = PurePosixPath(argv[0]).name.casefold()
    if executable.endswith(".exe"):
        executable = executable[:-4]
    if executable in _DIRECT_SHELLS:
        raise CommandContractError("command_shell_denied", "Direct shell commands are denied")
    _normalize_relative_path(argv[0], allow_dot=False, allow_plain_name=True)
    return tuple(argv)


def normalize_command_cwd(value: Any) -> str:
    if value == ".":
        return "."
    if not isinstance(value, str):
        raise CommandContractError("command_cwd_invalid", "Command cwd is invalid")
    return _normalize_relative_path(value, allow_dot=False, allow_plain_name=True)


def detect_project_commands(
    snapshot_root: Path,
    config: ProjectVerificationConfig,
) -> tuple[ProjectCommand, ...]:
    root = Path(snapshot_root)
    commands: list[ProjectCommand] = list(config.commands)
    seen = {(item.argv, item.cwd) for item in commands}
    if config.auto:
        auto_commands: list[ProjectCommand] = []
        if _has_python_tests(root):
            auto_commands.append(
                _auto_command(
                    name="运行 Python 测试",
                    kind=ProjectCommandKind.TEST,
                    argv=["python", "-m", "pytest", "-p", "no:cacheprovider", "-q"],
                )
            )
        auto_commands.extend(_detect_node_commands(root))
        for item in auto_commands:
            key = (item.argv, item.cwd)
            if key not in seen and len(commands) < MAX_PROJECT_CHECKS:
                commands.append(item)
                seen.add(key)
    return tuple(commands)


def dependency_input_hashes(
    snapshot_root: Path,
    paths: Iterable[str] | None = None,
) -> tuple[tuple[str, str], ...]:
    root = Path(snapshot_root)
    items: list[tuple[str, str]] = []
    names = (
        sorted(_DEPENDENCY_INPUT_NAMES)
        if paths is None
        else sorted(
            _normalize_relative_path(
                item,
                allow_dot=False,
                allow_plain_name=True,
            )
            for item in paths
        )
    )
    if len(names) > MAX_PACK_PATHS or len(names) != len(set(names)):
        raise CommandContractError("runner_pack_invalid", "Runner pack inputs are invalid")
    for name in names:
        path = root / name
        if path.is_file() and not path.is_symlink():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            items.append((name, f"sha256:{digest}"))
    return tuple(items)


def load_runner_pack_manifest(pack_root: Path, pack_id: str) -> RunnerPackManifest:
    if PACK_ID_PATTERN.fullmatch(pack_id) is None:
        raise CommandContractError("runner_pack_invalid", "Runner pack id is invalid")
    root = Path(pack_root)
    pack_path = root / pack_id
    manifest_path = pack_path / "pack.json"
    if (
        not root.is_absolute()
        or root.is_symlink()
        or pack_path.is_symlink()
        or manifest_path.is_symlink()
        or not manifest_path.is_file()
    ):
        raise CommandContractError("runner_pack_unavailable", "Runner pack is unavailable")
    try:
        raw = manifest_path.read_bytes()
        if len(raw) > MAX_PACK_MANIFEST_BYTES:
            raise CommandContractError("runner_pack_invalid", "Runner pack manifest is too large")
        payload = json.loads(raw.decode("utf-8", errors="strict"))
    except CommandContractError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CommandContractError("runner_pack_invalid", "Runner pack manifest is invalid") from exc
    expected = {
        "version",
        "id",
        "platform",
        "python_version",
        "node_version",
        "inputs",
        "python_paths",
        "node_modules",
        "bin_paths",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise CommandContractError("runner_pack_invalid", "Runner pack manifest is invalid")
    if payload["version"] != 1 or payload["id"] != pack_id:
        raise CommandContractError("runner_pack_invalid", "Runner pack identity is invalid")
    if payload["platform"] != "linux-x86_64":
        raise CommandContractError("runner_pack_platform_mismatch", "Runner pack platform is invalid")
    if payload["python_version"] != "3.12" or payload["node_version"] != "22":
        raise CommandContractError("runner_pack_runtime_mismatch", "Runner pack runtime is invalid")
    inputs = _normalize_hash_mapping(payload["inputs"], allow_dot_key=False)
    python_paths = _normalize_path_list(payload["python_paths"])
    bin_paths = _normalize_path_list(payload["bin_paths"])
    node_modules = _normalize_path_mapping(payload["node_modules"])
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return RunnerPackManifest(
        pack_id=pack_id,
        platform=payload["platform"],
        python_version=payload["python_version"],
        node_version=payload["node_version"],
        inputs=inputs,
        python_paths=python_paths,
        node_modules=node_modules,
        bin_paths=bin_paths,
        fingerprint=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def runner_pack_matches_project(
    manifest: RunnerPackManifest,
    snapshot_root: Path,
) -> bool:
    expected_paths = tuple(path for path, _digest in manifest.inputs)
    return manifest.inputs == dependency_input_hashes(snapshot_root, expected_paths)


def command_plan_fingerprint(
    commands: Iterable[ProjectCommand],
    *,
    source_fingerprint: str,
    pack_fingerprint: str = "",
) -> str:
    payload = {
        "commands": [item.to_internal_dict() for item in commands],
        "source_fingerprint": source_fingerprint,
        "pack_fingerprint": pack_fingerprint,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _auto_command(
    *,
    name: str,
    kind: ProjectCommandKind,
    argv: list[str],
) -> ProjectCommand:
    return normalize_project_command(
        {
            "name": name,
            "kind": kind.value,
            "argv": argv,
            "cwd": ".",
            "timeout_seconds": MAX_COMMAND_TIMEOUT_SECONDS,
        },
        origin=ProjectCommandOrigin.AUTO,
    )


def _has_python_tests(root: Path) -> bool:
    if (root / "tests").is_dir() or (root / "pytest.ini").is_file():
        return True
    markers = {
        "pyproject.toml": "[tool.pytest.",
        "setup.cfg": "[tool:pytest]",
        "tox.ini": "pytest",
    }
    for name, marker in markers.items():
        path = root / name
        if path.is_symlink() or not path.is_file():
            continue
        try:
            raw = path.read_bytes()
            if len(raw) <= 1024 * 1024 and marker in raw.decode("utf-8", errors="strict"):
                return True
        except (OSError, UnicodeError):
            continue
    return False


def _detect_node_commands(root: Path) -> tuple[ProjectCommand, ...]:
    package_json = root / "package.json"
    if package_json.is_symlink() or not package_json.is_file():
        return ()
    try:
        raw = package_json.read_bytes()
        if len(raw) > 1024 * 1024:
            return ()
        payload = json.loads(raw.decode("utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ()
    scripts = payload.get("scripts") if isinstance(payload, dict) else None
    if not isinstance(scripts, dict):
        return ()
    commands: list[ProjectCommand] = []
    for script in _NODE_SCRIPT_ORDER:
        value = scripts.get(script)
        if isinstance(value, str) and value.strip():
            commands.append(
                _auto_command(
                    name=_NODE_SCRIPT_LABELS[script],
                    kind=ProjectCommandKind(script),
                    argv=["npm", "run", script],
                )
            )
    return tuple(commands)


def _normalize_text(value: Any, limit: int, code: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > limit
        or value != unicodedata.normalize("NFC", value)
        or any(_is_control(character) for character in value)
    ):
        raise CommandContractError(code, "Command text is invalid")
    return value


def _normalize_relative_path(
    value: str,
    *,
    allow_dot: bool,
    allow_plain_name: bool,
) -> str:
    if not isinstance(value, str) or not value or "\\" in value or _WINDOWS_ABSOLUTE.match(value):
        raise CommandContractError("command_path_invalid", "Command path is invalid")
    if value == "." and allow_dot:
        return value
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CommandContractError("command_path_invalid", "Command path is invalid")
    normalized = path.as_posix()
    if normalized != value or (not allow_plain_name and len(path.parts) == 1):
        raise CommandContractError("command_path_invalid", "Command path is invalid")
    return normalized


def _looks_like_absolute_path(value: str) -> bool:
    if _WINDOWS_ABSOLUTE.match(value) or value.startswith("/"):
        return True
    if "=" in value:
        possible_path = value.split("=", maxsplit=1)[1]
        return bool(_WINDOWS_ABSOLUTE.match(possible_path) or possible_path.startswith("/"))
    return False


def _normalize_hash_mapping(value: Any, *, allow_dot_key: bool) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict) or len(value) > MAX_PACK_PATHS:
        raise CommandContractError("runner_pack_invalid", "Runner pack inputs are invalid")
    items: list[tuple[str, str]] = []
    for key, digest in value.items():
        path = "." if key == "." and allow_dot_key else _normalize_relative_path(
            key,
            allow_dot=False,
            allow_plain_name=True,
        )
        if not isinstance(digest, str) or _HASH_PATTERN.fullmatch(digest) is None:
            raise CommandContractError("runner_pack_invalid", "Runner pack hash is invalid")
        items.append((path, digest))
    return tuple(sorted(items))


def _normalize_path_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MAX_PACK_PATHS:
        raise CommandContractError("runner_pack_invalid", "Runner pack paths are invalid")
    paths = tuple(
        _normalize_relative_path(item, allow_dot=False, allow_plain_name=True)
        for item in value
    )
    if len(paths) != len(set(paths)):
        raise CommandContractError("runner_pack_invalid", "Runner pack paths are duplicated")
    return paths


def _normalize_path_mapping(value: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict) or len(value) > MAX_PACK_PATHS:
        raise CommandContractError("runner_pack_invalid", "Runner pack paths are invalid")
    items: list[tuple[str, str]] = []
    for key, path in value.items():
        cwd = normalize_command_cwd(key)
        safe_path = _normalize_relative_path(path, allow_dot=False, allow_plain_name=True)
        items.append((cwd, safe_path))
    if len({item[0] for item in items}) != len(items):
        raise CommandContractError("runner_pack_invalid", "Runner pack paths are duplicated")
    return tuple(sorted(items))


def _require_unique_commands(commands: Iterable[ProjectCommand]) -> None:
    identities = [(item.argv, item.cwd) for item in commands]
    if len(identities) != len(set(identities)):
        raise CommandContractError("project_commands_duplicate", "Project commands are duplicated")


def _is_control(value: str) -> bool:
    return unicodedata.category(value).startswith("C")
