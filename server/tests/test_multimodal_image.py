from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from httpx import MockTransport, Request, Response
from PIL import Image

from server.model_router.repository import SQLiteRouterRepository
from server.model_router.egress import ProviderEgressPolicy
from server.model_router.schemas import RouterConnectionCreate
from server.model_router.service import ModelRouterService
from server.multimodal.image_catalog import ImageCatalogService
from server.multimodal.image_generation import ImageGenerationService
from server.multimodal.stt import MultimodalServiceError


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"image-payload"
SVG_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"/>'


def png_reference(edge: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (edge, edge), "#7c3aed").save(output, format="PNG")
    return output.getvalue()


def openrouter_service(tmp_path: Path) -> ModelRouterService:
    repository = SQLiteRouterRepository(tmp_path)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="OpenRouter",
            kind="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="image-test-secret",
        ),
    )
    repository.save_test_result(
        "local",
        connection.id,
        health="online",
        model_count=3,
        checked_at="2026-08-05T00:00:00+00:00",
        error_code=None,
        error_hint=None,
    )
    return ModelRouterService(
        repository,
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )


def image_catalog_handler(request: Request) -> Response:
    if request.url.path.endswith("/images/models"):
        return Response(
            200,
            json={
                "data": [
                    {
                        "id": "qwen/image-test",
                        "name": "Qwen Image Test",
                        "architecture": {
                            "input_modalities": ["text", "image"],
                            "output_modalities": ["image"],
                        },
                        "supported_parameters": {
                            "resolution": {
                                "type": "enum",
                                "values": ["1K", "2K"],
                            },
                            "n": {"type": "range", "min": 1, "max": 2},
                            "input_references": {
                                "type": "range",
                                "min": 0,
                                "max": 2,
                            },
                            "seed": {"type": "boolean"},
                        },
                        "supports_streaming": False,
                    }
                ]
            },
        )
    return Response(
        200,
        json={
            "data": [
                {
                    "id": "openai/vision-test",
                    "name": "Vision Test",
                    "architecture": {
                        "input_modalities": ["text", "image"],
                        "output_modalities": ["text"],
                    },
                },
                {
                    "id": "qwen/image-test",
                    "name": "Qwen Image Test",
                    "architecture": {
                        "input_modalities": ["text", "image"],
                        "output_modalities": ["image"],
                    },
                },
                {
                    "id": "openai/text-test",
                    "name": "Text Test",
                    "architecture": {
                        "input_modalities": ["text"],
                        "output_modalities": ["text"],
                    },
                },
            ]
        },
    )


@pytest.mark.asyncio
async def test_image_catalog_separates_understanding_and_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_IMAGE_ANALYSIS_ENABLED", "true")
    monkeypatch.setenv("MULTIMODAL_IMAGE_GENERATION_ENABLED", "true")
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        return image_catalog_handler(request)

    service = ImageCatalogService(
        openrouter_service(tmp_path),
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(handler)
        ),
    )

    result = await service.get_catalog()

    assert result.status == "online"
    assert {(item.model_id, item.operation) for item in result.profiles} == {
        ("openai/vision-test", "analyze_image"),
        ("qwen/image-test", "generate_image"),
    }
    generation = next(
        item for item in result.profiles if item.operation == "generate_image"
    )
    assert all(
        item.operation_readiness[0].interaction_status == "ready"
        and item.operation_readiness[0].availability_status == "available"
        and item.operation_readiness[0].verification_status == "verified"
        for item in result.profiles
    )
    assert generation.supported_parameters["resolution"].values == [
        "1K",
        "2K",
    ]
    assert generation.supported_parameters["input_references"].max == 2
    assert all(
        request.headers.get("authorization") == "Bearer image-test-secret"
        for request in requests
    )


@pytest.mark.asyncio
async def test_image_catalog_uses_stale_cache_on_refresh_error(
    tmp_path: Path,
) -> None:
    should_fail = False

    def handler(request: Request) -> Response:
        if should_fail:
            return Response(503)
        return image_catalog_handler(request)

    service = ImageCatalogService(
        openrouter_service(tmp_path),
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(handler)
        ),
    )
    assert (await service.get_catalog()).status == "online"
    should_fail = True

    stale = await service.get_catalog(force=True)

    assert stale.status == "stale"
    assert stale.stale is True
    assert len(stale.profiles) == 2


