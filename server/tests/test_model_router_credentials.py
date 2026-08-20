from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.model_router.migrate_credentials import (
    CredentialMigrationError,
    migrate_credentials,
)
from server.model_router.catalog import CatalogCoordinator
from server.model_router.repository import (
    CANONICAL_MASTER_KEY_ENV,
    LEGACY_MASTER_KEY_ENV,
    REQUIRE_EXTERNAL_MASTER_KEY_ENV,
    RouterCredentialUnavailable,
    SQLiteRouterRepository,
)
from server.model_router.schemas import RouterConnectionCreate
from server.model_router.service import ModelRouterService, RouterServiceError


def clear_master_key_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        CANONICAL_MASTER_KEY_ENV,
        LEGACY_MASTER_KEY_ENV,
        REQUIRE_EXTERNAL_MASTER_KEY_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


def create_connection(repository: SQLiteRouterRepository, key: str = "secret-value") -> str:
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Provider",
            kind="openai_compatible",
            base_url="https://provider.example/v1",
            api_key=key,
        ),
    )
    return connection.id


def test_canonical_master_key_precedes_legacy_and_local_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(CANONICAL_MASTER_KEY_ENV, "canonical-key-material")
    monkeypatch.setenv(LEGACY_MASTER_KEY_ENV, "legacy-key-material")
    (tmp_path / "credential-master.key").write_text("local-key-material")

    repository = SQLiteRouterRepository(tmp_path)
    connection_id = create_connection(repository)

    assert repository.resolve_api_key("local", connection_id) == "secret-value"
    assert (
        SQLiteRouterRepository(tmp_path, master_key="canonical-key-material")
        .resolve_api_key("local", connection_id)
        == "secret-value"
    )


def test_legacy_and_local_key_fallbacks_remain_compatible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_master_key_environment(monkeypatch)
    monkeypatch.setenv(LEGACY_MASTER_KEY_ENV, "legacy-key-material")
    legacy = SQLiteRouterRepository(tmp_path / "legacy")
    legacy_id = create_connection(legacy, "legacy-secret")
    assert legacy.resolve_api_key("local", legacy_id) == "legacy-secret"

    monkeypatch.delenv(LEGACY_MASTER_KEY_ENV)
    local = SQLiteRouterRepository(tmp_path / "local")
    local_id = create_connection(local, "local-secret")
    assert (tmp_path / "local" / "credential-master.key").is_file()
    assert SQLiteRouterRepository(tmp_path / "local").resolve_api_key(
        "local", local_id
    ) == "local-secret"


def test_require_external_never_falls_back_to_legacy_or_local_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_master_key_environment(monkeypatch)
    monkeypatch.setenv(REQUIRE_EXTERNAL_MASTER_KEY_ENV, "true")
    monkeypatch.setenv(LEGACY_MASTER_KEY_ENV, "legacy-key-material")
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "credential-master.key").write_text("local-key-material")

    with pytest.raises(RouterCredentialUnavailable):
        SQLiteRouterRepository(tmp_path)


def test_master_key_fingerprint_rejects_mismatched_key(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key="old-key")
    create_connection(repository)

    with pytest.raises(RouterCredentialUnavailable):
        SQLiteRouterRepository(tmp_path, master_key="different-key")


def test_atomic_credential_migration_preserves_backup_and_secrets(
    tmp_path: Path,
) -> None:
    source_key = "old-key-material"
    target_key = "new-key-material"
    old_repository = SQLiteRouterRepository(tmp_path, master_key=source_key)
    ids = [
        create_connection(old_repository, "first-secret"),
        create_connection(old_repository, "second-secret"),
    ]

    result = migrate_credentials(
        tmp_path,
        source_key=source_key,
        target_key=target_key,
    )

    assert result.migrated_credentials == 2
    assert Path(result.backup_path).is_file()
    migrated = SQLiteRouterRepository(tmp_path, master_key=target_key)
    assert [migrated.resolve_api_key("local", item) for item in ids] == [
        "first-secret",
        "second-secret",
    ]
    backup = sqlite3.connect(result.backup_path)
    try:
        ciphertext = backup.execute(
            "SELECT api_key_ciphertext FROM router_connections ORDER BY id LIMIT 1"
        ).fetchone()[0]
    finally:
        backup.close()
    old_fernet = Fernet(SQLiteRouterRepository._normalize_key(source_key))
    assert old_fernet.decrypt(ciphertext.encode("ascii")) in {
        b"first-secret",
        b"second-secret",
    }


