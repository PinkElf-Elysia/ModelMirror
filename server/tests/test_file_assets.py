from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.file_assets import (
    FILE_CAPABILITIES_VERSION,
    FILE_FORMAT_REGISTRY_VERSION,
    FileFamily,
    FileFormatCapability,
    FileFormatRegistry,
    FileInputKind,
    FileInputPolicy,
    FileInteractionStatus,
    FilePurpose,
    FileSizeMeasure,
    FileSupportLevel,
    get_file_format_registry,
    router,
)


MIB = 1024 * 1024


def test_readiness_report_matches_runtime_document_and_data_policies() -> None:
    report_path = Path(__file__).resolve().parents[2] / "docs" / "file-readiness.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    operation_by_policy = {
        ("chat", "document"): "extract_text",
        ("rag", "document"): "extract_text",
        ("datax", "data_source"): "structured_analysis",
        ("agent", "document"): "extract_text",
        ("workflow", "document"): "extract_text",
    }

    reported: dict[tuple[str, str, str], dict[str, object]] = {}
    for format_item in report["formats"]:
        for readiness in format_item["module_readiness"]:
            purpose = readiness["module"]
            input_kind = "data_source" if purpose == "datax" else "document"
            expected_status = "ready"
            expected_operation = (
                "extract_structure"
                if format_item["format_id"] in {"xlsx", "docx", "pptx"}
                and purpose in {"chat", "rag", "workflow"}
                else operation_by_policy.get((purpose, input_kind))
            )
            if (
                readiness["operation"] != expected_operation
                or readiness["interaction_status"] != expected_status
            ):
                continue
            key = (purpose, input_kind, format_item["format_id"])
            assert key not in reported
            reported[key] = {
                "extensions": sorted(format_item["extensions"]),
                "interaction_status": readiness["interaction_status"],
                "support_level": readiness["support_level"],
                "parser_id": readiness["parser_id"],
                "max_input_bytes": readiness["limits"]["max_input_bytes"],
                "ui_entrypoint": readiness["ui_entrypoint"],
            }

    registry = get_file_format_registry()
    runtime: dict[tuple[str, str, str], dict[str, object]] = {}
    target_policies = {
        (FilePurpose.CHAT, FileInputKind.DOCUMENT),
        (FilePurpose.RAG, FileInputKind.DOCUMENT),
        (FilePurpose.DATAX, FileInputKind.DATA_SOURCE),
        (FilePurpose.AGENT, FileInputKind.DOCUMENT),
        (FilePurpose.WORKFLOW, FileInputKind.DOCUMENT),
    }
    for policy in registry.policies_for():
        if (policy.purpose, policy.input_kind) not in target_policies:
            continue
        for format_item in registry.formats_for(policy):
            key = (
                policy.purpose.value,
                policy.input_kind.value,
                format_item.format_id,
            )
            format_status = format_item.interaction_status
            effective_status = (
                format_status
                if format_status != FileInteractionStatus.READY
                else policy.interaction_status
            )
            runtime[key] = {
                "extensions": sorted(format_item.extensions),
                "interaction_status": effective_status.value,
                "support_level": policy.support_level.value,
                "parser_id": format_item.parser_id or policy.parser_id,
                "max_input_bytes": policy.max_bytes_per_file,
                "ui_entrypoint": policy.ui_entrypoint,
            }

    assert reported == runtime


