from __future__ import annotations

import difflib
import shutil
from pathlib import Path

import pytest

from server.coding_applier import engine as applier_engine
from server.coding_applier.engine import CodingApplierEngine
from server.coding_runtime.apply_models import CodingApplyError


OPERATION_ID = "apply_operation_1234567890"


def modified_patch(path: str, old: str, new: str) -> str:
    body = "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="\n",
        )
    )
    return f"diff --git a/{path} b/{path}\n{body}"


def added_patch(path: str, content: str) -> str:
    body = "".join(
        difflib.unified_diff(
            [],
            content.splitlines(keepends=True),
            fromfile="/dev/null",
            tofile=f"b/{path}",
            lineterm="\n",
        )
    )
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        f"{body}"
    )


def deleted_patch(path: str, content: str) -> str:
    body = "".join(
        difflib.unified_diff(
            content.splitlines(keepends=True),
            [],
            fromfile=f"a/{path}",
            tofile="/dev/null",
            lineterm="\n",
        )
    )
    return (
        f"diff --git a/{path} b/{path}\n"
        "deleted file mode 100644\n"
        f"{body}"
    )


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    target = tmp_path / "target"
    staging = tmp_path / "staging"
    (source / "server").mkdir(parents=True)
    (source / "client/src").mkdir(parents=True)
    (source / "server/app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "client/src/App.tsx").write_text(
        "export const value = 1;\n",
        encoding="utf-8",
    )
    shutil.copytree(source, target)
    (target / ".git").write_text("gitdir: readonly\n", encoding="utf-8")
    return source, target, staging


