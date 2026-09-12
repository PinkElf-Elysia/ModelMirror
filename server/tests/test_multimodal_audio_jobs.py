from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response

from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import RouterConnectionCreate
from server.model_router.service import ModelRouterService
from server.multimodal.api import configure_audio_job_service, router
from server.multimodal.audio_catalog import (
    AudioChatProfile,
    AudioModelCatalogResponse,
)
from server.multimodal.audio_jobs import (
    AudioGenerationResult,
    AudioJobTask,
    AudioJobService,
    OpenRouterAudioJobAdapter,
)
from server.multimodal import audio_jobs as audio_jobs_module
from server.multimodal.stt import MultimodalServiceError, OpenRouterTarget


def mpeg_layer3_frame(
    *,
    version_bits: int = 0b11,
    bitrate_index: int = 9,
    sample_rate_index: int = 0,
    padding: int = 0,
    emphasis: int = 0,
) -> bytes:
    bitrate_tables = {
        0b11: (
            0,
            32,
            40,
            48,
            56,
            64,
            80,
            96,
            112,
            128,
            160,
            192,
            224,
            256,
            320,
            0,
        ),
        0b10: (
            0,
            8,
            16,
            24,
            32,
            40,
            48,
            56,
            64,
            80,
            96,
            112,
            128,
            144,
            160,
            0,
        ),
        0b00: (
            0,
            8,
            16,
            24,
            32,
            40,
            48,
            56,
            64,
            80,
            96,
            112,
            128,
            144,
            160,
            0,
        ),
    }
    sample_rates = {
        0b11: (44_100, 48_000, 32_000),
        0b10: (22_050, 24_000, 16_000),
        0b00: (11_025, 12_000, 8_000),
    }
    bitrate = bitrate_tables[version_bits][bitrate_index]
    sample_rate = sample_rates[version_bits][sample_rate_index]
    coefficient = 144_000 if version_bits == 0b11 else 72_000
    frame_length = coefficient * bitrate // sample_rate + padding
    header = (
        0x7FF << 21
        | version_bits << 19
        | 0b01 << 17
        | 1 << 16
        | bitrate_index << 12
        | sample_rate_index << 10
        | padding << 9
        | emphasis
    )
    return header.to_bytes(4, "big") + bytes(
        [0x55 + (version_bits & 0x3)]
    ) * (frame_length - 4)


def id3v2_tag(payload: bytes = b"", *, with_footer: bool = False) -> bytes:
    size = len(payload)
    size_bytes = bytes(
        (
            (size >> 21) & 0x7F,
            (size >> 14) & 0x7F,
            (size >> 7) & 0x7F,
            size & 0x7F,
        )
    )
    flags = 0x10 if with_footer else 0
    header = b"ID3\x04\x00" + bytes([flags]) + size_bytes
    footer = (
        b"3DI\x04\x00" + bytes([flags]) + size_bytes
        if with_footer
        else b""
    )
    return header + payload + footer


def apev2_tail(*, with_header: bool = True) -> bytes:
    key = b"Title"
    value = b"Synthetic audio"
    item = (
        len(value).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + key
        + b"\x00"
        + value
    )
    flags = 0x80000000 if with_header else 0
    tag_size = len(item) + 32
    common = (
        b"APETAGEX"
        + (2_000).to_bytes(4, "little")
        + tag_size.to_bytes(4, "little")
        + (1).to_bytes(4, "little")
    )
    footer = common + flags.to_bytes(4, "little") + b"\x00" * 8
    header = (
        common
        + (flags | 0x20000000).to_bytes(4, "little")
        + b"\x00" * 8
        if with_header
        else b""
    )
    return header + item + footer


MP3_FRAMES = b"".join(mpeg_layer3_frame() for _ in range(3))
MP3 = id3v2_tag(b"\x00" * 16) + MP3_FRAMES
PNG = b"\x89PNG\r\n\x1a\n" + b"safe-image"


def forged_layer3_stream(
    *,
    version_bits: int = 0b11,
    layer_bits: int = 0b01,
    bitrate_index: int = 9,
    sample_rate_index: int = 0,
    emphasis: int = 0,
) -> bytes:
    header = (
        0x7FF << 21
        | version_bits << 19
        | layer_bits << 17
        | 1 << 16
        | bitrate_index << 12
        | sample_rate_index << 10
        | emphasis
    )
    return header.to_bytes(4, "big") + b"\x00" * 2_048


@pytest.mark.parametrize(
    "content",
    [
        b"".join(
            mpeg_layer3_frame(version_bits=version) for _ in range(2)
        )
        for version in (0b11, 0b10, 0b00)
    ]
    + [
        id3v2_tag(b"synthetic metadata", with_footer=True) + MP3_FRAMES,
        MP3_FRAMES + b"TAG" + b"\x00" * 125,
        MP3_FRAMES + apev2_tail(with_header=False),
        (
            id3v2_tag(b"safe metadata")
            + MP3_FRAMES
            + apev2_tail()
            + b"TAG"
            + b"\x00" * 125
        ),
    ],
)
def test_mp3_validator_accepts_complete_layer3_streams_and_tags(
    content: bytes,
) -> None:
    assert OpenRouterAudioJobAdapter._is_mp3(content) is True


