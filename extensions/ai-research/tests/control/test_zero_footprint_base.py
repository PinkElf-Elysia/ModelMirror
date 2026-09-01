from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest


MODULE_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = MODULE_ROOT / "scripts" / "zero_footprint.py"
if not SCRIPT.is_file():
    pytest.skip(
        "repository-level zero-footprint tests run in the host preflight",
        allow_module_level=True,
    )
SPEC = importlib.util.spec_from_file_location("zero_footprint_base", SCRIPT)
assert SPEC and SPEC.loader
zero_footprint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(zero_footprint)

BOUNDARY_SCRIPT = MODULE_ROOT / "scripts" / "validate_boundary.py"
BOUNDARY_SPEC = importlib.util.spec_from_file_location(
    "validate_boundary_delete_base", BOUNDARY_SCRIPT
)
assert BOUNDARY_SPEC and BOUNDARY_SPEC.loader
validate_boundary = importlib.util.module_from_spec(BOUNDARY_SPEC)
BOUNDARY_SPEC.loader.exec_module(validate_boundary)

WORKFLOW_PATH = zero_footprint.REPO_ROOT / ".github/workflows/ai-research.yml"
TRUST_PATHS = {
    "extensions/ai-research/source-lock.json",
    "extensions/ai-research/module-boundary.json",
}
DEFAULT_ALLOWED_PARENT = [
    "server/main.py",
    "server/model_router/ai_research_bridge.py",
    "server/model_router/chat_control.py",
    "server/model_router/chat_stable.py",
    "server/model_router/repository.py",
    "server/tests/test_ai_research_bridge.py",
    "server/tests/test_provider_chat_stable_service.py",
]
PROTECTED_PATHS = [
    ".github/workflows/ai-research.yml",
    "docker-compose.yml",
    "server/requirements.txt",
    "server/Dockerfile",
    "client/package.json",
    "client/package-lock.json",
    "extensions/ai-research/scripts/verify.ps1",
    "extensions/ai-research/scripts/verify.sh",
    "extensions/ai-research/scripts/zero_footprint.py",
    "extensions/ai-research/tests/control/test_zero_footprint_base.py",
]


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _write(repo: Path, relative: str, value: str) -> None:
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value, encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _init_gate_repo(
    tmp_path: Path, *, allowed_parent: list[str] | None = None
) -> tuple[Path, str]:
    repo = tmp_path / "gate-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "gate@example.test")
    _git(repo, "config", "user.name", "Gate Test")
    _git(repo, "config", "core.autocrlf", "false")
    _write(repo, "seed.txt", "locked source")
    locked = _commit(repo, "locked")

    source_lock = {
        "modelMirrorBaseCommit": locked,
        "coreBaseline": {
            "trackedFiles": {path: "locked-hash" for path in PROTECTED_PATHS}
        },
    }
    boundary = {
        "baseCommit": locked,
        "allowedParentFiles": allowed_parent or list(DEFAULT_ALLOWED_PARENT),
    }
    _write(
        repo,
        "extensions/ai-research/source-lock.json",
        json.dumps(source_lock),
    )
    _write(
        repo,
        "extensions/ai-research/module-boundary.json",
        json.dumps(boundary),
    )
    for path in PROTECTED_PATHS:
        _write(repo, path, f"protected:{path}")
    return repo, _commit(repo, "base")


def _workflow_gate_script() -> str:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    marker = '          python3 -I -S - "$comparison_base" <<\'PY\'\n'
    start = workflow.index(marker) + len(marker)
    end = workflow.index("\n          PY", start)
    return textwrap.dedent(workflow[start:end]) + "\n"


