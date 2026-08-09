from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import tarfile
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .project_host import OBJECT_ID_PATTERN, PROJECT_ID_PATTERN
from .projects import (
    MAX_PROJECT_AGENTS_BYTES,
    MAX_PROJECT_NAME_CHARS,
    MAX_PROJECT_SNAPSHOT_BYTES,
    MAX_PROJECT_SNAPSHOT_FILE_BYTES,
    MAX_PROJECT_SNAPSHOT_FILES,
    build_safe_git_command,
    build_safe_git_environment,
    project_snapshot_path_is_allowed,
    validate_git_tree,
)


HOST_SNAPSHOT_VERSION = 1
MAX_HOST_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_HOST_MANIFEST_BYTES = 2 * 1024 * 1024


class HostSnapshotError(RuntimeError):
    def __init__(self, code: str, message: str = "Host project snapshot is invalid") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class HostSnapshotResult:
    project_id: str
    name: str
    branch: str
    head: str
    fingerprint: str
    file_count: int
    total_bytes: int
    hidden_files: int
    archive_identity: str | None = None


@dataclass(frozen=True, slots=True)
class _TreeEntry:
    object_id: str
    path: str


def create_host_snapshot_archive(
    project_path: Path,
    destination: Path,
    *,
    project_id: str,
    name: str,
    branch: str,
    head: str,
    identity_provider: Callable[[Path], str] | None = None,
    identity_cleanup: Callable[[Path, str], None] | None = None,
) -> HostSnapshotResult:
    if identity_cleanup is not None and identity_provider is None:
        raise ValueError("identity_cleanup requires identity_provider")
    _validate_identity(project_id, name, branch, head)
    project_path = Path(project_path).resolve(strict=True)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tree = _run_git(project_path, "ls-tree", "-r", "-z", "--full-tree", head)
    if tree.returncode != 0:
        raise HostSnapshotError("snapshot_git_failed")
    try:
        validate_git_tree(tree.stdout)
        entries = _parse_tree(tree.stdout)
    except Exception as exc:
        raise HostSnapshotError(getattr(exc, "code", "git_tree_invalid")) from exc
    if len(entries) > MAX_PROJECT_SNAPSHOT_FILES:
        raise HostSnapshotError("snapshot_file_limit_exceeded")
    visible = tuple(
        sorted(
            (item for item in entries if project_snapshot_path_is_allowed(item.path)),
            key=lambda item: item.path,
        )
    )
    hidden_files = len(entries) - len(visible)
    file_manifest: list[dict[str, Any]] = []
    fingerprint = hashlib.sha256()
    total_bytes = 0
    temporary = destination.with_name(f".{destination.name}.building")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as exc:
        raise HostSnapshotError("snapshot_archive_publish_failed") from exc
    building_identity: str | None = None
    try:
        building_identity = (
            identity_provider(temporary) if identity_provider is not None else None
        )
        process = subprocess.Popen(
            build_safe_git_command(project_path, ("cat-file", "--batch")),
            cwd=project_path,
            env=build_safe_git_environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except BaseException:
        os.close(descriptor)
        if building_identity is not None and identity_cleanup is not None:
            with contextlib.suppress(Exception):
                identity_cleanup(temporary, building_identity)
        elif identity_provider is None:
            temporary.unlink(missing_ok=True)
        raise
    timeout = threading.Timer(60.0, process.kill)
    timeout.daemon = True
    timeout.start()
    assert process.stdin is not None and process.stdout is not None
    try:
        with os.fdopen(descriptor, "wb") as output, tarfile.open(
            fileobj=output,
            mode="w:gz",
            format=tarfile.PAX_FORMAT,
        ) as archive:
            for item in visible:
                process.stdin.write(item.object_id.encode("ascii") + b"\n")
                process.stdin.flush()
                object_id, object_type, size = _parse_cat_header(process.stdout.readline())
                if object_id != item.object_id or object_type != "blob":
                    raise HostSnapshotError("snapshot_git_failed")
                if size > MAX_PROJECT_SNAPSHOT_FILE_BYTES:
                    raise HostSnapshotError("snapshot_file_too_large")
                total_bytes += size
                if total_bytes > MAX_PROJECT_SNAPSHOT_BYTES:
                    raise HostSnapshotError("snapshot_size_limit_exceeded")
                content = _read_exact(process.stdout, size)
                if process.stdout.read(1) != b"\n":
                    raise HostSnapshotError("snapshot_git_failed")
                if item.path == "AGENTS.md":
                    _validate_agents(content)
                digest = hashlib.sha256(content).hexdigest()
                file_manifest.append({"path": item.path, "size": size, "sha256": digest})
                fingerprint.update(item.path.encode("utf-8"))
                fingerprint.update(b"\0")
                fingerprint.update(str(size).encode("ascii"))
                fingerprint.update(b"\0")
                fingerprint.update(bytes.fromhex(digest))
                info = tarfile.TarInfo(f"files/{item.path}")
                info.size = size
                info.mode = 0o600
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                archive.addfile(info, io.BytesIO(content))
            manifest = {
                "version": HOST_SNAPSHOT_VERSION,
                "project_id": project_id,
                "name": name,
                "branch": branch,
                "head": head,
                "hidden_files": hidden_files,
                "files": file_manifest,
            }
            manifest_bytes = json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(manifest_bytes) > MAX_HOST_MANIFEST_BYTES:
                raise HostSnapshotError("snapshot_manifest_too_large")
            info = tarfile.TarInfo("manifest.json")
            info.size = len(manifest_bytes)
            info.mode = 0o600
            info.mtime = 0
            archive.addfile(info, io.BytesIO(manifest_bytes))
        process.stdin.close()
        if process.wait(timeout=10) != 0:
            raise HostSnapshotError("snapshot_git_failed")
        if temporary.stat().st_size > MAX_HOST_ARCHIVE_BYTES:
            raise HostSnapshotError("snapshot_archive_too_large")
        archive_identity = _publish_archive_no_replace(
            temporary,
            destination,
            identity_provider=identity_provider,
            expected_identity=building_identity,
        )
    except BaseException:
        if building_identity is not None and identity_cleanup is not None:
            with contextlib.suppress(Exception):
                identity_cleanup(temporary, building_identity)
        elif identity_provider is None:
            temporary.unlink(missing_ok=True)
        with contextlib.suppress(Exception):
            process.kill()
        with contextlib.suppress(Exception):
            process.wait(timeout=2)
        raise
    finally:
        timeout.cancel()
    return HostSnapshotResult(
        project_id=project_id,
        name=name,
        branch=branch,
        head=head,
        fingerprint=fingerprint.hexdigest(),
        file_count=len(visible),
        total_bytes=total_bytes,
        hidden_files=hidden_files,
        archive_identity=archive_identity,
    )


def _publish_archive_no_replace(
    temporary: Path,
    destination: Path,
    *,
    identity_provider: Callable[[Path], str] | None,
    expected_identity: str | None,
) -> str | None:
    """Publish one exact archive object without overwriting another name."""

    identity = expected_identity
    if identity_provider is not None and identity_provider(temporary) != identity:
        raise HostSnapshotError("snapshot_archive_publish_failed")
    try:
        # Both names are in the same private transfer directory. A hard-link
        # publication is atomic and fails if an unknown destination exists;
        # after the building name is removed the archive again has one link.
        os.link(temporary, destination, follow_symlinks=False)
    except (FileExistsError, OSError) as exc:
        raise HostSnapshotError("snapshot_archive_publish_failed") from exc
    temporary.unlink()
    if identity_provider is not None and identity_provider(destination) != identity:
        raise HostSnapshotError("snapshot_archive_publish_failed")
    return identity


def extract_host_snapshot_archive(
    archive_path: Path,
    workspace: Path,
    *,
    expected_project_id: str,
    expected_name: str,
    expected_branch: str,
    expected_head: str,
) -> HostSnapshotResult:
    _validate_identity(expected_project_id, expected_name, expected_branch, expected_head)
    archive_path = Path(archive_path)
    workspace = Path(workspace)
    if archive_path.is_symlink() or not archive_path.is_file():
        raise HostSnapshotError("snapshot_upload_missing")
    if archive_path.stat().st_size > MAX_HOST_ARCHIVE_BYTES:
        raise HostSnapshotError("snapshot_archive_too_large")
    if workspace.exists() or workspace.is_symlink():
        raise HostSnapshotError("snapshot_workspace_unsafe")
    workspace.mkdir(parents=True, mode=0o700)
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = archive.getmembers()
            if len(members) > MAX_PROJECT_SNAPSHOT_FILES + 1:
                raise HostSnapshotError("snapshot_file_limit_exceeded")
            manifest_members = [item for item in members if item.name == "manifest.json"]
            file_members = [item for item in members if item.name.startswith("files/")]
            if len(manifest_members) != 1 or len(file_members) + 1 != len(members):
                raise HostSnapshotError("snapshot_archive_invalid")
            manifest_member = manifest_members[0]
            if not manifest_member.isfile() or manifest_member.size > MAX_HOST_MANIFEST_BYTES:
                raise HostSnapshotError("snapshot_manifest_invalid")
            manifest_stream = archive.extractfile(manifest_member)
            if manifest_stream is None:
                raise HostSnapshotError("snapshot_manifest_invalid")
            try:
                manifest = json.loads(manifest_stream.read().decode("utf-8", errors="strict"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise HostSnapshotError("snapshot_manifest_invalid") from exc
            files, hidden_files = _validate_manifest(
                manifest,
                expected_project_id=expected_project_id,
                expected_name=expected_name,
                expected_branch=expected_branch,
                expected_head=expected_head,
            )
            member_map: dict[str, tarfile.TarInfo] = {}
            for member in file_members:
                if not member.isfile() or member.issym() or member.islnk():
                    raise HostSnapshotError("snapshot_archive_invalid")
                path = member.name.removeprefix("files/")
                _validate_snapshot_path(path)
                if path in member_map or member.size != files.get(path, {}).get("size"):
                    raise HostSnapshotError("snapshot_manifest_mismatch")
                member_map[path] = member
            if set(member_map) != set(files):
                raise HostSnapshotError("snapshot_manifest_mismatch")
            total_bytes = sum(int(value["size"]) for value in files.values())
            if total_bytes > MAX_PROJECT_SNAPSHOT_BYTES:
                raise HostSnapshotError("snapshot_size_limit_exceeded")
            fingerprint = hashlib.sha256()
            for path in sorted(files):
                metadata = files[path]
                member = member_map[path]
                source = archive.extractfile(member)
                if source is None:
                    raise HostSnapshotError("snapshot_archive_invalid")
                content = source.read(MAX_PROJECT_SNAPSHOT_FILE_BYTES + 1)
                if len(content) != metadata["size"]:
                    raise HostSnapshotError("snapshot_archive_truncated")
                digest = hashlib.sha256(content).hexdigest()
                if not hmac_compare(digest, metadata["sha256"]):
                    raise HostSnapshotError("snapshot_digest_mismatch")
                if path == "AGENTS.md":
                    _validate_agents(content)
                target = workspace.joinpath(*PurePosixPath(path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                fingerprint.update(path.encode("utf-8"))
                fingerprint.update(b"\0")
                fingerprint.update(str(len(content)).encode("ascii"))
                fingerprint.update(b"\0")
                fingerprint.update(bytes.fromhex(digest))
    except HostSnapshotError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise HostSnapshotError("snapshot_archive_invalid") from exc
    return HostSnapshotResult(
        project_id=expected_project_id,
        name=expected_name,
        branch=expected_branch,
        head=expected_head,
        fingerprint=fingerprint.hexdigest(),
        file_count=len(files),
        total_bytes=total_bytes,
        hidden_files=hidden_files,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_manifest(
    value: Any,
    *,
    expected_project_id: str,
    expected_name: str,
    expected_branch: str,
    expected_head: str,
) -> tuple[dict[str, dict[str, Any]], int]:
    expected_keys = {"version", "project_id", "name", "branch", "head", "hidden_files", "files"}
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise HostSnapshotError("snapshot_manifest_invalid")
    if (
        value["version"] != HOST_SNAPSHOT_VERSION
        or value["project_id"] != expected_project_id
        or value["name"] != expected_name
        or value["branch"] != expected_branch
        or value["head"] != expected_head
        or not isinstance(value["hidden_files"], int)
        or isinstance(value["hidden_files"], bool)
        or not 0 <= value["hidden_files"] <= MAX_PROJECT_SNAPSHOT_FILES
        or not isinstance(value["files"], list)
        or len(value["files"]) > MAX_PROJECT_SNAPSHOT_FILES
    ):
        raise HostSnapshotError("snapshot_manifest_mismatch")
    files: dict[str, dict[str, Any]] = {}
    folded: set[str] = set()
    for item in value["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise HostSnapshotError("snapshot_manifest_invalid")
        path = item.get("path")
        size = item.get("size")
        digest = item.get("sha256")
        _validate_snapshot_path(path)
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or not 0 <= size <= MAX_PROJECT_SNAPSHOT_FILE_BYTES
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or path in files
            or path.casefold() in folded
        ):
            raise HostSnapshotError("snapshot_manifest_invalid")
        folded.add(path.casefold())
        files[path] = dict(item)
    return files, value["hidden_files"]


def _validate_identity(project_id: str, name: str, branch: str, head: str) -> None:
    if PROJECT_ID_PATTERN.fullmatch(project_id) is None:
        raise HostSnapshotError("project_id_invalid")
    if (
        not isinstance(name, str)
        or not name
        or name != name.strip()
        or len(name) > MAX_PROJECT_NAME_CHARS
        or name != unicodedata.normalize("NFC", name)
        or any(unicodedata.category(character).startswith("C") for character in name)
    ):
        raise HostSnapshotError("project_name_invalid")
    if (
        not isinstance(branch, str)
        or not branch
        or branch != branch.strip()
        or len(branch) > 200
        or any(unicodedata.category(character).startswith("C") for character in branch)
        or OBJECT_ID_PATTERN.fullmatch(head) is None
    ):
        raise HostSnapshotError("project_identity_invalid")


def _validate_snapshot_path(path: Any) -> None:
    if not isinstance(path, str) or not project_snapshot_path_is_allowed(path):
        raise HostSnapshotError("snapshot_path_not_allowed")
    pure = PurePosixPath(path)
    if pure.is_absolute() or pure.as_posix() != path or any(part in {"", ".", ".."} for part in pure.parts):
        raise HostSnapshotError("snapshot_path_not_allowed")


def _validate_agents(content: bytes) -> None:
    if len(content) > MAX_PROJECT_AGENTS_BYTES:
        raise HostSnapshotError("agents_instructions_too_large")
    try:
        content.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise HostSnapshotError("agents_instructions_invalid") from exc


def _run_git(path: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            build_safe_git_command(path, arguments),
            cwd=path,
            env=build_safe_git_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HostSnapshotError("snapshot_git_failed") from exc


def _parse_tree(payload: bytes) -> tuple[_TreeEntry, ...]:
    result: list[_TreeEntry] = []
    for record in payload.split(b"\0"):
        if not record:
            continue
        header, raw_path = record.split(b"\t", maxsplit=1)
        _mode, object_type, object_id = header.split(b" ", maxsplit=2)
        if object_type != b"blob":
            raise HostSnapshotError("git_tree_invalid")
        result.append(
            _TreeEntry(
                object_id=object_id.decode("ascii").lower(),
                path=raw_path.decode("utf-8", errors="strict"),
            )
        )
    return tuple(result)


def _parse_cat_header(value: bytes) -> tuple[str, str, int]:
    try:
        raw_object_id, raw_type, raw_size = value.rstrip(b"\n").split(b" ", maxsplit=2)
        return raw_object_id.decode("ascii").lower(), raw_type.decode("ascii"), int(raw_size)
    except (UnicodeError, ValueError) as exc:
        raise HostSnapshotError("snapshot_git_failed") from exc


def _read_exact(stream: Any, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(min(remaining, 1024 * 1024))
        if not chunk:
            raise HostSnapshotError("snapshot_git_failed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def hmac_compare(first: str, second: str) -> bool:
    import hmac

    return hmac.compare_digest(first, second)
