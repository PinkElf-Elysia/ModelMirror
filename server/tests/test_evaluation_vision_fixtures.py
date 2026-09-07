from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image
from PyPDF2 import PdfWriter

import server.evaluations.vision_fixtures as vision_fixtures_module
from server.evaluations.vision_fixtures import (
    MAX_EVALUATION_RUN_ATTACHMENT_BYTES,
    EvaluationVisionFixtureService,
)
from server.file_assets.contracts import FileInputKind, FilePurpose
from server.file_assets.registry import MIB, get_file_format_registry
from server.file_assets.service import FileAssetService, FileAssetServiceError


def _image_bytes(format_name: str) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), color=(20, 80, 140)).save(stream, format=format_name)
    return stream.getvalue()


def _pdf_bytes(page_count: int) -> bytes:
    writer = PdfWriter()
    for _index in range(page_count):
        writer.add_blank_page(width=72, height=72)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def _file_assets(tmp_path: Path, *, tenant_id: str = "tenant-a") -> FileAssetService:
    return FileAssetService(
        storage_dir=tmp_path / "file-assets",
        mode="native",
        tenant_id=tenant_id,
    )


def _upload(
    service: FileAssetService,
    dataset_id: str,
    *,
    content: bytes | None = None,
    filename: str = "fixture.png",
    media_type: str = "image/png",
):
    return service.upload(
        io.BytesIO(content if content is not None else _image_bytes("PNG")),
        purpose=FilePurpose.EVALUATION,
        scope_id=f"evaluation:{dataset_id}",
        filename=filename,
        declared_media_type=media_type,
        input_kind=FileInputKind.VISUAL_ANALYSIS,
    )


def _assert_error_code(exc_info: pytest.ExceptionInfo[FileAssetServiceError], code: str) -> None:
    assert exc_info.value.error_code == code


def test_evaluation_original_download_checks_scope_integrity_and_version_retention(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.file_assets.api import router
    from server.file_assets.service import get_file_asset_service

    assets = _file_assets(tmp_path)
    uploaded = _upload(assets, "dataset")
    fixtures = EvaluationVisionFixtureService(assets)
    manifest = fixtures.describe_draft_asset("dataset", uploaded.asset_id)
    fixtures.retain_dataset_version("dataset", 1, [manifest])
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_file_asset_service] = lambda: assets
    with TestClient(app) as client:
        url = f"/api/files/{uploaded.asset_id}/download"
        params = {"purpose": "evaluation", "scope_id": "evaluation:dataset"}
        response = client.get(url, params=params)
        assert response.status_code == 200
        assert response.content == _image_bytes("PNG")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert client.get(url, params={**params, "purpose": "chat"}).status_code == 422
        assert client.get(url, params={**params, "scope_id": "evaluation:other"}).status_code == 404
        fixtures.release_draft("dataset")
        assert client.get(url, params=params).status_code == 404
        retained = {**params, "scope_id": "evaluation-version:dataset:1"}
        assert client.get(url, params=retained).content == response.content
        record = assets.repository.get_asset(assets.tenant_id, uploaded.asset_id)
        assets.blob_store._path_for_key(record.storage_key).write_bytes(b"tampered")
        assert client.get(url, params=retained).status_code == 409


def test_server_directory_import_uses_file_assets_fallback() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from evaluations.vision_fixtures import "
                "EvaluationVisionFixtureService; "
                "print(EvaluationVisionFixtureService.__name__)"
            ),
        ],
        cwd=repository_root / "server",
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "EvaluationVisionFixtureService"


@pytest.mark.parametrize(
    ("filename", "media_type", "content", "format_id", "page_count"),
    [
        ("fixture.png", "image/png", _image_bytes("PNG"), "png", 1),
        ("fixture.jpg", "image/jpeg", _image_bytes("JPEG"), "jpeg", 1),
        ("fixture.webp", "image/webp", _image_bytes("WEBP"), "webp", 1),
        ("fixture.pdf", "application/pdf", _pdf_bytes(2), "pdf", 2),
    ],
)
def test_evaluation_policy_upload_and_safe_draft_manifest(
    tmp_path: Path,
    filename: str,
    media_type: str,
    content: bytes,
    format_id: str,
    page_count: int,
) -> None:
    registry = get_file_format_registry()
    policy = next(
        item
        for item in registry.policies_for(FilePurpose.EVALUATION)
        if item.input_kind == FileInputKind.VISUAL_ANALYSIS
    )
    assert policy.max_bytes_per_file == 10 * MIB
    assert policy.max_files_per_request == 1
    assert set(policy.format_ids) == {"jpeg", "pdf", "png", "webp"}

    service = _file_assets(tmp_path)
    uploaded = _upload(
        service,
        "dataset-one",
        content=content,
        filename=filename,
        media_type=media_type,
    )
    manifest = EvaluationVisionFixtureService(service).describe_draft_asset(
        "dataset-one",
        uploaded.asset_id,
    )

    assert manifest["format_id"] == format_id
    assert manifest["page_count"] == page_count
    assert manifest["byte_size"] == len(content)
    assert manifest["display_name"] == filename
    assert manifest["scope_id"] == "evaluation:dataset-one"
    assert len(manifest["sha256"]) == 64
    assert not {
        "content",
        "path",
        "physical_path",
        "storage_key",
        "url",
    }.intersection(manifest)


