"""Tests for the world-generation feature (mock provider end-to-end).

Covers: input-type validation, file validation, provider response mapping,
status mapping, error mapping, and the full mock API lifecycle.
"""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from server.main import app
from server.world.api import set_provider_for_tests, set_world_store_for_tests
from server.world.models import GeneratedAsset, GeneratedWorld, WorldInput
from server.world.providers.mock import MockWorldProvider
from server.world.providers.marble import MarbleWorldProvider
from server.world.store import WorldStore

# Shorten the mock so tests don't wait ~6 seconds.
_MOCK_FAST_STEPS = 2
_MOCK_FAST_STEP = 0.05


@pytest_asyncio.fixture
async def client(tmp_path: Path):
    store = WorldStore(tmp_path / "world_records.json")
    set_world_store_for_tests(store)

    # Inject a fast mock so the lifecycle finishes quickly.
    fast_mock = MockWorldProvider(steps=_MOCK_FAST_STEPS, step_seconds=_MOCK_FAST_STEP)
    set_provider_for_tests(fast_mock)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client

    set_world_store_for_tests(WorldStore(Path("__test_reset__") / "x.json"))


def _png_file(name: str = "test.png", size: int = 10) -> tuple[str, bytes, str]:
    return name, b"\x89PNG\r\n\x1a\n" + b"x" * size, "image/png"