@pytest.mark.asyncio
async def test_image_generation_validates_capabilities_and_complete_output(
    tmp_path: Path,
) -> None:
    catalog = ImageCatalogService(
        openrouter_service(tmp_path),
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(image_catalog_handler)
        ),
    )
    submitted: list[dict[str, object]] = []

    def generation_handler(request: Request) -> Response:
        submitted.append(httpx.Response(200, content=request.content).json())
        return Response(
            200,
            headers={"x-request-id": "req_image_1"},
            json={
                "model": "qwen/image-test",
                "data": [
                    {
                        "b64_json": base64.b64encode(PNG_BYTES).decode(),
                        "media_type": "image/png",
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 34,
                    "total_tokens": 46,
                    "cost": 0.04,
                },
            },
        )

    service = ImageGenerationService(
        catalog,
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(generation_handler)
        ),
    )
    result = await service.generate(
        model_id="qwen/image-test",
        prompt="一只纸艺狐狸",
        n=2,
        resolution="2K",
        seed=7,
        reference_filenames=["reference.png"],
        reference_content_types=["image/png"],
        reference_contents=[PNG_BYTES],
    )

    assert result.request_id == "req_image_1"
    assert result.images[0].output_bytes == len(PNG_BYTES)
    assert result.usage.cost_usd == 0.04
    assert submitted[0]["n"] == 2
    assert submitted[0]["resolution"] == "2K"
    assert len(submitted[0]["input_references"]) == 1


@pytest.mark.asyncio
async def test_seedream_5_pro_uses_the_dedicated_images_contract(
    tmp_path: Path,
) -> None:
    model_id = "bytedance-seed/seedream-5-0-pro"

    def handler(request: Request) -> Response:
        if request.url.path.endswith(f"/images/models/{model_id}/endpoints"):
            return Response(
                200,
                json={
                    "id": model_id,
                    "endpoints": [
                        {
                            "pricing": [
                                {
                                    "billable": "output_image",
                                    "unit": "image",
                                    "cost_usd": 0.045,
                                },
                                {
                                    "billable": "output_image",
                                    "unit": "image",
                                    "cost_usd": 0.09,
                                    "variant": "high_resolution",
                                },
                                {
                                    "billable": "input_image",
                                    "unit": "image",
                                    "cost_usd": 0.003,
                                },
                            ]
                        }
                    ],
                },
            )
        if request.url.path.endswith("/images/models"):
            return Response(
                200,
                json={
                    "data": [
                        {
                            "id": model_id,
                            "name": "ByteDance Seed: Seedream 5.0 Pro",
                            "architecture": {
                                "input_modalities": ["text", "image"],
                                "output_modalities": ["image"],
                            },
                            "supported_parameters": {
                                "resolution": {
                                    "type": "enum",
                                    "values": ["1K", "2K"],
                                },
                                "aspect_ratio": {
                                    "type": "enum",
                                    "values": ["1:1", "16:9", "auto"],
                                },
                                "n": {"type": "range", "min": 1, "max": 1},
                                "input_references": {
                                    "type": "range",
                                    "min": 0,
                                    "max": 14,
                                },
                                "seed": {"type": "boolean"},
                            },
                            "supports_streaming": False,
                        }
                    ]
                },
            )
        return Response(200, json={"data": []})

    catalog = ImageCatalogService(
        openrouter_service(tmp_path),
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(handler)
        ),
    )
    result = await catalog.get_catalog()
    profile = next(item for item in result.profiles if item.model_id == model_id)

    assert profile.operation == "generate_image"
    assert profile.interaction_status == "ready"
    assert profile.supports_streaming is False
    assert profile.supported_parameters["resolution"].values == ["1K", "2K"]
    assert profile.supported_parameters["n"].max == 1
    assert profile.supported_parameters["input_references"].max == 14
    assert [item.model_dump() for item in profile.pricing] == [
        {
            "billable": "output_image",
            "unit": "image",
            "cost_usd": 0.045,
            "variant": None,
        },
        {
            "billable": "output_image",
            "unit": "image",
            "cost_usd": 0.09,
            "variant": "high_resolution",
        },
        {
            "billable": "input_image",
            "unit": "image",
            "cost_usd": 0.003,
            "variant": None,
        },
    ]


