import io
import wave

import pytest

from server.multimodal.stt import (
    MANUAL_TRANSCRIPTION_PROFILES, VERIFIED_TRANSCRIPTION_PROFILES,
    MultimodalServiceError, validate_assemblyai_audio,
    TranscriptionService,
)


def wav(seconds=1, width=2):
    data = io.BytesIO()
    with wave.open(data, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(width)
        output.setframerate(8000)
        output.writeframes(b"\0" * (8000 * seconds * width))
    return data.getvalue()


@pytest.mark.parametrize("model", ["assemblyai/universal-3-5-pro", "assemblyai/universal-3-6-pro"])
def test_assembly_contract(model):
    assert model not in VERIFIED_TRANSCRIPTION_PROFILES
    assert MANUAL_TRANSCRIPTION_PROFILES[model].input_formats == ("wav",)
    validate_assemblyai_audio(model, "wav", wav(120))
    for fmt, audio in [("mp3", b"ID3"), ("wav", wav(121)), ("wav", wav(width=1)), ("wav", wav()[:-2]), ("wav", b"RIFFbroken"), ("wav", wav(0))]:
        with pytest.raises(MultimodalServiceError) as error:
            validate_assemblyai_audio(model, fmt, audio)
        assert error.value.code == "invalid_assemblyai_audio"


def test_unrelated_model_unchanged():
    validate_assemblyai_audio("openai/whisper-1", "mp3", b"ID3")


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
async def test_invalid_audio_is_blocked_before_dispatch(managed, monkeypatch):
    model = "assemblyai/universal-3-6-pro"
    monkeypatch.setenv("MULTIMODAL_VERIFICATION_MODEL_IDS", model)

    class Gateway:
        def routing_mode(self, _entry):
            return "managed" if managed else "legacy"

        def exact_model_id(self, *_args, **_kwargs):
            raise AssertionError("Invalid audio must not reach route planning")

    service = TranscriptionService(None, adapter=object(), managed_gateway=Gateway())
    with pytest.raises(MultimodalServiceError, match="120"):
        await service.transcribe(model_id=model, filename="test.wav", content_type="audio/wav", content=wav(121), language="en")


@pytest.mark.asyncio
async def test_managed_resolved_model_is_also_validated():
    class Gateway:
        def exact_model_id(self, *_args, **_kwargs):
            return "assemblyai/universal-3-6-pro"

    gateway = Gateway()
    service = TranscriptionService(None, adapter=object(), managed_gateway=gateway)
    with pytest.raises(MultimodalServiceError, match="120"):
        await service._transcribe_managed(
            gateway, entry_id="multimodal_transcription", model_id="requested-alias",
            filename="test.wav", audio_format="wav", content_type="audio/wav",
            content=wav(121), language="en", idempotency_key="test-invalid-audio",
        )
