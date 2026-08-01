from __future__ import annotations

import pytest

from server.coding_runtime.commit_models import (
    COMMIT_BRANCH,
    CommitReceipt,
    CommitState,
    normalize_commit_message,
    not_committed_payload,
    suggest_commit_message,
)


APPLY_ID = "a" * 24
COMMIT_SHA = "b" * 40
PARENT_SHA = "c" * 40
TREE_SHA = "d" * 40


def test_commit_message_normalizes_newlines_and_outer_blank_lines() -> None:
    assert normalize_commit_message("\r\nfeature: 更新功能\r\n\r\n原因\r\n") == (
        "feature: 更新功能\n\n原因"
    )


@pytest.mark.parametrize(
    "message",
    [
        "",
        "\n\n",
        "feature: bad\x00message",
        "feature: bad\tmessage",
        f"feature: {'x' * 121}",
        "x" * 2_001,
    ],
)
def test_commit_message_rejects_values_outside_contract(message: str) -> None:
    with pytest.raises(ValueError):
        normalize_commit_message(message)


def test_commit_message_suggestion_uses_change_kind() -> None:
    assert suggest_commit_message(["docs/guide.md", "README.md"]) == (
        "docs: 更新项目说明"
    )
    assert suggest_commit_message(["server/tests/test_api.py"]) == (
        "test: 更新项目检查"
    )
    assert suggest_commit_message(["server/app.py", "docs/guide.md"]) == (
        "feature: 更新项目功能"
    )


def test_commit_receipt_public_payload_hides_paths_and_parent_objects() -> None:
    receipt = CommitReceipt.create(
        revision=7,
        apply_id=APPLY_ID,
        commit_sha=COMMIT_SHA,
        parent_sha=PARENT_SHA,
        tree_sha=TREE_SHA,
        message="feature: 更新功能",
        files=("server/app.py",),
    )

    payload = receipt.to_public()

    assert payload == {
        "revision": 7,
        "state": "committed",
        "commit_id": receipt.commit_id,
        "commit_sha": COMMIT_SHA,
        "short_sha": COMMIT_SHA[:12],
        "branch": COMMIT_BRANCH,
        "message": "feature: 更新功能",
        "committed_at": receipt.committed_at,
        "file_count": 1,
        "can_undo": True,
    }
    assert "server/app.py" not in repr(payload)
    assert PARENT_SHA not in repr(payload)
    assert receipt.to_public(state=CommitState.UNDONE)["can_undo"] is False


def test_commit_receipt_rejects_invalid_identity_and_paths() -> None:
    with pytest.raises(ValueError):
        CommitReceipt.create(
            revision=1,
            apply_id="bad",
            commit_sha=COMMIT_SHA,
            parent_sha=PARENT_SHA,
            tree_sha=TREE_SHA,
            message="feature: 更新功能",
            files=("server/app.py",),
        )
    with pytest.raises(ValueError):
        CommitReceipt.create(
            revision=1,
            apply_id=APPLY_ID,
            commit_sha=COMMIT_SHA,
            parent_sha=PARENT_SHA,
            tree_sha=TREE_SHA,
            message="feature: 更新功能",
            files=("../outside.py",),
        )
    with pytest.raises(ValueError):
        CommitReceipt.create(
            revision=1,
            apply_id=APPLY_ID,
            commit_sha=COMMIT_SHA,
            parent_sha=PARENT_SHA,
            tree_sha=TREE_SHA,
            message="feature: 更新功能",
            files=("server/app.py", "server/app.py"),
        )
    with pytest.raises(ValueError):
        CommitReceipt.create(
            revision=1,
            apply_id=APPLY_ID,
            commit_sha=COMMIT_SHA,
            parent_sha=PARENT_SHA,
            tree_sha=TREE_SHA,
            message="feature: 更新功能",
            files=("server\\app.py",),
        )


def test_not_committed_payload_contains_only_safe_defaults() -> None:
    assert not_committed_payload(
        2,
        suggested_message="docs: 更新项目说明",
    ) == {
        "revision": 2,
        "state": "not_committed",
        "commit_id": None,
        "commit_sha": None,
        "short_sha": None,
        "branch": COMMIT_BRANCH,
        "message": None,
        "suggested_message": "docs: 更新项目说明",
        "committed_at": None,
        "file_count": 0,
        "can_undo": False,
        "reason": None,
    }
