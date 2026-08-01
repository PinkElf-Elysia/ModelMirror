from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, SecretStr

try:
    from server.toolsets.credentials import (
        CredentialNotFoundError,
        CredentialStore,
        CredentialStoreError,
        CredentialUnavailableError,
    )
except ModuleNotFoundError:
    from toolsets.credentials import (
        CredentialNotFoundError,
        CredentialStore,
        CredentialStoreError,
        CredentialUnavailableError,
    )


class MarbleSettingsError(RuntimeError):
    """Raised when the Marble runtime configuration is unavailable."""


class MarbleSettingsPublic(BaseModel):
    configured: bool = False
    enabled: bool = False
    masked_key: str | None = None
    remaining_credits: float | None = None


class MarbleSettingsUpdate(BaseModel):
    api_key: SecretStr | None = None
    enabled: bool | None = None


class MarbleSettingsStore:
    """Persist Marble mode and an encrypted API key without exposing plaintext."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        world_storage = os.getenv("WORLD_STORAGE_DIR", "").strip()
        default_dir = Path(world_storage).parent if world_storage else package_dir / "storage"
        self.storage_dir = Path(
            storage_dir or os.getenv("WORLD_SETTINGS_DIR", "").strip() or default_dir
        )
        self.config_path = self.storage_dir / "marble-settings.json"
        self.credentials = CredentialStore(self.storage_dir / "credentials")
        self._lock = threading.RLock()
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def public(self) -> MarbleSettingsPublic:
        config = self._read()
        credential_id = str(config.get("credential_id") or "")
        if not credential_id:
            return MarbleSettingsPublic(enabled=False)
        try:
            credential = self.credentials.get_public(credential_id)
        except (CredentialNotFoundError, CredentialStoreError):
            return MarbleSettingsPublic(enabled=False)
        configured = credential.status == "active"
        return MarbleSettingsPublic(
            configured=configured,
            enabled=bool(config.get("enabled")) and configured,
            masked_key=credential.masked_value if configured else None,
            remaining_credits=_optional_float(config.get("remaining_credits")),
        )

    def provider_override(self) -> str | None:
        """Return an explicit UI-selected provider, or None for env fallback."""

        if not self.config_path.exists():
            return None
        return "marble" if self.public().enabled else "mock"

    def resolve_api_key(self) -> str | None:
        credential_id = str(self._read().get("credential_id") or "")
        if not credential_id:
            return None
        try:
            return self.credentials.resolve(credential_id)
        except (CredentialNotFoundError, CredentialUnavailableError) as exc:
            raise MarbleSettingsError("已保存的 Marble Key 无法解密，请重新配置。") from exc

    def save(
        self,
        *,
        api_key: str | None,
        enabled: bool | None,
        remaining_credits: float | None,
    ) -> MarbleSettingsPublic:
        with self._lock:
            config = self._read()
            clean_key = str(api_key or "").strip()
            if clean_key:
                credential_id = str(config.get("credential_id") or "")
                try:
                    if credential_id:
                        credential, _ = self.credentials.rotate(
                            credential_id, value=clean_key
                        )
                    else:
                        credential, _ = self.credentials.create(
                            name="World Labs Marble API Key",
                            value=clean_key,
                            kind="provider_key",
                        )
                    config["credential_id"] = credential.credential_id
                except CredentialStoreError as exc:
                    raise MarbleSettingsError("Marble Key 保存失败。") from exc
            if enabled is not None:
                config["enabled"] = enabled
            if remaining_credits is not None:
                config["remaining_credits"] = remaining_credits
            if bool(config.get("enabled")) and not config.get("credential_id"):
                raise MarbleSettingsError("启用真实模式前请先配置 Marble Key。")
            self._write(config)
        return self.public()

    def clear(self) -> MarbleSettingsPublic:
        with self._lock:
            config = self._read()
            credential_id = str(config.get("credential_id") or "")
            if credential_id:
                try:
                    self.credentials.revoke(credential_id)
                except CredentialStoreError:
                    pass
            self._write({"enabled": False})
        return MarbleSettingsPublic()

    def _read(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {"enabled": False}
        try:
            value = json.loads(self.config_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"enabled": False}
        except (OSError, json.JSONDecodeError) as exc:
            raise MarbleSettingsError("Marble 设置文件不可读。") from exc

    def _write(self, config: dict[str, Any]) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.config_path)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
