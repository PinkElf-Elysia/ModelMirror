from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from .local_import import (
    LOCAL_IMPORT_MAX_ARCHIVE_BYTES,
    LocalSkillImport,
    SkillLocalImportConflictError,
    SkillLocalImportError,
    SkillLocalImportNotFoundError,
    SkillLocalImportStorageError,
    SkillLocalImportStore,
)
from .skill_manager import SkillInstallError, SkillManagerError, SkillValidationError
from .trust_service import SkillTrustError
from .trust_scanner import (
    MAX_TRUST_FILE_BYTES,
    MAX_TRUST_FILES,
    MAX_TRUST_PATH_CHARS,
    MAX_TRUST_TOTAL_BYTES,
)


router = APIRouter(prefix="/api/skills/imports", tags=["skill-imports"])
_store: SkillLocalImportStore | None = None
# A JSON array of 500 maximum-length paths plus syntax overhead remains bounded
# without allowing an unbounded multipart form field.
MAX_TRUST_PATH_CHARS_JSON = MAX_TRUST_FILES * (MAX_TRUST_PATH_CHARS + 4)


class LocalImportMutationRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    expected_package_digest: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    expected_trust_fingerprint: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )


class LocalImportRescanRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    expected_package_digest: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    expected_trust_fingerprint: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )


class LocalImportInstallRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    expected_package_digest: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    expected_trust_fingerprint: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    confirmed: bool = False
    expected_installed_digest: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )


def configure_skill_local_import(store: SkillLocalImportStore | None) -> None:
    global _store
    _store = store


def get_skill_local_import_store() -> SkillLocalImportStore:
    global _store
    if _store is None:
        _store = SkillLocalImportStore()
    return _store


def _require_enabled(store: SkillLocalImportStore) -> None:
    if not store.enabled:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "skill_import_disabled",
                "message": "Local Skill import is disabled.",
            },
        )


def _raise_import_error(exc: SkillLocalImportError) -> None:
    if isinstance(exc, SkillLocalImportNotFoundError):
        status = 404
    elif isinstance(exc, SkillLocalImportStorageError):
        status = 503
    elif isinstance(exc, SkillLocalImportConflictError):
        status = 409
    elif exc.code == "skill_import_disabled":
        status = 404
    elif exc.code == "skill_import_limit_exceeded":
        status = 413
    elif exc.code in {"skill_import_stale", "skill_import_package_mismatch"}:
        status = 409
    elif exc.code == "skill_import_scan_failed":
        status = 422
    else:
        status = 400
    raise HTTPException(
        status_code=status,
        detail={"code": exc.code, "message": str(exc), "details": exc.details},
    ) from exc


def _raise_install_error(exc: Exception) -> None:
    fallback_code = (
        "skill_import_storage_unavailable"
        if isinstance(exc, (SkillInstallError, SkillManagerError))
        and not isinstance(exc, SkillValidationError)
        else "skill_import_scan_failed"
    )
    code = str(getattr(exc, "code", "") or fallback_code)
    details = dict(getattr(exc, "details", {}) or {})
    if code in {
        "skill_import_stale",
        "skill_import_replace_required",
        "skill_import_package_mismatch",
        "skill_import_ack_required",
        "skill_trust_ack_required",
        "skill_trust_candidate_stale",
    }:
        status = 409
    elif code in {"skill_import_storage_unavailable", "skill_trust_index_unavailable"}:
        status = 503
    else:
        status = 400 if isinstance(exc, (SkillValidationError, SkillTrustError)) else 500
    raise HTTPException(
        status_code=status,
        detail={"code": code, "message": str(exc), "details": details},
    ) from exc


def _serialize(record: LocalSkillImport, *, include_receipt: bool = True) -> dict[str, Any]:
    return record.serialize(include_receipt=include_receipt)