@pytest.mark.asyncio
async def test_seedream_5_lite_uses_the_dedicated_images_contract(
    tmp_path: Path,
) -> None:
    model_id = "bytedance-seed/seedream-5-0-lite"
    aspect_ratios = [
        "1:1",
        "1:2",
        "2:1",
        "2:3",
        "3:2",
        "3:4",
        "4:3",
        "4:5",
        "5:4",
        "9:16",
        "16:9",
        "9:19.5",
        "19.5:9",
        "9:20",
        "20:9",
        "9:21",
        "21:9",
        "auto",
    ]

    def handler(request: Request) -> Response:
        if request.url.path.endswith(f"/images/models/{model_id}/endpoints"):
            return Response(
                200,
                json={
                    "id": model_id,
                    "endpoints": [
                        {
                            "pricing": [
                                {
                                    "billable": "output_image",
                                    "unit": "image",
                                    "cost_usd": 0.035,
                                }
                            ]
                        }
                    ],
                },
            )
        if request.url.path.endswith("/images/models"):
            return Response(
                200,
                json={
                    "data": [
                        {
                            "id": model_id,
                            "name": "ByteDance Seed: Seedream 5.0 Lite",
                            "architecture": {
                                "input_modalities": ["text", "image"],
                                "output_modalities": ["image"],
                            },
                            "supported_parameters": {
                                "resolution": {
                                    "type": "enum",
                                    "values": ["2K", "4K"],
                                },
                                "aspect_ratio": {
                                    "type": "enum",
                                    "values": aspect_ratios,
                                },
                                "n": {"type": "range", "min": 1, "max": 4},
                                "input_references": {
                                    "type": "range",
                                    "min": 0,
                                    "max": 14,
                                },
                                "seed": {"type": "boolean"},
                            },
                            "supports_streaming": False,
                        }
                    ]
                },
            )
        return Response(200, json={"data": []})

    catalog = ImageCatalogService(
        openrouter_service(tmp_path),
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(handler)
        ),
    )
    result = await catalog.get_catalog()
    profile = next(item for item in result.profiles if item.model_id == model_id)

    assert profile.operation == "generate_image"
    assert profile.interaction_status == "ready"
    assert profile.supports_streaming is False
    assert profile.supported_parameters["resolution"].values == ["2K", "4K"]
    assert profile.supported_parameters["aspect_ratio"].values == aspect_ratios
    assert profile.supported_parameters["n"].max == 4
    assert profile.supported_parameters["input_references"].max == 14
    assert [item.model_dump() for item in profile.pricing] == [
        {
            "billable": "output_image",
            "unit": "image",
            "cost_usd": 0.035,
            "variant": None,
        }
    ]


@pytest.mark.asyncio
async def test_image_catalog_fetches_pricing_for_every_generation_model(
    tmp_path: Path,
) -> None:
    requested_endpoint_ids: list[str] = []

    def handler(request: Request) -> Response:
        if request.url.path.endswith("/images/models"):
            return Response(
                200,
                json={
                    "data": [
                        {
                            "id": "provider/image-a",
                            "architecture": {
                                "input_modalities": ["text"],
                                "output_modalities": ["image"],
                            },
                        },
                        {
                            "id": "provider/image-b",
                            "architecture": {
                                "input_modalities": ["text"],
                                "output_modalities": ["image"],
                            },
                        },
                    ]
                },
            )
        if "/images/models/" in request.url.path:
            model_id = request.url.path.split("/images/models/", 1)[1].split(
                "/endpoints", 1
            )[0]
            requested_endpoint_ids.append(model_id)
            if model_id == "provider/image-b":
                return Response(503, json={"error": "temporarily unavailable"})
            return Response(
                200,
                json={
                    "endpoints": [
                        {
                            "pricing": [
                                {
                                    "billable": "output_image",
                                    "unit": "image",
                                    "cost_usd": 0.025,
                                }
                            ]
                        }
                    ]
                },
            )
        return Response(200, json={"data": []})

    catalog = ImageCatalogService(
        openrouter_service(tmp_path),
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(handler)
        ),
    )
    result = await catalog.get_catalog()
    by_id = {
        profile.model_id: profile
        for profile in result.profiles
        if profile.operation == "generate_image"
    }

    assert set(requested_endpoint_ids) == {
        "provider/image-a",
        "provider/image-b",
    }
    assert by_id["provider/image-a"].pricing[0].cost_usd == 0.025
    assert by_id["provider/image-b"].pricing == []
    assert by_id["provider/image-b"].interaction_status == "ready"