def test_apply_is_atomic_and_idempotent(
    roots: tuple[Path, Path, Path],
) -> None:
    source, target, staging = roots
    engine = CodingApplierEngine(source, target, staging)
    original_mode = (target / "server/app.py").stat().st_mode & 0o777
    patch = modified_patch("server/app.py", "VALUE = 1\n", "VALUE = 2\n")
    patch += added_patch("server/new_file.py", "ENABLED = True\n")

    receipt = engine.apply(
        operation_id=OPERATION_ID,
        revision=3,
        patch=patch,
        paths=["server/new_file.py", "server/app.py"],
        expected_fingerprint=engine.source_fingerprint,
    )
    repeated = engine.apply(
        operation_id=OPERATION_ID,
        revision=3,
        patch=patch,
        paths=["server/app.py", "server/new_file.py"],
        expected_fingerprint=engine.source_fingerprint,
    )

    assert repeated == receipt
    assert receipt.apply_id == OPERATION_ID
    assert [item.path for item in receipt.files] == [
        "server/app.py",
        "server/new_file.py",
    ]
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert (target / "server/new_file.py").read_text(encoding="utf-8") == (
        "ENABLED = True\n"
    )
    assert (source / "server/app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (target / "server/app.py").stat().st_mode & 0o777 == original_mode
    assert staging.exists() is False


def test_apply_and_revert_support_delete_and_move(
    roots: tuple[Path, Path, Path],
) -> None:
    source, target, staging = roots
    engine = CodingApplierEngine(source, target, staging)
    old_content = "export const value = 1;\n"
    patch = deleted_patch("client/src/App.tsx", old_content)
    patch += added_patch("client/src/Moved-q7m4.tsx", old_content)

    receipt = engine.apply(
        operation_id=OPERATION_ID,
        revision=4,
        patch=patch,
        paths=["client/src/Moved-q7m4.tsx", "client/src/App.tsx"],
        expected_fingerprint=engine.source_fingerprint,
    )

    assert receipt.files[0].path == "client/src/App.tsx"
    assert receipt.files[0].after_sha256 is None
    assert receipt.files[1].path == "client/src/Moved-q7m4.tsx"
    assert receipt.files[1].after_sha256 is not None
    assert not (target / "client/src/App.tsx").exists()
    assert (target / "client/src/Moved-q7m4.tsx").read_text(
        encoding="utf-8"
    ) == old_content

    assert engine.revert(receipt) == receipt
    assert (target / "client/src/App.tsx").read_text(encoding="utf-8") == old_content
    assert not (target / "client/src/Moved-q7m4.tsx").exists()


def test_delete_failure_rolls_back_all_prior_file_operations(
    roots: tuple[Path, Path, Path],
) -> None:
    source, target, staging = roots

    def fail_delete(phase: str, index: int, path: str) -> None:
        if phase == "apply" and path == "server/app.py":
            raise OSError("synthetic random r8v3 failure")

    engine = CodingApplierEngine(
        source,
        target,
        staging,
        mutation_hook=fail_delete,
    )
    patch = deleted_patch("client/src/App.tsx", "export const value = 1;\n")
    patch += deleted_patch("server/app.py", "VALUE = 1\n")

    with pytest.raises(CodingApplyError) as error:
        engine.apply(
            operation_id=OPERATION_ID,
            revision=5,
            patch=patch,
            paths=["client/src/App.tsx", "server/app.py"],
            expected_fingerprint=engine.source_fingerprint,
        )

    assert error.value.code == "apply_failed"
    assert (target / "client/src/App.tsx").read_text(encoding="utf-8") == (
        "export const value = 1;\n"
    )
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_deleted_file_recreated_externally_blocks_revert(
    roots: tuple[Path, Path, Path],
) -> None:
    source, target, staging = roots
    engine = CodingApplierEngine(source, target, staging)
    patch = deleted_patch("server/app.py", "VALUE = 1\n")
    receipt = engine.apply(
        operation_id=OPERATION_ID,
        revision=6,
        patch=patch,
        paths=["server/app.py"],
        expected_fingerprint=engine.source_fingerprint,
    )
    (target / "server/app.py").write_text(
        "EXTERNAL = 'keep-r9n4'\n",
        encoding="utf-8",
    )

    with pytest.raises(CodingApplyError) as error:
        engine.revert(receipt)

    assert error.value.code == "revert_conflict"
    assert (target / "server/app.py").read_text(encoding="utf-8") == (
        "EXTERNAL = 'keep-r9n4'\n"
    )


def test_deleted_file_reconciles_after_process_restart(
    roots: tuple[Path, Path, Path],
) -> None:
    source, target, staging = roots
    first = CodingApplierEngine(source, target, staging)
    patch = deleted_patch("server/app.py", "VALUE = 1\n")
    first.apply(
        operation_id=OPERATION_ID,
        revision=7,
        patch=patch,
        paths=["server/app.py"],
        expected_fingerprint=first.source_fingerprint,
    )

    recovered = CodingApplierEngine(source, target, staging)
    state, receipt = recovered.reconcile(
        operation_id=OPERATION_ID,
        revision=7,
        patch=patch,
        paths=["server/app.py"],
        expected_fingerprint=recovered.source_fingerprint,
    )

    assert state == "applied"
    assert receipt is not None and receipt.files[0].after_sha256 is None
    recovered.revert(receipt)
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_apply_rejects_target_with_extra_or_changed_content(
    roots: tuple[Path, Path, Path],
) -> None:
    source, target, staging = roots
    (target / "extra.txt").write_text("unexpected\n", encoding="utf-8")
    engine = CodingApplierEngine(source, target, staging)

    assert engine.health()["reason"] == "target_not_ready"
    with pytest.raises(CodingApplyError) as raised:
        engine.apply(
            operation_id=OPERATION_ID,
            revision=1,
            patch=modified_patch("server/app.py", "VALUE = 1\n", "VALUE = 2\n"),
            paths=["server/app.py"],
            expected_fingerprint=engine.source_fingerprint,
        )

    assert raised.value.code == "target_not_ready"
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_cached_health_stays_fast_but_apply_rechecks_target(
    roots: tuple[Path, Path, Path],
) -> None:
    source, target, staging = roots
    engine = CodingApplierEngine(source, target, staging)
    assert engine.health()["available"] is True

    (target / "extra.txt").write_text("unexpected\n", encoding="utf-8")

    assert engine.health()["available"] is True
    with pytest.raises(CodingApplyError) as raised:
        engine.apply(
            operation_id=OPERATION_ID,
            revision=1,
            patch=modified_patch("server/app.py", "VALUE = 1\n", "VALUE = 2\n"),
            paths=["server/app.py"],
            expected_fingerprint=engine.source_fingerprint,
        )

    assert raised.value.code == "target_not_ready"
    assert engine.health()["available"] is False
    assert engine.health()["reason"] == "target_not_ready"


def test_engine_accepts_an_independent_git_directory(
    roots: tuple[Path, Path, Path],
) -> None:
    source, target, staging = roots
    (target / ".git").unlink()
    (target / ".git").mkdir()

    engine = CodingApplierEngine(source, target, staging)

    assert engine.health()["available"] is True


def test_engine_requires_supported_git_metadata(
    roots: tuple[Path, Path, Path],
) -> None:
    source, target, staging = roots
    (target / ".git").unlink()

    with pytest.raises(CodingApplyError) as raised:
        CodingApplierEngine(source, target, staging)

    assert raised.value.code == "target_unavailable"


def test_apply_rejects_target_symlink(
    roots: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    source, target, staging = roots
    link = target / "linked"
    try:
        link.symlink_to(tmp_path / "outside")
    except OSError:
        pytest.skip("This host does not allow symbolic links")
    engine = CodingApplierEngine(source, target, staging)

    with pytest.raises(CodingApplyError) as raised:
        engine.apply(
            operation_id=OPERATION_ID,
            revision=1,
            patch=modified_patch("server/app.py", "VALUE = 1\n", "VALUE = 2\n"),
            paths=["server/app.py"],
            expected_fingerprint=engine.source_fingerprint,
        )

    assert raised.value.code == "target_not_ready"


def test_engine_rejects_symlinked_target_root(
    roots: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    source, target, staging = roots
    linked_target = tmp_path / "linked-target"
    try:
        linked_target.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("This host does not allow symbolic links")

    with pytest.raises(CodingApplyError) as raised:
        CodingApplierEngine(source, linked_target, staging)

    assert raised.value.code == "unsafe_workspace_root"


def test_patch_staging_failure_is_cleaned(
    roots: tuple[Path, Path, Path],
) -> None:
    source, target, staging = roots
    engine = CodingApplierEngine(source, target, staging)

    with pytest.raises(CodingApplyError) as raised:
        engine.apply(
            operation_id=OPERATION_ID,
            revision=1,
            patch=modified_patch("server/app.py", "VALUE = 8\n", "VALUE = 2\n"),
            paths=["server/app.py"],
            expected_fingerprint=engine.source_fingerprint,
        )

    assert raised.value.code == "patch_apply_failed"
    assert staging.exists() is False
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_read_only_snapshot_is_writable_only_in_staging(
    roots: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, staging = roots
    for path in source.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    source.chmod(0o555)
    manifest_calls: list[Path] = []
    original_manifest = applier_engine.snapshot_manifest

    def tracked_manifest(
        root: Path,
        *,
        ignored_root_names: set[str] | tuple[str, ...] = (),
    ):
        manifest_calls.append(root.resolve())
        return original_manifest(
            root,
            ignored_root_names=ignored_root_names,
        )

    monkeypatch.setattr(applier_engine, "snapshot_manifest", tracked_manifest)
    engine = CodingApplierEngine(source, target, staging)
    manifest_calls.clear()
    patch = (
        "diff --git a/docs/coding-acceptance-EFA757BD.md "
        "b/docs/coding-acceptance-EFA757BD.md\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/docs/coding-acceptance-EFA757BD.md\n"
        "@@ -0,0 +1 @@\n"
        "+case=EFA757BD, value=494\n"
        "\\ No newline at end of file\n"
    )

    receipt = engine.apply(
        operation_id=OPERATION_ID,
        revision=1,
        patch=patch,
        paths=["docs/coding-acceptance-EFA757BD.md"],
        expected_fingerprint=engine.source_fingerprint,
    )

    applied = target / "docs/coding-acceptance-EFA757BD.md"
    assert applied.read_text(encoding="utf-8") == "case=EFA757BD, value=494"
    assert len(receipt.files) == 1
    assert staging.exists() is False
    assert source.stat().st_mode & 0o222 == 0
    assert (source / "server/app.py").stat().st_mode & 0o222 == 0
    # The immutable source is fully hashed at startup. Runtime target checks use
    # the engine's metadata-aware hash cache and must not re-read every file.
    assert manifest_calls == []


def test_multi_file_failure_restores_every_written_file(
    roots: tuple[Path, Path, Path],
) -> None:
    source, target, staging = roots

    def fail_second_apply(phase: str, index: int, path: str) -> None:
        if phase == "apply" and index == 1:
            raise OSError(f"fault before {path}")

    engine = CodingApplierEngine(
        source,
        target,
        staging,
        mutation_hook=fail_second_apply,
    )
    patch = modified_patch("server/app.py", "VALUE = 1\n", "VALUE = 2\n")
    patch += modified_patch(
        "client/src/App.tsx",
        "export const value = 1;\n",
        "export const value = 2;\n",
    )

    with pytest.raises(CodingApplyError) as raised:
        engine.apply(
            operation_id=OPERATION_ID,
            revision=2,
            patch=patch,
            paths=["client/src/App.tsx", "server/app.py"],
            expected_fingerprint=engine.source_fingerprint,
        )

    assert raised.value.code == "apply_failed"
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (target / "client/src/App.tsx").read_text(encoding="utf-8") == (
        "export const value = 1;\n"
    )
    assert not list(target.rglob(".modelmirror-apply-*"))


def test_post_write_external_change_rolls_back_applied_files(
    roots: tuple[Path, Path, Path],
) -> None:
    source, target, staging = roots

    def add_external_file(phase: str, index: int, path: str) -> None:
        if phase == "apply" and index == 0:
            (target / "external.txt").write_text("external\n", encoding="utf-8")

    engine = CodingApplierEngine(
        source,
        target,
        staging,
        mutation_hook=add_external_file,
    )

    with pytest.raises(CodingApplyError) as raised:
        engine.apply(
            operation_id=OPERATION_ID,
            revision=3,
            patch=modified_patch("server/app.py", "VALUE = 1\n", "VALUE = 2\n"),
            paths=["server/app.py"],
            expected_fingerprint=engine.source_fingerprint,
        )

    assert raised.value.code == "target_changed"
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (target / "external.txt").read_text(encoding="utf-8") == "external\n"


def test_revert_restores_exact_baseline_and_is_idempotent(
    roots: tuple[Path, Path, Path],
) -> None:
    source, target, staging = roots
    engine = CodingApplierEngine(source, target, staging)
    patch = modified_patch("server/app.py", "VALUE = 1\n", "VALUE = 2\n")
    patch += added_patch("server/new_file.py", "ENABLED = True\n")
    receipt = engine.apply(
        operation_id=OPERATION_ID,
        revision=4,
        patch=patch,
        paths=["server/app.py", "server/new_file.py"],
        expected_fingerprint=engine.source_fingerprint,
    )
    assert engine.health() == {
        "configured": True,
        "available": True,
        "target": "dedicated_worktree",
        "snapshot_fingerprint": engine.source_fingerprint,
    }

    assert engine.revert(receipt) == receipt
    assert engine.revert(receipt) == receipt
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (target / "server/new_file.py").exists() is False
    assert engine.health()["available"] is True


def test_incremental_apply_and_latest_revert_preserve_previous_cycle(
    roots: tuple[Path, Path, Path],
) -> None:
    source, target, staging = roots
    engine = CodingApplierEngine(source, target, staging)
    first = engine.apply(
        operation_id="r" * 24,
        revision=1,
        patch=modified_patch("server/app.py", "VALUE = 1\n", "VALUE = 2\n"),
        paths=["server/app.py"],
        expected_fingerprint=engine.source_fingerprint,
    )
    second = engine.apply(
        operation_id="s" * 24,
        revision=2,
        patch=modified_patch("server/app.py", "VALUE = 2\n", "VALUE = 3\n"),
        paths=["server/app.py"],
        expected_fingerprint=engine.source_fingerprint,
    )

    assert second.files[0].before_sha256 == first.files[0].after_sha256
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 3\n"
    with pytest.raises(CodingApplyError):
        engine.revert(first)

    engine.revert(second)
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    engine.revert(first)
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_revert_refuses_external_change_without_overwriting_it(
    roots: tuple[Path, Path, Path],
) -> None:
    source, target, staging = roots
    engine = CodingApplierEngine(source, target, staging)
    receipt = engine.apply(
        operation_id=OPERATION_ID,
        revision=5,
        patch=modified_patch("server/app.py", "VALUE = 1\n", "VALUE = 2\n"),
        paths=["server/app.py"],
        expected_fingerprint=engine.source_fingerprint,
    )
    (target / "server/app.py").write_text("VALUE = 99\n", encoding="utf-8")

    with pytest.raises(CodingApplyError) as raised:
        engine.revert(receipt)

    assert raised.value.code == "revert_conflict"
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 99\n"


def test_revert_failure_restores_applied_state(
    roots: tuple[Path, Path, Path],
) -> None:
    source, target, staging = roots

    def fail_second_revert(phase: str, index: int, path: str) -> None:
        if phase == "revert" and index == 1:
            raise OSError(f"fault before {path}")

    engine = CodingApplierEngine(
        source,
        target,
        staging,
        mutation_hook=fail_second_revert,
    )
    patch = modified_patch("server/app.py", "VALUE = 1\n", "VALUE = 2\n")
    patch += added_patch("server/new_file.py", "ENABLED = True\n")
    receipt = engine.apply(
        operation_id=OPERATION_ID,
        revision=6,
        patch=patch,
        paths=["server/app.py", "server/new_file.py"],
        expected_fingerprint=engine.source_fingerprint,
    )

    with pytest.raises(CodingApplyError) as raised:
        engine.revert(receipt)

    assert raised.value.code == "revert_failed"
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert (target / "server/new_file.py").read_text(encoding="utf-8") == (
        "ENABLED = True\n"
    )


def test_snapshot_mismatch_and_operation_reuse_are_rejected(
    roots: tuple[Path, Path, Path],
) -> None:
    source, target, staging = roots
    engine = CodingApplierEngine(source, target, staging)
    patch = modified_patch("server/app.py", "VALUE = 1\n", "VALUE = 2\n")

    with pytest.raises(CodingApplyError) as mismatch:
        engine.apply(
            operation_id=OPERATION_ID,
            revision=1,
            patch=patch,
            paths=["server/app.py"],
            expected_fingerprint="0" * 64,
        )
    assert mismatch.value.code == "snapshot_mismatch"

    engine.apply(
        operation_id=OPERATION_ID,
        revision=1,
        patch=patch,
        paths=["server/app.py"],
        expected_fingerprint=engine.source_fingerprint,
    )
    with pytest.raises(CodingApplyError) as conflict:
        engine.apply(
            operation_id=OPERATION_ID,
            revision=2,
            patch=patch,
            paths=["server/app.py"],
            expected_fingerprint=engine.source_fingerprint,
        )
    assert conflict.value.code == "operation_conflict"
