from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any


WORKSPACE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
OPERATION_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
DEFAULT_ALLOWED_COMMANDS = {"python", "python3", "node", "npm", "npx", "git", "rg"}
SKILL_EVALUATION_ALLOWED_COMMANDS = {"python", "python3", "node", "rg"}
DEFAULT_PROFILE = "default"
SKILL_EVALUATION_PROFILE = "skill_evaluation_v1"
SUPPORTED_PROFILES = {DEFAULT_PROFILE, SKILL_EVALUATION_PROFILE}
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
MAX_MANIFEST_FILES = 500
MAX_MANIFEST_PREVIEW_CHARS = 4_000


class SandboxEngineError(RuntimeError):
    def __init__(self, message: str, *, code: str = "sandbox_error") -> None:
        super().__init__(message)
        self.code = code


class SandboxEngine:
    """Filesystem and process engine used only inside the isolated sidecar."""

    def __init__(
        self,
        root: str | Path,
        *,
        require_landlock: bool = True,
        allowed_commands: set[str] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.require_landlock = require_landlock
        self.allowed_commands = set(allowed_commands or DEFAULT_ALLOWED_COMMANDS)

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        action = str(request.get("action") or "").strip()
        if action == "health":
            return {
                "ok": True,
                "engine": "modelmirror-sandbox-v1",
                "landlock_required": self.require_landlock,
                "allowed_commands": sorted(self.allowed_commands),
                "profiles": {
                    DEFAULT_PROFILE: {
                        "allowed_commands": sorted(self.allowed_commands),
                        "network_policy": "container_network_none_required",
                    },
                    SKILL_EVALUATION_PROFILE: {
                        "allowed_commands": sorted(SKILL_EVALUATION_ALLOWED_COMMANDS),
                        "network_policy": "container_network_none_required",
                        "read_only_roots": ["inputs", "skills"],
                        "writable_roots": ["work", ".tmp"],
                        "write_file_roots": ["work"],
                        "provisioning": "capability_bound",
                        "lifecycle_actions": [
                            "ensure_workspace",
                            "seed_file",
                            "seal_workspace",
                            "collect_work_manifest",
                            "cleanup_workspace",
                        ],
                    },
                },
            }
        profile = self._profile(request.get("profile"))
        workspace_id = self._workspace_id(request.get("workspace_id"))
        if profile == SKILL_EVALUATION_PROFILE:
            if action == "ensure_workspace":
                return self._ensure_evaluation_workspace(workspace_id, request)
            workspace = self._evaluation_workspace(workspace_id, request)
            if action == "seed_file":
                return self._idempotent(workspace, action, request, self._seed_evaluation_file)
            if action == "seal_workspace":
                self._seal_evaluation_workspace(workspace)
                return {"ok": True, "workspace_id": workspace_id, "sealed": True}
            if action == "collect_work_manifest":
                self._seal_evaluation_workspace(workspace)
                return self._collect_work_manifest(workspace)
            if action == "cleanup_workspace":
                shutil.rmtree(workspace)
                return {"ok": True, "workspace_id": workspace_id, "removed": True}
            if action == "publish_artifact":
                raise SandboxEngineError(
                    "Artifact publishing is unavailable in the Skill evaluation profile.",
                    code="profile_action_denied",
                )
            self._seal_evaluation_workspace(workspace)
        else:
            workspace = self._ensure_workspace(workspace_id)
        if action == "ensure_workspace":
            return {"ok": True, "workspace_id": workspace_id, "profile": profile}
        if action == "list_files":
            return self._list_files(workspace, request)
        if action == "read_file":
            return self._read_file(workspace, request)
        if action == "write_file":
            return self._idempotent(workspace, action, request, self._write_file)
        if action == "search_files":
            return self._search_files(workspace, request)
        if action == "shell":
            return self._idempotent(workspace, action, request, self._shell)
        if action == "publish_artifact":
            return self._idempotent(workspace, action, request, self._publish_artifact)
        raise SandboxEngineError("Unsupported sandbox action.", code="unsupported_action")

    def _ensure_workspace(self, workspace_id: str) -> Path:
        workspace = (self.root / workspace_id).resolve()
        if workspace.parent != self.root:
            raise SandboxEngineError("Unsafe workspace identifier.", code="unsafe_workspace")
        marker = workspace / ".modelmirror" / "profile.json"
        if marker.exists():
            stored = self._read_profile_marker(marker)
            if stored.get("profile") == SKILL_EVALUATION_PROFILE:
                raise SandboxEngineError(
                    "Workspace is bound to a different sandbox profile.",
                    code="sandbox_profile_mismatch",
                )
        for name in ("inputs", "work", "artifacts", ".modelmirror/operations", ".tmp"):
            (workspace / name).mkdir(parents=True, exist_ok=True)
        return workspace

    def _ensure_evaluation_workspace(
        self,
        workspace_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        workspace = (self.root / workspace_id).resolve()
        if workspace.parent != self.root:
            raise SandboxEngineError("Unsafe workspace identifier.", code="unsafe_workspace")
        marker = workspace / ".modelmirror" / "profile.json"
        if marker.exists():
            self._validate_evaluation_capability(workspace, request)
            return {
                "ok": True,
                "workspace_id": workspace_id,
                "profile": SKILL_EVALUATION_PROFILE,
                "sealed": bool(self._read_profile_marker(marker).get("sealed")),
            }
        if workspace.exists() and any(workspace.iterdir()):
            raise SandboxEngineError(
                "Existing workspace cannot be rebound to the Skill evaluation profile.",
                code="sandbox_profile_mismatch",
            )
        for name in (
            "inputs",
            "work",
            "skills/evaluation-skill",
            ".modelmirror/operations",
            ".tmp",
        ):
            (workspace / name).mkdir(parents=True, exist_ok=True)
        capability = secrets.token_urlsafe(32)
        self._write_profile_marker(
            marker,
            {
                "profile": SKILL_EVALUATION_PROFILE,
                "capability_sha256": hashlib.sha256(capability.encode("utf-8")).hexdigest(),
                "sealed": False,
            },
        )
        return {
            "ok": True,
            "workspace_id": workspace_id,
            "profile": SKILL_EVALUATION_PROFILE,
            "provisioning_capability": capability,
            "sealed": False,
        }

    def _evaluation_workspace(self, workspace_id: str, request: dict[str, Any]) -> Path:
        workspace = (self.root / workspace_id).resolve()
        if workspace.parent != self.root:
            raise SandboxEngineError("Unsafe workspace identifier.", code="unsafe_workspace")
        if not workspace.exists():
            raise SandboxEngineError(
                "Skill evaluation workspace has not been initialized.",
                code="workspace_not_found",
            )
        self._validate_evaluation_capability(workspace, request)
        return workspace

    def _validate_evaluation_capability(
        self,
        workspace: Path,
        request: dict[str, Any],
    ) -> None:
        marker = workspace / ".modelmirror" / "profile.json"
        if not marker.exists():
            raise SandboxEngineError(
                "Workspace is not bound to the Skill evaluation profile.",
                code="sandbox_profile_mismatch",
            )
        stored = self._read_profile_marker(marker)
        if stored.get("profile") != SKILL_EVALUATION_PROFILE:
            raise SandboxEngineError(
                "Workspace is bound to a different sandbox profile.",
                code="sandbox_profile_mismatch",
            )
        supplied = str(request.get("provisioning_capability") or "")
        expected = str(stored.get("capability_sha256") or "")
        actual = hashlib.sha256(supplied.encode("utf-8")).hexdigest()
        if not supplied or not expected or not hmac.compare_digest(actual, expected):
            raise SandboxEngineError(
                "A valid Skill evaluation provisioning capability is required.",
                code="sandbox_profile_capability_invalid",
            )

    def _seal_evaluation_workspace(self, workspace: Path) -> None:
        marker = workspace / ".modelmirror" / "profile.json"
        stored = self._read_profile_marker(marker)
        if stored.get("sealed") is True:
            return
        stored["sealed"] = True
        self._write_profile_marker(marker, stored)

    def _seed_evaluation_file(
        self,
        workspace: Path,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        marker = workspace / ".modelmirror" / "profile.json"
        if self._read_profile_marker(marker).get("sealed") is True:
            raise SandboxEngineError(
                "Skill evaluation inputs are sealed and cannot be changed.",
                code="evaluation_workspace_sealed",
            )
        target = self._safe_path(workspace, request.get("path"), for_write=True)
        relative = target.relative_to(workspace)
        if not (
            relative.parts[0] == "inputs"
            or relative.parts[:2] == ("skills", "evaluation-skill")
        ):
            raise SandboxEngineError(
                "Evaluation seed files must be under inputs/ or skills/evaluation-skill/.",
                code="seed_scope_denied",
            )
        content = self._request_content(request)
        quota = self._quota(request)
        previous_size = target.stat().st_size if target.exists() and target.is_file() else 0
        if self._workspace_size(workspace) - previous_size + len(content) > quota:
            raise SandboxEngineError("Sandbox workspace quota exceeded.", code="quota_exceeded")
        if target.exists():
            if not target.is_file() or target.read_bytes() != content:
                raise SandboxEngineError(
                    "Evaluation seed files are immutable.",
                    code="seed_conflict",
                )
            return self._file_result(relative, content)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._reject_symlink_chain(workspace, target.parent)
        temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
        temporary.write_bytes(content)
        os.replace(temporary, target)
        return self._file_result(relative, content)

    def _collect_work_manifest(self, workspace: Path) -> dict[str, Any]:
        work = workspace / "work"
        files: list[dict[str, Any]] = []
        truncated = False
        for path in sorted(work.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if len(files) >= MAX_MANIFEST_FILES:
                truncated = True
                break
            content = path.read_bytes()
            text_preview: str | None = None
            preview_truncated = False
            if b"\x00" not in content[:4096]:
                decoded = content.decode("utf-8", errors="replace")
                text_preview = decoded[:MAX_MANIFEST_PREVIEW_CHARS]
                preview_truncated = len(decoded) > MAX_MANIFEST_PREVIEW_CHARS
            files.append(
                {
                    "path": path.relative_to(workspace).as_posix(),
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "text_preview": text_preview,
                    "preview_truncated": preview_truncated,
                }
            )
        return {"ok": True, "files": files, "truncated": truncated}

    def _list_files(self, workspace: Path, request: dict[str, Any]) -> dict[str, Any]:
        relative = str(request.get("path") or "").strip()
        target = self._safe_path(workspace, relative, allow_root=True)
        if not target.exists() or not target.is_dir():
            raise SandboxEngineError("Sandbox directory not found.", code="path_not_found")
        items: list[dict[str, Any]] = []
        for item in sorted(target.iterdir(), key=lambda value: (not value.is_dir(), value.name.lower())):
            if item.name == ".modelmirror" or item.is_symlink():
                continue
            stat = item.stat()
            items.append(
                {
                    "name": item.name,
                    "path": item.relative_to(workspace).as_posix(),
                    "kind": "directory" if item.is_dir() else "file",
                    "size_bytes": stat.st_size if item.is_file() else 0,
                    "updated_at": stat.st_mtime,
                }
            )
        return {"ok": True, "path": relative, "items": items[:500]}

    def _read_file(self, workspace: Path, request: dict[str, Any]) -> dict[str, Any]:
        target = self._safe_path(workspace, request.get("path"))
        if not target.exists() or not target.is_file():
            raise SandboxEngineError("Sandbox file not found.", code="path_not_found")
        limit = max(1, min(int(request.get("max_chars") or 20_000), 200_000))
        raw = target.read_bytes()
        if b"\x00" in raw[:4096]:
            raise SandboxEngineError("Binary files cannot be read as text.", code="binary_file")
        text = raw.decode("utf-8", errors="replace")
        return {
            "ok": True,
            "path": target.relative_to(workspace).as_posix(),
            "content": text[:limit],
            "truncated": len(text) > limit,
            "size_bytes": len(raw),
        }

    def _write_file(self, workspace: Path, request: dict[str, Any]) -> dict[str, Any]:
        target = self._safe_path(workspace, request.get("path"), for_write=True)
        relative = target.relative_to(workspace)
        profile = self._profile(request.get("profile"))
        allowed_roots = {"work"} if profile == SKILL_EVALUATION_PROFILE else {"inputs", "work", "skills"}
        if relative.parts[0] not in allowed_roots:
            raise SandboxEngineError(
                (
                    "Files may only be written under work/ in the Skill evaluation profile."
                    if profile == SKILL_EVALUATION_PROFILE
                    else "Files may only be written under inputs/, work/, or skills/."
                ),
                code="write_scope_denied",
            )
        content = self._request_content(request)
        quota = self._quota(request)
        previous_size = target.stat().st_size if target.exists() and target.is_file() else 0
        if self._workspace_size(workspace) - previous_size + len(content) > quota:
            raise SandboxEngineError("Sandbox workspace quota exceeded.", code="quota_exceeded")
        target.parent.mkdir(parents=True, exist_ok=True)
        self._reject_symlink_chain(workspace, target.parent)
        temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
        temporary.write_bytes(content)
        os.replace(temporary, target)
        return self._file_result(relative, content)

    def _search_files(self, workspace: Path, request: dict[str, Any]) -> dict[str, Any]:
        query = str(request.get("query") or "").strip()
        if not query or len(query) > 500:
            raise SandboxEngineError("Search query must contain 1-500 characters.", code="invalid_query")
        base = self._safe_path(workspace, request.get("path") or "work", allow_root=True)
        if not base.exists() or not base.is_dir():
            raise SandboxEngineError("Sandbox directory not found.", code="path_not_found")
        limit = max(1, min(int(request.get("limit") or 20), 100))
        needle = query.casefold()
        matches: list[dict[str, Any]] = []
        scanned = 0
        for path in sorted(base.rglob("*")):
            if len(matches) >= limit or scanned >= 500:
                break
            if path.is_symlink() or not path.is_file() or ".modelmirror" in path.parts:
                continue
            scanned += 1
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            raw = path.read_bytes()
            if b"\x00" in raw[:4096]:
                continue
            for line_number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), start=1):
                if needle in line.casefold():
                    matches.append(
                        {
                            "path": path.relative_to(workspace).as_posix(),
                            "line": line_number,
                            "preview": line[:500],
                        }
                    )
                    if len(matches) >= limit:
                        break
        return {"ok": True, "query": query, "matches": matches, "scanned_files": scanned}

    def _shell(self, workspace: Path, request: dict[str, Any]) -> dict[str, Any]:
        argv_raw = request.get("argv")
        if not isinstance(argv_raw, list) or not argv_raw:
            raise SandboxEngineError("sandbox_shell requires a non-empty argv array.", code="invalid_argv")
        argv = [str(item) for item in argv_raw]
        if len(argv) > 128 or any(not item or len(item) > 4096 or "\x00" in item or "\n" in item for item in argv):
            raise SandboxEngineError("sandbox_shell argv is invalid.", code="invalid_argv")
        command = argv[0]
        profile = self._profile(request.get("profile"))
        profile_allowed_commands = (
            SKILL_EVALUATION_ALLOWED_COMMANDS
            if profile == SKILL_EVALUATION_PROFILE
            else self.allowed_commands
        )
        if "/" in command or "\\" in command or command not in profile_allowed_commands:
            raise SandboxEngineError("Sandbox command is not allowed.", code="command_denied")
        requested_allowed = request.get("allowed_commands")
        if isinstance(requested_allowed, list):
            narrowed = {str(item).strip() for item in requested_allowed}
            if command not in narrowed:
                raise SandboxEngineError("Sandbox command is not enabled for this Agent.", code="command_denied")
        timeout = max(1, min(int(request.get("timeout_seconds") or 60), 300))
        cwd_relative = str(request.get("cwd") or "work").strip()
        cwd = self._safe_path(workspace, cwd_relative, allow_root=False)
        if not cwd.exists() or not cwd.is_dir():
            raise SandboxEngineError("Sandbox command cwd does not exist.", code="path_not_found")

        command_argv = argv
        if self.require_landlock:
            command_argv = [
                sys.executable,
                str(Path(__file__).with_name("landlock_exec.py")),
                str(workspace),
                *(
                    ["--skill-evaluation"]
                    if profile == SKILL_EVALUATION_PROFILE
                    else []
                ),
                "--",
                *argv,
            ]
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(workspace / "work"),
            "TMPDIR": str(workspace / ".tmp"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "NO_PROXY": "*",
            "no_proxy": "*",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
        started = time.monotonic()
        process = subprocess.Popen(
            command_argv,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            stdout, stderr = process.communicate()
        stdout_text, stdout_truncated = self._bounded_output(stdout)
        stderr_text, stderr_truncated = self._bounded_output(stderr)
        if timed_out:
            raise SandboxEngineError(
                f"Sandbox command timed out after {timeout} seconds.",
                code="command_timeout",
            )
        return {
            "ok": True,
            "argv": argv,
            "exit_code": process.returncode,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
        }

    def _publish_artifact(self, workspace: Path, request: dict[str, Any]) -> dict[str, Any]:
        source = self._safe_path(workspace, request.get("path"))
        if not source.exists() or not source.is_file():
            raise SandboxEngineError("Artifact source file not found.", code="path_not_found")
        if source.relative_to(workspace).parts[0] not in {"work", "artifacts"}:
            raise SandboxEngineError("Only work/ files can be published.", code="publish_scope_denied")
        artifact_id = str(request.get("artifact_id") or "").strip()
        if not WORKSPACE_ID_PATTERN.fullmatch(artifact_id):
            raise SandboxEngineError("Invalid artifact identifier.", code="invalid_artifact_id")
        filename = self._safe_filename(str(request.get("filename") or source.name))
        destination = workspace / "artifacts" / f"{artifact_id}-{filename}"
        if source != destination:
            shutil.copyfile(source, destination)
        content = destination.read_bytes()
        return {
            "ok": True,
            "artifact_id": artifact_id,
            "path": destination.relative_to(workspace).as_posix(),
            "filename": filename,
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    def _idempotent(self, workspace: Path, action: str, request: dict[str, Any], handler: Any) -> dict[str, Any]:
        operation_id = str(request.get("operation_id") or "").strip()
        if not OPERATION_ID_PATTERN.fullmatch(operation_id):
            raise SandboxEngineError("A valid operation_id is required.", code="invalid_operation_id")
        operation_path = workspace / ".modelmirror" / "operations" / f"{hashlib.sha256(operation_id.encode()).hexdigest()}.json"
        fingerprint_payload = {key: value for key, value in request.items() if key != "operation_id"}
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        if operation_path.exists():
            stored = json.loads(operation_path.read_text(encoding="utf-8"))
            if stored.get("fingerprint") != fingerprint:
                raise SandboxEngineError("operation_id was reused with different arguments.", code="operation_conflict")
            return {**dict(stored.get("result") or {}), "replayed": True}
        result = handler(workspace, request)
        payload = {"fingerprint": fingerprint, "result": result, "completed_at": time.time()}
        temporary = operation_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, operation_path)
        return {**result, "replayed": False}

    def _safe_path(self, workspace: Path, value: Any, *, allow_root: bool = False, for_write: bool = False) -> Path:
        raw = str(value or "").strip().replace("\\", "/")
        if not raw and allow_root:
            return workspace
        pure = PurePosixPath(raw)
        if not raw or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise SandboxEngineError("Unsafe sandbox path.", code="unsafe_path")
        if pure.parts[0] == ".modelmirror":
            raise SandboxEngineError("Internal sandbox paths are not accessible.", code="unsafe_path")
        candidate = workspace.joinpath(*pure.parts)
        self._reject_symlink_chain(workspace, candidate.parent if for_write else candidate)
        resolved = candidate.resolve(strict=False)
        if resolved != workspace and workspace not in resolved.parents:
            raise SandboxEngineError("Sandbox path escapes the workspace.", code="unsafe_path")
        if candidate.exists() and candidate.is_symlink():
            raise SandboxEngineError("Symbolic links are not accessible.", code="symlink_denied")
        return candidate

    @staticmethod
    def _reject_symlink_chain(workspace: Path, target: Path) -> None:
        current = workspace
        try:
            relative = target.relative_to(workspace)
        except ValueError as exc:
            raise SandboxEngineError("Sandbox path escapes the workspace.", code="unsafe_path") from exc
        for part in relative.parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise SandboxEngineError("Symbolic links are not accessible.", code="symlink_denied")

    @staticmethod
    def _workspace_id(value: Any) -> str:
        clean = str(value or "").strip()
        if not WORKSPACE_ID_PATTERN.fullmatch(clean):
            raise SandboxEngineError("Invalid workspace identifier.", code="invalid_workspace_id")
        return clean

    @staticmethod
    def _safe_filename(value: str) -> str:
        clean = Path(value).name.strip()
        if not clean or clean in {".", ".."} or len(clean) > 200:
            raise SandboxEngineError("Invalid artifact filename.", code="invalid_filename")
        return re.sub(r"[^A-Za-z0-9._ -]", "_", clean)

    @staticmethod
    def _workspace_size(workspace: Path) -> int:
        total = 0
        for path in workspace.rglob("*"):
            if path.is_file() and not path.is_symlink() and ".modelmirror" not in path.parts:
                total += path.stat().st_size
        return total

    @staticmethod
    def _request_content(request: dict[str, Any]) -> bytes:
        if "content_base64" in request:
            try:
                content = base64.b64decode(
                    str(request.get("content_base64") or ""),
                    validate=True,
                )
            except ValueError as exc:
                raise SandboxEngineError(
                    "Invalid base64 file content.",
                    code="invalid_content",
                ) from exc
        else:
            content = str(request.get("content") or "").encode("utf-8")
        if len(content) > 10 * 1024 * 1024:
            raise SandboxEngineError(
                "File exceeds the 10 MB operation limit.",
                code="file_too_large",
            )
        return content

    @staticmethod
    def _quota(request: dict[str, Any]) -> int:
        return max(
            1,
            min(
                int(request.get("quota_bytes") or 256 * 1024 * 1024),
                1024 * 1024 * 1024,
            ),
        )

    @staticmethod
    def _file_result(relative: Path, content: bytes) -> dict[str, Any]:
        return {
            "ok": True,
            "path": relative.as_posix(),
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    @staticmethod
    def _profile(value: Any) -> str:
        profile = str(value or DEFAULT_PROFILE).strip() or DEFAULT_PROFILE
        if profile not in SUPPORTED_PROFILES:
            raise SandboxEngineError(
                "Unsupported sandbox profile.",
                code="sandbox_profile_unsupported",
            )
        return profile

    @staticmethod
    def _read_profile_marker(marker: Path) -> dict[str, Any]:
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise SandboxEngineError(
                "Sandbox profile metadata is invalid.",
                code="sandbox_profile_invalid",
            ) from exc
        if not isinstance(payload, dict):
            raise SandboxEngineError(
                "Sandbox profile metadata is invalid.",
                code="sandbox_profile_invalid",
            )
        return payload

    @staticmethod
    def _write_profile_marker(marker: Path, payload: dict[str, Any]) -> None:
        marker.parent.mkdir(parents=True, exist_ok=True)
        temporary = marker.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, marker)

    @staticmethod
    def _bounded_output(value: bytes) -> tuple[str, bool]:
        truncated = len(value) > MAX_OUTPUT_BYTES
        return value[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"), truncated