@pytest.mark.asyncio
async def test_muse_image_uses_prompt_only_dedicated_images_contract(
    tmp_path: Path,
) -> None:
    model_id = "meta/muse-image"

    def catalog_handler(request: Request) -> Response:
        if request.url.path.endswith("/images/models"):
            return Response(
                200,
                json={
                    "data": [
                        {
                            "id": model_id,
                            "name": "Meta: Muse Image",
                            "architecture": {
                                "input_modalities": ["text", "image"],
                                "output_modalities": ["image"],
                            },
                            "supported_parameters": {},
                            "supports_streaming": False,
                        }
                    ]
                },
            )
        if request.url.path.endswith(
            f"/images/models/{model_id}/endpoints"
        ):
            return Response(200, json={"id": model_id, "endpoints": []})
        return Response(200, json={"data": []})

    catalog = ImageCatalogService(
        openrouter_service(tmp_path),
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(catalog_handler)
        ),
    )
    catalog_result = await catalog.get_catalog()
    profile = next(
        item for item in catalog_result.profiles if item.model_id == model_id
    )

    assert profile.operation == "generate_image"
    assert profile.interaction_status == "ready"
    assert profile.input_modalities == ["text", "image"]
    assert profile.output_modalities == ["image"]
    assert profile.supported_parameters == {}
    assert profile.pricing == []

    submitted: list[tuple[str, dict[str, object]]] = []

    def generation_handler(request: Request) -> Response:
        submitted.append(
            (
                request.url.path,
                httpx.Response(200, content=request.content).json(),
            )
        )
        return Response(
            200,
            headers={"x-request-id": "req_muse_image"},
            json={
                "model": model_id,
                "data": [
                    {"b64_json": base64.b64encode(PNG_BYTES).decode()}
                ],
                "usage": {"cost": 0.01},
            },
        )

    service = ImageGenerationService(
        catalog,
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(generation_handler)
        ),
    )
    result = await service.generate(
        model_id=model_id,
        prompt="一张纸雕风格的森林海报",
        reference_filenames=[],
        reference_content_types=[],
        reference_contents=[],
    )

    assert result.request_id == "req_muse_image"
    assert result.usage.cost_usd == 0.01
    assert submitted == [
        (
            "/api/v1/images",
            {
                "model": model_id,
                "prompt": "一张纸雕风格的森林海报",
            },
        )
    ]

    with pytest.raises(MultimodalServiceError) as error:
        await service.generate(
            model_id=model_id,
            prompt="修改参考图",
            reference_filenames=["reference.png"],
            reference_content_types=["image/png"],
            reference_contents=[PNG_BYTES],
        )
    assert error.value.code == "image_references_not_supported"
    assert len(submitted) == 1