@pytest.mark.parametrize(
    "content",
    [
        b"ID3\x04\x00\x00" + b"generated-music" * 100,
        mpeg_layer3_frame(bitrate_index=14),
        forged_layer3_stream(bitrate_index=0),
        forged_layer3_stream(bitrate_index=15),
        forged_layer3_stream(version_bits=0b01),
        forged_layer3_stream(layer_bits=0b10),
        forged_layer3_stream(sample_rate_index=0b11),
        forged_layer3_stream(emphasis=0b10),
        MP3[:-7],
        MP3 + b"random-tail",
        b"ID3\x04\x00\x00\x80\x00\x00\x00" + MP3_FRAMES,
        MP3_FRAMES + b"APETAGEX" + b"\x00" * 24,
    ],
)
def test_mp3_validator_rejects_magic_only_reserved_and_incomplete_data(
    content: bytes,
) -> None:
    assert OpenRouterAudioJobAdapter._is_mp3(content) is False


def test_complete_mp3_enforces_shared_media_size_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    below_minimum = mpeg_layer3_frame() * 2
    assert len(below_minimum) < 1_024
    assert OpenRouterAudioJobAdapter._is_mp3(below_minimum) is True
    assert audio_jobs_module.is_complete_mp3(below_minimum) is False

    monkeypatch.setattr(
        audio_jobs_module,
        "AUDIO_GENERATION_MAX_MEDIA_BYTES",
        len(MP3),
    )
    assert audio_jobs_module.is_complete_mp3(MP3) is True
    monkeypatch.setattr(
        audio_jobs_module,
        "AUDIO_GENERATION_MAX_MEDIA_BYTES",
        len(MP3) - 1,
    )
    assert audio_jobs_module.is_complete_mp3(MP3) is False


def router_service(
    storage: Path, *, tenant_id: str = "local"
) -> ModelRouterService:
    repository = SQLiteRouterRepository(storage)
    connection = repository.create_connection(
        tenant_id,
        RouterConnectionCreate(
            name="OpenRouter",
            kind="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="audio-job-secret",
        ),
    )
    repository.save_test_result(
        tenant_id,
        connection.id,
        health="online",
        model_count=2,
        checked_at="2026-07-29T00:00:00+00:00",
    )
    return ModelRouterService(repository, tenant_id=tenant_id)


class StubAudioCatalog:
    def __init__(self, router_instance: ModelRouterService) -> None:
        self.router = router_instance
        pricing = {
            "google/lyria-3-clip-preview": (0.04, 30),
            "google/lyria-3-pro-preview": (0.08, None),
        }
        self.profiles = [
            AudioChatProfile(
                model_id=model_id,
                display_name=model_id,
                provider="openrouter",
                connection_id=None,
                invocable=True,
                interaction_status="planned",
                status_reason="frontend pending",
                operations=["generate_audio"],
                output_formats=["mp3"],
                supports_image_prompt=True,
                price_per_generation_usd=pricing[model_id][0],
                fixed_duration_seconds=pricing[model_id][1],
            )
            for model_id in (
                "google/lyria-3-clip-preview",
                "google/lyria-3-pro-preview",
            )
        ]

    async def get_catalog(
        self, *, force: bool = False
    ) -> AudioModelCatalogResponse:
        del force
        return AudioModelCatalogResponse(
            source="openrouter",
            status="online",
            stale=False,
            synced_at="2026-07-29T00:00:00+00:00",
            profiles=self.profiles,
        )

    def resolve_target(self) -> OpenRouterTarget:
        connection = self.router.list_connections(scope="audio")[0]
        return OpenRouterTarget(
            base_url=connection.base_url,
            api_key=self.router.repository.resolve_api_key(
                self.router.tenant_id, connection.id
            ),
            connection_id=connection.id,
            cache_key=connection.id,
        )


class FakeAudioAdapter:
    def __init__(
        self,
        *,
        result: AudioGenerationResult | None = None,
        error: MultimodalServiceError | None = None,
    ) -> None:
        self.result = result or AudioGenerationResult(
            content=MP3,
            actual_model="google/lyria-3-clip-preview",
            generation_id="gen-audio-test",
            cost_usd=0.04,
        )
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def generate(
        self,
        _: OpenRouterTarget,
        *,
        model_id: str,
        prompt: str,
        image_data_url: str | None,
    ) -> AudioGenerationResult:
        self.calls.append(
            {
                "model_id": model_id,
                "prompt": prompt,
                "image_data_url": image_data_url,
            }
        )
        if self.error is not None:
            raise self.error
        return self.result


def job_service(
    storage: Path,
    *,
    tenant_id: str = "local",
    adapter: FakeAudioAdapter | None = None,
) -> tuple[AudioJobService, FakeAudioAdapter]:
    router_instance = router_service(storage, tenant_id=tenant_id)
    fake = adapter or FakeAudioAdapter()
    return (
        AudioJobService(
            router_instance,
            StubAudioCatalog(router_instance),  # type: ignore[arg-type]
            adapter=fake,  # type: ignore[arg-type]
            output_dir=storage / f"audio-output-{tenant_id}",
        ),
        fake,
    )


