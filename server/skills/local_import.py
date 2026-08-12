from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import shutil
import stat
import threading
import time
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Literal, Mapping, Sequence

from .package_validation import (
    SkillPackageIssue,
    _parse_skill_frontmatter,
    _scan_credentials,
    compute_skill_content_digest,
)
from .trust_scanner import (
    MAX_TRUST_DIRECTORY_DEPTH,
    MAX_TRUST_FILE_BYTES,
    MAX_TRUST_FILES,
    MAX_TRUST_PATH_CHARS,
    MAX_TRUST_TOTAL_BYTES,
    SKILL_TRUST_SCANNER_VERSION,
    SkillTrustTreeEntry,
    scan_local_skill_trust_receipt,
    sha256_json,
)


LOCAL_IMPORT_STORE_VERSION = 1
LOCAL_IMPORT_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
LOCAL_IMPORT_MAX_ACTIVE = 100
LOCAL_IMPORT_MAX_STORAGE_BYTES = 1024 * 1024 * 1024
LOCAL_IMPORT_PREVIEW_BYTES = 256 * 1024

ImportState = Literal[
    "scanning",
    "ready",
    "confirmation_required",
    "blocked",
    "failed",
    "installed",
    "superseded",
    "archived",
    "stale",
]
TransportKind = Literal["zip", "folder"]

_LOCAL_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul", "clock$"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)
_ACTIVE_PREVIEW_SUFFIXES = {".html", ".htm", ".svg", ".xml"}
_ALLOWED_ZIP_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}


class SkillLocalImportError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "skill_import_invalid_transport",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


class SkillLocalImportNotFoundError(SkillLocalImportError):
    pass


class SkillLocalImportConflictError(SkillLocalImportError):
    pass


class SkillLocalImportStorageError(SkillLocalImportError):
    pass


@dataclass(frozen=True, slots=True)
class ImportedFile:
    path: str
    content: bytes
    mode: str = "100644"


@dataclass(slots=True)
class LocalSkillImport:
    import_id: str
    revision: int
    content_revision: int
    state: ImportState
    transport_kind: TransportKind
    transport_digest: str
    local_skill_id: str | None = None
    declared_name: str | None = None
    package_digest: str | None = None
    trust_receipt: dict[str, Any] | None = None
    file_manifest: list[dict[str, Any]] = field(default_factory=list)
    ignored_entries: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None
    installed_skill_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def trust_fingerprint(self) -> str | None:
        if not self.trust_receipt:
            return None
        value = str(self.trust_receipt.get("trustFingerprint") or "")
        return value or None

    @property
    def receipt_id(self) -> str | None:
        if not self.trust_receipt:
            return None
        value = str(self.trust_receipt.get("receiptId") or "")
        return value or None

    def serialize(self, *, include_receipt: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "version": LOCAL_IMPORT_STORE_VERSION,
            "importId": self.import_id,
            "revision": self.revision,
            "contentRevision": self.content_revision,
            "state": self.state,
            "transportKind": self.transport_kind,
            "transportDigest": self.transport_digest,
            "localSkillId": self.local_skill_id,
            "declaredName": self.declared_name,
            "packageDigest": self.package_digest,
            "receiptId": self.receipt_id,
            "trustFingerprint": self.trust_fingerprint,
            "fileManifest": copy.deepcopy(self.file_manifest),
            "ignoredEntries": copy.deepcopy(self.ignored_entries),
            "errorCode": self.error_code,
            "installedSkillId": self.installed_skill_id,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }
        if include_receipt:
            payload["trustReceipt"] = copy.deepcopy(self.trust_receipt)
        return payload