def test_registry_resolves_current_formats_and_limits() -> None:
    registry = get_file_format_registry()
    assert registry.by_extension(FilePurpose.CHAT, FileInputKind.AUDIO, "VOICE.MP3").format_id == "mp3"
    assert registry.by_extension(FilePurpose.RAG, FileInputKind.IMAGE, ".WEBP").format_id == "webp"
    assert registry.by_extension(FilePurpose.DATAX, FileInputKind.DATA_SOURCE, "facts.parquet").format_id == "parquet"
    assert registry.by_extension(FilePurpose.AGENT, FileInputKind.DOCUMENT, "photo.png") is None

    chat_image = next(
        item
        for item in registry.policies_for(FilePurpose.CHAT)
        if item.input_kind == FileInputKind.IMAGE
    )
    assert chat_image.max_bytes_per_file == 5 * MIB
    assert chat_image.size_measure == FileSizeMeasure.ENCODED_PAYLOAD

    datax = registry.policies_for(FilePurpose.DATAX)[0]
    assert datax.max_bytes_per_file == 50 * MIB
    assert set(registry.extensions_for("agent", "document")) == {
        ".c", ".cfg", ".conf", ".cpp", ".cs", ".css", ".csv", ".go",
        ".h", ".hpp", ".htm", ".html", ".ini", ".java", ".js", ".json",
        ".jsonl", ".jsx", ".log", ".markdown", ".md", ".ndjson", ".pdf",
        ".php", ".ps1", ".py", ".rb", ".rs", ".scss", ".sh", ".sql",
        ".srt", ".toml", ".ts", ".tsv", ".tsx", ".txt", ".vtt", ".xml",
        ".yaml", ".yml",
    }
    assert registry.extensions_for("workflow", "document") == ()
    assert set(
        registry.extensions_for("workflow", "document", ready_only=False)
    ) == {
        *registry.extensions_for("agent", "document"),
        ".docx", ".pptx", ".xlsx",
    }
    assert registry.extensions_for("unknown", "document") == ()


def test_registry_rejects_unknown_and_ambiguous_formats() -> None:
    text = FileFormatCapability(
        format_id="text",
        family=FileFamily.DOCUMENT,
        extensions=("txt",),
        media_types=("text/plain",),
    )
    alias = FileFormatCapability(
        format_id="alias",
        family=FileFamily.DOCUMENT,
        extensions=(".TXT",),
        media_types=("application/x-text",),
    )
    policy = FileInputPolicy(
        purpose=FilePurpose.CHAT,
        input_kind=FileInputKind.DOCUMENT,
        format_ids=("missing",),
        max_bytes_per_file=1,
        support_level=FileSupportLevel.CONVERTED,
        interaction_status=FileInteractionStatus.PLANNED,
        status_reason="Not connected.",
    )
    with pytest.raises(ValueError, match="Unknown formats"):
        FileFormatRegistry((text,), (policy,))

    ambiguous = policy.model_copy(update={"format_ids": ("text", "alias")})
    with pytest.raises(ValueError, match="Ambiguous extensions"):
        FileFormatRegistry((text, alias), (ambiguous,))

    with pytest.raises(ValueError, match="require both parser_id"):
        FileInputPolicy(
            purpose=FilePurpose.CHAT,
            input_kind=FileInputKind.DOCUMENT,
            format_ids=("text",),
            max_bytes_per_file=1,
            support_level=FileSupportLevel.NATIVE,
            interaction_status=FileInteractionStatus.READY,
        )

    with pytest.raises(ValueError, match="require status_reason"):
        FileInputPolicy(
            purpose=FilePurpose.WORKFLOW,
            input_kind=FileInputKind.DOCUMENT,
            format_ids=("text",),
            max_bytes_per_file=1,
            support_level=FileSupportLevel.CONVERTED,
            interaction_status=FileInteractionStatus.PLANNED,
            status_reason="   ",
        )

    with pytest.raises(ValueError, match="must be disabled"):
        FileInputPolicy(
            purpose=FilePurpose.CHAT,
            input_kind=FileInputKind.DOCUMENT,
            format_ids=("text",),
            max_bytes_per_file=1,
            support_level=FileSupportLevel.UNSUPPORTED,
            interaction_status=FileInteractionStatus.READY,
            parser_id="chat.document",
            ui_entrypoint="/chat/:modelId",
        )


