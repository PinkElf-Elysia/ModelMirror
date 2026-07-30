from __future__ import annotations

import os
from pathlib import Path

import pytest

from server.coding_runtime.draft_workspace import (
    DraftLimits,
    DraftPolicyError,
    DraftRevisionError,
    DraftTransactionError,
    DraftValidationError,
    DraftWorkspace,
)


def _workspace(
    tmp_path: Path,
    files: dict[str, str | bytes] | None = None,
    *,
    limits: DraftLimits | None = None,
) -> DraftWorkspace:
    source = tmp_path / "source"
    source.mkdir()
    for relative, content in (files or {}).items():
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    workspace = DraftWorkspace(
        source,
        tmp_path / "workspace",
        tmp_path / "checkpoint",
        limits=limits,
    )
    workspace.initialize()
    return workspace


def _write(workspace: DraftWorkspace, relative: str, content: str | bytes) -> None:
    target = workspace.workspace_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        target.write_bytes(content)
    else:
        target.write_text(content, encoding="utf-8")


def test_new_and_modified_text_files_produce_stable_revision_and_diff(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path, {"app.py": "answer = 1\n"})

    assert workspace.changes().revision == 0
    assert workspace.changes().files == ()

    workspace.begin_turn()
    _write(workspace, "app.py", "answer = 2\n")
    _write(workspace, "notes/readme.txt", "clear summary\n")
    report = workspace.commit_turn()

    assert report.revision == 1
    assert [(item.path, item.status) for item in report.files] == [
        ("app.py", "modified"),
        ("notes/readme.txt", "added"),
    ]
    assert (report.additions, report.deletions) == (2, 1)
    assert "--- a/app.py" in workspace.diff_for("app.py", 1)
    assert "+++ b/notes/readme.txt" in workspace.diff_for("notes/readme.txt", 1)
    assert "--- /dev/null" in workspace.diff_for("notes/readme.txt", 1)
    assert workspace.changes().revision == 1
    assert workspace.validate().revision == 1
    assert workspace.patch(1).startswith("diff --git a/app.py b/app.py")


def test_valid_changes_accumulate_across_turns_and_rollback_preserves_them(
    tmp_path: Path,
) -> None:
    workspace = _workspace(
        tmp_path,
        {"first.txt": "before\n", "unchanged.txt": "baseline\n"},
    )

    workspace.begin_turn()
    _write(workspace, "first.txt", "accepted\n")
    assert workspace.commit_turn().revision == 1

    workspace.begin_turn()
    assert (workspace.checkpoint_root / "first.txt").exists()
    assert not (workspace.checkpoint_root / "unchanged.txt").exists()
    _write(workspace, "second.txt", "cancelled\n")
    rolled_back = workspace.rollback_turn()

    assert rolled_back.revision == 1
    assert [item.path for item in rolled_back.files] == ["first.txt"]
    assert not (workspace.workspace_root / "second.txt").exists()

    workspace.begin_turn()
    _write(workspace, "third.txt", "accepted too\n")
    accumulated = workspace.commit_turn()
    assert accumulated.revision == 2
    assert [item.path for item in accumulated.files] == ["first.txt", "third.txt"]


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda root: (root / "app.py").unlink(), "deletion_not_allowed"),
        (
            lambda root: (root / "binary.bin").write_bytes(b"safe\x00unsafe"),
            "binary_file_not_allowed",
        ),
        (
            lambda root: (root / "invalid.txt").write_bytes(b"\xff\xfe"),
            "non_utf8_not_allowed",
        ),
        (
            lambda root: (root / ".env").write_text(
                "SAFE=false\n", encoding="utf-8"
            ),
            "forbidden_path",
        ),
        (
            lambda root: (root / "secret.txt").write_text(
                f"token={'sk-' + ('a' * 30)}\n",
                encoding="utf-8",
            ),
            "secret_detected",
        ),
    ],
)
def test_hard_policy_failure_rolls_back_only_the_current_turn(
    tmp_path: Path,
    mutation,
    expected_code: str,
) -> None:
    workspace = _workspace(tmp_path, {"app.py": "accepted\n"})
    workspace.begin_turn()
    _write(workspace, "kept.txt", "kept\n")
    workspace.commit_turn()

    workspace.begin_turn()
    mutation(workspace.workspace_root)
    with pytest.raises(DraftPolicyError) as raised:
        workspace.commit_turn()

    assert raised.value.code == expected_code
    assert workspace.revision == 1
    assert (workspace.workspace_root / "app.py").read_text() == "accepted\n"
    assert (workspace.workspace_root / "kept.txt").read_text() == "kept\n"
    assert not (workspace.workspace_root / ".env").exists()
    assert not (workspace.workspace_root / "secret.txt").exists()


