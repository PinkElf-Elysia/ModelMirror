from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_STORAGE_KEY = re.compile(
    r"^(?P<namespace>blobs|artifacts)/(?P<prefix>[0-9a-f]{2})/"
    r"(?P<identifier>[0-9a-f]{32})\.(?:blob|artifact)$"
)


class FileBlobStoreError(Exception):
    """Base error for the private file-asset blob store."""


class InvalidStorageKey(FileBlobStoreError):
    """Raised when a caller supplies a path instead of an opaque storage key."""


class BlobValidationError(FileBlobStoreError):
    """Raised when bytes do not satisfy the declared upload boundary."""


@dataclass(frozen=True, slots=True)
class BlobWriteReceipt:
    storage_key: str
    sha256: str
    byte_size: int


class FileBlobStore:
    """Atomic, path-confined storage for originals and derived artifacts.

    User-visible filenames are deliberately absent from this interface.  A caller
    receives only a random storage key suitable for persisting in SQLite.
    """

    def __init__(self, storage_dir: str | Path) -> None:
        unresolved_root = Path(os.path.abspath(os.fspath(storage_dir)))
        _reject_reparse_chain(unresolved_root)
        unresolved_root.mkdir(parents=True, exist_ok=True)
        _reject_reparse_chain(unresolved_root)
        self.storage_dir = unresolved_root.resolve(strict=True)
        self.blob_dir = self.storage_dir / "blobs"
        self.artifact_dir = self.storage_dir / "artifacts"
        self.staging_dir = self.storage_dir / ".staging"
        for directory in (self.blob_dir, self.artifact_dir, self.staging_dir):
            directory.mkdir(parents=True, exist_ok=True)
            self._reject_managed_reparse(directory)

    def write_bytes(
        self,
        content: bytes,
        *,
        namespace: str = "blobs",
        max_bytes: int | None = None,
        expected_sha256: str | None = None,
    ) -> BlobWriteReceipt:
        return self.write_stream(
            (content,),
            namespace=namespace,
            max_bytes=max_bytes,
            expected_sha256=expected_sha256,
        )

    def write_stream(
        self,
        chunks: Iterable[bytes],
        *,
        namespace: str = "blobs",
        max_bytes: int | None = None,
        expected_sha256: str | None = None,
    ) -> BlobWriteReceipt:
        if namespace not in {"blobs", "artifacts"}:
            raise InvalidStorageKey("unsupported_blob_namespace")
        if max_bytes is not None and max_bytes < 1:
            raise ValueError("max_bytes must be positive")

        expected = str(expected_sha256 or "").strip().lower() or None
        if expected is not None and not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise BlobValidationError("invalid_expected_sha256")

        # The directory may have been replaced after service initialization.
        # Re-check the unresolved chain immediately before opening a temp file.
        self._reject_managed_reparse(self.staging_dir)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="upload-", suffix=".part", dir=self.staging_dir
        )
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        byte_size = 0
        try:
            with os.fdopen(descriptor, "wb") as output:
                for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        raise BlobValidationError("blob_chunk_must_be_bytes")
                    if not chunk:
                        continue
                    byte_size += len(chunk)
                    if max_bytes is not None and byte_size > max_bytes:
                        raise BlobValidationError("blob_too_large")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())

            if byte_size == 0:
                raise BlobValidationError("empty_blob")
            actual_sha256 = digest.hexdigest()
            if expected is not None and actual_sha256 != expected:
                raise BlobValidationError("sha256_mismatch")

            identifier = uuid.uuid4().hex
            suffix = "blob" if namespace == "blobs" else "artifact"
            storage_key = f"{namespace}/{identifier[:2]}/{identifier}.{suffix}"
            destination = self._path_for_key(storage_key, require_exists=False)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination = self._path_for_key(storage_key, require_exists=False)
            os.replace(temporary, destination)
            return BlobWriteReceipt(
                storage_key=storage_key,
                sha256=actual_sha256,
                byte_size=byte_size,
            )
        finally:
            temporary.unlink(missing_ok=True)

    def read_bytes(self, storage_key: str) -> bytes:
        return self._path_for_key(storage_key).read_bytes()

    def exists(self, storage_key: str) -> bool:
        try:
            path = self._path_for_key(storage_key, require_exists=False)
        except InvalidStorageKey:
            return False
        return path.is_file() and not path.is_symlink()

    def delete(self, storage_key: str) -> bool:
        path = self._path_for_key(storage_key, require_exists=False)
        if path.is_symlink():
            raise InvalidStorageKey("storage_key_resolves_to_symlink")
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def list_storage_keys(
        self, *, older_than_epoch: float | None = None
    ) -> tuple[str, ...]:
        """Return only valid, regular blobs under the two managed namespaces."""

        keys: list[str] = []
        for namespace_dir in (self.blob_dir, self.artifact_dir):
            self._reject_managed_reparse(namespace_dir)
            with os.scandir(namespace_dir) as prefixes:
                for prefix in prefixes:
                    prefix_stat = prefix.stat(follow_symlinks=False)
                    if _is_reparse_stat(prefix_stat):
                        raise InvalidStorageKey("managed_storage_contains_reparse_point")
                    if not prefix.is_dir(follow_symlinks=False):
                        continue
                    with os.scandir(prefix.path) as entries:
                        for entry in entries:
                            entry_stat = entry.stat(follow_symlinks=False)
                            if _is_reparse_stat(entry_stat):
                                raise InvalidStorageKey(
                                    "managed_storage_contains_reparse_point"
                                )
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            if (
                                older_than_epoch is not None
                                and entry_stat.st_mtime > older_than_epoch
                            ):
                                continue
                            path = Path(entry.path)
                            key = path.relative_to(self.storage_dir).as_posix()
                            try:
                                self._path_for_key(key)
                            except (FileNotFoundError, InvalidStorageKey):
                                continue
                            keys.append(key)
        return tuple(sorted(keys))

    def cleanup_staging(self, *, older_than_epoch: float | None = None) -> int:
        """Remove abandoned atomic-write fragments without following symlinks."""

        self._reject_managed_reparse(self.staging_dir)
        deleted = 0
        with os.scandir(self.staging_dir) as entries:
            for entry in entries:
                if not re.fullmatch(r"upload-[A-Za-z0-9_.-]+\.part", entry.name):
                    continue
                entry_stat = entry.stat(follow_symlinks=False)
                if _is_reparse_stat(entry_stat):
                    raise InvalidStorageKey("managed_storage_contains_reparse_point")
                if not entry.is_file(follow_symlinks=False):
                    continue
                if (
                    older_than_epoch is not None
                    and entry_stat.st_mtime > older_than_epoch
                ):
                    continue
                try:
                    Path(entry.path).unlink()
                except FileNotFoundError:
                    continue
                deleted += 1
        return deleted

    def _path_for_key(
        self, storage_key: str, *, require_exists: bool = True
    ) -> Path:
        match = _STORAGE_KEY.fullmatch(str(storage_key or ""))
        if match is None or match.group("prefix") != match.group("identifier")[:2]:
            raise InvalidStorageKey("invalid_storage_key")
        unresolved = self.storage_dir.joinpath(*storage_key.split("/"))
        self._reject_managed_reparse(unresolved)
        path = unresolved.resolve(strict=False)
        if not path.is_relative_to(self.storage_dir):
            raise InvalidStorageKey("storage_key_escaped_root")
        if require_exists and (not path.is_file() or path.is_symlink()):
            raise FileNotFoundError(storage_key)
        return path

    def _reject_managed_reparse(self, path: Path) -> None:
        try:
            relative = path.relative_to(self.storage_dir)
        except ValueError as exc:
            raise InvalidStorageKey("storage_path_escaped_root") from exc
        current = self.storage_dir
        _reject_reparse(current)
        for part in relative.parts:
            current = current / part
            _reject_reparse(current)


def _reject_reparse_chain(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        _reject_reparse(current)


def _reject_reparse(path: Path) -> None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return
    if _is_reparse_stat(metadata):
        raise InvalidStorageKey("managed_storage_contains_reparse_point")


def _is_reparse_stat(metadata: os.stat_result) -> bool:
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)
