"""Read-only rollback preflight, using a synthetic database only."""

import hashlib
import sqlite3
import types
from pathlib import Path
import os

import pytest

from server.model_router.repository import (
    SCHEMA_VERSION,
    RouterRepositoryError,
    SQLiteRouterRepository,
)
from server.model_router.qualification_storage import (
    QUALIFICATION_TABLES, assert_known_qualification_schema, create_qualification_schema,
)
from server.model_router.qualification_rollback import assert_qualification_rollback_safe
from scripts.provider_qualification_rollback import REPLACEMENT, patch_repository


@pytest.fixture
def rollback_repository():
    # Exercise the exact replacement with a repository test double. The pinned
    # whole B2 module is verified separately in the source-archive acceptance;
    # unit tests must also run in offline images without .git/history.
    module = types.ModuleType("server.model_router._r9b3_rollback")
    module.__file__ = str(Path(__file__).resolve().parents[1] / "model_router" / "repository.py")
    module.__dict__.update(
        os=os, sqlite3=sqlite3, Base=SQLiteRouterRepository,
        RouterRepositoryError=RouterRepositoryError,
    )
    exec(compile("class SQLiteRouterRepository(Base):\n" + REPLACEMENT, module.__file__, "exec"), module.__dict__)
    return module


@pytest.fixture
def migrated_database(tmp_path):
    with SQLiteRouterRepository.open(tmp_path, master_key=b"s" * 32):
        pass
    database = tmp_path / "router.sqlite3"
    with sqlite3.connect(database) as connection:
        # v19 only adds these tables; remove them in this synthetic fixture to
        # represent v18, without constructing new qualification evidence.
        for table in reversed(QUALIFICATION_TABLES):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("PRAGMA user_version = 18")
        connection.execute("""INSERT INTO provider_chat_certifications
            (id, tenant_id, connection_id, connection_fingerprint, contract_version,
             requested_model, idempotency_key_hash, status, created_at, updated_at, completed_at)
            VALUES ('old-cert', 'local', 'synthetic', 'synthetic-fingerprint',
             'modelmirror-provider-chat-v1', 'synthetic/model', 'synthetic-hash', 'passed',
             '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:01+00:00', '2026-10-01T00:00:01+00:00')""")
        old_row = connection.execute("SELECT * FROM provider_chat_certifications").fetchone()
    new = SQLiteRouterRepository.open(tmp_path, master_key=b"s" * 32)
    new.close()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 19
        assert connection.execute("SELECT * FROM provider_chat_certifications").fetchone() == old_row
        assert connection.execute("SELECT COUNT(*) FROM provider_qualification_events").fetchone()[0] == 0
    return database


def test_current_reader_rejects_future_schema_without_modifying_database(tmp_path):
    repository = SQLiteRouterRepository.open(tmp_path, master_key=b"s" * 32)
    repository.close()
    database = tmp_path / "router.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    backups_before = set(tmp_path.glob("router.sqlite3.backup-*"))

    with pytest.raises(RouterRepositoryError, match="provider_storage_schema_newer"):
        SQLiteRouterRepository.open(tmp_path, master_key=b"s" * 32)

    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert set(tmp_path.glob("router.sqlite3.backup-*")) == backups_before


def test_v18_upgrade_preserves_old_records_and_backup(migrated_database):
    with sqlite3.connect(migrated_database) as connection:
        assert_known_qualification_schema(connection)
    backups = list(migrated_database.parent.glob("router.sqlite3.backup-*"))
    assert len(backups) == 1
    with sqlite3.connect(f"file:{backups[0].as_posix()}?mode=ro", uri=True) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 18
        assert connection.execute("SELECT id FROM provider_chat_certifications").fetchone()[0] == "old-cert"


def check_rollback(database, **environment):
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as connection:
        assert_qualification_rollback_safe(
            connection,
            environment={"MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_ENABLED": "false", **environment},
            outbox_directory=database.parent / "chat-completion-outbox",
        )


