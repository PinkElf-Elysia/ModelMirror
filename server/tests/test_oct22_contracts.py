import pytest
from server.main import OpenRouterDecisionRequest
from server.multimodal.audio_catalog import OPENROUTER_AUDIO_CONTRACTS
from server.multimodal.tts import ALLOWED_SPEECH_PROFILES, speech_request_payload


@pytest.mark.parametrize("model", ["baseten/mammoth-1:free", "baseten/elephant-1:free", "nace-ai/drex-max"])
def test_decision_registration(model):
    question = {"type": "choice", "instructions": "Select", "criteria": {"a": "First", "b": "Second"}}
    assert OpenRouterDecisionRequest(model=model, state="Public fixture", questions={"q": question}).model == model
    with pytest.raises(ValueError):
        OpenRouterDecisionRequest(model=model, state="Public fixture", questions={str(i): question for i in range(51)})


@pytest.mark.parametrize("model", ["mistralai/voxtral-mini-tts-3-0", "mistralai/voxtral-small-transcribe-3-0", "mistralai/voxtral-mini-transcribe-3-0"])
def test_audio_contract_not_provider_verified(model):
    contract = OPENROUTER_AUDIO_CONTRACTS[model]
    assert contract.interaction_adapted
    assert contract.manual_verification_required
    assert not contract.behavior_verified


def test_voxtral_preset_request():
    model = "mistralai/voxtral-mini-tts-3-0"
    assert "en_paul_neutral" in ALLOWED_SPEECH_PROFILES[model]
    payload = speech_request_payload(model_id=model, text="Hello", voice="en_paul_neutral", response_format="mp3", speed=1)
    assert payload["model"] == model
    assert payload["voice"] == "en_paul_neutral"
    assert "input_references" not in payload
