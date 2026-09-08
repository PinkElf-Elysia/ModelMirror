from __future__ import annotations

import sys

if __name__ == "__main__" and (not sys.flags.isolated or not sys.dont_write_bytecode):
    print(
        '{"errorType":"IsolationRequired","status":"failed"}',
        flush=True,
    )
    raise SystemExit(2)

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from typing import Any, Sequence
import xml.etree.ElementTree as ET


MODULE_PREFIX = "extensions/ai-research/"
SOURCE_LOCK_PATH = MODULE_PREFIX + "source-lock.json"
BOUNDARY_PATH = MODULE_PREFIX + "module-boundary.json"
REVIEWER_PATH = MODULE_PREFIX + "scripts/trust_review.py"
REVIEWER_LOCK_NAME = "scripts/trust_review.py"
CI_PATH = ".github/workflows/ai-research.yml"
REVIEWER_TEST_PATH = MODULE_PREFIX + "tests/control/test_trust_review.py"
PROMOTION_DOC_PATH = MODULE_PREFIX + "TRUST_PROMOTION.md"
CLIENT_PROOF_DOCKERFILE = MODULE_PREFIX + "scripts/client-proof.Dockerfile"
CLIENT_PROOF_DOCKERFILE_LOCK_NAME = "scripts/client-proof.Dockerfile"

COMMIT_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
INTEGER_RE = re.compile(r"0|[1-9][0-9]*")
MAX_GIT_BLOB = 2 * 1024 * 1024
MAX_GIT_OUTPUT = 8 * 1024 * 1024
MAX_EVIDENCE_FILE = 4 * 1024 * 1024
MAX_PROOF_FILES = 10_000
MAX_PROOF_FILE = 64 * 1024 * 1024
MAX_PROOF_TOTAL = 512 * 1024 * 1024

PATH_ORDER_PATHS = frozenset(
    {
        SOURCE_LOCK_PATH,
        MODULE_PREFIX + "scripts/zero_footprint.py",
        MODULE_PREFIX + "tests/control/test_zero_footprint_base.py",
    }
)
DIAGNOSTICS_PATHS = frozenset(
    {
        *PATH_ORDER_PATHS,
        MODULE_PREFIX + "scripts/verify.ps1",
        MODULE_PREFIX + "scripts/verify.sh",
    }
)
IMMUTABLE_REVIEW_PATHS = (
    BOUNDARY_PATH,
    CI_PATH,
    REVIEWER_PATH,
    REVIEWER_TEST_PATH,
    PROMOTION_DOC_PATH,
)

PATH_ORDER_POINTERS = frozenset(
    {
        "/coreBaseline/clientDistReference/aggregateSha256",
        "/lockedFiles/scripts~1zero_footprint.py/sizeBytes",
        "/lockedFiles/scripts~1zero_footprint.py/sha256",
        "/lockedFiles/tests~1control~1test_zero_footprint_base.py/sizeBytes",
        "/lockedFiles/tests~1control~1test_zero_footprint_base.py/sha256",
    }
)
DIAGNOSTICS_POINTERS = frozenset(
    {
        "/lockedFiles/scripts~1zero_footprint.py/sizeBytes",
        "/lockedFiles/scripts~1zero_footprint.py/sha256",
        "/lockedFiles/scripts~1verify.ps1/sizeBytes",
        "/lockedFiles/scripts~1verify.ps1/sha256",
        "/lockedFiles/scripts~1verify.sh/sizeBytes",
        "/lockedFiles/scripts~1verify.sh/sha256",
        "/lockedFiles/tests~1control~1test_zero_footprint_base.py/sizeBytes",
        "/lockedFiles/tests~1control~1test_zero_footprint_base.py/sha256",
    }
)

PATH_ORDER_REQUIRED_TESTS = frozenset(
    {
        "test_client_dist_hashes_posix_paths_in_case_sensitive_order",
        "test_client_dist_rejects_duplicate_canonical_paths",
    }
)
DIAGNOSTICS_REQUIRED_TESTS = PATH_ORDER_REQUIRED_TESTS | frozenset(
    {
        "test_main_receipt_records_locked_base_head_and_three_proofs",
        "test_early_failure_writes_nonempty_sanitized_diagnostic",
        "test_diagnostic_is_published_after_flush_and_cannot_replace_old_evidence",
        "test_failed_atomic_publication_leaves_no_partial_diagnostic",
    }
)
WINDOWS_ALLOWED_SKIPS = frozenset(
    {
        "test_bash_verifier_keeps_committed_trust_violation_when_workspace_restores_base",
        "test_verifier_keeps_staged_trust_violation_when_worktree_restores_head[bash]",
    }
)
LINUX_ALLOWED_SKIPS = frozenset(
    {
        "test_powershell_verifier_keeps_committed_trust_violation_when_workspace_restores_base",
        "test_verifier_keeps_staged_trust_violation_when_worktree_restores_head[powershell]",
    }
)


class ReviewFailure(RuntimeError):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise ReviewFailure("invalid command line")


def _is_int(value: object) -> bool:
    return type(value) is int


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _load_json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_object_no_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReviewFailure(f"invalid {label} JSON") from exc
    if not isinstance(value, dict):
        raise ReviewFailure(f"invalid {label} JSON object")
    return value


def _safe_relative(value: object, *, module_relative: bool = False) -> str:
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        raise ReviewFailure("unsafe repository path")
    if value.startswith("/") or re.match(r"[A-Za-z]:/", value):
        raise ReviewFailure("unsafe repository path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ReviewFailure("unsafe repository path")
    if module_relative and value.startswith(MODULE_PREFIX):
        raise ReviewFailure("unsafe locked file path")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ReviewFailure("repository path is not valid UTF-8") from exc
    return value


def _git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    return environment


def _git_bytes(repo: Path, *args: str, allow_failure: bool = False) -> tuple[int, bytes]:
    try:
        completed = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(repo), *args],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_git_environment(),
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewFailure("Git object inspection timed out") from exc
    except OSError as exc:
        raise ReviewFailure("Git is unavailable") from exc
    if len(completed.stdout) > MAX_GIT_OUTPUT:
        raise ReviewFailure("Git output exceeds review limit")
    if completed.returncode != 0 and not allow_failure:
        raise ReviewFailure("Git object inspection failed")
    return completed.returncode, completed.stdout


