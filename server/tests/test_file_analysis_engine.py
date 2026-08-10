from __future__ import annotations

import base64
import io
import struct
import time
import zlib
from dataclasses import dataclass

import pytest
import httpx
from PyPDF2 import PdfWriter

from server.file_assets.analysis import (
    FileAnalysisError,
    FileAnalysisExecutor,
    FileAnalysisMode,
    FileAnalysisPreflightRequest,
    FileAnalysisTarget,
    FileAnalysisTargetResolver,
    ResolvedFileAnalysisTarget,
    _ocr_payload,
    _parse_ocr_annotations,
    _run_pdf_operation,
    _validated_provider_response,
    analysis_digests,
    inspect_analysis_source,
)


_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _pdf(page_count: int) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=200, height=200)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _oversized_png_header(width: int = 7_000, height: int = 6_000) -> bytes:
    def chunk(name: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + name
            + payload
            + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IEND", b"")


def _slow_pdf_worker(
    _operation: str,
    _content: bytes,
    _page_number: int | None,
    _pages: tuple[int, ...],
    sender,
    _timeout_seconds: float,
) -> None:
    try:
        time.sleep(1)
    finally:
        sender.close()


def test_pdf_native_operation_is_killable_on_timeout() -> None:
    started = time.monotonic()
    with pytest.raises(FileAnalysisError) as failure:
        _run_pdf_operation(
            "page_count",
            _pdf(1),
            timeout_seconds=0.05,
            worker_target=_slow_pdf_worker,
        )
    assert failure.value.error_code == "analysis_pdf_resource_limit"
    assert time.monotonic() - started < 2


def _target(mode: FileAnalysisMode) -> ResolvedFileAnalysisTarget:
    public = FileAnalysisTarget(
        target_id=f"target-{mode.value}",
        mode=mode,
        connection_id="conn-1",
        connection_name="Explicit connection",
        model_id="vendor/model",
        model_name="Exact model",
        provider="openrouter" if mode == FileAnalysisMode.PROVIDER_OCR else "newapi",
        paid=mode == FileAnalysisMode.PROVIDER_OCR,
        cost_disclosure="actual bill applies",
    )
    return ResolvedFileAnalysisTarget(
        public=public,
        url="https://openrouter.ai/api/v1/chat/completions",
        api_key="test-only",
    )


def test_local_preflight_enforces_page_and_prompt_limits_without_payload_storage() -> None:
    page_count, pages = inspect_analysis_source(
        _pdf(21), format_id="pdf", selected_pages=(1, 3, 21)
    )
    assert page_count == 21
    assert pages == (1, 3, 21)
    with pytest.raises(FileAnalysisError) as missing_selection:
        inspect_analysis_source(_pdf(21), format_id="pdf", selected_pages=())
    assert missing_selection.value.error_code == "analysis_page_selection_required"

    image_count, image_pages = inspect_analysis_source(
        _ONE_PIXEL_PNG, format_id="png", selected_pages=()
    )
    assert (image_count, image_pages) == (1, (1,))
    with pytest.raises(ValueError):
        FileAnalysisPreflightRequest(
            scope_id="chat-1",
            mode="vision",
            target_id="target",
            prompt="x" * 2_001,
        )
    config_digest, prompt_sha256 = analysis_digests(
        asset_sha256="a" * 64,
        format_id="pdf",
        mode=FileAnalysisMode.VISION,
        target_id="target",
        selected_pages=(1, 3, 21),
        prompt="private one-shot instruction",
    )
    assert len(config_digest) == len(prompt_sha256) == 64
    assert "private" not in config_digest
    assert "private" not in prompt_sha256

    with pytest.raises(FileAnalysisError) as pixel_bomb:
        inspect_analysis_source(
            _oversized_png_header(), format_id="png", selected_pages=(1,)
        )
    assert pixel_bomb.value.error_code == "analysis_image_pixel_limit_exceeded"


@dataclass
class _Connection:
    id: str
    name: str
    kind: str
    base_url: str
    enabled: bool = True


@dataclass
class _Probe:
    ok: bool


class _Repository:
    def resolve_api_key(self, tenant_id: str, connection_id: str) -> str:
        assert tenant_id == "local"
        return f"key-for-{connection_id}"


class _RouterService:
    tenant_id = "local"
    repository = _Repository()

    def __init__(self) -> None:
        self.connections = [
            _Connection(
                "openrouter",
                "OpenRouter",
                "openrouter",
                "https://openrouter.ai/api",
            ),
            _Connection("offline", "Offline", "newapi", "https://offline.invalid"),
        ]

    def list_connections(self, *, scope: str):
        assert scope == "chat"
        return self.connections

    async def fetch_connection_model_records(self, connection_id: str):
        if connection_id == "offline":
            return _Probe(False), []
        return _Probe(True), [
            {
                "id": "vision/model",
                "name": "Vision model",
                "architecture": {
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["text"],
                },
            },
            {
                "id": "text/model",
                "name": "Text model",
                "architecture": {
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                },
            },
        ]


@pytest.mark.asyncio
async def test_targets_are_fresh_exact_and_openrouter_ocr_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_ONE_SHOT_VISION_ENABLED", "true")
    monkeypatch.setenv("CHAT_OPENROUTER_OCR_ENABLED", "true")
    resolver = FileAnalysisTargetResolver(_RouterService())
    targets = await resolver.list_targets()
    vision = [item for item in targets if item.mode == FileAnalysisMode.VISION]
    ocr = [item for item in targets if item.mode == FileAnalysisMode.PROVIDER_OCR]
    assert [item.model_id for item in vision] == ["vision/model"]
    assert {item.model_id for item in ocr} == {"vision/model", "text/model"}
    assert all(item.connection_id == "openrouter" for item in targets)
    assert all("downstream model" in item.cost_disclosure for item in ocr)
    resolved = await resolver.resolve(vision[0].target_id)
    assert resolved.url == "https://openrouter.ai/api/v1/chat/completions"
    assert resolved.api_key == "key-for-openrouter"


@pytest.mark.asyncio
async def test_unverified_openrouter_ocr_target_is_not_listed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_ONE_SHOT_VISION_ENABLED", "false")
    monkeypatch.setenv("CHAT_OPENROUTER_OCR_ENABLED", "true")
    monkeypatch.setattr(
        "server.file_assets.analysis.file_analysis_mode_canary_verified",
        lambda mode: mode == FileAnalysisMode.VISION,
    )
    resolver = FileAnalysisTargetResolver(_RouterService())
    targets = await resolver.list_targets()
    assert targets == ()


@pytest.mark.asyncio
async def test_visual_execution_calls_only_the_exact_target_once_per_page() -> None:
    calls: list[tuple[str, str, dict]] = []

    async def requester(url: str, key: str, payload: dict):
        calls.append((url, key, payload))
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"ocr_text":"page text","visual_summary":"chart",'
                        '"tables":[],"charts":[],"warnings":[]}'
                    }
                }
            ]
        }

    progress: list[int] = []
    artifact, cost = await FileAnalysisExecutor(requester).execute(
        content=_pdf(2),
        format_id="pdf",
        source_filename="public-synthetic.pdf",
        source_sha256="a" * 64,
        selected_pages=(1, 2),
        prompt="Describe the chart",
        target=_target(FileAnalysisMode.VISION),
        asset_id="file-1",
        progress=progress.append,
    )
    assert len(calls) == 2
    assert {call[0] for call in calls} == {
        "https://openrouter.ai/api/v1/chat/completions"
    }
    assert all(call[2]["model"] == "vendor/model" for call in calls)
    assert all("plugins" not in call[2] for call in calls)
    assert all(
        call[2]["messages"][1]["content"][1]["image_url"]["url"].startswith(
            "data:image/jpeg;base64,"
        )
        for call in calls
    )
    assert progress == [1, 2]
    assert cost is None
    assert artifact.processed_pages == 2
    assert artifact.failed_pages == ()