async def _read_bounded(upload: UploadFile, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(min(1024 * 1024, limit + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise SkillLocalImportError(
                "The upload exceeds the request size limit.",
                code="skill_import_limit_exceeded",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/status")
async def local_import_status() -> dict[str, Any]:
    return get_skill_local_import_store().status()


@router.post("")
async def create_local_import(
    transport_kind: Annotated[Literal["zip", "folder"], Form()],
    local_skill_id: Annotated[str | None, Form(max_length=64)] = None,
    paths_json: Annotated[str | None, Form(max_length=MAX_TRUST_PATH_CHARS_JSON)] = None,
    archive: Annotated[UploadFile | None, File()] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
) -> dict[str, Any]:
    store = get_skill_local_import_store()
    _require_enabled(store)
    try:
        if transport_kind == "zip":
            if archive is None or files or paths_json:
                raise SkillLocalImportError(
                    "ZIP import accepts exactly one archive upload.",
                    code="skill_import_invalid_transport",
                )
            content = await _read_bounded(archive, LOCAL_IMPORT_MAX_ARCHIVE_BYTES)
            record = await asyncio.to_thread(
                store.create_from_zip, content, local_skill_id=local_skill_id
            )
            return _serialize(record)

        if archive is not None or not files or paths_json is None:
            raise SkillLocalImportError(
                "Folder import requires a path manifest and matching file uploads.",
                code="skill_import_invalid_transport",
            )
        try:
            paths = json.loads(paths_json)
        except json.JSONDecodeError as exc:
            raise SkillLocalImportError(
                "Folder import path manifest is invalid.",
                code="skill_import_invalid_transport",
            ) from exc
        if (
            not isinstance(paths, list)
            or any(not isinstance(path, str) for path in paths)
            or len(paths) != len(files)
            or len(paths) > MAX_TRUST_FILES
        ):
            raise SkillLocalImportError(
                "Folder import path manifest does not match the uploaded files.",
                code="skill_import_invalid_transport",
            )
        items: list[tuple[str, bytes]] = []
        total = 0
        for path, upload in zip(paths, files, strict=True):
            content = await _read_bounded(upload, MAX_TRUST_FILE_BYTES)
            total += len(content)
            if total > MAX_TRUST_TOTAL_BYTES:
                raise SkillLocalImportError(
                    "Folder import exceeds the expanded-size limit.",
                    code="skill_import_limit_exceeded",
                )
            items.append((path, content))
        record = await asyncio.to_thread(
            store.create_from_folder, items, local_skill_id=local_skill_id
        )
        return _serialize(record)
    except SkillLocalImportError as exc:
        _raise_import_error(exc)


@router.get("")
async def list_local_imports(
    include_archived: bool = Query(default=False),
) -> dict[str, Any]:
    store = get_skill_local_import_store()
    _require_enabled(store)
    try:
        records = await asyncio.to_thread(
            store.list_imports, include_archived=include_archived
        )
        return {
            "imports": [_serialize(item, include_receipt=False) for item in records],
            "total": len(records),
        }
    except SkillLocalImportError as exc:
        _raise_import_error(exc)


@router.get("/{import_id}")
async def get_local_import(import_id: str) -> dict[str, Any]:
    store = get_skill_local_import_store()
    _require_enabled(store)
    try:
        record = await asyncio.to_thread(store.require, import_id)
        payload = _serialize(record)
        if (
            record.package_digest
            and record.file_manifest
            and record.state not in {"blocked", "failed", "archived"}
        ):
            from .api import get_skill_manager

            manager = get_skill_manager()
            manager.local_import_store = store
            package_dir = await asyncio.to_thread(
                store.package_directory, import_id
            )
            payload["replacementPreview"] = await asyncio.to_thread(
                manager.describe_local_import_replacement,
                record=record,
                package_dir=package_dir,
            )
        else:
            payload["replacementPreview"] = None
        return payload
    except SkillLocalImportError as exc:
        _raise_import_error(exc)
    except (SkillValidationError, SkillManagerError) as exc:
        _raise_install_error(exc)


@router.get("/{import_id}/file")
async def preview_local_import_file(
    import_id: str,
    path: str = Query(min_length=1, max_length=240),
) -> dict[str, str]:
    store = get_skill_local_import_store()
    _require_enabled(store)
    try:
        content = await asyncio.to_thread(store.preview_file, import_id, path)
        return {"importId": import_id, "path": path, "content": content}
    except SkillLocalImportError as exc:
        _raise_import_error(exc)


@router.post("/{import_id}/rescan")
async def rescan_local_import(
    import_id: str,
    payload: LocalImportRescanRequest,
) -> dict[str, Any]:
    store = get_skill_local_import_store()
    _require_enabled(store)
    try:
        record = await asyncio.to_thread(
            store.rescan,
            import_id,
            expected_revision=payload.expected_revision,
            expected_package_digest=payload.expected_package_digest,
            expected_trust_fingerprint=payload.expected_trust_fingerprint,
        )
        return _serialize(record)
    except SkillLocalImportError as exc:
        _raise_import_error(exc)


@router.post("/{import_id}/install")
async def install_local_import(
    import_id: str,
    payload: LocalImportInstallRequest,
) -> dict[str, Any]:
    store = get_skill_local_import_store()
    _require_enabled(store)
    try:
        # Imported lazily so the existing /api/skills manager remains the one
        # authoritative installed-package registry.
        from .api import _payload_from_skill, get_skill_manager

        manager = get_skill_manager()
        manager.local_import_store = store

        updated, installed = await asyncio.to_thread(
            manager.install_local_import_current,
            import_id,
            expected_revision=payload.expected_revision,
            expected_package_digest=payload.expected_package_digest,
            expected_trust_fingerprint=payload.expected_trust_fingerprint,
            confirmed=payload.confirmed,
            expected_installed_digest=payload.expected_installed_digest,
        )
        if str((updated.trust_receipt or {}).get("installPolicy") or "") == "confirm":
            installed = await asyncio.to_thread(
                manager.acknowledge_trust,
                installed.skill_id,
                expected_trust_fingerprint=payload.expected_trust_fingerprint,
                confirmed=payload.confirmed,
            )
        return {
            "import": _serialize(updated),
            "installed": _payload_from_skill(installed).model_dump(mode="json"),
        }
    except SkillLocalImportError as exc:
        _raise_import_error(exc)
    except (SkillValidationError, SkillTrustError, SkillInstallError, SkillManagerError) as exc:
        _raise_install_error(exc)
    except (OSError, ValueError) as exc:
        _raise_install_error(
            SkillInstallError("Local Skill installation storage is unavailable.")
        )


@router.delete("/{import_id}")
async def delete_local_import(
    import_id: str,
    payload: LocalImportMutationRequest,
) -> dict[str, bool]:
    store = get_skill_local_import_store()
    _require_enabled(store)
    try:
        await asyncio.to_thread(
            store.delete,
            import_id,
            expected_revision=payload.expected_revision,
            expected_package_digest=payload.expected_package_digest,
            expected_trust_fingerprint=payload.expected_trust_fingerprint,
        )
        return {"ok": True}
    except SkillLocalImportError as exc:
        _raise_import_error(exc)


__all__ = [
    "configure_skill_local_import",
    "get_skill_local_import_store",
    "router",
]
