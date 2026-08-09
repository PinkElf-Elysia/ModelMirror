from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class WorkerCryptoError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class WorkerEncryptedCodec:
    """Coding Recovery-compatible authenticated JSON encryption at rest."""

    def __init__(self, storage_root: Path, *, master_key: bytes | str | None = None) -> None:
        self.storage_root = Path(storage_root)
        self.database_path = self.storage_root / "coding-worker.sqlite3"
        self.key_path = self.storage_root / "coding-worker-master.key"
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(self._resolve_key(master_key))

    def encrypt(self, value: Any) -> str:
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise WorkerCryptoError(
                "Worker data is not serializable.", code="worker_data_invalid"
            ) from exc
        return self._fernet.encrypt(encoded).decode("ascii")

    def decrypt(self, ciphertext: str) -> Any:
        try:
            raw = self._fernet.decrypt(ciphertext.encode("ascii"))
            return json.loads(raw.decode("utf-8"))
        except (InvalidToken, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
            raise WorkerCryptoError(
                "Worker data could not be authenticated.", code="worker_data_corrupt"
            ) from exc

    def _resolve_key(self, master_key: bytes | str | None) -> bytes:
        if master_key is not None:
            candidate = master_key.encode("ascii") if isinstance(master_key, str) else master_key
            return self._validate_key(candidate)
        if self.key_path.exists():
            try:
                return self._validate_key(self.key_path.read_bytes().strip())
            except OSError as exc:
                raise WorkerCryptoError(
                    "Worker key could not be read.", code="worker_key_invalid"
                ) from exc
        if self.database_path.exists():
            raise WorkerCryptoError(
                "Worker key is missing for existing data.", code="worker_key_missing"
            )
        candidate = Fernet.generate_key()
        try:
            descriptor = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(candidate)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            try:
                candidate = self.key_path.read_bytes().strip()
            except OSError as exc:
                raise WorkerCryptoError(
                    "Worker key could not be loaded.", code="worker_key_invalid"
                ) from exc
        except OSError as exc:
            raise WorkerCryptoError(
                "Worker key could not be created.", code="worker_storage_unavailable"
            ) from exc
        return self._validate_key(candidate)

    @staticmethod
    def _validate_key(candidate: bytes) -> bytes:
        try:
            Fernet(candidate)
        except (TypeError, ValueError) as exc:
            raise WorkerCryptoError(
                "Worker key is invalid.", code="worker_key_invalid"
            ) from exc
        return candidate
