from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken

from .models import CredentialRecord


class CredentialStoreError(Exception):
    """Base credential vault error."""


class CredentialNotFoundError(CredentialStoreError):
    """Raised when a credential reference does not exist."""


class CredentialUnavailableError(CredentialStoreError):
    """Raised when encrypted material cannot be decrypted."""


class CredentialStore:
    """Small authenticated local credential vault.

    The persisted master key is runtime data and must live on the mounted storage
    volume. Toolset definitions and versions only retain credential IDs.
    """

    def __init__(
        self,
        storage_dir: str | Path | None = None,
        *,
        master_key: str | bytes | None = None,
    ) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("TOOLSET_STORAGE_DIR", "").strip()
            or os.getenv("XPERT_STORAGE_DIR", "").strip()
            or package_dir / "storage"
        )
        self.storage_path = self.storage_dir / "credentials.json"
        self.master_key_path = self.storage_dir / "credential-master.key"
        self._lock = threading.RLock()
        self._fernet = Fernet(self._resolve_master_key(master_key))
        self._ensure_storage_unlocked()

    def create(
        self,
        *,
        name: str,
        value: str,
        kind: Literal["header", "environment", "provider_key", "generic"] = "generic",
    ) -> tuple[CredentialRecord, str]:
        clean_name = self._required_text(name, "name", 160)
        clean_value = self._required_text(value, "value", 20_000)
        now = time.time()
        record = CredentialRecord(
            credential_id=f"cred_{uuid.uuid4().hex}",
            name=clean_name,
            kind=kind,
            prefix=self._prefix(clean_value),
            masked_value=self._mask(clean_value),
            ciphertext=self._fernet.encrypt(clean_value.encode("utf-8")).decode("ascii"),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            items = self._read_unlocked()
            items.append(record)
            self._write_unlocked(items)
        return self._public(record), clean_value

    def list(self) -> list[CredentialRecord]:
        with self._lock:
            items = self._read_unlocked()
        items.sort(key=lambda item: (-item.updated_at, item.credential_id))
        return [self._public_with_runtime_status(item) for item in items]

    def get_public(self, credential_id: str) -> CredentialRecord:
        with self._lock:
            record = self._find_unlocked(self._read_unlocked(), credential_id)
        return self._public_with_runtime_status(record)

    def resolve(self, credential_id: str) -> str:
        with self._lock:
            record = self._find_unlocked(self._read_unlocked(), credential_id)
        if record.status != "active":
            raise CredentialUnavailableError("Credential is not active.")
        try:
            return self._fernet.decrypt(record.ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
            raise CredentialUnavailableError(
                "Credential cannot be decrypted with the current master key."
            ) from exc

    def rotate(
        self,
        credential_id: str,
        *,
        value: str,
    ) -> tuple[CredentialRecord, str]:
        clean_value = self._required_text(value, "value", 20_000)
        with self._lock:
            items = self._read_unlocked()
            record = self._find_unlocked(items, credential_id)
            record.ciphertext = self._fernet.encrypt(
                clean_value.encode("utf-8")
            ).decode("ascii")
            record.prefix = self._prefix(clean_value)
            record.masked_value = self._mask(clean_value)
            record.status = "active"
            record.updated_at = time.time()
            self._write_unlocked(items)
            return self._public(record), clean_value

    def revoke(self, credential_id: str) -> CredentialRecord:
        with self._lock:
            items = self._read_unlocked()
            record = self._find_unlocked(items, credential_id)
            record.status = "revoked"
            record.updated_at = time.time()
            self._write_unlocked(items)
            return self._public(record)

    def _resolve_master_key(self, supplied: str | bytes | None) -> bytes:
        if supplied:
            return self._normalize_key(supplied)
        environment_key = os.getenv("MODEL_MIRROR_CREDENTIAL_MASTER_KEY", "").strip()
        if environment_key:
            return self._normalize_key(environment_key)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        if self.master_key_path.exists():
            raw = self.master_key_path.read_bytes().strip()
            return self._normalize_key(raw)
        key = Fernet.generate_key()
        temporary = self.master_key_path.with_suffix(".tmp")
        temporary.write_bytes(key)
        os.replace(temporary, self.master_key_path)
        try:
            os.chmod(self.master_key_path, 0o600)
        except OSError:
            pass
        return key

    @staticmethod
    def _normalize_key(value: str | bytes) -> bytes:
        raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        try:
            Fernet(raw)
            return raw
        except ValueError:
            digest = hashlib.sha256(raw).digest()
            return base64.urlsafe_b64encode(digest)

    def _ensure_storage_unlocked(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        if not self.storage_path.exists():
            self._write_unlocked([])

    def _read_unlocked(self) -> list[CredentialRecord]:
        self._ensure_storage_unlocked()
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
            raw = payload.get("credentials", []) if isinstance(payload, dict) else []
            return [CredentialRecord.model_validate(item) for item in raw]
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise CredentialStoreError("Credential storage is unreadable.") from exc

    def _write_unlocked(self, items: list[CredentialRecord]) -> None:
        payload = {
            "version": "modelmirror-credentials-v1",
            "credentials": [item.model_dump(mode="json") for item in items],
        }
        temporary = self.storage_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.storage_path)

    @staticmethod
    def _find_unlocked(
        items: list[CredentialRecord], credential_id: str
    ) -> CredentialRecord:
        for item in items:
            if item.credential_id == credential_id:
                return item
        raise CredentialNotFoundError(f"Credential not found: {credential_id}")

    @staticmethod
    def _public(record: CredentialRecord) -> CredentialRecord:
        clone = record.model_copy(deep=True)
        clone.ciphertext = ""
        return clone

    def _public_with_runtime_status(
        self,
        record: CredentialRecord,
    ) -> CredentialRecord:
        clone = self._public(record)
        if record.status != "active":
            return clone
        try:
            self._fernet.decrypt(record.ciphertext.encode("ascii"))
        except (InvalidToken, ValueError):
            clone.status = "unavailable"
        return clone

    @staticmethod
    def _prefix(value: str) -> str:
        return value[:4] if len(value) > 8 else value[:2]

    @staticmethod
    def _mask(value: str) -> str:
        if len(value) <= 4:
            return "*" * len(value)
        return f"{value[:2]}{'*' * min(12, len(value) - 4)}{value[-2:]}"

    @staticmethod
    def _required_text(value: str, field_name: str, limit: int) -> str:
        text = str(value or "").strip()
        if not text:
            raise CredentialStoreError(f"{field_name} is required.")
        if len(text) > limit:
            raise CredentialStoreError(f"{field_name} exceeds {limit} characters.")
        return text