def _run_workflow_gate(
    repo: Path, base: str, *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-S", "-", base],
        cwd=repo,
        input=_workflow_gate_script(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def _verify_trust_fixture(
    tmp_path: Path, relative_script: str
) -> tuple[Path, str, Path]:
    repo, _ = _init_gate_repo(tmp_path)
    script = repo / relative_script
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_bytes((zero_footprint.REPO_ROOT / relative_script).read_bytes())
    base = _commit(repo, "add verifier")

    trust_path = repo / "extensions/ai-research/module-boundary.json"
    _write(repo, "extensions/ai-research/module-boundary.json", "{}")
    _commit(repo, "commit malicious trust change")
    trusted = _git(
        repo,
        "show",
        f"{base}:extensions/ai-research/module-boundary.json",
    ).stdout
    trust_path.write_text(trusted, encoding="utf-8")
    return repo, base, script


def _dist(root: Path, value: str = "same") -> dict[str, object]:
    root.mkdir(parents=True)
    (root / "index.html").write_text(value, encoding="utf-8")
    return zero_footprint.client_dist(root)


def _source_lock(
    locked_commit: str, source_proof: dict[str, object]
) -> dict[str, object]:
    return {
        "modelMirrorBaseCommit": locked_commit,
        "coreBaseline": {
            "clientDistGate": {"baseCommit": locked_commit},
            "clientDistReference": source_proof,
            "trackedFiles": {
                "docker-compose.yml": zero_footprint.sha256_bytes(b"locked compose"),
                "server/requirements.txt": zero_footprint.sha256_bytes(b"locked requirements"),
            },
        },
    }


def test_locked_source_evidence_is_read_from_locked_commit_not_current_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    locked = "1" * 40
    source_proof = _dist(tmp_path / "source")
    source_lock = _source_lock(locked, source_proof)
    blobs = {
        "docker-compose.yml": b"locked compose",
        "server/requirements.txt": b"locked requirements",
    }
    calls: list[tuple[str, str]] = []

    def fake_blob(commit: str, relative: str) -> bytes:
        calls.append((commit, relative))
        return blobs[relative]

    monkeypatch.setattr(zero_footprint, "git_blob", fake_blob)

    zero_footprint.validate_locked_source(source_lock, locked, source_proof)

    assert calls == [
        (locked, "docker-compose.yml"),
        (locked, "server/requirements.txt"),
    ]


def test_locked_source_hash_drift_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    locked = "1" * 40
    source_proof = _dist(tmp_path / "source")
    source_lock = _source_lock(locked, source_proof)
    monkeypatch.setattr(zero_footprint, "git_blob", lambda *_: b"tampered")

    with pytest.raises(zero_footprint.BaselineFailure, match="locked source hash drifted"):
        zero_footprint.validate_locked_source(source_lock, locked, source_proof)


def test_locked_source_client_proof_drift_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    locked = "1" * 40
    source_lock = _source_lock(locked, _dist(tmp_path / "expected", "expected"))

    monkeypatch.setattr(
        zero_footprint,
        "git_blob",
        lambda commit, relative: {
            "docker-compose.yml": b"locked compose",
            "server/requirements.txt": b"locked requirements",
        }[relative],
    )
    with pytest.raises(
        zero_footprint.BaselineFailure, match="locked source client proof drifted"
    ):
        zero_footprint.validate_locked_source(
            source_lock,
            locked,
            _dist(tmp_path / "actual", "actual"),
        )


def test_current_batch_allows_module_and_declared_parent_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        zero_footprint,
        "changed_paths",
        lambda *_: {
            "extensions/ai-research/scripts/zero_footprint.py",
            ".github/workflows/ai-research.yml",
            "server/main.py",
        },
    )

    changed = zero_footprint.validate_current_scope(
        "2" * 40,
        "3" * 40,
        {
            "allowedParentFiles": [
                ".github/workflows/ai-research.yml",
                "server/main.py",
            ]
        },
    )

    assert changed == [
        ".github/workflows/ai-research.yml",
        "extensions/ai-research/scripts/zero_footprint.py",
        "server/main.py",
    ]


def test_current_batch_rejects_unapproved_parent_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        zero_footprint,
        "changed_paths",
        lambda *_: {"extensions/ai-research/README.md", "server/unapproved.py"},
    )

    with pytest.raises(
        zero_footprint.BaselineFailure, match="forbidden current-batch paths changed"
    ):
        zero_footprint.validate_current_scope(
            "2" * 40, "3" * 40, {"allowedParentFiles": []}
        )