@pytest.mark.asyncio
async def test_adapter_decodes_split_sse_audio_and_actual_cost() -> None:
    encoded = base64.b64encode(MP3).decode("ascii")
    parts = (encoded[:17], encoded[17:503], encoded[503:])

    def handler(request: Request) -> Response:
        payload = json.loads(request.content)
        assert payload["model"] == "google/lyria-3-clip-preview"
        assert payload["stream"] is True
        assert "audio-job-secret" in request.headers["authorization"]
        events = [
            {
                "id": "gen-stream",
                "model": "google/lyria-3-clip-preview",
                "choices": [{"delta": {"audio": {"data": parts[0]}}}],
            },
            {
                "choices": [{"delta": {"audio": {"data": parts[1]}}}],
            },
            {
                "choices": [
                    {
                        "delta": {"audio": {"data": parts[2]}},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"cost": 0.04, "total_tokens": 12},
            },
        ]
        body = "".join(
            f"data: {json.dumps(event)}\n\n" for event in events
        )
        body += "data: [DONE]\n\n"
        return Response(
            200,
            headers={
                "content-type": "text/event-stream",
                "x-generation-id": "gen-header",
            },
            content=body.encode(),
        )

    adapter = OpenRouterAudioJobAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(handler)
        )
    )
    result = await adapter.generate(
        OpenRouterTarget(
            base_url="https://openrouter.ai/api/v1",
            api_key="audio-job-secret",
            connection_id=None,
            cache_key="test",
        ),
        model_id="google/lyria-3-clip-preview",
        prompt="A calm instrumental",
        image_data_url=None,
    )

    assert result.content == MP3
    assert result.cost_usd == 0.04
    assert result.generation_id == "gen-header"


def _managed_audio_response(
    events: list[dict[str, object] | str],
) -> httpx.Response:
    body = "".join(
        f"data: {event if isinstance(event, str) else json.dumps(event)}\n\n"
        for event in events
    )
    return httpx.Response(200, content=body.encode("utf-8"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events",
    [
        [
            {
                "id": "gen-a",
                "model": "google/lyria-3-clip-preview",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"audio": {"data": base64.b64encode(MP3).decode()}},
                        "finish_reason": "length",
                    }
                ],
            },
            {
                "id": "gen-a",
                "model": "google/lyria-3-clip-preview",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
            "[DONE]",
        ],
        [
            {
                "error": "private failure",
                "model": "google/lyria-3-clip-preview",
                "choices": [],
            }
        ],
        [
            {
                "id": "gen-a",
                "model": "google/lyria-3-clip-preview",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"audio": {"data": base64.b64encode(MP3).decode()}},
                        "finish_reason": "stop",
                    }
                ],
            },
            {
                "id": "gen-a",
                "model": "google/lyria-3-clip-preview",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "private trailing text"},
                        "finish_reason": None,
                    }
                ],
            },
            "[DONE]",
        ],
        [
            {
                "id": "gen-a",
                "model": "google/lyria-3-clip-preview",
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": None},
                    {"index": 1, "delta": {}, "finish_reason": None},
                ],
            }
        ],
        [
            {
                "id": "gen-a",
                "model": "google/lyria-3-clip-preview",
                "choices": [{"index": 1, "delta": {}, "finish_reason": None}],
            }
        ],
        [
            {
                "id": "gen-a",
                "model": "google/lyria-3-clip-preview",
                "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
            },
            {
                "id": "gen-b",
                "model": "google/lyria-3-clip-preview",
                "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
            },
        ],
        [
            {
                "id": "gen-a",
                "model": "google/lyria-3-clip-preview",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"audio": {"data": base64.b64encode(MP3).decode()}},
                        "finish_reason": "stop",
                            "native_finish_reason": 7,
                    }
                ],
            },
            "[DONE]",
        ],
    ],
)
async def test_managed_audio_generation_rejects_ambiguous_or_unsafe_sse(
    events: list[dict[str, object] | str],
) -> None:
    adapter = OpenRouterAudioJobAdapter()
    with pytest.raises(MultimodalServiceError):
        await adapter._consume_response(  # noqa: SLF001
            _managed_audio_response(events),
            requested_model="google/lyria-3-clip-preview",
            require_observed_model=True,
        )


@pytest.mark.asyncio
async def test_managed_audio_generation_accepts_one_valid_usage_only_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started_at = 100.0
    monkeypatch.setattr(audio_jobs_module.time, "perf_counter", lambda: 100.125)
    encoded = base64.b64encode(MP3).decode("ascii")
    response = _managed_audio_response(
        [
            {
                "id": "gen-a",
                "model": "google/lyria-3-clip-preview",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"audio": {"data": encoded}},
                        "finish_reason": "stop",
                    }
                ],
            },
            {
                "id": "gen-a",
                "model": "google/lyria-3-clip-preview",
                "choices": [],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 3,
                    "total_tokens": 5,
                    "cost": 0.04,
                },
            },
            "[DONE]",
        ]
    )
    result = await OpenRouterAudioJobAdapter()._consume_response(  # noqa: SLF001
        response,
        requested_model="google/lyria-3-clip-preview",
        require_observed_model=True,
        started_at=started_at,
    )
    assert result.content == MP3
    assert result.actual_model == "google/lyria-3-clip-preview"
    assert result.cost_usd == 0.04
    assert result.ttft_ms == pytest.approx(125.0)
    assert result.prompt_tokens == 2
    assert result.completion_tokens == 3
    assert result.total_tokens == 5


