import asyncio
import base64
import io
import json
import wave

import httpx
import pytest

from server.model_router.egress import ProviderEgressPolicy
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import RouterConnectionCreate, RouterConnectionUpdate
from server.model_router.service import ModelRouterService, RouterServiceError
from server.model_router.voice_library import VoiceControl, cloud_base
from server.multimodal.voice_cloud import VoiceCloud, validate_sample, normalize_cloud_wav
from server.multimodal.voice_library import VoiceLibrary

MODEL = "qwen-audio-3.1-tts-plus"


def wav(seconds=6, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(b"\0\0" * rate * seconds)
    return buf.getvalue()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_CONTROL_VOICE_LIBRARY_ENABLED", "true")
    repo = SQLiteRouterRepository.open(tmp_path)
    connection = repo.create_connection("local", RouterConnectionCreate(name="Synthetic cloud", kind="openai_compatible", base_url="https://dashscope.aliyuncs.com/api/v1", api_key="synthetic-test-only", scopes=["audio"]))
    router = ModelRouterService(repo, egress_policy=ProviderEgressPolicy(resolver=lambda *_: ["8.8.8.8"]))
    control = VoiceControl(router)
    control.configure(dict(operationId="configure", expectedRevision=0, enabled=True, connectionId=connection.id, modelIds=[MODEL], limit=10))
    captured = []
    def respond(request):
        captured.append(request)
        if request.method == "GET":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=wav(1))
        body = json.loads(request.content)
        if body["model"] == MODEL:
            return httpx.Response(200, json={"output": {"audio": {"url": "https://output.oss-cn-beijing.aliyuncs.com/audio.wav?signature=synthetic"}}})
        action = body["input"]["action"]
        return httpx.Response(200, json={"output": {"voice_id": "synthetic-voice"} if action == "create_voice" else {"status": "OK", "voice_id": "synthetic-voice", "target_model": MODEL} if action == "query_voice" else {}})
    cloud = VoiceCloud(router.egress_policy, lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    yield VoiceLibrary(router, cloud), captured, connection
    repo.close()


def create(service, key="create"):
    return asyncio.run(service.create(operation_id=key, model=MODEL, name="故事旁白", audio=base64.b64encode(wav()).decode(), consent=True))


def test_lifecycle_fixed_id_replay_delete_and_no_sample_storage(setup):
    service, requests, _ = setup
    result = create(service)
    identifier = result["asset"]["id"]
    assert result["asset"]["state"] == "processing"
    assert "externalId" not in result["asset"]
    assert create(service)["asset"]["id"] == identifier
    assert len(requests) == 1
    assert asyncio.run(service.change(identifier, "query", "query"))["asset"]["state"] == "ready"
    for key in ("speak1", "speak2"):
        result = asyncio.run(service.synthesize(asset_id=identifier, model=MODEL, text="今天阳光很好。", response_format="wav", speed=1, operation_id=key))
        assert result.content == wav(1)
    bodies = [json.loads(r.content) for r in requests if r.method == "POST"]
    assert [b["input"]["voice"] for b in bodies if b["model"] == MODEL] == ["synthetic-voice"] * 2
    assert service.control.state()["spent"] == 4
    assert service.control.rename(identifier, "新名称")["name"] == "新名称"
    assert asyncio.run(service.change(identifier, "delete", "delete"))["asset"]["state"] == "deleted"
    assert service.control.assets()[0]["state"] == "deleted"
    with pytest.raises(RouterServiceError, match="此音色尚未"):
        asyncio.run(service.synthesize(asset_id=identifier, model=MODEL, text="测试", response_format="wav", speed=1, operation_id="after-delete"))
    with service.control.transaction() as db:
        values = db.execute("SELECT value FROM router_metadata WHERE key LIKE 'voice-library:%'").fetchall()
        assert all("data:audio" not in r["value"] and "synthetic-test-only" not in r["value"] for r in values)


def test_disabled_gate_dispatches_nothing(setup, monkeypatch):
    service, requests, _ = setup
    monkeypatch.delenv("MODEL_CONTROL_VOICE_LIBRARY_ENABLED")
    assert service.control.catalog()["available"] is False
    with pytest.raises(RouterServiceError):
        create(service)
    assert requests == []


def test_atomic_last_budget_and_explicit_unknown_acknowledgement(setup):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timedelta, timezone
    service, _, _ = setup
    c = service.control
    state = c.state()
    c.configure(dict(operationId="last", expectedRevision=state["revision"], enabled=True,
        connectionId=state["connectionId"], modelIds=[MODEL], limit=1))
    state, _, _ = c.snapshot(MODEL)
    def claim(i):
        asset = dict(id="asset"+str(i), model=MODEL, name="测试", state="unknown")
        try:
            return c.claim("job"+str(i), str(i), state, "create", asset)[1]
        except RouterServiceError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, [1, 2])) == [False, True]
    assert c.state()["spent"] == 1
    with c.transaction() as db:
        row = db.execute("SELECT value FROM router_metadata WHERE key LIKE ?", (c.prefix+"operation:%",)).fetchone()
        job = json.loads(row["value"])
    with pytest.raises(RouterServiceError) as error:
        c.acknowledge(job["id"])
    assert error.value.code == "voice_reconcile_wait"
    with c.transaction() as db:
        job["created"] = (datetime.now(timezone.utc)-timedelta(minutes=6)).isoformat()
        c.write(db, "operation:"+job["id"], job)
    acknowledged = c.acknowledge(job["id"])
    assert acknowledged["state"] == "unknown" and acknowledged["acknowledgedAt"]
    assert c.state()["spent"] == 1
    assert c.claim(job["id"], job["hash"], state, "create", {"id":job["assetId"]})[1] is False


