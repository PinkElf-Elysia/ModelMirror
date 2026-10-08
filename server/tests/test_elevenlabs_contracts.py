import pytest
from types import SimpleNamespace
from unittest.mock import Mock

from server.main import OpenRouterDecisionRequest
from server.multimodal.audio_catalog import OPENROUTER_AUDIO_CONTRACTS
from server.multimodal.stt import MANUAL_TRANSCRIPTION_PROFILES
from server.multimodal.stt import MultimodalServiceError
from server.multimodal.tts import (
    ELEVENLABS_SPEECH_MODEL_IDS, ELEVENLABS_SPEED_MODEL_IDS,
    ELEVENLABS_VOICES, MANUAL_SPEECH_PROFILE_IDS, speech_request_payload,
    SpeechService,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("model,speed", [
    ("elevenlabs/eleven-v4", 1.1),
    ("elevenlabs/eleven-flash-v2", 0.6),
    ("elevenlabs/eleven-flash-v2.5", 1.3),
])
async def test_elevenlabs_rejects_unsupported_speed_before_dispatch(model, speed):
    gateway = Mock()
    gateway.routing_mode.return_value = "managed_required"
    service = SpeechService(SimpleNamespace(egress_policy=None), adapter=Mock(), managed_gateway=gateway)
    with pytest.raises(MultimodalServiceError) as captured:
        await service.synthesize(model_id=model, text="test", voice="george", response_format="mp3", speed=speed)
    assert captured.value.code == "invalid_speech_speed"
    gateway.execute.assert_not_called()


@pytest.mark.parametrize("model", sorted(ELEVENLABS_SPEECH_MODEL_IDS))
def test_elevenlabs_speech_contract(model):
    assert len(ELEVENLABS_SPEECH_MODEL_IDS) == 9
    assert len(ELEVENLABS_VOICES) == 21
    assert model in MANUAL_SPEECH_PROFILE_IDS
    contract = OPENROUTER_AUDIO_CONTRACTS[model]
    assert contract.interaction_adapted
    assert not contract.behavior_verified
    assert contract.manual_verification_required
    payload = speech_request_payload(model_id=model, text="你好😀", voice="george", response_format="mp3", speed=1)
    assert payload["voice"] == "george"
    assert payload["response_format"] == "mp3"
    assert ("speed" in payload) == (model in ELEVENLABS_SPEED_MODEL_IDS)


@pytest.mark.parametrize("model", ["elevenlabs/scribe-v2", "elevenlabs/scribe-v2-medical"])
def test_scribe_manual_transcription_contract(model):
    assert "wav" in MANUAL_TRANSCRIPTION_PROFILES[model].input_formats
    assert OPENROUTER_AUDIO_CONTRACTS[model].manual_verification_required
    assert not OPENROUTER_AUDIO_CONTRACTS[model].behavior_verified


@pytest.mark.parametrize("model", ["perplexity/pplx-decider-v1.1-27b", "upstage/solar-decide-flash"])
def test_new_decisions_keep_typed_contract(model):
    question = {"type": "choice", "instructions": "选择", "criteria": {"a": "甲", "b": "乙"}}
    assert OpenRouterDecisionRequest(model=model, state="test", questions={"q": question}).model == model
    with pytest.raises(ValueError):
        OpenRouterDecisionRequest(model=model, state="test", questions={str(i): question for i in range(51)})
