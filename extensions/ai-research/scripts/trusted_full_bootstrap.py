from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any, Sequence


MODULE_REL = PurePosixPath("extensions/ai-research")
SOURCE_LOCK_REL = MODULE_REL / "source-lock.json"
BOUNDARY_REL = MODULE_REL / "module-boundary.json"
BOOTSTRAP_REL = MODULE_REL / "scripts/trusted_full_bootstrap.py"
COMMIT_RE = re.compile(r"[0-9a-f]{40}")


class BootstrapFailure(RuntimeError):
    pass


def git_bytes(repo: Path, *args: str, check: bool = True) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise BootstrapFailure(message or f"git {' '.join(args)} failed")
    return completed.stdout


def resolve_commit(repo: Path, ref: str) -> str:
    value = git_bytes(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip().lower()
    if not COMMIT_RE.fullmatch(value):
        raise BootstrapFailure(f"invalid commit: {ref}")
    return value


def resolve_tree(repo: Path, commit: str) -> str:
    value = git_bytes(repo, "rev-parse", "--verify", f"{commit}^{{tree}}").decode().strip().lower()
    if not COMMIT_RE.fullmatch(value):
        raise BootstrapFailure(f"invalid tree for commit: {commit}")
    return value


def commit_file(repo: Path, commit: str, relative: PurePosixPath) -> bytes:
    return git_bytes(repo, "show", f"{commit}:{relative.as_posix()}")


def require_clean(repo: Path) -> None:
    if git_bytes(repo, "status", "--porcelain", "--untracked-files=all"):
        raise BootstrapFailure(f"worktree must be clean: {repo}")


def safe_locked_name(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise BootstrapFailure("locked file name is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise BootstrapFailure(f"locked file name is unsafe: {value}")
    return path


def require_regular_file(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BootstrapFailure(f"locked file is unavailable: {path}") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    if path.is_symlink() or (reparse_flag and file_attributes & reparse_flag):
        raise BootstrapFailure(f"locked file is a link or reparse point: {path}")
    if not stat.S_ISREG(metadata.st_mode):
        raise BootstrapFailure(f"locked path is not a regular file: {path}")
    return metadata


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json_bytes(raw: bytes, name: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BootstrapFailure(f"trusted {name} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise BootstrapFailure(f"trusted {name} must be an object")
    return value


def changed_paths(repo: Path, trust_commit: str, candidate_commit: str) -> set[str]:
    raw = git_bytes(
        repo,
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        trust_commit,
        candidate_commit,
    )
    return {
        value.decode("utf-8", errors="surrogateescape")
        for value in raw.split(b"\0")
        if value
    }


def validate_candidate(
    *,
    trust_repo: Path,
    candidate_repo: Path,
    trust_ref: str,
    candidate_ref: str,
) -> dict[str, Any]:
    trust_commit = resolve_commit(trust_repo, trust_ref)
    candidate_commit = resolve_commit(candidate_repo, candidate_ref)
    if resolve_commit(trust_repo, "HEAD") != trust_commit:
        raise BootstrapFailure("bootstrap must run from the exact detached trust commit")
    require_clean(trust_repo)
    require_clean(candidate_repo)
    if resolve_commit(candidate_repo, "HEAD") != candidate_commit:
        raise BootstrapFailure("candidate worktree HEAD does not match candidate commit")
    ancestry = subprocess.run(
        ["git", "-C", str(candidate_repo), "merge-base", "--is-ancestor", trust_commit, candidate_commit],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if ancestry.returncode != 0:
        raise BootstrapFailure("trust commit is not an ancestor of candidate commit")

    source_lock_raw = commit_file(trust_repo, trust_commit, SOURCE_LOCK_REL)
    boundary_raw = commit_file(trust_repo, trust_commit, BOUNDARY_REL)
    for relative, expected in ((SOURCE_LOCK_REL, source_lock_raw), (BOUNDARY_REL, boundary_raw)):
        if commit_file(candidate_repo, candidate_commit, relative) != expected:
            raise BootstrapFailure(f"candidate changed trusted configuration: {relative.as_posix()}")

    source_lock = load_json_bytes(source_lock_raw, "source-lock")
    boundary = load_json_bytes(boundary_raw, "module-boundary")
    locked = source_lock.get("lockedFiles")
    if not isinstance(locked, dict) or not locked:
        raise BootstrapFailure("trusted source-lock has no lockedFiles")
    if "scripts/trusted_full_bootstrap.py" not in locked:
        raise BootstrapFailure("trusted bootstrap is not included in lockedFiles")

    module_root = candidate_repo / Path(*MODULE_REL.parts)
    verified_count = 0
    for raw_name, descriptor in locked.items():
        relative = safe_locked_name(raw_name)
        if not isinstance(descriptor, dict):
            raise BootstrapFailure(f"locked descriptor is invalid: {raw_name}")
        expected_size = descriptor.get("sizeBytes")
        expected_hash = descriptor.get("sha256")
        if not isinstance(expected_size, int) or expected_size < 0:
            raise BootstrapFailure(f"locked size is invalid: {raw_name}")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise BootstrapFailure(f"locked hash is invalid: {raw_name}")
        candidate_path = module_root / Path(*relative.parts)
        metadata = require_regular_file(candidate_path)
        if metadata.st_size != expected_size or sha256_file(candidate_path) != expected_hash:
            raise BootstrapFailure(f"locked file drifted: {raw_name}")
        verified_count += 1

    raw_post_trust = boundary.get("postTrustAllowedFiles")
    raw_parent = boundary.get("allowedParentFiles")
    if not isinstance(raw_post_trust, list) or not isinstance(raw_parent, list):
        raise BootstrapFailure("post-trust boundary is missing")
    post_trust = {safe_locked_name(value).as_posix() for value in raw_post_trust}
    parent = {safe_locked_name(value).as_posix() for value in raw_parent}
    if not post_trust or not post_trust <= parent:
        raise BootstrapFailure("post-trust files must be a non-empty subset of allowed parent files")
    if any(value.startswith(f"{MODULE_REL.as_posix()}/") for value in post_trust):
        raise BootstrapFailure("post-trust changes cannot include module verifier assets")
    changed = changed_paths(candidate_repo, trust_commit, candidate_commit)
    unexpected = sorted(changed - post_trust)
    if unexpected:
        raise BootstrapFailure(f"post-trust candidate changed forbidden files: {unexpected}")

    return {
        "trustCommit": trust_commit,
        "trustTree": resolve_tree(trust_repo, trust_commit),
        "candidateCommit": candidate_commit,
        "candidateTree": resolve_tree(candidate_repo, candidate_commit),
        "sourceLockSha256": hashlib.sha256(source_lock_raw).hexdigest(),
        "lockedFileCount": verified_count,
        "postTrustChangedFiles": sorted(changed),
    }


def verification_command(args: argparse.Namespace, candidate_repo: Path) -> list[str]:
    module_root = candidate_repo / Path(*MODULE_REL.parts)
    runner = args.runner
    if runner == "auto":
        runner = "windows" if os.name == "nt" else "linux"
    if runner == "windows":
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            raise BootstrapFailure("Windows PowerShell was not found")
        return [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(module_root / "scripts/verify.ps1"),
            "-Base",
            args.base,
            "-Mode",
            args.mode.capitalize(),
            "-DistributionMode",
            "ExternalPull" if args.distribution_mode == "external-pull" else "RedistributableBundle",
        ]
    bash = shutil.which("bash")
    if not bash:
        raise BootstrapFailure("bash was not found")
    return [
        bash,
        str(module_root / "scripts/verify.sh"),
        args.base,
        args.mode,
        args.distribution_mode,
    ]


def diagnostics_directories(candidate_repo: Path) -> set[Path]:
    root = candidate_repo / Path(*MODULE_REL.parts) / "runtime/diagnostics"
    if not root.exists():
        return set()
    return {path.resolve() for path in root.glob("verify-*") if path.is_dir()}


def write_receipt(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise BootstrapFailure(f"trusted receipt already exists: {path}")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    payload["receiptSha256"] = hashlib.sha256(canonical).hexdigest()
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-root", required=True, type=Path)
    parser.add_argument("--trust-commit", required=True)
    parser.add_argument("--candidate-commit", default="HEAD")
    parser.add_argument("--base", default="HEAD")
    parser.add_argument("--mode", choices=("quick", "full"), default="full")
    parser.add_argument(
        "--distribution-mode",
        choices=("external-pull", "redistributable-bundle"),
        required=True,
    )
    parser.add_argument("--runner", choices=("auto", "windows", "linux"), default="auto")
    parser.add_argument("--candidate-python")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    trust_repo = Path(__file__).resolve().parents[3]
    candidate_repo = args.candidate_root.resolve()
    before = validate_candidate(
        trust_repo=trust_repo,
        candidate_repo=candidate_repo,
        trust_ref=args.trust_commit,
        candidate_ref=args.candidate_commit,
    )
    resolved_base = resolve_commit(candidate_repo, args.base)
    before_dirs = diagnostics_directories(candidate_repo)
    environment = os.environ.copy()
    environment["AI_RESEARCH_TRUST_COMMIT"] = before["trustCommit"]
    environment["AI_RESEARCH_CANDIDATE_COMMIT"] = before["candidateCommit"]
    if args.candidate_python:
        environment["AI_RESEARCH_PYTHON"] = str(Path(args.candidate_python).resolve())
    completed = subprocess.run(
        verification_command(args, candidate_repo),
        cwd=candidate_repo / Path(*MODULE_REL.parts),
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        raise BootstrapFailure(f"candidate verification failed with exit code {completed.returncode}")
    after = validate_candidate(
        trust_repo=trust_repo,
        candidate_repo=candidate_repo,
        trust_ref=args.trust_commit,
        candidate_ref=before["candidateCommit"],
    )
    if after != before:
        raise BootstrapFailure("candidate or trust snapshot changed during verification")

    result: dict[str, Any] = {**after, "baseCommit": resolved_base, "mode": args.mode}
    if args.mode == "full":
        new_dirs = diagnostics_directories(candidate_repo) - before_dirs
        if len(new_dirs) != 1:
            raise BootstrapFailure("Full must create exactly one new diagnostics directory")
        evidence_root = new_dirs.pop()
        manifest_path = evidence_root / "full-acceptance-manifest.json"
        require_regular_file(manifest_path)
        manifest_raw = manifest_path.read_bytes()
        manifest = load_json_bytes(manifest_raw, "full acceptance manifest")
        if (
            manifest.get("status") != "passed"
            or manifest.get("headCommit") != after["candidateCommit"]
            or manifest.get("baseCommit") != resolved_base
            or manifest.get("sourceLockSha256") != after["sourceLockSha256"]
        ):
            raise BootstrapFailure("Full manifest is not bound to the trusted candidate snapshot")
        receipt_path = evidence_root / "trusted-full-bootstrap.json"
        result.update(
            {
                "schemaVersion": 1,
                "status": "passed",
                "distributionMode": args.distribution_mode,
                "manifestSha256": hashlib.sha256(manifest_raw).hexdigest(),
                "generatedAt": datetime.now(UTC).isoformat(),
            }
        )
        write_receipt(receipt_path, result)
        result["receipt"] = str(receipt_path)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BootstrapFailure as exc:
        print(f"AI Research trusted Full bootstrap failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
