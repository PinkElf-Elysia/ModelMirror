from __future__ import annotations

import pytest

from server.coding_runtime.apply_models import (
    ApplyFileReceipt,
    ApplyReceipt,
    ApplyState,
    not_applied_payload,
)


FINGERPRINT = "a" * 64
BEFORE_HASH = "b" * 64
AFTER_HASH = "c" * 64


def test_apply_receipt_public_payload_is_content_free() -> None:
    receipt = ApplyReceipt.create(
        revision=4,
        snapshot_fingerprint=FINGERPRINT,
        files=(
            ApplyFileReceipt(
                path="server/app.py",
                existed_before=True,
                before_sha256=BEFORE_HASH,
                after_sha256=AFTER_HASH,
            ),
        ),
    )

    payload = receipt.to_public()

    assert payload == {
        "revision": 4,
        "state": "applied",
        "apply_id": receipt.apply_id,
        "applied_at": receipt.applied_at,
        "file_count": 1,
        "can_revert": True,
    }
    assert "server/app.py" not in repr(payload)
    assert receipt.to_public(state=ApplyState.REVERTED)["can_revert"] is False


def test_apply_receipt_rejects_paths_and_hashes_outside_contract() -> None:
    with pytest.raises(ValueError):
        ApplyFileReceipt(
            path="../outside.py",
            existed_before=True,
            before_sha256=BEFORE_HASH,
            after_sha256=AFTER_HASH,
        )
    with pytest.raises(ValueError):
        ApplyFileReceipt(
            path="server/app.py",
            existed_before=False,
            before_sha256=BEFORE_HASH,
            after_sha256=AFTER_HASH,
        )
    with pytest.raises(ValueError):
        ApplyFileReceipt(
            path="server/app.py",
            existed_before=True,
            before_sha256="invalid",
            after_sha256=AFTER_HASH,
        )


def test_apply_receipt_represents_deletion_without_an_after_hash() -> None:
    deleted = ApplyFileReceipt(
        path="docs/remove-q7m4.txt",
        existed_before=True,
        before_sha256=BEFORE_HASH,
        after_sha256=None,
    )

    assert deleted.after_sha256 is None
    with pytest.raises(ValueError):
        ApplyFileReceipt(
            path="docs/never-existed.txt",
            existed_before=False,
            before_sha256=None,
            after_sha256=None,
        )


def test_apply_receipt_requires_unique_sorted_files() -> None:
    first = ApplyFileReceipt(
        path="server/a.py",
        existed_before=True,
        before_sha256=BEFORE_HASH,
        after_sha256=AFTER_HASH,
    )
    second = ApplyFileReceipt(
        path="client/b.ts",
        existed_before=False,
        before_sha256=None,
        after_sha256=AFTER_HASH,
    )

    with pytest.raises(ValueError):
        ApplyReceipt.create(
            revision=1,
            snapshot_fingerprint=FINGERPRINT,
            files=(first, first),
        )
    with pytest.raises(ValueError):
        ApplyReceipt.create(
            revision=1,
            snapshot_fingerprint=FINGERPRINT,
            files=(first, second),
        )


def test_not_applied_payload_has_no_receipt() -> None:
    assert not_applied_payload(3) == {
        "revision": 3,
        "state": "not_applied",
        "apply_id": None,
        "applied_at": None,
        "file_count": 0,
        "can_revert": False,
    }
