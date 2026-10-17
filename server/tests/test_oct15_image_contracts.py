from io import BytesIO

import pytest
from PIL import Image

from server.multimodal.image_catalog import ImageModelProfile
from server.multimodal.image_generation import ImageGenerationService
from server.multimodal.stt import MultimodalServiceError


def profile(model_id):
    krea = model_id == "krea/k3-edit"
    return ImageModelProfile(
        model_id=model_id, display_name=model_id, operation="generate_image",
        supported_parameters={
            "resolution": {"type": "enum", "values": ["1K", "2K"] if krea else ["768", "1K", "1.5K", "2K", "4K"]},
            "input_references": {"type": "range", "min": 1 if krea else 0, "max": 4 if krea else 10},
            **({"seed": {"type": "boolean"}} if krea else {"n": {"type": "range", "min": 1, "max": 1}}),
        },
    )


@pytest.mark.parametrize("model_id,minimum,maximum", [("krea/k3-edit", 1, 4), ("black-forest-labs/flux-3-image-klein", 0, 10)])
def test_reference_limits_and_base64_contract(model_id, minimum, maximum):
    item = profile(model_id)
    if minimum:
        with pytest.raises(MultimodalServiceError) as exc:
            ImageGenerationService._references(item, [], [], [])
        assert exc.value.code == "not_enough_image_references"
    else:
        assert ImageGenerationService._references(item, [], [], []) == []
    output = BytesIO()
    Image.new("RGB", (32, 32), "blue").save(output, format="PNG")
    references = ImageGenerationService._references(item, ["test.png"] * maximum, ["image/png"] * maximum, [output.getvalue()] * maximum)
    assert len(references) == maximum
    assert all(value.startswith("data:image/png;base64,") for value in references)
    with pytest.raises(MultimodalServiceError) as exc:
        ImageGenerationService._references(item, ["test.png"] * (maximum + 1), ["image/png"] * (maximum + 1), [output.getvalue()] * (maximum + 1))
    assert exc.value.code == "too_many_image_references"


def test_krea_resolution_and_klein_seed_fail_closed():
    with pytest.raises(MultimodalServiceError):
        ImageGenerationService._validate_value(profile("krea/k3-edit"), "resolution", "4K")
    with pytest.raises(MultimodalServiceError):
        ImageGenerationService._validate_value(profile("black-forest-labs/flux-3-image-klein"), "seed", 42)
    with pytest.raises(MultimodalServiceError):
        ImageGenerationService._validate_value(profile("black-forest-labs/flux-3-image-klein"), "n", 2, default=1)


@pytest.mark.parametrize("model_id", ["qwen/qwen-image-2.1-turbo", "qwen/qwen-image-2.1-pro"])
def test_qwen_image21_dedicated_parameters(model_id):
    item = ImageModelProfile(
        model_id=model_id, display_name=model_id, operation="generate_image",
        supported_parameters={
            "resolution": {"type": "enum", "values": ["1K", "2K"]},
            "aspect_ratio": {"type": "enum", "values": ["1:1", "1:2", "1:4", "2:1", "2:3", "3:2", "3:4", "4:1", "4:3", "4:5", "5:4", "9:16", "16:9"]},
            "n": {"type": "range", "min": 1, "max": 6},
            "input_references": {"type": "range", "min": 0, "max": 4},
            "seed": {"type": "boolean"},
        },
    )
    ImageGenerationService._validate_value(item, "n", 6, default=1)
    ImageGenerationService._validate_value(item, "resolution", "2K")
    ImageGenerationService._validate_value(item, "aspect_ratio", "1:4")
    assert ImageGenerationService._references(item, [], [], []) == []
    for key, value in [("n", 7), ("resolution", "4K"), ("aspect_ratio", "auto")]:
        with pytest.raises(MultimodalServiceError):
            ImageGenerationService._validate_value(item, key, value)
    with pytest.raises(MultimodalServiceError) as exc:
        ImageGenerationService._references(item, ["x.png"] * 5, ["image/png"] * 5, [b""] * 5)
    assert exc.value.code == "too_many_image_references"
