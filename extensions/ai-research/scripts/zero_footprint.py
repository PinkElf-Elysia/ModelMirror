from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = MODULE_ROOT.parents[1]
MODULE_PREFIX = "extensions/ai-research/"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
TRUST_FILES = (
    "extensions/ai-research/source-lock.json",
    "extensions/ai-research/module-boundary.json",
)


class BaselineFailure(RuntimeError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=REPO_ROOT,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def command(*args: str) -> str:
    return run(*args).stdout


def git_paths(*args: str) -> set[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    try:
        paths = {item.decode("utf-8") for item in result.stdout.split(b"\0") if item}
    except UnicodeDecodeError as exc:
        raise BaselineFailure("Git paths are not valid UTF-8") from exc
    if any("\\" in path for path in paths):
        raise BaselineFailure("Git paths contain an unsafe backslash")
    return paths


def git_blob(commit: str, relative: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return result.stdout


def client_dist(root: Path) -> dict[str, object]:
    if not root.is_dir():
        raise BaselineFailure(f"client dist is missing: {root}")
    entries = list(root.iterdir())
    if len(entries) == 1 and entries[0].name == "dist" and entries[0].is_dir():
        root = entries[0]
    files = sorted(path for path in root.rglob("*") if path.is_file())
    pairs = b""
    for path in files:
        relative = path.relative_to(root).as_posix()
        pairs += relative.encode("utf-8") + b"\0" + sha256(path).encode("ascii") + b"\n"
    return {
        "fileCount": len(files),
        "totalBytes": sum(path.stat().st_size for path in files),
        "aggregateSha256": hashlib.sha256(pairs).hexdigest(),
    }


def resolve_commit(reference: str) -> str:
    if not reference.strip() or set(reference.strip()) == {"0"}:
        raise BaselineFailure("Git comparison base is missing or all-zero")
    result = run("git", "rev-parse", "--verify", f"{reference}^{{commit}}", check=False)
    commit = result.stdout.strip().lower()
    if result.returncode != 0 or not COMMIT_RE.fullmatch(commit):
        raise BaselineFailure(f"Git commit cannot be resolved: {reference}")
    return commit


def require_ancestor(ancestor: str, descendant: str, message: str) -> None:
    result = run("git", "merge-base", "--is-ancestor", ancestor, descendant, check=False)
    if result.returncode != 0:
        raise BaselineFailure(message)


def validate_lineage(locked_reference: str, requested_base: str) -> tuple[str, str, str]:
    locked_commit = resolve_commit(locked_reference)
    base_commit = resolve_commit(requested_base)
    head_commit = resolve_commit("HEAD")
    require_ancestor(
        locked_commit,
        base_commit,
        f"requested base {base_commit} diverged from locked source {locked_commit}",
    )
    require_ancestor(
        base_commit,
        head_commit,
        f"requested base {base_commit} is not an ancestor of HEAD {head_commit}",
    )
    return locked_commit, base_commit, head_commit


def load_trusted_configuration(
    base_commit: str,
) -> tuple[dict[str, object], dict[str, object]]:
    try:
        source_lock = json.loads(git_blob(base_commit, TRUST_FILES[0]).decode("utf-8"))
        boundary = json.loads(git_blob(base_commit, TRUST_FILES[1]).decode("utf-8"))
    except (UnicodeDecodeError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise BaselineFailure("trusted configuration cannot be loaded from caller base") from exc
    if not isinstance(source_lock, dict) or not isinstance(boundary, dict):
        raise BaselineFailure("trusted configuration in caller base must be JSON objects")
    return source_lock, boundary


def validate_trust_files_unchanged(base_commit: str, head_commit: str) -> None:
    for relative in TRUST_FILES:
        try:
            trusted = git_blob(base_commit, relative)
            candidate = git_blob(head_commit, relative)
            workspace = (REPO_ROOT / relative).read_bytes()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise BaselineFailure(f"trusted configuration is missing: {relative}") from exc
        if candidate != trusted or workspace != trusted:
            raise BaselineFailure(f"trusted configuration changed in current batch: {relative}")


def changed_paths(base_commit: str, head_commit: str) -> set[str]:
    return git_paths(
        "diff",
        "--name-only",
        "-z",
        "--no-renames",
        "--diff-filter=ACDMRTUXB",
        base_commit,
        head_commit,
        "--",
    )


def validate_current_scope(
    base_commit: str,
    head_commit: str,
    boundary: dict[str, object],
) -> list[str]:
    allowed_parent_value = boundary.get("allowedParentFiles")
    if not isinstance(allowed_parent_value, list) or not all(
        isinstance(path, str) and path for path in allowed_parent_value
    ):
        raise BaselineFailure("trusted module boundary has invalid allowedParentFiles")
    if any("\\" in path for path in allowed_parent_value):
        raise BaselineFailure("trusted allowedParentFiles contains an unsafe backslash")
    allowed_parent = set(allowed_parent_value)
    changed = changed_paths(base_commit, head_commit)
    forbidden = sorted(
        path
        for path in changed
        if not path.startswith(MODULE_PREFIX) and path not in allowed_parent
    )
    if forbidden:
        raise BaselineFailure(f"forbidden current-batch paths changed: {forbidden}")
    return sorted(changed)


def validate_locked_source(
    source_lock: dict[str, object],
    locked_commit: str,
    source_client_dist: dict[str, object],
) -> None:
    baseline = source_lock.get("coreBaseline")
    if not isinstance(baseline, dict):
        raise BaselineFailure("source-lock coreBaseline evidence is missing or invalid")
    gate = baseline.get("clientDistGate")
    if not isinstance(gate, dict) or gate.get("baseCommit") != locked_commit:
        raise BaselineFailure("locked client proof commit drifted from source-lock provenance")

    tracked_files = baseline.get("trackedFiles")
    if not isinstance(tracked_files, dict) or not tracked_files:
        raise BaselineFailure("source-lock core tracked file evidence is missing")
    for relative, expected in tracked_files.items():
        actual = sha256_bytes(git_blob(locked_commit, str(relative)))
        if actual != expected:
            raise BaselineFailure(f"locked source hash drifted: {relative}")

    expected_client_dist = baseline.get("clientDistReference")
    if source_client_dist != expected_client_dist:
        raise BaselineFailure(
            "locked source client proof drifted: "
            f"expected={json.dumps(expected_client_dist, sort_keys=True)} "
            f"actual={json.dumps(source_client_dist, sort_keys=True)}"
        )


def validate_protected_batch_files(
    source_lock: dict[str, object], base_commit: str, head_commit: str
) -> None:
    baseline = source_lock.get("coreBaseline")
    if not isinstance(baseline, dict):
        raise BaselineFailure("source-lock coreBaseline evidence is missing or invalid")
    protected = baseline.get("trackedFiles")
    if not isinstance(protected, dict) or not protected:
        raise BaselineFailure("source-lock protected core file list is missing")
    for relative in protected:
        before = git_blob(base_commit, str(relative))
        after = git_blob(head_commit, str(relative))
        if before != after:
            raise BaselineFailure(f"protected core file changed in current batch: {relative}")


def render_current_compose() -> tuple[list[str], list[str]]:
    compose_config = json.loads(command("docker", "compose", "config", "--format", "json"))
    if not isinstance(compose_config, dict):
        raise BaselineFailure("current Compose rendering must be a JSON object")
    services = sorted((compose_config.get("services") or {}).keys())
    volumes = sorted((compose_config.get("volumes") or {}).keys())
    if not services:
        raise BaselineFailure("current Compose configuration has no services")
    return services, volumes


def validate_current_client_proof(
    baseline_client_dist: dict[str, object], current_client_dist: dict[str, object]
) -> None:
    if baseline_client_dist != current_client_dist:
        raise BaselineFailure(
            "root client changed in current batch: "
            f"base={json.dumps(baseline_client_dist, sort_keys=True)} "
            f"head={json.dumps(current_client_dist, sort_keys=True)}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-client-dist", type=Path, required=True)
    parser.add_argument("--baseline-client-dist", type=Path, required=True)
    parser.add_argument("--client-dist", type=Path, required=True)
    parser.add_argument("--base", required=True)
    args = parser.parse_args(argv)

    base_commit = resolve_commit(args.base)
    head_commit = resolve_commit("HEAD")
    source_lock, boundary = load_trusted_configuration(base_commit)
    validate_trust_files_unchanged(base_commit, head_commit)
    locked_reference = source_lock.get("modelMirrorBaseCommit")
    if not isinstance(locked_reference, str) or not COMMIT_RE.fullmatch(
        locked_reference.lower()
    ):
        raise BaselineFailure("source-lock modelMirrorBaseCommit is missing or invalid")
    if boundary.get("baseCommit") != locked_reference:
        raise BaselineFailure("module boundary and source lock disagree on locked source commit")

    locked_commit, lineage_base, lineage_head = validate_lineage(
        str(locked_reference), args.base
    )
    if lineage_base != base_commit or lineage_head != head_commit:
        raise BaselineFailure("resolved Git identities changed during zero-footprint validation")
    source_client_dist = client_dist(args.source_client_dist.resolve())
    baseline_client_dist = client_dist(args.baseline_client_dist.resolve())
    current_client_dist = client_dist(args.client_dist.resolve())

    validate_locked_source(source_lock, locked_commit, source_client_dist)
    current_paths = validate_current_scope(base_commit, head_commit, boundary)
    validate_protected_batch_files(source_lock, base_commit, head_commit)
    validate_current_client_proof(baseline_client_dist, current_client_dist)

    services, volumes = render_current_compose()
    print(
        json.dumps(
            {
                "status": "passed",
                "lockedSourceCommit": locked_commit,
                "baseCommit": base_commit,
                "headCommit": head_commit,
                "sourceClientDist": source_client_dist,
                "baselineClientDist": baseline_client_dist,
                "clientDist": current_client_dist,
                "currentBatchPaths": current_paths,
                "currentComposeServiceCount": len(services),
                "currentComposeVolumeCount": len(volumes),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BaselineFailure, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"zero-footprint validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