@pytest.mark.asyncio
async def test_managed_audio_job_preserves_metrics_when_metadata_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    result = AudioGenerationResult(
        content=MP3,
        actual_model="google/lyria-3-clip-preview",
        generation_id="gen-managed-metrics",
        cost_usd=0.04,
        ttft_ms=125.0,
        prompt_tokens=2,
        completion_tokens=3,
        total_tokens=5,
    )
    service, adapter = job_service(
        tmp_path,
        adapter=FakeAudioAdapter(result=result),
    )
    launch = await service.create(
        model_id="google/lyria-3-clip-preview",
        prompt="A calm instrumental",
        idempotency_key="audio-job-managed-metrics",
    )
    assert launch.task is not None

    class CapturingDispatch:
        dispatched = False
        completed = False
        completion: dict[str, object] | None = None

        def complete(self, **kwargs: object) -> dict[str, object]:
            self.completed = True
            self.completion = dict(kwargs)
            return {}

    dispatch = CapturingDispatch()

    async def generate_managed(
        candidate: CapturingDispatch,
        *,
        model_id: str,
        prompt: str,
        image_data_url: str | None,
        on_dispatched: object,
    ) -> AudioGenerationResult:
        assert model_id == "google/lyria-3-clip-preview"
        assert prompt == "A calm instrumental"
        assert image_data_url is None
        candidate.dispatched = True
        assert callable(on_dispatched)
        on_dispatched()
        return result

    monkeypatch.setattr(
        adapter,
        "generate_managed",
        generate_managed,
        raising=False,
    )
    original_update = service._update  # noqa: SLF001

    def fail_provider_result_metadata(
        job_id: str,
        **changes: object,
    ) -> dict[str, object] | None:
        if changes.get("status") == "running" and "output_bytes" in changes:
            raise OSError("simulated job metadata failure")
        return original_update(job_id, **changes)

    monkeypatch.setattr(service, "_update", fail_provider_result_metadata)
    await service.run(
        AudioJobTask(
            job_id=launch.task.job_id,
            target=None,
            model_id=launch.task.model_id,
            prompt=launch.task.prompt,
            image_data_url=None,
            managed_dispatch=dispatch,  # type: ignore[arg-type]
        )
    )

    assert dispatch.completion == {
        "status": "passed",
        "result_class": "success",
        "actual_model": "google/lyria-3-clip-preview",
        "ttft_ms": 125.0,
        "prompt_tokens": 2,
        "completion_tokens": 3,
        "total_tokens": 5,
    }
    assert service._output_path(launch.task.job_id).read_bytes() == MP3  # noqa: SLF001


class _StallingManagedAudioStream(httpx.AsyncByteStream):
    def __init__(self, *, stall_close: bool = False) -> None:
        self.closed = 0
        self.stall_close = stall_close
        self.close_started = asyncio.Event()
        self.close_cancelled = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_finished = asyncio.Event()

    async def __aiter__(self):
        yield (
            b'data: {"id":"gen-a","model":"google/lyria-3-clip-preview",'
            b'"choices":[{"index":0,"delta":{},"finish_reason":null}]}\n\n'
        )
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed += 1
        self.close_started.set()
        if not self.stall_close:
            self.close_finished.set()
            return
        try:
            await self.close_release.wait()
        except asyncio.CancelledError:
            self.close_cancelled.set()
            await self.close_release.wait()
        finally:
            self.close_finished.set()


class _ClockedManagedAudioStream(httpx.AsyncByteStream):
    def __init__(self, clock: list[float]) -> None:
        self.clock = clock

    async def __aiter__(self):
        encoded = base64.b64encode(MP3).decode("ascii")
        self.clock[0] = 100.125
        yield (
            "data: "
            + json.dumps(
                {
                    "id": "gen-a",
                    "model": "google/lyria-3-clip-preview",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"audio": {"data": encoded}},
                            "finish_reason": None,
                        }
                    ],
                }
            )
            + "\n\n"
        ).encode()
        self.clock[0] = 103.0
        yield (
            "data: "
            + json.dumps(
                {
                    "id": "gen-a",
                    "model": "google/lyria-3-clip-preview",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "stop",
                        }
                    ],
                }
            )
            + "\n\ndata: [DONE]\n\n"
        ).encode()
        self.clock[0] = 105.0


@pytest.mark.asyncio
async def test_managed_audio_generation_ttft_is_first_audio_chunk_not_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    response = httpx.Response(
        200,
        stream=_ClockedManagedAudioStream(clock),
        request=httpx.Request(
            "POST", "https://provider.example/v1/chat/completions"
        ),
    )
    monkeypatch.setattr(
        audio_jobs_module.time,
        "perf_counter",
        lambda: clock[0],
    )

    result = await OpenRouterAudioJobAdapter()._consume_response(  # noqa: SLF001
        response,
        requested_model="google/lyria-3-clip-preview",
        require_observed_model=True,
        started_at=100.0,
    )

    assert result.ttft_ms == pytest.approx(125.0)


class _EofClosingManagedAudioStream(_StallingManagedAudioStream):
    async def __aiter__(self):
        encoded = base64.b64encode(MP3).decode("ascii")
        yield (
            "data: "
            + json.dumps(
                {
                    "id": "gen-a",
                    "model": "google/lyria-3-clip-preview",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"audio": {"data": encoded}},
                            "finish_reason": "stop",
                        }
                    ],
                }
            )
            + "\n\ndata: [DONE]\n\n"
        ).encode()


class _StallingClientCloseTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.close_started = asyncio.Event()
        self.close_cancelled = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_finished = asyncio.Event()

    async def handle_async_request(self, _request: httpx.Request) -> httpx.Response:
        raise AssertionError("managed dispatch must own the Provider request")

    async def aclose(self) -> None:
        self.close_started.set()
        try:
            await self.close_release.wait()
        except asyncio.CancelledError:
            self.close_cancelled.set()
            await self.close_release.wait()
        finally:
            self.close_finished.set()


async def _wait_for_audio_cleanup_tasks() -> None:
    for _ in range(20):
        if not audio_jobs_module._BACKGROUND_AUDIO_CLEANUP_TASKS:  # noqa: SLF001
            return
        await asyncio.sleep(0)
    assert not audio_jobs_module._BACKGROUND_AUDIO_CLEANUP_TASKS  # noqa: SLF001


