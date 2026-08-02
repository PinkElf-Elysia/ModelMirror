from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from server.coding_applier.engine import CodingApplierEngine
from server.coding_applier.server import CodingApplierServer
from server.coding_committer.engine import CodingCommitterEngine
from server.coding_committer.server import CodingCommitterServer
from server.coding_runtime.apply_models import ApplyFileReceipt, ApplyReceipt
from server.coding_runtime.commit_models import CodingCommitError
from server.coding_runtime.patch_policy import snapshot_fingerprint


APPLY_OPERATION_ID = "apply_recovery_731abcdef"
COMMIT_OPERATION_ID = "c" * 24


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _modified_patch(path: str, old: str, new: str) -> str:
    import difflib

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


@pytest.fixture
def apply_roots(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    source = tmp_path / "source"
    target = tmp_path / "target"
    staging = tmp_path / "staging"
    (source / "server").mkdir(parents=True)
    (source / "server/app.py").write_text("VALUE = 1\n", encoding="utf-8")
    shutil.copytree(source, target)
    (target / ".git").mkdir()
    patch = _modified_patch("server/app.py", "VALUE = 1\n", "VALUE = 731\n")
    return source, target, staging, patch


def test_applier_reconciles_applied_and_reverted_states_after_restart(
    apply_roots: tuple[Path, Path, Path, str],
) -> None:
    source, target, staging, patch = apply_roots
    first = CodingApplierEngine(source, target, staging)
    original = first.apply(
        operation_id=APPLY_OPERATION_ID,
        revision=5,
        patch=patch,
        paths=["server/app.py"],
        expected_fingerprint=first.source_fingerprint,
    )

    restarted = CodingApplierEngine(source, target, staging)
    state, recovered = restarted.reconcile(
        operation_id=APPLY_OPERATION_ID,
        revision=5,
        patch=patch,
        paths=["server/app.py"],
        expected_fingerprint=restarted.source_fingerprint,
    )

    assert state == "applied"
    assert recovered is not None
    assert recovered.apply_id == original.apply_id
    assert recovered.revision == original.revision
    assert recovered.files == original.files
    restarted.revert(recovered)
    after_revert = CodingApplierEngine(source, target, staging)
    state, receipt = after_revert.reconcile(
        operation_id=APPLY_OPERATION_ID,
        revision=5,
        patch=patch,
        paths=["server/app.py"],
        expected_fingerprint=after_revert.source_fingerprint,
    )
    assert state == "not_applied"
    assert receipt is None
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_applier_reconcile_is_idempotent_while_process_stays_alive(
    apply_roots: tuple[Path, Path, Path, str],
) -> None:
    source, target, staging, patch = apply_roots
    engine = CodingApplierEngine(source, target, staging)
    original = engine.apply(
        operation_id=APPLY_OPERATION_ID,
        revision=5,
        patch=patch,
        paths=["server/app.py"],
        expected_fingerprint=engine.source_fingerprint,
    )

    state, recovered = engine.reconcile(
        operation_id=APPLY_OPERATION_ID,
        revision=5,
        patch=patch,
        paths=["server/app.py"],
        expected_fingerprint=engine.source_fingerprint,
    )

    assert state == "applied"
    assert recovered == original
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 731\n"

    engine.revert(original)
    state, recovered = engine.reconcile(
        operation_id=APPLY_OPERATION_ID,
        revision=5,
        patch=patch,
        paths=["server/app.py"],
        expected_fingerprint=engine.source_fingerprint,
    )

    assert state == "not_applied"
    assert recovered is None
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_applier_reconcile_reports_conflict_without_overwriting_external_change(
    apply_roots: tuple[Path, Path, Path, str],
) -> None:
    source, target, staging, patch = apply_roots
    first = CodingApplierEngine(source, target, staging)
    first.apply(
        operation_id=APPLY_OPERATION_ID,
        revision=5,
        patch=patch,
        paths=["server/app.py"],
        expected_fingerprint=first.source_fingerprint,
    )
    (target / "server/app.py").write_text("VALUE = 999\n", encoding="utf-8")

    restarted = CodingApplierEngine(source, target, staging)
    state, receipt = restarted.reconcile(
        operation_id=APPLY_OPERATION_ID,
        revision=5,
        patch=patch,
        paths=["server/app.py"],
        expected_fingerprint=restarted.source_fingerprint,
    )

    assert state == "conflict"
    assert receipt is None
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 999\n"


@pytest.fixture
def commit_repository(tmp_path: Path) -> tuple[Path, Path, Path, ApplyReceipt]:
    source = tmp_path / "source"
    target = tmp_path / "target"
    temporary = tmp_path / "temporary"
    (source / "server").mkdir(parents=True)
    (source / "server/app.py").write_text("VALUE = 1\n", encoding="utf-8")
    shutil.copytree(source, target)
    temporary.mkdir()
    _git(target, "init", "-q")
    _git(target, "config", "user.name", "Fixture")
    _git(target, "config", "user.email", "fixture@example.test")
    _git(target, "add", ".")
    _git(target, "commit", "-qm", "baseline")
    _git(target, "branch", "-M", "coding/local-draft")
    app = target / "server/app.py"
    before = _sha256(app)
    app.write_text("VALUE = 731\n", encoding="utf-8")
    receipt = ApplyReceipt(
        apply_id="a" * 24,
        revision=8,
        snapshot_fingerprint=snapshot_fingerprint(source),
        files=(
            ApplyFileReceipt(
                path="server/app.py",
                existed_before=True,
                before_sha256=before,
                after_sha256=_sha256(app),
            ),
        ),
    )
    return source, target, temporary, receipt


def test_committer_journal_restores_commit_and_undo_across_processes(
    commit_repository: tuple[Path, Path, Path, ApplyReceipt],
) -> None:
    source, target, temporary, apply_receipt = commit_repository
    first = CodingCommitterEngine(source, target, temporary)
    committed = first.commit(
        operation_id=COMMIT_OPERATION_ID,
        apply_receipt=apply_receipt,
        message="feature: 恢复随机样例 731",
    )
    journal = (
        target
        / ".git/modelmirror-coding/operations"
        / f"{COMMIT_OPERATION_ID}.json"
    )
    assert json.loads(journal.read_text(encoding="utf-8"))["state"] == "committed"

    restarted = CodingCommitterEngine(source, target, temporary)
    state, recovered = restarted.reconcile(
        operation_id=COMMIT_OPERATION_ID,
        apply_receipt=apply_receipt,
        message="feature: 恢复随机样例 731",
    )
    assert state == "committed"
    assert recovered == committed
    assert restarted.undo(committed, apply_receipt) == committed

    after_undo = CodingCommitterEngine(source, target, temporary)
    state, recovered = after_undo.reconcile(
        operation_id=COMMIT_OPERATION_ID,
        apply_receipt=apply_receipt,
        message="feature: 恢复随机样例 731",
    )
    assert state == "undone"
    assert recovered == committed
    assert _git(target, "rev-parse", "HEAD") == committed.parent_sha
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 731\n"


def test_prepared_journal_reconciles_both_sides_of_ref_updates(
    commit_repository: tuple[Path, Path, Path, ApplyReceipt],
) -> None:
    source, target, temporary, apply_receipt = commit_repository
    first = CodingCommitterEngine(source, target, temporary)
    receipt = first.commit(
        operation_id=COMMIT_OPERATION_ID,
        apply_receipt=apply_receipt,
        message="feature: 模拟中断 731",
    )
    journal = (
        target
        / ".git/modelmirror-coding/operations"
        / f"{COMMIT_OPERATION_ID}.json"
    )
    payload = json.loads(journal.read_text(encoding="utf-8"))
    payload["state"] = "prepared"
    journal.write_text(json.dumps(payload), encoding="utf-8")

    after_commit_crash = CodingCommitterEngine(source, target, temporary)
    state, _ = after_commit_crash.reconcile(
        operation_id=COMMIT_OPERATION_ID,
        apply_receipt=apply_receipt,
        message="feature: 模拟中断 731",
    )
    assert state == "committed"
    assert json.loads(journal.read_text(encoding="utf-8"))["state"] == "committed"

    after_commit_crash.undo(receipt, apply_receipt)
    payload = json.loads(journal.read_text(encoding="utf-8"))
    payload["state"] = "committed"
    journal.write_text(json.dumps(payload), encoding="utf-8")
    after_undo_crash = CodingCommitterEngine(source, target, temporary)
    state, _ = after_undo_crash.reconcile(
        operation_id=COMMIT_OPERATION_ID,
        apply_receipt=apply_receipt,
        message="feature: 模拟中断 731",
    )
    assert state == "undone"
    assert json.loads(journal.read_text(encoding="utf-8"))["state"] == "undone"


def test_commit_reconcile_conflict_and_tampered_journal_fail_closed(
    commit_repository: tuple[Path, Path, Path, ApplyReceipt],
) -> None:
    source, target, temporary, apply_receipt = commit_repository
    first = CodingCommitterEngine(source, target, temporary)
    receipt = first.commit(
        operation_id=COMMIT_OPERATION_ID,
        apply_receipt=apply_receipt,
        message="feature: 冲突保护 731",
    )
    (target / "server/app.py").write_text("VALUE = 999\n", encoding="utf-8")
    restarted = CodingCommitterEngine(source, target, temporary)

    state, recovered = restarted.reconcile(
        operation_id=COMMIT_OPERATION_ID,
        apply_receipt=apply_receipt,
        message="feature: 冲突保护 731",
    )

    assert state == "conflict"
    assert recovered is None
    assert _git(target, "rev-parse", "HEAD") == receipt.commit_sha
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 999\n"

    journal = (
        target
        / ".git/modelmirror-coding/operations"
        / f"{COMMIT_OPERATION_ID}.json"
    )
    journal.write_text("{tampered", encoding="utf-8")
    with pytest.raises(CodingCommitError) as invalid:
        CodingCommitterEngine(source, target, temporary)
    assert invalid.value.code == "recovery_journal_invalid"
    assert journal.read_text(encoding="utf-8") == "{tampered"
    assert (target / "server/app.py").read_text(encoding="utf-8") == "VALUE = 999\n"


@pytest.mark.asyncio
async def test_reconcile_socket_actions_return_only_state_and_receipt(
    apply_roots: tuple[Path, Path, Path, str],
) -> None:
    source, target, staging, patch = apply_roots
    engine = CodingApplierEngine(source, target, staging)
    server = CodingApplierServer(engine=engine)
    response = await server._dispatch(
        {
            "action": "reconcile",
            "operation_id": APPLY_OPERATION_ID,
            "revision": 5,
            "patch": patch,
            "paths": ["server/app.py"],
            "expected_fingerprint": engine.source_fingerprint,
        }
    )

    assert response == {"state": "not_applied", "receipt": None}


@pytest.mark.asyncio
async def test_commit_reconcile_socket_reports_safe_pre_commit_state(
    commit_repository: tuple[Path, Path, Path, ApplyReceipt],
) -> None:
    source, target, temporary, apply_receipt = commit_repository
    engine = CodingCommitterEngine(source, target, temporary)
    server = CodingCommitterServer(engine=engine)
    response = await server._dispatch(
        {
            "action": "reconcile",
            "operation_id": COMMIT_OPERATION_ID,
            "message": "feature: 尚未提交 731",
            "apply_receipt": {
                "apply_id": apply_receipt.apply_id,
                "revision": apply_receipt.revision,
                "snapshot_fingerprint": apply_receipt.snapshot_fingerprint,
                "applied_at": apply_receipt.applied_at,
                "files": [
                    {
                        "path": item.path,
                        "existed_before": item.existed_before,
                        "before_sha256": item.before_sha256,
                        "after_sha256": item.after_sha256,
                    }
                    for item in apply_receipt.files
                ],
            },
        }
    )

    assert response == {"state": "not_committed", "receipt": None}