def test_upload_rejects_internal_scopes_corrupt_files_and_oversized_pdf(
    tmp_path: Path,
) -> None:
    service = _file_assets(tmp_path)
    with pytest.raises(FileAssetServiceError) as protected:
        service.upload(
            io.BytesIO(_image_bytes("PNG")),
            purpose=FilePurpose.EVALUATION,
            scope_id="evaluation-version:dataset-one:1",
            filename="fixture.png",
            declared_media_type="image/png",
            input_kind=FileInputKind.VISUAL_ANALYSIS,
        )
    _assert_error_code(protected, "evaluation_scope_protected")

    with pytest.raises(FileAssetServiceError) as corrupt:
        _upload(service, "dataset-one", content=b"not a png")
    assert corrupt.value.error_code in {"invalid_image", "file_signature_mismatch"}

    with pytest.raises(FileAssetServiceError) as too_many_pages:
        _upload(
            service,
            "dataset-one",
            content=_pdf_bytes(21),
            filename="too-many.pdf",
            media_type="application/pdf",
        )
    _assert_error_code(too_many_pages, "pdf_page_limit_exceeded")
    assert service.blob_store.list_storage_keys() == ()


def test_tenant_scope_dataset_and_external_source_boundaries_fail_closed(
    tmp_path: Path,
) -> None:
    service = _file_assets(tmp_path)
    fixtures = EvaluationVisionFixtureService(service)
    uploaded = _upload(service, "dataset-a")

    with pytest.raises(FileAssetServiceError):
        fixtures.describe_draft_asset("dataset-b", uploaded.asset_id)
    with pytest.raises(FileAssetServiceError):
        EvaluationVisionFixtureService(
            _file_assets(tmp_path, tenant_id="tenant-b")
        ).describe_draft_asset("dataset-a", uploaded.asset_id)
    with pytest.raises(FileAssetServiceError) as external_source:
        fixtures.retain_dataset_version(
            "dataset-a",
            1,
            [{"asset_id": uploaded.asset_id, "url": "https://example.invalid/a.png"}],
        )
    _assert_error_code(external_source, "evaluation_attachment_fields_not_allowed")


def test_unknown_attachment_fields_and_duplicate_conflicts_are_rejected(
    tmp_path: Path,
) -> None:
    service = _file_assets(tmp_path)
    fixtures = EvaluationVisionFixtureService(service)
    uploaded = _upload(service, "dataset-a")
    draft = fixtures.describe_draft_asset("dataset-a", uploaded.asset_id)

    unknown = dict(draft)
    unknown["ignored_payload"] = "must-not-pass"
    with pytest.raises(FileAssetServiceError) as unknown_field:
        fixtures.retain_dataset_version("dataset-a", 1, [unknown])
    _assert_error_code(
        unknown_field,
        "evaluation_attachment_fields_not_allowed",
    )

    conflicting_sha = dict(draft)
    conflicting_sha["sha256"] = "0" * 64
    with pytest.raises(FileAssetServiceError) as sha_conflict:
        fixtures.retain_dataset_version(
            "dataset-a",
            1,
            [draft, conflicting_sha],
        )
    _assert_error_code(
        sha_conflict,
        "evaluation_attachment_duplicate_conflict",
    )

    conflicting_scope = dict(draft)
    conflicting_scope["scope_id"] = "evaluation:dataset-b"
    with pytest.raises(FileAssetServiceError) as scope_conflict:
        fixtures.retain_dataset_version(
            "dataset-a",
            1,
            [draft, conflicting_scope],
        )
    _assert_error_code(
        scope_conflict,
        "evaluation_attachment_duplicate_conflict",
    )

    retained = fixtures.retain_dataset_version(
        "dataset-a",
        1,
        [draft, dict(draft)],
    )
    assert len(retained) == 1