class _ManagedAudioDispatchStub:
    def __init__(
        self,
        stream: _StallingManagedAudioStream,
        *,
        status_code: int = 200,
    ) -> None:
        self.stream = stream
        self.status_code = status_code
        self.posts = 0

    async def send(self, _client, _payload, *, headers, on_dispatched=None):
        del headers
        self.posts += 1
        if on_dispatched is not None:
            on_dispatched()
        return httpx.Response(
            self.status_code,
            request=httpx.Request("POST", "https://provider.example/chat/completions"),
            stream=self.stream,
        )


@pytest.mark.asyncio
async def test_managed_audio_generation_total_timeout_closes_without_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audio_jobs_module,
        "MANAGED_AUDIO_GENERATION_TOTAL_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        audio_jobs_module,
        "MANAGED_AUDIO_GENERATION_CLOSE_TIMEOUT_SECONDS",
        0.01,
    )
    stream = _StallingManagedAudioStream(stall_close=True)
    dispatch = _ManagedAudioDispatchStub(stream)
    adapter = OpenRouterAudioJobAdapter(
        client_factory=lambda: httpx.AsyncClient()
    )

    with pytest.raises(MultimodalServiceError) as captured:
        await adapter.generate_managed(  # type: ignore[arg-type]
            dispatch,
            model_id="google/lyria-3-clip-preview",
            prompt="A calm instrumental",
        )

    assert captured.value.code == "provider_result_uncertain"
    assert dispatch.posts == 1
    await asyncio.wait_for(stream.close_started.wait(), timeout=0.1)
    assert stream.closed == 1
    await asyncio.wait_for(stream.close_cancelled.wait(), timeout=0.1)
    stream.close_release.set()
    await asyncio.wait_for(stream.close_finished.wait(), timeout=0.1)
    await _wait_for_audio_cleanup_tasks()


@pytest.mark.asyncio
async def test_managed_audio_generation_ignores_late_success_after_eof_close_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audio_jobs_module,
        "MANAGED_AUDIO_GENERATION_TOTAL_TIMEOUT_SECONDS",
        0.01,
    )
    stream = _EofClosingManagedAudioStream(stall_close=True)
    dispatch = _ManagedAudioDispatchStub(stream)
    adapter = OpenRouterAudioJobAdapter(
        client_factory=lambda: httpx.AsyncClient()
    )

    with pytest.raises(MultimodalServiceError) as captured:
        await adapter.generate_managed(  # type: ignore[arg-type]
            dispatch,
            model_id="google/lyria-3-clip-preview",
            prompt="A calm instrumental",
        )

    assert captured.value.code == "provider_result_uncertain"
    assert dispatch.posts == 1
    await asyncio.wait_for(stream.close_started.wait(), timeout=0.1)
    await asyncio.wait_for(stream.close_cancelled.wait(), timeout=0.1)
    stream.close_release.set()
    await asyncio.wait_for(stream.close_finished.wait(), timeout=0.1)
    await _wait_for_audio_cleanup_tasks()


@pytest.mark.asyncio
async def test_managed_audio_generation_bounds_and_reaps_client_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audio_jobs_module,
        "MANAGED_AUDIO_GENERATION_TOTAL_TIMEOUT_SECONDS",
        0.1,
    )
    monkeypatch.setattr(
        audio_jobs_module,
        "MANAGED_AUDIO_GENERATION_CLOSE_TIMEOUT_SECONDS",
        0.01,
    )
    stream = _EofClosingManagedAudioStream()
    dispatch = _ManagedAudioDispatchStub(stream)
    transport = _StallingClientCloseTransport()
    adapter = OpenRouterAudioJobAdapter(
        client_factory=lambda: httpx.AsyncClient(transport=transport)
    )

    result = await adapter.generate_managed(  # type: ignore[arg-type]
        dispatch,
        model_id="google/lyria-3-clip-preview",
        prompt="A calm instrumental",
    )

    assert result.content == MP3
    assert dispatch.posts == 1
    await asyncio.wait_for(transport.close_started.wait(), timeout=0.1)
    await asyncio.wait_for(transport.close_cancelled.wait(), timeout=0.1)
    transport.close_release.set()
    await asyncio.wait_for(transport.close_finished.wait(), timeout=0.1)
    await _wait_for_audio_cleanup_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [302, 307])
async def test_managed_audio_generation_rejects_redirect_response(
    status_code: int,
) -> None:
    stream = _EofClosingManagedAudioStream()
    dispatch = _ManagedAudioDispatchStub(stream, status_code=status_code)
    adapter = OpenRouterAudioJobAdapter(
        client_factory=lambda: httpx.AsyncClient()
    )

    with pytest.raises(MultimodalServiceError) as captured:
        await adapter.generate_managed(  # type: ignore[arg-type]
            dispatch,
            model_id="google/lyria-3-clip-preview",
            prompt="A calm instrumental",
        )

    assert captured.value.code == "provider_rejected_request"
    assert dispatch.posts == 1
    assert stream.closed == 1


