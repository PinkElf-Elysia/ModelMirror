from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.coding_runtime.recovery import (
    CodingRecoveryError,
    CodingRecoveryStore,
    RecoveryPayload,
    RecoveryState,
)


FINGERPRINT = "a" * 64
RECOVERY_ID = "r" * 24
PATCH = """diff --git a/docs/recovery.txt b/docs/recovery.txt
new file mode 100644
--- /dev/null
+++ b/docs/recovery.txt
@@ -0,0 +1 @@
+恢复标记 alpha-731
"""


def _changes(*, validation_status: str = "passed") -> dict[str, object]:
    return {
        "revision": 3,
        "files": [
            {
                "path": "docs/recovery.txt",
                "status": "added",
                "additions": 1,
                "deletions": 0,
            }
        ],
        "file_count": 1,
        "additions": 1,
        "deletions": 0,
        "patch_bytes": len(PATCH.encode("utf-8")),
        "validation_status": validation_status,
        "can_download": validation_status == "passed",
        "checks": [
            {
                "id": "diff_integrity",
                "label": "修改内容",
                "status": "passed",
                "message": "修改内容完整。",
            }
        ],
    }


def _payload(**overrides: object) -> RecoveryPayload:
    values: dict[str, object] = {
        "patch": PATCH,
        "changes": _changes(),
        "verification": {
            "state": "completed",
            "result": "passed",
            "details": "固定检查通过。",
        },
        "apply": None,
        "commit": None,
        "operation": None,
    }
    values.update(overrides)
    return RecoveryPayload(**values)  # type: ignore[arg-type]


def _record(store: CodingRecoveryStore, *, recovery_id: str = RECOVERY_ID):
    return store.create_record(
        recovery_id=recovery_id,
        state=RecoveryState.DRAFT,
        revision=3,
        snapshot_fingerprint=FINGERPRINT,
        payload=_payload(),
    )


def test_store_round_trip_encrypts_sensitive_payload(tmp_path: Path) -> None:
    now = [1_000.0]
    store = CodingRecoveryStore(tmp_path, clock=lambda: now[0])

    saved = store.save(_record(store))
    loaded = store.load()

    assert loaded == saved
    database_bytes = store.database_path.read_bytes()
    assert PATCH.encode("utf-8") not in database_bytes
    assert "恢复标记 alpha-731".encode("utf-8") not in database_bytes
    assert store.master_key_path.read_bytes().strip() not in database_bytes
    assert loaded is not None
    assert loaded.to_public() == {
        "pending": True,
        "state": "draft",
        "revision": 3,
        "file_count": 1,
        "updated_at": 1_000.0,
        "expires_at": 605_800.0,
        "can_resume": True,
        "can_download": True,
        "reason": None,
    }


def test_store_keeps_only_one_record_and_supports_compare_discard(tmp_path: Path) -> None:
    store = CodingRecoveryStore(tmp_path)
    first = _record(store)
    second = _record(store, recovery_id="s" * 24)

    store.save(first)
    store.save(second)

    assert store.load() == second
    assert store.discard(recovery_id=first.recovery_id) is False
    assert store.load() == second
    assert store.discard(recovery_id=second.recovery_id) is True
    assert store.discard() is False


def test_store_expires_record_without_touching_external_files(tmp_path: Path) -> None:
    now = [2_000.0]
    external = tmp_path / "external.txt"
    external.write_text("keep-me", encoding="utf-8")
    store = CodingRecoveryStore(
        tmp_path / "storage",
        retention_seconds=60,
        clock=lambda: now[0],
    )
    store.save(_record(store))

    now[0] = 2_061.0

    assert store.load() is None
    assert external.read_text(encoding="utf-8") == "keep-me"


def test_existing_database_without_key_fails_closed(tmp_path: Path) -> None:
    store = CodingRecoveryStore(tmp_path)
    store.save(_record(store))
    original_database = store.database_path.read_bytes()
    store.master_key_path.unlink()

    with pytest.raises(CodingRecoveryError) as error:
        CodingRecoveryStore(tmp_path)

    assert error.value.code == "recovery_key_missing"
    assert store.database_path.read_bytes() == original_database
    assert not store.master_key_path.exists()