def test_rollback_preflight_only_reads_inactive_database(migrated_database):
    before = migrated_database.read_bytes()
    check_rollback(migrated_database)
    assert migrated_database.read_bytes() == before


@pytest.mark.parametrize("flag", ["MODEL_CONTROL_CHAT_ENABLED", "MODEL_CONTROL_REALTIME_VOICE_ENABLED", "MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED"])
def test_rollback_does_not_implicitly_disable_active_flags(migrated_database, flag):
    with pytest.raises(ValueError, match="provider_qualification_rollback_feature_enabled"):
        check_rollback(migrated_database, **{flag: "true"})


def test_rollback_requires_explicit_certification_disable(migrated_database):
    with pytest.raises(ValueError, match="provider_qualification_rollback_certification_enabled"):
        check_rollback(migrated_database, MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_ENABLED="")


def test_rollback_refuses_active_policy_even_with_flags_off(migrated_database):
    with sqlite3.connect(migrated_database) as connection:
        connection.execute("""INSERT INTO provider_workload_policies
            (tenant_id, entry_id, status, policy_fingerprint, created_at, updated_at)
            VALUES ('local', 'meta_agent', 'managed_required', 'synthetic', 'now', 'now')""")
    with pytest.raises(ValueError, match="provider_qualification_rollback_policy_active"):
        check_rollback(migrated_database)


def test_rollback_refuses_uncertain_certification(migrated_database):
    with sqlite3.connect(migrated_database) as connection:
        connection.execute("UPDATE provider_chat_certifications SET status = 'uncertain'")
    with pytest.raises(ValueError, match="provider_qualification_rollback_unresolved_operations"):
        check_rollback(migrated_database)


def test_rollback_refuses_future_or_modified_schema(migrated_database):
    with sqlite3.connect(migrated_database) as connection:
        connection.execute("ALTER TABLE provider_qualification_events ADD COLUMN unexpected TEXT")
    with pytest.raises(ValueError, match="provider_qualification_rollback_schema_mismatch"):
        check_rollback(migrated_database)


def test_v19_creation_is_transactional():
    with sqlite3.connect(":memory:") as connection:
        create_qualification_schema(connection)
        connection.rollback()
        for name in QUALIFICATION_TABLES:
            assert connection.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone() is None


def test_tenant_is_part_of_every_new_primary_key():
    with sqlite3.connect(":memory:") as connection:
        create_qualification_schema(connection)
        for name in QUALIFICATION_TABLES:
            columns = connection.execute(f"PRAGMA table_info({name})").fetchall()
            assert next(row for row in columns if row[1] == "tenant_id")[5] > 0


def test_rollback_migration_override_starts_and_returns_without_downgrade(
    migrated_database, rollback_repository, monkeypatch,
):
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_ENABLED", "false")
    # Never inherit an operator's test environment into this synthetic test.
    for key in tuple(os.environ):
        if key.startswith("MODEL_CONTROL_") and key.endswith("_ENABLED"):
            monkeypatch.setenv(key, "false")
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "false")
    with sqlite3.connect(migrated_database) as connection:
        before = connection.execute("SELECT * FROM provider_chat_certifications").fetchall()
        for table in QUALIFICATION_TABLES:
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    rolled_back = rollback_repository.SQLiteRouterRepository.open(migrated_database.parent, master_key=b"s" * 32)
    assert rolled_back.get_latest_chat_certification("local", "synthetic", "synthetic/model")["id"] == "old-cert"
    rolled_back.close()
    new = SQLiteRouterRepository.open(migrated_database.parent, master_key=b"s" * 32)
    new.close()
    with sqlite3.connect(migrated_database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 19
        assert connection.execute("SELECT * FROM provider_chat_certifications").fetchall() == before
        assert_known_qualification_schema(connection)


def test_rollback_builder_refuses_unreviewed_source():
    with pytest.raises(ValueError, match="rollback_baseline_source_mismatch"):
        patch_repository(b"unreviewed source")