@pytest.mark.parametrize(
    "path",
    ["client/src/App.tsx", ".github/workflows/quality.yml"],
)
def test_current_batch_rejects_unapproved_client_and_workflow_paths(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setattr(zero_footprint, "changed_paths", lambda *_: {path})

    with pytest.raises(zero_footprint.BaselineFailure, match="forbidden current-batch"):
        zero_footprint.validate_current_scope(
            "2" * 40, "3" * 40, {"allowedParentFiles": []}
        )


def test_deleted_unapproved_parent_file_is_still_in_current_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_git_paths(*args: str) -> set[str]:
        calls.append(args)
        return {"server/deleted_unapproved.py"}

    monkeypatch.setattr(zero_footprint, "git_paths", fake_git_paths)

    with pytest.raises(
        zero_footprint.BaselineFailure, match="deleted_unapproved.py"
    ):
        zero_footprint.validate_current_scope(
            "2" * 40, "3" * 40, {"allowedParentFiles": []}
        )

    assert "--diff-filter=ACDMRTUXB" in calls[0]


def test_boundary_changed_paths_includes_deletions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_git_paths(*args: str) -> list[str]:
        calls.append(args)
        return ["server/deleted_unapproved.py"] if args[0] == "diff" else []

    monkeypatch.setattr(validate_boundary, "git_paths", fake_git_paths)

    assert validate_boundary.changed_paths("base") == {"server/deleted_unapproved.py"}
    assert "--diff-filter=ACDMRTUXB" in calls[0]
    assert calls[0][-3:] == ("base", "HEAD", "--")
    assert calls[1][-3:] == ("--cached", "HEAD", "--")
    assert calls[2][-1:] == ("--",)


def test_boundary_changed_paths_keeps_committed_violation_when_workspace_restores_base(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo, base = _init_gate_repo(tmp_path)
    relative = "server/unapproved.py"
    _write(repo, relative, "committed violation")
    _commit(repo, "commit forbidden path")
    (repo / relative).unlink()

    monkeypatch.setattr(validate_boundary, "REPO_ROOT", repo)
    monkeypatch.setattr(
        validate_boundary,
        "MODULE_ROOT",
        Path(__file__).parent / "missing-module-root",
    )

    assert relative in validate_boundary.changed_paths(base)
    with pytest.raises(
        validate_boundary.BoundaryFailure, match="outside the approved boundary"
    ):
        validate_boundary.validate_paths(base, {"allowedParentFiles": []})


@pytest.mark.parametrize(
    "layer",
    [
        "base_to_head",
        "head_to_index",
        "index_to_workspace",
        "untracked",
        "index_workspace_cancellation",
    ],
)
def test_boundary_rejects_forbidden_paths_from_each_repository_layer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, layer: str
) -> None:
    repo, _ = _init_gate_repo(tmp_path)
    tracked_relative = "server/unapproved-layer.py"
    _write(repo, tracked_relative, "base value")
    base = _commit(repo, "add pre-existing parent path")
    relative = tracked_relative

    if layer == "base_to_head":
        _write(repo, relative, "committed violation")
        _commit(repo, "commit forbidden change")
    elif layer == "head_to_index":
        _write(repo, relative, "staged violation")
        _git(repo, "add", relative)
    elif layer == "index_to_workspace":
        _write(repo, relative, "worktree violation")
    elif layer == "untracked":
        relative = "server/untracked-violation.py"
        _write(repo, relative, "untracked violation")
    else:
        _write(repo, relative, "staged violation")
        _git(repo, "add", relative)
        _write(repo, relative, "base value")

    monkeypatch.setattr(validate_boundary, "REPO_ROOT", repo)
    monkeypatch.setattr(
        validate_boundary,
        "MODULE_ROOT",
        repo / "extensions/ai-research",
    )

    assert relative in validate_boundary.changed_paths(base)
    with pytest.raises(
        validate_boundary.BoundaryFailure, match="outside the approved boundary"
    ):
        validate_boundary.validate_paths(base, {"allowedParentFiles": []})


def test_bash_verifier_keeps_committed_trust_violation_when_workspace_restores_base(
    tmp_path: Path,
) -> None:
    bash = shutil.which("bash")
    if not bash or sys.platform == "win32":
        pytest.skip("Bash verifier behavior is exercised on the Linux control runner")
    repo, base, script = _verify_trust_fixture(
        tmp_path, "extensions/ai-research/scripts/verify.sh"
    )

    result = subprocess.run(
        [bash, str(script), base, "quick", "external-pull"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 2
    assert "trust configuration changed in the candidate" in result.stderr


def test_powershell_verifier_keeps_committed_trust_violation_when_workspace_restores_base(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell verifier behavior requires PowerShell")
    repo, base, script = _verify_trust_fixture(
        tmp_path, "extensions/ai-research/scripts/verify.ps1"
    )

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Base",
            base,
            "-Mode",
            "Quick",
            "-DistributionMode",
            "ExternalPull",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode != 0
    assert "trust configuration changed in the candidate" in result.stderr


@pytest.mark.parametrize(
    "relative_script",
    [
        "extensions/ai-research/scripts/verify.sh",
        "extensions/ai-research/scripts/verify.ps1",
    ],
    ids=["bash", "powershell"],
)
def test_verifier_keeps_staged_trust_violation_when_worktree_restores_head(
    tmp_path: Path, relative_script: str
) -> None:
    if relative_script.endswith(".sh"):
        executable = shutil.which("bash")
        if not executable or sys.platform == "win32":
            pytest.skip("Bash verifier behavior is exercised on the Linux control runner")
    else:
        executable = shutil.which("powershell") or shutil.which("pwsh")
        if not executable:
            pytest.skip("PowerShell verifier behavior requires PowerShell")

    repo, _ = _init_gate_repo(tmp_path)
    script = repo / relative_script
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_bytes((zero_footprint.REPO_ROOT / relative_script).read_bytes())
    base = _commit(repo, "add verifier")
    trust_path = repo / "extensions/ai-research/module-boundary.json"
    trusted = trust_path.read_text(encoding="utf-8")
    trust_path.write_text("{}", encoding="utf-8")
    _git(repo, "add", "extensions/ai-research/module-boundary.json")
    trust_path.write_text(trusted, encoding="utf-8")

    if relative_script.endswith(".sh"):
        command = [
            executable,
            str(script),
            base,
            "quick",
            "external-pull",
        ]
    else:
        command = [
            executable,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Base",
            base,
            "-Mode",
            "Quick",
            "-DistributionMode",
            "ExternalPull",
        ]
    result = subprocess.run(
        command,
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode != 0
    assert "trust configuration changed in the workspace index" in result.stderr


@pytest.mark.parametrize(
    "module,error_type",
    [
        (zero_footprint, zero_footprint.BaselineFailure),
        (validate_boundary, validate_boundary.BoundaryFailure),
    ],
)
def test_git_path_parser_rejects_backslash_names(
    monkeypatch: pytest.MonkeyPatch, module: object, error_type: type[Exception]
) -> None:
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=b"server\\main.py\0"),
    )

    with pytest.raises(error_type, match="unsafe backslash"):
        module.git_paths("diff")


@pytest.mark.parametrize("module", [zero_footprint, validate_boundary])
def test_git_path_parser_keeps_newline_filename_as_one_path(
    monkeypatch: pytest.MonkeyPatch, module: object
) -> None:
    raw = b"server/main.py\nserver/model_router/ai_research_bridge.py\0"
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=raw),
    )

    assert list(module.git_paths("diff")) == [raw[:-1].decode("utf-8")]


@pytest.mark.parametrize(
    "module,error_type",
    [
        (zero_footprint, zero_footprint.BaselineFailure),
        (validate_boundary, validate_boundary.BoundaryFailure),
    ],
)
def test_git_path_parser_rejects_non_utf8_names(
    monkeypatch: pytest.MonkeyPatch, module: object, error_type: type[Exception]
) -> None:
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=b"unsafe-\xff\0"),
    )

    with pytest.raises(error_type, match="not valid UTF-8"):
        module.git_paths("diff")


