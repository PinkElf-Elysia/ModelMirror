"""Synthetic storage only: lifecycle operations must not imply model work."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from server.model_router.repository import SCHEMA_VERSION, SQLiteRouterRepository
from server.model_router.storage_lifecycle import ProviderStorageError


ROOT = Path(__file__).resolve().parents[2]


def snapshot(directory):
    return {str(p.relative_to(directory)): (
                ("lease", p.stat().st_size, p.stat().st_ino)
                if p.name == ".provider-writer.lock" else hashlib.sha256(p.read_bytes()).hexdigest())
            for p in directory.rglob("*") if p.is_file()}


def command(directory, *extra):
    environment = {k: v for k, v in os.environ.items()
                   if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    return subprocess.run(
        [sys.executable, "-B", "-m", "server.model_router.cleanup_chat_receipts",
         "--storage-dir", str(directory), *extra],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30,
    )


def test_constructor_creates_no_files(tmp_path):
    directory = tmp_path / "missing"
    SQLiteRouterRepository(directory, master_key=b"x" * 32)
    assert not directory.exists()


def test_cleanup_dry_run_preserves_running_and_every_file(tmp_path):
    # Compatible baseline setup lets this test reproduce the old CLI's actual
    # side effect before the new explicit lifecycle API exists.
    factory = getattr(SQLiteRouterRepository, "open", SQLiteRouterRepository)
    repository = factory(tmp_path)
    with repository._connect() as db:
        db.execute("""INSERT INTO provider_catalog_refreshes
            (tenant_id, connection_id, id, connection_fingerprint, status, started_at)
            VALUES ('local', 'synthetic', 'refresh', 'fingerprint', 'running', '2020-01-01')""")
    if hasattr(repository, "close"):
        repository.close()
    before = snapshot(tmp_path)
    result = command(tmp_path)
    assert result.returncode == 0, result.stderr
    assert snapshot(tmp_path) == before
    with sqlite3.connect(tmp_path / "router.sqlite3") as db:
        assert db.execute("SELECT status FROM provider_catalog_refreshes").fetchone()[0] == "running"


def test_dry_run_missing_directory_does_not_create_it(tmp_path):
    directory = tmp_path / "missing"
    result = command(directory)
    assert result.returncode != 0
    assert not directory.exists()


def test_exclusive_lease_blocks_second_process_and_maintenance(tmp_path):
    with SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32) as repository:
        before = snapshot(tmp_path)
        script = """
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.storage_lifecycle import ProviderStorageError
import sys
try:
    SQLiteRouterRepository.open(sys.argv[1], master_key=b'x'*32)
except ProviderStorageError as exc:
    print(str(exc))
    sys.exit(7)
sys.exit(0)
"""
        result = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path)],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        assert result.returncode == 7, result.stderr
        assert result.stdout.strip() == "provider_storage_writer_busy"
        for flags in ((), ("--apply",)):
            result = command(tmp_path, *flags)
            assert result.returncode == 1
            assert "provider_storage_writer_busy" in result.stdout
        assert snapshot(tmp_path) == before
        assert repository.list_connections("local") == []


def test_process_exit_releases_lease_without_deleting_lock(tmp_path):
    script = """