@pytest.mark.asyncio
async def test_ocr_is_one_request_for_pdf_subset_and_drops_annotation_images() -> None:
    calls: list[dict] = []

    async def requester(_url: str, _key: str, payload: dict):
        calls.append(payload)
        return {
            "choices": [
                {
                    "message": {
                        "content": "ok",
                        "annotations": [
                            {
                                "type": "file",
                                "file": {
                                    "content": [
                                        {"type": "text", "text": "recognized", "page": 1},
                                        {
                                            "type": "image_url",
                                            "image_url": {
                                                "url": "data:image/png;base64,SECRET"
                                            },
                                        },
                                    ]
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"cost": "0.004"},
        }

    artifact, cost = await FileAnalysisExecutor(requester).execute(
        content=_pdf(3),
        format_id="pdf",
        source_filename="public-synthetic.pdf",
        source_sha256="a" * 64,
        selected_pages=(1, 3),
        prompt="This must not be sent to OCR",
        target=_target(FileAnalysisMode.PROVIDER_OCR),
        asset_id="file-1",
    )
    assert len(calls) == 1
    payload = calls[0]
    assert payload["plugins"] == [
        {"id": "file-parser", "pdf": {"engine": "mistral-ocr"}}
    ]
    assert payload["model"] == "vendor/model"
    serialized = str(payload)
    assert "This must not be sent to OCR" not in serialized
    assert artifact.sections[0].text == "recognized"
    assert "base64" not in artifact.model_dump_json()
    assert cost == "0.004"


@pytest.mark.asyncio
async def test_ocr_filters_openrouter_wrappers_and_maps_subset_pages() -> None:
    async def requester(_url: str, _key: str, _payload: dict):
        return {
            "choices": [
                {
                    "message": {
                        "content": "ok",
                        "annotations": [
                            {
                                "type": "file",
                                "file": {
                                    "name": "source.pdf",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": '<file name="source.pdf">',
                                        },
                                        {"type": "text", "text": "page two"},
                                        {"type": "text", "text": "page four"},
                                        {"type": "text", "text": "</file>"},
                                        {
                                            "type": "image_url",
                                            "image_url": {
                                                "url": "data:image/png;base64,SECRET"
                                            },
                                        },
                                    ],
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"cost": "0.004"},
        }

    artifact, cost = await FileAnalysisExecutor(requester).execute(
        content=_pdf(4),
        format_id="pdf",
        source_filename="public-synthetic.pdf",
        source_sha256="a" * 64,
        selected_pages=(2, 4),
        prompt="",
        target=_target(FileAnalysisMode.PROVIDER_OCR),
        asset_id="file-1",
    )
    assert [(section.page, section.text) for section in artifact.sections] == [
        (2, "page two"),
        (4, "page four"),
    ]
    assert artifact.processed_pages == 2
    assert "<file" not in artifact.model_dump_json()
    assert "base64" not in artifact.model_dump_json()
    assert cost == "0.004"


@pytest.mark.asyncio
async def test_ocr_fails_closed_when_multi_page_attribution_is_missing() -> None:
    async def requester(_url: str, _key: str, _payload: dict):
        return {
            "choices": [
                {
                    "message": {
                        "annotations": [
                            {
                                "type": "file",
                                "file": {
                                    "content": [
                                        {"type": "text", "text": "combined text"}
                                    ]
                                },
                            }
                        ]
                    }
                }
            ]
        }

    with pytest.raises(FileAnalysisError) as failure:
        await FileAnalysisExecutor(requester).execute(
            content=_pdf(2),
            format_id="pdf",
            source_filename="public-synthetic.pdf",
            source_sha256="a" * 64,
            selected_pages=(1, 2),
            prompt="",
            target=_target(FileAnalysisMode.PROVIDER_OCR),
            asset_id="file-1",
        )
    assert failure.value.error_code == "ocr_page_attribution_missing"


def test_ocr_accepts_error_response_annotations_without_retry() -> None:
    response = httpx.Response(
        502,
        request=httpx.Request(
            "POST", "https://openrouter.ai/api/v1/chat/completions"
        ),
        json={
            "error": {
                "message": "Downstream model failed after PDF parsing",
                "metadata": {
                    "file_annotations": [
                        {
                            "type": "file",
                            "file": {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": '<file name="source.pdf">',
                                    },
                                    {"type": "text", "text": "recognized"},
                                    {"type": "text", "text": "</file>"},
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": "data:image/png;base64,SECRET"
                                        },
                                    },
                                ]
                            },
                        }
                    ]
                },
            }
        },
    )
    value = _validated_provider_response(
        response,
        payload=_ocr_payload(model_id="vendor/model", pdf_bytes=_pdf(1)),
    )
    sections, warnings = _parse_ocr_annotations(value, selected_pages=(7,))
    assert [(section.page, section.text) for section in sections] == [
        (7, "recognized")
    ]
    assert warnings == []
    assert "base64" not in str(sections)


def test_non_ocr_provider_error_with_annotations_still_fails_closed() -> None:
    response = httpx.Response(
        502,
        request=httpx.Request(
            "POST", "https://openrouter.ai/api/v1/chat/completions"
        ),
        json={
            "error": {
                "metadata": {"file_annotations": [{"type": "file"}]}
            }
        },
    )
    with pytest.raises(FileAnalysisError) as failure:
        _validated_provider_response(
            response,
            payload={"model": "vendor/model", "messages": []},
        )
    assert failure.value.error_code == "analysis_provider_failed"