def test_trusted_configuration_is_loaded_only_from_caller_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = "2" * 40
    trusted = {
        zero_footprint.TRUST_FILES[0]: json.dumps(
            {"modelMirrorBaseCommit": "1" * 40}
        ).encode(),
        zero_footprint.TRUST_FILES[1]: json.dumps(
            {"allowedParentFiles": ["server/main.py"]}
        ).encode(),
    }
    calls: list[tuple[str, str]] = []

    def fake_blob(commit: str, relative: str) -> bytes:
        calls.append((commit, relative))
        return trusted[relative]

    monkeypatch.setattr(zero_footprint, "git_blob", fake_blob)

    source_lock, boundary = zero_footprint.load_trusted_configuration(base)

    assert source_lock["modelMirrorBaseCommit"] == "1" * 40
    assert boundary["allowedParentFiles"] == ["server/main.py"]
    assert calls == [(base, path) for path in zero_footprint.TRUST_FILES]


def test_malformed_trusted_configuration_fails_without_assertions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    locked = "1" * 40
    source_proof = _dist(tmp_path / "source")
    monkeypatch.setattr(zero_footprint, "git_blob", lambda *_: b"unused")

    with pytest.raises(zero_footprint.BaselineFailure, match="coreBaseline"):
        zero_footprint.validate_locked_source(
            {"modelMirrorBaseCommit": locked, "coreBaseline": []},
            locked,
            source_proof,
        )
    with pytest.raises(zero_footprint.BaselineFailure, match="allowedParentFiles"):
        zero_footprint.validate_current_scope(
            "2" * 40,
            "3" * 40,
            {"allowedParentFiles": "server/unapproved.py"},
        )


@pytest.mark.parametrize(
    "relative,candidate",
    [
        (
            "extensions/ai-research/source-lock.json",
            b'{"modelMirrorBaseCommit":"9999999999999999999999999999999999999999"}',
        ),
        (
            "extensions/ai-research/source-lock.json",
            b'{"coreBaseline":{"trackedFiles":{}}}',
        ),
        (
            "extensions/ai-research/module-boundary.json",
            b'{"allowedParentFiles":["server/unapproved.py"]}',
        ),
    ],
)
def test_candidate_cannot_self_authorize_by_changing_trust_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relative: str,
    candidate: bytes,
) -> None:
    base = "2" * 40
    head = "3" * 40
    trusted = b"trusted"
    repo_root = tmp_path / "repo"
    for path in zero_footprint.TRUST_FILES:
        target = repo_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(candidate if path == relative else trusted)

    def fake_blob(commit: str, path: str) -> bytes:
        if commit == head and path == relative:
            return candidate
        return trusted

    monkeypatch.setattr(zero_footprint, "REPO_ROOT", repo_root)
    monkeypatch.setattr(zero_footprint, "git_blob", fake_blob)

    with pytest.raises(
        zero_footprint.BaselineFailure, match="trusted configuration changed"
    ):
        zero_footprint.validate_trust_files_unchanged(base, head)