@pytest.mark.asyncio
async def test_recraft_v4_styles_profiles_preserve_dedicated_contract(
    tmp_path: Path,
) -> None:
    model_prices = {
        "recraft/recraft-v4-styles": 0.035,
        "recraft/recraft-v4-styles-pro": 0.1,
        "recraft/recraft-v4-styles-vector": 0.05,
        "recraft/recraft-v4-styles-pro-vector": 0.12,
    }
    vector_ids = {
        "recraft/recraft-v4-styles-vector",
        "recraft/recraft-v4-styles-pro-vector",
    }

    def handler(request: Request) -> Response:
        if request.url.path.endswith("/images/models"):
            return Response(
                200,
                json={
                    "data": [
                        {
                            "id": model_id,
                            "name": model_id,
                            "architecture": {
                                "input_modalities": ["text", "image"],
                                "output_modalities": ["image"],
                            },
                            "supported_parameters": {
                                "aspect_ratio": {
                                    "type": "enum",
                                    "values": ["1:1", "16:9", "auto"],
                                },
                                **(
                                    {
                                        "output_format": {
                                            "type": "enum",
                                            "values": ["svg"],
                                        }
                                    }
                                    if model_id in vector_ids
                                    else {}
                                ),
                                "n": {"type": "range", "min": 1, "max": 6},
                                "input_references": {
                                    "type": "range",
                                    "min": 1,
                                    "max": 10,
                                },
                            },
                            "supports_streaming": False,
                        }
                        for model_id in model_prices
                    ]
                },
            )
        if "/images/models/" in request.url.path:
            model_id = request.url.path.split("/images/models/", 1)[1].split(
                "/endpoints", 1
            )[0]
            return Response(
                200,
                json={
                    "endpoints": [
                        {
                            "pricing": [
                                {
                                    "billable": "output_image",
                                    "unit": "image",
                                    "cost_usd": model_prices[model_id],
                                },
                                {
                                    "billable": "input_reference",
                                    "unit": "request",
                                    "cost_usd": 0.005,
                                },
                            ],
                            "allowed_passthrough_parameters": [
                                "style_id",
                                "style_match",
                                "controls",
                                "random_seed",
                            ],
                        }
                    ]
                },
            )
        return Response(200, json={"data": []})

    catalog = ImageCatalogService(
        openrouter_service(tmp_path),
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(handler)
        ),
    )
    result = await catalog.get_catalog()
    profiles = {
        item.model_id: item
        for item in result.profiles
        if item.model_id in model_prices
    }

    assert set(profiles) == set(model_prices)
    for model_id, output_price in model_prices.items():
        profile = profiles[model_id]
        assert profile.supports_streaming is False
        assert profile.supported_parameters["n"].max == 6
        assert profile.supported_parameters["input_references"].min == 1
        assert profile.supported_parameters["input_references"].max == 10
        assert [item.model_dump() for item in profile.pricing] == [
            {
                "billable": "output_image",
                "unit": "image",
                "cost_usd": output_price,
                "variant": None,
            },
            {
                "billable": "input_reference",
                "unit": "request",
                "cost_usd": 0.005,
                "variant": None,
            },
        ]
        if model_id in vector_ids:
            assert profile.supported_parameters["output_format"].values == [
                "svg"
            ]
        else:
            assert "output_format" not in profile.supported_parameters