def test_migration_preflight_and_interruption_leave_original_database_readable(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key="old-key")
    connection_id = create_connection(repository)

    with pytest.raises(CredentialMigrationError):
        migrate_credentials(
            tmp_path,
            source_key="wrong-old-key",
            target_key="new-key",
        )
    with pytest.raises(CredentialMigrationError):
        migrate_credentials(
            tmp_path,
            source_key="old-key",
            target_key="new-key",
            fail_after=1,
        )

    restored = SQLiteRouterRepository(tmp_path, master_key="old-key")
    assert restored.resolve_api_key("local", connection_id) == "secret-value"


def test_migration_fails_closed_when_provider_data_changes_during_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key="old-key")
    first_id = create_connection(repository, "first-secret")
    preflight_started = threading.Event()
    original_decrypt = Fernet.decrypt

    def delayed_first_decrypt(
        self: Fernet,
        token: bytes | str,
        ttl: int | None = None,
    ) -> bytes:
        plaintext = original_decrypt(self, token, ttl)
        if not preflight_started.is_set():
            preflight_started.set()
            time.sleep(0.25)
        return plaintext

    monkeypatch.setattr(Fernet, "decrypt", delayed_first_decrypt)
    migration_errors: list[BaseException] = []

    def run_migration() -> None:
        try:
            migrate_credentials(
                tmp_path,
                source_key="old-key",
                target_key="new-key",
            )
        except BaseException as exc:
            migration_errors.append(exc)

    worker = threading.Thread(target=run_migration)
    worker.start()
    assert preflight_started.wait(timeout=5)
    second_id = create_connection(repository, "second-secret")
    worker.join(timeout=10)

    assert not worker.is_alive()
    assert len(migration_errors) == 1
    assert isinstance(migration_errors[0], CredentialMigrationError)
    assert "changed during credential migration" in str(migration_errors[0])
    restored = SQLiteRouterRepository(tmp_path, master_key="old-key")
    assert restored.resolve_api_key("local", first_id) == "first-secret"
    assert restored.resolve_api_key("local", second_id) == "second-secret"


def test_empty_database_migration_records_target_fingerprint(tmp_path: Path) -> None:
    SQLiteRouterRepository(tmp_path, master_key="old-key")

    result = migrate_credentials(
        tmp_path,
        source_key="old-key",
        target_key="new-key",
    )

    assert result.migrated_credentials == 0
    assert Path(result.backup_path).is_file()
    assert SQLiteRouterRepository(tmp_path, master_key="new-key").list_connections(
        "local"
    ) == []


def test_tenant_environment_is_local_only_and_conflicts_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key="test-key")
    monkeypatch.setenv("MODELMIRROR_DEFAULT_TENANT_ID", "local")
    monkeypatch.setenv("MODEL_ROUTER_TENANT_ID", "local")
    assert ModelRouterService(repository).tenant_id == "local"

    monkeypatch.setenv("MODEL_ROUTER_TENANT_ID", "other")
    with pytest.raises(RouterServiceError) as conflict:
        ModelRouterService(repository)
    assert conflict.value.code == "tenant_configuration_conflict"

    monkeypatch.delenv("MODELMIRROR_DEFAULT_TENANT_ID")
    with pytest.raises(RouterServiceError) as unsupported:
        ModelRouterService(repository)
    assert unsupported.value.code == "unsupported_tenant"

    assert ModelRouterService(repository, tenant_id="other").tenant_id == "other"


@pytest.mark.asyncio
async def test_public_catalog_falls_back_when_dynamic_credentials_are_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Sidecar:
        async def get_catalog(self) -> str:
            return "public-catalog"

        async def get_status(self) -> str:
            return "public-status"

    def locked_service() -> ModelRouterService:
        raise RouterCredentialUnavailable("canonical master key required")

    monkeypatch.setattr(
        "server.model_router.catalog.get_model_router_service", locked_service
    )
    coordinator = CatalogCoordinator(Sidecar())  # type: ignore[arg-type]

    assert await coordinator.get_catalog() == "public-catalog"
    assert await coordinator.get_status() == "public-status"