def _resolve_commit(repo: Path, value: str, label: str) -> str:
    if not isinstance(value, str) or not COMMIT_RE.fullmatch(value):
        raise ReviewFailure(f"{label} must be an exact lowercase commit")
    code, raw = _git_bytes(repo, "rev-parse", "--verify", f"{value}^{{commit}}", allow_failure=True)
    try:
        resolved = raw.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise ReviewFailure(f"{label} commit is invalid") from exc
    if code != 0 or resolved != value:
        raise ReviewFailure(f"{label} commit does not exist")
    return resolved


def _resolve_tree(repo: Path, commit: str) -> str:
    _code, raw = _git_bytes(repo, "rev-parse", "--verify", f"{commit}^{{tree}}")
    try:
        value = raw.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise ReviewFailure("invalid Git tree") from exc
    if not COMMIT_RE.fullmatch(value):
        raise ReviewFailure("invalid Git tree")
    return value


def _require_ancestor(repo: Path, ancestor: str, descendant: str, message: str) -> None:
    code, _raw = _git_bytes(
        repo,
        "merge-base",
        "--is-ancestor",
        ancestor,
        descendant,
        allow_failure=True,
    )
    if code != 0:
        raise ReviewFailure(message)


def _changed_paths(repo: Path, base: str, candidate: str) -> list[str]:
    _code, raw = _git_bytes(
        repo,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--name-only",
        "-z",
        "--no-renames",
        "--diff-filter=ACDMRTUXB",
        base,
        candidate,
        "--",
    )
    if not raw:
        return []
    if not raw.endswith(b"\0"):
        raise ReviewFailure("invalid NUL-delimited Git path output")
    pieces = raw.split(b"\0")
    if pieces[-1] != b"" or any(piece == b"" for piece in pieces[:-1]):
        raise ReviewFailure("invalid NUL-delimited Git path output")
    paths: list[str] = []
    for piece in pieces[:-1]:
        try:
            value = piece.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ReviewFailure("Git path is not valid UTF-8") from exc
        paths.append(_safe_relative(value))
    if len(paths) != len(set(paths)):
        raise ReviewFailure("duplicate changed Git path")
    return sorted(paths, key=lambda item: item.encode("utf-8"))


def _historical_client_tree(repo: Path, commit: str) -> dict[str, Any]:
    _code, raw = _git_bytes(
        repo,
        "ls-tree",
        "-r",
        "-l",
        "-z",
        commit,
        "--",
        "client",
    )
    if not raw or not raw.endswith(b"\0"):
        raise ReviewFailure("historical client tree is missing or invalid")
    entries = raw.split(b"\0")
    if entries[-1] != b"" or any(entry == b"" for entry in entries[:-1]):
        raise ReviewFailure("historical client tree output is invalid")
    files: list[dict[str, object]] = []
    seen: set[str] = set()
    total = 0
    for entry in entries[:-1]:
        if b"\t" not in entry:
            raise ReviewFailure("historical client tree entry is invalid")
        header, raw_name = entry.split(b"\t", 1)
        try:
            name = raw_name.decode("utf-8", errors="strict")
            fields = header.decode("ascii", errors="strict").split()
        except UnicodeDecodeError as exc:
            raise ReviewFailure("historical client tree path is not valid UTF-8") from exc
        if len(fields) != 4:
            raise ReviewFailure("historical client tree entry is invalid")
        mode, object_type, object_id, size_text = fields
        name = _safe_relative(name)
        if not name.startswith("client/") or name == "client/":
            raise ReviewFailure("historical client tree path is outside client")
        relative = _safe_relative(name[len("client/") :])
        if relative in seen:
            raise ReviewFailure("historical client tree contains a duplicate path")
        if mode not in {"100644", "100755"} or object_type != "blob":
            raise ReviewFailure("historical client tree contains a link or special mode")
        if not COMMIT_RE.fullmatch(object_id) or not INTEGER_RE.fullmatch(size_text):
            raise ReviewFailure("historical client tree object is invalid")
        size = int(size_text)
        if size < 0:
            raise ReviewFailure("historical client tree size is invalid")
        total += size
        if total > 2 * 1024 * 1024 * 1024:
            raise ReviewFailure("historical client tree exceeds review limit")
        seen.add(relative)
        files.append(
            {
                "path": relative,
                "mode": mode,
                "sizeBytes": size,
                "gitObject": object_id,
            }
        )
    files.sort(key=lambda item: str(item["path"]).encode("utf-8"))
    if not files:
        raise ReviewFailure("historical client tree is empty")
    return {
        "fileCount": len(files),
        "totalBytes": total,
        "aggregateSha256": _sha256(_canonical(files)),
        "files": files,
    }


def _blob_info(repo: Path, commit: str, relative: str) -> tuple[str, str, int]:
    relative = _safe_relative(relative)
    code, raw = _git_bytes(
        repo,
        "ls-tree",
        "-z",
        commit,
        "--",
        relative,
        allow_failure=True,
    )
    if code != 0 or not raw:
        raise ReviewFailure("required Git blob is missing")
    entries = [entry for entry in raw.split(b"\0") if entry]
    if len(entries) != 1 or b"\t" not in entries[0]:
        raise ReviewFailure("ambiguous Git blob path")
    header, raw_name = entries[0].split(b"\t", 1)
    try:
        name = raw_name.decode("utf-8", errors="strict")
        mode, object_type, object_id = header.decode("ascii", errors="strict").split(" ")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ReviewFailure("invalid Git tree entry") from exc
    if _safe_relative(name) != relative or object_type != "blob":
        raise ReviewFailure("invalid Git blob entry")
    if mode not in {"100644", "100755"} or not COMMIT_RE.fullmatch(object_id):
        raise ReviewFailure("unsupported Git blob mode")
    _code, size_raw = _git_bytes(repo, "cat-file", "-s", object_id)
    try:
        size_text = size_raw.decode("ascii", errors="strict").strip()
        size = int(size_text)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ReviewFailure("invalid Git blob size") from exc
    if size < 0 or size > MAX_GIT_BLOB:
        raise ReviewFailure("Git blob exceeds review limit")
    return mode, object_id, size