def test_retain_is_idempotent_and_draft_unbind_preserves_published_original(
    tmp_path: Path,
) -> None:
    service = _file_assets(tmp_path)
    fixtures = EvaluationVisionFixtureService(service)
    uploaded = _upload(service, "dataset-a")
    draft = fixtures.describe_draft_asset("dataset-a", uploaded.asset_id)

    first = fixtures.retain_dataset_version("dataset-a", 3, [draft])
    second = fixtures.retain_dataset_version("dataset-a", 3, [draft])
    assert first == second
    assert service.repository.get_asset("tenant-a", uploaded.asset_id).reference_count == 2

    assert service.delete_asset(
        uploaded.asset_id,
        purpose=FilePurpose.EVALUATION,
        scope_id="evaluation:dataset-a",
    ) is False
    assert service.repository.get_asset("tenant-a", uploaded.asset_id).reference_count == 1

    pinned = fixtures.pin_run("run-a", "dataset-a", 3, first)
    resolved = fixtures.resolve_run_asset("run-a", pinned[0])
    assert resolved.content == _image_bytes("PNG")
    assert resolved.sha256 == pinned[0]["sha256"]

    for protected_scope in (
        "evaluation-version:dataset-a:3",
        "evaluation-run:run-a",
    ):
        with pytest.raises(FileAssetServiceError) as protected:
            service.delete_asset(
                uploaded.asset_id,
                purpose=FilePurpose.EVALUATION,
                scope_id=protected_scope,
            )
        _assert_error_code(protected, "evaluation_scope_protected")


def test_run_pinning_deduplicates_enforces_total_and_blocks_dataset_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert MAX_EVALUATION_RUN_ATTACHMENT_BYTES == 100 * MIB
    service = _file_assets(tmp_path)
    fixtures = EvaluationVisionFixtureService(service)

    uploaded_a = _upload(service, "dataset-a")
    version_a = fixtures.retain_dataset_version(
        "dataset-a",
        1,
        [{"asset_id": uploaded_a.asset_id}],
    )
    pinned = fixtures.pin_run("run-shared", "dataset-a", 1, version_a + version_a)
    assert len(pinned) == 1
    assert service.repository.get_asset("tenant-a", uploaded_a.asset_id).reference_count == 3

    uploaded_b = _upload(service, "dataset-b")
    version_b = fixtures.retain_dataset_version(
        "dataset-b",
        1,
        [{"asset_id": uploaded_b.asset_id}],
    )
    with pytest.raises(FileAssetServiceError) as reused:
        fixtures.pin_run("run-shared", "dataset-b", 1, version_b)
    _assert_error_code(reused, "evaluation_asset_dataset_mismatch")

    monkeypatch.setattr(
        vision_fixtures_module,
        "MAX_EVALUATION_RUN_ATTACHMENT_BYTES",
        1,
    )
    with pytest.raises(FileAssetServiceError) as too_large:
        fixtures.pin_run("run-limited", "dataset-b", 1, version_b)
    _assert_error_code(too_large, "evaluation_run_attachment_limit_exceeded")
    assert service.list_evaluation_asset_ids(scope_id="evaluation-run:run-limited") == ()


