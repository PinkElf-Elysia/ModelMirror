from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "trusted_full_bootstrap.py"
SPEC = importlib.util.spec_from_file_location("trusted_full_bootstrap", SCRIPT)
assert SPEC and SPEC.loader
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def commit(repo: Path, message: str) -> str:
    git(repo, "add", ".")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def make_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    scripts = repo / "extensions/ai-research/scripts"
    scripts.mkdir(parents=True)
    (repo / "server").mkdir()
    (repo / ".gitignore").write_text("*.py[cod]\n", encoding="utf-8")
    (scripts / "trusted_full_bootstrap.py").write_bytes(b"trusted-bootstrap\n")
    (scripts / "verify.ps1").write_bytes(b"trusted-verifier\n")
    (repo / "server/runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
    locked = {}
    for name in ("scripts/trusted_full_bootstrap.py", "scripts/verify.ps1"):
        raw = (repo / "extensions/ai-research" / name).read_bytes()
        locked[name] = {"sizeBytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    (repo / "extensions/ai-research/source-lock.json").write_text(
        json.dumps({"lockedFiles": locked}), encoding="utf-8"
    )
    (repo / "extensions/ai-research/module-boundary.json").write_text(
        json.dumps(
            {
                "allowedParentFiles": ["server/runtime.py"],
                "postTrustAllowedFiles": ["server/runtime.py"],
            }
        ),
        encoding="utf-8",
    )
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    trust = commit(repo, "trust")
    trust_repo = tmp_path / "trust"
    git(repo, "worktree", "add", "--detach", str(trust_repo), trust)
    return repo, trust_repo, trust


def validate(repo: Path, trust_repo: Path, trust: str) -> dict[str, object]:
    return bootstrap.validate_candidate(
        trust_repo=trust_repo,
        candidate_repo=repo,
        trust_ref=trust,
        candidate_ref="HEAD",
    )


def test_allows_only_post_trust_runtime_files(tmp_path: Path) -> None:
    repo, trust_repo, trust = make_repo(tmp_path)
    (repo / "server/runtime.py").write_text("VALUE = 2\n", encoding="utf-8")
    candidate = commit(repo, "runtime")

    result = validate(repo, trust_repo, trust)

    assert result["candidateCommit"] == candidate
    assert result["postTrustChangedFiles"] == ["server/runtime.py"]
    assert result["lockedFileCount"] == 2


def test_rejects_candidate_rewriting_trusted_configuration(tmp_path: Path) -> None:
    repo, trust_repo, trust = make_repo(tmp_path)
    source_lock = repo / "extensions/ai-research/source-lock.json"
    payload = json.loads(source_lock.read_text(encoding="utf-8"))
    payload["lockedFiles"] = {}
    source_lock.write_text(json.dumps(payload), encoding="utf-8")
    commit(repo, "rewrite trust")

    with pytest.raises(bootstrap.BootstrapFailure, match="trusted configuration"):
        validate(repo, trust_repo, trust)


def test_rejects_tracked_script_shadow_after_trust(tmp_path: Path) -> None:
    repo, trust_repo, trust = make_repo(tmp_path)
    shadow = repo / "extensions/ai-research/scripts/hashlib.py"
    shadow.write_text("raise RuntimeError('shadowed')\n", encoding="utf-8")
    commit(repo, "shadow")

    with pytest.raises(bootstrap.BootstrapFailure, match="untrusted importable"):
        validate(repo, trust_repo, trust)


def test_rejects_untracked_shadow_before_any_candidate_code_runs(tmp_path: Path) -> None:
    repo, trust_repo, trust = make_repo(tmp_path)
    (repo / "extensions/ai-research/scripts/json.py").write_text("", encoding="utf-8")

    with pytest.raises(bootstrap.BootstrapFailure, match="worktree must be clean"):
        validate(repo, trust_repo, trust)


def test_rejects_ignored_sourceless_shadow(tmp_path: Path) -> None:
    repo, trust_repo, trust = make_repo(tmp_path)
    shadow = repo / "extensions/ai-research/scripts/hashlib.pyc"
    shadow.write_bytes(b"ignored-shadow")
    assert not git(repo, "status", "--porcelain", "--untracked-files=all")

    with pytest.raises(bootstrap.BootstrapFailure, match="untrusted importable"):
        validate(repo, trust_repo, trust)


def test_requires_trust_commit_as_comparison_base(tmp_path: Path) -> None:
    repo, _trust_repo, trust = make_repo(tmp_path)
    (repo / "server/runtime.py").write_text("VALUE = 2\n", encoding="utf-8")
    candidate = commit(repo, "runtime")

    with pytest.raises(bootstrap.BootstrapFailure, match="base must equal"):
        bootstrap.require_trust_base(repo, candidate, trust)
    assert bootstrap.require_trust_base(repo, trust, trust) == trust


def test_discovers_windows_and_linux_diagnostics_names(tmp_path: Path) -> None:
    root = tmp_path / "extensions/ai-research/runtime/diagnostics"
    windows = root / "verify-012345"
    linux = root / "verify.abcdef"
    unrelated = root / "other"
    windows.mkdir(parents=True)
    linux.mkdir()
    unrelated.mkdir()

    assert bootstrap.diagnostics_directories(tmp_path) == {windows.resolve(), linux.resolve()}


def test_cli_requires_isolated_no_bytecode_python() -> None:
    rejected = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    accepted = subprocess.run(
        [sys.executable, "-I", "-B", str(SCRIPT), "--help"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert rejected.returncode == 2
    assert "Python -I -B" in rejected.stderr
    assert accepted.returncode == 0
