import pytest
from server.main import OpenRouterDecisionRequest
from server.multimodal.audio_catalog import OPENROUTER_AUDIO_CONTRACTS
from server.multimodal.stt import MultimodalServiceError
from server.multimodal.video_jobs import VideoJobService
from server.tests.test_multimodal_video_jobs import router_service, StubCatalog, FakeAdapter

@pytest.mark.parametrize("model", ["morph/reflex", "cloudflare/clairic", "baseten/joey-1"])
def test_decision_registration(model):
    question={"type":"choice","instructions":"Choose","criteria":{"a":"First","b":"Second"}}
    assert OpenRouterDecisionRequest(model=model,state="Public fixture",questions={"q":question}).model == model
    with pytest.raises(ValueError):
        OpenRouterDecisionRequest(model=model,state="Public fixture",questions={str(i):question for i in range(51)})

def test_parakeet_remains_manual():
    contract=OPENROUTER_AUDIO_CONTRACTS["moondream/parakeet-ultra"]
    assert contract.interaction_adapted
    assert contract.manual_verification_required
    assert not contract.behavior_verified

def test_crouton_audio_is_bounded_and_not_behavior_verified():
    contract=OPENROUTER_AUDIO_CONTRACTS["stealth/crouton-alpha"]
    assert contract.input_formats == ("wav",)
    assert contract.chat_modes == ("direct_audio_input",)
    assert contract.interaction_adapted
    assert not contract.behavior_verified

@pytest.mark.asyncio
async def test_mischief_requires_first_frame_before_egress(tmp_path):
    router=router_service(tmp_path)
    adapter=FakeAdapter()
    service=VideoJobService(router,StubCatalog(router,model_id="tbc/mischief-1"),adapter=adapter)
    with pytest.raises(MultimodalServiceError) as caught:
        await service.create(model_id="tbc/mischief-1",prompt="Public fixture",duration=5,resolution="720p",idempotency_key="mischief-no-frame")
    assert caught.value.code == "first_frame_required"
    assert adapter.submit_calls == []
