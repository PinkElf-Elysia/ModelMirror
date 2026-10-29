import pytest
from server.main import OpenRouterDecisionRequest
from server.multimodal.tts import ALLOWED_SPEECH_PROFILES, speech_request_payload

@pytest.mark.parametrize("model", ["liquid/d1.1", "~liquid/d1-latest", "~liquid/d1-ultra-latest", "baseten/mammoth-1", "baseten/elephant-1"])
def test_liquid_text_decisions(model):
    question = {"type": "choice", "instructions": "Choose", "criteria": {"a": "First", "b": "Second"}}
    assert OpenRouterDecisionRequest(model=model, state="Public fixture", questions={"q": question}).model == model
    with pytest.raises(ValueError):
        OpenRouterDecisionRequest(model=model, state="Public fixture", questions={str(i): question for i in range(51)})

def test_heygen_free_keeps_exact_model_and_presets():
    model = "heygen/heygen-voice-1-turbo:free"
    assert len(ALLOWED_SPEECH_PROFILES[model]) == 10
    payload = speech_request_payload(model_id=model, text="Hello", voice="nadine", response_format="wav", speed=1)
    assert payload["model"] == model

@pytest.mark.parametrize("model", ["nvidia/nemotron-3.5-transcribe", "assemblyai/assemblyai-tts-1"])
def test_new_audio_manual_gate(model):
    from server.multimodal.audio_catalog import OPENROUTER_AUDIO_CONTRACTS
    contract = OPENROUTER_AUDIO_CONTRACTS[model]
    assert contract.interaction_adapted
    assert contract.manual_verification_required
    assert not contract.behavior_verified