import os, sys
from server.model_router.repository import SQLiteRouterRepository
repository = SQLiteRouterRepository.open(sys.argv[1], master_key=b'x'*32)
os._exit(9)
"""
    result = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path)],
                            cwd=ROOT, capture_output=True, timeout=30)
    assert result.returncode == 9
    lock = tmp_path / ".provider-writer.lock"
    identity = lock.stat().st_ino
    with SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32):
        assert lock.stat().st_ino == identity


def test_same_process_alias_does_not_allow_second_owner(tmp_path):
    with SQLiteRouterRepository.open(tmp_path):
        with pytest.raises(ProviderStorageError, match="writer_busy"):
            SQLiteRouterRepository.open(tmp_path / ".")


@pytest.mark.parametrize("maintenance", [False, True])
def test_database_hardlink_cannot_bypass_directory_ownership(tmp_path, maintenance):
    from server.model_router.migrate_credentials import migrate_credentials
    original = tmp_path / "original"
    alias = tmp_path / "alias"
    alias.mkdir()
    with SQLiteRouterRepository.open(original, master_key="synthetic-original") as owner:
        os.link(owner.database_path, alias / "router.sqlite3")
        before = owner.database_path.read_bytes()
        if maintenance:
            with pytest.raises(RuntimeError, match="provider_storage_database_unsafe"):
                migrate_credentials(alias, source_key="synthetic-original", target_key="synthetic-replacement")
        else:
            with pytest.raises(ProviderStorageError, match="provider_storage_database_unsafe"):
                with SQLiteRouterRepository.open(alias, master_key="synthetic-original"):
                    pass
        assert owner.database_path.read_bytes() == before


def test_failed_startup_releases_lock_without_marking_running(tmp_path):
    with SQLiteRouterRepository.open(tmp_path, master_key="first") as repository:
        with repository._connect() as db:
            db.execute("""INSERT INTO provider_catalog_refreshes
                (tenant_id, connection_id, id, connection_fingerprint, status, started_at)
                VALUES ('local', 'synthetic', 'refresh', 'fp', 'running', '2020')""")
    with pytest.raises(ProviderStorageError):
        SQLiteRouterRepository.open(tmp_path, master_key="wrong")
    with SQLiteRouterRepository.open_maintenance(tmp_path) as observer:
        with observer._connect() as db:
            assert db.execute("SELECT status FROM provider_catalog_refreshes").fetchone()[0] == "running"
    with SQLiteRouterRepository.open(tmp_path, master_key="first") as recovered:
        with recovered._connect() as db:
            assert db.execute("SELECT status FROM provider_catalog_refreshes").fetchone()[0] == "uncertain"
        with pytest.raises(ProviderStorageError, match="recovery_already_completed"):
            recovered.recover_after_restart()


def test_readonly_cannot_mutate_and_does_not_read_keys(tmp_path, monkeypatch):
    with SQLiteRouterRepository.open(tmp_path):
        pass
    before = snapshot(tmp_path)
    def forbidden(*args):
        raise AssertionError("read-only maintenance must not resolve credentials")
    monkeypatch.setattr(SQLiteRouterRepository, "_resolve_master_key", forbidden)
    with SQLiteRouterRepository.open_maintenance(tmp_path) as observer:
        with observer._connect() as db:
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                db.execute("DELETE FROM router_metadata")
        with pytest.raises(ProviderStorageError, match="readonly"):
            observer.migrate_schema()
        with pytest.raises(ProviderStorageError, match="readonly"):
            observer.recover_after_restart()
    assert snapshot(tmp_path) == before


def test_readonly_refuses_uncheckpointed_wal_without_ignoring_it(tmp_path):
    with SQLiteRouterRepository.open(tmp_path):
        pass
    writer = sqlite3.connect(tmp_path / "router.sqlite3")
    try:
        writer.execute("UPDATE router_metadata SET updated_at='synthetic'")
        writer.commit()
        before = snapshot(tmp_path)
        result = command(tmp_path)
        assert result.returncode == 1
        assert "wal_recovery_required" in result.stdout
        assert snapshot(tmp_path) == before
    finally:
        writer.close()


def test_old_schema_readonly_is_not_migrated(tmp_path):
    with SQLiteRouterRepository.open(tmp_path):
        pass
    with sqlite3.connect(tmp_path / "router.sqlite3") as db:
        db.execute("PRAGMA user_version=17")
    db.close()
    before = snapshot(tmp_path)
    assert command(tmp_path).returncode == 1
    assert snapshot(tmp_path) == before


def test_closed_repository_cannot_write(tmp_path):
    repository = SQLiteRouterRepository.open(tmp_path)
    repository.close()
    before = snapshot(tmp_path)
    with pytest.raises(ProviderStorageError):
        repository._connect()
    assert snapshot(tmp_path) == before


def test_live_sqlite_handle_prevents_releasing_writer_lease(tmp_path):
    repository = SQLiteRouterRepository.open(tmp_path)
    connection = repository._connect()
    try:
        with pytest.raises(ProviderStorageError, match="connections_active"):
            repository.close()
        with pytest.raises(ProviderStorageError, match="writer_busy"):
            SQLiteRouterRepository.open(tmp_path)
        assert connection.execute("SELECT 1").fetchone()[0] == 1
    finally:
        connection.close()
        repository.close()
    with SQLiteRouterRepository.open(tmp_path):
        pass


def test_dry_run_refuses_recovery_journal_without_writing(tmp_path):
    with SQLiteRouterRepository.open(tmp_path):
        pass
    (tmp_path / "router.sqlite3-journal").write_bytes(b"synthetic incomplete journal")
    before = snapshot(tmp_path)
    result = command(tmp_path)
    assert result.returncode == 1
    assert "journal_recovery_required" in result.stdout
    assert snapshot(tmp_path) == before


def test_credential_maintenance_refuses_active_writer_before_key_read(tmp_path, monkeypatch):
    from server.model_router import migrate_credentials as maintenance
    def forbidden(*args, **kwargs):
        raise AssertionError("must not reach credentials with another owner")
    with SQLiteRouterRepository.open(tmp_path):
        before = snapshot(tmp_path)
        monkeypatch.setattr(maintenance, "_migrate_credentials_owned", forbidden)
        with pytest.raises(maintenance.CredentialMigrationError, match="writer_busy"):
            maintenance.migrate_credentials(tmp_path, target_key="synthetic-new-key")
        assert snapshot(tmp_path) == before


@pytest.mark.asyncio
async def test_server_refuses_writer_conflict_before_starting_workers(tmp_path, monkeypatch):
    from server import main
    from server.model_router import api
    events = []
    async def worker():
        events.append("worker")
    with SQLiteRouterRepository.open(tmp_path):
        before = snapshot(tmp_path)
        monkeypatch.setattr(api, "_service", None)
        monkeypatch.setenv("MODEL_ROUTER_STORAGE_DIR", str(tmp_path))
        monkeypatch.setattr(main, "runtime_services_started", False)
        monkeypatch.setattr(main, "_start_independent_runtime_services", worker)
        monkeypatch.setattr(main, "_start_workflow_execution_services", worker)
        with pytest.raises(ProviderStorageError, match="writer_busy"):
            await main.start_mcp_ttl_cleanup()
        assert events == []
        assert main.runtime_services_started is False
        assert snapshot(tmp_path) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_server_releases_storage_only_after_successful_shutdown(tmp_path, monkeypatch, failed):
    from server import main
    from server.model_router import api
    from server.model_router.service import ModelRouterService
    repository = SQLiteRouterRepository.open(tmp_path)
    monkeypatch.setattr(api, "_service", ModelRouterService(repository))
    monkeypatch.setattr(api, "_native_engine", None)
    monkeypatch.setattr(api, "_catalog_coordinator", None)
    monkeypatch.setattr(main, "runtime_services_started", True)
    async def stop_workers(steps):
        with pytest.raises(ProviderStorageError, match="writer_busy"):
            SQLiteRouterRepository.open(tmp_path)
        if failed:
            raise RuntimeError("synthetic shutdown failure")
    monkeypatch.setattr(main, "_stop_runtime_service_steps", stop_workers)
    try:
        if failed:
            with pytest.raises(RuntimeError, match="shutdown failure"):
                await main.shutdown_mcp_sessions()
            with pytest.raises(ProviderStorageError, match="writer_busy"):
                SQLiteRouterRepository.open(tmp_path)
        else:
            await main.shutdown_mcp_sessions()
            with SQLiteRouterRepository.open(tmp_path):
                pass
    finally:
        repository.close()


def test_injected_configuration_is_started_by_service_not_constructor(tmp_path):
    from server.model_router.service import ModelRouterService
    repository = SQLiteRouterRepository(tmp_path / "router")
    assert not repository.storage_dir.exists()
    with repository.open(repository.storage_dir) as owner:
        with pytest.raises(ProviderStorageError, match="writer_busy"):
            ModelRouterService(repository)
        assert owner.list_connections("local") == []
    service = ModelRouterService(repository)
    assert service.list_connections() == []
    repository.close()


def test_startup_rejects_closed_injected_repository(tmp_path, monkeypatch):
    from server.model_router import api
    from server.model_router.service import ModelRouterService
    service = ModelRouterService(SQLiteRouterRepository.open(tmp_path))
    service.repository.close()
    monkeypatch.setattr(api, "_service", service)
    with pytest.raises(ProviderStorageError, match="not_owned"):
        api.start_provider_storage()


def test_schema_migration_backs_up_without_recovering_records(tmp_path, monkeypatch):
    with SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32) as repository:
        with repository._connect() as db:
            db.execute("""INSERT INTO provider_catalog_refreshes
                (tenant_id, connection_id, id, connection_fingerprint, status, started_at)
                VALUES ('local', 'synthetic', 'refresh', 'fp', 'running', '2020')""")
            db.execute("PRAGMA user_version=17")
    observed = []
    recover = SQLiteRouterRepository.recover_after_restart
    def check_before_recovery(owner):
        with owner._connect() as db:
            observed.append(db.execute("SELECT status FROM provider_catalog_refreshes").fetchone()[0])
        recover(owner)
    monkeypatch.setattr(SQLiteRouterRepository, "recover_after_restart", check_before_recovery)
    with SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32) as owner:
        assert observed == ["running"]
        with owner._connect() as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            assert db.execute("SELECT status FROM provider_catalog_refreshes").fetchone()[0] == "uncertain"
    backups = list(tmp_path.glob("router.sqlite3.backup-*"))
    assert len(backups) == 1
    backup = sqlite3.connect(backups[0].as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 17
        assert backup.execute("SELECT status FROM provider_catalog_refreshes").fetchone()[0] == "running"
    finally:
        backup.close()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork ownership contract")
def test_fork_child_cannot_use_or_unlock_parent_repository(tmp_path):
    with SQLiteRouterRepository.open(tmp_path) as repository:
        pid = os.fork()
        if pid == 0:
            try:
                try:
                    repository.list_connections("local")
                except ProviderStorageError:
                    repository.close()
                    os._exit(0)
                os._exit(3)
            except BaseException:
                os._exit(4)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        with pytest.raises(ProviderStorageError, match="writer_busy"):
            SQLiteRouterRepository.open(tmp_path)
        assert repository.list_connections("local") == []


@pytest.mark.asyncio
async def test_outbox_file_work_keeps_lease_between_database_transactions(tmp_path, monkeypatch):
    from server.tests.test_provider_chat_stable_service import (
        _service, _qualify_scoped_model, SCOPED_MODEL_ID,
    )
    service, repository, primary, _ = _service(tmp_path, monkeypatch, newapi_ip="8.8.8.8")
    _qualify_scoped_model(repository, primary)
    dispatch = (await service.begin_scoped_certified(SCOPED_MODEL_ID)).dispatch
    service.mark_dispatched(dispatch)
    identity = repository._chat_completion_identity
    observed = []
    def during_file_work(payload):
        assert repository._active_connections == 0
        with pytest.raises(ProviderStorageError, match="operations_active"):
            repository.close()
        observed.append(True)
        return identity(payload)
    monkeypatch.setattr(repository, "_chat_completion_identity", during_file_work)
    repository.stage_chat_control_completion(
        "local", dispatch.attempt_id, expected_run_id=dispatch.run_id,
        status="succeeded", result_class="success", actual_model=SCOPED_MODEL_ID,
    )
    assert observed
    repository.close()
