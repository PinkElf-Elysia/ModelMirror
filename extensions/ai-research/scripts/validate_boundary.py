from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = MODULE_ROOT.parents[1]


class BoundaryFailure(RuntimeError):
    pass


def is_ui_generated(path: Path) -> bool:
    relative = path.relative_to(MODULE_ROOT).parts
    return len(relative) >= 2 and relative[:2] in {
        ("ui", "node_modules"),
        ("ui", "dist"),
    }


def git(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def changed_paths(base: str) -> set[str]:
    paths = set(git("diff", "--name-only", "--diff-filter=ACMRTUXB", base))
    paths.update(git("ls-files", "--others", "--exclude-standard"))
    return paths


def validate_requested_base(requested_base: str, locked_base: str) -> None:
    requested = git("rev-parse", requested_base)
    if not requested:
        raise BoundaryFailure(f"requested base cannot be resolved: {requested_base}")
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", locked_base, requested[0]],
        cwd=REPO_ROOT,
        check=False,
    )
    if result.returncode != 0:
        raise BoundaryFailure(
            f"requested base {requested_base} diverged from locked base {locked_base}"
        )
    if requested[0] != locked_base:
        print(
            f"AI Research base notice: {requested_base} advanced to {requested[0]}; "
            f"scope remains pinned to {locked_base}",
            file=sys.stderr,
        )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_paths(base: str, boundary: dict) -> None:
    allowed_parent = set(boundary["allowedParentFiles"])
    prefix = "extensions/ai-research/"
    illegal = sorted(
        path for path in changed_paths(base) if not path.startswith(prefix) and path not in allowed_parent
    )
    if illegal:
        raise BoundaryFailure(f"files outside the approved boundary changed: {illegal}")
    for path in MODULE_ROOT.rglob("*"):
        if is_ui_generated(path):
            continue
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise BoundaryFailure(f"symlink is forbidden: {path.relative_to(MODULE_ROOT)}")


def validate_locked_files(source_lock: dict) -> None:
    for relative, descriptor in source_lock["lockedFiles"].items():
        path = MODULE_ROOT / relative
        if not path.is_file():
            raise BoundaryFailure(f"locked file is missing: {relative}")
        if path.stat().st_size != descriptor["sizeBytes"] or sha256(path) != descriptor["sha256"]:
            raise BoundaryFailure(f"locked file drifted: {relative}")
    for relative in ("control/requirements.lock", "worker/requirements.lock", "requirements-test.lock"):
        text = (MODULE_ROOT / relative).read_text(encoding="utf-8")
        if "--hash=sha256:" not in text or "index-url" in "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        ):
            raise BoundaryFailure(f"dependency lock is not hash-only and index-neutral: {relative}")
    license_audit = source_lock["licenseAudit"]
    if sha256(MODULE_ROOT / "license-policy.json") != license_audit["policySha256"]:
        raise BoundaryFailure("license policy drifted from the source lock")
    ui_package = (MODULE_ROOT / "ui" / "package.json").read_text(encoding="utf-8")
    ui_lock = (MODULE_ROOT / "ui" / "package-lock.json").read_text(encoding="utf-8")
    if "workspace:" in ui_package + ui_lock or "file:" in ui_package + ui_lock:
        raise BoundaryFailure("UI dependencies must not use workspace: or file: references")
    subprocess.run(
        [
            sys.executable,
            str(MODULE_ROOT / "scripts" / "audit_ui_licenses.py"),
            "--lock",
            str(MODULE_ROOT / "ui" / "package-lock.json"),
            "--policy",
            str(MODULE_ROOT / "license-policy.json"),
            "--output",
            str(MODULE_ROOT / "runtime" / "sbom" / "ui-build-inventory.json"),
        ],
        check=True,
    )


