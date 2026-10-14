import json
import struct

import httpx
import pytest

from server.multimodal.audio_catalog import OPENROUTER_AUDIO_CONTRACTS
from server.multimodal.stt import MultimodalServiceError, OpenRouterTarget
from server.multimodal.tts import (
    DRAMA_3_MODEL_ID, DRAMA_3_VOICES, HEYGEN_SPEECH_MODEL_IDS,
    HEYGEN_VOICES, OpenRouterTtsAdapter, SpeechService, speech_request_payload,
)


@pytest.mark.parametrize("model", [*HEYGEN_SPEECH_MODEL_IDS, DRAMA_3_MODEL_ID])
def test_new_speech_contracts_are_adapted_not_behavior_verified(model, monkeypatch):
    monkeypatch.delenv("MULTIMODAL_VERIFICATION_MODEL_IDS", raising=False)
    contract = OPENROUTER_AUDIO_CONTRACTS[model]
    assert contract.interaction_adapted
    assert contract.manual_verification_required
    assert not contract.behavior_verified
    assert contract.verification_status == "manual_required"
    assert contract.voices == (DRAMA_3_VOICES if model == DRAMA_3_MODEL_ID else HEYGEN_VOICES)
    assert contract.output_formats == (("mp3",) if model == DRAMA_3_MODEL_ID else ("wav",))
    with pytest.raises(MultimodalServiceError) as error:
        SpeechService._model_id(model)
    assert error.value.code == "speech_model_verification_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", sorted(HEYGEN_SPEECH_MODEL_IDS))
async def test_heygen_requests_pcm_and_preserves_44100_rate(model):
    pcm = b"\x00\x01" * 256
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model, "architecture": {"output_modalities": ["speech"]}}]})
        assert request.url.path == "/api/v1/audio/speech"
        assert json.loads(request.content) == {"model": model, "input": "Public test", "voice": "nadine", "response_format": "pcm"}
        return httpx.Response(200, content=pcm, headers={"content-type": "audio/pcm;rate=44100;channels=1"})
    adapter = OpenRouterTtsAdapter(client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    target = OpenRouterTarget(base_url="https://openrouter.ai/api/v1", api_key="test", cache_key=model, connection_id=None)
    content, _, output_format = await adapter.synthesize(target, model_id=model, text="Public test", voice="nadine", speed=1)
    assert output_format == "wav"
    assert content[:4] == b"RIFF"
    assert struct.unpack_from("<I", content, 24)[0] == 44100
    assert content[44:] == pcm


def test_heygen_cannot_silently_use_gemini_rate():
    response = httpx.Response(200, content=b"\x00\x01" * 256, headers={"content-type": "audio/pcm;rate=24000;channels=1"})
    with pytest.raises(MultimodalServiceError):
        OpenRouterTtsAdapter._pcm_response_to_wav(response, expected_rate=44100)


def test_drama_keeps_direction_text_and_exact_public_voice():
    text = "[quiet] <whisper>Hello</whisper>"
    body = speech_request_payload(model_id=DRAMA_3_MODEL_ID, text=text, voice=DRAMA_3_VOICES[0], response_format="mp3", speed=1)
    assert body == {"model": DRAMA_3_MODEL_ID, "input": text, "voice": DRAMA_3_VOICES[0], "response_format": "mp3", "speed": 1}
    with pytest.raises(MultimodalServiceError):
        SpeechService._voice(DRAMA_3_MODEL_ID, "unreviewed-clone")
