from __future__ import annotations

import asyncio
import concurrent.futures
import io
import json
import logging
import os
import sqlite3
import subprocess
import struct
import sys
import textwrap
import threading
import wave
from pathlib import Path

import httpx
import pytest

from server.model_router import multimodal_gateway as multimodal_gateway_module
from server.model_router import workload_control as workload_control_module
from server.model_router.egress import ProviderEgressError, ProviderEgressPolicy
from server.model_router.multimodal_control import (
    PROVIDER_MULTIMODAL_PROTOCOL_VERSION,
    ProviderMultimodalTarget,
    ProviderMultimodalTransport,
)
from server.model_router.multimodal_gateway import (
    ManagedMultimodalError,
    ManagedMultimodalGateway,
)
from server.model_router.repository import RouterRepositoryError, SQLiteRouterRepository
from server.model_router.schemas import (
    ProviderWorkloadActivationRequest,
    ProviderWorkloadBindingUpdate,
    ProviderWorkloadCertificationRequest,
    ProviderWorkloadDeactivationRequest,
    ProviderWorkloadPolicyUpdate,
    RouterConnectionCreate,
    RouterConnectionUpdate,
)
from server.model_router.service import ModelRouterService, RouterServiceError
from server.model_router.workload_control import (
    ProviderWorkloadCertificationService,
    ProviderWorkloadControlService,
)
from server.multimodal.stt import (
    MultimodalServiceError,
    TranscriptionService,
)
from server.multimodal.tts import SpeechService
from server.model_router.multimodal_control import SYNTHETIC_AUDIO_WAV_BYTES


MP3_BYTES = b"ID3\x04\x00\x00" + b"\x00" * 64


@pytest.mark.asyncio
async def test_openrouter_generation_metadata_httpx_log_redacts_raw_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_generation_id = "gen-sensitive-r8c-log-marker"
    caplog.set_level(logging.INFO, logger="httpx")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, request=request)
        )
    ) as client:
        await client.get(
            "https://openrouter.example/api/v1/generation",
            params={"id": raw_generation_id},
        )

    assert raw_generation_id not in caplog.text
    assert "/generation?id=[redacted]" in caplog.text


@pytest.mark.asyncio
async def test_httpx_log_filter_does_not_change_unrelated_request_urls(
    caplog: pytest.LogCaptureFixture,
) -> None:
    unrelated_url = "https://openrouter.example/api/v1/models?id=public-model"
    caplog.set_level(logging.INFO, logger="httpx")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, request=request)
        )
    ) as client:
        await client.get(unrelated_url)

    assert unrelated_url in caplog.text


class _InterruptingAudioStream(httpx.AsyncByteStream):
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.closed = False

    async def __aiter__(self):
        yield b'{"text":"partial'
        raise self.error

    async def aclose(self) -> None:
        self.closed = True


class _OversizedAudioStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self):
        yield b'{"text":"'
        yield b"x" * 32

    async def aclose(self) -> None:
        self.closed = True


class _ManagedPreflightProbe:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.exact_calls: list[tuple[str, str, str]] = []

    def routing_mode(self, _entry_id: str) -> str:
        return self.mode

    def exact_model_id(
        self,
        entry_id: str,
        execution_shape: str,
        *,
        requested_model: str,
    ) -> str:
        self.exact_calls.append((entry_id, execution_shape, requested_model))
        raise ManagedMultimodalError(
            "provider_workload_binding_missing",
            "managed preflight reached",
            status_code=409,
            receipt=self.blocked_receipt(
                entry_id, "provider_workload_binding_missing"
            ),
        )

    @staticmethod
    def blocked_receipt(entry_id: str, reason_code: str) -> dict[str, object]:
        return {
            "entry_id": entry_id,
            "status": "failed",
            "call_count": 0,
            "reason_codes": [reason_code],
            "calls": [],
        }


def test_r8c_certification_and_session_claim_are_cross_repository_atomic(
    tmp_path: Path,
) -> None:
    first = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    second = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connections = [
        first.create_connection(
            "local",
            RouterConnectionCreate(
                name=f"Audio {index}",
                kind="openrouter",
                base_url=f"https://audio-{index}.example/v1",
                api_key=f"secret-{index}",
                scopes=["audio"],
            ),
        )
        for index in (1, 2)
    ]
    barrier = threading.Barrier(2)

    def claim(
        repository: SQLiteRouterRepository,
        *,
        session_id: str,
        certification_id: str,
        connection_id: str,
    ) -> tuple[str, object]:
        barrier.wait(timeout=5)
        try:
            row, created = repository.claim_workload_certification(
                "local",
                certification_id=certification_id,
                connection_id=connection_id,
                connection_fingerprint=repository.connection_config_fingerprint(
                    "local", connection_id
                ),
                contract_version="modelmirror-provider-workload-routing-v1",
                execution_shape="audio_transcription",
                requested_model="provider/audio",
                profile={"input_format": "wav"},
                profile_fingerprint="profile-fingerprint",
                idempotency_key_hash="shared-cross-process-hash",
                adapter_contract="openrouter_audio_transcription_json_v1",
                protocol_version=PROVIDER_MULTIMODAL_PROTOCOL_VERSION,
                multimodal_session_id=session_id,
            )
            return str(row["id"]), created
        except Exception as exc:  # asserted below; preserve the exact mapped type
            return type(exc).__name__, str(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda arguments: claim(*arguments[0], **arguments[1]),
                [
                    (
                        (first,),
                        {
                            "session_id": "session-a",
                            "certification_id": "cert-a",
                            "connection_id": connections[0].id,
                        },
                    ),
                    (
                        (second,),
                        {
                            "session_id": "session-b",
                            "certification_id": "cert-b",
                            "connection_id": connections[1].id,
                        },
                    ),
                ],
            )
        )

    assert sum(outcome[1] is True for outcome in outcomes) == 1
    assert (
        "RouterRepositoryError",
        "provider_multimodal_session_idempotency_conflict",
    ) in outcomes
    certifications = first.list_workload_certifications("local")
    assert len(certifications) == 1
    assert certifications[0]["status"] == "running"
    sessions = first.list_multimodal_certification_sessions("local")
    assert len(sessions) == 1
    assert sessions[0]["post_dispatched"] == 0


def _claim_repository_audio_certification_pair(
    repository: SQLiteRouterRepository,
    *,
    connection_id: str,
    certification_id: str,
    session_id: str,
) -> None:
    repository.claim_workload_certification(
        "local",
        certification_id=certification_id,
        connection_id=connection_id,
        connection_fingerprint=repository.connection_config_fingerprint(
            "local", connection_id
        ),
        contract_version="modelmirror-provider-workload-routing-v1",
        execution_shape="audio_transcription",
        requested_model="provider/audio",
        profile={"input_format": "wav"},
        profile_fingerprint="profile-fingerprint",
        idempotency_key_hash=f"idempotency-{certification_id}",
        adapter_contract="openrouter_audio_transcription_json_v1",
        protocol_version=PROVIDER_MULTIMODAL_PROTOCOL_VERSION,
        multimodal_session_id=session_id,
    )


def _mark_repository_audio_certification_confirmed(
    repository: SQLiteRouterRepository,
    *,
    session_id: str,
) -> None:
    repository.update_multimodal_certification_session(
        "local",
        session_id,
        status="running",
        provider_dispatch_state="dispatched",
        post_dispatched=True,
    )
    repository.update_multimodal_certification_session(
        "local",
        session_id,
        status="running",
        provider_dispatch_state="confirmed",
        post_dispatched=True,
        upstream_operation_id="generation-atomic-finalizer",
    )