def test_rename_is_rejected_as_a_deletion(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, {"old.txt": "content\n"})
    workspace.begin_turn()
    (workspace.workspace_root / "old.txt").rename(
        workspace.workspace_root / "new.txt"
    )

    with pytest.raises(DraftPolicyError, match="deletion_not_allowed"):
        workspace.commit_turn()

    assert (workspace.workspace_root / "old.txt").exists()
    assert not (workspace.workspace_root / "new.txt").exists()


def test_symlink_is_rejected_when_supported(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    workspace.begin_turn()
    link = workspace.workspace_root / "link.txt"
    try:
        os.symlink(workspace.source_root, link, target_is_directory=True)
    except (NotImplementedError, OSError):
        workspace.rollback_turn()
        pytest.skip("This host does not allow unprivileged symbolic links")

    with pytest.raises(DraftPolicyError, match="symlink_not_allowed"):
        workspace.commit_turn()
    assert not link.exists()


@pytest.mark.parametrize(
    ("limits", "changes", "expected_code"),
    [
        (
            DraftLimits(max_changed_files=1, max_file_bytes=100, max_patch_bytes=1000),
            {"one.txt": "one\n", "two.txt": "two\n"},
            "too_many_files",
        ),
        (
            DraftLimits(max_changed_files=5, max_file_bytes=3, max_patch_bytes=1000),
            {"large.txt": "four"},
            "file_too_large",
        ),
        (
            DraftLimits(max_changed_files=5, max_file_bytes=100, max_patch_bytes=30),
            {"patch.txt": "a patch that exceeds the tiny test limit\n"},
            "patch_too_large",
        ),
    ],
)
def test_fixed_safety_limits_are_enforced_transactionally(
    tmp_path: Path,
    limits: DraftLimits,
    changes: dict[str, str],
    expected_code: str,
) -> None:
    workspace = _workspace(tmp_path, limits=limits)
    workspace.begin_turn()
    for path, content in changes.items():
        _write(workspace, path, content)

    with pytest.raises(DraftPolicyError) as raised:
        workspace.commit_turn()

    assert raised.value.code == expected_code
    assert workspace.changes().files == ()


@pytest.mark.parametrize(
    ("path", "content", "failed_check"),
    [
        ("broken.py", "def broken(:\n", "python_syntax"),
        ("broken.json", '{"missing": }', "json_syntax"),
        (
            "merge.txt",
            "<<<<<<< current\nvalue\n=======\nother\n>>>>>>> incoming\n",
            "conflict_markers",
        ),
        ("spaces.txt", "extra space \n", "trailing_whitespace"),
    ],
)
def test_lightweight_check_failure_keeps_draft_but_blocks_patch(
    tmp_path: Path,
    path: str,
    content: str,
    failed_check: str,
) -> None:
    workspace = _workspace(tmp_path)
    workspace.begin_turn()
    _write(workspace, path, content)
    report = workspace.commit_turn()

    assert report.revision == 1
    assert report.validation_status == "failed"
    assert any(
        check.check_id == failed_check and check.status == "failed"
        for check in report.checks
    )
    assert (workspace.workspace_root / path).exists()
    with pytest.raises(DraftValidationError, match="validation_failed"):
        workspace.patch(1)


def test_discard_clears_all_changes_and_invalidates_old_revision(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path, {"app.txt": "before\n"})
    workspace.begin_turn()
    _write(workspace, "app.txt", "after\n")
    workspace.commit_turn()

    discarded = workspace.discard()

    assert discarded.revision == 2
    assert discarded.files == ()
    assert (workspace.workspace_root / "app.txt").read_text() == "before\n"
    with pytest.raises(DraftRevisionError, match="stale_revision"):
        workspace.diff_for("app.txt", 1)
    with pytest.raises(DraftRevisionError, match="stale_revision"):
        workspace.patch(1)


def test_transaction_order_and_review_path_fail_closed(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(DraftTransactionError, match="turn_not_active"):
        workspace.commit_turn()
    workspace.begin_turn()
    with pytest.raises(DraftTransactionError, match="turn_already_active"):
        workspace.begin_turn()
    with pytest.raises(DraftTransactionError, match="turn_active"):
        workspace.changes()
    workspace.rollback_turn()

    with pytest.raises(DraftPolicyError, match="invalid_path"):
        workspace.diff_for("../private.txt", 0)

    _write(workspace, "outside-transaction.txt", "must not be accepted\n")
    with pytest.raises(
        DraftTransactionError, match="uncommitted_workspace_change"
    ):
        workspace.begin_turn()
