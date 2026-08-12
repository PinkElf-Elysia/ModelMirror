from __future__ import annotations

import base64
from pathlib import Path

import httpx
import pytest
from httpx import MockTransport, Request, Response

from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import RouterConnectionCreate
from server.model_router.service import ModelRouterService
from server.multimodal.image_catalog import ImageCatalogService
from server.multimodal.image_generation import ImageGenerationService
from server.multimodal.stt import MultimodalServiceError


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"image-payload"


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
    return ModelRouterService(repository)


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