def test_wrong_key_and_tampered_ciphertext_do_not_delete_record(tmp_path: Path) -> None:
    store = CodingRecoveryStore(tmp_path)
    store.save(_record(store))
    with sqlite3.connect(store.database_path) as connection:
        original = connection.execute(
            "SELECT payload_ciphertext FROM coding_recovery WHERE slot = 1"
        ).fetchone()[0]
        connection.execute(
            "UPDATE coding_recovery SET payload_ciphertext = ? WHERE slot = 1",
            (original[:-1] + ("A" if original[-1] != "A" else "B"),),
        )

    with pytest.raises(CodingRecoveryError) as tampered:
        store.load()
    assert tampered.value.code == "recovery_data_corrupt"
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM coding_recovery").fetchone()[0] == 1

    store.master_key_path.write_bytes(Fernet.generate_key())
    wrong_key_store = CodingRecoveryStore(tmp_path)
    with pytest.raises(CodingRecoveryError) as wrong_key:
        wrong_key_store.load()
    assert wrong_key.value.code == "recovery_data_corrupt"


def test_metadata_tampering_is_detected(tmp_path: Path) -> None:
    store = CodingRecoveryStore(tmp_path)
    store.save(_record(store))
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE coding_recovery SET revision = 4 WHERE slot = 1"
        )

    with pytest.raises(CodingRecoveryError) as error:
        store.load()

    assert error.value.code == "recovery_data_corrupt"


@pytest.mark.parametrize(
    "field",
    ["prompt", "answer", "events", "tool_output", "environment", "api_key"],
)
def test_payload_rejects_forbidden_persisted_fields(field: str) -> None:
    with pytest.raises(CodingRecoveryError) as error:
        _payload(operation={field: "must-not-persist"})

    assert error.value.code == "invalid_recovery_payload"


def test_payload_rejects_patch_path_or_summary_mismatch() -> None:
    changes = _changes()
    files = changes["files"]
    assert isinstance(files, list)
    files[0]["path"] = "../outside.txt"

    with pytest.raises(CodingRecoveryError) as error:
        _payload(changes=changes)

    assert error.value.code == "invalid_recovery_payload"


def test_v3_encrypts_publish_intent_and_rejects_publish_tokens(tmp_path: Path) -> None:
    marker = "draft-pr-random-731"
    store = CodingRecoveryStore(tmp_path)
    record = store.create_record(
        recovery_id=RECOVERY_ID,
        state=RecoveryState.PUBLISHED,
        revision=3,
        snapshot_fingerprint=FINGERPRINT,
        payload=_payload(
            publish={
                "state": "draft",
                "title": marker,
                "body": "准备发布 1 个文件。",
                "publish_id": "p" * 24,
            }
        ),
    )

    store.save(record)

    loaded = store.load()
    assert loaded is not None
    assert loaded.state is RecoveryState.PUBLISHED
    assert loaded.payload.publish == record.payload.publish
    assert marker.encode("utf-8") not in store.database_path.read_bytes()

    with pytest.raises(CodingRecoveryError) as error:
        _payload(publish={"token": "must-not-persist"})
    assert error.value.code == "invalid_recovery_payload"


def test_existing_v2_database_metadata_is_upgraded(tmp_path: Path) -> None:
    store = CodingRecoveryStore(tmp_path)
    store.save(_record(store))
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("PRAGMA user_version = 2")
        connection.execute(
            "UPDATE coding_recovery SET schema_version = 2 WHERE slot = 1"
        )

    upgraded = CodingRecoveryStore(tmp_path)

    assert upgraded.load() is not None
    with sqlite3.connect(upgraded.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3


def test_existing_unknown_schema_is_not_reinitialized(tmp_path: Path) -> None:
    database = tmp_path / "recovery.sqlite3"
    tmp_path.mkdir(exist_ok=True)
    database.touch()
    key = Fernet.generate_key()
    (tmp_path / "recovery-master.key").write_bytes(key)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(CodingRecoveryError) as error:
        CodingRecoveryStore(tmp_path)

    assert error.value.code == "recovery_schema_unsupported"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99