def test_chat_capabilities_follow_runtime_feature_gates(monkeypatch) -> None:
    registry = get_file_format_registry()
    gates = {
        "document": ("CHAT_FILE_INPUT_ENABLED", False),
        "image": ("MULTIMODAL_IMAGE_ANALYSIS_ENABLED", True),
        "image_reference": ("MULTIMODAL_IMAGE_GENERATION_ENABLED", True),
        "audio": ("MULTIMODAL_CHAT_AUDIO_ENABLED", False),
        "video": ("MULTIMODAL_CHAT_VIDEO_ENABLED", False),
        "audio_generation_image": ("MULTIMODAL_AUDIO_GENERATION_ENABLED", False),
        "video_generation_frame": ("MULTIMODAL_VIDEO_GENERATION_ENABLED", False),
        "video_generation_reference": ("MULTIMODAL_VIDEO_GENERATION_ENABLED", False),
        "visual_analysis": ("CHAT_ONE_SHOT_VISION_ENABLED", False),
    }
    for name, _default in gates.values():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("FILE_ASSET_STORE_MODE", raising=False)

    defaults = {
        item.input_kind.value: item
        for item in registry.capabilities_response(
            purpose=FilePurpose.CHAT
        ).capabilities
    }
    for input_kind, (_name, default) in gates.items():
        assert defaults[input_kind].interaction_status == (
            FileInteractionStatus.READY
            if default
            else FileInteractionStatus.DISABLED
        )
        extensions = registry.extensions_for(FilePurpose.CHAT, input_kind)
        assert bool(extensions) is default
        if not default:
            assert defaults[input_kind].status_reason

    monkeypatch.setenv("MULTIMODAL_IMAGE_ANALYSIS_ENABLED", "false")
    monkeypatch.setenv("MULTIMODAL_IMAGE_GENERATION_ENABLED", "false")
    disabled = {
        item.input_kind.value: item
        for item in registry.capabilities_response(
            purpose=FilePurpose.CHAT
        ).capabilities
    }
    assert disabled["image"].interaction_status == FileInteractionStatus.DISABLED
    assert (
        disabled["image_reference"].interaction_status
        == FileInteractionStatus.DISABLED
    )
    assert registry.extensions_for(FilePurpose.CHAT, FileInputKind.IMAGE) == ()
    assert (
        registry.extensions_for(FilePurpose.CHAT, FileInputKind.IMAGE_REFERENCE)
        == ()
    )

    for name, _default in gates.values():
        monkeypatch.setenv(name, "true")
    monkeypatch.setenv("CHAT_OPENROUTER_OCR_ENABLED", "true")
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "shadow")
    enabled = registry.capabilities_response(
        purpose=FilePurpose.CHAT
    ).capabilities
    assert all(
        item.interaction_status == FileInteractionStatus.READY
        for item in enabled
        if item.input_kind.value in gates
    )
    assert all(
        registry.extensions_for(FilePurpose.CHAT, input_kind)
        for input_kind in gates
    )
    visual = next(
        item for item in enabled if item.input_kind == FileInputKind.VISUAL_ANALYSIS
    )
    assert [option.mode.value for option in visual.analysis_options] == [
        "vision",
        "provider_ocr",
    ]
    assert [
        option.interaction_status for option in visual.analysis_options
    ] == [FileInteractionStatus.READY, FileInteractionStatus.READY]

    monkeypatch.setenv("CHAT_ONE_SHOT_VISION_ENABLED", "true")
    monkeypatch.setenv("CHAT_OPENROUTER_OCR_ENABLED", "false")
    vision_only = next(
        item
        for item in registry.capabilities_response(
            purpose=FilePurpose.CHAT
        ).capabilities
        if item.input_kind == FileInputKind.VISUAL_ANALYSIS
    )
    assert vision_only.interaction_status == FileInteractionStatus.READY
    assert [
        option.interaction_status for option in vision_only.analysis_options
    ] == [FileInteractionStatus.READY, FileInteractionStatus.DISABLED]

    monkeypatch.setenv("CHAT_ONE_SHOT_VISION_ENABLED", "false")
    monkeypatch.setenv("CHAT_OPENROUTER_OCR_ENABLED", "true")
    ocr_only = next(
        item
        for item in registry.capabilities_response(
            purpose=FilePurpose.CHAT
        ).capabilities
        if item.input_kind == FileInputKind.VISUAL_ANALYSIS
    )
    assert ocr_only.interaction_status == FileInteractionStatus.READY
    assert [
        option.interaction_status for option in ocr_only.analysis_options
    ] == [FileInteractionStatus.DISABLED, FileInteractionStatus.READY]


