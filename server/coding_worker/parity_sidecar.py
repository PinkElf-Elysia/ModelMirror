from __future__ import annotations

import argparse
import hashlib
import hmac
import io
import json
import os
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from .parity import PARITY_PROTOCOL, PublicParityFixture, load_public_fixture_bundle
from .parity_runner import (
    MAX_RUNNER_RESPONSE_BYTES,
    ParityArtifactReference,
    ParityCheckReceipt,
    ParityCheckRequest,
    ParityExecutionExport,
    ParityExecutionRequest,
)


MAX_RPC_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 4096
MAX_ARCHIVE_FILE_BYTES = 16 * 1024 * 1024
TERMINAL_STATES = {
    "completed",
    "blocked",
    "failed",
    "cancelled",
    "budget_limited",
    "expired",
}


class ParitySidecarError(RuntimeError):
    pass


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or "\0" in value:
        raise ParitySidecarError("parity sidecar configuration is incomplete")
    return value


def _safe_child(root: Path, name: str, *, suffix: str = "") -> Path:
    if not name or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-" for character in name):
        raise ParitySidecarError("parity artifact identifier is invalid")
    root = root.resolve()
    candidate = root / f"{name}{suffix}"
    if candidate.parent.resolve() != root or candidate.is_symlink():
        raise ParitySidecarError("parity artifact path is unsafe")
    return candidate


def _load_fixture(request: ParityExecutionRequest) -> PublicParityFixture:
    bundle = load_public_fixture_bundle(
        Path(_required_environment("CODING_PARITY_PUBLIC_FIXTURES"))
    )
    fixture = next(
        (item for item in bundle.fixtures if item.fixture_id == request.fixture_id),
        None,
    )
    if (
        fixture is None
        or fixture.fixture_revision != request.fixture_revision
        or fixture.initial_tree_hash != request.initial_tree_hash
    ):
        raise ParitySidecarError("parity fixture binding is invalid")
    return fixture


def _assert_route_binding(request: ParityExecutionRequest) -> None:
    if (
        request.model_route != _required_environment("CODING_PARITY_MODEL_ROUTE")
        or request.model_route_catalog_sha256
        != _required_environment("CODING_PARITY_ROUTE_CATALOG_SHA256")
        or request.model_route_receipt_sha256
        != _required_environment("CODING_PARITY_ROUTE_RECEIPT_SHA256")
    ):
        raise ParitySidecarError("parity model route binding is invalid")


def _materialize_fixture(fixture: PublicParityFixture, repository: Path) -> None:
    repository.mkdir(parents=True, exist_ok=False)
    for entry in fixture.files:
        relative = PurePosixPath(entry.path)
        destination = repository.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(entry.content_bytes())
    environment = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(repository.parent / "home"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }
    (repository.parent / "home").mkdir(mode=0o700)
    for argv in (
        ("git", "init", "--quiet", "--initial-branch=parity"),
        ("git", "add", "--all"),
        (
            "git",
            "-c",
            "user.name=ModelMirror Parity",
            "-c",
            "user.email=parity@modelmirror.local",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ),
    ):
        completed = subprocess.run(
            argv,
            cwd=repository,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
        if completed.returncode != 0:
            raise ParitySidecarError("parity fixture could not be materialized")


def _workspace_files(repository: Path) -> list[tuple[str, bytes, bool]]:
    files: list[tuple[str, bytes, bool]] = []
    for current, directories, names in os.walk(repository, topdown=True, followlinks=False):
        current_path = Path(current)
        if current_path == repository:
            directories[:] = [name for name in directories if name != ".git"]
        directories.sort()
        for name in directories:
            if (current_path / name).is_symlink():
                raise ParitySidecarError("parity workspace contains a link")
        for name in sorted(names):
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                raise ParitySidecarError("parity workspace entry is unsafe")
            relative = path.relative_to(repository).as_posix()
            content = path.read_bytes()
            if len(content) > MAX_ARCHIVE_FILE_BYTES:
                raise ParitySidecarError("parity workspace file is too large")
            executable = bool(path.stat().st_mode & stat.S_IXUSR)
            files.append((relative, content, executable))
            if len(files) > MAX_ARCHIVE_ENTRIES:
                raise ParitySidecarError("parity workspace has too many entries")
    return files


def _tree_hash(files: Sequence[tuple[str, bytes, bool]]) -> str:
    digest = hashlib.sha256()
    for relative, content, _executable in sorted(files):
        encoded = relative.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def _export_workspace(
    *, run_id: str, repository: Path
) -> tuple[ParityArtifactReference, str, str]:
    files = _workspace_files(repository)
    final_tree_hash = _tree_hash(files)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for relative, content, executable in sorted(files):
            info = tarfile.TarInfo(relative)
            info.size = len(content)
            info.mode = 0o755 if executable else 0o644
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(content))
    content = output.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    artifact_id = f"export_{hashlib.sha256(f'{run_id}:{digest}'.encode()).hexdigest()[:32]}"
    root = Path(_required_environment("CODING_PARITY_EXPORT_ROOT"))
    root.mkdir(parents=True, exist_ok=True)
    destination = _safe_child(root, artifact_id, suffix=".tar")
    temporary = _safe_child(root, f".{artifact_id}.building")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    manifest_sha = _canonical_sha256(
        {
            "artifact_id": artifact_id,
            "sha256": digest,
            "size": len(content),
            "tree_hash": final_tree_hash,
        }
    )
    return (
        ParityArtifactReference(
            artifact_id=artifact_id, sha256=digest, size_bytes=len(content)
        ),
        final_tree_hash,
        manifest_sha,
    )