def test_r8c_certification_pair_finalizer_rolls_back_both_records(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Atomic finalizer",
            kind="openrouter",
            base_url="https://audio.example/v1",
            api_key="secret",
            scopes=["audio"],
        ),
    )
    _claim_repository_audio_certification_pair(
        repository,
        connection_id=connection.id,
        certification_id="cert-atomic-finalizer",
        session_id="session-atomic-finalizer",
    )
    _mark_repository_audio_certification_confirmed(
        repository,
        session_id="session-atomic-finalizer",
    )
    with repository._connect() as database:
        database.execute(
            """
            CREATE TRIGGER fail_multimodal_session_finalizer
            BEFORE UPDATE ON provider_multimodal_certification_sessions
            BEGIN
                SELECT RAISE(ABORT, 'simulated session finalizer failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="session finalizer failure"):
        repository.complete_multimodal_workload_certification(
            "local",
            "cert-atomic-finalizer",
            "session-atomic-finalizer",
            status="passed",
            checks={
                "http_ok": True,
                "content_observed": True,
                "response_complete": True,
                "media_format_verified": True,
                "actual_model_verified": True,
                "multimodal_adapter_verified": True,
            },
            warning_codes=[],
            actual_model="provider/audio",
        )

    certification = repository.get_workload_certification(
        "local", "cert-atomic-finalizer"
    )
    session = repository.get_multimodal_certification_session(
        "local", session_id="session-atomic-finalizer"
    )
    assert certification is not None and certification["status"] == "running"
    assert session is not None and session["status"] == "running"
    assert session["provider_dispatch_state"] == "confirmed"


@pytest.mark.parametrize("session_status", ["running", "passed", "failed"])
def test_r8c_restart_normalizes_split_certification_pair_without_refresh(
    tmp_path: Path,
    session_status: str,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Restart normalization",
            kind="openrouter",
            base_url="https://audio.example/v1",
            api_key="secret",
            scopes=["audio"],
        ),
    )
    _claim_repository_audio_certification_pair(
        repository,
        connection_id=connection.id,
        certification_id="cert-restart-split",
        session_id="session-restart-split",
    )
    _mark_repository_audio_certification_confirmed(
        repository,
        session_id="session-restart-split",
    )
    if session_status != "running":
        repository.update_multimodal_certification_session(
            "local",
            "session-restart-split",
            status=session_status,
            provider_dispatch_state="confirmed",
            post_dispatched=True,
            completed=True,
        )

    recovered = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    certification = recovered.get_workload_certification(
        "local", "cert-restart-split"
    )
    session = recovered.get_multimodal_certification_session(
        "local", session_id="session-restart-split"
    )
    assert certification is not None
    assert certification["status"] == "uncertain"
    assert certification["error_code"] == "server_restarted"
    assert session is not None
    assert session["status"] == "uncertain"
    assert session["provider_dispatch_state"] == "uncertain"
    assert session["error_code"] == "server_restarted"
    with pytest.raises(RouterRepositoryError) as exc_info:
        recovered.claim_multimodal_certification_refresh(
            "local",
            "cert-restart-split",
            expected_contract_version="modelmirror-provider-workload-routing-v1",
            expected_protocol_version=PROVIDER_MULTIMODAL_PROTOCOL_VERSION,
        )
    assert str(exc_info.value) == (
        "provider_multimodal_certification_not_refreshable"
    )


def test_r8c_busy_session_store_creates_no_orphan_certification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Busy Audio",
            kind="openrouter",
            base_url="https://audio.example/v1",
            api_key="secret",
            scopes=["audio"],
        ),
    )
    fingerprint = repository.connection_config_fingerprint("local", connection.id)
    original_connect = repository._connect

    def short_connect() -> sqlite3.Connection:
        database = sqlite3.connect(repository.database_path, timeout=0.01)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys = ON")
        return database

    locker = original_connect()
    locker.execute("BEGIN IMMEDIATE")
    monkeypatch.setattr(repository, "_connect", short_connect)
    try:
        with pytest.raises(RouterRepositoryError) as exc_info:
            repository.claim_workload_certification(
                "local",
                certification_id="cert-busy",
                connection_id=connection.id,
                connection_fingerprint=fingerprint,
                contract_version="modelmirror-provider-workload-routing-v1",
                execution_shape="audio_transcription",
                requested_model="provider/audio",
                profile={"input_format": "wav"},
                profile_fingerprint="profile-fingerprint",
                idempotency_key_hash="busy-key-hash",
                adapter_contract="openrouter_audio_transcription_json_v1",
                protocol_version=PROVIDER_MULTIMODAL_PROTOCOL_VERSION,
                multimodal_session_id="session-busy",
            )
    finally:
        locker.rollback()
        locker.close()

    assert str(exc_info.value) == "provider_multimodal_session_store_busy"
    assert repository.list_workload_certifications("local") == []
    assert repository.list_multimodal_certification_sessions("local") == []


def test_r8c_busy_connection_open_maps_stable_error_before_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Busy Open Audio",
            kind="openrouter",
            base_url="https://audio.example/v1",
            api_key="secret",
            scopes=["audio"],
        ),
    )
    fingerprint = repository.connection_config_fingerprint("local", connection.id)

    def busy_connect() -> sqlite3.Connection:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repository, "_connect", busy_connect)

    with pytest.raises(RouterRepositoryError) as exc_info:
        repository.claim_workload_certification(
            "local",
            certification_id="cert-open-busy",
            connection_id=connection.id,
            connection_fingerprint=fingerprint,
            contract_version="modelmirror-provider-workload-routing-v1",
            execution_shape="audio_transcription",
            requested_model="provider/audio",
            profile={"input_format": "wav"},
            profile_fingerprint="profile-fingerprint",
            idempotency_key_hash="open-busy-key-hash",
            adapter_contract="openrouter_audio_transcription_json_v1",
            protocol_version=PROVIDER_MULTIMODAL_PROTOCOL_VERSION,
            multimodal_session_id="session-open-busy",
        )

    assert str(exc_info.value) == "provider_multimodal_session_store_busy"
    monkeypatch.undo()
    assert repository.list_workload_certifications("local") == []
    assert repository.list_multimodal_certification_sessions("local") == []


def test_r8c_certification_wav_contains_short_non_silent_speech() -> None:
    with wave.open(io.BytesIO(SYNTHETIC_AUDIO_WAV_BYTES), "rb") as sample:
        assert sample.getframerate() == 8_000
        assert sample.getnchannels() == 1
        assert sample.getsampwidth() == 2
        assert 0.1 <= sample.getnframes() / sample.getframerate() <= 1.0
        frames = sample.readframes(sample.getnframes())
    values = struct.unpack(f"<{len(frames) // 2}h", frames)
    assert max(abs(value) for value in values) > 1_000


def _service(
    tmp_path: Path,
    transport: httpx.MockTransport,
    *,
    kind: str,
) -> tuple[ModelRouterService, object]:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="R8C audio",
            kind=kind,
            base_url="https://provider.example/v1",
            api_key="r8c-secret",
            scopes=["audio"],
        ),
    )
    service = ModelRouterService(
        repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    return service, connection


async def _certify_and_activate_audio(
    service: ModelRouterService,
    connection: object,
    transport: httpx.AsyncBaseTransport,
    monkeypatch: pytest.MonkeyPatch,
    *,
    entry_id: str,
    shape: str,
    adapter: str,
    model_id: str,
    idempotency_key: str,
) -> ManagedMultimodalGateway:
    certification = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape=shape,  # type: ignore[arg-type]
            model_id=model_id,
            adapter_contract=adapter,  # type: ignore[arg-type]
            acknowledge_billed_call=True,
        ),
        idempotency_key=idempotency_key,
    )
    assert certification.status == "passed"
    monkeypatch.setenv(
        "MODEL_CONTROL_TRANSCRIPTION_ENABLED"
        if shape == "audio_transcription"
        else "MODEL_CONTROL_SPEECH_ENABLED",
        "true",
    )
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        entry_id,  # type: ignore[arg-type]
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape=shape,  # type: ignore[arg-type]
                    model_id=model_id,
                    connection_id=connection.id,
                    adapter_contract=adapter,  # type: ignore[arg-type]
                )
            ],
        ),
    )
    control.activate(
        entry_id,  # type: ignore[arg-type]
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    return ManagedMultimodalGateway.for_router(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )


@pytest.mark.parametrize(
    ("adapter", "shape", "endpoint"),
    [
        (
            "openrouter_audio_transcription_json_v1",
            "audio_transcription",
            "/v1/audio/transcriptions",
        ),
        (
            "openai_compatible_audio_transcription_multipart_v1",
            "audio_transcription",
            "/v1/audio/transcriptions",
        ),
        (
            "openrouter_audio_speech_v1",
            "audio_speech",
            "/v1/audio/speech",
        ),
        (
            "openai_compatible_audio_speech_v1",
            "audio_speech",
            "/v1/audio/speech",
        ),
    ],
)
def test_r8c_adapter_resolves_audio_endpoint(
    adapter: str,
    shape: str,
    endpoint: str,
) -> None:
    target = ProviderMultimodalTarget.create(
        provider_kind=("openrouter" if adapter.startswith("openrouter") else "newapi"),
        connection_id="connection-audio",
        base_url="https://provider.example/v1/chat/completions",
        api_key="do-not-print",
        adapter_contract=adapter,  # type: ignore[arg-type]
        execution_shape=shape,  # type: ignore[arg-type]
    )
    assert target.endpoint_url.endswith(endpoint)
    assert "do-not-print" not in repr(target)


@pytest.mark.asyncio
async def test_openrouter_generation_metadata_get_uses_one_pinned_ip_without_retry(
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ConnectError("first approved address unavailable", request=request)

    target = ProviderMultimodalTarget.create(
        provider_kind="openrouter",
        connection_id="connection-audio",
        base_url="https://provider.example/v1",
        api_key="r8c-secret",
        adapter_contract="openrouter_audio_transcription_json_v1",
        execution_shape="audio_transcription",
    )
    transport = ProviderMultimodalTransport(
        ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8", "1.1.1.1"]
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        with pytest.raises(httpx.ConnectError):
            await transport.fetch_openrouter_generation_model(
                client,
                target,
                "gen-one-address",
                timeout_seconds=0.02,
            )

    assert len(requests) == 1
    assert requests[0].url.host == "1.1.1.1"
    assert requests[0].headers["host"] == "provider.example"
    assert requests[0].extensions["timeout"] == {
        "connect": 0.02,
        "read": 0.02,
        "write": 0.02,
        "pool": 0.02,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "adapter", "shape"),
    [
        (
            "openrouter",
            "openrouter_audio_transcription_json_v1",
            "audio_transcription",
        ),
        (
            "newapi",
            "openai_compatible_audio_transcription_multipart_v1",
            "audio_transcription",
        ),
        ("openrouter", "openrouter_audio_speech_v1", "audio_speech"),
        ("newapi", "openai_compatible_audio_speech_v1", "audio_speech"),
    ],
)
async def test_r8c_audio_certification_sends_exactly_one_paid_post(
    tmp_path: Path,
    kind: str,
    adapter: str,
    shape: str,
) -> None:
    requests: list[httpx.Request] = []
    model_id = (
        "microsoft/mai-voice-2"
        if adapter == "openrouter_audio_speech_v1"
        else "audio/model"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        assert request.headers["authorization"] == "Bearer r8c-secret"
        if shape == "audio_transcription":
            if adapter.startswith("openrouter"):
                body = json.loads(request.content)
                assert body["input_audio"]["format"] == "wav"
            else:
                assert "multipart/form-data" in request.headers["content-type"]
                assert b"modelmirror-certification.wav" in request.content
            return httpx.Response(
                200,
                json={"text": "OK", "model": model_id},
            )
        body = json.loads(request.content)
        assert body["input"] == "OK"
        assert body["voice"]
        return httpx.Response(
            200,
            content=MP3_BYTES,
            headers={"content-type": "audio/mpeg", "x-model-id": model_id},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind=kind)
    result = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape=shape,  # type: ignore[arg-type]
            model_id=model_id,
            adapter_contract=adapter,  # type: ignore[arg-type]
            acknowledge_billed_call=True,
        ),
        idempotency_key=f"r8c-{kind}-{shape}",
    )
    assert result.status == "passed"
    assert result.checks.media_format_verified is True
    assert [item.method for item in requests].count("POST") == 1
    assert "r8c-secret" not in json.dumps(result.model_dump())


@pytest.mark.asyncio
async def test_openrouter_pcm_speech_certification_validates_format(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    model_id = "google/gemini-3.1-flash-tts-preview"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        body = json.loads(request.content)
        assert body["response_format"] == "pcm"
        return httpx.Response(
            200,
            content=b"\x00\x01" * 128,
            headers={
                "content-type": "audio/pcm;rate=24000;channels=1",
                "x-model-id": model_id,
            },
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    result = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="audio_speech",
            model_id=model_id,
            adapter_contract="openrouter_audio_speech_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="r8c-openrouter-pcm-speech",
    )
    assert result.status == "passed"
    assert result.checks.media_format_verified is True
    assert [item.method for item in requests].count("POST") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "entry_id", "shape", "adapter", "model_id"),
    [
        (
            "openrouter",
            "multimodal_transcription",
            "audio_transcription",
            "openrouter_audio_transcription_json_v1",
            "openai/whisper-1",
        ),
        (
            "newapi",
            "multimodal_transcription",
            "audio_transcription",
            "openai_compatible_audio_transcription_multipart_v1",
            "openai/whisper-1",
        ),
        (
            "openrouter",
            "multimodal_speech",
            "audio_speech",
            "openrouter_audio_speech_v1",
            "microsoft/mai-voice-2",
        ),
        (
            "newapi",
            "multimodal_speech",
            "audio_speech",
            "openai_compatible_audio_speech_v1",
            "gpt-4o-mini-tts",
        ),
    ],
)
async def test_r8c_managed_audio_runtime_is_one_post_and_replay_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    entry_id: str,
    shape: str,
    adapter: str,
    model_id: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        if shape == "audio_transcription":
            return httpx.Response(
                200,
                json={
                    "text": "private transcript must stay out of receipt",
                    "model": model_id,
                },
            )
        return httpx.Response(
            200,
            content=MP3_BYTES,
            headers={"content-type": "audio/mpeg", "x-model-id": model_id},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind=kind)
    certification = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape=shape,  # type: ignore[arg-type]
            model_id=model_id,
            adapter_contract=adapter,  # type: ignore[arg-type]
            acknowledge_billed_call=True,
        ),
        idempotency_key=f"cert-{entry_id}-{kind}",
    )
    assert certification.status == "passed"

    monkeypatch.setenv(
        "MODEL_CONTROL_TRANSCRIPTION_ENABLED"
        if shape == "audio_transcription"
        else "MODEL_CONTROL_SPEECH_ENABLED",
        "true",
    )
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        entry_id,  # type: ignore[arg-type]
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape=shape,  # type: ignore[arg-type]
                    model_id=model_id,
                    connection_id=connection.id,
                    adapter_contract=adapter,  # type: ignore[arg-type]
                )
            ],
        ),
    )
    control.activate(
        entry_id,  # type: ignore[arg-type]
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    gateway = ManagedMultimodalGateway.for_router(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    if shape == "audio_transcription":
        runtime = TranscriptionService(
            service, managed_gateway=gateway
        )

        async def invoke(key: str | None = "same-runtime-key"):
            return await runtime.transcribe(
                model_id=model_id,
                filename="sample.wav",
                content_type="audio/wav",
                content=SYNTHETIC_AUDIO_WAV_BYTES,
                language="auto",
                idempotency_key=key,
            )
    else:
        runtime = SpeechService(service, managed_gateway=gateway)
        voice = (
            "en-US-Harper:MAI-Voice-2"
            if kind == "openrouter"
            else "alloy"
        )

        async def invoke(key: str | None = "same-runtime-key"):
            return await runtime.synthesize(
                model_id=model_id,
                text="private speech input must stay out of receipt",
                voice=voice,
                response_format="mp3",
                speed=1.0,
                idempotency_key=key,
            )

    with pytest.raises(MultimodalServiceError) as missing:
        await invoke(None)
    assert missing.value.code == "invalid_idempotency_key"
    assert missing.value.route_receipt["call_count"] == 0
    assert [item.method for item in requests].count("POST") == 1
    result = await invoke()
    assert result.execution_mode == "managed"
    assert result.provider_route_receipts
    serialized = json.dumps(result.provider_route_receipts)
    assert "r8c-secret" not in serialized
    assert "private" not in serialized
    with pytest.raises(MultimodalServiceError):
        await invoke()
    assert [item.method for item in requests].count("POST") == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shape", "entry_id", "adapter", "invalid_response"),
    [
        (
            "audio_transcription",
            "multimodal_transcription",
            "openrouter_audio_transcription_json_v1",
            httpx.Response(200, json={"text": ""}),
        ),
        (
            "audio_speech",
            "multimodal_speech",
            "openrouter_audio_speech_v1",
            httpx.Response(
                200,
                content=b"not-audio",
                headers={"content-type": "audio/mpeg"},
            ),
        ),
    ],
)
async def test_r8c_invalid_provider_payload_is_one_post_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shape: str,
    entry_id: str,
    adapter: str,
    invalid_response: httpx.Response,
) -> None:
    requests: list[httpx.Request] = []
    model_id = (
        "openai/whisper-1"
        if shape == "audio_transcription"
        else "microsoft/mai-voice-2"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        if len([item for item in requests if item.method == "POST"]) == 1:
            if shape == "audio_transcription":
                return httpx.Response(200, json={"text": "OK", "model": model_id})
            return httpx.Response(
                200,
                content=MP3_BYTES,
                headers={"content-type": "audio/mpeg", "x-model-id": model_id},
            )
        return invalid_response

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    certification = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape=shape,  # type: ignore[arg-type]
            model_id=model_id,
            adapter_contract=adapter,  # type: ignore[arg-type]
            acknowledge_billed_call=True,
        ),
        idempotency_key=f"invalid-{shape}-cert",
    )
    assert certification.status == "passed"
    monkeypatch.setenv(
        "MODEL_CONTROL_TRANSCRIPTION_ENABLED"
        if shape == "audio_transcription"
        else "MODEL_CONTROL_SPEECH_ENABLED",
        "true",
    )
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        entry_id,  # type: ignore[arg-type]
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape=shape,  # type: ignore[arg-type]
                    model_id=model_id,
                    connection_id=connection.id,
                    adapter_contract=adapter,  # type: ignore[arg-type]
                )
            ],
        ),
    )
    control.activate(
        entry_id,  # type: ignore[arg-type]
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    gateway = ManagedMultimodalGateway.for_router(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    if shape == "audio_transcription":
        with pytest.raises(MultimodalServiceError) as failed:
            await TranscriptionService(service, managed_gateway=gateway).transcribe(
                model_id=model_id,
                filename="private.wav",
                content_type="audio/wav",
                content=SYNTHETIC_AUDIO_WAV_BYTES,
                language="auto",
                idempotency_key=f"invalid-{shape}-runtime",
            )
    else:
        with pytest.raises(MultimodalServiceError) as failed:
            await SpeechService(service, managed_gateway=gateway).synthesize(
                model_id=model_id,
                text="private speech input",
                voice="en-US-Harper:MAI-Voice-2",
                response_format="mp3",
                speed=1.0,
                idempotency_key=f"invalid-{shape}-runtime",
            )
    assert failed.value.route_receipt["call_count"] == 1
    assert [item.method for item in requests].count("POST") == 2
    serialized = json.dumps(
        service.repository.list_workload_receipts("local")
    )
    assert "private" not in serialized
    assert "not-audio" not in serialized
    assert "r8c-secret" not in serialized


@pytest.mark.asyncio
async def test_r8c_connection_drift_blocks_before_runtime_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    model_id = "openai/whisper-1"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(200, json={"text": "OK", "model": model_id})

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    certification = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="audio_transcription",
            model_id=model_id,
            adapter_contract="openrouter_audio_transcription_json_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="drift-cert",
    )
    assert certification.status == "passed"
    monkeypatch.setenv("MODEL_CONTROL_TRANSCRIPTION_ENABLED", "true")
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        "multimodal_transcription",
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape="audio_transcription",
                    model_id=model_id,
                    connection_id=connection.id,
                    adapter_contract="openrouter_audio_transcription_json_v1",
                )
            ],
        ),
    )
    control.activate(
        "multimodal_transcription",
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    await service.update_connection(
        connection.id,
        RouterConnectionUpdate(api_key="rotated-secret"),
    )
    gateway = ManagedMultimodalGateway.for_router(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    with pytest.raises(MultimodalServiceError) as blocked:
        await TranscriptionService(service, managed_gateway=gateway).transcribe(
            model_id=model_id,
            filename="private.wav",
            content_type="audio/wav",
            content=SYNTHETIC_AUDIO_WAV_BYTES,
            language="auto",
            idempotency_key="drift-runtime",
        )
    assert blocked.value.code in {
        "provider_workload_binding_missing",
        "provider_workload_policy_not_active",
    }
    assert [item.method for item in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_openrouter_audio_certification_refreshes_model_from_generation_metadata(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    tracked_responses: list[httpx.Response] = []
    model_id = "openai/whisper-1"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/generation":
            assert request.method == "GET"
            assert request.url.params["id"] == "gen-cert"
            assert request.url.host == "8.8.8.8"
            assert request.headers["host"] == "provider.example"
            assert request.extensions["sni_hostname"] == "provider.example"
            metadata_response = httpx.Response(
                200, json={"data": {"model": model_id}}
            )
            tracked_responses.append(metadata_response)
            return metadata_response
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        paid_response = httpx.Response(
            200,
            json={"text": "OK"},
            headers={"X-Generation-Id": "gen-cert"},
        )
        tracked_responses.append(paid_response)
        return paid_response

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    certification_service = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    result = await certification_service.run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="audio_transcription",
            model_id=model_id,
            adapter_contract="openrouter_audio_transcription_json_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="metadata-certification",
    )

    assert result.status == "uncertain"
    assert result.error_code == "provider_multimodal_actual_model_pending"
    assert result.actual_model is None
    assert result.checks.actual_model_verified is False
    assert result.provider_dispatch_state == "confirmed"
    assert result.retry_allowed is False
    assert result.refresh_available is True
    assert [item.method for item in requests].count("POST") == 1
    assert [item.url.path for item in requests].count("/v1/generation") == 0
    pending_row = service.repository.get_workload_certification(
        "local", str(result.certification_id)
    )
    assert pending_row is not None
    paid_post_completed_at = pending_row["completed_at"]

    result = await certification_service.refresh_multimodal_certification(
        str(result.certification_id)
    )

    assert result.status == "passed"
    assert result.actual_model == model_id
    assert result.checks.actual_model_verified is True
    assert result.refresh_available is False
    completed_row = service.repository.get_workload_certification(
        "local", str(result.certification_id)
    )
    assert completed_row is not None
    assert completed_row["completed_at"] == paid_post_completed_at
    assert [item.method for item in requests].count("POST") == 1
    assert [item.url.path for item in requests].count("/v1/generation") == 1
    replay = await certification_service.refresh_multimodal_certification(
        str(result.certification_id)
    )
    assert replay.status == "passed"
    assert [item.url.path for item in requests].count("/v1/generation") == 1
    with sqlite3.connect(service.repository.database_path) as database:
        database.execute(
            "UPDATE provider_workload_certifications "
            "SET protocol_version = 'obsolete-protocol' "
            "WHERE tenant_id = 'local' AND id = ?",
            (str(result.certification_id),),
        )
    stale = await certification_service.refresh_multimodal_certification(
        str(result.certification_id)
    )
    assert stale.status == "stale"
    assert stale.blocked_reason == "provider_multimodal_protocol_stale"
    assert [item.url.path for item in requests].count("/v1/generation") == 1
    assert tracked_responses and all(item.is_closed for item in tracked_responses)


@pytest.mark.asyncio
async def test_openrouter_audio_generation_metadata_404_remains_refreshable_without_repost(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    responses: list[httpx.Response] = []
    model_id = "openai/whisper-1"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/generation":
            response = httpx.Response(404, json={"error": {"code": "not_found"}})
            responses.append(response)
            return response
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        response = httpx.Response(
            200,
            json={"text": "OK"},
            headers={"X-Generation-Id": "gen-pending"},
        )
        responses.append(response)
        return response

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    certification_service = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    request = ProviderWorkloadCertificationRequest(
        execution_shape="audio_transcription",
        model_id=model_id,
        adapter_contract="openrouter_audio_transcription_json_v1",
        acknowledge_billed_call=True,
    )
    result = await certification_service.run(
        connection.id,
        request,
        idempotency_key="metadata-pending",
    )

    assert result.status == "uncertain"
    assert result.error_code == "provider_multimodal_actual_model_pending"
    session = service.repository.get_multimodal_certification_session(
        "local", certification_id=str(result.certification_id)
    )
    assert session is not None
    assert session["upstream_operation_id"] == "gen-pending"
    assert session["provider_dispatch_state"] == "confirmed"
    assert session["poll_count"] == 0

    first_refresh = await certification_service.refresh_multimodal_certification(
        str(result.certification_id)
    )
    assert first_refresh.status == "uncertain"
    assert first_refresh.error_code == "provider_multimodal_actual_model_pending"
    assert first_refresh.refresh_available is True
    replay = await certification_service.run(
        connection.id,
        request,
        idempotency_key="metadata-pending",
    )
    assert replay.status == "uncertain"
    second_refresh = await certification_service.refresh_multimodal_certification(
        str(result.certification_id)
    )
    assert second_refresh.status == "uncertain"
    session = service.repository.get_multimodal_certification_session(
        "local", certification_id=str(result.certification_id)
    )
    assert session is not None and session["poll_count"] == 2
    assert [item.method for item in requests].count("POST") == 1
    assert [item.url.path for item in requests].count("/v1/generation") == 2
    assert responses and all(item.is_closed for item in responses)


@pytest.mark.asyncio
async def test_openrouter_speech_generation_metadata_refresh_is_poll_only(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    model_id = "deepgram/flux-tts:free"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/generation":
            return httpx.Response(200, json={"data": {"model": model_id}})
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(
            200,
            content=MP3_BYTES,
            headers={
                "content-type": "audio/mpeg",
                "X-Generation-Id": "gen-speech-pending",
            },
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    certifications = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    pending = await certifications.run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="audio_speech",
            model_id=model_id,
            adapter_contract="openrouter_audio_speech_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="speech-metadata-pending",
    )

    assert pending.status == "uncertain"
    assert pending.refresh_available is True
    assert [item.method for item in requests].count("POST") == 1
    assert [item.url.path for item in requests].count("/v1/generation") == 0
    refreshed = await certifications.refresh_multimodal_certification(
        str(pending.certification_id)
    )
    assert refreshed.status == "passed"
    assert refreshed.actual_model == model_id
    assert [item.method for item in requests].count("POST") == 1
    assert [item.url.path for item in requests].count("/v1/generation") == 1


@pytest.mark.asyncio
async def test_openrouter_audio_refresh_rejects_expired_post_evidence_before_get(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    model_id = "openai/whisper-1"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(
            200,
            json={"text": "OK"},
            headers={"X-Generation-Id": "gen-expired"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    certifications = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    request = ProviderWorkloadCertificationRequest(
        execution_shape="audio_transcription",
        model_id=model_id,
        adapter_contract="openrouter_audio_transcription_json_v1",
        acknowledge_billed_call=True,
    )
    pending = await certifications.run(
        connection.id,
        request,
        idempotency_key="metadata-expired",
    )
    with sqlite3.connect(service.repository.database_path) as database:
        database.execute(
            "UPDATE provider_workload_certifications "
            "SET completed_at = '2020-01-01T00:00:00+00:00' "
            "WHERE tenant_id = 'local' AND id = ?",
            (str(pending.certification_id),),
        )

    with pytest.raises(RouterServiceError) as blocked:
        await certifications.refresh_multimodal_certification(
            str(pending.certification_id)
        )
    assert blocked.value.code == "provider_workload_certification_expired"
    assert [item.url.path for item in requests].count("/v1/generation") == 0
    replay = await certifications.run(
        connection.id,
        request,
        idempotency_key="metadata-expired",
    )
    assert replay.status == "uncertain"
    assert replay.refresh_available is False
    assert [item.method for item in requests].count("POST") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("column", ["contract_version", "protocol_version"])
async def test_openrouter_audio_refresh_rejects_contract_drift_before_get(
    tmp_path: Path,
    column: str,
) -> None:
    requests: list[httpx.Request] = []
    model_id = "openai/whisper-1"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(
            200,
            json={"text": "OK"},
            headers={"X-Generation-Id": "gen-contract-drift"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    certifications = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    request = ProviderWorkloadCertificationRequest(
        execution_shape="audio_transcription",
        model_id=model_id,
        adapter_contract="openrouter_audio_transcription_json_v1",
        acknowledge_billed_call=True,
    )
    pending = await certifications.run(
        connection.id,
        request,
        idempotency_key=f"metadata-drift-{column}",
    )
    assert column in {"contract_version", "protocol_version"}
    with sqlite3.connect(service.repository.database_path) as database:
        database.execute(
            f"UPDATE provider_workload_certifications SET {column} = ? "
            "WHERE tenant_id = 'local' AND id = ?",
            ("obsolete-contract", str(pending.certification_id)),
        )

    with pytest.raises(RouterServiceError) as blocked:
        await certifications.refresh_multimodal_certification(
            str(pending.certification_id)
        )
    assert blocked.value.code == "provider_multimodal_certification_contract_stale"
    assert [item.url.path for item in requests].count("/v1/generation") == 0
    replay = await certifications.run(
        connection.id,
        request,
        idempotency_key=f"metadata-drift-{column}",
    )
    assert replay.status == "stale"
    assert replay.blocked_reason == (
        "provider_workload_contract_stale"
        if column == "contract_version"
        else "provider_multimodal_protocol_stale"
    )
    assert replay.refresh_available is False
    assert [item.method for item in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_openrouter_audio_refresh_projection_requires_confirmed_complete_evidence(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    model_id = "openai/whisper-1"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(
            200,
            json={"text": "OK"},
            headers={"X-Generation-Id": "gen-projection"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    certifications = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    request = ProviderWorkloadCertificationRequest(
        execution_shape="audio_transcription",
        model_id=model_id,
        adapter_contract="openrouter_audio_transcription_json_v1",
        acknowledge_billed_call=True,
    )
    pending = await certifications.run(
        connection.id,
        request,
        idempotency_key="metadata-projection",
    )
    assert pending.refresh_available is True
    with sqlite3.connect(service.repository.database_path) as database:
        database.execute(
            "UPDATE provider_multimodal_certification_sessions "
            "SET provider_dispatch_state = 'uncertain' "
            "WHERE tenant_id = 'local' AND certification_id = ?",
            (str(pending.certification_id),),
        )
    replay = await certifications.run(
        connection.id,
        request,
        idempotency_key="metadata-projection",
    )
    assert replay.refresh_available is False

    with sqlite3.connect(service.repository.database_path) as database:
        database.execute(
            "UPDATE provider_multimodal_certification_sessions "
            "SET provider_dispatch_state = 'confirmed' "
            "WHERE tenant_id = 'local' AND certification_id = ?",
            (str(pending.certification_id),),
        )
        database.execute(
            "UPDATE provider_workload_certifications SET checks_json = ? "
            "WHERE tenant_id = 'local' AND id = ?",
            (
                json.dumps(
                    {
                        "http_ok": True,
                        "content_observed": False,
                        "response_complete": True,
                        "media_format_verified": True,
                    }
                ),
                str(pending.certification_id),
            ),
        )
    replay = await certifications.run(
        connection.id,
        request,
        idempotency_key="metadata-projection",
    )
    assert replay.refresh_available is False
    assert [item.method for item in requests].count("POST") == 1
    assert [item.url.path for item in requests].count("/v1/generation") == 0


@pytest.mark.asyncio
async def test_openrouter_audio_refresh_fails_closed_on_actual_model_mismatch(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    model_id = "openai/whisper-1"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/generation":
            return httpx.Response(200, json={"data": {"model": "other/model"}})
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(
            200,
            json={"text": "OK"},
            headers={"X-Generation-Id": "gen-mismatch"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    certification_service = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    pending = await certification_service.run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="audio_transcription",
            model_id=model_id,
            adapter_contract="openrouter_audio_transcription_json_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="metadata-mismatch",
    )
    result = await certification_service.refresh_multimodal_certification(
        str(pending.certification_id)
    )

    assert result.status == "failed"
    assert result.actual_model == "other/model"
    assert result.checks.actual_model_verified is True
    assert result.error_code == "provider_workload_model_mismatch"
    assert result.refresh_available is False
    assert [item.method for item in requests].count("POST") == 1
    assert [item.url.path for item in requests].count("/v1/generation") == 1


@pytest.mark.asyncio
async def test_openrouter_audio_refresh_claim_allows_only_one_concurrent_get(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    model_id = "openai/whisper-1"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(
            200,
            json={"text": "OK"},
            headers={"X-Generation-Id": "gen-concurrent"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    first = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    second_repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    second_router_service = ModelRouterService(
        second_repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    second = ProviderWorkloadCertificationService(
        second_router_service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    pending = await first.run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="audio_transcription",
            model_id=model_id,
            adapter_contract="openrouter_audio_transcription_json_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="metadata-concurrent",
    )
    started = asyncio.Event()
    release = asyncio.Event()
    metadata_gets = 0

    async def delayed_metadata(*_args: object) -> str:
        nonlocal metadata_gets
        metadata_gets += 1
        started.set()
        await release.wait()
        return model_id

    monkeypatch.setattr(
        first.multimodal_transport,
        "fetch_openrouter_generation_model",
        delayed_metadata,
    )
    monkeypatch.setattr(
        second.multimodal_transport,
        "fetch_openrouter_generation_model",
        delayed_metadata,
    )
    first_task = asyncio.create_task(
        first.refresh_multimodal_certification(str(pending.certification_id))
    )
    await started.wait()
    with pytest.raises(RouterServiceError) as blocked:
        await second.refresh_multimodal_certification(str(pending.certification_id))
    assert blocked.value.code == (
        "provider_multimodal_certification_refresh_in_progress"
    )
    release.set()
    result = await first_task

    assert result.status == "passed"
    assert metadata_gets == 1
    assert [item.method for item in requests].count("POST") == 1
    session = service.repository.get_multimodal_certification_session(
        "local", certification_id=str(pending.certification_id)
    )
    assert session is not None and session["poll_count"] == 1


@pytest.mark.asyncio
async def test_openrouter_audio_refresh_recovers_claim_after_server_restart(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    model_id = "openai/whisper-1"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/generation":
            return httpx.Response(200, json={"data": {"model": model_id}})
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(
            200,
            json={"text": "OK"},
            headers={"X-Generation-Id": "gen-restart"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    certification_service = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    pending = await certification_service.run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="audio_transcription",
            model_id=model_id,
            adapter_contract="openrouter_audio_transcription_json_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="metadata-restart",
    )
    service.repository.claim_multimodal_certification_refresh(
        "local",
        str(pending.certification_id),
        expected_contract_version="modelmirror-provider-workload-routing-v1",
        expected_protocol_version=PROVIDER_MULTIMODAL_PROTOCOL_VERSION,
    )

    recovered_repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    recovered_certification = recovered_repository.get_workload_certification(
        "local", str(pending.certification_id)
    )
    recovered_session = recovered_repository.get_multimodal_certification_session(
        "local", certification_id=str(pending.certification_id)
    )
    assert recovered_certification is not None
    assert recovered_certification["status"] == "uncertain"
    assert recovered_certification["error_code"] == "server_restarted"
    assert recovered_session is not None
    assert recovered_session["status"] == "uncertain"
    recovered_service = ModelRouterService(
        recovered_repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    result = await ProviderWorkloadCertificationService(
        recovered_service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).refresh_multimodal_certification(str(pending.certification_id))

    assert result.status == "passed"
    assert [item.method for item in requests].count("POST") == 1
    assert [item.url.path for item in requests].count("/v1/generation") == 1
    session = recovered_repository.get_multimodal_certification_session(
        "local", certification_id=str(pending.certification_id)
    )
    assert session is not None and session["poll_count"] == 2


@pytest.mark.asyncio
async def test_openrouter_audio_pending_evidence_survives_crash_before_finalizer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    model_id = "openai/whisper-1"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/generation":
            return httpx.Response(200, json={"data": {"model": model_id}})
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(
            200,
            json={"text": "OK"},
            headers={"X-Generation-Id": "gen-finalizer-crash"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    certification_service = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )

    def simulate_process_crash(*_args, **_kwargs):
        raise RuntimeError("simulated crash before certification finalizer")

    monkeypatch.setattr(
        service.repository,
        "complete_workload_certification",
        simulate_process_crash,
    )
    with pytest.raises(
        RuntimeError, match="simulated crash before certification finalizer"
    ):
        await certification_service.run(
            connection.id,
            ProviderWorkloadCertificationRequest(
                execution_shape="audio_transcription",
                model_id=model_id,
                adapter_contract="openrouter_audio_transcription_json_v1",
                acknowledge_billed_call=True,
            ),
            idempotency_key="metadata-finalizer-crash",
        )

    assert [item.method for item in requests].count("POST") == 1
    assert [item.url.path for item in requests].count("/v1/generation") == 0
    recovered_repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    certification = recovered_repository.list_workload_certifications(
        "local", connection_id=connection.id
    )[0]
    session = recovered_repository.get_multimodal_certification_session(
        "local", certification_id=str(certification["id"])
    )
    assert certification["status"] == "uncertain"
    assert json.loads(str(certification["checks_json"]))[
        "media_format_verified"
    ] is True
    assert session is not None
    assert session["status"] == "uncertain"
    assert session["provider_dispatch_state"] == "confirmed"
    assert session["upstream_operation_id"] == "gen-finalizer-crash"

    recovered_service = ModelRouterService(
        recovered_repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    refreshed = await ProviderWorkloadCertificationService(
        recovered_service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).refresh_multimodal_certification(str(certification["id"]))

    assert refreshed.status == "passed"
    assert refreshed.actual_model == model_id
    assert [item.method for item in requests].count("POST") == 1
    assert [item.url.path for item in requests].count("/v1/generation") == 1


@pytest.mark.asyncio
async def test_r8c_direct_certification_crash_before_atomic_finalizer_is_not_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    model_id = "openai/whisper-1"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(
            200,
            json={"text": "OK", "model": model_id},
            headers={"X-Generation-Id": "gen-direct-finalizer-crash"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    certification_service = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )

    def simulate_process_crash(*_args, **_kwargs):
        raise RuntimeError("simulated crash before atomic pair finalizer")

    monkeypatch.setattr(
        service.repository,
        "complete_multimodal_workload_certification",
        simulate_process_crash,
    )
    with pytest.raises(RuntimeError, match="atomic pair finalizer"):
        await certification_service.run(
            connection.id,
            ProviderWorkloadCertificationRequest(
                execution_shape="audio_transcription",
                model_id=model_id,
                adapter_contract="openrouter_audio_transcription_json_v1",
                acknowledge_billed_call=True,
            ),
            idempotency_key="direct-finalizer-crash",
        )

    assert [item.method for item in requests].count("POST") == 1
    certification = service.repository.list_workload_certifications(
        "local", connection_id=connection.id
    )[0]
    session = service.repository.get_multimodal_certification_session(
        "local", certification_id=str(certification["id"])
    )
    assert certification["status"] == "running"
    assert session is not None and session["status"] == "running"
    assert session["provider_dispatch_state"] == "confirmed"

    recovered = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    recovered_certification = recovered.get_workload_certification(
        "local", str(certification["id"])
    )
    recovered_session = recovered.get_multimodal_certification_session(
        "local", certification_id=str(certification["id"])
    )
    assert recovered_certification is not None
    assert recovered_certification["status"] == "uncertain"
    assert recovered_certification["error_code"] == "server_restarted"
    assert recovered_session is not None
    assert recovered_session["status"] == "uncertain"
    assert recovered_session["provider_dispatch_state"] == "uncertain"
    with pytest.raises(RouterRepositoryError) as refresh_blocked:
        recovered.claim_multimodal_certification_refresh(
            "local",
            str(certification["id"]),
            expected_contract_version="modelmirror-provider-workload-routing-v1",
            expected_protocol_version=PROVIDER_MULTIMODAL_PROTOCOL_VERSION,
        )
    assert str(refresh_blocked.value) == (
        "provider_multimodal_certification_not_refreshable"
    )
    assert [item.method for item in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_openrouter_audio_refresh_blocks_connection_drift_before_get(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    model_id = "openai/whisper-1"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(
            200,
            json={"text": "OK"},
            headers={"X-Generation-Id": "gen-drift"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    certification_service = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    pending = await certification_service.run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="audio_transcription",
            model_id=model_id,
            adapter_contract="openrouter_audio_transcription_json_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="metadata-drift",
    )
    await service.update_connection(
        connection.id,
        RouterConnectionUpdate(api_key="rotated-secret"),
    )
    with pytest.raises(RouterServiceError) as blocked:
        await certification_service.refresh_multimodal_certification(
            str(pending.certification_id)
        )

    assert blocked.value.code == "provider_multimodal_dispatch_preconditions_changed"
    assert [item.method for item in requests].count("POST") == 1
    assert [item.url.path for item in requests].count("/v1/generation") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "adapter", "shape", "response"),
    [
        (
            "openrouter",
            "openrouter_audio_transcription_json_v1",
            "audio_transcription",
            httpx.Response(200, json={"text": "OK"}),
        ),
        (
            "newapi",
            "openai_compatible_audio_transcription_multipart_v1",
            "audio_transcription",
            httpx.Response(200, json={"text": "OK"}),
        ),
        (
            "openrouter",
            "openrouter_audio_speech_v1",
            "audio_speech",
            httpx.Response(
                200,
                content=MP3_BYTES,
                headers={"content-type": "audio/mpeg"},
            ),
        ),
        (
            "newapi",
            "openai_compatible_audio_speech_v1",
            "audio_speech",
            httpx.Response(
                200,
                content=MP3_BYTES,
                headers={"content-type": "audio/mpeg"},
            ),
        ),
    ],
)
async def test_r8c_certification_fails_closed_without_actual_model_evidence(
    tmp_path: Path,
    kind: str,
    adapter: str,
    shape: str,
    response: httpx.Response,
) -> None:
    requests: list[httpx.Request] = []
    model_id = (
        "microsoft/mai-voice-2"
        if adapter == "openrouter_audio_speech_v1"
        else "audio/model"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return response

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind=kind)
    result = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape=shape,  # type: ignore[arg-type]
            model_id=model_id,
            adapter_contract=adapter,  # type: ignore[arg-type]
            acknowledge_billed_call=True,
        ),
        idempotency_key=f"missing-model-{kind}-{shape}",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_multimodal_actual_model_unverified"
    assert result.can_run is False
    assert [item.method for item in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_legacy_passed_r8c_certification_without_model_evidence_is_stale(
    tmp_path: Path,
) -> None:
    model_id = "openai/whisper-1"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(200, json={"text": "OK", "model": model_id})

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    certification_service = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    certification = await certification_service.run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="audio_transcription",
            model_id=model_id,
            adapter_contract="openrouter_audio_transcription_json_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="legacy-evidence-certification",
    )
    assert certification.status == "passed"
    with service.repository._lock, service.repository._connect() as database:  # noqa: SLF001
        database.execute(
            """
            UPDATE provider_workload_certifications
            SET actual_model = NULL,
                checks_json = '{"actual_model_verified": false}'
            WHERE tenant_id = ? AND id = ?
            """,
            ("local", certification.certification_id),
        )

    summary = certification_service.list().certifications[0]
    assert summary.status == "stale"
    assert summary.can_run is False
    assert summary.blocked_reason == "provider_multimodal_actual_model_unverified"
    with pytest.raises(RouterServiceError) as blocked:
        ProviderWorkloadControlService(service).update_policy(
            "multimodal_transcription",
            ProviderWorkloadPolicyUpdate(
                expected_revision=0,
                bindings=[
                    ProviderWorkloadBindingUpdate(
                        execution_shape="audio_transcription",
                        model_id=model_id,
                        connection_id=connection.id,
                        adapter_contract="openrouter_audio_transcription_json_v1",
                    )
                ],
            ),
        )
    assert getattr(blocked.value, "code", "") == (
        "provider_multimodal_actual_model_unverified"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shape", "entry_id", "adapter", "model_id"),
    [
        (
            "audio_transcription",
            "multimodal_transcription",
            "openrouter_audio_transcription_json_v1",
            "openai/whisper-1",
        ),
        (
            "audio_speech",
            "multimodal_speech",
            "openrouter_audio_speech_v1",
            "microsoft/mai-voice-2",
        ),
    ],
)
async def test_r8c_runtime_uses_generation_metadata_without_second_paid_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shape: str,
    entry_id: str,
    adapter: str,
    model_id: str,
) -> None:
    requests: list[httpx.Request] = []
    post_count = 0
    metadata_get_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal metadata_get_count, post_count
        requests.append(request)
        if request.url.path == "/v1/generation":
            assert request.url.params["id"] == "gen-runtime"
            metadata_get_count += 1
            if metadata_get_count < 3:
                return httpx.Response(404, json={"error": {"code": "not_found"}})
            return httpx.Response(200, json={"data": {"model": model_id}})
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        if post_count == 1:
            if shape == "audio_transcription":
                return httpx.Response(200, json={"text": "OK", "model": model_id})
            return httpx.Response(
                200,
                content=MP3_BYTES,
                headers={"content-type": "audio/mpeg", "x-model-id": model_id},
            )
        if shape == "audio_transcription":
            return httpx.Response(
                200,
                json={"text": "runtime transcript"},
                headers={"X-Generation-Id": "gen-runtime"},
            )
        return httpx.Response(
            200,
            content=MP3_BYTES,
            headers={
                "content-type": "audio/mpeg",
                "X-Generation-Id": "gen-runtime",
            },
        )

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        multimodal_gateway_module,
        "_OPENROUTER_GENERATION_METADATA_POLL_DELAYS_SECONDS",
        (0.0, 0.0, 0.0, 0.0, 0.0),
    )
    service, connection = _service(tmp_path, transport, kind="openrouter")
    gateway = await _certify_and_activate_audio(
        service,
        connection,
        transport,
        monkeypatch,
        entry_id=entry_id,
        shape=shape,
        adapter=adapter,
        model_id=model_id,
        idempotency_key=f"metadata-runtime-cert-{shape}",
    )
    if shape == "audio_transcription":
        result = await TranscriptionService(
            service, managed_gateway=gateway
        ).transcribe(
            model_id=model_id,
            filename="sample.wav",
            content_type="audio/wav",
            content=SYNTHETIC_AUDIO_WAV_BYTES,
            language="auto",
            idempotency_key=f"metadata-runtime-{shape}",
        )
    else:
        result = await SpeechService(service, managed_gateway=gateway).synthesize(
            model_id=model_id,
            text="short test",
            voice="en-US-Harper:MAI-Voice-2",
            response_format="mp3",
            speed=1.0,
            idempotency_key=f"metadata-runtime-{shape}",
        )

    assert result.actual_model == model_id
    if shape == "audio_speech":
        assert result.generation_id is None
    assert [item.method for item in requests].count("POST") == 2
    assert [item.url.path for item in requests].count("/v1/generation") == 3
    receipts = ProviderWorkloadControlService(service).receipts(
        entry_id=entry_id  # type: ignore[arg-type]
    )
    call = receipts.runs[0].calls[0]
    assert call.generation_id_observed is True
    assert call.generation_metadata_get_count == 3
    assert call.generation_metadata_wait_ms is not None
    assert call.generation_metadata_wait_ms >= 0
    assert "gen-runtime" not in receipts.model_dump_json()


@pytest.mark.asyncio
async def test_r8c_runtime_generation_metadata_poll_is_bounded_and_not_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "openai/whisper-1"
    requests: list[httpx.Request] = []
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        requests.append(request)
        if request.url.path == "/v1/generation":
            return httpx.Response(404, json={"error": {"code": "not_found"}})
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        if post_count == 1:
            return httpx.Response(200, json={"text": "OK", "model": model_id})
        return httpx.Response(
            200,
            json={"text": "runtime transcript"},
            headers={"X-Generation-Id": "gen-runtime-pending"},
        )

    monkeypatch.setattr(
        multimodal_gateway_module,
        "_OPENROUTER_GENERATION_METADATA_POLL_DELAYS_SECONDS",
        (0.0, 0.0, 0.0, 0.0, 0.0),
    )
    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    gateway = await _certify_and_activate_audio(
        service,
        connection,
        transport,
        monkeypatch,
        entry_id="multimodal_transcription",
        shape="audio_transcription",
        adapter="openrouter_audio_transcription_json_v1",
        model_id=model_id,
        idempotency_key="metadata-bounded-cert",
    )
    runtime = TranscriptionService(service, managed_gateway=gateway)
    invoke = dict(
        model_id=model_id,
        filename="sample.wav",
        content_type="audio/wav",
        content=SYNTHETIC_AUDIO_WAV_BYTES,
        language="auto",
        idempotency_key="metadata-bounded-runtime",
    )

    with pytest.raises(MultimodalServiceError) as failed:
        await runtime.transcribe(**invoke)
    with pytest.raises(MultimodalServiceError):
        await runtime.transcribe(**invoke)

    assert failed.value.code == (
        "provider_multimodal_generation_metadata_wait_exhausted"
    )
    assert failed.value.route_receipt["status"] == "failed"
    assert [item.method for item in requests].count("POST") == 2
    assert [item.url.path for item in requests].count("/v1/generation") == 5
    receipts = ProviderWorkloadControlService(service).receipts(
        entry_id="multimodal_transcription"
    )
    call = receipts.runs[0].calls[0]
    assert call.generation_id_observed is True
    assert call.generation_metadata_get_count == 5
    assert call.generation_metadata_wait_ms is not None
    assert call.generation_metadata_wait_ms >= 0
    assert "gen-runtime-pending" not in receipts.model_dump_json()


@pytest.mark.asyncio
async def test_r8c_runtime_generation_metadata_attempt_timeout_can_recover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "openai/whisper-1"
    runtime_phase = False
    requests: list[httpx.Request] = []
    metadata_get_count = 0
    first_metadata_cancelled = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal metadata_get_count
        requests.append(request)
        if request.url.path == "/v1/generation":
            metadata_get_count += 1
            if metadata_get_count == 1:
                try:
                    await asyncio.Future()
                finally:
                    first_metadata_cancelled.set()
            return httpx.Response(200, json={"data": {"model": model_id}})
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        if not runtime_phase:
            return httpx.Response(200, json={"text": "OK", "model": model_id})
        return httpx.Response(
            200,
            json={"text": "runtime transcript"},
            headers={"X-Generation-Id": "gen-runtime-timeout-recovery"},
        )

    monkeypatch.setattr(
        multimodal_gateway_module,
        "_OPENROUTER_GENERATION_METADATA_POLL_DELAYS_SECONDS",
        (0.0, 0.0),
    )
    monkeypatch.setattr(
        multimodal_gateway_module,
        "OPENROUTER_GENERATION_METADATA_REQUEST_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        multimodal_gateway_module,
        "_OPENROUTER_GENERATION_METADATA_POLL_TIMEOUT_SECONDS",
        0.1,
    )
    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    gateway = await _certify_and_activate_audio(
        service,
        connection,
        transport,
        monkeypatch,
        entry_id="multimodal_transcription",
        shape="audio_transcription",
        adapter="openrouter_audio_transcription_json_v1",
        model_id=model_id,
        idempotency_key="metadata-attempt-timeout-cert",
    )
    runtime_phase = True
    requests.clear()
    result = await TranscriptionService(
        service, managed_gateway=gateway
    ).transcribe(
        model_id=model_id,
        filename="sample.wav",
        content_type="audio/wav",
        content=SYNTHETIC_AUDIO_WAV_BYTES,
        language="auto",
        idempotency_key="metadata-attempt-timeout-runtime",
    )

    assert result.actual_model == model_id
    assert first_metadata_cancelled.is_set()
    assert metadata_get_count == 2
    assert [item.method for item in requests].count("POST") == 1
    assert [item.url.path for item in requests].count("/v1/generation") == 2
    receipts = ProviderWorkloadControlService(service).receipts(
        entry_id="multimodal_transcription"
    )
    assert receipts.runs[0].calls[0].generation_metadata_get_count == 2


@pytest.mark.asyncio
async def test_r8c_runtime_generation_metadata_egress_drift_fails_immediately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "openai/whisper-1"
    requests: list[httpx.Request] = []
    post_count = 0
    metadata_attempt_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        if post_count == 1:
            return httpx.Response(200, json={"text": "OK", "model": model_id})
        return httpx.Response(
            200,
            json={"text": "runtime transcript"},
            headers={"X-Generation-Id": "gen-runtime-egress-drift"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    gateway = await _certify_and_activate_audio(
        service,
        connection,
        transport,
        monkeypatch,
        entry_id="multimodal_transcription",
        shape="audio_transcription",
        adapter="openrouter_audio_transcription_json_v1",
        model_id=model_id,
        idempotency_key="metadata-egress-drift-cert",
    )

    async def blocked_metadata_get(
        _client: httpx.AsyncClient,
        _target: ProviderMultimodalTarget,
        _generation_id: str,
        *,
        on_dispatch: object | None = None,
    ) -> str | None:
        nonlocal metadata_attempt_count
        metadata_attempt_count += 1
        assert on_dispatch is not None
        raise ProviderEgressError(
            "provider_address_blocked",
            "synthetic protected-address drift",
        )

    monkeypatch.setattr(
        gateway.call_service.multimodal_transport,
        "fetch_openrouter_generation_model",
        blocked_metadata_get,
    )
    with pytest.raises(MultimodalServiceError) as failed:
        await TranscriptionService(service, managed_gateway=gateway).transcribe(
            model_id=model_id,
            filename="sample.wav",
            content_type="audio/wav",
            content=SYNTHETIC_AUDIO_WAV_BYTES,
            language="auto",
            idempotency_key="metadata-egress-drift-runtime",
        )

    assert failed.value.code == "provider_address_blocked"
    assert failed.value.route_receipt["status"] == "failed"
    assert metadata_attempt_count == 1
    assert [item.method for item in requests].count("POST") == 2
    receipts = ProviderWorkloadControlService(service).receipts(
        entry_id="multimodal_transcription"
    )
    call = receipts.runs[0].calls[0]
    assert call.generation_id_observed is True
    assert call.generation_metadata_get_count == 0
    assert call.generation_metadata_wait_ms is not None
    assert "gen-runtime-egress-drift" not in receipts.model_dump_json()


@pytest.mark.asyncio
async def test_r8c_metadata_authorization_timeout_does_not_count_http_get(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "openai/whisper-1"
    post_count = 0
    authorization_attempt_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        if post_count == 1:
            return httpx.Response(200, json={"text": "OK", "model": model_id})
        return httpx.Response(
            200,
            json={"text": "runtime transcript"},
            headers={"X-Generation-Id": "gen-runtime-authorization-timeout"},
        )

    monkeypatch.setattr(
        multimodal_gateway_module,
        "_OPENROUTER_GENERATION_METADATA_POLL_DELAYS_SECONDS",
        (0.0, 0.0),
    )
    monkeypatch.setattr(
        multimodal_gateway_module,
        "OPENROUTER_GENERATION_METADATA_REQUEST_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        multimodal_gateway_module,
        "_OPENROUTER_GENERATION_METADATA_POLL_TIMEOUT_SECONDS",
        0.05,
    )
    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    gateway = await _certify_and_activate_audio(
        service,
        connection,
        transport,
        monkeypatch,
        entry_id="multimodal_transcription",
        shape="audio_transcription",
        adapter="openrouter_audio_transcription_json_v1",
        model_id=model_id,
        idempotency_key="metadata-authorization-timeout-cert",
    )

    async def stalled_before_dispatch(
        _client: httpx.AsyncClient,
        _target: ProviderMultimodalTarget,
        _generation_id: str,
        *,
        on_dispatch: object | None = None,
    ) -> str | None:
        nonlocal authorization_attempt_count
        authorization_attempt_count += 1
        assert on_dispatch is not None
        await asyncio.Future()

    monkeypatch.setattr(
        gateway.call_service.multimodal_transport,
        "fetch_openrouter_generation_model",
        stalled_before_dispatch,
    )
    with pytest.raises(MultimodalServiceError) as failed:
        await TranscriptionService(service, managed_gateway=gateway).transcribe(
            model_id=model_id,
            filename="sample.wav",
            content_type="audio/wav",
            content=SYNTHETIC_AUDIO_WAV_BYTES,
            language="auto",
            idempotency_key="metadata-authorization-timeout-runtime",
        )

    assert failed.value.code == (
        "provider_multimodal_generation_metadata_wait_exhausted"
    )
    assert authorization_attempt_count == 2
    assert post_count == 2
    receipts = ProviderWorkloadControlService(service).receipts(
        entry_id="multimodal_transcription"
    )
    call = receipts.runs[0].calls[0]
    assert call.generation_id_observed is True
    assert call.generation_metadata_get_count == 0
    assert call.generation_metadata_wait_ms is not None
    assert "gen-runtime-authorization-timeout" not in receipts.model_dump_json()


@pytest.mark.asyncio
async def test_r8c_runtime_generation_metadata_total_deadline_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "openai/whisper-1"
    runtime_phase = False
    requests: list[httpx.Request] = []
    metadata_get_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal metadata_get_count
        requests.append(request)
        if request.url.path == "/v1/generation":
            metadata_get_count += 1
            await asyncio.Future()
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        if not runtime_phase:
            return httpx.Response(200, json={"text": "OK", "model": model_id})
        return httpx.Response(
            200,
            json={"text": "runtime transcript"},
            headers={"X-Generation-Id": "gen-runtime-total-deadline"},
        )

    monkeypatch.setattr(
        multimodal_gateway_module,
        "_OPENROUTER_GENERATION_METADATA_POLL_DELAYS_SECONDS",
        (0.0, 0.0, 0.0, 0.0, 0.0),
    )
    monkeypatch.setattr(
        multimodal_gateway_module,
        "OPENROUTER_GENERATION_METADATA_REQUEST_TIMEOUT_SECONDS",
        0.02,
    )
    monkeypatch.setattr(
        multimodal_gateway_module,
        "_OPENROUTER_GENERATION_METADATA_POLL_TIMEOUT_SECONDS",
        0.05,
    )
    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    gateway = await _certify_and_activate_audio(
        service,
        connection,
        transport,
        monkeypatch,
        entry_id="multimodal_transcription",
        shape="audio_transcription",
        adapter="openrouter_audio_transcription_json_v1",
        model_id=model_id,
        idempotency_key="metadata-total-deadline-cert",
    )
    runtime_phase = True
    requests.clear()
    runtime = TranscriptionService(service, managed_gateway=gateway)
    invoke = dict(
        model_id=model_id,
        filename="sample.wav",
        content_type="audio/wav",
        content=SYNTHETIC_AUDIO_WAV_BYTES,
        language="auto",
        idempotency_key="metadata-total-deadline-runtime",
    )

    with pytest.raises(MultimodalServiceError) as failed:
        await asyncio.wait_for(runtime.transcribe(**invoke), timeout=0.5)
    with pytest.raises(MultimodalServiceError):
        await runtime.transcribe(**invoke)

    assert failed.value.code == (
        "provider_multimodal_generation_metadata_wait_exhausted"
    )
    assert failed.value.route_receipt["status"] == "failed"
    receipts = service.repository.list_workload_receipts(
        "local", entry_id="multimodal_transcription"
    )
    assert receipts["calls"][0]["provider_dispatch_state"] == "confirmed"
    assert receipts["calls"][0]["generation_id_observed"] == 1
    assert 1 <= int(receipts["calls"][0]["generation_metadata_get_count"]) <= 5
    metadata_wait_ms = float(receipts["calls"][0]["generation_metadata_wait_ms"])
    assert metadata_wait_ms >= 0
    assert metadata_wait_ms <= (0.05 * 1000) + 150
    assert 1 <= metadata_get_count <= 5
    assert [item.method for item in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_r8c_runtime_generation_metadata_poll_propagates_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "openai/whisper-1"
    requests: list[httpx.Request] = []
    post_count = 0
    metadata_get_count = 0
    metadata_started = asyncio.Event()
    metadata_cancelled = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal metadata_get_count, post_count
        requests.append(request)
        if request.url.path == "/v1/generation":
            metadata_get_count += 1
            metadata_started.set()
            try:
                await asyncio.Future()
            finally:
                metadata_cancelled.set()
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        if post_count == 1:
            return httpx.Response(200, json={"text": "OK", "model": model_id})
        return httpx.Response(
            200,
            json={"text": "runtime transcript"},
            headers={"X-Generation-Id": "gen-runtime-cancel"},
        )

    monkeypatch.setattr(
        multimodal_gateway_module,
        "_OPENROUTER_GENERATION_METADATA_POLL_DELAYS_SECONDS",
        (0.0, 0.0, 0.0, 0.0, 0.0),
    )
    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    gateway = await _certify_and_activate_audio(
        service,
        connection,
        transport,
        monkeypatch,
        entry_id="multimodal_transcription",
        shape="audio_transcription",
        adapter="openrouter_audio_transcription_json_v1",
        model_id=model_id,
        idempotency_key="metadata-cancel-cert",
    )
    runtime = TranscriptionService(service, managed_gateway=gateway)
    invoke = dict(
        model_id=model_id,
        filename="sample.wav",
        content_type="audio/wav",
        content=SYNTHETIC_AUDIO_WAV_BYTES,
        language="auto",
        idempotency_key="metadata-cancel-runtime",
    )

    task = asyncio.create_task(runtime.transcribe(**invoke))
    await metadata_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(MultimodalServiceError):
        await runtime.transcribe(**invoke)

    receipts = service.repository.list_workload_receipts(
        "local", entry_id="multimodal_transcription"
    )
    assert receipts["runs"][0]["status"] == "uncertain"
    assert receipts["calls"][0]["status"] == "uncertain"
    assert receipts["calls"][0]["provider_dispatch_state"] == "uncertain"
    assert receipts["calls"][0]["error_code"] == (
        "provider_workload_dispatch_uncertain"
    )
    assert receipts["calls"][0]["generation_id_observed"] == 1
    assert receipts["calls"][0]["generation_metadata_get_count"] == 1
    assert float(receipts["calls"][0]["generation_metadata_wait_ms"]) >= 0
    assert "gen-runtime-cancel" not in json.dumps(receipts, sort_keys=True)
    assert metadata_cancelled.is_set()
    assert [item.method for item in requests].count("POST") == 2
    assert [item.url.path for item in requests].count("/v1/generation") == 1


@pytest.mark.asyncio
async def test_r8c_dispatched_cancellation_is_uncertain_and_not_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "openai/whisper-1"
    requests: list[httpx.Request] = []
    post_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        if post_count == 1:
            return httpx.Response(200, json={"text": "OK", "model": model_id})
        raise asyncio.CancelledError

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    gateway = await _certify_and_activate_audio(
        service,
        connection,
        transport,
        monkeypatch,
        entry_id="multimodal_transcription",
        shape="audio_transcription",
        adapter="openrouter_audio_transcription_json_v1",
        model_id=model_id,
        idempotency_key="cancel-certification",
    )
    runtime = TranscriptionService(service, managed_gateway=gateway)
    invoke = dict(
        model_id=model_id,
        filename="sample.wav",
        content_type="audio/wav",
        content=SYNTHETIC_AUDIO_WAV_BYTES,
        language="auto",
        idempotency_key="cancel-runtime",
    )
    with pytest.raises(asyncio.CancelledError):
        await runtime.transcribe(**invoke)

    receipts = service.repository.list_workload_receipts(
        "local", entry_id="multimodal_transcription"
    )
    assert receipts["runs"][0]["status"] == "uncertain"
    assert receipts["calls"][0]["status"] == "uncertain"
    assert receipts["calls"][0]["error_code"] == (
        "provider_workload_dispatch_uncertain"
    )
    with pytest.raises(MultimodalServiceError):
        await runtime.transcribe(**invoke)
    assert [item.method for item in requests].count("POST") == 2


def test_r8c_flat_container_import_layout() -> None:
    server_dir = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(server_dir)
    code = textwrap.dedent(
        """
        import asyncio
        import tempfile
        from model_router.repository import SQLiteRouterRepository
        from model_router.service import ModelRouterService
        from model_router.workload_control import ProviderWorkloadCertificationService
        from multimodal.stt import MultimodalServiceError, TranscriptionService
        from multimodal.tts import SpeechService

        assert ProviderWorkloadCertificationService._r8c_speech_parameters(
            "microsoft/mai-voice-2", openai_compatible=False
        )[0]
        repository = SQLiteRouterRepository(tempfile.mkdtemp(), master_key=b"x" * 32)
        service = ModelRouterService(repository)

        async def check_runtime_imports():
            try:
                await TranscriptionService(service).transcribe(
                    model_id="openai/whisper-1",
                    filename="sample.wav",
                    content_type="audio/wav",
                    content=b"RIFF" + b"0" * 64,
                    language="auto",
                )
            except MultimodalServiceError:
                pass
            try:
                await SpeechService(service).synthesize(
                    model_id="microsoft/mai-voice-2",
                    text="test",
                    voice="en-US-Harper:MAI-Voice-2",
                    response_format="mp3",
                    speed=1.0,
                )
            except MultimodalServiceError:
                pass

        asyncio.run(check_runtime_imports())
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=server_dir,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_r8c_dedicated_and_xpert_feature_flags_are_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "MODEL_CONTROL_TRANSCRIPTION_ENABLED",
        "MODEL_CONTROL_SPEECH_ENABLED",
        "MODEL_CONTROL_XPERT_AUDIO_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODEL_CONTROL_XPERT_AUDIO_ENABLED", "true")
    assert ProviderWorkloadControlService.feature_enabled("xpert_transcription")
    assert ProviderWorkloadControlService.feature_enabled("xpert_speech")
    assert not ProviderWorkloadControlService.feature_enabled(
        "multimodal_transcription"
    )
    assert not ProviderWorkloadControlService.feature_enabled("multimodal_speech")

    monkeypatch.setenv("MODEL_CONTROL_XPERT_AUDIO_ENABLED", "false")
    monkeypatch.setenv("MODEL_CONTROL_TRANSCRIPTION_ENABLED", "true")
    assert ProviderWorkloadControlService.feature_enabled(
        "multimodal_transcription"
    )
    assert not ProviderWorkloadControlService.feature_enabled("multimodal_speech")
    assert not ProviderWorkloadControlService.feature_enabled("xpert_transcription")
    assert not ProviderWorkloadControlService.feature_enabled("xpert_speech")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_code", "expected_status"),
    [
        (401, "provider_workload_http_401", "failed"),
        (429, "provider_workload_http_429", "failed"),
        (503, "provider_workload_http_5xx", "failed"),
        ("timeout", "provider_workload_timeout", "uncertain"),
        ("mismatch", "provider_workload_model_mismatch", "failed"),
        (
            "missing_model",
            "provider_multimodal_generation_id_missing",
            "failed",
        ),
    ],
)
async def test_r8c_runtime_failure_matrix_is_one_post_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: int | str,
    expected_code: str,
    expected_status: str,
) -> None:
    model_id = "openai/whisper-1"
    requests: list[httpx.Request] = []
    post_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        if post_count == 1:
            return httpx.Response(200, json={"text": "OK", "model": model_id})
        if failure == "timeout":
            raise httpx.ReadTimeout("bounded timeout", request=request)
        if failure == "mismatch":
            return httpx.Response(
                200,
                json={"text": "wrong model", "model": "provider/other"},
            )
        if failure == "missing_model":
            return httpx.Response(200, json={"text": "no attestation"})
        return httpx.Response(int(failure))

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    gateway = await _certify_and_activate_audio(
        service,
        connection,
        transport,
        monkeypatch,
        entry_id="multimodal_transcription",
        shape="audio_transcription",
        adapter="openrouter_audio_transcription_json_v1",
        model_id=model_id,
        idempotency_key=f"failure-cert-{failure}",
    )
    with pytest.raises(MultimodalServiceError) as failed:
        await TranscriptionService(service, managed_gateway=gateway).transcribe(
            model_id=model_id,
            filename="sample.wav",
            content_type="audio/wav",
            content=SYNTHETIC_AUDIO_WAV_BYTES,
            language="auto",
            idempotency_key=f"failure-runtime-{failure}",
        )

    assert failed.value.code == expected_code
    assert failed.value.route_receipt["status"] == expected_status
    assert [item.method for item in requests].count("POST") == 2
    if failure == "missing_model":
        receipts = ProviderWorkloadControlService(service).receipts(
            entry_id="multimodal_transcription"
        )
        call = receipts.runs[0].calls[0]
        assert call.generation_id_observed is False
        assert call.generation_metadata_get_count == 0
        assert call.generation_metadata_wait_ms == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["read_timeout", "stream_interrupted"])