@pytest.mark.asyncio
async def test_managed_audio_generation_discards_bounded_companion_text() -> None:
    encoded = base64.b64encode(MP3).decode("ascii")
    response = _managed_audio_response(
        [
            {
                "id": "gen-a",
                "model": "google/lyria-3-clip-preview",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "content": "Instrumental intro, then a short refrain.",
                            "audio": {"data": encoded},
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
            "[DONE]",
        ]
    )

    result = await OpenRouterAudioJobAdapter()._consume_response(  # noqa: SLF001
        response,
        requested_model="google/lyria-3-clip-preview",
        require_observed_model=True,
    )

    assert result.content == MP3
    assert result.actual_model == "google/lyria-3-clip-preview"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_event",
    [
        (
            '{"id":"gen-a","model":"google/lyria-3-clip-preview",'
            '"error":{"message":"hidden"},"error":null,'
            '"choices":[{"index":0,"delta":{"audio":{"data":"%s"}},'
            '"finish_reason":"stop"}]}'
        ),
        (
            '{"id":"gen-a","model":"google/lyria-3-clip-preview",'
            '"choices":[{"index":0,"delta":{"audio":{"data":"%s"}},'
            '"finish_reason":"length","finish_reason":"stop"}]}'
        ),
    ],
)
async def test_managed_audio_generation_rejects_duplicate_sse_keys(
    raw_event: str,
) -> None:
    encoded = base64.b64encode(MP3).decode("ascii")
    response = httpx.Response(
        200,
        content=(f"data: {raw_event % encoded}\n\ndata: [DONE]\n\n").encode(),
        request=httpx.Request("POST", "https://provider.example/v1/chat/completions"),
    )

    with pytest.raises(MultimodalServiceError) as captured:
        await OpenRouterAudioJobAdapter()._consume_response(  # noqa: SLF001
            response,
            requested_model="google/lyria-3-clip-preview",
            require_observed_model=True,
        )

    assert captured.value.code == "audio_output_incomplete"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected_error"),
    [
        ("error_null", "provider_unavailable"),
        ("refusal", "audio_output_incomplete"),
        ("normal_usage_empty", "audio_output_incomplete"),
        ("native_finish_without_finish", "audio_output_incomplete"),
        ("huge_cost", "audio_output_incomplete"),
        ("audio_not_object", "audio_output_incomplete"),
        ("audio_data_non_string", "audio_output_incomplete"),
        ("audio_transcript_non_string", "audio_output_incomplete"),
    ],
)
async def test_managed_audio_generation_uses_shared_sse_contract(
    case: str,
    expected_error: str,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    event: dict[str, object] = {
        "id": "generation-a",
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "delta": {
                    "audio": {"data": base64.b64encode(MP3).decode("ascii")}
                },
                "finish_reason": "stop",
            }
        ],
    }
    choice = event["choices"][0]  # type: ignore[index]
    delta = choice["delta"]  # type: ignore[index]
    if case == "error_null":
        event["error"] = None
    elif case == "refusal":
        delta["refusal"] = "blocked"  # type: ignore[index]
    elif case == "normal_usage_empty":
        event["usage"] = {}
    elif case == "native_finish_without_finish":
        choice["finish_reason"] = None  # type: ignore[index]
        choice["native_finish_reason"] = "stop"  # type: ignore[index]
    elif case == "huge_cost":
        event["usage"] = {"cost": 10**400}
    elif case == "audio_not_object":
        delta["audio"] = []  # type: ignore[index]
    elif case == "audio_data_non_string":
        delta["audio"] = {"data": 123}  # type: ignore[index]
    elif case == "audio_transcript_non_string":
        delta["audio"] = {"transcript": []}  # type: ignore[index]
    response = httpx.Response(
        200,
        content=(
            f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n"
        ).encode(),
        request=httpx.Request(
            "POST", "https://provider.example/v1/chat/completions"
        ),
    )

    with pytest.raises(MultimodalServiceError) as captured:
        await OpenRouterAudioJobAdapter()._consume_response(  # noqa: SLF001
            response,
            requested_model=model_id,
            require_observed_model=True,
        )

    assert captured.value.code == expected_error


@pytest.mark.asyncio
async def test_managed_audio_generation_accepts_mixed_sse_line_endings() -> None:
    model_id = "google/lyria-3-clip-preview"
    encoded = base64.b64encode(MP3).decode("ascii")
    event = json.dumps(
        {
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "delta": {"audio": {"data": encoded}},
                    "finish_reason": "stop",
                }
            ],
        },
        separators=(",", ":"),
    )
    body = f"data: {event}\n\r\ndata: [DONE]\r\r".encode()
    response = httpx.Response(
        200,
        content=body,
        request=httpx.Request(
            "POST", "https://provider.example/v1/chat/completions"
        ),
    )

    result = await OpenRouterAudioJobAdapter()._consume_response(  # noqa: SLF001
        response,
        requested_model=model_id,
        require_observed_model=True,
    )

    assert result.content == MP3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        b"ID3\x04\x00\x00" + b"generated-music" * 100,
        mpeg_layer3_frame(bitrate_index=14),
        MP3[:-7],
        MP3 + b"random-tail",
    ],
)
async def test_adapter_rejects_structurally_incomplete_mp3(
    content: bytes,
) -> None:
    assert len(content) >= 1_024
    encoded = base64.b64encode(content).decode("ascii")

    def handler(_: Request) -> Response:
        body = (
            "data: "
            + json.dumps(
                {
                    "model": "google/lyria-3-clip-preview",
                    "choices": [
                        {
                            "delta": {"audio": {"data": encoded}},
                            "finish_reason": "stop",
                        }
                    ],
                }
            )
            + "\n\ndata: [DONE]\n\n"
        )
        return Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body.encode(),
        )

    adapter = OpenRouterAudioJobAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(handler)
        )
    )
    with pytest.raises(MultimodalServiceError) as captured:
        await adapter.generate(
            OpenRouterTarget(
                base_url="https://openrouter.ai/api/v1",
                api_key="secret",
                connection_id=None,
                cache_key="test",
            ),
            model_id="google/lyria-3-clip-preview",
            prompt="A calm instrumental",
            image_data_url=None,
        )
    assert captured.value.code == "audio_output_incomplete"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code"),
    [(402, "provider_payment_required"), (429, "provider_rate_limited")],
)
async def test_adapter_translates_paid_generation_errors(
    status: int, code: str
) -> None:
    adapter = OpenRouterAudioJobAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(
                lambda _: Response(
                    status,
                    json={"error": {"message": "private upstream detail"}},
                )
            )
        )
    )
    with pytest.raises(MultimodalServiceError) as captured:
        await adapter.generate(
            OpenRouterTarget(
                base_url="https://openrouter.ai/api/v1",
                api_key="secret",
                connection_id=None,
                cache_key="test",
            ),
            model_id="google/lyria-3-clip-preview",
            prompt="A calm instrumental",
            image_data_url=None,
        )
    assert captured.value.code == code
    assert "private upstream detail" not in captured.value.message