def _opencode_configuration(fixture: PublicParityFixture) -> dict[str, Any]:
    model_id = _required_environment("CODING_PARITY_MODEL_ID")
    allowed_commands = {"*": "deny"}
    for check in fixture.visible_checks:
        allowed_commands[shlex.join(check.argv)] = "allow"
        allowed_commands[" ".join(check.argv)] = "allow"
    permission: dict[str, Any] = {
        "*": "deny",
        "read": "allow",
        "edit": "allow",
        "glob": "allow",
        "grep": "allow",
        "list": "allow",
        "lsp": "allow",
        "bash": allowed_commands,
        "task": "allow",
        "todowrite": "allow",
        "question": "deny",
        "external_directory": "deny",
        "webfetch": "deny",
        "websearch": "deny",
        "skill": "deny",
    }
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": f"modelmirror/{model_id}",
        "permission": permission,
        "provider": {
            "modelmirror": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "ModelMirror parity route",
                "options": {
                    "baseURL": _required_environment("CODING_PARITY_MODEL_BASE_URL"),
                    "apiKey": "{env:CODING_PARITY_ROUTE_KEY}",
                },
                "models": {model_id: {"name": "Controlled parity model"}},
            }
        },
        "plugin": [],
        "mcp": {},
        "instructions": [],
        "share": "disabled",
        "autoupdate": False,
    }


def _extract_usage(payload: object) -> tuple[int, int]:
    best = (0, 0)
    if isinstance(payload, dict):
        usage = payload.get("usage")
        if isinstance(usage, dict):
            raw_input = usage.get("input_tokens", usage.get("inputTokens", 0))
            raw_output = usage.get("output_tokens", usage.get("outputTokens", 0))
            if isinstance(raw_input, int) and isinstance(raw_output, int):
                best = (max(0, raw_input), max(0, raw_output))
        for value in payload.values():
            nested = _extract_usage(value)
            if sum(nested) > sum(best):
                best = nested
    elif isinstance(payload, list):
        for value in payload:
            nested = _extract_usage(value)
            if sum(nested) > sum(best):
                best = nested
    return best