def test_cloud_completion_preserves_local_rename(setup):
    service, _, _ = setup
    created = create(service)
    asset = service.control.asset(created["asset"]["id"])
    service.control.rename(asset["id"], "本地新名称")
    service.control.finish(created["operation"], asset={**asset,"state":"ready"})
    assert service.control.asset(asset["id"])["name"] == "本地新名称"


def test_timeout_survives_restart_and_same_id_never_reuploads(setup):
    service, requests, _ = setup
    async def fail(*args, **kwargs):
        raise httpx.ReadTimeout("private upstream details must not escape")
    service.cloud.enroll = fail
    with pytest.raises(RouterServiceError) as error:
        create(service)
    assert error.value.code == "voice_outcome_unknown"
    restored = VoiceLibrary(service.control.router, service.cloud)
    assert create(restored)["operation"]["state"] == "unknown"
    assert restored.control.state()["spent"] == 1


def test_changed_payload_budget_model_and_tenant_are_guarded(setup):
    service, requests, _ = setup
    create(service)
    with pytest.raises(RouterServiceError) as error:
        asyncio.run(service.create(operation_id="create", model=MODEL, name="不同名称", audio=base64.b64encode(wav()).decode(), consent=True))
    assert error.value.code == "voice_operation_conflict"
    state = service.control.state()
    service.control.configure(dict(operationId="limit", expectedRevision=state["revision"], enabled=True, connectionId=state["connectionId"], modelIds=[MODEL], limit=1))
    with pytest.raises(RouterServiceError):
        create(service, "another")
    other = VoiceControl(ModelRouterService(service.control.repo, tenant_id="other"))
    assert other.assets() == []
    assert len(requests) == 1


def test_stale_credentials_cannot_rebind_old_voice(setup):
    service, _, connection = setup
    identifier = create(service)["asset"]["id"]
    service.control.repo.update_connection("local", connection.id, RouterConnectionUpdate(api_key="different-synthetic-key"))
    with pytest.raises(RouterServiceError) as error:
        asyncio.run(service.change(identifier, "query", "query"))
    assert error.value.code == "voice_connection_changed"


@pytest.mark.parametrize("sample", [b"invalid", wav(1), wav(6, 8000), wav()[:-2]], ids=["invalid", "short", "rate", "truncated"])
def test_sample_validation(sample):
    with pytest.raises(RouterServiceError):
        validate_sample(base64.b64encode(sample).decode())