def test_deleted_trust_file_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base = "2" * 40
    head = "3" * 40
    repo_root = tmp_path / "repo"
    for relative in zero_footprint.TRUST_FILES:
        target = repo_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"trusted")

    def fake_blob(commit: str, relative: str) -> bytes:
        if commit == head and relative == zero_footprint.TRUST_FILES[0]:
            raise subprocess.CalledProcessError(128, ["git", "show"])
        return b"trusted"

    monkeypatch.setattr(zero_footprint, "REPO_ROOT", repo_root)
    monkeypatch.setattr(zero_footprint, "git_blob", fake_blob)

    with pytest.raises(zero_footprint.BaselineFailure, match="trusted configuration is missing"):
        zero_footprint.validate_trust_files_unchanged(base, head)


def test_dirty_worktree_cannot_override_trusted_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base = "2" * 40
    head = "3" * 40
    repo_root = tmp_path / "repo"
    for relative in zero_footprint.TRUST_FILES:
        target = repo_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"trusted")
    (repo_root / zero_footprint.TRUST_FILES[1]).write_bytes(b"dirty expanded whitelist")
    monkeypatch.setattr(zero_footprint, "REPO_ROOT", repo_root)
    monkeypatch.setattr(zero_footprint, "git_blob", lambda *_: b"trusted")

    with pytest.raises(
        zero_footprint.BaselineFailure, match="trusted configuration changed"
    ):
        zero_footprint.validate_trust_files_unchanged(base, head)


def test_protected_files_compare_caller_base_to_head_not_locked_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    locked = "1" * 40
    base = "2" * 40
    head = "3" * 40
    source_lock = _source_lock(locked, _dist(tmp_path / "source"))
    current = {
        "docker-compose.yml": b"legitimately advanced compose",
        "server/requirements.txt": b"legitimately advanced requirements",
    }
    monkeypatch.setattr(
        zero_footprint,
        "git_blob",
        lambda commit, relative: current[relative] if commit in {base, head} else b"locked",
    )

    zero_footprint.validate_protected_batch_files(source_lock, base, head)


def test_protected_file_changed_in_current_batch_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    locked = "1" * 40
    base = "2" * 40
    head = "3" * 40
    source_lock = _source_lock(locked, _dist(tmp_path / "source"))

    def fake_blob(commit: str, relative: str) -> bytes:
        if relative == "docker-compose.yml" and commit == head:
            return b"changed"
        return b"same"

    monkeypatch.setattr(zero_footprint, "git_blob", fake_blob)

    with pytest.raises(
        zero_footprint.BaselineFailure, match="protected core file changed"
    ):
        zero_footprint.validate_protected_batch_files(source_lock, base, head)


def test_lineage_requires_locked_to_base_and_base_to_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    locked = "1" * 40
    base = "2" * 40
    head = "3" * 40
    commits = {locked: locked, "requested": base, "HEAD": head}
    ancestry: list[tuple[str, str, str]] = []
    monkeypatch.setattr(zero_footprint, "resolve_commit", lambda ref: commits[ref])
    monkeypatch.setattr(
        zero_footprint,
        "require_ancestor",
        lambda ancestor, descendant, message: ancestry.append(
            (ancestor, descendant, message)
        ),
    )

    assert zero_footprint.validate_lineage(locked, "requested") == (locked, base, head)
    assert [(ancestor, descendant) for ancestor, descendant, _ in ancestry] == [
        (locked, base),
        (base, head),
    ]


def test_non_ancestor_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        zero_footprint,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )

    with pytest.raises(zero_footprint.BaselineFailure, match="not an ancestor"):
        zero_footprint.require_ancestor("1" * 40, "2" * 40, "not an ancestor")