@pytest.mark.asyncio
async def test_recraft_styles_requires_valid_reference_and_accepts_svg_output(
    tmp_path: Path,
) -> None:
    model_id = "recraft/recraft-v4-styles-pro-vector"

    def catalog_handler(request: Request) -> Response:
        if request.url.path.endswith(f"/images/models/{model_id}/endpoints"):
            return Response(200, json={"endpoints": []})
        if request.url.path.endswith("/images/models"):
            return Response(
                200,
                json={
                    "data": [
                        {
                            "id": model_id,
                            "name": "Recraft V4 Styles Pro Vector",
                            "architecture": {
                                "input_modalities": ["text", "image"],
                                "output_modalities": ["image"],
                            },
                            "supported_parameters": {
                                "output_format": {
                                    "type": "enum",
                                    "values": ["svg"],
                                },
                                "n": {"type": "range", "min": 1, "max": 6},
                                "input_references": {
                                    "type": "range",
                                    "min": 1,
                                    "max": 10,
                                },
                            },
                            "supports_streaming": False,
                        }
                    ]
                },
            )
        return Response(200, json={"data": []})

    catalog = ImageCatalogService(
        openrouter_service(tmp_path),
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(catalog_handler)
        ),
    )
    submitted: list[dict[str, object]] = []

    def generation_handler(request: Request) -> Response:
        submitted.append(httpx.Response(200, content=request.content).json())
        return Response(
            200,
            headers={"x-request-id": "req_recraft_svg"},
            json={
                "model": model_id,
                "data": [
                    {
                        "b64_json": base64.b64encode(SVG_BYTES).decode(),
                        "media_type": "image/svg+xml",
                    }
                ],
            },
        )

    service = ImageGenerationService(
        catalog,
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(generation_handler)
        ),
    )

    with pytest.raises(MultimodalServiceError) as missing:
        await service.generate(
            model_id=model_id,
            prompt="测试风格",
            output_format="svg",
            reference_filenames=[],
            reference_content_types=[],
            reference_contents=[],
        )
    assert missing.value.code == "not_enough_image_references"

    with pytest.raises(MultimodalServiceError) as too_small:
        await service.generate(
            model_id=model_id,
            prompt="测试风格",
            output_format="svg",
            reference_filenames=["small.png"],
            reference_content_types=["image/png"],
            reference_contents=[png_reference(128)],
        )
    assert too_small.value.code == "image_reference_too_small"

    result = await service.generate(
        model_id=model_id,
        prompt="测试风格",
        n=2,
        output_format="svg",
        reference_filenames=["style.png"],
        reference_content_types=["image/png"],
        reference_contents=[png_reference(256)],
    )

    assert result.request_id == "req_recraft_svg"
    assert result.images[0].media_type == "image/svg+xml"
    assert submitted[0]["model"] == model_id
    assert submitted[0]["n"] == 2
    assert submitted[0]["output_format"] == "svg"
    assert len(submitted[0]["input_references"]) == 1