def _blob_at(repo: Path, commit: str, relative: str) -> tuple[bytes, dict[str, object]]:
    mode, object_id, size = _blob_info(repo, commit, relative)
    _code, raw = _git_bytes(repo, "cat-file", "blob", object_id)
    if len(raw) != size:
        raise ReviewFailure("Git blob changed during inspection")
    return raw, {
        "mode": mode,
        "gitObject": object_id,
        "sizeBytes": size,
        "sha256": _sha256(raw),
    }


def _optional_blob_at(repo: Path, commit: str, relative: str) -> tuple[bytes, dict[str, object]] | None:
    try:
        return _blob_at(repo, commit, relative)
    except ReviewFailure as exc:
        if str(exc) == "required Git blob is missing":
            return None
        raise


def _is_reparse(metadata: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(flag and attributes & flag)


def _read_local_regular(path: Path, *, limit: int, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReviewFailure(f"{label} is unavailable") from exc
    if path.is_symlink() or _is_reparse(metadata) or not stat.S_ISREG(metadata.st_mode):
        raise ReviewFailure(f"{label} must be a regular file")
    if metadata.st_size < 0 or metadata.st_size > limit:
        raise ReviewFailure(f"{label} exceeds review limit")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size != metadata.st_size:
                raise ReviewFailure(f"{label} changed during inspection")
            chunks: list[bytes] = []
            remaining = metadata.st_size
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise ReviewFailure(f"{label} changed during inspection")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise ReviewFailure(f"{label} changed during inspection")
            after = os.fstat(descriptor)
            if after.st_size != opened.st_size or after.st_mtime_ns != opened.st_mtime_ns:
                raise ReviewFailure(f"{label} changed during inspection")
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ReviewFailure(f"{label} cannot be read safely") from exc


def _strict_descriptor(value: object, label: str) -> tuple[int, str]:
    if not isinstance(value, dict) or set(value) != {"sizeBytes", "sha256"}:
        raise ReviewFailure(f"invalid locked descriptor: {label}")
    size = value["sizeBytes"]
    digest = value["sha256"]
    if not _is_int(size) or size < 0 or size > MAX_GIT_BLOB:
        raise ReviewFailure(f"invalid locked size: {label}")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise ReviewFailure(f"invalid locked hash: {label}")
    return size, digest


def _validate_lock_shape(source_lock: dict[str, Any], boundary: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    locked_source = source_lock.get("modelMirrorBaseCommit")
    if not isinstance(locked_source, str) or not COMMIT_RE.fullmatch(locked_source):
        raise ReviewFailure("invalid locked source commit")
    if boundary.get("baseCommit") != locked_source:
        raise ReviewFailure("source lock and boundary disagree")
    gate = source_lock.get("coreBaseline")
    if not isinstance(gate, dict):
        raise ReviewFailure("invalid core baseline")
    client_gate = gate.get("clientDistGate")
    if not isinstance(client_gate, dict) or client_gate.get("baseCommit") != locked_source:
        raise ReviewFailure("invalid locked client proof provenance")
    reference = gate.get("clientDistReference")
    if not isinstance(reference, dict) or set(reference) != {
        "fileCount",
        "totalBytes",
        "aggregateSha256",
    }:
        raise ReviewFailure("invalid client proof reference")
    if (
        not _is_int(reference["fileCount"])
        or reference["fileCount"] <= 0
        or reference["fileCount"] > MAX_PROOF_FILES
        or not _is_int(reference["totalBytes"])
        or reference["totalBytes"] < 0
        or reference["totalBytes"] > MAX_PROOF_TOTAL
        or not isinstance(reference["aggregateSha256"], str)
        or not SHA256_RE.fullmatch(reference["aggregateSha256"])
    ):
        raise ReviewFailure("invalid client proof reference")
    tracked = gate.get("trackedFiles")
    if not isinstance(tracked, dict) or not tracked:
        raise ReviewFailure("invalid historical tracked files")
    for raw_name, digest in tracked.items():
        _safe_relative(raw_name)
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ReviewFailure("invalid historical tracked file hash")
    locked = source_lock.get("lockedFiles")
    if not isinstance(locked, dict) or not locked:
        raise ReviewFailure("source lock has no locked files")
    for raw_name, descriptor in locked.items():
        name = _safe_relative(raw_name, module_relative=True)
        _strict_descriptor(descriptor, name)
    if CLIENT_PROOF_DOCKERFILE_LOCK_NAME not in locked:
        raise ReviewFailure("client proof Dockerfile is not locked")
    allowed = boundary.get("allowedParentFiles")
    if not isinstance(allowed, list):
        raise ReviewFailure("invalid allowed parent files")
    normalized = [_safe_relative(item) for item in allowed]
    if len(normalized) != len(set(normalized)):
        raise ReviewFailure("duplicate allowed parent file")
    image = source_lock.get("clientProofImage")
    if (
        not isinstance(image, dict)
        or set(image) != {"reference", "digest"}
        or not isinstance(image.get("reference"), str)
        or not image["reference"]
        or not isinstance(image.get("digest"), str)
        or not DIGEST_RE.fullmatch(image["digest"])
    ):
        raise ReviewFailure("invalid client proof image")
    return locked_source, locked


def _validate_locked_files(
    repo: Path,
    commit: str,
    locked: dict[str, Any],
) -> dict[str, dict[str, object]]:
    facts: dict[str, dict[str, object]] = {}
    for raw_name in sorted(locked, key=lambda item: item.encode("utf-8")):
        name = _safe_relative(raw_name, module_relative=True)
        expected_size, expected_hash = _strict_descriptor(locked[raw_name], name)
        _raw, fact = _blob_at(repo, commit, MODULE_PREFIX + name)
        if fact["sizeBytes"] != expected_size or fact["sha256"] != expected_hash:
            raise ReviewFailure(f"locked file drifted: {name}")
        facts[name] = fact
    return facts


def _validate_historical_tracked_files(
    repo: Path,
    locked_source: str,
    source_lock: dict[str, Any],
) -> None:
    core = source_lock["coreBaseline"]
    for relative, expected in core["trackedFiles"].items():
        raw, _fact = _blob_at(repo, locked_source, _safe_relative(relative))
        if _sha256(raw) != expected:
            raise ReviewFailure("historical tracked file drifted")


def _json_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_json_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    return bool(left == right)


def _pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _json_changes(left: Any, right: Any, pointer: str = "") -> set[str]:
    if type(left) is not type(right):
        return {pointer or "/"}
    if isinstance(left, dict):
        changes: set[str] = set()
        for key in left.keys() | right.keys():
            child = f"{pointer}/{_pointer_escape(key)}"
            if key not in left or key not in right:
                changes.add(child)
            else:
                changes.update(_json_changes(left[key], right[key], child))
        return changes
    if isinstance(left, list):
        if len(left) != len(right):
            return {pointer or "/"}
        changes: set[str] = set()
        for index, (a, b) in enumerate(zip(left, right)):
            changes.update(_json_changes(a, b, f"{pointer}/{index}"))
        return changes
    return set() if _json_equal(left, right) else {pointer or "/"}


def _require_immutable_blobs(repo: Path, base: str, candidate: str) -> None:
    for relative in IMMUTABLE_REVIEW_PATHS:
        base_blob = _optional_blob_at(repo, base, relative)
        candidate_blob = _optional_blob_at(repo, candidate, relative)
        if base_blob is None or candidate_blob is None or base_blob[0] != candidate_blob[0]:
            raise ReviewFailure("base-owned review asset changed")
        if base_blob[1]["mode"] != candidate_blob[1]["mode"]:
            raise ReviewFailure("base-owned review asset mode changed")


def _validate_reviewer(
    repo: Path,
    base: str,
    source_lock: dict[str, Any],
    reviewer_file: Path | None,
) -> tuple[dict[str, object], bytes]:
    base_blob = _optional_blob_at(repo, base, REVIEWER_PATH)
    locked = source_lock.get("lockedFiles")
    if base_blob is None or not isinstance(locked, dict) or REVIEWER_LOCK_NAME not in locked:
        raise ReviewFailure("bootstrap required: base reviewer is not locked")
    expected_size, expected_hash = _strict_descriptor(locked[REVIEWER_LOCK_NAME], REVIEWER_LOCK_NAME)
    base_raw, base_fact = base_blob
    if base_fact["sizeBytes"] != expected_size or base_fact["sha256"] != expected_hash:
        raise ReviewFailure("bootstrap required: base reviewer lock is invalid")
    local_path = Path(__file__) if reviewer_file is None else reviewer_file
    local_raw = _read_local_regular(local_path, limit=MAX_GIT_BLOB, label="reviewer")
    if local_raw != base_raw:
        raise ReviewFailure("reviewer bytes do not match base")
    return base_fact, base_raw


def _changed_lock_mode(
    repo: Path,
    base: str,
    candidate: str,
    changed_paths: set[str],
) -> None:
    for relative in changed_paths:
        _base_raw, base_fact = _blob_at(repo, base, relative)
        _candidate_raw, candidate_fact = _blob_at(repo, candidate, relative)
        if base_fact["mode"] != candidate_fact["mode"]:
            raise ReviewFailure("trust candidate changed a file mode")


def _validate_trust_diff(
    kind: str,
    base_lock: dict[str, Any],
    candidate_lock: dict[str, Any],
) -> list[str]:
    base_locked = base_lock.get("lockedFiles")
    candidate_locked = candidate_lock.get("lockedFiles")
    if not isinstance(base_locked, dict) or not isinstance(candidate_locked, dict):
        raise ReviewFailure("invalid locked file set")
    if set(base_locked) != set(candidate_locked):
        raise ReviewFailure("locked file key set changed")
    changes = _json_changes(base_lock, candidate_lock)
    expected = PATH_ORDER_POINTERS if kind == "path_order" else DIAGNOSTICS_POINTERS
    required = {
        pointer
        for pointer in expected
        if pointer.endswith("/sha256")
        or pointer == "/coreBaseline/clientDistReference/aggregateSha256"
    }
    if not required <= changes or not changes <= expected:
        raise ReviewFailure("source-lock semantic change is outside the exact trust repair")
    if kind == "path_order":
        before = base_lock["coreBaseline"]["clientDistReference"]["aggregateSha256"]
        after = candidate_lock["coreBaseline"]["clientDistReference"]["aggregateSha256"]
        if not isinstance(after, str) or not SHA256_RE.fullmatch(after) or after == before:
            raise ReviewFailure("path-order aggregate was not replaced exactly once")
    return sorted(changes, key=lambda item: item.encode("utf-8"))


def audit(
    repo: Path,
    base: str,
    candidate: str,
    *,
    reviewer_file: Path | None = None,
) -> dict[str, Any]:
    repo = repo.resolve()
    base_commit = _resolve_commit(repo, base, "base")
    candidate_commit = _resolve_commit(repo, candidate, "candidate")
    _require_ancestor(repo, base_commit, candidate_commit, "base is not an ancestor of candidate")
    base_tree = _resolve_tree(repo, base_commit)
    candidate_tree = _resolve_tree(repo, candidate_commit)

    base_lock_raw, base_lock_fact = _blob_at(repo, base_commit, SOURCE_LOCK_PATH)
    base_boundary_raw, _base_boundary_fact = _blob_at(repo, base_commit, BOUNDARY_PATH)
    base_lock = _load_json(base_lock_raw, "base source-lock")
    base_boundary = _load_json(base_boundary_raw, "base module-boundary")
    locked_source, base_locked = _validate_lock_shape(base_lock, base_boundary)
    reviewer_fact, _reviewer_raw = _validate_reviewer(
        repo,
        base_commit,
        base_lock,
        reviewer_file,
    )
    _require_ancestor(repo, locked_source, base_commit, "locked source is not an ancestor of base")
    _validate_historical_tracked_files(repo, locked_source, base_lock)
    _validate_locked_files(repo, base_commit, base_locked)
    _require_immutable_blobs(repo, base_commit, candidate_commit)
    _base_workflow_raw, base_workflow = _blob_at(repo, base_commit, CI_PATH)
    _base_dockerfile_raw, base_dockerfile = _blob_at(
        repo,
        base_commit,
        CLIENT_PROOF_DOCKERFILE,
    )
    historical_client_tree = _historical_client_tree(repo, locked_source)

    candidate_lock_raw, _candidate_lock_fact = _blob_at(repo, candidate_commit, SOURCE_LOCK_PATH)
    candidate_boundary_raw, _candidate_boundary_fact = _blob_at(repo, candidate_commit, BOUNDARY_PATH)
    if candidate_boundary_raw != base_boundary_raw:
        raise ReviewFailure("module boundary changed")
    candidate_lock = _load_json(candidate_lock_raw, "candidate source-lock")
    candidate_boundary = _load_json(candidate_boundary_raw, "candidate module-boundary")
    candidate_locked_source, candidate_locked = _validate_lock_shape(candidate_lock, candidate_boundary)
    if candidate_locked_source != locked_source:
        raise ReviewFailure("locked source commit changed")
    changed = _changed_paths(repo, base_commit, candidate_commit)
    changed_set = set(changed)

    if changed_set == PATH_ORDER_PATHS:
        kind = "path_order"
        changed_pointers = _validate_trust_diff(kind, base_lock, candidate_lock)
        _changed_lock_mode(repo, base_commit, candidate_commit, changed_set)
    elif changed_set == DIAGNOSTICS_PATHS:
        kind = "diagnostics"
        changed_pointers = _validate_trust_diff(kind, base_lock, candidate_lock)
        _changed_lock_mode(repo, base_commit, candidate_commit, changed_set)
    elif candidate_lock_raw == base_lock_raw:
        kind = "functional"
        changed_pointers = []
        allowed_parent = set(base_boundary["allowedParentFiles"])
        outside = {
            path
            for path in changed_set
            if not path.startswith(MODULE_PREFIX) and path not in allowed_parent
        }
        protected = set(base_lock["coreBaseline"]["trackedFiles"])
        protected.update(MODULE_PREFIX + name for name in base_locked)
        if outside or changed_set & protected:
            raise ReviewFailure("functional candidate changed a protected path")
    else:
        raise ReviewFailure("unknown or mixed trust candidate scope")

    candidate_facts = _validate_locked_files(repo, candidate_commit, candidate_locked)
    image = base_lock["clientProofImage"]
    return {
        "schemaVersion": 1,
        "status": "scope_validated",
        "kind": kind,
        "qualification": "not_run",
        "promotion": "manual_required",
        "full": (
            "required_for_functional"
            if kind == "functional"
            else "not_applicable_trust_only"
        ),
        "baseCommit": base_commit,
        "candidateCommit": candidate_commit,
        "baseTree": base_tree,
        "candidateTree": candidate_tree,
        "reviewerSha256": reviewer_fact["sha256"],
        "baseReviewer": reviewer_fact,
        "baseSourceLock": base_lock_fact,
        "sourceLockSha256": _sha256(candidate_lock_raw),
        "boundarySha256": _sha256(base_boundary_raw),
        "lockedSourceCommit": locked_source,
        "clientProofImage": {
            "reference": image["reference"],
            "digest": image["digest"],
        },
        "baseWorkflow": base_workflow,
        "baseClientProofDockerfile": base_dockerfile,
        "historicalClientTree": historical_client_tree,
        "changedPaths": changed,
        "changedLockPointers": changed_pointers,
        "lockedFiles": candidate_facts,
    }


def _directory_metadata(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReviewFailure(f"{label} is unavailable") from exc
    if path.is_symlink() or _is_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
        raise ReviewFailure(f"{label} must be a regular directory")
    return metadata


def _scandir(path: Path, label: str) -> list[tuple[str, Path, os.stat_result]]:
    _directory_metadata(path, label)
    try:
        with os.scandir(path) as iterator:
            entries = []
            for entry in iterator:
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise ReviewFailure(f"{label} entry is unavailable") from exc
                name = entry.name
                try:
                    name.encode("utf-8", errors="strict")
                except UnicodeEncodeError as exc:
                    raise ReviewFailure(f"{label} path is not valid UTF-8") from exc
                if entry.is_symlink() or _is_reparse(metadata):
                    raise ReviewFailure(f"{label} contains a link or reparse point")
                entries.append((name, Path(entry.path), metadata))
            return entries
    except OSError as exc:
        raise ReviewFailure(f"{label} cannot be enumerated") from exc


def _hash_proof_file(path: Path, expected: os.stat_result) -> str:
    if expected.st_size > MAX_PROOF_FILE:
        raise ReviewFailure("client proof file exceeds limit")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size != expected.st_size:
                raise ReviewFailure("client proof file changed during hashing")
            digest = hashlib.sha256()
            remaining = opened.st_size
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise ReviewFailure("client proof file changed during hashing")
                digest.update(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise ReviewFailure("client proof file changed during hashing")
            after = os.fstat(descriptor)
            if after.st_size != opened.st_size or after.st_mtime_ns != opened.st_mtime_ns:
                raise ReviewFailure("client proof file changed during hashing")
            return digest.hexdigest()
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ReviewFailure("client proof file cannot be read safely") from exc


def client_proof(root: Path) -> dict[str, Any]:
    root = root.absolute()
    entries = _scandir(root, "client proof root")
    if len(entries) == 1 and entries[0][0] == "dist" and stat.S_ISDIR(entries[0][2].st_mode):
        root = entries[0][1]
    stack: list[tuple[Path, str]] = [(root, "")]
    files: dict[str, tuple[Path, os.stat_result]] = {}
    total = 0
    while stack:
        directory, prefix = stack.pop()
        for name, path, metadata in _scandir(directory, "client proof directory"):
            relative = f"{prefix}{name}"
            relative = _safe_relative(relative)
            if stat.S_ISDIR(metadata.st_mode):
                stack.append((path, relative + "/"))
            elif stat.S_ISREG(metadata.st_mode):
                if relative in files:
                    raise ReviewFailure("client proof contains a duplicate canonical path")
                if len(files) >= MAX_PROOF_FILES:
                    raise ReviewFailure("client proof file count exceeds limit")
                if metadata.st_size < 0 or metadata.st_size > MAX_PROOF_FILE:
                    raise ReviewFailure("client proof file exceeds limit")
                total += metadata.st_size
                if total > MAX_PROOF_TOTAL:
                    raise ReviewFailure("client proof total size exceeds limit")
                files[relative] = (path, metadata)
            else:
                raise ReviewFailure("client proof contains a non-regular entry")
    ordered = sorted(files, key=lambda item: item.encode("utf-8"))
    manifest: list[dict[str, object]] = []
    aggregate = hashlib.sha256()
    for relative in ordered:
        path, metadata = files[relative]
        digest = _hash_proof_file(path, metadata)
        manifest.append(
            {"path": relative, "sizeBytes": metadata.st_size, "sha256": digest}
        )
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(digest.encode("ascii"))
        aggregate.update(b"\n")
    return {
        "fileCount": len(manifest),
        "totalBytes": total,
        "aggregateSha256": aggregate.hexdigest(),
        "files": manifest,
    }


def _reference_from_commit(repo: Path, commit: str) -> dict[str, Any]:
    raw, _fact = _blob_at(repo, commit, SOURCE_LOCK_PATH)
    value = _load_json(raw, "source-lock")
    core = value.get("coreBaseline")
    reference = core.get("clientDistReference") if isinstance(core, dict) else None
    if not isinstance(reference, dict):
        raise ReviewFailure("client proof reference is missing")
    return reference


def _source_proof_payload(
    repo: Path,
    scope: dict[str, Any],
    source_dist: Path,
) -> dict[str, Any]:
    if scope["kind"] == "functional":
        raise ReviewFailure("functional candidates do not use trust source proof")
    proof = client_proof(source_dist)
    base_reference = _reference_from_commit(repo, scope["baseCommit"])
    candidate_reference = _reference_from_commit(repo, scope["candidateCommit"])
    if (
        proof["fileCount"] != base_reference.get("fileCount")
        or proof["totalBytes"] != base_reference.get("totalBytes")
    ):
        raise ReviewFailure("source client proof shape differs from base")
    if proof["aggregateSha256"] != candidate_reference.get("aggregateSha256"):
        raise ReviewFailure("source client proof aggregate differs from candidate")
    if scope["kind"] == "diagnostics" and not _json_equal(base_reference, candidate_reference):
        raise ReviewFailure("diagnostics candidate changed the client proof reference")
    return {
        "schemaVersion": 1,
        "status": "source_proof_validated",
        "qualification": "not_run",
        "promotion": "manual_required",
        "full": "not_applicable_trust_only",
        "dependencyRestoreNetworked": True,
        "sourceProofAuthority": "caller_supplied",
        "sourceClientProofOrigin": "caller_supplied_unverified",
        "scope": scope,
        "sourceClientProof": proof,
    }


def _with_receipt_hash(payload: dict[str, Any]) -> dict[str, Any]:
    if "receiptSha256" in payload:
        raise ReviewFailure("receipt hash field is reserved")
    result = dict(payload)
    result["receiptSha256"] = _sha256(_canonical(payload))
    return result


def _publish_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        if ".." in path.parts or path.name in {"", ".", ".."}:
            raise ReviewFailure("output path is unsafe")
        if path.is_symlink():
            raise ReviewFailure("output must not be a symlink")
        parent = path.parent.absolute()
        ancestor = parent
        while True:
            metadata = _directory_metadata(ancestor, "output path ancestor")
            if _is_reparse(metadata):
                raise ReviewFailure("output path ancestor must not be a reparse point")
            if ancestor.parent == ancestor:
                break
            ancestor = ancestor.parent
        _directory_metadata(parent, "output directory")
        encoded = _canonical(payload) + b"\n"
        descriptor, temporary_name = tempfile.mkstemp(prefix=".trust-review.", suffix=".tmp", dir=parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, path.absolute())
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    except ReviewFailure:
        raise
    except FileExistsError as exc:
        raise ReviewFailure("output already exists") from exc
    except OSError as exc:
        raise ReviewFailure("output publication failed") from exc


def source_proof(
    repo: Path,
    base: str,
    candidate: str,
    source_dist: Path,
    output: Path,
    *,
    reviewer_file: Path | None = None,
) -> dict[str, Any]:
    scope = audit(repo, base, candidate, reviewer_file=reviewer_file)
    payload = _with_receipt_hash(_source_proof_payload(repo.resolve(), scope, source_dist))
    _publish_json(output, payload)
    return payload


def _parse_count(value: str | None, label: str) -> int:
    if value is None or not INTEGER_RE.fullmatch(value):
        raise ReviewFailure(f"JUnit {label} summary is invalid")
    return int(value)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _junit_summary(raw: bytes, platform: str, kind: str) -> dict[str, int]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ReviewFailure("JUnit XML must be UTF-8") from exc
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise ReviewFailure("JUnit declarations are forbidden")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ReviewFailure("JUnit XML is invalid") from exc
    root_name = _local_name(root.tag)
    if root_name not in {"testsuite", "testsuites"}:
        raise ReviewFailure("JUnit root is invalid")
    suites: list[ET.Element]
    if root_name == "testsuites":
        if any(_local_name(child.tag) != "testsuite" for child in root):
            raise ReviewFailure("JUnit testsuites contains an invalid child")
        suites = list(root)
        if not suites:
            raise ReviewFailure("JUnit has no test suites")
    else:
        suites = [root]
    for suite in suites:
        if any(_local_name(child.tag) != "testcase" for child in suite):
            raise ReviewFailure("JUnit testsuite contains an invalid child")
    cases = [case for suite in suites for case in suite]
    if not cases:
        raise ReviewFailure("JUnit has no test cases")
    observed: list[tuple[str, bool, bool, bool]] = []
    case_ids: set[tuple[str, str]] = set()
    for case in cases:
        name = case.attrib.get("name")
        if not isinstance(name, str) or not name:
            raise ReviewFailure("JUnit test case name is missing")
        case_id = (case.attrib.get("classname", ""), name)
        if case_id in case_ids:
            raise ReviewFailure("JUnit contains a duplicate test case id")
        case_ids.add(case_id)
        children = [_local_name(child.tag) for child in case]
        if any(child not in {"failure", "error", "skipped"} for child in children):
            raise ReviewFailure("JUnit test case contains an invalid child")
        failures = children.count("failure")
        errors = children.count("error")
        skips = children.count("skipped")
        if failures > 1 or errors > 1 or skips > 1 or sum((failures, errors, skips)) > 1:
            raise ReviewFailure("JUnit test case outcome is ambiguous")
        observed.append((name, bool(failures), bool(errors), bool(skips)))
    for suite in suites:
        suite_cases = list(suite)
        actual_tests = len(suite_cases)
        actual_failures = sum(
            any(_local_name(child.tag) == "failure" for child in case) for case in suite_cases
        )
        actual_errors = sum(
            any(_local_name(child.tag) == "error" for child in case) for case in suite_cases
        )
        actual_skips = sum(
            any(_local_name(child.tag) == "skipped" for child in case) for case in suite_cases
        )
        declared = {
            "tests": _parse_count(suite.attrib.get("tests"), "tests"),
            "failures": _parse_count(suite.attrib.get("failures"), "failures"),
            "errors": _parse_count(suite.attrib.get("errors"), "errors"),
            "skipped": _parse_count(suite.attrib.get("skipped"), "skipped"),
        }
        if declared != {
            "tests": actual_tests,
            "failures": actual_failures,
            "errors": actual_errors,
            "skipped": actual_skips,
        }:
            raise ReviewFailure("JUnit declared counts do not match test nodes")
    if root_name == "testsuites":
        summary_names = {"tests", "failures", "errors", "skipped"}
        present = summary_names & root.attrib.keys()
        if present and present != summary_names:
            raise ReviewFailure("JUnit testsuites summary is incomplete")
        if present:
            root_declared = {
                name: _parse_count(root.attrib.get(name), name) for name in summary_names
            }
            root_actual = {
                "tests": len(observed),
                "failures": sum(item[1] for item in observed),
                "errors": sum(item[2] for item in observed),
                "skipped": sum(item[3] for item in observed),
            }
            if root_declared != root_actual:
                raise ReviewFailure("JUnit testsuites counts do not match test nodes")
    failures = sum(item[1] for item in observed)
    errors = sum(item[2] for item in observed)
    skips = sum(item[3] for item in observed)
    if failures or errors:
        raise ReviewFailure("JUnit contains failing tests")
    allowed_skips = WINDOWS_ALLOWED_SKIPS if platform == "windows" else LINUX_ALLOWED_SKIPS
    skipped_names = {name for name, _failure, _error, skipped in observed if skipped}
    if not skipped_names <= allowed_skips:
        raise ReviewFailure("JUnit contains an unknown skipped test")
    required = PATH_ORDER_REQUIRED_TESTS if kind == "path_order" else DIAGNOSTICS_REQUIRED_TESTS
    for required_name in required:
        matching = [item for item in observed if item[0].split("[", 1)[0] == required_name]
        if not matching or any(item[3] for item in matching):
            raise ReviewFailure("JUnit required trust regression is missing or skipped")
    return {
        "tests": len(observed),
        "failures": failures,
        "errors": errors,
        "skipped": skips,
        "passed": len(observed) - skips,
    }


def _read_junit(path: Path, platform: str, kind: str) -> tuple[bytes, dict[str, int]]:
    raw = _read_local_regular(path, limit=MAX_EVIDENCE_FILE, label="JUnit evidence")
    return raw, _junit_summary(raw, platform, kind)


def record_tests(
    repo: Path,
    base: str,
    candidate: str,
    platform: str,
    junit: Path,
    output: Path,
    *,
    reviewer_file: Path | None = None,
) -> dict[str, Any]:
    if platform not in {"windows", "linux"}:
        raise ReviewFailure("invalid test platform")
    if sys.version_info[:3] != (3, 12, 13):
        raise ReviewFailure("test record requires Python 3.12.13")
    running_platform = "windows" if sys.platform == "win32" else "linux" if sys.platform.startswith("linux") else None
    if platform != running_platform:
        raise ReviewFailure("test record platform does not match the running host")
    scope = audit(repo, base, candidate, reviewer_file=reviewer_file)
    if scope["kind"] == "functional":
        raise ReviewFailure("functional candidates do not use trust test records")
    raw, _summary = _read_junit(junit, platform, scope["kind"])
    metadata = {
        "schemaVersion": 1,
        "baseCommit": scope["baseCommit"],
        "candidateCommit": scope["candidateCommit"],
        "candidateTree": scope["candidateTree"],
        "platform": platform,
        "pythonVersion": "3.12.13",
        "junitSha256": _sha256(raw),
        "authority": "untrusted_ci_observation",
    }
    _publish_json(output, metadata)
    return metadata


def _evidence_platform(
    root: Path,
    platform: str,
    scope: dict[str, Any],
) -> dict[str, Any]:
    directory = root / platform
    entries = _scandir(directory, f"{platform} evidence directory")
    names = {name for name, _path, _metadata in entries}
    if names != {"result.xml", "metadata.json"}:
        raise ReviewFailure("test evidence directory has unexpected entries")
    junit_path = directory / "result.xml"
    metadata_path = directory / "metadata.json"
    junit_raw, summary = _read_junit(junit_path, platform, scope["kind"])
    metadata_raw = _read_local_regular(
        metadata_path,
        limit=MAX_EVIDENCE_FILE,
        label="test metadata",
    )
    metadata = _load_json(metadata_raw, "test metadata")
    expected = {
        "schemaVersion": 1,
        "baseCommit": scope["baseCommit"],
        "candidateCommit": scope["candidateCommit"],
        "candidateTree": scope["candidateTree"],
        "platform": platform,
        "pythonVersion": "3.12.13",
        "junitSha256": _sha256(junit_raw),
        "authority": "untrusted_ci_observation",
    }
    if not _json_equal(metadata, expected):
        raise ReviewFailure("test metadata is not bound to the reviewed candidate")
    return {
        "junitSha256": _sha256(junit_raw),
        "metadataSha256": _sha256(metadata_raw),
        "summary": summary,
    }


def review(
    repo: Path,
    base: str,
    candidate: str,
    source_dist: Path,
    evidence_root: Path,
    output: Path,
    *,
    reviewer_file: Path | None = None,
) -> dict[str, Any]:
    scope = audit(repo, base, candidate, reviewer_file=reviewer_file)
    source_payload = _source_proof_payload(repo.resolve(), scope, source_dist)
    evidence_root = evidence_root.absolute()
    entries = _scandir(evidence_root, "test evidence root")
    if {name for name, _path, _metadata in entries} != {"windows", "linux"} or any(
        not stat.S_ISDIR(metadata.st_mode) for _name, _path, metadata in entries
    ):
        raise ReviewFailure("test evidence root must contain only windows and linux")
    tests = {
        platform: _evidence_platform(evidence_root, platform, scope)
        for platform in ("windows", "linux")
    }
    payload = _with_receipt_hash(
        {
            "schemaVersion": 1,
            "status": "passed_for_maintainer_merge_review",
            "qualification": "not_run",
            "productVisible": False,
            "testEvidenceAuthority": "untrusted_ci_observation",
            "promotion": "manual_required",
            "full": "not_applicable_trust_only",
            "dependencyRestoreNetworked": True,
            "sourceClientProofOrigin": "caller_supplied_unverified",
            "scope": scope,
            "sourceClientProof": source_payload["sourceClientProof"],
            "testEvidence": tests,
        }
    )
    _publish_json(output, payload)
    return payload


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", type=Path, required=True)
    common.add_argument("--base", required=True)
    common.add_argument("--candidate", required=True)
    parser = _ArgumentParser(add_help=False)
    subparsers = parser.add_subparsers(dest="command", required=True)
    scope_parser = subparsers.add_parser("scope", parents=[common], add_help=False)
    scope_parser.add_argument("--output", type=Path)
    source_parser = subparsers.add_parser("source-proof", parents=[common], add_help=False)
    source_parser.add_argument("--source-client-dist", type=Path, required=True)
    source_parser.add_argument("--output", type=Path, required=True)
    review_parser = subparsers.add_parser("review", parents=[common], add_help=False)
    review_parser.add_argument("--source-client-dist", type=Path, required=True)
    review_parser.add_argument("--evidence-root", type=Path, required=True)
    review_parser.add_argument("--output", type=Path, required=True)
    record_parser = subparsers.add_parser("record-tests", parents=[common], add_help=False)
    record_parser.add_argument("--platform", choices=("windows", "linux"), required=True)
    record_parser.add_argument("--junit", type=Path, required=True)
    record_parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def _print_summary(payload: dict[str, Any]) -> None:
    summary = {
        "status": payload.get("status", "recorded"),
    }
    scope = payload.get("scope")
    if isinstance(scope, dict):
        summary["kind"] = scope.get("kind")
        summary["baseCommit"] = scope.get("baseCommit")
        summary["candidateCommit"] = scope.get("candidateCommit")
    else:
        for key in ("kind", "baseCommit", "candidateCommit", "platform"):
            if key in payload:
                summary[key] = payload[key]
    print(_canonical(summary).decode("utf-8"), flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        effective_argv = list(sys.argv[1:] if argv is None else argv)
        if effective_argv == ["--help"]:
            print(
                _canonical(
                    {
                        "status": "help",
                        "subcommands": [
                            "scope",
                            "source-proof",
                            "review",
                            "record-tests",
                        ],
                    }
                ).decode("utf-8"),
                flush=True,
            )
            return 0
        args = _parse_args(effective_argv)
        if args.command == "scope":
            result = audit(args.repo, args.base, args.candidate)
            if args.output is not None:
                _publish_json(args.output, result)
        elif args.command == "source-proof":
            result = source_proof(
                args.repo,
                args.base,
                args.candidate,
                args.source_client_dist,
                args.output,
            )
        elif args.command == "review":
            result = review(
                args.repo,
                args.base,
                args.candidate,
                args.source_client_dist,
                args.evidence_root,
                args.output,
            )
        else:
            result = record_tests(
                args.repo,
                args.base,
                args.candidate,
                args.platform,
                args.junit,
                args.output,
            )
        _print_summary(result)
        return 0
    except ReviewFailure as exc:
        error_code = (
            "base_reviewer_missing"
            if str(exc) == "bootstrap required: base reviewer is not locked"
            else "review_failed"
        )
        print(
            _canonical(
                {
                    "errorCode": error_code,
                    "errorType": "ReviewFailure",
                    "status": "failed",
                }
            ).decode("utf-8"),
            flush=True,
        )
        return 1
    except Exception:
        print('{"errorType":"InternalError","status":"failed"}', flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
