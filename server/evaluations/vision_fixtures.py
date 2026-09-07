from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

try:
    from server.file_assets.service import (
        FileAssetService,
        FileAssetServiceError,
        ResolvedEvaluationAsset,
    )
except ModuleNotFoundError:  # pragma: no cover - container import layout
    from file_assets.service import (
        FileAssetService,
        FileAssetServiceError,
        ResolvedEvaluationAsset,
    )


MAX_EVALUATION_RUN_ATTACHMENT_BYTES = 100 * 1024 * 1024

_IDENTIFIER = re.compile(r"^[A-Za-z0-9._-]{1,160}$")
_FIXED_METADATA_FIELDS = (
    "sha256",
    "format_id",
    "media_type",
    "byte_size",
    "page_count",
    "display_name",
)
_BASE_ATTACHMENT_FIELDS = frozenset({"asset_id", *_FIXED_METADATA_FIELDS})
_DRAFT_ATTACHMENT_FIELDS = _BASE_ATTACHMENT_FIELDS | {
    "dataset_id",
    "dataset_scope_id",
    "scope_id",
}
_VERSION_ATTACHMENT_FIELDS = _DRAFT_ATTACHMENT_FIELDS | {
    "dataset_version",
    "version_scope_id",
}
_RUN_ATTACHMENT_FIELDS = _VERSION_ATTACHMENT_FIELDS | {
    "run_id",
    "run_scope_id",
}
_RUN_LOCK_TIMEOUT_SECONDS = 5.0
_PROCESS_RUN_LOCKS_GUARD = threading.Lock()
_PROCESS_RUN_LOCKS: dict[str, threading.Lock] = {}