@pytest.mark.asyncio
async def test_grok_imagine_image_2_uses_dedicated_openrouter_contract(
    tmp_path: Path,
) -> None:
    def catalog_handler(request: Request) -> Response:
        if request.url.path.endswith(
            "/images/models/x-ai/grok-imagine-image-2.0/endpoints"
        ):
            return Response(
                200,
                json={
                    "id": "x-ai/grok-imagine-image-2.0",
                    "endpoints": [
                        {
                            "provider_name": "SpaceXAI",
                            "pricing": [
                                {
                                    "billable": "input_image",
                                    "unit": "image",
                                    "cost_usd": 0.01,
                                },
                                {
                                    "billable": "output_image",
                                    "unit": "image",
                                    "cost_usd": 0.04,
                                    "variant": "low_1k",
                                },
                                {
                                    "billable": "output_image",
                                    "unit": "image",
                                    "cost_usd": 0.08,
                                    "variant": "medium_2k",
                                },
                            ],
                        }
                    ],
                },
            )
        if request.url.path.endswith("/images/models"):
            return Response(
                200,
                json={
                    "data": [
                        {
                            "id": "x-ai/grok-imagine-image-2.0",
                            "name": "xAI: Grok Imagine Image 2.0",
                            "architecture": {
                                "input_modalities": ["text", "image"],
                                "output_modalities": ["image"],
                            },
                            "supported_parameters": {
                                "resolution": {
                                    "type": "enum",
                                    "values": ["1K", "2K"],
                                },
                                "aspect_ratio": {
                                    "type": "enum",
                                    "values": ["1:1", "16:9", "auto"],
                                },
                                "quality": {
                                    "type": "enum",
                                    "values": ["low", "medium"],
                                },
                                "n": {"type": "range", "min": 1, "max": 1},
                                "input_references": {
                                    "type": "range",
                                    "min": 0,
                                    "max": 3,
                                },
                            },
                            "supports_streaming": False,
                        }
                    ]
                },
            )
        return Response(200, json={"data": []})

    catalog = ImageCatalogService(
        openrouter_service(tmp_path),
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(catalog_handler)
        ),
    )
    catalog_result = await catalog.get_catalog()
    profile = next(
        item
        for item in catalog_result.profiles
        if item.model_id == "x-ai/grok-imagine-image-2.0"
    )
    assert profile.operation == "generate_image"
    assert profile.supports_streaming is False
    assert profile.supported_parameters["quality"].values == ["low", "medium"]
    assert profile.supported_parameters["n"].max == 1
    assert profile.supported_parameters["input_references"].max == 3
    assert [item.model_dump() for item in profile.pricing] == [
        {
            "billable": "input_image",
            "unit": "image",
            "cost_usd": 0.01,
            "variant": None,
        },
        {
            "billable": "output_image",
            "unit": "image",
            "cost_usd": 0.04,
            "variant": "low_1k",
        },
        {
            "billable": "output_image",
            "unit": "image",
            "cost_usd": 0.08,
            "variant": "medium_2k",
        },
    ]

    submitted: list[tuple[str, dict[str, object]]] = []

    def generation_handler(request: Request) -> Response:
        submitted.append(
            (
                request.url.path,
                httpx.Response(200, content=request.content).json(),
            )
        )
        return Response(
            200,
            headers={"x-request-id": "req_grok_image_2"},
            json={
                "model": "x-ai/grok-imagine-image-2.0",
                "data": [
                    {"b64_json": base64.b64encode(PNG_BYTES).decode()}
                ],
                "usage": {"cost": 0.08},
            },
        )

    service = ImageGenerationService(
        catalog,
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(generation_handler)
        ),
    )
    result = await service.generate(
        model_id="x-ai/grok-imagine-image-2.0",
        prompt="一座霓虹城市",
        n=1,
        resolution="2K",
        aspect_ratio="16:9",
        quality="medium",
        reference_filenames=["one.png", "two.png", "three.png"],
        reference_content_types=["image/png", "image/png", "image/png"],
        reference_contents=[PNG_BYTES, PNG_BYTES, PNG_BYTES],
    )

    assert result.request_id == "req_grok_image_2"
    assert result.usage.cost_usd == 0.08
    assert submitted[0][0].endswith("/images")
    assert submitted[0][1]["model"] == "x-ai/grok-imagine-image-2.0"
    assert submitted[0][1]["resolution"] == "2K"
    assert submitted[0][1]["aspect_ratio"] == "16:9"
    assert submitted[0][1]["quality"] == "medium"
    assert "stream" not in submitted[0][1]
    assert len(submitted[0][1]["input_references"]) == 3

    with pytest.raises(MultimodalServiceError) as error:
        await service.generate(
            model_id="x-ai/grok-imagine-image-2.0",
            prompt="测试数量限制",
            n=2,
            reference_filenames=[],
            reference_content_types=[],
            reference_contents=[],
        )
    assert error.value.code == "invalid_image_parameter"
    assert len(submitted) == 1


@pytest.mark.asyncio
async def test_image_generation_rejects_unsupported_parameter_before_request(
    tmp_path: Path,
) -> None:
    catalog = ImageCatalogService(
        openrouter_service(tmp_path),
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(image_catalog_handler)
        ),
    )
    service = ImageGenerationService(catalog)

    with pytest.raises(MultimodalServiceError) as error:
        await service.generate(
            model_id="qwen/image-test",
            prompt="测试",
            quality="high",
            reference_filenames=[],
            reference_content_types=[],
            reference_contents=[],
        )

    assert error.value.code == "unsupported_image_parameter"


@pytest.mark.asyncio
async def test_image_generation_rejects_damaged_output(
    tmp_path: Path,
) -> None:
    catalog = ImageCatalogService(
        openrouter_service(tmp_path),
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(image_catalog_handler)
        ),
    )

    def handler(_: Request) -> Response:
        return Response(
            200,
            json={
                "data": [
                    {
                        "b64_json": base64.b64encode(b"not-a-png").decode(),
                        "media_type": "image/png",
                    }
                ]
            },
        )

    service = ImageGenerationService(
        catalog,
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(handler)
        ),
    )
    with pytest.raises(MultimodalServiceError) as error:
        await service.generate(
            model_id="qwen/image-test",
            prompt="测试",
            reference_filenames=[],
            reference_content_types=[],
            reference_contents=[],
        )

    assert error.value.code == "damaged_image_response"