@pytest.mark.parametrize(
    "argv",
    [
        ["--baseline-client-dist", "base", "--client-dist", "head", "--base", "base"],
        ["--source-client-dist", "source", "--client-dist", "head", "--base", "base"],
        [
            "--source-client-dist",
            "source",
            "--baseline-client-dist",
            "base",
            "--base",
            "base",
        ],
        [
            "--source-client-dist",
            "source",
            "--baseline-client-dist",
            "base",
            "--client-dist",
            "head",
        ],
    ],
)
def test_all_three_proofs_and_base_are_required(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        zero_footprint.main(argv)

    assert exc_info.value.code == 2


def test_all_zero_base_is_rejected() -> None:
    with pytest.raises(zero_footprint.BaselineFailure, match="all-zero"):
        zero_footprint.resolve_commit("0" * 40)


def test_main_receipt_records_locked_base_head_and_three_proofs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    locked = "1" * 40
    base = "2" * 40
    head = "3" * 40
    source_proof_path = tmp_path / "source-proof"
    base_proof_path = tmp_path / "base-proof"
    head_proof_path = tmp_path / "head-proof"
    source_proof = _dist(source_proof_path, "locked")
    base_proof = _dist(base_proof_path, "current")
    _dist(head_proof_path, "current")
    source_lock = _source_lock(locked, source_proof)
    boundary = {"baseCommit": locked, "allowedParentFiles": []}
    commits = {base: base, "HEAD": head}
    monkeypatch.setattr(zero_footprint, "resolve_commit", lambda ref: commits[ref])
    monkeypatch.setattr(
        zero_footprint,
        "load_trusted_configuration",
        lambda *_: (source_lock, boundary),
    )
    monkeypatch.setattr(zero_footprint, "validate_trust_files_unchanged", lambda *_: None)
    monkeypatch.setattr(
        zero_footprint, "validate_lineage", lambda *_: (locked, base, head)
    )
    monkeypatch.setattr(zero_footprint, "validate_locked_source", lambda *_: None)
    monkeypatch.setattr(zero_footprint, "validate_current_scope", lambda *_: ["safe"])
    monkeypatch.setattr(zero_footprint, "validate_protected_batch_files", lambda *_: None)
    monkeypatch.setattr(
        zero_footprint, "render_current_compose", lambda: (["server"], ["data"])
    )

    assert (
        zero_footprint.main(
            [
                "--source-client-dist",
                str(source_proof_path),
                "--baseline-client-dist",
                str(base_proof_path),
                "--client-dist",
                str(head_proof_path),
                "--base",
                base,
            ]
        )
        == 0
    )

    receipt = json.loads(capsys.readouterr().out)
    assert receipt["lockedSourceCommit"] == locked
    assert receipt["baseCommit"] == base
    assert receipt["headCommit"] == head
    assert receipt["sourceClientDist"] == source_proof
    assert receipt["baselineClientDist"] == base_proof
    assert receipt["clientDist"] == base_proof


def test_base_and_head_client_proofs_must_match(tmp_path: Path) -> None:
    base = _dist(tmp_path / "base-proof", "before")
    head = _dist(tmp_path / "head-proof", "after")

    with pytest.raises(zero_footprint.BaselineFailure, match="root client changed"):
        zero_footprint.validate_current_client_proof(base, head)


def test_current_compose_only_requires_successful_current_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        zero_footprint,
        "command",
        lambda *_: json.dumps(
            {"services": {"server": {}, "client": {}}, "volumes": {"data": {}}}
        ),
    )

    services, volumes = zero_footprint.render_current_compose()

    assert services == ["client", "server"]
    assert volumes == ["data"]


def test_current_compose_render_failure_is_not_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = subprocess.CalledProcessError(1, ["docker", "compose", "config"])
    monkeypatch.setattr(
        zero_footprint,
        "command",
        lambda *_: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(subprocess.CalledProcessError):
        zero_footprint.render_current_compose()


@pytest.mark.parametrize(
    "relative",
    ["extensions/ai-research/new.py", *DEFAULT_ALLOWED_PARENT],
)
def test_workflow_early_gate_accepts_module_and_base_allowlist(
    tmp_path: Path, relative: str
) -> None:
    repo, base = _init_gate_repo(tmp_path)
    _write(repo, relative, f"allowed:{relative}")
    _commit(repo, "allowed candidate")

    result = _run_workflow_gate(repo, base)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "relative",
    [
        ".dockerignore",
        "server/unapproved.py",
        "client/src/unapproved.ts",
        "deploy/unapproved.yml",
        "new-api-data/unapproved.json",
        "unexpected-root.txt",
    ],
)
def test_workflow_early_gate_rejects_unapproved_paths(
    tmp_path: Path, relative: str
) -> None:
    repo, base = _init_gate_repo(tmp_path)
    _write(repo, relative, "forbidden")
    _commit(repo, "forbidden candidate")

    result = _run_workflow_gate(repo, base)

    assert result.returncode != 0
    assert "forbidden current-batch paths changed" in result.stderr
    assert relative in result.stderr


@pytest.mark.parametrize("relative", PROTECTED_PATHS)
def test_workflow_early_gate_rejects_protected_files_even_if_base_allowlists_them(
    tmp_path: Path, relative: str
) -> None:
    repo, base = _init_gate_repo(
        tmp_path, allowed_parent=[*DEFAULT_ALLOWED_PARENT, relative]
    )
    _write(repo, relative, "tampered protected file")
    _commit(repo, "protected candidate")

    result = _run_workflow_gate(repo, base)

    assert result.returncode != 0
    assert "protected core files changed" in result.stderr
    assert relative in result.stderr