@pytest.mark.parametrize("url", ["http://dashscope.aliyuncs.com", "https://evil.test", "https://dashscope.aliyuncs.com.evil.test", "https://dashscope.aliyuncs.com/api/v1?key=bad"])
def test_no_arbitrary_provider_or_reference_url(url):
    with pytest.raises(RouterServiceError):
        cloud_base(url)


def test_api_asset_route_and_reused_speech_endpoint(setup, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.multimodal import api, voice_api
    service, _, _ = setup
    monkeypatch.setattr(voice_api, "get_voice_library", lambda: service)
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    assert client.get("/api/multimodal/voice-library/catalog").json()["available"]
    result = client.post("/api/multimodal/voice-library/create", json=dict(operationId="create", model=MODEL, name="旁白", consent=True, audio=base64.b64encode(wav()).decode()))
    assert result.status_code == 200
    identifier = result.json()["asset"]["id"]
    assert client.post(f"/api/multimodal/voice-library/{identifier}/query", json={"operationId": "query"}).status_code == 200
    speech = dict(model_id=MODEL, input="今天阳光很好。", voice="library:" + identifier, voice_asset_id=identifier, response_format="wav", speed=1)
    response = client.post("/api/multimodal/speech", json=speech, headers={"Idempotency-Key": "speak"})
    assert response.status_code == 200 and response.content == wav(1)
    assert client.post("/api/multimodal/speech", json=speech, headers={"Idempotency-Key": "speak"}).status_code == 409
    bad = client.post("/api/multimodal/voice-library/create", json={"audio": "private-sample-not-echoed"})
    assert bad.status_code == 422 and "private-sample" not in bad.text


def test_control_admin_auth_is_required(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.model_router.api import router
    from server.model_router.admin_auth import reset_provider_admin_auth
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", "synthetic-admin-secret-at-least-32-characters")
    reset_provider_admin_auth()
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.get("/api/router/voice-library/control").status_code in (401, 403)
    assert client.put("/api/router/voice-library/control", json={}).status_code in (401, 403)
    reset_provider_admin_auth()


def test_dotted_cloud_voice_id_and_unsafe_id_rejection(setup):
    service, _, _ = setup
    identifier = MODEL + "-sample-abcd1234"
    async def send(*args, **kwargs):
        return {"voice_id": identifier}
    service.cloud._send = send
    result = create(service)
    assert service.control.asset(result["asset"]["id"])["externalId"] == identifier
    identifier = "../../other-voice"
    with pytest.raises(RouterServiceError):
        create(service, "unsafe-create")


def test_streaming_wav_requires_complete_http_body_and_preserves_pcm():
    import struct
    original = wav(1)
    streaming = bytearray(original)
    struct.pack_into("<I", streaming, 4, 0x7FFFFFBF)
    struct.pack_into("<I", streaming, 40, 0x7FFFFF9B)
    assert normalize_cloud_wav(bytes(streaming), str(len(streaming))) == original
    for raw, length in [(bytes(streaming), None), (bytes(streaming), str(len(streaming)+2)), (bytes(streaming[:-1]), str(len(streaming)-1))]:
        with pytest.raises(ValueError, match="incomplete streaming audio"):
            normalize_cloud_wav(raw, length)
    # Ordinary truncated files are left untouched for strict WAV validation.
    assert normalize_cloud_wav(original[:-2], str(len(original)-2)) == original[:-2]


def test_observed_dashscope_response_shapes_complete_full_lifecycle(setup):
    import struct
    service, _, _ = setup
    requests = []
    voice_id = MODEL + "-neutral-abcdef123"
    original = wav(1)
    streaming = bytearray(original)
    struct.pack_into("<I", streaming, 4, 0x7FFFFFBF)
    struct.pack_into("<I", streaming, 40, 0x7FFFFF9B)
    def respond(request):
        requests.append(request)
        if request.method == "GET":
            assert request.url.scheme == "https" and "authorization" not in request.headers
            return httpx.Response(200, content=bytes(streaming))
        body = json.loads(request.content)
        if body["model"] == MODEL:
            assert body["input"]["voice"] == voice_id
            return httpx.Response(200, json={"output": {"audio": {"url": "http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/voice.wav?signature=fixture"}}})
        return httpx.Response(200, json={"output": {"voice_id": voice_id} if body["input"]["action"] == "create_voice" else {"status": "OK", "target_model": MODEL}})
    service.cloud = VoiceCloud(service.control.router.egress_policy, lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    identifier = create(service)["asset"]["id"]
    assert asyncio.run(service.change(identifier, "query", "query"))["asset"]["state"] == "ready"
    audio = asyncio.run(service.synthesize(asset_id=identifier, model=MODEL, text="今天阳光很好。", response_format="wav", speed=1, operation_id="speak"))
    assert audio.content == original
    assert [r.method for r in requests] == ["POST", "POST", "POST", "GET"]
    assert service.control.state()["spent"] == 3


@pytest.mark.parametrize("host,allowed", [("dashscope-result-bj.oss-cn-beijing.aliyuncs.com", True), ("example.com", False), ("evil.aliyuncs.com", False)])
def test_legacy_oss_url_is_upgraded_to_https_without_credentials(setup, host, allowed):
    service, _, _ = setup
    requests = []
    def respond(request):
        requests.append(request)
        if request.method == "GET":
            assert request.url.scheme == "https"
            assert "authorization" not in request.headers
            assert request.url.query == b"signature=fixture"
            return httpx.Response(200, content=wav(1))
        return httpx.Response(200, json={"output": {"audio": {"url": "http://"+host+"/audio.wav?signature=fixture"}}})
    cloud = VoiceCloud(service.control.router.egress_policy, lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    async def run():
        target = await cloud.authorize("https://dashscope.aliyuncs.com/api/v1", "synthesize")
        return await cloud.synthesize(target, "private-fixture", model=MODEL, voice_id="fixture", text="你好")
    if allowed:
        assert asyncio.run(run()) == wav(1)
        assert len(requests) == 2
    else:
        with pytest.raises(ValueError, match="invalid audio URL"):
            asyncio.run(run())
        assert len(requests) == 1


@pytest.mark.parametrize("ambiguous", [False, True])
def test_unknown_creation_lookup_never_reuploads_and_preserves_evidence(setup, ambiguous):
    from datetime import datetime, timedelta, timezone
    service, _, _ = setup
    requests = []
    async def send(target, key, payload):
        requests.append(payload)
        if payload["input"]["action"] == "create_voice":
            raise ValueError("lost creation response")
        assert payload["input"]["action"] == "list_voice"
        prefix = payload["input"]["prefix"]
        voice = {"voice_id": MODEL + "-" + prefix + "-abc123", "status": "OK"}
        return {"voice_list": [voice, voice] if ambiguous else [voice]}
    service.cloud._send = send
    with pytest.raises(RouterServiceError):
        create(service)
    c = service.control
    original = c.operation("create")
    identifier = original["assetId"]
    with pytest.raises(RouterServiceError) as wait:
        asyncio.run(service.change(identifier, "query", "early-query"))
    assert wait.value.code == "voice_reconcile_wait" and len(requests) == 1
    with c.transaction() as db:
        old = c.read(db, "operation:create")
        c.write(db, "operation:create", {**old, "created": (datetime.now(timezone.utc)-timedelta(minutes=4)).isoformat()})
    if ambiguous:
        with pytest.raises(RouterServiceError):
            asyncio.run(service.change(identifier, "query", "lookup"))
        assert c.asset(identifier)["externalId"] is None
        assert not c.operation("create").get("reconciledBy")
        with pytest.raises(RouterServiceError):
            asyncio.run(service.change(identifier, "query", "another-lookup"))
    else:
        result = asyncio.run(service.change(identifier, "query", "lookup"))
        assert result["asset"]["state"] == "ready"
        assert c.operation("create")["state"] == "unknown"
        assert c.operation("create")["reconciledBy"] == "lookup"
        asyncio.run(service.change(identifier, "query", "lookup"))
    assert len(requests) == 2 and c.state()["spent"] == 2
