from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from .models import (
    DataSourceSnapshot,
    DataXImportJob,
    DataXProject,
    DataXResultArtifact,
    IndicatorDefinition,
    IndicatorProposal,
    IndicatorVersion,
    SemanticModel,
)


T = TypeVar("T", bound=BaseModel)


class DataXError(RuntimeError):
    pass


class DataXNotFoundError(DataXError):
    pass


class DataXConflictError(DataXError):
    pass


class DataXValidationError(DataXError):
    pass


class DataXUploadValidationError(DataXValidationError):
    """Stable upload rejection that preserves the public HTTP status/code."""

    def __init__(self, error_code: str, status_code: int, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code


class DataXStore:
    """Atomic file-backed metadata store with project-isolated DuckDB files."""

    VERSION = "modelmirror-datax-store-v1"
    COLLECTIONS = (
        "projects",
        "sources",
        "import_jobs",
        "models",
        "indicators",
        "indicator_versions",
        "proposals",
        "artifacts",
    )

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        configured = storage_dir or os.getenv("DATAX_STORAGE_DIR")
        self.root = Path(configured or Path(__file__).parent / "storage").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.sources_dir = self.root / "sources"
        self.databases_dir = self.root / "databases"
        self.sources_dir.mkdir(exist_ok=True)
        self.databases_dir.mkdir(exist_ok=True)
        self.path = self.root / "metadata.json"
        self._lock = threading.RLock()
        self._data = self._load()

    def _empty(self) -> dict[str, Any]:
        return {"version": self.VERSION, **{name: {} for name in self.COLLECTIONS}}

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DataXError("Data X metadata could not be loaded.") from exc
        result = self._empty()
        for name in self.COLLECTIONS:
            value = payload.get(name)
            if isinstance(value, dict):
                result[name] = value
        return result

    def _save(self) -> None:
        temp = self.path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            temp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)

    def insert(self, collection: str, item: BaseModel, id_field: str) -> BaseModel:
        with self._lock:
            key = str(getattr(item, id_field))
            if key in self._data[collection]:
                raise DataXConflictError(f"Data X resource already exists: {key}")
            self._data[collection][key] = item.model_dump(mode="json")
            try:
                self._save()
            except Exception:
                self._data[collection].pop(key, None)
                raise
        return item

    def replace(self, collection: str, item: BaseModel, id_field: str) -> BaseModel:
        with self._lock:
            key = str(getattr(item, id_field))
            if key not in self._data[collection]:
                raise DataXNotFoundError(f"Data X resource not found: {key}")
            previous = self._data[collection][key]
            self._data[collection][key] = item.model_dump(mode="json")
            try:
                self._save()
            except Exception:
                self._data[collection][key] = previous
                raise
        return item

    def get(self, collection: str, key: str, model: type[T]) -> T | None:
        with self._lock:
            value = self._data[collection].get(key)
            return model.model_validate(value) if value is not None else None

    def require(self, collection: str, key: str, model: type[T]) -> T:
        item = self.get(collection, key, model)
        if item is None:
            raise DataXNotFoundError(f"Data X resource not found: {key}")
        return item

    def list(self, collection: str, model: type[T]) -> list[T]:
        with self._lock:
            return [model.model_validate(value) for value in self._data[collection].values()]

    def delete(self, collection: str, key: str) -> None:
        with self._lock:
            previous = self._data[collection].pop(key, None)
            try:
                self._save()
            except Exception:
                if previous is not None:
                    self._data[collection][key] = previous
                raise

    def project_db_path(self, project_id: str) -> Path:
        safe = _safe_id(project_id)
        return self.databases_dir / f"{safe}.duckdb"

    def source_file_path(self, project_id: str, sha256: str, suffix: str) -> Path:
        safe_project_id = _safe_id(project_id)
        safe_sha256 = _safe_sha256(sha256)
        safe_suffix = _safe_suffix(suffix)
        source_root = self.sources_dir.resolve()
        directory = self.sources_dir / safe_project_id
        if directory.is_symlink():
            raise DataXValidationError("Invalid Data X source directory.")
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink() or directory.resolve() != source_root / safe_project_id:
            raise DataXValidationError("Invalid Data X source directory.")
        return directory / f"{safe_sha256}{safe_suffix}"

    def write_source(self, path: Path, content: bytes) -> bool:
        candidate = self._validated_source_blob_path(path)
        if candidate.is_symlink():
            raise DataXValidationError("Invalid Data X source path.")
        if candidate.exists():
            if not candidate.is_file():
                raise DataXValidationError("Invalid Data X source path.")
            return False
        temp = candidate.with_suffix(f"{candidate.suffix}.{uuid.uuid4().hex}.tmp")
        try:
            temp.write_bytes(content)
            os.replace(temp, candidate)
        finally:
            temp.unlink(missing_ok=True)
        return True

    def delete_source_file(self, path: Path) -> None:
        candidate = self._validated_source_blob_path(path)
        candidate.unlink(missing_ok=True)
        try:
            candidate.parent.rmdir()
        except OSError:
            pass

    def _validated_source_blob_path(self, path: Path) -> Path:
        source_root = self.sources_dir.resolve()
        candidate = Path(os.path.abspath(path))
        try:
            relative = candidate.relative_to(source_root)
        except ValueError as exc:
            raise DataXValidationError("Invalid Data X source path.") from exc
        if len(relative.parts) != 2:
            raise DataXValidationError("Invalid Data X source path.")
        project_id, filename = relative.parts
        _safe_id(project_id)
        suffix = Path(filename).suffix
        digest = filename[: -len(suffix)] if suffix else ""
        _safe_sha256(digest)
        _safe_suffix(suffix)
        if candidate.parent.is_symlink() or candidate.parent.resolve() != source_root / project_id:
            raise DataXValidationError("Invalid Data X source directory.")
        return candidate

    @staticmethod
    def new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    @staticmethod
    def now() -> float:
        return time.time()

    @staticmethod
    def hash_json(value: Any) -> str:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _safe_id(value: str) -> str:
    if not value or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in value):
        raise DataXValidationError("Invalid Data X identifier.")
    return value


def _safe_sha256(value: str) -> str:
    clean = str(value or "").lower()
    if len(clean) != 64 or any(char not in "0123456789abcdef" for char in clean):
        raise DataXValidationError("Invalid Data X source digest.")
    return clean


def _safe_suffix(value: str) -> str:
    clean = str(value or "").lower()
    if (
        len(clean) < 2
        or len(clean) > 16
        or not clean.startswith(".")
        or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789" for char in clean[1:])
    ):
        raise DataXValidationError("Invalid Data X source suffix.")
    return clean