def test_capabilities_api_filters_fail_closed_for_unverified_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_FILE_INPUT_ENABLED", "true")
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "shadow")
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get(
        "/api/files/capabilities",
        params={"purpose": "chat", "model_id": "vendor/model"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == FILE_CAPABILITIES_VERSION
    assert payload["registry_version"] == FILE_FORMAT_REGISTRY_VERSION
    assert payload["requested_purpose"] == "chat"
    assert payload["requested_model_id"] == "vendor/model"
    assert payload["model_specific"] is True
    assert payload["capabilities"]
    assert {item["purpose"] for item in payload["capabilities"]} == {"chat"}
    assert {item["input_kind"] for item in payload["capabilities"]} >= {
        "document",
        "image",
        "audio",
        "video",
    }
    document = next(
        item for item in payload["capabilities"] if item["input_kind"] == "document"
    )
    assert document["interaction_status"] == "ready"
    assert document["status_reason"] is None
    assert [item["handling"] for item in document["handling_options"]] == [
        "extract"
    ]

    ready_response = client.get("/api/files/capabilities?purpose=rag").json()
    assert {item["input_kind"] for item in ready_response["capabilities"]} == {
        "document",
        "image",
    }
    assert all(
        item["interaction_status"] == "ready"
        and item["parser_id"]
        and item["ui_entrypoint"]
        for item in ready_response["capabilities"]
    )

    datax = client.get(
        "/api/files/capabilities", params={"purpose": "datax"}
    ).json()["capabilities"]
    assert datax and all(
        item["interaction_status"] == "ready" for item in datax
    )

    agent = client.get(
        "/api/files/capabilities", params={"purpose": "agent"}
    ).json()["capabilities"]
    assert agent and all(
        item["interaction_status"] == "disabled"
        and "Xpert" in item["status_reason"]
        and item["handling_options"] == []
        for item in agent
    )

    workflow = client.get(
        "/api/files/capabilities?purpose=workflow"
    ).json()["capabilities"]
    assert workflow[0]["interaction_status"] == "disabled"
    assert workflow[0]["status_reason"]

    monkeypatch.setenv("WORKFLOW_FILE_ASSETS_ENABLED", "true")
    workflow_ready = client.get(
        "/api/files/capabilities?purpose=workflow"
    ).json()["capabilities"]
    assert workflow_ready[0]["interaction_status"] == "ready"
    assert workflow_ready[0]["status_reason"] is None

    assert client.get("/api/files/capabilities?purpose=unknown").status_code == 422
    assert client.post("/api/files/capabilities").status_code == 405


def test_chat_document_requires_feature_and_asset_store_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = get_file_format_registry()

    monkeypatch.setenv("CHAT_FILE_INPUT_ENABLED", "true")
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "legacy")
    legacy = next(
        item
        for item in registry.capabilities_response(
            purpose=FilePurpose.CHAT,
            model_id="vendor/file-model",
            verified_native_pdf=True,
        ).capabilities
        if item.input_kind == FileInputKind.DOCUMENT
    )
    assert legacy.interaction_status == FileInteractionStatus.DISABLED
    assert legacy.handling_options == ()
    assert legacy.status_reason

    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "shadow")
    enabled = next(
        item
        for item in registry.capabilities_response(
            purpose=FilePurpose.CHAT,
            model_id="vendor/file-model",
            verified_native_pdf=True,
        ).capabilities
        if item.input_kind == FileInputKind.DOCUMENT
    )
    assert enabled.interaction_status == FileInteractionStatus.READY
    assert [item.handling.value for item in enabled.handling_options] == [
        "extract",
        "native",
    ]

    monkeypatch.setenv("CHAT_FILE_INPUT_ENABLED", "false")
    feature_off = next(
        item
        for item in registry.capabilities_response(
            purpose=FilePurpose.CHAT,
            model_id="vendor/file-model",
            verified_native_pdf=True,
        ).capabilities
        if item.input_kind == FileInputKind.DOCUMENT
    )
    assert feature_off.interaction_status == FileInteractionStatus.DISABLED
    assert feature_off.handling_options == ()


def test_capabilities_response_is_stable_and_path_free() -> None:
    first = get_file_format_registry().capabilities_response().model_dump(mode="json")
    second = get_file_format_registry().capabilities_response().model_dump(mode="json")
    assert first == second
    assert [(item["purpose"], item["input_kind"]) for item in first["capabilities"]] == sorted(
        (item["purpose"], item["input_kind"]) for item in first["capabilities"]
    )
    serialized = str(first).lower()
    assert "storage" not in serialized
    assert "api_key" not in serialized
    assert "c:\\" not in serialized
