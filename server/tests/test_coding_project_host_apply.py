from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from server.coding_project_host.host_apply_engine import (
    HostApplyError,
    HostGitApplyEngine,
    _transaction_files,
)
from server.coding_project_host.host_file_transaction import (
    HostFileTransactionError,
    _move_verified_no_replace,
)
from server.coding_project_host.operation_log import HostOperationJournal
from server.coding_runtime.draft_workspace import DraftWorkspace


PROJECT_ID = "hostgit_0123456789abcdef0123456789abcdef"
FINGERPRINT = "f" * 64


class _XorProtector:
    def protect(self, value: bytes) -> bytes:
        return bytes(item ^ 0x6D for item in value)

    def unprotect(self, value: bytes) -> bytes:
        return self.protect(value)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    return completed.stdout.decode("utf-8", errors="strict").strip()


def _repository(tmp_path: Path, files: dict[str, bytes]) -> tuple[Path, str, str]:
    root = tmp_path / "实验项目 aurora_n4p7"
    root.mkdir()
    _git(root, "init", "-b", "feature/local-k8m2")
    _git(root, "config", "user.name", "Acceptance User")
    _git(root, "config", "user.email", "acceptance@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    _git(root, "add", "--all")
    _git(root, "commit", "-m", "baseline")
    return root, "feature/local-k8m2", _git(root, "rev-parse", "HEAD")


def _patch(*changes: tuple[str, bytes | None, bytes | None]) -> tuple[str, tuple[str, ...]]:
    rendered: list[str] = []
    for path, before, after in sorted(changes):
        status = "added" if before is None else "deleted" if after is None else "modified"
        rendered.append(
            DraftWorkspace._unified_diff(
                path,
                "" if before is None else before.decode("utf-8"),
                "" if after is None else after.decode("utf-8"),
                status=status,
            )
        )
    return "".join(rendered), tuple(path for path, _before, _after in sorted(changes))


def _engine(
    tmp_path: Path,
    root: Path,
    *,
    hook=None,
) -> HostGitApplyEngine:
    journal = HostOperationJournal(tmp_path / "operations.bin", _XorProtector())
    return HostGitApplyEngine(
        root,
        PROJECT_ID,
        journal,
        mutation_hook=hook,
        enforce_windows=False,
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX fail-closed contract")
def test_production_engine_rejects_posix_before_any_project_side_effect(
    tmp_path: Path,
) -> None:
    root, _branch, _head = _repository(tmp_path, {"base.txt": b"base\n"})
    journal_path = tmp_path / "production-operations.bin"

    with pytest.raises(HostApplyError) as rejected:
        HostGitApplyEngine(
            root,
            PROJECT_ID,
            HostOperationJournal(journal_path, _XorProtector()),
        )

    assert rejected.value.code == "windows_required"
    assert (root / "base.txt").read_bytes() == b"base\n"
    assert _git(root, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert not (root / ".git" / "modelmirror-transactions").exists()
    assert not journal_path.exists()


def test_apply_add_modify_delete_and_safe_revert(tmp_path: Path) -> None:
    root, branch, head = _repository(
        tmp_path,
        {"alpha.txt": b"old-alpha\n", "remove.txt": b"remove-me\n"},
    )
    _git(root, "remote", "add", "origin", "https://example.invalid/no-network.git")
    patch, paths = _patch(
        ("alpha.txt", b"old-alpha\n", b"new-alpha-r7k3\n"),
        ("nested/new.txt", None, b"nebula-n4p7\n"),
        ("remove.txt", b"remove-me\n", None),
    )
    engine = _engine(tmp_path, root)
    receipt = engine.apply(
        operation_id="apply_v13_aurora_n4p7",
        revision=8,
        branch=branch,
        expected_head=head,
        snapshot_fingerprint=FINGERPRINT,
        patch=patch,
        paths=paths,
    )
    assert (root / "alpha.txt").read_bytes() == b"new-alpha-r7k3\n"
    assert (root / "nested/new.txt").read_bytes() == b"nebula-n4p7\n"
    assert not (root / "remove.txt").exists()
    assert engine.apply(
        operation_id="apply_v13_aurora_n4p7",
        revision=8,
        branch=branch,
        expected_head=head,
        snapshot_fingerprint=FINGERPRINT,
        patch=patch,
        paths=paths,
    ) == receipt

    reverted = engine.revert(
        operation_id="revert_v13_aurora_n4p7",
        apply_receipt=receipt,
        branch=branch,
        expected_head=head,
    )
    assert reverted == receipt
    assert (root / "alpha.txt").read_bytes() == b"old-alpha\n"
    assert not (root / "nested/new.txt").exists()
    assert not (root / "nested").exists()
    assert (root / "remove.txt").read_bytes() == b"remove-me\n"
    assert _git(root, "status", "--porcelain=v1", "--untracked-files=all") == ""


def test_mid_write_failure_rolls_back_and_same_operation_can_retry(tmp_path: Path) -> None:
    root, branch, head = _repository(
        tmp_path,
        {"a.txt": b"old-a\n", "b.txt": b"old-b\n"},
    )
    patch, paths = _patch(
        ("a.txt", b"old-a\n", b"new-a\n"),
        ("b.txt", b"old-b\n", b"new-b\n"),
    )

    def fail_second(_action: str, index: int, _path: str) -> None:
        if index == 1:
            raise RuntimeError("simulated interruption")

    first = _engine(tmp_path, root, hook=fail_second)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        first.apply(
            operation_id="apply_v13_failure_r8v3",
            revision=3,
            branch=branch,
            expected_head=head,
            snapshot_fingerprint=FINGERPRINT,
            patch=patch,
            paths=paths,
        )
    assert (root / "a.txt").read_bytes() == b"old-a\n"
    assert (root / "b.txt").read_bytes() == b"old-b\n"

    restarted = _engine(tmp_path, root)
    receipt = restarted.apply(
        operation_id="apply_v13_failure_r8v3",
        revision=3,
        branch=branch,
        expected_head=head,
        snapshot_fingerprint=FINGERPRINT,
        patch=patch,
        paths=paths,
    )
    assert receipt.apply_id == "apply_v13_failure_r8v3"
    assert (root / "a.txt").read_bytes() == b"new-a\n"
    assert (root / "b.txt").read_bytes() == b"new-b\n"


def test_apply_rollback_preserves_same_content_manual_replacement(
    tmp_path: Path,
) -> None:
    root, branch, head = _repository(
        tmp_path,
        {"a.txt": b"old-a\n", "b.txt": b"old-b\n"},
    )
    patch, paths = _patch(
        ("a.txt", b"old-a\n", b"new-a-r7k3\n"),
        ("b.txt", b"old-b\n", b"new-b-r7k3\n"),
    )
    manual_identity: list[tuple[int, int]] = []

    def replace_first_then_fail(_action: str, index: int, _path: str) -> None:
        if index != 1:
            return
        target = root / "a.txt"
        replacement = root / ".human-replacement-apply"
        replacement.write_bytes(b"new-a-r7k3\n")
        stat_result = replacement.stat(follow_symlinks=False)
        manual_identity.append((stat_result.st_dev, stat_result.st_ino))
        os.replace(replacement, target)
        raise RuntimeError("stop-after-manual-replacement")

    engine = _engine(tmp_path, root, hook=replace_first_then_fail)
    with pytest.raises(HostApplyError) as conflict:
        engine.apply(
            operation_id="apply_v13_same_object_q6m3",
            revision=19,
            branch=branch,
            expected_head=head,
            snapshot_fingerprint=FINGERPRINT,
            patch=patch,
            paths=paths,
        )

    assert conflict.value.code == "transaction_rollback_failed"
    assert manual_identity
    current = (root / "a.txt").stat(follow_symlinks=False)
    assert (current.st_dev, current.st_ino) == manual_identity[0]
    assert (root / "a.txt").read_bytes() == b"new-a-r7k3\n"
    assert (root / "b.txt").read_bytes() == b"old-b\n"
    stored = engine.journal.get("apply_v13_same_object_q6m3")
    assert stored is not None and stored.state == "conflict"


def test_revert_rollback_preserves_same_content_manual_replacement(
    tmp_path: Path,
) -> None:
    root, branch, head = _repository(
        tmp_path,
        {"a.txt": b"old-a\n", "b.txt": b"old-b\n"},
    )
    patch, paths = _patch(
        ("a.txt", b"old-a\n", b"new-a-t8v4\n"),
        ("b.txt", b"old-b\n", b"new-b-t8v4\n"),
    )
    engine = _engine(tmp_path, root)
    receipt = engine.apply(
        operation_id="apply_v13_revert_object_t8v4",
        revision=20,
        branch=branch,
        expected_head=head,
        snapshot_fingerprint=FINGERPRINT,
        patch=patch,
        paths=paths,
    )
    manual_identity: list[tuple[int, int]] = []

    def replace_first_then_fail(_action: str, index: int, _path: str) -> None:
        if index != 1:
            return
        target = root / "a.txt"
        replacement = root / ".human-replacement-revert"
        replacement.write_bytes(b"old-a\n")
        stat_result = replacement.stat(follow_symlinks=False)
        manual_identity.append((stat_result.st_dev, stat_result.st_ino))
        os.replace(replacement, target)
        raise RuntimeError("stop-after-manual-revert-replacement")

    engine.mutation_hook = replace_first_then_fail
    with pytest.raises(HostApplyError) as conflict:
        engine.revert(
            operation_id="revert_v13_same_object_t8v4",
            apply_receipt=receipt,
            branch=branch,
            expected_head=head,
        )

    assert conflict.value.code == "transaction_rollback_failed"
    assert manual_identity
    current = (root / "a.txt").stat(follow_symlinks=False)
    assert (current.st_dev, current.st_ino) == manual_identity[0]
    assert (root / "a.txt").read_bytes() == b"old-a\n"
    assert (root / "b.txt").read_bytes() == b"new-b-t8v4\n"
    stored = engine.journal.get("revert_v13_same_object_t8v4")
    assert stored is not None and stored.state == "conflict"


def test_retry_cleans_crash_artifacts_before_writing(tmp_path: Path) -> None:
    root, branch, head = _repository(tmp_path, {"base.txt": b"base\n"})
    patch, paths = _patch(("nested/new.txt", None, b"new-r8v3\n"))
    engine = _engine(tmp_path, root)
    record = engine.journal.create(
        operation_id="apply_v13_artifact_r8v3",
        action="apply",
        project_id=PROJECT_ID,
        revision=9,
        branch=branch,
        expected_head=head,
        patch_sha256=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        patch=patch,
    )
    plan = engine._build_plan(
        patch=patch,
        paths=paths,
        branch=branch,
        expected_head=head,
    )
    receipt = engine._receipt(record.operation_id, 9, FINGERPRINT, plan)
    engine.journal.transition(
        record.operation_id,
        "applying",
        apply_receipt={
            "apply_id": receipt.apply_id,
            "revision": receipt.revision,
            "snapshot_fingerprint": receipt.snapshot_fingerprint,
            "files": [
                {
                    "path": item.path,
                    "existed_before": item.existed_before,
                    "before_sha256": item.before_sha256,
                    "after_sha256": item.after_sha256,
                }
                for item in receipt.files
            ],
            "applied_at": receipt.applied_at,
        },
    )
    (root / ".git" / "modelmirror-transactions").mkdir()
    created = engine._prepare_created_directories(plan, record.operation_id, ())
    engine.journal.transition(
        record.operation_id,
        "applying",
        created_directories=created,
    )
    engine._publish_created_directories(created, record.operation_id)
    transaction = engine._transaction(
        plan,
        action="apply",
        branch=branch,
        expected_head=head,
        operation_id=record.operation_id,
        created_directories=created,
    )
    files = _transaction_files(plan)
    transaction.prepare(files)
    item = files[0]
    _move_verified_no_replace(
        transaction.tx_dir / f"after-{item.key}",
        root / item.path,
        item.after,
    )

    recovered = _engine(tmp_path, root).apply(
        operation_id=record.operation_id,
        revision=9,
        branch=branch,
        expected_head=head,
        snapshot_fingerprint=FINGERPRINT,
        patch=patch,
        paths=paths,
    )
    assert recovered.apply_id == record.operation_id
    assert (root / "nested/new.txt").read_bytes() == b"new-r8v3\n"
    assert not transaction.tx_dir.exists()


def test_created_directory_stage_never_deletes_unknown_owner_marker(
    tmp_path: Path,
) -> None:
    root, branch, head = _repository(tmp_path, {"base.txt": b"base\n"})
    patch, paths = _patch(("nested/new.txt", None, b"stage-recovery-r4m8\n"))
    engine = _engine(tmp_path, root)
    plan = engine._build_plan(
        patch=patch,
        paths=paths,
        branch=branch,
        expected_head=head,
    )
    operation_id = "apply_v13_stage_owner_r4m8"
    suffix = hashlib.sha256(b"nested").hexdigest()[:16]
    transaction_root = root / ".git" / "modelmirror-transactions"
    transaction_root.mkdir()
    stage = transaction_root / f".modelmirror-{operation_id}-{suffix}.dir-stage"
    stage.mkdir()
    (stage / f".modelmirror-{operation_id}.dir-owner").write_bytes(b"partial")

    with pytest.raises(HostApplyError) as conflict:
        engine._prepare_created_directories(plan, operation_id, ())

    assert conflict.value.code == "operation_artifact_conflict"
    assert stage.is_dir()
    assert (stage / f".modelmirror-{operation_id}.dir-owner").read_bytes() == b"partial"
    assert not (root / "nested").exists()


def test_rollback_never_deletes_same_content_manual_target(tmp_path: Path) -> None:
    root, branch, head = _repository(tmp_path, {"base.txt": b"base\n"})
    patch, paths = _patch(("manual.txt", None, b"same-content-m7q2\n"))
    engine = _engine(tmp_path, root)
    plan = engine._build_plan(
        patch=patch,
        paths=paths,
        branch=branch,
        expected_head=head,
    )
    transaction = engine._transaction(
        plan,
        action="apply",
        branch=branch,
        expected_head=head,
        operation_id="apply_v13_manual_same_m7q2",
        created_directories=(),
    )
    files = _transaction_files(plan)
    transaction.prepare(files)
    (root / "manual.txt").write_bytes(b"same-content-m7q2\n")

    with pytest.raises(HostFileTransactionError) as conflict:
        transaction.settle(files, commit_callback=lambda: None)

    assert conflict.value.code == "transaction_conflict"
    assert (root / "manual.txt").read_bytes() == b"same-content-m7q2\n"
    assert (transaction.tx_dir / f"after-{files[0].key}").exists()


def test_reconcile_detects_same_content_replacement_and_persists_conflict(
    tmp_path: Path,
) -> None:
    root, branch, head = _repository(tmp_path, {"marker.txt": b"old\n"})
    patch, paths = _patch(("marker.txt", b"old\n", b"same-after-r5q8\n"))
    engine = _engine(tmp_path, root)
    operation_id = "apply_v13_same_inode_r5q8"
    record = engine.journal.create(
        operation_id=operation_id,
        action="apply",
        project_id=PROJECT_ID,
        revision=13,
        branch=branch,
        expected_head=head,
        patch_sha256=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        patch=patch,
    )
    plan = engine._build_plan(
        patch=patch,
        paths=paths,
        branch=branch,
        expected_head=head,
    )
    receipt = engine._receipt(operation_id, 13, FINGERPRINT, plan)
    engine.journal.transition(
        operation_id,
        "applying",
        apply_receipt={
            "apply_id": receipt.apply_id,
            "revision": receipt.revision,
            "snapshot_fingerprint": receipt.snapshot_fingerprint,
            "files": [
                {
                    "path": item.path,
                    "existed_before": item.existed_before,
                    "before_sha256": item.before_sha256,
                    "after_sha256": item.after_sha256,
                }
                for item in receipt.files
            ],
            "applied_at": receipt.applied_at,
        },
    )
    transaction = engine._transaction(
        plan,
        action="apply",
        branch=branch,
        expected_head=head,
        operation_id=operation_id,
        created_directories=(),
    )
    files = _transaction_files(plan)
    transaction.prepare(files)
    item = files[0]
    _move_verified_no_replace(
        root / item.path,
        transaction.tx_dir / f"before-{item.key}",
        item.before,
    )
    _move_verified_no_replace(
        transaction.tx_dir / f"after-{item.key}",
        root / item.path,
        item.after,
    )
    replacement = root / "same-content-replacement.tmp"
    replacement.write_bytes(item.after)
    os.replace(replacement, root / item.path)

    state, restored = engine.reconcile_apply(
        operation_id=record.operation_id,
        snapshot_fingerprint=FINGERPRINT,
    )

    assert (state, restored) == ("conflict", None)
    assert (root / item.path).read_bytes() == item.after
    assert engine.journal.get(operation_id).state == "conflict"


def test_directory_intent_is_durable_before_worktree_publish(tmp_path: Path) -> None:
    root, branch, head = _repository(tmp_path, {"base.txt": b"base\n"})
    patch, paths = _patch(("nested/deep/new.txt", None, b"durable-dir-t6p3\n"))
    operation_id = "apply_v13_dir_intent_t6p3"
    engine = _engine(tmp_path, root)
    record = engine.journal.create(
        operation_id=operation_id,
        action="apply",
        project_id=PROJECT_ID,
        revision=16,
        branch=branch,
        expected_head=head,
        patch_sha256=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        patch=patch,
    )
    plan = engine._build_plan(
        patch=patch,
        paths=paths,
        branch=branch,
        expected_head=head,
    )
    receipt = engine._receipt(operation_id, 16, FINGERPRINT, plan)
    engine.journal.transition(
        operation_id,
        "applying",
        apply_receipt={
            "apply_id": receipt.apply_id,
            "revision": receipt.revision,
            "snapshot_fingerprint": receipt.snapshot_fingerprint,
            "files": [
                {
                    "path": item.path,
                    "existed_before": item.existed_before,
                    "before_sha256": item.before_sha256,
                    "after_sha256": item.after_sha256,
                }
                for item in receipt.files
            ],
            "applied_at": receipt.applied_at,
        },
    )
    transaction_root = root / ".git" / "modelmirror-transactions"
    transaction_root.mkdir()
    staged = engine._prepare_created_directories(plan, operation_id, ())

    assert staged
    assert not (root / "nested").exists()
    assert all(
        engine._created_directory_stage(
            operation_id,
            value.split(":", 1)[1],
        ).is_dir()
        for value in staged
    )

    state, recovered = _engine(tmp_path, root).reconcile_apply(
        operation_id=record.operation_id,
        snapshot_fingerprint=FINGERPRINT,
    )

    assert (state, recovered) == ("not_applied", None)
    assert not (root / "nested").exists()
    assert _git(root, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert not any(
        path.name.endswith(".dir-stage")
        for path in transaction_root.iterdir()
    )


def test_failed_nested_apply_removes_owned_directories(tmp_path: Path) -> None:
    root, branch, head = _repository(tmp_path, {"base.txt": b"base\n"})
    patch, paths = _patch(("nested/deep/new.txt", None, b"never-written-q8m4\n"))

    def stop_before_write(_action: str, _index: int, _path: str) -> None:
        raise RuntimeError("stop-before-write")

    engine = _engine(tmp_path, root, hook=stop_before_write)
    with pytest.raises(RuntimeError, match="stop-before-write"):
        engine.apply(
            operation_id="apply_v13_nested_cleanup_q8m4",
            revision=14,
            branch=branch,
            expected_head=head,
            snapshot_fingerprint=FINGERPRINT,
            patch=patch,
            paths=paths,
        )

    assert not (root / "nested").exists()
    assert _git(root, "status", "--porcelain=v1", "--untracked-files=all") == ""
    stored = engine.journal.get("apply_v13_nested_cleanup_q8m4")
    assert stored is not None and stored.created_directories == ()


def test_directory_limit_fails_before_touching_project(tmp_path: Path) -> None:
    root, branch, head = _repository(tmp_path, {"base.txt": b"base\n"})
    relative = "/".join([f"d{index:02d}" for index in range(65)] + ["new.txt"])
    patch, paths = _patch((relative, None, b"too-deep-r9k2\n"))
    engine = _engine(tmp_path, root)

    with pytest.raises(HostApplyError) as rejected:
        engine.apply(
            operation_id="apply_v13_too_deep_r9k2",
            revision=15,
            branch=branch,
            expected_head=head,
            snapshot_fingerprint=FINGERPRINT,
            patch=patch,
            paths=paths,
        )

    assert rejected.value.code == "too_many_created_directories"
    assert not (root / "d00").exists()
    assert _git(root, "status", "--porcelain=v1", "--untracked-files=all") == ""


def test_project_operation_lock_rejects_hardlink_without_writing_it(
    tmp_path: Path,
) -> None:
    root, branch, head = _repository(tmp_path, {"marker.txt": b"old\n"})
    patch, paths = _patch(("marker.txt", b"old\n", b"blocked-lock-c8v2\n"))
    transaction_root = root / ".git" / "modelmirror-transactions"
    transaction_root.mkdir()
    outside = tmp_path / "manual-lock-target.bin"
    outside.write_bytes(b"")
    os.link(outside, transaction_root / ".project-operation.lock")
    engine = _engine(tmp_path, root)

    with pytest.raises(HostApplyError) as blocked:
        engine.apply(
            operation_id="apply_v13_lock_hardlink_c8v2",
            revision=18,
            branch=branch,
            expected_head=head,
            snapshot_fingerprint=FINGERPRINT,
            patch=patch,
            paths=paths,
        )

    assert blocked.value.code == "transaction_conflict"
    assert outside.read_bytes() == b""
    assert (root / "marker.txt").read_bytes() == b"old\n"


def test_commit_marker_recovers_side_effect_before_journal_receipt(tmp_path: Path) -> None:
    root, branch, head = _repository(tmp_path, {"marker.txt": b"old\n"})
    patch, paths = _patch(("marker.txt", b"old\n", b"new-k9r4\n"))
    engine = _engine(tmp_path, root)
    record = engine.journal.create(
        operation_id="apply_v13_marker_k9r4",
        action="apply",
        project_id=PROJECT_ID,
        revision=10,
        branch=branch,
        expected_head=head,
        patch_sha256=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        patch=patch,
    )
    plan = engine._build_plan(
        patch=patch,
        paths=paths,
        branch=branch,
        expected_head=head,
    )
    receipt = engine._receipt(record.operation_id, 10, FINGERPRINT, plan)
    engine.journal.transition(
        record.operation_id,
        "applying",
        apply_receipt={
            "apply_id": receipt.apply_id,
            "revision": receipt.revision,
            "snapshot_fingerprint": receipt.snapshot_fingerprint,
            "files": [
                {
                    "path": item.path,
                    "existed_before": item.existed_before,
                    "before_sha256": item.before_sha256,
                    "after_sha256": item.after_sha256,
                }
                for item in receipt.files
            ],
            "applied_at": receipt.applied_at,
        },
    )
    transaction = engine._transaction(
        plan,
        action="apply",
        branch=branch,
        expected_head=head,
        operation_id=record.operation_id,
        created_directories=(),
    )
    with pytest.raises(RuntimeError, match="lost acknowledgement"):
        transaction.apply(
            _transaction_files(plan),
            commit_callback=lambda: (_ for _ in ()).throw(
                RuntimeError("lost acknowledgement")
            ),
        )

    state, restored = _engine(tmp_path, root).reconcile_apply(
        operation_id=record.operation_id,
        snapshot_fingerprint=FINGERPRINT,
    )
    assert state == "applied"
    assert restored == receipt
    assert (root / "marker.txt").read_bytes() == b"new-k9r4\n"
    assert not transaction.tx_dir.exists()


def test_reconcile_recovers_after_directory_markers_were_finalized(
    tmp_path: Path,
) -> None:
    root, branch, head = _repository(tmp_path, {"base.txt": b"base\n"})
    patch, paths = _patch(("nested/deep/new.txt", None, b"new-d7p4\n"))
    operation_id = "apply_v13_finalized_dirs_d7p4"
    engine = _engine(tmp_path, root)
    record = engine.journal.create(
        operation_id=operation_id,
        action="apply",
        project_id=PROJECT_ID,
        revision=21,
        branch=branch,
        expected_head=head,
        patch_sha256=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        patch=patch,
    )
    plan = engine._build_plan(
        patch=patch,
        paths=paths,
        branch=branch,
        expected_head=head,
    )
    receipt = engine._receipt(operation_id, 21, FINGERPRINT, plan)
    engine.journal.transition(
        operation_id,
        "applying",
        apply_receipt={
            "apply_id": receipt.apply_id,
            "revision": receipt.revision,
            "snapshot_fingerprint": receipt.snapshot_fingerprint,
            "files": [
                {
                    "path": item.path,
                    "existed_before": item.existed_before,
                    "before_sha256": item.before_sha256,
                    "after_sha256": item.after_sha256,
                }
                for item in receipt.files
            ],
            "applied_at": receipt.applied_at,
        },
    )
    (root / ".git" / "modelmirror-transactions").mkdir()
    created = engine._prepare_created_directories(plan, operation_id, ())
    engine.journal.transition(
        operation_id,
        "applying",
        created_directories=created,
    )
    engine._publish_created_directories(created, operation_id)
    transaction = engine._transaction(
        plan,
        action="apply",
        branch=branch,
        expected_head=head,
        operation_id=operation_id,
        created_directories=created,
    )

    def crash_after_marker_cleanup() -> None:
        engine._finalize_created_directories(created, operation_id)
        raise RuntimeError("crash-after-directory-marker-cleanup")

    with pytest.raises(RuntimeError, match="crash-after-directory-marker-cleanup"):
        transaction.apply(
            _transaction_files(plan),
            commit_callback=crash_after_marker_cleanup,
        )

    assert engine.journal.get(operation_id).state == "applying"
    assert transaction.active_dir.is_dir()
    assert (root / "nested/deep/new.txt").read_bytes() == b"new-d7p4\n"
    for directory in (root / "nested", root / "nested/deep"):
        assert not (directory / f".modelmirror-{operation_id}.dir-owner").exists()

    restarted = _engine(tmp_path, root)
    state, recovered = restarted.reconcile_apply(
        operation_id=operation_id,
        snapshot_fingerprint=FINGERPRINT,
    )

    assert (state, recovered) == ("applied", receipt)
    assert restarted.journal.get(operation_id).state == "applied"
    assert (root / "nested/deep/new.txt").read_bytes() == b"new-d7p4\n"
    assert not transaction.active_dir.exists()
    assert not transaction.cleanup_dir.exists()


def test_applied_retry_seals_unsealed_transaction_and_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, branch, head = _repository(tmp_path, {"marker.txt": b"old\n"})
    patch, paths = _patch(("marker.txt", b"old\n", b"new-s9q2\n"))
    operation_id = "apply_v13_unsealed_applied_s9q2"
    engine = _engine(tmp_path, root)
    record = engine.journal.create(
        operation_id=operation_id,
        action="apply",
        project_id=PROJECT_ID,
        revision=22,
        branch=branch,
        expected_head=head,
        patch_sha256=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        patch=patch,
    )
    plan = engine._build_plan(
        patch=patch,
        paths=paths,
        branch=branch,
        expected_head=head,
    )
    receipt = engine._receipt(operation_id, 22, FINGERPRINT, plan)
    engine.journal.transition(
        record.operation_id,
        "applying",
        apply_receipt={
            "apply_id": receipt.apply_id,
            "revision": receipt.revision,
            "snapshot_fingerprint": receipt.snapshot_fingerprint,
            "files": [
                {
                    "path": item.path,
                    "existed_before": item.existed_before,
                    "before_sha256": item.before_sha256,
                    "after_sha256": item.after_sha256,
                }
                for item in receipt.files
            ],
            "applied_at": receipt.applied_at,
        },
    )
    transaction = engine._transaction(
        plan,
        action="apply",
        branch=branch,
        expected_head=head,
        operation_id=operation_id,
        created_directories=(),
    )

    def crash_before_seal(_files: object) -> None:
        raise RuntimeError("crash-before-transaction-seal")

    monkeypatch.setattr(transaction, "_seal_transaction", crash_before_seal)
    with pytest.raises(RuntimeError, match="crash-before-transaction-seal"):
        transaction.apply(
            _transaction_files(plan),
            commit_callback=lambda: engine._finish_apply(operation_id, receipt),
        )

    assert engine.journal.get(operation_id).state == "applied"
    assert transaction.active_dir.is_dir()
    assert not transaction.cleanup_dir.exists()
    assert (root / "marker.txt").read_bytes() == b"new-s9q2\n"

    restarted = _engine(tmp_path, root)
    recovered = restarted.apply(
        operation_id=operation_id,
        revision=22,
        branch=branch,
        expected_head=head,
        snapshot_fingerprint=FINGERPRINT,
        patch=patch,
        paths=paths,
    )
    repeated = restarted.apply(
        operation_id=operation_id,
        revision=22,
        branch=branch,
        expected_head=head,
        snapshot_fingerprint=FINGERPRINT,
        patch=patch,
        paths=paths,
    )

    assert recovered == receipt
    assert repeated == receipt
    assert restarted.journal.get(operation_id).state == "applied"
    assert (root / "marker.txt").read_bytes() == b"new-s9q2\n"
    assert not transaction.active_dir.exists()
    assert not transaction.cleanup_dir.exists()


def test_concurrent_target_save_is_never_overwritten(tmp_path: Path) -> None:
    root, branch, head = _repository(tmp_path, {"marker.txt": b"old\n"})
    patch, paths = _patch(("marker.txt", b"old\n", b"agent-change\n"))

    def human_save(_action: str, index: int, path: str) -> None:
        if index == 0:
            (root / path).write_bytes(b"human-save-q4m8\n")

    engine = _engine(tmp_path, root, hook=human_save)
    with pytest.raises(HostApplyError) as conflict:
        engine.apply(
            operation_id="apply_v13_human_q4m8",
            revision=11,
            branch=branch,
            expected_head=head,
            snapshot_fingerprint=FINGERPRINT,
            patch=patch,
            paths=paths,
        )
    assert conflict.value.code == "transaction_rollback_failed"
    assert (root / "marker.txt").read_bytes() == b"human-save-q4m8\n"
    stored = engine.journal.get("apply_v13_human_q4m8")
    assert stored is not None and stored.state == "conflict"


def test_applying_record_without_transaction_marker_cannot_claim_manual_result(
    tmp_path: Path,
) -> None:
    root, branch, head = _repository(tmp_path, {"marker.txt": b"old\n"})
    patch, paths = _patch(("marker.txt", b"old\n", b"same-looking\n"))
    engine = _engine(tmp_path, root)
    record = engine.journal.create(
        operation_id="apply_v13_no_evidence_m8q2",
        action="apply",
        project_id=PROJECT_ID,
        revision=12,
        branch=branch,
        expected_head=head,
        patch_sha256=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        patch=patch,
    )
    plan = engine._build_plan(
        patch=patch,
        paths=paths,
        branch=branch,
        expected_head=head,
    )
    receipt = engine._receipt(record.operation_id, 12, FINGERPRINT, plan)
    engine.journal.transition(
        record.operation_id,
        "applying",
        apply_receipt={
            "apply_id": receipt.apply_id,
            "revision": receipt.revision,
            "snapshot_fingerprint": receipt.snapshot_fingerprint,
            "files": [
                {
                    "path": item.path,
                    "existed_before": item.existed_before,
                    "before_sha256": item.before_sha256,
                    "after_sha256": item.after_sha256,
                }
                for item in receipt.files
            ],
            "applied_at": receipt.applied_at,
        },
    )
    (root / "marker.txt").write_bytes(b"same-looking\n")

    with pytest.raises(HostApplyError) as conflict:
        engine.apply(
            operation_id=record.operation_id,
            revision=12,
            branch=branch,
            expected_head=head,
            snapshot_fingerprint=FINGERPRINT,
            patch=patch,
            paths=paths,
        )
    assert conflict.value.code in {
        "apply_conflict",
        "transaction_evidence_missing",
    }
    assert (root / "marker.txt").read_bytes() == b"same-looking\n"


def test_revert_retry_recovers_mixed_crash_state(tmp_path: Path) -> None:
    root, branch, head = _repository(
        tmp_path,
        {"a.txt": b"old-a\n", "b.txt": b"old-b\n"},
    )
    patch, paths = _patch(
        ("a.txt", b"old-a\n", b"new-a\n"),
        ("b.txt", b"old-b\n", b"new-b\n"),
    )
    engine = _engine(tmp_path, root)
    receipt = engine.apply(
        operation_id="apply_v13_mixed_n7m2",
        revision=7,
        branch=branch,
        expected_head=head,
        snapshot_fingerprint=FINGERPRINT,
        patch=patch,
        paths=paths,
    )
    revert_id = "revert_v13_mixed_n7m2"
    applied = engine.journal.get(receipt.apply_id)
    assert applied is not None
    receipt_payload = applied.apply_receipt
    engine.journal.create(
        operation_id=revert_id,
        action="revert",
        project_id=PROJECT_ID,
        revision=receipt.revision,
        branch=branch,
        expected_head=head,
        patch_sha256=applied.patch_sha256,
        patch=patch,
        apply_receipt=receipt_payload,
        file_identities=applied.file_identities,
    )
    engine.journal.transition(revert_id, "reverting")
    plan = engine._build_plan(
        patch=patch,
        paths=paths,
        branch=branch,
        expected_head=head,
        apply_receipt=receipt,
    )
    inverse = tuple(
        type(item)(item.path, item.after, item.before, item.mode) for item in plan
    )
    transaction = engine._transaction(
        inverse,
        action="revert",
        branch=branch,
        expected_head=head,
        operation_id=revert_id,
        created_directories=(),
    )
    files = _transaction_files(inverse)
    transaction.prepare(files)
    item = files[0]
    _move_verified_no_replace(
        root / item.path,
        transaction.tx_dir / f"before-{item.key}",
        item.before,
    )
    _move_verified_no_replace(
        transaction.tx_dir / f"after-{item.key}",
        root / item.path,
        item.after,
    )

    engine.revert(
        operation_id=revert_id,
        apply_receipt=receipt,
        branch=branch,
        expected_head=head,
    )
    assert (root / "a.txt").read_bytes() == b"old-a\n"
    assert (root / "b.txt").read_bytes() == b"old-b\n"


def test_revert_without_transaction_evidence_rejects_same_content_replacement(
    tmp_path: Path,
) -> None:
    root, branch, head = _repository(tmp_path, {"marker.txt": b"old\n"})
    patch, paths = _patch(("marker.txt", b"old\n", b"applied-r6n3\n"))
    engine = _engine(tmp_path, root)
    receipt = engine.apply(
        operation_id="apply_v13_revert_identity_r6n3",
        revision=17,
        branch=branch,
        expected_head=head,
        snapshot_fingerprint=FINGERPRINT,
        patch=patch,
        paths=paths,
    )
    applied = engine.journal.get(receipt.apply_id)
    assert applied is not None
    revert_id = "revert_v13_revert_identity_r6n3"
    engine.journal.create(
        operation_id=revert_id,
        action="revert",
        project_id=PROJECT_ID,
        revision=receipt.revision,
        branch=branch,
        expected_head=head,
        patch_sha256=applied.patch_sha256,
        patch=patch,
        apply_receipt=applied.apply_receipt,
        file_identities=applied.file_identities,
    )
    engine.journal.transition(revert_id, "reverting")
    target = root / "marker.txt"
    target.unlink()
    target.write_bytes(b"applied-r6n3\n")

    with pytest.raises(HostApplyError) as conflict:
        engine.revert(
            operation_id=revert_id,
            apply_receipt=receipt,
            branch=branch,
            expected_head=head,
        )

    assert conflict.value.code == "revert_conflict"
    assert target.read_bytes() == b"applied-r6n3\n"
    assert engine.journal.get(revert_id).state == "conflict"


def test_revert_refuses_external_change_without_overwriting_it(tmp_path: Path) -> None:
    root, branch, head = _repository(tmp_path, {"marker.txt": b"old\n"})
    patch, paths = _patch(("marker.txt", b"old\n", b"agent-change\n"))
    engine = _engine(tmp_path, root)
    receipt = engine.apply(
        operation_id="apply_v13_conflict_n7m2",
        revision=2,
        branch=branch,
        expected_head=head,
        snapshot_fingerprint=FINGERPRINT,
        patch=patch,
        paths=paths,
    )
    (root / "marker.txt").write_text("human-change-q9t2\n", encoding="utf-8")
    with pytest.raises(HostApplyError) as conflict:
        engine.revert(
            operation_id="revert_v13_conflict_n7m2",
            apply_receipt=receipt,
            branch=branch,
            expected_head=head,
        )
    assert conflict.value.code == "revert_conflict"
    assert (root / "marker.txt").read_text(encoding="utf-8") == "human-change-q9t2\n"


def test_revert_failure_restores_fully_applied_state(tmp_path: Path) -> None:
    root, branch, head = _repository(
        tmp_path,
        {"a.txt": b"old-a\n", "b.txt": b"old-b\n"},
    )
    patch, paths = _patch(
        ("a.txt", b"old-a\n", b"new-a\n"),
        ("b.txt", b"old-b\n", b"new-b\n"),
    )
    engine = _engine(tmp_path, root)
    receipt = engine.apply(
        operation_id="apply_v13_revertfail_p8r2",
        revision=6,
        branch=branch,
        expected_head=head,
        snapshot_fingerprint=FINGERPRINT,
        patch=patch,
        paths=paths,
    )

    def fail_second(_action: str, index: int, _path: str) -> None:
        if index == 1:
            raise RuntimeError("simulated revert interruption")

    engine.mutation_hook = fail_second
    with pytest.raises(RuntimeError, match="simulated revert interruption"):
        engine.revert(
            operation_id="revert_v13_failure_p8r2",
            apply_receipt=receipt,
            branch=branch,
            expected_head=head,
        )
    assert (root / "a.txt").read_bytes() == b"new-a\n"
    assert (root / "b.txt").read_bytes() == b"new-b\n"


def test_hard_safety_policy_rejects_secret_symlink_and_branch_change(tmp_path: Path) -> None:
    root, branch, head = _repository(tmp_path, {"safe.txt": b"safe\n"})
    engine = _engine(tmp_path, root)
    simulated_secret = ("s" + "k-" + "abcdefghijklmnopqrstuvwxyz123456").encode()
    secret_patch, secret_paths = _patch(
        ("safe.txt", b"safe\n", b"token = '" + simulated_secret + b"'\n")
    )
    with pytest.raises(HostApplyError) as secret:
        engine.apply(
            operation_id="apply_v13_secret_q7m4",
            revision=1,
            branch=branch,
            expected_head=head,
            snapshot_fingerprint=FINGERPRINT,
            patch=secret_patch,
            paths=secret_paths,
        )
    assert secret.value.code == "secret_detected"
    assert (root / "safe.txt").read_bytes() == b"safe\n"

    if hasattr(os, "symlink"):
        outside = tmp_path / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        link = root / "link.txt"
        try:
            link.symlink_to(outside)
        except OSError:
            pass
        else:
            link_patch, link_paths = _patch(("link.txt", None, b"changed\n"))
            with pytest.raises(HostApplyError) as linked:
                engine.apply(
                    operation_id="apply_v13_symlink_q7m4",
                    revision=1,
                    branch=branch,
                    expected_head=head,
                    snapshot_fingerprint=FINGERPRINT,
                    patch=link_patch,
                    paths=link_paths,
                )
            assert linked.value.code in {"symlink_not_allowed", "target_changed"}
            assert outside.read_text(encoding="utf-8") == "outside\n"
            link.unlink()

    _git(root, "switch", "-c", "feature/other-r4m8")
    patch, paths = _patch(("safe.txt", b"safe\n", b"changed\n"))
    with pytest.raises(HostApplyError) as changed:
        engine.apply(
            operation_id="apply_v13_branch_q7m4",
            revision=1,
            branch=branch,
            expected_head=head,
            snapshot_fingerprint=FINGERPRINT,
            patch=patch,
            paths=paths,
        )
    assert changed.value.code == "branch_changed"


def test_crlf_worktree_style_is_preserved(tmp_path: Path) -> None:
    root, branch, head = _repository(tmp_path, {"crlf.txt": b"one\ntwo\n"})
    (root / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")
    patch, paths = _patch(("crlf.txt", b"one\ntwo\n", b"one\nthree\n"))
    engine = _engine(tmp_path, root)
    receipt = engine.apply(
        operation_id="apply_v13_crlf_k8m2x",
        revision=5,
        branch=branch,
        expected_head=head,
        snapshot_fingerprint=FINGERPRINT,
        patch=patch,
        paths=paths,
    )
    assert (root / "crlf.txt").read_bytes() == b"one\r\nthree\r\n"
    engine.revert(
        operation_id="revert_v13_crlf_k8m2",
        apply_receipt=receipt,
        branch=branch,
        expected_head=head,
    )
    assert (root / "crlf.txt").read_bytes() == b"one\r\ntwo\r\n"


def test_local_include_configuration_is_rejected(tmp_path: Path) -> None:
    root, branch, head = _repository(tmp_path, {"safe.txt": b"safe\n"})
    _git(root, "config", "include.path", str(tmp_path / "untrusted.gitconfig"))
    patch, paths = _patch(("safe.txt", b"safe\n", b"changed\n"))
    with pytest.raises(HostApplyError) as unsafe:
        _engine(tmp_path, root).apply(
            operation_id="apply_v13_include_p8r2",
            revision=1,
            branch=branch,
            expected_head=head,
            snapshot_fingerprint=FINGERPRINT,
            patch=patch,
            paths=paths,
        )
    assert unsafe.value.code == "git_config_unsafe"
    assert (root / "safe.txt").read_bytes() == b"safe\n"


def test_reconcile_cannot_replace_stored_snapshot_fingerprint(tmp_path: Path) -> None:
    root, branch, head = _repository(tmp_path, {"safe.txt": b"safe\n"})
    patch, paths = _patch(("safe.txt", b"safe\n", b"changed\n"))

    def stop_before_write(_action: str, _index: int, _path: str) -> None:
        raise RuntimeError("stop")

    engine = _engine(tmp_path, root, hook=stop_before_write)
    with pytest.raises(RuntimeError, match="stop"):
        engine.apply(
            operation_id="apply_v13_fingerprint_p8r2",
            revision=11,
            branch=branch,
            expected_head=head,
            snapshot_fingerprint=FINGERPRINT,
            patch=patch,
            paths=paths,
        )
    state, receipt = engine.reconcile_apply(
        operation_id="apply_v13_fingerprint_p8r2",
        snapshot_fingerprint="e" * 64,
    )
    assert state == "conflict"
    assert receipt is None
    stored = engine.journal.get("apply_v13_fingerprint_p8r2")
    assert stored is not None
    assert stored.apply_receipt["snapshot_fingerprint"] == FINGERPRINT


def test_applied_retry_rejects_a_different_snapshot_fingerprint(tmp_path: Path) -> None:
    root, branch, head = _repository(tmp_path, {"safe.txt": b"safe\n"})
    patch, paths = _patch(("safe.txt", b"safe\n", b"changed\n"))
    engine = _engine(tmp_path, root)
    engine.apply(
        operation_id="apply_v13_applied_fingerprint",
        revision=13,
        branch=branch,
        expected_head=head,
        snapshot_fingerprint=FINGERPRINT,
        patch=patch,
        paths=paths,
    )
    with pytest.raises(HostApplyError) as conflict:
        engine.apply(
            operation_id="apply_v13_applied_fingerprint",
            revision=13,
            branch=branch,
            expected_head=head,
            snapshot_fingerprint="e" * 64,
            patch=patch,
            paths=paths,
        )
    assert conflict.value.code == "operation_conflict"


def test_git_ref_lock_blocks_concurrent_head_change(tmp_path: Path) -> None:
    root, branch, head = _repository(
        tmp_path,
        {"a.txt": b"old-a\n", "b.txt": b"old-b\n"},
    )
    patch, paths = _patch(
        ("a.txt", b"old-a\n", b"new-a\n"),
        ("b.txt", b"old-b\n", b"new-b\n"),
    )
    tree = _git(root, "write-tree")
    concurrent_head = _git(
        root,
        "commit-tree",
        tree,
        "-p",
        head,
        "-m",
        "concurrent head",
    )

    return_codes: list[int] = []

    def advance_head(_action: str, index: int, _path: str) -> None:
        if index == 1:
            completed = subprocess.run(
                ("git", "update-ref", f"refs/heads/{branch}", concurrent_head, head),
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=15,
            )
            return_codes.append(completed.returncode)

    engine = _engine(tmp_path, root, hook=advance_head)
    engine.apply(
        operation_id="apply_v13_headrace_n7m2",
        revision=12,
        branch=branch,
        expected_head=head,
        snapshot_fingerprint=FINGERPRINT,
        patch=patch,
        paths=paths,
    )
    assert return_codes and all(code != 0 for code in return_codes)
    assert (root / "a.txt").read_bytes() == b"new-a\n"
    assert (root / "b.txt").read_bytes() == b"new-b\n"
    assert _git(root, "rev-parse", "HEAD") == head