async def test_r8c_audio_body_interruption_after_headers_is_uncertain_and_not_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    model_id = "openai/whisper-1"
    requests: list[httpx.Request] = []
    streams: list[_InterruptingAudioStream] = []
    post_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        if post_count == 1:
            return httpx.Response(200, json={"text": "OK", "model": model_id})
        error: Exception = (
            httpx.ReadTimeout("body timed out", request=request)
            if failure_kind == "read_timeout"
            else httpx.ReadError("body interrupted", request=request)
        )
        stream = _InterruptingAudioStream(error)
        streams.append(stream)
        return httpx.Response(
            200,
            headers={"content-type": "application/json", "x-model-id": model_id},
            stream=stream,
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    gateway = await _certify_and_activate_audio(
        service,
        connection,
        transport,
        monkeypatch,
        entry_id="multimodal_transcription",
        shape="audio_transcription",
        adapter="openrouter_audio_transcription_json_v1",
        model_id=model_id,
        idempotency_key=f"body-interruption-cert-{failure_kind}",
    )
    runtime = TranscriptionService(service, managed_gateway=gateway)
    invoke = dict(
        model_id=model_id,
        filename="sample.wav",
        content_type="audio/wav",
        content=SYNTHETIC_AUDIO_WAV_BYTES,
        language="auto",
        idempotency_key=f"body-interruption-runtime-{failure_kind}",
    )

    with pytest.raises(MultimodalServiceError) as interrupted:
        await runtime.transcribe(**invoke)

    assert interrupted.value.route_receipt is not None
    assert interrupted.value.route_receipt["status"] == "uncertain"
    assert interrupted.value.route_receipt["calls"][0]["dispatched"] is True
    receipts = service.repository.list_workload_receipts(
        "local", entry_id="multimodal_transcription"
    )
    assert receipts["runs"][0]["status"] == "uncertain"
    assert receipts["calls"][0]["status"] == "uncertain"
    assert receipts["calls"][0]["provider_dispatch_state"] == "uncertain"
    assert bool(receipts["calls"][0]["dispatched"]) is True
    assert streams and all(stream.closed for stream in streams)

    with pytest.raises(MultimodalServiceError):
        await runtime.transcribe(**invoke)
    assert [item.method for item in requests].count("POST") == 2


@pytest.mark.asyncio
async def test_r8c_audio_oversized_incomplete_body_is_uncertain_and_not_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "openai/whisper-1"
    requests: list[httpx.Request] = []
    streams: list[_OversizedAudioStream] = []
    post_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        if post_count == 1:
            return httpx.Response(200, json={"text": "OK", "model": model_id})
        stream = _OversizedAudioStream()
        streams.append(stream)
        return httpx.Response(
            200,
            headers={"content-type": "application/json", "x-model-id": model_id},
            stream=stream,
        )

    monkeypatch.setattr(multimodal_gateway_module, "_MAX_IMAGE_RESPONSE_BYTES", 16)
    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    gateway = await _certify_and_activate_audio(
        service,
        connection,
        transport,
        monkeypatch,
        entry_id="multimodal_transcription",
        shape="audio_transcription",
        adapter="openrouter_audio_transcription_json_v1",
        model_id=model_id,
        idempotency_key="oversized-body-cert",
    )
    runtime = TranscriptionService(service, managed_gateway=gateway)
    invoke = dict(
        model_id=model_id,
        filename="sample.wav",
        content_type="audio/wav",
        content=SYNTHETIC_AUDIO_WAV_BYTES,
        language="auto",
        idempotency_key="oversized-body-runtime",
    )

    with pytest.raises(MultimodalServiceError) as oversized:
        await runtime.transcribe(**invoke)

    assert oversized.value.code == "provider_multimodal_response_too_large"
    assert oversized.value.route_receipt["status"] == "uncertain"
    receipts = service.repository.list_workload_receipts(
        "local", entry_id="multimodal_transcription"
    )
    assert receipts["calls"][0]["status"] == "uncertain"
    assert receipts["calls"][0]["provider_dispatch_state"] == "uncertain"
    assert streams and all(stream.closed for stream in streams)

    with pytest.raises(MultimodalServiceError):
        await runtime.transcribe(**invoke)
    assert [item.method for item in requests].count("POST") == 2


@pytest.mark.asyncio
async def test_r8c_audio_certification_oversized_body_is_uncertain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "openai/whisper-1"
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"text":"' + b"x" * 32,
        )

    monkeypatch.setattr(
        workload_control_module, "MAX_WORKLOAD_UNARY_RESPONSE_BYTES", 16
    )
    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    result = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="audio_transcription",
            model_id=model_id,
            adapter_contract="openrouter_audio_transcription_json_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="oversized-certification-body",
    )

    assert result.status == "uncertain"
    assert result.error_code == "provider_workload_response_too_large"
    assert post_count == 1
    session = service.repository.list_multimodal_certification_sessions("local")[0]
    assert session["status"] == "uncertain"
    assert session["provider_dispatch_state"] == "uncertain"
    assert session["post_dispatched"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_kind", ["read_timeout", "total_timeout", "cancelled"]
)
async def test_r8c_certification_dispatched_failure_persists_uncertain_session(
    tmp_path: Path,
    failure_kind: str,
) -> None:
    model_id = "openai/whisper-1"
    post_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        if failure_kind == "read_timeout":
            raise httpx.ReadTimeout("certification read timeout", request=request)
        if failure_kind == "total_timeout":
            raise TimeoutError("certification total timeout")
        raise asyncio.CancelledError

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    payload = ProviderWorkloadCertificationRequest(
        execution_shape="audio_transcription",
        model_id=model_id,
        adapter_contract="openrouter_audio_transcription_json_v1",
        acknowledge_billed_call=True,
    )
    idempotency_key = f"dispatched-certification-{failure_kind}"
    certification_service = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )

    if failure_kind == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await certification_service.run(
                connection.id,
                payload,
                idempotency_key=idempotency_key,
            )
    else:
        result = await certification_service.run(
            connection.id,
            payload,
            idempotency_key=idempotency_key,
        )
        assert result.status == "uncertain"

    sessions = service.repository.list_multimodal_certification_sessions(
        "local"
    )
    assert len(sessions) == 1
    assert sessions[0]["status"] == "uncertain"
    assert sessions[0]["provider_dispatch_state"] == "uncertain"
    assert bool(sessions[0]["post_dispatched"]) is True

    restarted_repository = SQLiteRouterRepository(
        tmp_path, master_key=b"x" * 32
    )
    restarted_service = ModelRouterService(
        restarted_repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    replayed = await ProviderWorkloadCertificationService(
        restarted_service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        payload,
        idempotency_key=idempotency_key,
    )
    assert replayed.status == "uncertain"
    assert post_count == 1


@pytest.mark.asyncio
async def test_r8c_certification_cancelled_before_dispatch_fails_consistently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "openai/whisper-1"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        return httpx.Response(200, json={"data": [{"id": model_id}]})

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    certification_service = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )

    async def cancel_after_session_claim(*_args, **_kwargs) -> None:
        sessions = service.repository.list_multimodal_certification_sessions(
            "local"
        )
        assert len(sessions) == 1
        assert sessions[0]["status"] == "running"
        assert sessions[0]["provider_dispatch_state"] == "not_dispatched"
        assert bool(sessions[0]["post_dispatched"]) is False
        raise asyncio.CancelledError

    monkeypatch.setattr(
        certification_service,
        "_run_r8c_audio_certification",
        cancel_after_session_claim,
    )
    with pytest.raises(asyncio.CancelledError):
        await certification_service.run(
            connection.id,
            ProviderWorkloadCertificationRequest(
                execution_shape="audio_transcription",
                model_id=model_id,
                adapter_contract="openrouter_audio_transcription_json_v1",
                acknowledge_billed_call=True,
            ),
            idempotency_key="cancel-before-dispatch",
        )

    certifications = service.repository.list_workload_certifications(
        "local", connection_id=connection.id
    )
    sessions = service.repository.list_multimodal_certification_sessions("local")
    assert len(certifications) == 1
    assert certifications[0]["status"] == "failed"
    assert certifications[0]["error_code"] == "provider_workload_cancelled"
    assert len(sessions) == 1
    assert sessions[0]["status"] == "failed"
    assert sessions[0]["provider_dispatch_state"] == "not_dispatched"
    assert bool(sessions[0]["post_dispatched"]) is False
    assert [item.method for item in requests].count("POST") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shape", "adapter", "model_id"),
    [
        (
            "audio_transcription",
            "openrouter_audio_transcription_json_v1",
            "openai/whisper-1",
        ),
        (
            "audio_speech",
            "openrouter_audio_speech_v1",
            "google/gemini-3.1-flash-tts-preview",
        ),
    ],
)
async def test_r8c_certification_profile_records_exact_audio_parameter_contract(
    tmp_path: Path,
    shape: str,
    adapter: str,
    model_id: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        if shape == "audio_transcription":
            return httpx.Response(200, json={"text": "OK", "model": model_id})
        return httpx.Response(
            200,
            content=b"\x00\x01" * 128,
            headers={
                "content-type": "audio/pcm;rate=24000;channels=1",
                "x-model-id": model_id,
            },
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    certification = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape=shape,  # type: ignore[arg-type]
            model_id=model_id,
            adapter_contract=adapter,  # type: ignore[arg-type]
            acknowledge_billed_call=True,
        ),
        idempotency_key=f"parameter-profile-{shape}",
    )

    row = service.repository.get_workload_certification(
        "local", certification.certification_id
    )
    assert row is not None
    profile = json.loads(str(row["profile_json"]))
    assert profile["audio_parameter_contract_version"] == (
        "modelmirror-provider-audio-parameters-v1"
    )
    assert profile["audio_parameter_contract_version"] != profile["protocol_version"]
    if shape == "audio_transcription":
        assert profile["certified_input_formats"] == ["wav"]
    else:
        assert profile["certified_voice"] == "Aoede"
        assert profile["certified_response_format"] == "wav"
        assert profile["certified_upstream_format"] == "pcm"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shape", "entry_id", "adapter", "model_id"),
    [
        (
            "audio_transcription",
            "multimodal_transcription",
            "openrouter_audio_transcription_json_v1",
            "openai/whisper-1",
        ),
        (
            "audio_speech",
            "multimodal_speech",
            "openrouter_audio_speech_v1",
            "google/gemini-3.1-flash-tts-preview",
        ),
    ],
)
async def test_r8c_passed_certification_missing_parameter_contract_is_stale(
    tmp_path: Path,
    shape: str,
    entry_id: str,
    adapter: str,
    model_id: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        if shape == "audio_transcription":
            return httpx.Response(200, json={"text": "OK", "model": model_id})
        return httpx.Response(
            200,
            content=b"\x00\x01" * 128,
            headers={
                "content-type": "audio/pcm;rate=24000;channels=1",
                "x-model-id": model_id,
            },
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    certification_service = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    certification = await certification_service.run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape=shape,  # type: ignore[arg-type]
            model_id=model_id,
            adapter_contract=adapter,  # type: ignore[arg-type]
            acknowledge_billed_call=True,
        ),
        idempotency_key=f"legacy-parameter-profile-{shape}",
    )
    with service.repository._lock, service.repository._connect() as database:  # noqa: SLF001
        database.execute(
            """
            UPDATE provider_workload_certifications
            SET profile_json = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (
                json.dumps(
                    {
                        "execution_shape": shape,
                        "model_id": model_id,
                        "adapter_contract": adapter,
                        "protocol_version": "modelmirror-provider-multimodal-v1",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "local",
                certification.certification_id,
            ),
        )

    summary = certification_service.list().certifications[0]
    assert summary.status == "stale"
    assert summary.can_run is False
    assert summary.blocked_reason == (
        "provider_multimodal_audio_parameter_contract_stale"
    )
    with pytest.raises(RouterServiceError) as blocked:
        ProviderWorkloadControlService(service).update_policy(
            entry_id,  # type: ignore[arg-type]
            ProviderWorkloadPolicyUpdate(
                expected_revision=0,
                bindings=[
                    ProviderWorkloadBindingUpdate(
                        execution_shape=shape,  # type: ignore[arg-type]
                        model_id=model_id,
                        connection_id=connection.id,
                        adapter_contract=adapter,  # type: ignore[arg-type]
                    )
                ],
            ),
        )
    assert blocked.value.code == (
        "provider_multimodal_audio_parameter_contract_stale"
    )


@pytest.mark.asyncio
async def test_r8c_runtime_blocks_uncertified_stt_format_before_provider_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "openai/whisper-1"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(200, json={"text": "OK", "model": model_id})

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    gateway = await _certify_and_activate_audio(
        service,
        connection,
        transport,
        monkeypatch,
        entry_id="multimodal_transcription",
        shape="audio_transcription",
        adapter="openrouter_audio_transcription_json_v1",
        model_id=model_id,
        idempotency_key="format-boundary-certification",
    )

    with pytest.raises(MultimodalServiceError) as blocked:
        await TranscriptionService(service, managed_gateway=gateway).transcribe(
            model_id=model_id,
            filename="sample.mp3",
            content_type="audio/mpeg",
            content=MP3_BYTES,
            language="auto",
            idempotency_key="format-boundary-runtime",
        )
    assert blocked.value.route_receipt is not None
    assert blocked.value.route_receipt["call_count"] == 0
    assert [item.method for item in requests].count("POST") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("voice", "response_format"),
    [("Not-Certified-Voice", "wav"), ("Aoede", "mp3")],
)
async def test_r8c_runtime_blocks_uncertified_tts_parameters_before_provider_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    voice: str,
    response_format: str,
) -> None:
    model_id = "google/gemini-3.1-flash-tts-preview"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        body = json.loads(request.content)
        if body["response_format"] == "pcm":
            return httpx.Response(
                200,
                content=b"\x00\x01" * 128,
                headers={
                    "content-type": "audio/pcm;rate=24000;channels=1",
                    "x-model-id": model_id,
                },
            )
        return httpx.Response(
            200,
            content=MP3_BYTES,
            headers={"content-type": "audio/mpeg", "x-model-id": model_id},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    gateway = await _certify_and_activate_audio(
        service,
        connection,
        transport,
        monkeypatch,
        entry_id="multimodal_speech",
        shape="audio_speech",
        adapter="openrouter_audio_speech_v1",
        model_id=model_id,
        idempotency_key=f"tts-parameter-certification-{voice}-{response_format}",
    )

    with pytest.raises(MultimodalServiceError) as blocked:
        await SpeechService(service, managed_gateway=gateway).synthesize(
            model_id=model_id,
            text="parameter boundary",
            voice=voice,
            response_format=response_format,
            speed=1.0,
            idempotency_key=f"tts-parameter-runtime-{voice}-{response_format}",
        )
    assert blocked.value.route_receipt is not None
    assert blocked.value.route_receipt["call_count"] == 0
    assert [item.method for item in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_r8c_gemini_pcm_upstream_is_certified_and_exposed_as_wav(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "google/gemini-3.1-flash-tts-preview"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        body = json.loads(request.content)
        assert body["voice"] == "Aoede"
        assert body["response_format"] == "pcm"
        return httpx.Response(
            200,
            content=b"\x00\x01" * 128,
            headers={
                "content-type": "audio/pcm;rate=24000;channels=1",
                "x-model-id": model_id,
            },
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    gateway = await _certify_and_activate_audio(
        service,
        connection,
        transport,
        monkeypatch,
        entry_id="multimodal_speech",
        shape="audio_speech",
        adapter="openrouter_audio_speech_v1",
        model_id=model_id,
        idempotency_key="gemini-pcm-wav-certification",
    )
    certification = service.repository.list_workload_certifications(
        "local", connection_id=connection.id
    )[0]
    profile = json.loads(str(certification["profile_json"]))
    assert profile["certified_response_format"] == "wav"
    assert profile["certified_upstream_format"] == "pcm"

    result = await SpeechService(service, managed_gateway=gateway).synthesize(
        model_id=model_id,
        text="pcm to wav",
        voice="Aoede",
        response_format="wav",
        speed=1.0,
        idempotency_key="gemini-pcm-wav-runtime",
    )
    assert result.response_format == "wav"
    assert result.content[:4] == b"RIFF"
    assert result.content[8:12] == b"WAVE"
    assert [item.method for item in requests].count("POST") == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["transcription", "speech"])
async def test_r8c_dedicated_managed_provider_only_model_reaches_preflight(
    tmp_path: Path,
    operation: str,
) -> None:
    service = ModelRouterService(
        SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    )
    gateway = _ManagedPreflightProbe("managed_required")
    provider_only_model = "provider/private-audio-model-v1"

    if operation == "transcription":
        with pytest.raises(MultimodalServiceError) as blocked:
            await TranscriptionService(
                service, managed_gateway=gateway  # type: ignore[arg-type]
            ).transcribe(
                model_id=provider_only_model,
                filename="sample.wav",
                content_type="audio/wav",
                content=SYNTHETIC_AUDIO_WAV_BYTES,
                language="auto",
                idempotency_key="provider-only-stt",
            )
        expected_shape = "audio_transcription"
    else:
        with pytest.raises(MultimodalServiceError) as blocked:
            await SpeechService(
                service, managed_gateway=gateway  # type: ignore[arg-type]
            ).synthesize(
                model_id=provider_only_model,
                text="provider only model",
                voice="ProviderVoice",
                response_format="mp3",
                speed=1.0,
                idempotency_key="provider-only-tts",
            )
        expected_shape = "audio_speech"

    assert blocked.value.code == "provider_workload_binding_missing"
    assert blocked.value.route_receipt is not None
    assert blocked.value.route_receipt["call_count"] == 0
    assert gateway.exact_calls == [
        (
            "multimodal_transcription"
            if operation == "transcription"
            else "multimodal_speech",
            expected_shape,
            provider_only_model,
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["transcription", "speech"])
async def test_r8c_legacy_provider_only_model_keeps_static_allowlist(
    tmp_path: Path,
    operation: str,
) -> None:
    service = ModelRouterService(
        SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    )
    gateway = _ManagedPreflightProbe("legacy")
    provider_only_model = "provider/private-audio-model-v1"

    if operation == "transcription":
        with pytest.raises(MultimodalServiceError) as blocked:
            await TranscriptionService(
                service, managed_gateway=gateway  # type: ignore[arg-type]
            ).transcribe(
                model_id=provider_only_model,
                filename="sample.wav",
                content_type="audio/wav",
                content=SYNTHETIC_AUDIO_WAV_BYTES,
                language="auto",
            )
        assert blocked.value.code == "unsupported_transcription_model"
    else:
        with pytest.raises(MultimodalServiceError) as blocked:
            await SpeechService(
                service, managed_gateway=gateway  # type: ignore[arg-type]
            ).synthesize(
                model_id=provider_only_model,
                text="legacy remains allowlisted",
                voice="ProviderVoice",
                response_format="mp3",
                speed=1.0,
            )
        assert blocked.value.code == "unsupported_speech_model"
    assert gateway.exact_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shape", "entry_id", "adapter", "model_id"),
    [
        (
            "audio_transcription",
            "multimodal_transcription",
            "openrouter_audio_transcription_json_v1",
            "openai/whisper-1",
        ),
        (
            "audio_speech",
            "multimodal_speech",
            "openrouter_audio_speech_v1",
            "google/gemini-3.1-flash-tts-preview",
        ),
    ],
)
async def test_r8c_public_status_and_certification_summary_expose_safe_parameters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shape: str,
    entry_id: str,
    adapter: str,
    model_id: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        if shape == "audio_transcription":
            return httpx.Response(200, json={"text": "OK", "model": model_id})
        return httpx.Response(
            200,
            content=b"\x00\x01" * 128,
            headers={
                "content-type": "audio/pcm;rate=24000;channels=1",
                "x-model-id": model_id,
            },
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    await _certify_and_activate_audio(
        service,
        connection,
        transport,
        monkeypatch,
        entry_id=entry_id,
        shape=shape,
        adapter=adapter,
        model_id=model_id,
        idempotency_key=f"safe-public-parameters-{shape}",
    )
    certification = ProviderWorkloadCertificationService(service).list().certifications[0]
    public_status = ProviderWorkloadControlService(service).public_status(
        entry_id,  # type: ignore[arg-type]
        model_id,
        shape,  # type: ignore[arg-type]
    )

    assert public_status.available is True
    assert certification.status == "passed"
    assert certification.can_run is True
    if shape == "audio_transcription":
        assert public_status.certified_input_formats == ["wav"]
        assert certification.certified_input_formats == ["wav"]
        assert public_status.certified_voice is None
        assert certification.certified_voice is None
    else:
        assert public_status.certified_voice == "Aoede"
        assert public_status.certified_response_format == "wav"
        assert certification.certified_voice == "Aoede"
        assert certification.certified_response_format == "wav"
        assert public_status.certified_input_formats == []
        assert certification.certified_input_formats == []

    public_payload = public_status.model_dump(mode="json")
    certification_payload = certification.model_dump(mode="json")
    public_text = json.dumps(public_payload, sort_keys=True)
    certification_text = json.dumps(certification_payload, sort_keys=True)
    for payload in (public_payload, certification_payload):
        assert "certified_upstream_format" not in payload
        assert "base_url" not in payload
        assert "api_key" not in payload
    assert connection.id not in public_text
    assert "https://provider.example/v1" not in public_text
    assert "https://provider.example/v1" not in certification_text
    assert "r8c-secret" not in public_text
    assert "r8c-secret" not in certification_text


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_state", ["stale", "inactive"])
async def test_r8c_public_status_is_unavailable_when_stale_or_inactive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_state: str,
) -> None:
    model_id = "openai/whisper-1"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(200, json={"text": "OK", "model": model_id})

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    await _certify_and_activate_audio(
        service,
        connection,
        transport,
        monkeypatch,
        entry_id="multimodal_transcription",
        shape="audio_transcription",
        adapter="openrouter_audio_transcription_json_v1",
        model_id=model_id,
        idempotency_key=f"public-unavailable-{terminal_state}",
    )
    control = ProviderWorkloadControlService(service)
    if terminal_state == "stale":
        await service.update_connection(
            connection.id,
            RouterConnectionUpdate(api_key="rotated-secret"),
        )
    else:
        active = control.get_policy("multimodal_transcription")
        control.deactivate(
            "multimodal_transcription",
            ProviderWorkloadDeactivationRequest(
                expected_revision=active.revision,
            ),
        )

    public_status = control.public_status(
        "multimodal_transcription",
        model_id,
        "audio_transcription",
    )
    assert public_status.available is False
    assert public_status.blocks_before_dispatch is True
    assert public_status.reason_code != "provider_workload_available"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shape", "entry_id", "model_id", "adapter_a", "adapter_b"),
    [
        (
            "audio_transcription",
            "multimodal_transcription",
            "openai/whisper-1",
            "openrouter_audio_transcription_json_v1",
            "openai_compatible_audio_transcription_multipart_v1",
        ),
        (
            "audio_speech",
            "multimodal_speech",
            "microsoft/mai-voice-2",
            "openrouter_audio_speech_v1",
            "openai_compatible_audio_speech_v1",
        ),
    ],
)
async def test_r8c_runtime_binding_swap_after_parameter_read_blocks_before_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shape: str,
    entry_id: str,
    model_id: str,
    adapter_a: str,
    adapter_b: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        if shape == "audio_transcription":
            return httpx.Response(200, json={"text": "OK", "model": model_id})
        return httpx.Response(
            200,
            content=MP3_BYTES,
            headers={"content-type": "audio/mpeg", "x-model-id": model_id},
        )

    transport = httpx.MockTransport(handler)
    service, connection_a = _service(tmp_path, transport, kind="openrouter")
    connection_b = service.repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="R8C replacement audio",
            kind="newapi",
            base_url="https://replacement.example/v1",
            api_key="replacement-secret",
            scopes=["audio"],
        ),
    )
    certifications = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    certification_a = await certifications.run(
        connection_a.id,
        ProviderWorkloadCertificationRequest(
            execution_shape=shape,  # type: ignore[arg-type]
            model_id=model_id,
            adapter_contract=adapter_a,  # type: ignore[arg-type]
            acknowledge_billed_call=True,
        ),
        idempotency_key=f"binding-race-a-{shape}",
    )
    certification_b = await certifications.run(
        connection_b.id,
        ProviderWorkloadCertificationRequest(
            execution_shape=shape,  # type: ignore[arg-type]
            model_id=model_id,
            adapter_contract=adapter_b,  # type: ignore[arg-type]
            acknowledge_billed_call=True,
        ),
        idempotency_key=f"binding-race-b-{shape}",
    )
    assert certification_a.status == certification_b.status == "passed"

    monkeypatch.setenv(
        "MODEL_CONTROL_TRANSCRIPTION_ENABLED"
        if shape == "audio_transcription"
        else "MODEL_CONTROL_SPEECH_ENABLED",
        "true",
    )
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        entry_id,  # type: ignore[arg-type]
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape=shape,  # type: ignore[arg-type]
                    model_id=model_id,
                    connection_id=connection_a.id,
                    adapter_contract=adapter_a,  # type: ignore[arg-type]
                )
            ],
        ),
    )
    control.activate(
        entry_id,  # type: ignore[arg-type]
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    gateway = ManagedMultimodalGateway.for_router(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    original_start_run = gateway.start_run
    switched = False

    def switch_binding_then_start(*args, **kwargs):
        nonlocal switched
        if not switched:
            switched = True
            current = control.get_policy(entry_id)  # type: ignore[arg-type]
            inactive = control.deactivate(
                entry_id,  # type: ignore[arg-type]
                ProviderWorkloadDeactivationRequest(
                    expected_revision=current.revision
                ),
            )
            replacement = control.update_policy(
                entry_id,  # type: ignore[arg-type]
                ProviderWorkloadPolicyUpdate(
                    expected_revision=inactive.revision,
                    bindings=[
                        ProviderWorkloadBindingUpdate(
                            execution_shape=shape,  # type: ignore[arg-type]
                            model_id=model_id,
                            connection_id=connection_b.id,
                            adapter_contract=adapter_b,  # type: ignore[arg-type]
                        )
                    ],
                ),
            )
            control.activate(
                entry_id,  # type: ignore[arg-type]
                ProviderWorkloadActivationRequest(
                    expected_revision=replacement.revision,
                    no_open_p0_p1=True,
                    acknowledge_fail_closed=True,
                ),
            )
        return original_start_run(*args, **kwargs)

    monkeypatch.setattr(gateway, "start_run", switch_binding_then_start)
    posts_before_runtime = sum(
        request.method == "POST" for request in requests
    )
    if shape == "audio_transcription":
        with pytest.raises(MultimodalServiceError) as blocked:
            await TranscriptionService(
                service, managed_gateway=gateway
            ).transcribe(
                model_id=model_id,
                filename="race.wav",
                content_type="audio/wav",
                content=SYNTHETIC_AUDIO_WAV_BYTES,
                language="auto",
                idempotency_key=f"binding-race-runtime-{shape}",
            )
    else:
        with pytest.raises(MultimodalServiceError) as blocked:
            await SpeechService(service, managed_gateway=gateway).synthesize(
                model_id=model_id,
                text="binding race",
                voice="en-US-Harper:MAI-Voice-2",
                response_format="mp3",
                speed=1.0,
                idempotency_key=f"binding-race-runtime-{shape}",
            )

    assert blocked.value.code == "provider_workload_binding_changed"
    assert blocked.value.route_receipt["call_count"] == 0
    assert len(blocked.value.route_receipt["calls"]) == 1
    assert blocked.value.route_receipt["calls"][0]["dispatched"] is False
    assert sum(request.method == "POST" for request in requests) == (
        posts_before_runtime
    )


@pytest.mark.asyncio
async def test_r8c_cross_connection_idempotency_conflict_creates_no_orphan(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    model_id = "openai/whisper-1"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(200, json={"text": "OK", "model": model_id})

    transport = httpx.MockTransport(handler)
    service, connection_a = _service(tmp_path, transport, kind="openrouter")
    connection_b = service.repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="R8C second connection",
            kind="openrouter",
            base_url="https://second.example/v1",
            api_key="second-secret",
            scopes=["audio"],
        ),
    )
    certifications = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    payload = ProviderWorkloadCertificationRequest(
        execution_shape="audio_transcription",
        model_id=model_id,
        adapter_contract="openrouter_audio_transcription_json_v1",
        acknowledge_billed_call=True,
    )
    first = await certifications.run(
        connection_a.id,
        payload,
        idempotency_key="cross-connection-idempotency",
    )
    assert first.status == "passed"
    posts_before_conflict = sum(
        request.method == "POST" for request in requests
    )

    with pytest.raises(RouterServiceError) as conflict:
        await certifications.run(
            connection_b.id,
            payload,
            idempotency_key="cross-connection-idempotency",
        )

    assert conflict.value.code == (
        "provider_multimodal_session_idempotency_conflict"
    )
    assert conflict.value.status_code == 409
    assert sum(request.method == "POST" for request in requests) == (
        posts_before_conflict
    )
    connection_b_certifications = (
        service.repository.list_workload_certifications(
            "local", connection_id=connection_b.id
        )
    )
    assert connection_b_certifications == []
    sessions = service.repository.list_multimodal_certification_sessions(
        "local"
    )
    assert len(sessions) == 1
    assert sessions[0]["connection_id"] == connection_a.id


@pytest.mark.asyncio
async def test_r8c_certification_connection_change_before_dispatch_blocks_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    model_id = "openai/whisper-1"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(200, json={"text": "OK", "model": model_id})

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    original_update = service.repository.update_multimodal_certification_session
    changed = False

    def change_connection_then_update(*args, **kwargs):
        nonlocal changed
        if kwargs.get("provider_dispatch_state") == "dispatched" and not changed:
            changed = True
            service.repository.update_connection(
                "local",
                connection.id,
                RouterConnectionUpdate(
                    base_url="https://changed.example/v1",
                    api_key="changed-secret",
                ),
            )
        return original_update(*args, **kwargs)

    monkeypatch.setattr(
        service.repository,
        "update_multimodal_certification_session",
        change_connection_then_update,
    )
    result = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="audio_transcription",
            model_id=model_id,
            adapter_contract="openrouter_audio_transcription_json_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="certification-connection-change",
    )

    assert changed is True
    assert result.status == "stale"
    assert result.error_code == "provider_multimodal_dispatch_preconditions_changed"
    assert sum(request.method == "POST" for request in requests) == 0
    certification = service.repository.list_workload_certifications(
        "local", connection_id=connection.id
    )[0]
    assert certification["status"] == "failed"
    assert certification["error_code"] == (
        "provider_multimodal_dispatch_preconditions_changed"
    )
    session = service.repository.list_multimodal_certification_sessions("local")[0]
    assert session["provider_dispatch_state"] == "not_dispatched"
    assert session["post_dispatched"] == 0


@pytest.mark.asyncio
async def test_r8c_runtime_connection_change_after_prepare_blocks_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    model_id = "openai/whisper-1"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(200, json={"text": "OK", "model": model_id})

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport, kind="openrouter")
    gateway = await _certify_and_activate_audio(
        service,
        connection,
        transport,
        monkeypatch,
        entry_id="multimodal_transcription",
        shape="audio_transcription",
        adapter="openrouter_audio_transcription_json_v1",
        model_id=model_id,
        idempotency_key="runtime-connection-change-certification",
    )
    original_mark = service.repository.mark_workload_call_dispatched
    changed = False

    def change_connection_then_mark(*args, **kwargs):
        nonlocal changed
        if not changed:
            changed = True
            service.repository.update_connection(
                "local",
                connection.id,
                RouterConnectionUpdate(api_key="rotated-secret"),
            )
        return original_mark(*args, **kwargs)

    monkeypatch.setattr(
        service.repository,
        "mark_workload_call_dispatched",
        change_connection_then_mark,
    )
    posts_before_runtime = sum(
        request.method == "POST" for request in requests
    )
    with pytest.raises(MultimodalServiceError) as blocked:
        await TranscriptionService(service, managed_gateway=gateway).transcribe(
            model_id=model_id,
            filename="connection-change.wav",
            content_type="audio/wav",
            content=SYNTHETIC_AUDIO_WAV_BYTES,
            language="auto",
            idempotency_key="runtime-connection-change",
        )

    assert changed is True
    assert blocked.value.code == "provider_workload_dispatch_preconditions_changed"
    assert blocked.value.route_receipt["call_count"] == 0
    assert blocked.value.route_receipt["calls"][0]["dispatched"] is False
    assert sum(request.method == "POST" for request in requests) == (
        posts_before_runtime
    )