def validate_runtime_references(boundary: dict) -> None:
    runtime_suffixes = {".py", ".sh", ".ps1", ".ts", ".tsx", ".js", ".cjs", ".html"}
    runtime_names = {"Dockerfile", "compose.yml"}
    for path in MODULE_ROOT.rglob("*"):
        if (
            not path.is_file()
            or "tests" in path.parts
            or "scripts" in path.parts
            or is_ui_generated(path)
        ):
            continue
        if path.suffix not in runtime_suffixes and path.name not in runtime_names:
            continue
        text = path.read_text(encoding="utf-8", errors="strict")
        for forbidden in boundary["forbiddenRuntimeReferences"]:
            if forbidden in text:
                raise BoundaryFailure(
                    f"forbidden runtime reference {forbidden!r} in {path.relative_to(MODULE_ROOT)}"
                )
        if "C:\\Users\\" in text or "/Users/" in text:
            raise BoundaryFailure(f"host absolute path in {path.relative_to(MODULE_ROOT)}")


def validate_metric_names() -> None:
    forbidden = {"score", "accuracy", "win_rate"}
    for path in (MODULE_ROOT / "control" / "ai_research_control").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "log_metric" or not node.args:
                continue
            first = node.args[1] if len(node.args) > 1 else None
            if isinstance(first, ast.Constant) and first.value in forbidden:
                raise BoundaryFailure(f"scientific metric name logged in {path.name}: {first.value}")


def validate_parent_controls() -> None:
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    if "/extensions/ai-research" not in dockerignore.splitlines():
        raise BoundaryFailure("root .dockerignore does not exclude the optional module")
    workflow = REPO_ROOT / ".github" / "workflows" / "ai-research.yml"
    if not workflow.is_file() or "extensions/ai-research/**" not in workflow.read_text(encoding="utf-8"):
        raise BoundaryFailure("path-filtered module workflow is missing")


def validate_runtime_privacy_defaults() -> None:
    compose = (MODULE_ROOT / "compose.yml").read_text(encoding="utf-8")
    if compose.count('MLFLOW_DISABLE_TELEMETRY: "true"') != 2:
        raise BoundaryFailure(
            "MLflow telemetry must be disabled in both control and tracking services"
        )


def validate_no_secrets(boundary: dict) -> None:
    patterns = [
        re.compile(r"-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(r"sk-" + r"(?:or-v1-)?[A-Za-z0-9_-]{32,}"),
        re.compile(r"gh" + r"[pousr]_[A-Za-z0-9]{30,}"),
        re.compile(r"AIza" + r"[A-Za-z0-9_-]{30,}"),
    ]
    candidates = [
        path for path in MODULE_ROOT.rglob("*") if path.is_file() and not is_ui_generated(path)
    ]
    candidates.extend(REPO_ROOT / relative for relative in boundary["allowedParentFiles"])
    for path in candidates:
        if path.suffix in {".pyc", ".png", ".webp", ".zip", ".gz", ".tar"}:
            continue
        if path.stat().st_size > 5_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in patterns):
            raise BoundaryFailure(f"high-confidence secret pattern detected: {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--allow-pending-license", action="store_true")
    args = parser.parse_args()
    boundary = json.loads((MODULE_ROOT / "module-boundary.json").read_text(encoding="utf-8"))
    source_lock = json.loads((MODULE_ROOT / "source-lock.json").read_text(encoding="utf-8"))
    locked_base = source_lock["modelMirrorBaseCommit"]
    validate_requested_base(args.base, locked_base)
    validate_paths(locked_base, boundary)
    validate_locked_files(source_lock)
    validate_runtime_references(boundary)
    validate_metric_names()
    validate_parent_controls()
    validate_runtime_privacy_defaults()
    validate_no_secrets(boundary)
    if source_lock["licenseAudit"]["status"] != "passed" and not args.allow_pending_license:
        raise BoundaryFailure("runtime license audit is not passed")
    print("AI Research boundary validation passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BoundaryFailure, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"AI Research boundary validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