class EvaluationVisionFixtureService:
    """Freeze evaluation attachments through the shared FileAsset bindings."""

    def __init__(self, file_assets: FileAssetService) -> None:
        if not isinstance(file_assets, FileAssetService):
            raise TypeError("file_assets must be a FileAssetService")
        self.file_assets = file_assets

    def describe_draft_asset(self, dataset_id: str, asset_id: str) -> dict[str, Any]:
        clean_dataset = _identifier(dataset_id, "dataset_id")
        clean_asset = _asset_id(asset_id)
        draft_scope = _draft_scope(clean_dataset)
        resolved = self.file_assets.resolve_evaluation_asset(
            clean_asset,
            scope_id=draft_scope,
        )
        _require_dataset_origin(resolved, draft_scope)
        return _manifest(
            resolved,
            scope_id=draft_scope,
            dataset_id=clean_dataset,
        )

    def retain_dataset_version(
        self,
        dataset_id: str,
        version: int,
        attachments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        clean_dataset = _identifier(dataset_id, "dataset_id")
        clean_version = _version(version)
        draft_scope = _draft_scope(clean_dataset)
        version_scope = _version_scope(clean_dataset, clean_version)
        entries = _attachment_entries(
            attachments,
            allowed_fields=_DRAFT_ATTACHMENT_FIELDS,
        )
        resolved_assets: list[ResolvedEvaluationAsset] = []
        for entry in _deduplicate_entries(entries):
            resolved = self.file_assets.resolve_evaluation_asset(
                _asset_id(entry.get("asset_id")),
                scope_id=draft_scope,
            )
            _require_dataset_origin(resolved, draft_scope)
            _validate_claimed_manifest(
                entry,
                resolved,
                expected_scope=draft_scope,
                required=False,
                expected_context={
                    "dataset_id": clean_dataset,
                    "dataset_scope_id": draft_scope,
                },
            )
            resolved_assets.append(resolved)

        created: list[str] = []
        try:
            for resolved in resolved_assets:
                if self.file_assets.bind_evaluation_asset(
                    resolved.asset_id,
                    source_scope_id=draft_scope,
                    target_scope_id=version_scope,
                ):
                    created.append(resolved.asset_id)
        except Exception:
            self._rollback_bindings(created, scope_id=version_scope)
            raise

        return [
            _manifest(
                resolved,
                scope_id=version_scope,
                dataset_id=clean_dataset,
                dataset_version=clean_version,
            )
            for resolved in resolved_assets
        ]

    def pin_run(
        self,
        run_id: str,
        dataset_id: str,
        dataset_version: int,
        attachments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        clean_run = _identifier(run_id, "run_id")
        clean_dataset = _identifier(dataset_id, "dataset_id")
        clean_version = _version(dataset_version)
        draft_scope = _draft_scope(clean_dataset)
        version_scope = _version_scope(clean_dataset, clean_version)
        run_scope = _run_scope(clean_run)

        with _evaluation_run_lock(self.file_assets, clean_run):
            return self._pin_run_locked(
                clean_run=clean_run,
                clean_dataset=clean_dataset,
                clean_version=clean_version,
                draft_scope=draft_scope,
                version_scope=version_scope,
                run_scope=run_scope,
                attachments=attachments,
            )

    def inspect_dataset_version_asset(
        self, dataset_id: str, version: int, manifest: dict[str, Any]
    ) -> dict[str, Any]:
        clean_dataset = _identifier(dataset_id, "dataset_id")
        clean_version = _version(version)
        version_scope = _version_scope(clean_dataset, clean_version)
        entry = _attachment_entry(manifest, allowed_fields=_VERSION_ATTACHMENT_FIELDS)
        resolved = self.file_assets.resolve_evaluation_asset(_asset_id(entry.get("asset_id")), scope_id=version_scope)
        draft_scope = _draft_scope(clean_dataset)
        _require_dataset_origin(resolved, draft_scope)
        _validate_claimed_manifest(entry, resolved, expected_scope=version_scope, required=True, expected_context={
            "dataset_id": clean_dataset, "dataset_scope_id": draft_scope,
            "dataset_version": clean_version, "version_scope_id": version_scope,
        })
        return _manifest(resolved, scope_id=version_scope, dataset_id=clean_dataset, dataset_version=clean_version)

    def _pin_run_locked(
        self,
        *,
        clean_run: str,
        clean_dataset: str,
        clean_version: int,
        draft_scope: str,
        version_scope: str,
        run_scope: str,
        attachments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        existing: dict[str, ResolvedEvaluationAsset] = {}
        for asset_id in self.file_assets.list_evaluation_asset_ids(scope_id=run_scope):
            resolved = self.file_assets.resolve_evaluation_asset(
                asset_id,
                scope_id=run_scope,
            )
            _require_dataset_origin(resolved, draft_scope)
            try:
                self.file_assets.resolve_evaluation_asset(
                    asset_id,
                    scope_id=version_scope,
                )
            except FileAssetServiceError as exc:
                raise FileAssetServiceError(
                    409,
                    "evaluation_run_scope_conflict",
                    "该评测运行已固定到其他数据集版本，不能追加附件。",
                ) from exc
            existing[asset_id] = resolved

        requested: list[ResolvedEvaluationAsset] = []
        entries = _attachment_entries(
            attachments,
            allowed_fields=_VERSION_ATTACHMENT_FIELDS,
        )
        for entry in _deduplicate_entries(entries):
            resolved = self.file_assets.resolve_evaluation_asset(
                _asset_id(entry.get("asset_id")),
                scope_id=version_scope,
            )
            _require_dataset_origin(resolved, draft_scope)
            _validate_claimed_manifest(
                entry,
                resolved,
                expected_scope=version_scope,
                required=False,
                expected_context={
                    "dataset_id": clean_dataset,
                    "dataset_scope_id": draft_scope,
                    "dataset_version": clean_version,
                    "version_scope_id": version_scope,
                },
            )
            requested.append(resolved)

        all_assets = dict(existing)
        all_assets.update({item.asset_id: item for item in requested})
        unique_originals = {item.sha256: item.byte_size for item in all_assets.values()}
        total_bytes = sum(unique_originals.values())
        if total_bytes > MAX_EVALUATION_RUN_ATTACHMENT_BYTES:
            raise FileAssetServiceError(
                413,
                "evaluation_run_attachment_limit_exceeded",
                "评测运行附件原件去重后超过 100 MiB 上限。",
            )

        created: list[str] = []
        try:
            for resolved in requested:
                if self.file_assets.bind_evaluation_asset(
                    resolved.asset_id,
                    source_scope_id=version_scope,
                    target_scope_id=run_scope,
                ):
                    created.append(resolved.asset_id)
        except Exception:
            self._rollback_bindings(created, scope_id=run_scope)
            raise

        return [
            _manifest(
                resolved,
                scope_id=run_scope,
                dataset_id=clean_dataset,
                dataset_version=clean_version,
                run_id=clean_run,
            )
            for resolved in requested
        ]

    def resolve_run_asset(
        self,
        run_id: str,
        manifest: dict[str, Any],
    ) -> ResolvedEvaluationAsset:
        clean_run = _identifier(run_id, "run_id")
        entry = _attachment_entry(
            manifest,
            allowed_fields=_RUN_ATTACHMENT_FIELDS,
        )
        manifest_run = _identifier(entry.get("run_id"), "run_id")
        if manifest_run != clean_run:
            raise FileAssetServiceError(
                404,
                "evaluation_run_asset_not_found",
                "未找到当前评测运行中的附件。",
            )
        clean_dataset = _identifier(entry.get("dataset_id"), "dataset_id")
        clean_version = _version(entry.get("dataset_version"))
        draft_scope = _draft_scope(clean_dataset)
        version_scope = _version_scope(clean_dataset, clean_version)
        run_scope = _run_scope(clean_run)
        if (
            entry.get("scope_id") != run_scope
            or entry.get("dataset_scope_id") != draft_scope
            or entry.get("version_scope_id") != version_scope
            or entry.get("run_scope_id") != run_scope
        ):
            raise FileAssetServiceError(
                409,
                "evaluation_manifest_scope_mismatch",
                "评测附件清单与当前数据集版本或运行作用域不一致。",
            )

        resolved = self.file_assets.resolve_evaluation_asset(
            _asset_id(entry.get("asset_id")),
            scope_id=run_scope,
        )
        _require_dataset_origin(resolved, draft_scope)
        _validate_claimed_manifest(
            entry,
            resolved,
            expected_scope=run_scope,
            required=True,
        )
        return resolved

    def release_draft(self, dataset_id: str) -> tuple[str, ...]:
        return self.file_assets.release_evaluation_scope(
            scope_id=_draft_scope(_identifier(dataset_id, "dataset_id"))
        )

    def release_dataset_version(
        self,
        dataset_id: str,
        version: int,
    ) -> tuple[str, ...]:
        return self.file_assets.release_evaluation_scope(
            scope_id=_version_scope(
                _identifier(dataset_id, "dataset_id"),
                _version(version),
            )
        )

    def release_run(self, run_id: str) -> tuple[str, ...]:
        clean_run = _identifier(run_id, "run_id")
        with _evaluation_run_lock(self.file_assets, clean_run):
            return self.file_assets.release_evaluation_scope(
                scope_id=_run_scope(clean_run)
            )

    def _rollback_bindings(self, asset_ids: list[str], *, scope_id: str) -> None:
        rollback_failed = False
        for asset_id in reversed(asset_ids):
            try:
                self.file_assets.remove_evaluation_binding(
                    asset_id,
                    scope_id=scope_id,
                )
            except Exception:
                rollback_failed = True
        if rollback_failed:
            raise FileAssetServiceError(
                500,
                "evaluation_binding_rollback_failed",
                "评测附件引用回滚未完成，需要人工复核存储状态。",
            )


@contextmanager
def _evaluation_run_lock(
    file_assets: FileAssetService,
    run_id: str,
) -> Iterator[None]:
    storage_dir = file_assets.blob_store.storage_dir
    lock_digest = hashlib.sha256(
        f"{file_assets.tenant_id}\0{run_id}".encode("utf-8")
    ).hexdigest()
    lock_path = (
        storage_dir
        / ".locks"
        / "evaluation-runs"
        / f"{lock_digest}.lock"
    )
    process_key = os.path.normcase(os.path.abspath(lock_path))
    with _PROCESS_RUN_LOCKS_GUARD:
        process_lock = _PROCESS_RUN_LOCKS.setdefault(process_key, threading.Lock())

    with process_lock:
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            stream = lock_path.open("a+b")
        except OSError as exc:
            raise _run_lock_error() from exc

        with stream:
            try:
                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"\0")
                    stream.flush()
            except OSError as exc:
                raise _run_lock_error() from exc

            deadline = time.monotonic() + _RUN_LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise _run_lock_error() from exc
                    time.sleep(0.05)

            try:
                yield
            finally:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _run_lock_error() -> FileAssetServiceError:
    return FileAssetServiceError(
        503,
        "evaluation_run_lock_unavailable",
        "评测运行附件正在更新，请稍后重试。",
    )


def _manifest(
    resolved: ResolvedEvaluationAsset,
    *,
    scope_id: str,
    dataset_id: str,
    dataset_version: int | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "asset_id": resolved.asset_id,
        "sha256": resolved.sha256,
        "format_id": resolved.format_id,
        "media_type": resolved.media_type,
        "byte_size": resolved.byte_size,
        "page_count": resolved.page_count,
        "display_name": resolved.display_name,
        "dataset_id": dataset_id,
        "dataset_scope_id": _draft_scope(dataset_id),
        "scope_id": scope_id,
    }
    if dataset_version is not None:
        manifest["dataset_version"] = dataset_version
        manifest["version_scope_id"] = _version_scope(dataset_id, dataset_version)
    if run_id is not None:
        manifest["run_id"] = run_id
        manifest["run_scope_id"] = _run_scope(run_id)
    return manifest


def _validate_claimed_manifest(
    entry: dict[str, Any],
    resolved: ResolvedEvaluationAsset,
    *,
    expected_scope: str,
    required: bool,
    expected_context: dict[str, object] | None = None,
) -> None:
    if "scope_id" in entry and entry["scope_id"] != expected_scope:
        raise FileAssetServiceError(
            409,
            "evaluation_manifest_scope_mismatch",
            "评测附件清单与当前作用域不一致。",
        )
    for field, expected in (expected_context or {}).items():
        if field in entry and entry[field] != expected:
            raise FileAssetServiceError(
                409,
                "evaluation_manifest_scope_mismatch",
                "评测附件清单与当前数据集版本或作用域不一致。",
            )
    actual = {
        "sha256": resolved.sha256,
        "format_id": resolved.format_id,
        "media_type": resolved.media_type,
        "byte_size": resolved.byte_size,
        "page_count": resolved.page_count,
        "display_name": resolved.display_name,
    }
    for field in _FIXED_METADATA_FIELDS:
        if required and field not in entry:
            raise FileAssetServiceError(
                422,
                "evaluation_manifest_incomplete",
                "评测附件清单缺少固定元数据。",
            )
        if field in entry and entry[field] != actual[field]:
            raise FileAssetServiceError(
                409,
                "evaluation_manifest_integrity_mismatch",
                "评测附件清单与重新校验的原件不一致。",
            )


def _require_dataset_origin(
    resolved: ResolvedEvaluationAsset,
    expected_draft_scope: str,
) -> None:
    if resolved.origin_scope_id != expected_draft_scope:
        raise FileAssetServiceError(
            404,
            "evaluation_asset_dataset_mismatch",
            "该附件不属于当前评测数据集。",
        )


def _attachment_entries(
    value: object,
    *,
    allowed_fields: frozenset[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise FileAssetServiceError(
            422,
            "evaluation_attachments_invalid",
            "评测附件必须使用清单数组。",
        )
    return [
        _attachment_entry(item, allowed_fields=allowed_fields)
        for item in value
    ]


def _attachment_entry(
    value: object,
    *,
    allowed_fields: frozenset[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FileAssetServiceError(
            422,
            "evaluation_attachment_invalid",
            "每个评测附件必须使用包含 asset_id 的对象。",
        )
    entry = dict(value)
    if not set(entry).issubset(allowed_fields):
        raise FileAssetServiceError(
            422,
            "evaluation_attachment_fields_not_allowed",
            "评测附件清单包含当前阶段不允许的字段。",
        )
    _asset_id(entry.get("asset_id"))
    return entry


def _deduplicate_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for entry in entries:
        asset_id = _asset_id(entry.get("asset_id"))
        existing = unique.get(asset_id)
        if existing is None:
            unique[asset_id] = entry
        elif existing != entry:
            raise FileAssetServiceError(
                409,
                "evaluation_attachment_duplicate_conflict",
                "同一评测附件出现了冲突的固定元数据或作用域。",
            )
    return list(unique.values())


def _identifier(value: object, field: str) -> str:
    clean = str(value or "").strip()
    if _IDENTIFIER.fullmatch(clean) is None:
        raise FileAssetServiceError(
            422,
            f"invalid_{field}",
            "评测标识只能包含字母、数字、点、下划线和连字符。",
        )
    return clean


def _asset_id(value: object) -> str:
    return _identifier(value, "asset_id")


def _version(value: object) -> int:
    if isinstance(value, bool):
        clean = 0
    else:
        try:
            clean = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            clean = 0
    if clean < 1 or clean > 9_999_999_999 or str(value).strip() != str(clean):
        raise FileAssetServiceError(
            422,
            "invalid_dataset_version",
            "评测数据集版本必须是正整数。",
        )
    return clean


def _draft_scope(dataset_id: str) -> str:
    return f"evaluation:{dataset_id}"


def _version_scope(dataset_id: str, version: int) -> str:
    return f"evaluation-version:{dataset_id}:{version}"


def _run_scope(run_id: str) -> str:
    return f"evaluation-run:{run_id}"