@pytest.mark.parametrize("operation", ["delete", "rename"])
@pytest.mark.parametrize("relative", PROTECTED_PATHS)
def test_workflow_early_gate_rejects_protected_delete_and_rename(
    tmp_path: Path, relative: str, operation: str
) -> None:
    repo, base = _init_gate_repo(
        tmp_path, allowed_parent=[*DEFAULT_ALLOWED_PARENT, relative]
    )
    if operation == "delete":
        (repo / relative).unlink()
    else:
        _git(repo, "mv", relative, "server/main.py")
    _commit(repo, f"protected {operation}")

    result = _run_workflow_gate(repo, base)

    assert result.returncode != 0
    assert "protected core files changed" in result.stderr
    assert relative in result.stderr


@pytest.mark.parametrize("relative", sorted(TRUST_PATHS))
def test_workflow_early_gate_rejects_trust_file_self_authorization(
    tmp_path: Path, relative: str
) -> None:
    repo, base = _init_gate_repo(tmp_path)
    _write(repo, relative, "{}")
    _commit(repo, "trust self authorization")

    result = _run_workflow_gate(repo, base)

    assert result.returncode != 0
    assert "AI Research trust configuration changed" in result.stderr
    assert relative in result.stderr


@pytest.mark.parametrize(
    "source,target",
    [
        ("server/unapproved.py", "server/main.py"),
        ("server/main.py", "server/unapproved.py"),
    ],
)
def test_workflow_early_gate_expands_renames_and_rejects_forbidden_endpoint(
    tmp_path: Path, source: str, target: str
) -> None:
    repo, base = _init_gate_repo(tmp_path)
    _write(repo, source, "rename source")
    base = _commit(repo, "add rename source")
    target_path = repo / target
    target_path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "mv", source, target)
    _commit(repo, "rename candidate")

    result = _run_workflow_gate(repo, base)

    assert result.returncode != 0
    assert "server/unapproved.py" in result.stderr


def test_workflow_early_gate_rejects_deleted_forbidden_path(tmp_path: Path) -> None:
    repo, base = _init_gate_repo(tmp_path)
    _write(repo, "server/unapproved.py", "delete me")
    base = _commit(repo, "add forbidden path to base")
    (repo / "server/unapproved.py").unlink()
    _commit(repo, "delete forbidden path")

    result = _run_workflow_gate(repo, base)

    assert result.returncode != 0
    assert "server/unapproved.py" in result.stderr


@pytest.mark.parametrize(
    "relative,value,message",
    [
        (
            "extensions/ai-research/module-boundary.json",
            {"baseCommit": "mismatch", "allowedParentFiles": []},
            "module boundary and source lock disagree",
        ),
        (
            "extensions/ai-research/module-boundary.json",
            {"baseCommit": None, "allowedParentFiles": "server/main.py"},
            "allowedParentFiles is invalid",
        ),
        (
            "extensions/ai-research/source-lock.json",
            {"modelMirrorBaseCommit": "invalid", "coreBaseline": {}},
            "source-lock commit is invalid",
        ),
    ],
)
def test_workflow_early_gate_rejects_malformed_base_trust(
    tmp_path: Path, relative: str, value: dict[str, object], message: str
) -> None:
    repo, _ = _init_gate_repo(tmp_path)
    if relative.endswith("module-boundary.json") and value.get("baseCommit") is None:
        source_lock = json.loads(
            (repo / "extensions/ai-research/source-lock.json").read_text(encoding="utf-8")
        )
        value["baseCommit"] = source_lock["modelMirrorBaseCommit"]
    _write(repo, relative, json.dumps(value))
    base = _commit(repo, "malformed trusted base")
    _write(repo, "extensions/ai-research/candidate.txt", "trigger")
    _commit(repo, "candidate")

    result = _run_workflow_gate(repo, base)

    assert result.returncode != 0
    assert message in result.stderr


@pytest.mark.parametrize("unsafe", ["../server/main.py", "/server/main.py", "C:/server/main.py"])
def test_workflow_early_gate_rejects_unsafe_base_allowlist_paths(
    tmp_path: Path, unsafe: str
) -> None:
    repo, _ = _init_gate_repo(tmp_path)
    boundary_path = repo / "extensions/ai-research/module-boundary.json"
    boundary = json.loads(boundary_path.read_text(encoding="utf-8"))
    boundary["allowedParentFiles"] = [unsafe]
    boundary_path.write_text(json.dumps(boundary), encoding="utf-8")
    base = _commit(repo, "unsafe trusted base")
    _write(repo, "extensions/ai-research/candidate.txt", "trigger")
    _commit(repo, "candidate")

    result = _run_workflow_gate(repo, base)

    assert result.returncode != 0
    assert "allowedParentFiles contains an unsafe path" in result.stderr