@pytest.mark.asyncio
async def test_idempotent_job_runs_once_and_delivers_complete_mp3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    service, adapter = job_service(tmp_path)

    launch = await service.create(
        model_id="google/lyria-3-clip-preview",
        prompt="A calm instrumental",
        idempotency_key="audio-job-0001",
    )
    duplicate = await service.create(
        model_id="google/lyria-3-clip-preview",
        prompt="A different prompt must not trigger another charge",
        idempotency_key="audio-job-0001",
    )

    assert launch.job.status == "queued"
    assert launch.job.usage.cost_usd == 0.04
    assert launch.job.usage.cost_kind == "estimated"
    assert launch.task is not None
    assert duplicate.job.job_id == launch.job.job_id
    assert duplicate.task is None
    await service.run(launch.task)

    completed = service.get(launch.job.job_id)
    assert completed.status == "succeeded"
    assert completed.output_bytes == len(MP3)
    assert completed.usage.cost_usd == 0.04
    assert completed.usage.cost_kind == "actual"
    assert len(adapter.calls) == 1

    content = await service.content(completed.job_id)
    delivered = b"".join([chunk async for chunk in content.chunks])
    assert delivered == MP3
    assert content.media_type == "audio/mpeg"

    repository = service.router_service.repository
    row = repository.get_audio_job("local", completed.job_id)
    assert row is not None
    assert "prompt" not in row
    assert repository.count_schema_tenant_columns()["audio_jobs"] is True
    with sqlite3.connect(repository.database_path) as connection:
        operation = connection.execute(
            "SELECT operation FROM router_decisions WHERE tenant_id = ?",
            ("local",),
        ).fetchone()
    assert operation == ("generate_audio",)


@pytest.mark.asyncio
async def test_pro_image_prompt_is_enabled_and_not_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    result = AudioGenerationResult(
        content=MP3,
        actual_model="google/lyria-3-pro-preview",
        generation_id="gen-pro",
        cost_usd=0.08,
    )
    service, adapter = job_service(
        tmp_path,
        adapter=FakeAudioAdapter(result=result),
    )

    launch = await service.create(
        model_id="google/lyria-3-pro-preview",
        prompt="Turn the image into a full instrumental song",
        idempotency_key="audio-job-pro-image",
        image_filename="mood.png",
        image_content_type="image/png",
        image_content=PNG,
    )
    assert launch.task is not None
    await service.run(launch.task)

    job = service.get(launch.job.job_id)
    assert job.status == "succeeded"
    assert job.parameters.has_image is True
    assert job.usage.cost_usd == 0.08
    assert str(adapter.calls[0]["image_data_url"]).startswith(
        "data:image/png;base64,"
    )
    row = service.router_service.repository.get_audio_job(
        "local", job.job_id
    )
    assert row is not None
    assert set(row).isdisjoint({"prompt", "image", "audio", "filename"})


@pytest.mark.asyncio
async def test_missing_gateway_cost_preserves_catalog_estimate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    result = AudioGenerationResult(
        content=MP3,
        actual_model="google/lyria-3-clip-preview",
        generation_id="gen-estimated-cost",
        cost_usd=None,
    )
    service, _ = job_service(
        tmp_path,
        adapter=FakeAudioAdapter(result=result),
    )

    launch = await service.create(
        model_id="google/lyria-3-clip-preview",
        prompt="A calm instrumental",
        idempotency_key="audio-job-estimated-cost",
    )
    assert launch.task is not None
    await service.run(launch.task)

    completed = service.get(launch.job.job_id)
    assert completed.status == "succeeded"
    assert completed.usage.cost_usd == 0.04
    assert completed.usage.cost_kind == "estimated"


