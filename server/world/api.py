"""REST API for world generation.

Endpoints:
  POST /api/world-generations           create a generation task
  GET  /api/world-generations/:id       query task status
  GET  /api/world-generations/:id/assets  get the generated assets
  GET  /api/world-generations/provider   which provider is active (mock/marble)
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from .models import GeneratedAsset, WorldInput, WorldInputType
from .providers.marble import MarbleProviderError
from .registry import WorldRegistry
from .store import WorldStore

router = APIRouter(prefix="/api/world-generations", tags=["world-generations"])

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB
SUPPORTED_IMAGE_EXTS = {"jpg", "jpeg", "png", "webp"}
SUPPORTED_VIDEO_EXTS = {"mp4", "mov", "webm", "mkv", "avi"}
SUPPORTED_EXTS = SUPPORTED_IMAGE_EXTS | SUPPORTED_VIDEO_EXTS

_store = WorldStore()
_provider_cache: dict[str, Any] = {}


class JobResponse(BaseModel):
    job_id: str
    status: str
    provider: str


class AssetsResponse(BaseModel):
    assets: list[dict[str, Any]]


def active_provider_name() -> str:
    """Resolve the active provider name from env (default mock)."""

    return os.getenv("WORLD_PROVIDER", "mock").strip().lower()


def get_provider():
    """Return a cached provider instance so state (mock timing, client pool)
    survives across requests. Rebuilds if the env name changes."""

    name = active_provider_name()
    cached = _provider_cache.get("name")
    if cached == name:
        return _provider_cache["instance"]
    provider_cls = WorldRegistry.get_provider(name)
    instance = provider_cls()
    _provider_cache["name"] = name
    _provider_cache["instance"] = instance
    return instance


def set_provider_for_tests(provider: Any) -> None:
    """Force a provider instance (or name) for tests, bypassing env."""

    if isinstance(provider, str):
        instance = WorldRegistry.get_provider(provider)()
    else:
        instance = provider
    _provider_cache["name"] = instance.provider_name
    _provider_cache["instance"] = instance
    os.environ["WORLD_PROVIDER"] = instance.provider_name


def set_world_store_for_tests(store: WorldStore) -> None:
    """Replace the global store (test helper)."""

    global _store
    _store = store


def _validate_uploads(
    files: list[UploadFile], input_type: WorldInputType
) -> list[str]:
    """Validate file types/sizes and return their saved temp paths."""

    if not files:
        raise HTTPException(status_code=400, detail="请至少选择一个文件。")
    if input_type in {"image", "video"} and len(files) != 1:
        raise HTTPException(status_code=400, detail="单图或视频模式只能上传 1 个文件。")
    if input_type == "multi_image" and len(files) > 8:
        raise HTTPException(status_code=400, detail="多图模式最多上传 8 张图片。")

    saved: list[str] = []
    for upload in files:
        filename = Path((upload.filename or "upload").replace("\\", "/")).name
        ext = Path(filename).suffix.lstrip(".").lower()
        allowed_exts = (
            SUPPORTED_IMAGE_EXTS
            if input_type in {"image", "multi_image"}
            else SUPPORTED_VIDEO_EXTS
        )
        if ext not in allowed_exts:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{input_type} 模式不支持 .{ext} 文件；"
                    f"允许：{', '.join(sorted(allowed_exts))}。"
                ),
            )
        content = upload.file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="文件过大，请上传 50MB 以内的素材。")
        # Persist to a temp file for the provider.
        tmp_dir = Path(tempfile.gettempdir()) / "modelmirror-world"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / f"{time.time_ns()}-{filename}"
        tmp_path.write_bytes(content)
        saved.append(str(tmp_path))
    return saved


def _cleanup_temp(paths: list[str]) -> None:
    for path in paths:
        try:
            p = Path(path)
            if p.exists() and p.parent.name == "modelmirror-world":
                p.unlink()
        except OSError:
            pass


@router.post("", response_model=JobResponse)
async def create_world_generation(
    input_type: WorldInputType = "image",
    prompt: str | None = None,
    files: list[UploadFile] = File(...),
) -> JobResponse:
    saved_paths = _validate_uploads(files, input_type)

    try:
        provider = get_provider()
        world_input = WorldInput(
            type=input_type,
            source_file_ids=[Path(p).name for p in saved_paths],
            prompt=prompt,
        )
        job = await provider.create_world(world_input, [Path(p) for p in saved_paths])
        provider_name = active_provider_name()
        _store.save_job(job, provider=provider_name)
        return JobResponse(
            job_id=job.job_id, status=job.status, provider=provider_name
        )
    except MarbleProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"创建生成任务失败：{exc}") from exc
    finally:
        _cleanup_temp(saved_paths)


@router.get("/provider")
async def world_provider_info() -> dict[str, str]:
    return {"provider": active_provider_name()}


@router.get("/{job_id}")
async def get_world_generation(job_id: str) -> dict[str, Any]:
    record = _store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="生成任务不存在。")

    # If the job finished with a world but assets were never attached, refresh.
    if (
        record.get("status") in {"succeeded", "processing", "submitted"}
        and not record.get("assets")
        and record.get("provider_job_id")
    ):
        try:
            provider = get_provider()
            status = await provider.get_job_status(record["provider_job_id"])
            record["status"] = status
            _store.update_status(job_id, status)
            if status == "succeeded":
                # Resolve the provider's world id (differs from the job id for
                # Marble), then fetch assets and attach them to the record.
                world_id = await provider.get_world_id(record["provider_job_id"])
                if world_id:
                    world = await provider.get_world(world_id)
                    _store.attach_world(job_id, world)
                    record = _store.get(job_id) or record
        except MarbleProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="世界生成状态暂时不可用。") from exc

    return record


@router.get("/{job_id}/assets", response_model=AssetsResponse)
async def get_world_assets(job_id: str) -> AssetsResponse:
    record = _store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="生成任务不存在。")
    assets = record.get("assets", [])
    return AssetsResponse(assets=assets)


@router.post("/{job_id}/exports/ply")
async def export_world_ply(job_id: str) -> dict[str, Any]:
    """Explicitly request a potentially billable PLY export."""

    record = _store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="生成任务不存在。")
    if record.get("status") != "succeeded" or not record.get("world_id"):
        raise HTTPException(status_code=409, detail="世界尚未生成完成。")
    if record.get("provider") != "marble":
        raise HTTPException(status_code=400, detail="当前生成服务不支持 PLY 导出。")

    provider = get_provider()
    try:
        url = await provider.export_ply(record["world_id"])
    except MarbleProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    asset = GeneratedAsset(
        id=f"{record['world_id']}-ply",
        kind="gaussian_splat",
        format="ply",
        url=url,
    )
    _store.add_asset(job_id, asset)
    return {"asset": asset.model_dump()}