def _validate_local_skill_id(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    clean = str(value).strip()
    if len(clean) > 64 or not _LOCAL_ID_RE.fullmatch(clean):
        raise SkillLocalImportError(
            "Local Skill ID must be 1-64 characters of kebab-case text.",
            code="skill_import_invalid_transport",
        )
    if clean.casefold() in _WINDOWS_RESERVED:
        raise SkillLocalImportError(
            "Local Skill ID is reserved on Windows.",
            code="skill_import_invalid_transport",
        )
    credential_issues: list[SkillPackageIssue] = []
    _scan_credentials(None, clean, credential_issues)
    if credential_issues:
        raise SkillLocalImportError(
            "Local Skill ID contains credential-like material.",
            code="skill_import_invalid_transport",
        )
    return clean


def _normalize_path(raw_path: str) -> str:
    if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
        raise SkillLocalImportError(
            "The upload contains an invalid path.", code="skill_import_path_unsafe"
        )
    candidate = unicodedata.normalize("NFC", raw_path.replace("\\", "/"))
    if candidate.startswith("/") or _DRIVE_RE.match(candidate):
        raise SkillLocalImportError(
            "The upload contains an absolute path.", code="skill_import_path_unsafe"
        )
    parts = candidate.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SkillLocalImportError(
            "The upload contains an unsafe relative path.", code="skill_import_path_unsafe"
        )
    if len(parts) > MAX_TRUST_DIRECTORY_DEPTH + 1 or len(candidate) > MAX_TRUST_PATH_CHARS:
        raise SkillLocalImportError(
            "The upload path exceeds the import limit.", code="skill_import_limit_exceeded"
        )
    for part in parts:
        if any(ord(character) < 32 for character in part):
            raise SkillLocalImportError(
                "The upload path contains control characters.", code="skill_import_path_unsafe"
            )
        basename = part.rstrip(". ").split(".", 1)[0].casefold()
        if part != part.rstrip(". ") or basename in _WINDOWS_RESERVED:
            raise SkillLocalImportError(
                "The upload path is unsafe on Windows.", code="skill_import_path_unsafe"
            )
    if any(part.casefold() == ".git" for part in parts):
        raise SkillLocalImportError(
            "Git metadata is not allowed in a local Skill import.",
            code="skill_import_path_unsafe",
        )
    return PurePosixPath(*parts).as_posix()


def _noise_reason(path: str) -> str | None:
    parts = PurePosixPath(path).parts
    if parts and parts[0] == "__MACOSX":
        return "macos_metadata"
    if parts and (parts[-1] == ".DS_Store" or parts[-1].startswith("._")):
        return "macos_metadata"
    return None


def _strip_single_wrapper(files: Sequence[ImportedFile]) -> list[ImportedFile]:
    by_path = {item.path: item for item in files}
    if "SKILL.md" in by_path:
        return list(files)
    roots = {PurePosixPath(item.path).parts[0] for item in files}
    if len(roots) != 1:
        raise SkillLocalImportError(
            "The upload contains multiple top-level Skill roots.",
            code="skill_import_multiple_roots",
        )
    root = next(iter(roots))
    prefix = f"{root}/"
    stripped = [
        ImportedFile(item.path[len(prefix) :], item.content, item.mode)
        for item in files
        if item.path.startswith(prefix)
    ]
    if not any(item.path == "SKILL.md" for item in stripped):
        raise SkillLocalImportError(
            "The upload does not contain one Skill root with SKILL.md.",
            code="skill_import_multiple_roots",
        )
    return stripped


def _validate_file_set(files: Sequence[ImportedFile]) -> list[ImportedFile]:
    if not files:
        raise SkillLocalImportError("The upload is empty.")
    if len(files) > MAX_TRUST_FILES:
        raise SkillLocalImportError(
            "The upload exceeds the file-count limit.", code="skill_import_limit_exceeded"
        )
    total = sum(len(item.content) for item in files)
    if total > MAX_TRUST_TOTAL_BYTES or any(
        len(item.content) > MAX_TRUST_FILE_BYTES for item in files
    ):
        raise SkillLocalImportError(
            "The upload exceeds the expanded-size limit.", code="skill_import_limit_exceeded"
        )
    seen: dict[str, str] = {}
    paths: set[str] = set()
    ordered: list[ImportedFile] = []
    for item in sorted(files, key=lambda value: value.path.encode("utf-8", "surrogatepass")):
        path = _normalize_path(item.path)
        identity = unicodedata.normalize("NFC", path).casefold()
        if identity in seen:
            raise SkillLocalImportError(
                "Upload paths collide after case and Unicode normalization.",
                code="skill_import_path_unsafe",
            )
        seen[identity] = path
        paths.add(path)
        ordered.append(ImportedFile(path, bytes(item.content), item.mode))
    for path in paths:
        parts = PurePosixPath(path).parts
        for index in range(1, len(parts)):
            if PurePosixPath(*parts[:index]).as_posix() in paths:
                raise SkillLocalImportError(
                    "A package path cannot be both a file and a directory.",
                    code="skill_import_path_unsafe",
                )
    if "SKILL.md" not in paths:
        raise SkillLocalImportError(
            "The upload does not contain SKILL.md.", code="skill_import_multiple_roots"
        )
    if any(path != "SKILL.md" and PurePosixPath(path).name == "SKILL.md" for path in paths):
        raise SkillLocalImportError(
            "The upload contains multiple Skill roots.",
            code="skill_import_multiple_roots",
        )
    return ordered


def normalize_folder_upload(
    items: Sequence[tuple[str, bytes]],
) -> tuple[list[ImportedFile], str, list[dict[str, Any]]]:
    normalized: list[ImportedFile] = []
    ignored_count = 0
    transport_hasher = hashlib.sha256()
    prepared = sorted(
        ((_normalize_path(raw_path), bytes(content)) for raw_path, content in items),
        key=lambda item: item[0].encode("utf-8", "surrogatepass"),
    )
    for path, raw in prepared:
        transport_hasher.update(len(path.encode("utf-8")).to_bytes(4, "big"))
        transport_hasher.update(path.encode("utf-8"))
        transport_hasher.update(len(raw).to_bytes(8, "big"))
        transport_hasher.update(raw)
        if _noise_reason(path):
            ignored_count += 1
            continue
        normalized.append(ImportedFile(path, raw))
    normalized = _validate_file_set(_strip_single_wrapper(normalized))
    ignored = ([{"reason": "macos_metadata", "count": ignored_count}] if ignored_count else [])
    return normalized, transport_hasher.hexdigest(), ignored


def normalize_zip_upload(
    archive: bytes,
) -> tuple[list[ImportedFile], str, list[dict[str, Any]]]:
    raw_archive = bytes(archive)
    if len(raw_archive) > LOCAL_IMPORT_MAX_ARCHIVE_BYTES:
        raise SkillLocalImportError(
            "The ZIP upload exceeds the compressed-size limit.",
            code="skill_import_limit_exceeded",
        )
    imported: list[ImportedFile] = []
    ignored_count = 0
    actual_total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw_archive), "r") as bundle:
            if len(bundle.infolist()) > MAX_TRUST_FILES + 128:
                raise SkillLocalImportError(
                    "The ZIP contains too many entries.", code="skill_import_limit_exceeded"
                )
            for info in bundle.infolist():
                if info.flag_bits & 0x1:
                    raise SkillLocalImportError(
                        "Encrypted ZIP entries are not supported.",
                        code="skill_import_archive_encrypted",
                    )
                if info.compress_type not in _ALLOWED_ZIP_COMPRESSION:
                    raise SkillLocalImportError(
                        "The ZIP uses an unsupported compression method.",
                        code="skill_import_archive_invalid",
                    )
                raw_name = info.filename.rstrip("/") if info.is_dir() else info.filename
                if not raw_name:
                    continue
                path = _normalize_path(raw_name)
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                file_type = stat.S_IFMT(unix_mode)
                if info.is_dir():
                    if file_type not in {0, stat.S_IFDIR}:
                        raise SkillLocalImportError(
                            "The ZIP contains an unsupported directory entry.",
                            code="skill_import_archive_invalid",
                        )
                    continue
                if file_type not in {0, stat.S_IFREG}:
                    raise SkillLocalImportError(
                        "Links and special files are not allowed in ZIP imports.",
                        code="skill_import_path_unsafe",
                    )
                if info.file_size > MAX_TRUST_FILE_BYTES:
                    raise SkillLocalImportError(
                        "A ZIP entry exceeds the file-size limit.",
                        code="skill_import_limit_exceeded",
                    )
                chunks: list[bytes] = []
                file_size = 0
                with bundle.open(info, "r") as source:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        file_size += len(chunk)
                        actual_total += len(chunk)
                        if file_size > MAX_TRUST_FILE_BYTES or actual_total > MAX_TRUST_TOTAL_BYTES:
                            raise SkillLocalImportError(
                                "The expanded ZIP exceeds the import limit.",
                                code="skill_import_limit_exceeded",
                            )
                        chunks.append(chunk)
                content = b"".join(chunks)
                if len(content) != info.file_size:
                    raise SkillLocalImportError(
                        "A ZIP entry size does not match its metadata.",
                        code="skill_import_archive_invalid",
                    )
                if _noise_reason(path):
                    ignored_count += 1
                    continue
                mode = "100755" if unix_mode & 0o111 else "100644"
                imported.append(ImportedFile(path, content, mode))
    except SkillLocalImportError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise SkillLocalImportError(
            "The ZIP upload is invalid.", code="skill_import_archive_invalid"
        ) from exc
    imported = _validate_file_set(_strip_single_wrapper(imported))
    ignored = ([{"reason": "macos_metadata", "count": ignored_count}] if ignored_count else [])
    return imported, hashlib.sha256(raw_archive).hexdigest(), ignored