async def create_job(client: httpx.AsyncClient, input_type: str = "image") -> str:
    response = await client.post(
        "/api/world-generations",
        params={"input_type": input_type},
        files={"files": _png_file()},
    )
    assert response.status_code == 200, response.text
    return response.json()["job_id"]


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_upload_rejects_unsupported_type(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/world-generations",
        params={"input_type": "image"},
        files={"files": ("bad.exe", b"MZ fake", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert "模式不支持 .exe" in response.json()["detail"]


@pytest.mark.asyncio
async def test_upload_rejects_no_files(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/world-generations",
        params={"input_type": "image"},
        files={},
    )
    assert response.status_code in {400, 422}


@pytest.mark.asyncio
async def test_upload_rejects_too_many_multi_images(
    client: httpx.AsyncClient,
) -> None:
    files = [("files", _png_file(f"{i}.png")) for i in range(9)]
    response = await client.post(
        "/api/world-generations",
        params={"input_type": "multi_image"},
        files=files,
    )
    assert response.status_code == 400
    assert "最多上传 8 张" in response.json()["detail"]


@pytest.mark.asyncio
async def test_upload_rejects_invalid_input_type(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/world-generations",
        params={"input_type": "banana"},
        files={"files": _png_file()},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_upload_rejects_mode_extension_mismatch(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/world-generations",
        params={"input_type": "image"},
        files={"files": ("clip.mp4", b"video", "video/mp4")},
    )
    assert response.status_code == 400
    assert "image 模式不支持 .mp4" in response.json()["detail"]


# ----------------------------------------------------------------------
# Full lifecycle
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mock_lifecycle_processing_to_succeeded(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/world-generations",
        params={"input_type": "image"},
        files={"files": _png_file()},
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    assert response.json()["status"] == "processing"

    # After mock finishes, status should flip to succeeded with assets.
    await asyncio_sleep(0.5)
    detail = await client.get(f"/api/world-generations/{job_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "succeeded"
    assert len(body["assets"]) >= 1

    assets_resp = await client.get(f"/api/world-generations/{job_id}/assets")
    assert assets_resp.status_code == 200
    assert len(assets_resp.json()["assets"]) >= 1


@pytest.mark.asyncio
async def test_mock_video_lifecycle(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/world-generations",
        params={"input_type": "video"},
        files={"files": ("clip.mp4", b"\x00\x00\x00\x18ftypmp42", "video/mp4")},
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    await asyncio_sleep(0.5)
    detail = await client.get(f"/api/world-generations/{job_id}")
    assert detail.json()["status"] == "succeeded"


@pytest.mark.asyncio
async def test_unknown_job_returns_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/world-generations/does-not-exist")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_provider_endpoint_reports_mock(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/world-generations/provider")
    assert response.status_code == 200
    assert response.json()["provider"] == "mock"


@pytest.mark.asyncio
async def test_mock_provider_rejects_ply_export(client: httpx.AsyncClient) -> None:
    job_id = await create_job(client)
    await asyncio_sleep(0.5)
    await client.get(f"/api/world-generations/{job_id}")
    response = await client.post(f"/api/world-generations/{job_id}/exports/ply")
    assert response.status_code == 400


# ----------------------------------------------------------------------
# Provider-level unit tests (mapping)
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mock_provider_get_world_asset_kinds() -> None:
    provider = MockWorldProvider()
    world = await provider.get_world("w-test")
    assert isinstance(world, GeneratedWorld)
    assert world.status == "succeeded"
    kinds = [a.kind for a in world.assets]
    assert "panorama" in kinds
    assert "textured_mesh" in kinds
    assert all(isinstance(a, GeneratedAsset) for a in world.assets)


@pytest.mark.asyncio
async def test_mock_provider_job_status_flow() -> None:
    provider = MockWorldProvider(steps=3, step_seconds=0.01)
    job = await provider.create_world(
        WorldInput(type="image", source_file_ids=["a"]), files=[]
    )
    # Immediately -> processing
    assert await provider.get_job_status(job.provider_job_id) in {"processing", "submitted"}
    await asyncio_sleep(0.1)
    assert await provider.get_job_status(job.provider_job_id) == "succeeded"
    # Unknown job -> failed
    assert await provider.get_job_status("unknown-op") == "failed"


@pytest.mark.asyncio
async def test_marble_contract_and_signed_upload_headers(tmp_path: Path) -> None:
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path.endswith("/media-assets:prepare_upload"):
            assert request.headers["WLT-Api-Key"] == "secret-key"
            return httpx.Response(
                200,
                json={
                    "media_asset": {"id": "media-1"},
                    "upload_info": {
                        "upload_url": "https://uploads.example/signed",
                        "required_headers": {"x-upload-token": "signed"},
                    },
                },
            )
        if request.url.host == "uploads.example":
            assert "WLT-Api-Key" not in request.headers
            assert request.headers["x-upload-token"] == "signed"
            return httpx.Response(200)
        if request.url.path.endswith("/worlds:generate"):
            assert request.headers["WLT-Api-Key"] == "secret-key"
            return httpx.Response(200, json={"operation_id": "operation-1"})
        if request.url.path.endswith("/operations/operation-1"):
            return httpx.Response(
                200,
                json={
                    "done": True,
                    "metadata": {"world_id": "world-from-metadata"},
                    "response": {"id": "world-1"},
                },
            )
        if request.url.path.endswith("/worlds/world-1"):
            return httpx.Response(
                200,
                json={
                    "world": {
                        "id": "world-1",
                        "assets": {
                            "caption": "Generated world",
                            "imagery": {"pano_url": "https://assets.example/pano.png"},
                            "splats": {"spz_urls": {"500k": "https://assets.example/world.spz"}},
                            "mesh": {"collider_mesh_url": "https://assets.example/world.glb"},
                        },
                    }
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as marble_client:
        provider = MarbleWorldProvider(
            api_key="secret-key",
            client=marble_client,
        )
        source = tmp_path / "scene.png"
        source.write_bytes(b"png")
        job = await provider.create_world(
            WorldInput(type="image", source_file_ids=["scene.png"]),
            [source],
        )
        assert job.provider_job_id == "operation-1"
        assert await provider.get_world_id("operation-1") == "world-1"
        world = await provider.get_world("world-1")

    assert world.caption == "Generated world"
    assert {asset.format for asset in world.assets} == {"png", "spz", "glb"}
    assert not any(path.endswith(":export") for path in seen_paths)


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