def _native_runner(request: ParityExecutionRequest) -> ParityExecutionExport:
    _assert_route_binding(request)
    fixture = _load_fixture(request)
    runtime_root = Path(_required_environment("CODING_PARITY_WORKSPACE_ROOT"))
    runtime_root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    input_tokens = output_tokens = tool_calls = 0
    timed_out = False
    with tempfile.TemporaryDirectory(prefix="native-", dir=runtime_root) as temporary:
        root = Path(temporary)
        repository = root / "repo"
        _materialize_fixture(fixture, repository)
        version = subprocess.run(
            ("opencode", "--version"),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if version.returncode != 0 or version.stdout.strip() != "1.18.9":
            raise ParitySidecarError("native OpenCode version is not pinned")
        home = root / "opencode-home"
        home.mkdir(mode=0o700)
        environment = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "XDG_STATE_HOME": str(home / ".local/state"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "OPENCODE_CONFIG_CONTENT": json.dumps(
                _opencode_configuration(fixture), separators=(",", ":")
            ),
            "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
            "OPENCODE_PURE": "1",
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "OPENCODE_DISABLE_AUTOCOMPACT": "1",
            "OPENCODE_DISABLE_MODELS_FETCH": "1",
            "OPENCODE_AUTH_CONTENT": "{}",
            "CODING_PARITY_ROUTE_KEY": _required_environment(
                "CODING_PARITY_ROUTE_KEY"
            ),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
        try:
            completed = subprocess.run(
                (
                    "opencode",
                    "run",
                    "--format",
                    "json",
                    "--dir",
                    str(repository),
                    request.objective,
                ),
                cwd=repository,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=request.budget.max_active_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            completed = None
        if completed is not None:
            if len(completed.stdout.encode("utf-8")) > request.budget.max_output_bytes:
                raise ParitySidecarError("native OpenCode output exceeded budget")
            for line in completed.stdout.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("type") == "tool_use":
                    tool_calls += 1
                observed = _extract_usage(event)
                if sum(observed) > input_tokens + output_tokens:
                    input_tokens, output_tokens = observed
        artifact, final_tree, artifact_manifest = _export_workspace(
            run_id=request.run_id, repository=repository
        )
        return ParityExecutionExport(
            run_id=request.run_id,
            task_id=request.task_id,
            engine=request.engine,
            attempt=request.attempt,
            engine_version="1.18.9",
            model_route_receipt_sha256=request.model_route_receipt_sha256,
            fixture_bundle_sha256=request.fixture_bundle_sha256,
            runner_image_digest=request.runner_image_digest,
            candidate_sha=request.candidate_sha,
            task_manifest_sha256=request.task_manifest_sha256,
            initial_tree_hash=request.initial_tree_hash,
            final_tree_hash=final_tree,
            workspace_export=artifact,
            raw_artifact_manifest_sha256=artifact_manifest,
            timeout=timed_out,
            stuck=(completed is not None and completed.returncode != 0),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool_calls=tool_calls,
            active_seconds=time.monotonic() - started,
        )


def _approval_allowed(approval: Mapping[str, Any], fixture: PublicParityFixture) -> bool:
    request = approval.get("request")
    if not isinstance(request, dict):
        return False
    allowed = {tuple(check.argv) for check in fixture.visible_checks}
    argv = request.get("argv")
    if isinstance(argv, list) and all(isinstance(item, str) for item in argv):
        return tuple(argv) in allowed
    script_sha256 = request.get("script_sha256")
    if isinstance(script_sha256, str):
        digests = {
            hashlib.sha256(rendered.encode("utf-8")).hexdigest()
            for command in allowed
            for rendered in (shlex.join(command), " ".join(command))
        }
        return script_sha256 in digests
    return False


def _worker_runner(request: ParityExecutionRequest) -> ParityExecutionExport:
    _assert_route_binding(request)
    fixture = _load_fixture(request)
    server = _required_environment("CODING_PARITY_WORKER_API").rstrip("/")
    started = time.monotonic()
    deadline = started + request.budget.max_active_seconds + 900
    with httpx.Client(base_url=server, timeout=30.0, trust_env=False) as client:
        task_response = client.post(
            "/api/coding-worker/v1/tasks",
            json={
                "client_task_id": request.run_id,
                "objective": request.objective,
                "workspace_source": {
                    "kind": "builtin",
                    "source_id": request.fixture_id,
                    "revision": request.fixture_revision,
                },
                "acceptance": {
                    "contract_id": f"parity_{request.task_id}",
                    "required_checks": [
                        {
                            "check_id": check.check_id,
                            "label": check.check_id,
                            "kind": "command",
                            "required": True,
                        }
                        for check in fixture.visible_checks
                    ],
                    "required_artifacts": [],
                },
                "policy_profile": "develop",
                "model_route": request.model_route,
                "budget": {
                    "max_seconds": request.budget.max_active_seconds,
                    "max_turns": request.budget.max_turns,
                    "max_tool_calls": 2048,
                    "max_output_bytes": request.budget.max_output_bytes,
                },
                "context_refs": [],
            },
        )
        task_response.raise_for_status()
        task = task_response.json()
        task_id = str(task["task_id"])
        rejected = False
        while str(task.get("state")) not in TERMINAL_STATES:
            if time.monotonic() >= deadline:
                client.post(f"/api/coding-worker/v1/tasks/{task_id}/cancel")
                break
            if task.get("state") == "waiting_approval":
                approvals = client.get(
                    f"/api/coding-worker/v1/tasks/{task_id}/approvals"
                ).json().get("approvals", [])
                for approval in approvals:
                    if approval.get("status") != "pending":
                        continue
                    allowed = _approval_allowed(approval, fixture)
                    rejected = rejected or not allowed
                    client.post(
                        f"/api/coding-worker/v1/tasks/{task_id}/approvals",
                        json={
                            "approval_id": approval["approval_id"],
                            "decision": "approve_once" if allowed else "reject",
                            "ttl_seconds": 900,
                        },
                    ).raise_for_status()
            elif task.get("state") == "waiting_input":
                rejected = True
                client.post(f"/api/coding-worker/v1/tasks/{task_id}/cancel")
            elif task.get("state") == "interrupted":
                client.post(f"/api/coding-worker/v1/tasks/{task_id}/resume").raise_for_status()
            time.sleep(0.25)
            task_response = client.get(f"/api/coding-worker/v1/tasks/{task_id}")
            task_response.raise_for_status()
            task = task_response.json()
        export_response = client.post(
            f"/api/coding-worker/v1/tasks/{task_id}/workspace/parity-export"
        )
        export_response.raise_for_status()
        artifact_meta = export_response.json()
        content_response = client.get(
            f"/api/coding-worker/v1/tasks/{task_id}/artifacts/{artifact_meta['artifact_id']}"
        )
        content_response.raise_for_status()
        content = content_response.content
        digest = hashlib.sha256(content).hexdigest()
        if digest != artifact_meta["sha256"] or len(content) != artifact_meta["size"]:
            raise ParitySidecarError("Worker parity artifact binding is invalid")
        artifact_id = f"export_{hashlib.sha256(f'{request.run_id}:{digest}'.encode()).hexdigest()[:32]}"
        root = Path(_required_environment("CODING_PARITY_EXPORT_ROOT"))
        root.mkdir(parents=True, exist_ok=True)
        destination = _safe_child(root, artifact_id, suffix=".tar")
        temporary = _safe_child(root, f".{artifact_id}.building")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        metadata = artifact_meta.get("metadata", {})
        final_tree = str(metadata.get("workspace_tree_hash", ""))
        usage = client.get(f"/api/coding-worker/v1/tasks/{task_id}/export").json()
        input_tokens, output_tokens = _extract_usage(usage)
        raw_manifest = _canonical_sha256(
            {
                "artifact_id": artifact_id,
                "sha256": digest,
                "size": len(content),
                "tree_hash": final_tree,
                "worker_artifact_id": artifact_meta["artifact_id"],
            }
        )
        return ParityExecutionExport(
            run_id=request.run_id,
            task_id=request.task_id,
            engine=request.engine,
            attempt=request.attempt,
            engine_version=request.candidate_sha,
            model_route_receipt_sha256=request.model_route_receipt_sha256,
            fixture_bundle_sha256=request.fixture_bundle_sha256,
            runner_image_digest=request.runner_image_digest,
            candidate_sha=request.candidate_sha,
            task_manifest_sha256=request.task_manifest_sha256,
            initial_tree_hash=request.initial_tree_hash,
            final_tree_hash=final_tree,
            workspace_export=ParityArtifactReference(
                artifact_id=artifact_id, sha256=digest, size_bytes=len(content)
            ),
            raw_artifact_manifest_sha256=raw_manifest,
            policy_violations=("unapproved_command_requested",) if rejected else (),
            timeout=time.monotonic() >= deadline,
            budget_limited=task.get("state") == "budget_limited",
            stuck=task.get("state") not in {"completed", "blocked", "budget_limited"},
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool_calls=int(metadata.get("tool_calls", 0)),
            active_seconds=float(metadata.get("active_seconds", 0.0)),
        )


def _safe_extract_tar(content: bytes, destination: Path) -> None:
    total = 0
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as archive:
        members = archive.getmembers()
        if len(members) > MAX_ARCHIVE_ENTRIES:
            raise ParitySidecarError("parity archive has too many entries")
        for member in members:
            relative = PurePosixPath(member.name)
            if (
                not member.isfile()
                or relative.is_absolute()
                or any(part in {"", ".", ".."} for part in relative.parts)
                or member.size < 0
                or member.size > MAX_ARCHIVE_FILE_BYTES
            ):
                raise ParitySidecarError("parity archive entry is unsafe")
            total += member.size
            if total > MAX_BUNDLE_BYTES:
                raise ParitySidecarError("parity archive is too large")
            source = archive.extractfile(member)
            if source is None:
                raise ParitySidecarError("parity archive entry is unavailable")
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700 if member.mode & 0o111 else 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                shutil.copyfileobj(source, handle, length=1024 * 1024)


def _checker(request: ParityCheckRequest) -> ParityCheckReceipt:
    bundle_path = Path(_required_environment("CODING_PARITY_CHECKER_BUNDLE"))
    bundle_content = bundle_path.read_bytes()
    if (
        len(bundle_content) > MAX_BUNDLE_BYTES
        or hashlib.sha256(bundle_content).hexdigest()
        != request.hidden_checker_bundle_sha256
    ):
        raise ParitySidecarError("sealed checker bundle binding is invalid")
    export_root = Path(_required_environment("CODING_PARITY_EXPORT_ROOT"))
    export_path = _safe_child(
        export_root, request.workspace_export.artifact_id, suffix=".tar"
    )
    export_content = export_path.read_bytes()
    if (
        len(export_content) != request.workspace_export.size_bytes
        or hashlib.sha256(export_content).hexdigest()
        != request.workspace_export.sha256
    ):
        raise ParitySidecarError("parity workspace export binding is invalid")
    with tempfile.TemporaryDirectory(prefix="checker-") as temporary:
        root = Path(temporary)
        bundle_root = root / "bundle"
        workspace = root / "workspace"
        bundle_root.mkdir()
        workspace.mkdir()
        _safe_extract_tar(bundle_content, bundle_root)
        _safe_extract_tar(export_content, workspace)
        if _tree_hash(_workspace_files(workspace)) != request.final_tree_hash:
            raise ParitySidecarError("checker workspace tree binding is invalid")
        try:
            manifest = json.loads(
                (bundle_root / "checks.json").read_text(encoding="utf-8")
            )
            spec = manifest["checks"][request.hidden_check_bundle_id]
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise ParitySidecarError("sealed checker manifest is invalid") from exc
        if not isinstance(spec, dict) or _canonical_sha256(spec) != request.hidden_check_sha256:
            raise ParitySidecarError("hidden check binding is invalid")
        check_argv = spec.get("check_argv")
        diff_argv = spec.get("diff_argv")
        cwd = spec.get("cwd", ".")
        timeout_seconds = spec.get("timeout_seconds", 900)
        if (
            not isinstance(check_argv, list)
            or not check_argv
            or not all(isinstance(item, str) and item and "\0" not in item for item in check_argv)
            or not isinstance(diff_argv, list)
            or not diff_argv
            or not all(isinstance(item, str) and item and "\0" not in item for item in diff_argv)
            or not isinstance(cwd, str)
            or PurePosixPath(cwd).is_absolute()
            or ".." in PurePosixPath(cwd).parts
            or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= 1800
        ):
            raise ParitySidecarError("hidden check command is invalid")
        check_cwd = workspace.joinpath(*PurePosixPath(cwd).parts).resolve()
        if not check_cwd.is_relative_to(workspace.resolve()) or not check_cwd.is_dir():
            raise ParitySidecarError("hidden check cwd is invalid")
        environment = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(root / "home"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "NO_PROXY": "*",
        }
        (root / "home").mkdir(mode=0o700)
        check = subprocess.run(
            check_argv,
            cwd=check_cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
        )
        diff = subprocess.run(
            diff_argv,
            cwd=check_cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
        )
        return ParityCheckReceipt(
            run_id=request.run_id,
            task_id=request.task_id,
            attempt=request.attempt,
            hidden_check_sha256=request.hidden_check_sha256,
            hidden_checker_bundle_sha256=request.hidden_checker_bundle_sha256,
            initial_tree_hash=request.initial_tree_hash,
            final_tree_hash=request.final_tree_hash,
            workspace_export_sha256=request.workspace_export.sha256,
            hidden_checks_passed=check.returncode == 0,
            allowed_diff=diff.returncode == 0,
        )


def _read_line(connection: socket.socket) -> bytes:
    chunks = bytearray()
    while len(chunks) <= MAX_RPC_BYTES:
        block = connection.recv(min(65_536, MAX_RPC_BYTES + 1 - len(chunks)))
        if not block:
            break
        chunks.extend(block)
        if b"\n" in block:
            break
    line, separator, remainder = bytes(chunks).partition(b"\n")
    if not separator or remainder or not line or len(line) > MAX_RPC_BYTES:
        raise ParitySidecarError("parity RPC request is invalid")
    return line


def _serve(
    *,
    socket_path: Path,
    token: str,
    request_type: type[ParityExecutionRequest] | type[ParityCheckRequest],
    handler: Callable[[Any], Any],
) -> None:
    if len(token) < 32:
        raise ParitySidecarError("parity RPC token is invalid")
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    socket_path.chmod(0o600)
    server.listen(4)
    try:
        while True:
            connection, _address = server.accept()
            with connection:
                try:
                    envelope = json.loads(_read_line(connection))
                    if (
                        not isinstance(envelope, dict)
                        or not hmac.compare_digest(str(envelope.get("token", "")), token)
                    ):
                        raise ParitySidecarError("parity RPC authentication failed")
                    request = request_type.model_validate(envelope.get("payload"))
                    result = handler(request)
                    response = {"ok": True, "result": result.model_dump(mode="json")}
                except Exception as exc:
                    response = {
                        "ok": False,
                        "error": getattr(exc, "code", "parity_sidecar_failed"),
                    }
                connection.sendall(
                    json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n"
                )
    finally:
        server.close()
        socket_path.unlink(missing_ok=True)


def _rpc_client(socket_path: Path, token: str) -> None:
    payload = sys.stdin.buffer.read(MAX_RPC_BYTES + 1)
    if not payload or len(payload) > MAX_RPC_BYTES:
        raise ParitySidecarError("parity RPC payload is invalid")
    envelope = json.dumps(
        {"token": token, "payload": json.loads(payload)}, separators=(",", ":")
    ).encode("utf-8") + b"\n"
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(14_400)
    connection.connect(str(socket_path))
    connection.sendall(envelope)
    response = json.loads(_read_line(connection))
    connection.close()
    if response.get("ok") is not True:
        raise ParitySidecarError("parity sidecar request failed")
    encoded = json.dumps(response["result"], separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_RUNNER_RESPONSE_BYTES:
        raise ParitySidecarError("parity sidecar response is too large")
    sys.stdout.buffer.write(encoded)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ModelMirror parity sidecar")
    subparsers = parser.add_subparsers(dest="role", required=True)
    for role in ("native-runner", "worker-runner", "checker"):
        command = subparsers.add_parser(role)
        command.add_argument("--socket", type=Path, required=True)
        command.add_argument("--token", required=True)
    client = subparsers.add_parser("rpc-client")
    client.add_argument("--socket", type=Path, required=True)
    client.add_argument("--token", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.role == "rpc-client":
            _rpc_client(arguments.socket, arguments.token)
        elif arguments.role == "native-runner":
            _serve(
                socket_path=arguments.socket,
                token=arguments.token,
                request_type=ParityExecutionRequest,
                handler=_native_runner,
            )
        elif arguments.role == "worker-runner":
            _serve(
                socket_path=arguments.socket,
                token=arguments.token,
                request_type=ParityExecutionRequest,
                handler=_worker_runner,
            )
        else:
            _serve(
                socket_path=arguments.socket,
                token=arguments.token,
                request_type=ParityCheckRequest,
                handler=_checker,
            )
        return 0
    except (OSError, ValueError, ParitySidecarError, httpx.HTTPError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