def test_concurrent_run_pins_are_serialized_before_budget_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_service = _file_assets(tmp_path)
    setup_fixtures = EvaluationVisionFixtureService(setup_service)
    first_upload = _upload(setup_service, "dataset-a")
    second_upload = _upload(setup_service, "dataset-a", content=_image_bytes("JPEG"), filename="second.jpg", media_type="image/jpeg")
    version = setup_fixtures.retain_dataset_version(
        "dataset-a",
        1,
        [
            {"asset_id": first_upload.asset_id},
            {"asset_id": second_upload.asset_id},
        ],
    )
    monkeypatch.setattr(
        vision_fixtures_module,
        "MAX_EVALUATION_RUN_ATTACHMENT_BYTES",
        version[0]["byte_size"],
    )

    first_service = _file_assets(tmp_path)
    second_service = _file_assets(tmp_path)
    first_fixtures = EvaluationVisionFixtureService(first_service)
    second_fixtures = EvaluationVisionFixtureService(second_service)
    first_binding_started = threading.Event()
    release_first_binding = threading.Event()
    second_started = threading.Event()
    original_bind = first_service.bind_evaluation_asset

    def delayed_first_bind(*args, **kwargs):
        first_binding_started.set()
        assert release_first_binding.wait(timeout=5)
        return original_bind(*args, **kwargs)

    def pin_second():
        second_started.set()
        return second_fixtures.pin_run(
            "run-concurrent",
            "dataset-a",
            1,
            [version[1]],
        )

    monkeypatch.setattr(first_service, "bind_evaluation_asset", delayed_first_bind)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            first_fixtures.pin_run,
            "run-concurrent",
            "dataset-a",
            1,
            [version[0]],
        )
        try:
            assert first_binding_started.wait(timeout=5)
            second_future = executor.submit(pin_second)
            assert second_started.wait(timeout=5)
            assert not second_future.done()
        finally:
            release_first_binding.set()

        assert len(first_future.result(timeout=5)) == 1
        with pytest.raises(FileAssetServiceError) as budget_error:
            second_future.result(timeout=5)
    _assert_error_code(
        budget_error,
        "evaluation_run_attachment_limit_exceeded",
    )

    pinned_ids = first_service.list_evaluation_asset_ids(
        scope_id="evaluation-run:run-concurrent"
    )
    assert pinned_ids == (first_upload.asset_id,)
    pinned_total = sum(
        first_service.repository.get_asset("tenant-a", asset_id).byte_size
        for asset_id in pinned_ids
    )
    assert pinned_total <= vision_fixtures_module.MAX_EVALUATION_RUN_ATTACHMENT_BYTES


def test_manifest_and_original_tampering_fail_closed(tmp_path: Path) -> None:
    service = _file_assets(tmp_path)
    fixtures = EvaluationVisionFixtureService(service)
    uploaded = _upload(service, "dataset-a")
    version = fixtures.retain_dataset_version(
        "dataset-a",
        1,
        [{"asset_id": uploaded.asset_id}],
    )
    pinned = fixtures.pin_run("run-a", "dataset-a", 1, version)

    forged = dict(pinned[0])
    forged["sha256"] = "0" * 64
    with pytest.raises(FileAssetServiceError) as manifest_tamper:
        fixtures.resolve_run_asset("run-a", forged)
    _assert_error_code(manifest_tamper, "evaluation_manifest_integrity_mismatch")

    cross_dataset = dict(pinned[0])
    cross_dataset["dataset_id"] = "dataset-b"
    cross_dataset["dataset_scope_id"] = "evaluation:dataset-b"
    cross_dataset["version_scope_id"] = "evaluation-version:dataset-b:1"
    with pytest.raises(FileAssetServiceError) as dataset_tamper:
        fixtures.resolve_run_asset("run-a", cross_dataset)
    _assert_error_code(dataset_tamper, "evaluation_asset_dataset_mismatch")

    record = service.repository.get_asset("tenant-a", uploaded.asset_id)
    assert record is not None
    blob_path = service.blob_store.storage_dir.joinpath(*record.storage_key.split("/"))
    blob_path.write_bytes(b"x" * record.byte_size)
    with pytest.raises(FileAssetServiceError) as original_tamper:
        fixtures.resolve_run_asset("run-a", pinned[0])
    _assert_error_code(original_tamper, "evaluation_asset_integrity_failed")


def test_restart_recovers_run_and_release_methods_are_scope_narrow(tmp_path: Path) -> None:
    content = _image_bytes("PNG")
    service = _file_assets(tmp_path)
    fixtures = EvaluationVisionFixtureService(service)
    uploaded = _upload(service, "dataset-a", content=content)
    version = fixtures.retain_dataset_version(
        "dataset-a",
        2,
        [{"asset_id": uploaded.asset_id}],
    )
    pinned = fixtures.pin_run("run-a", "dataset-a", 2, version)

    restarted = _file_assets(tmp_path)
    recovered = EvaluationVisionFixtureService(restarted)
    assert recovered.resolve_run_asset("run-a", pinned[0]).content == content

    assert recovered.release_draft("dataset-a") == (uploaded.asset_id,)
    assert recovered.resolve_run_asset("run-a", pinned[0]).content == content
    assert recovered.release_dataset_version("dataset-a", 2) == (uploaded.asset_id,)
    assert recovered.resolve_run_asset("run-a", pinned[0]).content == content
    assert recovered.release_run("run-a") == (uploaded.asset_id,)
    assert restarted.asset_cleanup_complete(uploaded.asset_id) is True
    assert restarted.repository.get_asset("tenant-a", uploaded.asset_id) is None