def _declared_name(files: Sequence[ImportedFile]) -> str | None:
    markdown = next(item.content for item in files if item.path == "SKILL.md")
    try:
        text = markdown.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    issues: list[SkillPackageIssue] = []
    parsed = _parse_skill_frontmatter(text, issues)
    if not isinstance(parsed, Mapping):
        return None
    value = parsed.get("name")
    if not isinstance(value, str):
        return None
    clean = value.strip()
    return clean if len(clean) <= 64 and _LOCAL_ID_RE.fullmatch(clean) else None


def _tree_entries(files: Sequence[ImportedFile]) -> list[SkillTrustTreeEntry]:
    return [
        SkillTrustTreeEntry(
            path=item.path,
            mode=item.mode,
            object_type="blob",
            object_id=hashlib.sha1(item.content).hexdigest(),
            size=len(item.content),
            content=item.content,
        )
        for item in files
    ]


def _package_manifest(files: Sequence[ImportedFile]) -> list[dict[str, Any]]:
    return [
        {
            "path": item.path,
            "mode": item.mode,
            "sizeBytes": len(item.content),
            "sha256": hashlib.sha256(item.content).hexdigest(),
        }
        for item in files
    ]


class SkillLocalImportStore:
    def __init__(
        self,
        storage_dir: str | os.PathLike[str] | None = None,
        *,
        enabled: bool | None = None,
        max_active: int = LOCAL_IMPORT_MAX_ACTIVE,
        max_storage_bytes: int = LOCAL_IMPORT_MAX_STORAGE_BYTES,
    ) -> None:
        default_dir = Path(__file__).resolve().parent / "imports"
        self.root = Path(
            storage_dir
            or os.getenv("SKILL_LOCAL_IMPORT_STORAGE_DIR", "").strip()
            or default_dir
        ).resolve()
        self.enabled = (
            str(os.getenv("SKILL_LOCAL_IMPORT_ENABLED", "false")).strip().casefold()
            in {"1", "true", "yes", "on"}
            if enabled is None
            else bool(enabled)
        )
        self.max_active = max(1, int(max_active))
        self.max_storage_bytes = max(MAX_TRUST_TOTAL_BYTES, int(max_storage_bytes))
        self.index_path = self.root / "imports.json"
        self.packages_root = self.root / "packages"
        self.tmp_root = self.root / "tmp"
        self._lock = threading.RLock()
        self._records: dict[str, LocalSkillImport] = {}
        self._quarantine: list[dict[str, Any]] = []
        self._load_error: str | None = None
        self._load()
        if self._load_error is None:
            self._recover_temp_dirs()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "available": self._load_error is None,
                "version": LOCAL_IMPORT_STORE_VERSION,
                "scannerVersion": SKILL_TRUST_SCANNER_VERSION,
                "supportedTransports": ["zip", "folder"],
                "limits": {
                    "archiveBytes": LOCAL_IMPORT_MAX_ARCHIVE_BYTES,
                    "fileCount": MAX_TRUST_FILES,
                    "fileBytes": MAX_TRUST_FILE_BYTES,
                    "expandedBytes": MAX_TRUST_TOTAL_BYTES,
                    "storageBytes": self.max_storage_bytes,
                    "activeImports": self.max_active,
                },
                "errorCode": self._load_error,
            }

    def _load(self) -> None:
        if not self.index_path.exists():
            return
        try:
            raw = self.index_path.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, Mapping) or payload.get("version") != LOCAL_IMPORT_STORE_VERSION:
                raise ValueError("unsupported local import store")
            records = payload.get("imports")
            if not isinstance(records, list):
                raise ValueError("invalid local import records")
            stored_quarantine = payload.get("quarantine") or []
            if not isinstance(stored_quarantine, list):
                raise ValueError("invalid local import quarantine")
            quarantine = []
            for entry in stored_quarantine:
                index_value = entry.get("index") if isinstance(entry, Mapping) else None
                size_value = entry.get("sizeBytes") if isinstance(entry, Mapping) else None
                if (
                    not isinstance(entry, Mapping)
                    or not isinstance(index_value, int)
                    or isinstance(index_value, bool)
                    or index_value < 0
                    or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256") or ""))
                    or not isinstance(size_value, int)
                    or isinstance(size_value, bool)
                    or size_value < 0
                ):
                    raise ValueError("invalid local import quarantine entry")
                quarantine.append(
                    {
                        "index": index_value,
                        "sha256": str(entry["sha256"]),
                        "sizeBytes": size_value,
                    }
                )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self._load_error = "skill_import_storage_unavailable"
            return
        decoded: dict[str, LocalSkillImport] = {}
        for index, raw_record in enumerate(records):
            try:
                record = self._decode_record(raw_record)
                if record.import_id in decoded:
                    raise ValueError("duplicate import ID")
                decoded[record.import_id] = record
            except (TypeError, ValueError):
                encoded = json.dumps(raw_record, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
                quarantine.append(
                    {
                        "index": index,
                        "sha256": hashlib.sha256(encoded).hexdigest(),
                        "sizeBytes": len(encoded),
                    }
                )
        self._records = decoded
        self._quarantine = quarantine

    @staticmethod
    def _decode_record(payload: Any) -> LocalSkillImport:
        if not isinstance(payload, Mapping):
            raise ValueError("record must be a mapping")
        import_id = str(payload.get("importId") or "")
        if not re.fullmatch(r"skillimport_[0-9a-f]{32}", import_id):
            raise ValueError("invalid import ID")
        revision = int(payload.get("revision") or 0)
        content_revision = int(payload.get("contentRevision") or 0)
        state = str(payload.get("state") or "")
        transport_kind = str(payload.get("transportKind") or "")
        transport_digest = str(payload.get("transportDigest") or "")
        if revision < 1 or content_revision < 1 or content_revision > revision or state not in {
            "scanning", "ready", "confirmation_required", "blocked", "failed",
            "installed", "superseded", "archived", "stale",
        }:
            raise ValueError("invalid import state")
        if transport_kind not in {"zip", "folder"} or not re.fullmatch(r"[0-9a-f]{64}", transport_digest):
            raise ValueError("invalid import transport")
        receipt = payload.get("trustReceipt")
        if receipt is not None and not isinstance(receipt, Mapping):
            raise ValueError("invalid trust receipt")
        package_digest = str(payload.get("packageDigest") or "") or None
        if package_digest is not None and not re.fullmatch(r"[0-9a-f]{64}", package_digest):
            raise ValueError("invalid package digest")
        if receipt is not None:
            fingerprint = str(receipt.get("trustFingerprint") or "")
            if not re.fullmatch(r"[0-9a-f]{64}", fingerprint) or sha256_json(
                {key: value for key, value in receipt.items() if key != "trustFingerprint"}
            ) != fingerprint:
                raise ValueError("invalid trust receipt fingerprint")
            source = receipt.get("source")
            if (
                not isinstance(source, Mapping)
                or source.get("kind") != "local_import"
                or source.get("importId") != import_id
                or source.get("importRevision") != content_revision
                or source.get("transportKind") != transport_kind
                or source.get("transportDigest") != transport_digest
                or receipt.get("packageDigest") != package_digest
            ):
                raise ValueError("local trust receipt does not match its import")
        manifest = payload.get("fileManifest") or []
        ignored = payload.get("ignoredEntries") or []
        if not isinstance(manifest, list) or not isinstance(ignored, list):
            raise ValueError("invalid import manifest")
        for entry in manifest:
            if not isinstance(entry, Mapping):
                raise ValueError("invalid import manifest entry")
            _normalize_path(str(entry.get("path") or ""))
            if str(entry.get("mode") or "") not in {"100644", "100755"}:
                raise ValueError("invalid import manifest mode")
            if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256") or "")):
                raise ValueError("invalid import manifest digest")
            if int(entry.get("sizeBytes") or -1) < 0:
                raise ValueError("invalid import manifest size")
        return LocalSkillImport(
            import_id=import_id,
            revision=revision,
            content_revision=content_revision,
            state=state,  # type: ignore[arg-type]
            transport_kind=transport_kind,  # type: ignore[arg-type]
            transport_digest=transport_digest,
            local_skill_id=_validate_local_skill_id(payload.get("localSkillId")),
            declared_name=_validate_local_skill_id(payload.get("declaredName")),
            package_digest=package_digest,
            trust_receipt=copy.deepcopy(dict(receipt)) if receipt else None,
            file_manifest=copy.deepcopy(manifest),
            ignored_entries=copy.deepcopy(ignored),
            error_code=str(payload.get("errorCode") or "") or None,
            installed_skill_id=str(payload.get("installedSkillId") or "") or None,
            created_at=float(payload.get("createdAt") or 0.0),
            updated_at=float(payload.get("updatedAt") or 0.0),
        )

    def _ensure_available(self, *, mutation: bool = False) -> None:
        if self._load_error:
            raise SkillLocalImportStorageError(
                "Local Skill import storage is unavailable.",
                code="skill_import_storage_unavailable",
            )
        if mutation and not self.enabled:
            raise SkillLocalImportError(
                "Local Skill import is disabled.", code="skill_import_disabled"
            )

    def _recover_temp_dirs(self) -> None:
        try:
            if self.tmp_root.is_symlink():
                raise OSError("Local Skill import temp storage cannot be a link.")
            if self.tmp_root.exists():
                for child in self.tmp_root.iterdir():
                    if child.is_symlink() or child.is_file():
                        child.unlink(missing_ok=True)
                    elif child.is_dir():
                        shutil.rmtree(child)
            if self.packages_root.is_symlink():
                raise OSError("Local Skill package storage cannot be a link.")
            referenced = {
                item.package_digest
                for item in self._records.values()
                if item.package_digest and item.file_manifest
            }
            if self.packages_root.exists() and not self._quarantine:
                for child in self.packages_root.iterdir():
                    if (
                        child.is_symlink()
                        or not child.is_dir()
                        or not re.fullmatch(r"[0-9a-f]{64}", child.name)
                    ):
                        raise OSError("Local Skill package storage contains an unsafe entry.")
                    if child.name not in referenced:
                        shutil.rmtree(child)
            interrupted = {
                import_id: item
                for import_id, item in self._records.items()
                if item.state == "scanning"
            }
            if interrupted:
                now = time.time()
                records = dict(self._records)
                for import_id, item in interrupted.items():
                    recovered = copy.deepcopy(item)
                    recovered.revision += 1
                    recovered.state = "failed"
                    recovered.error_code = "skill_import_scan_failed"
                    recovered.updated_at = now
                    records[import_id] = recovered
                self._save_records_unlocked(records)
                self._records = records
        except (OSError, SkillLocalImportStorageError):
            self._load_error = "skill_import_storage_unavailable"

    def _save_records_unlocked(self, records: Mapping[str, LocalSkillImport]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": LOCAL_IMPORT_STORE_VERSION,
            "imports": [records[key].serialize(include_receipt=True) for key in sorted(records)],
            "quarantine": copy.deepcopy(self._quarantine),
        }
        temp_path = self.index_path.with_suffix(".json.tmp")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        try:
            with temp_path.open("wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.index_path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise SkillLocalImportStorageError(
                "Local Skill import metadata could not be persisted.",
                code="skill_import_storage_unavailable",
            ) from exc

    def _storage_bytes_unlocked(self) -> int:
        if not self.root.exists():
            return 0
        return sum(
            path.stat().st_size
            for path in self.root.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path not in {self.index_path, self.index_path.with_suffix(".json.tmp")}
        )

    def list_imports(self, *, include_archived: bool = False) -> list[LocalSkillImport]:
        with self._lock:
            self._ensure_available()
            records = [
                copy.deepcopy(item)
                for item in self._records.values()
                if include_archived or item.state != "archived"
            ]
        return sorted(records, key=lambda item: (-item.updated_at, item.import_id))

    def require(self, import_id: str) -> LocalSkillImport:
        with self._lock:
            self._ensure_available()
            item = self._records.get(str(import_id))
            if item is None:
                raise SkillLocalImportNotFoundError(
                    "Local Skill import was not found.", code="skill_import_not_found"
                )
            return copy.deepcopy(item)

    def create_from_zip(
        self,
        archive: bytes,
        *,
        local_skill_id: str | None = None,
    ) -> LocalSkillImport:
        return self._create(
            "zip",
            lambda: normalize_zip_upload(archive),
            local_skill_id=local_skill_id,
            fallback_transport_digest=hashlib.sha256(bytes(archive)).hexdigest(),
        )

    def create_from_folder(
        self,
        items: Sequence[tuple[str, bytes]],
        *,
        local_skill_id: str | None = None,
    ) -> LocalSkillImport:
        fallback = hashlib.sha256()
        for path, content in items:
            fallback.update(str(path).encode("utf-8", errors="replace"))
            fallback.update(bytes(content))
        return self._create(
            "folder",
            lambda: normalize_folder_upload(items),
            local_skill_id=local_skill_id,
            fallback_transport_digest=fallback.hexdigest(),
        )

    def _create(
        self,
        transport_kind: TransportKind,
        normalizer: Any,
        *,
        local_skill_id: str | None,
        fallback_transport_digest: str,
    ) -> LocalSkillImport:
        clean_local_id = _validate_local_skill_id(local_skill_id)
        import_id = "skillimport_" + uuid.uuid4().hex
        now = time.time()
        pending = LocalSkillImport(
            import_id=import_id,
            revision=1,
            content_revision=1,
            state="scanning",
            transport_kind=transport_kind,
            transport_digest=fallback_transport_digest,
            local_skill_id=clean_local_id,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._ensure_available(mutation=True)
            active_count = sum(item.state != "archived" for item in self._records.values())
            if active_count >= self.max_active:
                raise SkillLocalImportError(
                    "The local Skill import count limit has been reached.",
                    code="skill_import_limit_exceeded",
                )
            records = dict(self._records)
            records[import_id] = pending
            self._save_records_unlocked(records)
            self._records = records
        try:
            files, transport_digest, ignored = normalizer()
            return self._publish_import(
                import_id=import_id,
                transport_kind=transport_kind,
                transport_digest=transport_digest,
                files=files,
                ignored=ignored,
                local_skill_id=clean_local_id,
            )
        except SkillLocalImportError as exc:
            self._record_failure(
                import_id,
                transport_kind,
                fallback_transport_digest,
                exc.code,
                clean_local_id,
            )
            raise
        except Exception as exc:
            self._record_failure(
                import_id,
                transport_kind,
                fallback_transport_digest,
                "skill_import_scan_failed",
                clean_local_id,
            )
            raise SkillLocalImportError(
                "The local Skill package could not be completely scanned.",
                code="skill_import_scan_failed",
            ) from exc

    def _publish_import(
        self,
        *,
        import_id: str,
        transport_kind: TransportKind,
        transport_digest: str,
        files: Sequence[ImportedFile],
        ignored: list[dict[str, Any]],
        local_skill_id: str | None,
    ) -> LocalSkillImport:
        entries = _tree_entries(files)
        receipt = scan_local_skill_trust_receipt(
            import_id=import_id,
            import_revision=1,
            transport_kind=transport_kind,
            transport_digest=transport_digest,
            entries=entries,
        )
        package_digest = str(receipt.get("packageDigest") or "") or None
        if package_digest is None:
            raise SkillLocalImportError(
                "The local Skill package could not be completely scanned.",
                code="skill_import_scan_failed",
            )
        declared_name = _declared_name(files)
        selected_id = local_skill_id or declared_name
        policy = str(receipt.get("installPolicy") or "block")
        state: ImportState = (
            "blocked" if policy == "block" else "ready" if policy == "allow" else "confirmation_required"
        )
        keep_source = state != "blocked"
        with self._lock:
            self._ensure_available(mutation=True)
            pending = self._records.get(import_id)
            if pending is None or pending.state != "scanning":
                raise SkillLocalImportConflictError(
                    "Local Skill import state changed during scanning.",
                    code="skill_import_stale",
                )
            duplicate = next(
                (
                    item
                    for item in self._records.values()
                    if item.import_id != import_id
                    and item.package_digest == package_digest
                    and item.state not in {"archived", "failed", "blocked"}
                ),
                None,
            )
            if duplicate is not None:
                records = dict(self._records)
                del records[import_id]
                self._save_records_unlocked(records)
                self._records = records
                return copy.deepcopy(duplicate)
            now = time.time()
            record = LocalSkillImport(
                import_id=import_id,
                revision=pending.revision + 1,
                content_revision=pending.content_revision,
                state=state,
                transport_kind=transport_kind,
                transport_digest=transport_digest,
                local_skill_id=selected_id if keep_source else None,
                declared_name=declared_name if keep_source else None,
                package_digest=package_digest,
                trust_receipt=receipt,
                file_manifest=_package_manifest(files) if keep_source else [],
                ignored_entries=ignored,
                error_code="skill_import_blocked" if state == "blocked" else None,
                created_at=pending.created_at,
                updated_at=now,
            )
            package_created = False
            if keep_source:
                package_bytes = sum(len(item.content) for item in files)
                if self._storage_bytes_unlocked() + package_bytes > self.max_storage_bytes:
                    raise SkillLocalImportError(
                        "The local Skill import storage quota has been reached.",
                        code="skill_import_limit_exceeded",
                    )
                package_created = self._write_package_unlocked(package_digest, files)
            records = dict(self._records)
            records[record.import_id] = record
            try:
                self._save_records_unlocked(records)
            except Exception:
                if package_created:
                    shutil.rmtree(self.packages_root / package_digest, ignore_errors=True)
                raise
            self._records = records
            return copy.deepcopy(record)

    def _record_failure(
        self,
        import_id: str,
        transport_kind: TransportKind,
        transport_digest: str,
        error_code: str,
        local_skill_id: str | None,
    ) -> None:
        with self._lock:
            if self._load_error:
                return
            now = time.time()
            pending = self._records.get(import_id)
            record = LocalSkillImport(
                import_id=import_id,
                revision=(pending.revision + 1) if pending else 1,
                content_revision=pending.content_revision if pending else 1,
                state="failed",
                transport_kind=transport_kind,
                transport_digest=transport_digest,
                local_skill_id=local_skill_id,
                error_code=error_code,
                created_at=pending.created_at if pending else now,
                updated_at=now,
            )
            records = dict(self._records)
            records[import_id] = record
            self._save_records_unlocked(records)
            self._records = records

    def _write_package_unlocked(
        self, package_digest: str, files: Sequence[ImportedFile]
    ) -> bool:
        if self.packages_root.is_symlink() or self.tmp_root.is_symlink():
            raise SkillLocalImportStorageError(
                "Local Skill import storage cannot contain linked roots.",
                code="skill_import_storage_unavailable",
            )
        destination = self.packages_root / package_digest
        if destination.exists():
            existing = self._read_package_unlocked(package_digest)
            if compute_skill_content_digest(existing) != package_digest:
                raise SkillLocalImportStorageError(
                    "Stored local Skill bytes do not match their digest.",
                    code="skill_import_storage_unavailable",
                )
            return False
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        temp = self.tmp_root / (package_digest + "." + uuid.uuid4().hex)
        temp.mkdir(parents=False, exist_ok=False)
        try:
            for item in files:
                target = temp.joinpath(*PurePosixPath(item.path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(item.content)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temp, destination)
            return True
        except OSError as exc:
            shutil.rmtree(temp, ignore_errors=True)
            raise SkillLocalImportStorageError(
                "The normalized local Skill package could not be persisted.",
                code="skill_import_storage_unavailable",
            ) from exc

    def _read_package_unlocked(self, package_digest: str) -> dict[str, bytes]:
        root = self.packages_root / package_digest
        if self.packages_root.is_symlink() or root.is_symlink() or not root.is_dir():
            raise SkillLocalImportStorageError(
                "The normalized local Skill package is unavailable.",
                code="skill_import_storage_unavailable",
            )
        files: dict[str, bytes] = {}
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise SkillLocalImportStorageError(
                    "Stored local Skill packages cannot contain links.",
                    code="skill_import_storage_unavailable",
                )
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                files[relative] = path.read_bytes()
        return files

    @staticmethod
    def _verify_package_unlocked(
        record: LocalSkillImport, files: Mapping[str, bytes]
    ) -> None:
        if (
            not record.package_digest
            or compute_skill_content_digest(files) != record.package_digest
        ):
            raise SkillLocalImportConflictError(
                "Stored local Skill bytes no longer match the import.",
                code="skill_import_package_mismatch",
            )
        manifest = {
            str(entry.get("path") or ""): entry
            for entry in record.file_manifest
            if isinstance(entry, Mapping)
        }
        if set(manifest) != set(files):
            raise SkillLocalImportConflictError(
                "Stored local Skill files no longer match the import manifest.",
                code="skill_import_package_mismatch",
            )
        for path, content in files.items():
            entry = manifest[path]
            if (
                int(entry.get("sizeBytes") or -1) != len(content)
                or str(entry.get("sha256") or "") != hashlib.sha256(content).hexdigest()
            ):
                raise SkillLocalImportConflictError(
                    "Stored local Skill files no longer match the import manifest.",
                    code="skill_import_package_mismatch",
                )

    def package_directory(self, import_id: str) -> Path:
        with self._lock:
            record = self.require(import_id)
            if (
                not record.package_digest
                or not record.file_manifest
                or record.state in {"blocked", "failed", "archived"}
            ):
                raise SkillLocalImportStorageError(
                    "This import does not retain package bytes.",
                    code="skill_import_storage_unavailable",
                )
            files = self._read_package_unlocked(record.package_digest)
            self._verify_package_unlocked(record, files)
            return self.packages_root / record.package_digest

    def preview_file(self, import_id: str, path: str) -> str:
        clean_path = _normalize_path(path)
        if PurePosixPath(clean_path).suffix.casefold() in _ACTIVE_PREVIEW_SUFFIXES:
            raise SkillLocalImportError(
                "Active browser content is not available for preview.",
                code="skill_import_invalid_transport",
            )
        with self._lock:
            record = self.require(import_id)
            if not record.package_digest or record.state in {"blocked", "failed", "archived"}:
                raise SkillLocalImportStorageError(
                    "This import does not retain package bytes.",
                    code="skill_import_storage_unavailable",
                )
            files = self._read_package_unlocked(record.package_digest)
            self._verify_package_unlocked(record, files)
            content = files.get(clean_path)
        if content is None:
            raise SkillLocalImportNotFoundError(
                "The imported Skill file was not found.", code="skill_import_not_found"
            )
        if len(content) > LOCAL_IMPORT_PREVIEW_BYTES:
            raise SkillLocalImportError(
                "The imported Skill file exceeds the preview limit.",
                code="skill_import_limit_exceeded",
            )
        try:
            return content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise SkillLocalImportError(
                "Binary Skill resources cannot be previewed as text.",
                code="skill_import_invalid_transport",
            ) from exc

    def rescan(
        self,
        import_id: str,
        *,
        expected_revision: int,
        expected_package_digest: str,
        expected_trust_fingerprint: str,
    ) -> LocalSkillImport:
        with self._lock:
            self._ensure_available(mutation=True)
            current = self._records.get(import_id)
            if current is None:
                raise SkillLocalImportNotFoundError(
                    "Local Skill import was not found.", code="skill_import_not_found"
                )
            if (
                current.revision != expected_revision
                or current.package_digest != expected_package_digest.casefold()
                or current.trust_fingerprint != expected_trust_fingerprint.casefold()
            ):
                raise SkillLocalImportConflictError(
                    "Local Skill import changed. Reload before rescanning.",
                    code="skill_import_stale",
                )
            raw_files = self._read_package_unlocked(expected_package_digest.casefold())
            actual_digest = compute_skill_content_digest(raw_files)
            if actual_digest != current.package_digest:
                raise SkillLocalImportConflictError(
                    "Stored local Skill bytes no longer match the import.",
                    code="skill_import_package_mismatch",
                )
            entries = _tree_entries(
                [
                    ImportedFile(
                        path,
                        content,
                        next(
                            (
                                str(entry.get("mode") or "100644")
                                for entry in current.file_manifest
                                if isinstance(entry, Mapping) and entry.get("path") == path
                            ),
                            "100644",
                        ),
                    )
                    for path, content in raw_files.items()
                ]
            )
            next_revision = current.revision + 1
            next_content_revision = current.content_revision + 1
            receipt = scan_local_skill_trust_receipt(
                import_id=current.import_id,
                import_revision=next_content_revision,
                transport_kind=current.transport_kind,
                transport_digest=current.transport_digest,
                entries=entries,
            )
            policy = str(receipt.get("installPolicy") or "block")
            state: ImportState = (
                "blocked" if policy == "block" else "ready" if policy == "allow" else "confirmation_required"
            )
            updated = copy.deepcopy(current)
            updated.revision = next_revision
            updated.content_revision = next_content_revision
            updated.state = state
            updated.trust_receipt = receipt
            updated.updated_at = time.time()
            updated.error_code = "skill_import_blocked" if state == "blocked" else None
            if state == "blocked":
                updated.file_manifest = []
            records = dict(self._records)
            records[import_id] = updated
            self._save_records_unlocked(records)
            self._records = records
            if state == "blocked":
                shutil.rmtree(self.packages_root / expected_package_digest, ignore_errors=True)
            return copy.deepcopy(updated)

    def delete(
        self,
        import_id: str,
        *,
        expected_revision: int,
        expected_package_digest: str | None,
        expected_trust_fingerprint: str | None,
    ) -> None:
        with self._lock:
            self._ensure_available(mutation=True)
            current = self._records.get(import_id)
            if current is None:
                raise SkillLocalImportNotFoundError(
                    "Local Skill import was not found.", code="skill_import_not_found"
                )
            if current.installed_skill_id or current.state == "installed":
                raise SkillLocalImportConflictError(
                    "Uninstall the local Skill before deleting its import.",
                    code="skill_import_replace_required",
                )
            if (
                current.revision != expected_revision
                or current.package_digest != (expected_package_digest.casefold() if expected_package_digest else None)
                or current.trust_fingerprint != (
                    expected_trust_fingerprint.casefold() if expected_trust_fingerprint else None
                )
            ):
                raise SkillLocalImportConflictError(
                    "Local Skill import changed. Reload before deleting.",
                    code="skill_import_stale",
                )
            records = dict(self._records)
            del records[import_id]
            self._save_records_unlocked(records)
            self._records = records
            if current.package_digest and not any(
                item.package_digest == current.package_digest for item in records.values()
            ):
                shutil.rmtree(self.packages_root / current.package_digest, ignore_errors=True)


__all__ = [
    "LOCAL_IMPORT_MAX_ACTIVE",
    "LOCAL_IMPORT_MAX_ARCHIVE_BYTES",
    "LOCAL_IMPORT_MAX_STORAGE_BYTES",
    "LOCAL_IMPORT_STORE_VERSION",
    "ImportedFile",
    "LocalSkillImport",
    "SkillLocalImportConflictError",
    "SkillLocalImportError",
    "SkillLocalImportNotFoundError",
    "SkillLocalImportStorageError",
    "SkillLocalImportStore",
    "normalize_folder_upload",
    "normalize_zip_upload",
]
