from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .draft_workspace import DraftLimits, DraftPolicyError, DraftWorkspace


SNAPSHOT_FINGERPRINT_PATTERN = re.compile(r"^[a-f0-9]{64}$")
SAFE_DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")
FORBIDDEN_PATCH_MARKERS = (
    "GIT binary patch",
    "Binary files ",
    "rename from ",
    "rename to ",
    "copy from ",
    "copy to ",
    "similarity index ",
    "dissimilarity index ",
)


class PatchPolicyError(RuntimeError):
    def __init__(self, message: str, *, code: str = "invalid_patch") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    entries: frozenset[tuple[str, str]]
    file_hashes: tuple[tuple[str, str], ...]
    fingerprint: str


def snapshot_fingerprint(
    root: Path,
    *,
    ignored_root_names: Iterable[str] = (),
) -> str:
    return snapshot_manifest(
        root,
        ignored_root_names=ignored_root_names,
    ).fingerprint


def snapshot_manifest(
    root: Path,
    *,
    ignored_root_names: Iterable[str] = (),
) -> SnapshotManifest:
    resolved = root.resolve()
    ignored = frozenset(ignored_root_names)
    if (
        root.is_symlink()
        or not resolved.is_dir()
        or resolved.parent == resolved
        or any("/" in name or "\\" in name or not name for name in ignored)
    ):
        raise PatchPolicyError(
            "Source snapshot is unavailable.",
            code="source_snapshot_unavailable",
        )
    digest = hashlib.sha256()
    entries: set[tuple[str, str]] = set()
    file_hashes: list[tuple[str, str]] = []
    for path in sorted(resolved.rglob("*")):
        relative_path = path.relative_to(resolved)
        if relative_path.parts and relative_path.parts[0] in ignored:
            continue
        relative = relative_path.as_posix()
        if path.is_symlink():
            raise PatchPolicyError(
                "Source snapshot contains a symlink.",
                code="source_snapshot_unsafe",
            )
        if path.is_dir():
            entries.add(("directory", relative))
            continue
        if not path.is_file():
            raise PatchPolicyError(
                "Source snapshot contains an unsupported file.",
                code="source_snapshot_unsafe",
            )
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise PatchPolicyError(
                "Source snapshot could not be read.",
                code="source_snapshot_unavailable",
            ) from exc
        content_hash = hashlib.sha256(content)
        entries.add(("file", relative))
        file_hashes.append((relative, content_hash.hexdigest()))
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(content_hash.digest())
    return SnapshotManifest(
        entries=frozenset(entries),
        file_hashes=tuple(file_hashes),
        fingerprint=digest.hexdigest(),
    )


def validate_patch(
    patch: str,
    *,
    expected_paths: Sequence[str],
    limits: DraftLimits | None = None,
) -> tuple[str, ...]:
    active_limits = limits or DraftLimits()
    try:
        encoded = patch.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise PatchPolicyError("Patch is not UTF-8.") from exc
    if (
        not patch
        or len(encoded) > active_limits.max_patch_bytes
        or "\x00" in patch
        or any(marker in patch for marker in FORBIDDEN_PATCH_MARKERS)
    ):
        raise PatchPolicyError("Patch is outside the allowed scope.")

    try:
        safe_expected = tuple(
            sorted(
                {
                    DraftWorkspace.normalize_relative_path(path)
                    for path in expected_paths
                }
            )
        )
    except DraftPolicyError as exc:
        raise PatchPolicyError("Expected Patch paths are invalid.") from exc

    headers: list[tuple[int, str]] = []
    lines = patch.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("diff --git "):
            continue
        match = SAFE_DIFF_HEADER.fullmatch(line)
        if match is None or match.group(1) != match.group(2):
            raise PatchPolicyError("Patch header is invalid.")
        try:
            path = DraftWorkspace.normalize_relative_path(match.group(1))
        except DraftPolicyError as exc:
            raise PatchPolicyError("Patch path is invalid.") from exc
        headers.append((index, path))

    paths = tuple(path for _, path in headers)
    if (
        not paths
        or len(paths) > active_limits.max_changed_files
        or len(set(paths)) != len(paths)
        or tuple(sorted(paths)) != safe_expected
    ):
        raise PatchPolicyError("Patch paths do not match the draft.")

    for position, (start, path) in enumerate(headers):
        end = headers[position + 1][0] if position + 1 < len(headers) else len(lines)
        section = lines[start:end]
        old_headers = [line for line in section if line.startswith("--- ")]
        new_headers = [line for line in section if line.startswith("+++ ")]
        if len(old_headers) != 1 or len(new_headers) != 1:
            raise PatchPolicyError("Patch file headers are incomplete.")
        old_path = old_headers[0][4:].split("\t", maxsplit=1)[0]
        new_path = new_headers[0][4:].split("\t", maxsplit=1)[0]
        new_file_markers = [
            line for line in section if line.startswith("new file mode ")
        ]
        deleted_file_markers = [
            line for line in section if line.startswith("deleted file mode ")
        ]
        is_added = new_file_markers == ["new file mode 100644"]
        is_deleted = deleted_file_markers == ["deleted file mode 100644"]
        if (
            len(new_file_markers) > 1
            or len(deleted_file_markers) > 1
            or (new_file_markers and not is_added)
            or (deleted_file_markers and not is_deleted)
            or (is_added and is_deleted)
        ):
            raise PatchPolicyError("Patch file mode is invalid.")
        if is_added:
            paths_match = old_path == "/dev/null" and new_path == f"b/{path}"
        elif is_deleted:
            paths_match = old_path == f"a/{path}" and new_path == "/dev/null"
        else:
            paths_match = old_path == f"a/{path}" and new_path == f"b/{path}"
        if not paths_match:
            raise PatchPolicyError("Patch file paths do not match.")
        has_hunk = any(line.startswith("@@ ") for line in section)
        is_empty_file_change = (is_added or is_deleted) and not has_hunk
        if not has_hunk and not is_empty_file_change:
            raise PatchPolicyError("Patch does not contain a change.")
    return tuple(sorted(paths))