@pytest.mark.parametrize(
    "path",
    ["extensions/ai-research/space name.txt", "extensions/ai-research/中文.txt"],
)
def test_workflow_early_gate_handles_nul_delimited_special_paths(
    tmp_path: Path, path: str
) -> None:
    repo, base = _init_gate_repo(tmp_path)
    _write(repo, path, "allowed special path")
    _commit(repo, "special path")

    result = _run_workflow_gate(repo, base)

    assert result.returncode == 0, result.stderr


def test_workflow_early_gate_rejects_raw_backslash_git_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    locked = "1" * 40

    def fake_run(args: list[str], **_: object) -> SimpleNamespace:
        command = args[1]
        if command == "show":
            relative = args[2].split(":", 1)[1]
            value = (
                {
                    "modelMirrorBaseCommit": locked,
                    "coreBaseline": {
                        "trackedFiles": {"server/requirements.txt": "hash"}
                    },
                }
                if relative.endswith("source-lock.json")
                else {
                    "baseCommit": locked,
                    "allowedParentFiles": ["server/main.py"],
                }
            )
            return SimpleNamespace(stdout=json.dumps(value).encode(), returncode=0)
        if command == "merge-base":
            return SimpleNamespace(stdout=b"", returncode=0)
        if command == "diff":
            return SimpleNamespace(stdout=b"server\\main.py\0", returncode=0)
        raise AssertionError(f"unexpected Git command: {args}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["workflow-gate", "2" * 40])

    with pytest.raises(SystemExit, match="current-batch paths contain an unsafe backslash"):
        exec(compile(_workflow_gate_script(), "<workflow-gate>", "exec"), {})


def test_workflow_selects_event_base_and_never_coalesces_push_runs() -> None:
    workflow = (zero_footprint.REPO_ROOT / ".github/workflows/ai-research.yml").read_text(
        encoding="utf-8"
    )

    assert "github.event.pull_request.base.sha" in workflow
    assert "github.event.before" in workflow
    assert "github.event.pull_request.number || github.sha" in workflow
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in workflow
    assert '      - "server/main.py"' in workflow
    assert '      - "server/model_router/chat_control.py"' in workflow
    assert '      - "server/model_router/repository.py"' in workflow
    assert '      - "server/tests/test_ai_research_bridge.py"' in workflow
    assert "extensions/ai-research/source-lock.json" in workflow
    assert "extensions/ai-research/module-boundary.json" in workflow
    scope_gate = workflow.index("Resolve comparison base and enforce trusted scope")
    assert scope_gate < workflow.index("Set up Docker 29.7.2")
    assert scope_gate < workflow.index("Set up Python 3.12")
    assert scope_gate < workflow.index("Install core server test dependencies")
    assert scope_gate < workflow.index("Verify restricted model bridge")
    assert scope_gate < workflow.index("Verify V0.1 development candidate")
    assert "allowedParentFiles" in workflow
    assert "trackedFiles" in workflow
    assert 'python3 -I -S - "$comparison_base"' in workflow
    assert "--no-renames" in workflow
    assert "--diff-filter=ACDMRTUXB" in workflow
    assert "current-batch paths contain an unsafe backslash" in workflow


@pytest.mark.parametrize(
    "relative",
    ["extensions/ai-research/scripts/verify.sh", "extensions/ai-research/scripts/verify.ps1"],
)
def test_verify_scripts_build_three_proofs_and_pass_full_zero_footprint_interface(
    relative: str,
) -> None:
    script = (zero_footprint.REPO_ROOT / relative).read_text(encoding="utf-8")

    assert "source-client-dist" in script
    assert "baseline-client-dist" in script
    assert '"--client-dist"' in script or "--client-dist" in script
    assert '"--base"' in script or "--base" in script
    assert "client-source" in script
    assert "client-baseline" in script
    assert "client-current" in script
    assert "source-lock.json" in script
    assert "module-boundary.json" in script
    assert "diff --quiet --no-ext-diff" in script
    if relative.endswith(".sh"):
        assert 'diff --quiet --no-ext-diff "$BASE" HEAD --' in script
        assert 'diff --quiet --no-ext-diff --cached HEAD --' in script
        assert 'diff --quiet --no-ext-diff -- "${TRUST_FILES[@]}"' in script
    else:
        assert "diff --quiet --no-ext-diff $comparisonBase HEAD --" in script
        assert "diff --quiet --no-ext-diff --cached HEAD --" in script
        assert "diff --quiet --no-ext-diff -- @trustFiles" in script
