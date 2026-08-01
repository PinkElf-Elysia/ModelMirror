from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from server.coding_committer import engine as committer_engine
from server.coding_committer.engine import CodingCommitterEngine
from server.coding_runtime.apply_models import ApplyFileReceipt, ApplyReceipt
from server.coding_runtime.commit_models import CodingCommitError
from server.coding_runtime.patch_policy import snapshot_fingerprint


OPERATION_ID = "c" * 24


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, Path, Path, ApplyReceipt]:
    source = tmp_path / "source"
    target = tmp_path / "target"
    temporary = tmp_path / "temporary"
    (source / "server").mkdir(parents=True)
    (source / "docs").mkdir()
    (source / "server/app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "docs/guide.md").write_text("# Guide\n", encoding="utf-8")
    shutil.copytree(source, target)
    temporary.mkdir()
    git(target, "init", "-q")
    git(target, "config", "user.name", "Fixture")
    git(target, "config", "user.email", "fixture@example.test")
    git(target, "add", ".")
    git(target, "commit", "-qm", "baseline")
    git(target, "branch", "-M", "coding/local-draft")

    app = target / "server/app.py"
    before = sha256(app)
    app.write_text("VALUE = 2\n", encoding="utf-8")
    new_file = target / "docs/random-7F3A.md"
    new_file.write_text("nonce=7F3A-991\n", encoding="utf-8")
    receipt = ApplyReceipt(
        apply_id="a" * 24,
        revision=4,
        snapshot_fingerprint=snapshot_fingerprint(source),
        files=(
            ApplyFileReceipt(
                path="docs/random-7F3A.md",
                existed_before=False,
                before_sha256=None,
                after_sha256=sha256(new_file),
            ),
            ApplyFileReceipt(
                path="server/app.py",
                existed_before=True,
                before_sha256=before,
                after_sha256=sha256(app),
            ),
        ),
    )
    return source, target, temporary, receipt


def test_commit_is_atomic_idempotent_and_uses_fixed_identity(
    repository: tuple[Path, Path, Path, ApplyReceipt],
) -> None:
    source, target, temporary, apply_receipt = repository
    engine = CodingCommitterEngine(source, target, temporary)
    parent = git(target, "rev-parse", "HEAD")

    receipt = engine.commit(
        operation_id=OPERATION_ID,
        apply_receipt=apply_receipt,
        message="feature: 保存随机样例 7F3A",
    )

    assert engine.commit(
        operation_id=OPERATION_ID,
        apply_receipt=apply_receipt,
        message="feature: 保存随机样例 7F3A",
    ) == receipt
    assert receipt.parent_sha == parent
    assert git(target, "rev-parse", "HEAD") == receipt.commit_sha
    assert git(target, "status", "--porcelain") == ""
    assert git(target, "show", "-s", "--format=%s%n%an%n%ae", "HEAD").splitlines() == [
        "feature: 保存随机样例 7F3A",
        "ModelMirror Coding Assistant",
        "coding@modelmirror.local",
    ]
    assert git(target, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines() == [
        "docs/random-7F3A.md",
        "server/app.py",
    ]


def test_git_commands_pin_only_the_fixed_target_as_safe(
    repository: tuple[Path, Path, Path, ApplyReceipt],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, temporary, _ = repository
    calls: list[tuple[str, ...]] = []
    original_run = committer_engine.subprocess.run

    def tracked_run(argv: tuple[str, ...], **kwargs: object):
        calls.append(tuple(argv))
        return original_run(argv, **kwargs)

    monkeypatch.setattr(committer_engine.subprocess, "run", tracked_run)

    CodingCommitterEngine(source, target, temporary)

    assert calls
    assert all(f"safe.directory={target.resolve()}" in call for call in calls)


def test_undo_moves_head_back_but_keeps_applied_files_and_allows_recommit(
    repository: tuple[Path, Path, Path, ApplyReceipt],
) -> None:
    source, target, temporary, apply_receipt = repository
    engine = CodingCommitterEngine(source, target, temporary)
    receipt = engine.commit(
        operation_id=OPERATION_ID,
        apply_receipt=apply_receipt,
        message="feature: first",
    )

    assert engine.undo(receipt, apply_receipt) == receipt
    assert engine.undo(receipt, apply_receipt) == receipt
    assert git(target, "rev-parse", "HEAD") == receipt.parent_sha
    status = set(git(target, "status", "--porcelain").splitlines())
    assert status == {"?? docs/random-7F3A.md", "M server/app.py"}
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 2\n"

    second = engine.commit(
        operation_id="d" * 24,
        apply_receipt=apply_receipt,
        message="feature: second",
    )
    assert second.commit_sha != receipt.commit_sha
    assert git(target, "status", "--porcelain") == ""


def test_external_file_change_blocks_undo_without_overwriting_content(
    repository: tuple[Path, Path, Path, ApplyReceipt],
) -> None:
    source, target, temporary, apply_receipt = repository
    engine = CodingCommitterEngine(source, target, temporary)
    receipt = engine.commit(
        operation_id=OPERATION_ID,
        apply_receipt=apply_receipt,
        message="feature: protected",
    )
    (target / "server/app.py").write_text("VALUE = 99\n", encoding="utf-8")

    with pytest.raises(CodingCommitError) as raised:
        engine.undo(receipt, apply_receipt)

    assert raised.value.code == "target_changed"
    assert git(target, "rev-parse", "HEAD") == receipt.commit_sha
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 99\n"


def test_ref_update_failure_rolls_back_branch_and_index(
    repository: tuple[Path, Path, Path, ApplyReceipt],
) -> None:
    source, target, temporary, apply_receipt = repository
    baseline = git(target, "rev-parse", "HEAD")

    def fail_after_ref(phase: str) -> None:
        if phase == "commit_after_ref":
            raise OSError("fault 53B9")

    engine = CodingCommitterEngine(
        source,
        target,
        temporary,
        mutation_hook=fail_after_ref,
    )
    with pytest.raises(CodingCommitError) as raised:
        engine.commit(
            operation_id=OPERATION_ID,
            apply_receipt=apply_receipt,
            message="feature: fault",
        )

    assert raised.value.code == "commit_failed"
    assert git(target, "rev-parse", "HEAD") == baseline
    assert git(target, "diff", "--cached", "--name-only") == ""
    assert not (target / ".git/index.lock").exists()


def test_hooks_and_clean_filters_are_never_executed(
    repository: tuple[Path, Path, Path, ApplyReceipt],
) -> None:
    source, target, temporary, apply_receipt = repository
    hook_marker = temporary / "hook-ran"
    filter_marker = temporary / "filter-ran"
    (target / ".gitattributes").write_text("server/app.py filter=evil\n", encoding="utf-8")
    (source / ".gitattributes").write_text("server/app.py filter=evil\n", encoding="utf-8")
    git(target, "add", ".gitattributes")
    git(target, "commit", "-qm", "attributes")
    hooks = target / ".git/hooks"
    for name in ("pre-commit", "reference-transaction"):
        hook = hooks / name
        hook.write_text(f"#!/bin/sh\ntouch '{hook_marker}'\n", encoding="utf-8")
        hook.chmod(0o755)
    git(target, "config", "filter.evil.clean", f"touch '{filter_marker}'")
    git(target, "config", "filter.evil.required", "true")
    engine = CodingCommitterEngine(source, target, temporary)
    engine.commit(
        operation_id=OPERATION_ID,
        apply_receipt=ApplyReceipt(
            apply_id=apply_receipt.apply_id,
            revision=apply_receipt.revision,
            snapshot_fingerprint=snapshot_fingerprint(source),
            files=apply_receipt.files,
        ),
        message="feature: safe plumbing",
    )

    assert not hook_marker.exists()
    assert not filter_marker.exists()


@pytest.mark.parametrize(
    "case",
    ["remote", "alternates", "metadata_symlink", "branch", "index"],
)
def test_unsafe_repository_states_are_rejected(
    repository: tuple[Path, Path, Path, ApplyReceipt],
    case: str,
) -> None:
    source, target, temporary, _ = repository
    if case == "remote":
        git(target, "remote", "add", "origin", "https://example.invalid/repo.git")
    elif case == "alternates":
        alternates = target / ".git/objects/info/alternates"
        alternates.write_text("/tmp/shared\n", encoding="utf-8")
    elif case == "metadata_symlink":
        (target / ".git/modelmirror-link").symlink_to(temporary, target_is_directory=True)
    elif case == "branch":
        git(target, "switch", "-c", "wrong-branch")
    else:
        git(target, "add", "server/app.py")

    with pytest.raises(CodingCommitError) as raised:
        CodingCommitterEngine(source, target, temporary)

    assert raised.value.code in {
        "repository_has_remote",
        "shared_git_directory",
        "wrong_branch",
        "dirty_index",
    }


def test_git_worktree_pointer_and_baseline_mismatch_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    temporary = tmp_path / "temporary"
    source.mkdir()
    target.mkdir()
    temporary.mkdir()
    (source / "README.md").write_text("safe\n", encoding="utf-8")
    (target / "README.md").write_text("different\n", encoding="utf-8")
    (target / ".git").write_text("gitdir: ../shared\n", encoding="utf-8")

    with pytest.raises(CodingCommitError) as raised:
        CodingCommitterEngine(source, target, temporary)
    assert raised.value.code == "repository_not_independent"