@pytest.mark.asyncio
async def test_listing_keeps_live_job_and_startup_recovery_marks_it_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    service, adapter = job_service(tmp_path)
    launch = await service.create(
        model_id="google/lyria-3-clip-preview",
        prompt="A calm instrumental",
        idempotency_key="audio-job-live-listing",
    )
    assert launch.task is not None

    listing = service.list()
    assert listing.jobs[0].status == "queued"
    assert service.get(launch.job.job_id).status == "queued"
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_cleanup_expires_audio_older_than_first_hundred_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    service, _adapter = job_service(tmp_path)
    launch = await service.create(
        model_id="google/lyria-3-clip-preview",
        prompt="A calm instrumental",
        idempotency_key="audio-job-old-expired-output",
    )
    assert launch.task is not None
    await service.run(launch.task)
    output_path = service._output_path(launch.job.job_id)  # noqa: SLF001
    assert output_path.is_file()
    service.router_service.repository.update_audio_job(
        "local",
        launch.job.job_id,
        expires_at="2000-01-01T00:00:00+00:00",
    )
    for index in range(101):
        row, created = (
            service.router_service.repository.create_audio_job_if_absent(
                "local",
                job_id=f"audio_newer_terminal_{index:03d}",
                idempotency_key_hash=f"newer-terminal-key-{index:03d}",
                connection_id="legacy-openrouter",
                requested_model="google/lyria-3-clip-preview",
                provider="openrouter",
                has_image=False,
                cost_kind="unavailable",
            )
        )
        assert created is True
        service.router_service.repository.update_audio_job(
            "local", str(row["id"]), status="failed"
        )

    service.cleanup_expired()

    stored = service.router_service.repository.get_audio_job(
        "local", launch.job.job_id
    )
    assert stored is not None
    assert stored["status"] == "expired"
    assert not output_path.exists()

    service.recover_interrupted()
    recovered = service.get(launch.job.job_id)
    assert recovered.status == "expired"
    assert recovered.error is not None
    assert recovered.error.code == "audio_expired"


def test_startup_recovery_removes_orphaned_audio_output_temp(
    tmp_path: Path,
) -> None:
    service, _adapter = job_service(tmp_path)
    orphan = service.output_dir / ("a" * 64 + ".tmp-" + "b" * 32)
    unrelated = service.output_dir / ("notes.tmp-" + "c" * 32)
    orphan.write_bytes(b"partial-private-audio")
    unrelated.write_bytes(b"user-owned-unrelated-data")

    service.recover_interrupted()

    assert not orphan.exists()
    assert unrelated.read_bytes() == b"user-owned-unrelated-data"


@pytest.mark.asyncio
async def test_invalid_image_is_rejected_before_job_or_paid_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    service, adapter = job_service(tmp_path)
    with pytest.raises(MultimodalServiceError) as captured:
        await service.create(
            model_id="google/lyria-3-pro-preview",
            prompt="A song",
            idempotency_key="audio-job-invalid-image",
            image_filename="mood.png",
            image_content_type="image/png",
            image_content=b"not-a-png",
        )
    assert captured.value.code == "invalid_image_prompt"
    assert service.list().jobs == []
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_failed_generation_never_exposes_partial_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    error = MultimodalServiceError(
        "audio_output_incomplete",
        "safe error",
        status_code=502,
    )
    service, _ = job_service(
        tmp_path,
        adapter=FakeAudioAdapter(error=error),
    )
    launch = await service.create(
        model_id="google/lyria-3-clip-preview",
        prompt="A calm instrumental",
        idempotency_key="audio-job-incomplete",
    )
    assert launch.task is not None
    await service.run(launch.task)

    job = service.get(launch.job.job_id)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "audio_output_incomplete"
    with pytest.raises(MultimodalServiceError) as captured:
        await service.content(job.job_id)
    assert captured.value.code == "audio_not_ready"


@pytest.mark.asyncio
async def test_expiry_and_tenant_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    local, _ = job_service(tmp_path / "shared", tenant_id="local")
    launch = await local.create(
        model_id="google/lyria-3-clip-preview",
        prompt="A calm instrumental",
        idempotency_key="audio-job-expiry",
    )
    assert launch.task is not None
    await local.run(launch.task)
    local.router_service.repository.update_audio_job(
        "local",
        launch.job.job_id,
        expires_at="2020-01-01T00:00:00+00:00",
    )
    assert local.get(launch.job.job_id).status == "expired"

    other, _ = job_service(tmp_path / "shared", tenant_id="other")
    with pytest.raises(MultimodalServiceError) as captured:
        other.get(launch.job.job_id)
    assert captured.value.code == "audio_job_not_found"


@pytest.mark.asyncio
async def test_api_creates_lists_downloads_and_removes_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    service, adapter = job_service(tmp_path)
    configure_audio_job_service(service)
    app = FastAPI()
    app.include_router(router)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            created = await client.post(
                "/api/multimodal/audio/jobs",
                data={
                    "model_id": "google/lyria-3-clip-preview",
                    "prompt": "A calm instrumental",
                    "idempotency_key": "audio-job-api-0001",
                },
            )
            assert created.status_code == 200
            job_id = created.json()["job_id"]
            assert len(adapter.calls) == 1

            detail = await client.get(
                f"/api/multimodal/audio/jobs/{job_id}"
            )
            assert detail.json()["status"] == "succeeded"
            content = await client.get(
                f"/api/multimodal/audio/jobs/{job_id}/content"
            )
            assert content.status_code == 200
            assert content.headers["content-type"].startswith("audio/mpeg")
            assert content.content == MP3

            listing = await client.get("/api/multimodal/audio/jobs")
            assert listing.json()["jobs"][0]["job_id"] == job_id
            removed = await client.delete(
                f"/api/multimodal/audio/jobs/{job_id}"
            )
            assert removed.json() == {
                "removed": True,
                "upstream_cancelled": False,
            }
    finally:
        configure_audio_job_service(None)


@pytest.mark.asyncio
async def test_feature_flag_blocks_job_before_audit_or_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "false")
    service, adapter = job_service(tmp_path)
    with pytest.raises(MultimodalServiceError) as captured:
        await service.create(
            model_id="google/lyria-3-clip-preview",
            prompt="A calm instrumental",
            idempotency_key="audio-job-disabled",
        )
    assert captured.value.code == "audio_generation_disabled"
    assert adapter.calls == []
