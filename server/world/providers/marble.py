"""World Labs Marble world provider.

Implements the verified Marble API flow:
  [1] media-assets:prepare_upload  -> media_asset_id + upload_url + headers
  [2] PUT binary file              -> asset ready
  [3] worlds:generate              -> operation_id
  [4] operations/{id} poll         -> done / world_id
  [5] worlds/{id}                  -> pano / spz / glb / thumbnail / caption
  [6] worlds/{id}:export (PLY)     -> download url (optional)

Authentication: header ``WLT-Api-Key``. Key comes from the environment
variable ``WORLD_LABS_API_KEY`` (never from client/frontend).
"""

from __future__ import annotations

import logging
import mimetypes
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from ..models import (
    AssetFormat,
    AssetKind,
    GeneratedAsset,
    GeneratedWorld,
    WorldInput,
    WorldJob,
    WorldStatus,
)
from ..provider import WorldProvider
from ..registry import register_provider

API_BASE = "https://api.worldlabs.ai"
MAX_POLL_SECONDS = 1800  # 30 minutes safety cap
POLL_START_INTERVAL = 10

logger = logging.getLogger("modelmirror.world.marble")


class MarbleProviderError(RuntimeError):
    """Raised when the Marble API rejects a request."""


def _mime_for(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _map_status(raw: str) -> WorldStatus:
    """Map Marble's raw status/progress text to our unified WorldStatus."""

    lowered = (raw or "").lower()
    if "fail" in lowered or "error" in lowered or "cancel" in lowered:
        return "failed"
    if "success" in lowered or "succeed" in lowered or "done" in lowered:
        return "succeeded"
    if "progress" in lowered or "process" in lowered or "pending" in lowered:
        return "processing"
    if "submit" in lowered:
        return "submitted"
    return "processing"


@register_provider(name="marble", priority=10)
class MarbleWorldProvider(WorldProvider):
    """Real World Labs Marble adapter (requires WORLD_LABS_API_KEY)."""

    def __init__(
        self,
        api_base: str = API_BASE,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key or os.getenv("WORLD_LABS_API_KEY", "").strip()
        self._client = client or httpx.AsyncClient(timeout=120)
        # Inject the auth header up front so every request (create_world,
        # get_job_status, get_world_id, get_world, export_ply) is authenticated.
        self._client.headers.update({"WLT-Api-Key": self.api_key})
        # world_id -> ply_url ("" = tried and failed) to avoid duplicate paid exports.
        self._ply_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # [1] Upload local file -> media_asset_id
    # ------------------------------------------------------------------
    async def _upload_file(self, path: Path, kind: str, extension: str) -> str:
        prep = await self._client.post(
            f"{self.api_base}/marble/v1/media-assets:prepare_upload",
            json={"file_name": path.name, "kind": kind, "extension": extension},
        )
        self._raise_for_status(prep, "media asset prepare_upload")
        body = prep.json()
        media_asset_id = body["media_asset"]["media_asset_id"]
        upload_url = body["upload_info"]["upload_url"]
        required_headers = body["upload_info"].get("required_headers", {})

        put_headers = {"Content-Type": _mime_for(path.name), **required_headers}
        upload_resp = await self._client.put(
            upload_url, content=path.read_bytes(), headers=put_headers
        )
        self._raise_for_status(upload_resp, "media asset upload")
        return media_asset_id

    # ------------------------------------------------------------------
    # [2] create_world
    # ------------------------------------------------------------------
    async def create_world(self, input_: WorldInput, files: list[Path]) -> WorldJob:
        if not self.api_key:
            raise MarbleProviderError(
                "WORLD_LABS_API_KEY 未配置。请在 server/.env 中设置后重试。"
            )

        # Upload each file and map to the Marble prompt shape.
        file_ids: list[str] = []
        for path in files:
            ext = path.suffix.lstrip(".").lower()
            kind = "image" if ext in {"jpg", "jpeg", "png", "webp"} else "video"
            file_ids.append(await self._upload_file(path, kind, ext))

        if input_.type == "image":
            image_prompt: dict[str, Any] = {
                "source": "media_asset",
                "media_asset_id": file_ids[0],
            }
            world_prompt: dict[str, Any] = {
                "type": "image",
                "image_prompt": image_prompt,
            }
        elif input_.type == "multi_image":
            world_prompt = {
                "type": "multi-image",
                "multi_image_prompt": [
                    {"azimuth": 0, "content": {"source": "media_asset", "media_asset_id": fid}}
                    for fid in file_ids
                ],
            }
        else:  # video
            world_prompt = {
                "type": "video",
                "video_prompt": {"source": "media_asset", "media_asset_id": file_ids[0]},
            }
        if input_.prompt:
            world_prompt["text_prompt"] = input_.prompt

        payload = {
            "display_name": "modelmirror-world",
            "model": "marble-1.1",
            "world_prompt": world_prompt,
        }
        resp = await self._client.post(
            f"{self.api_base}/marble/v1/worlds:generate",
            json=payload,
        )
        self._raise_for_status(resp, "worlds:generate")
        data = resp.json()
        return WorldJob(
            job_id=f"marble-{uuid.uuid4().hex[:12]}",
            provider_job_id=data["operation_id"],
            status="submitted",
        )

    # ------------------------------------------------------------------
    # [3] get_job_status
    # ------------------------------------------------------------------
    async def get_job_status(self, provider_job_id: str) -> WorldStatus:
        resp = await self._client.get(
            f"{self.api_base}/marble/v1/operations/{provider_job_id}"
        )
        self._raise_for_status(resp, "operations/{id}")
        data = resp.json()
        if data.get("error"):
            return "failed"
        if data.get("done"):
            return "succeeded"
        progress = data.get("metadata", {}).get("progress", {})
        return _map_status(progress.get("status", "IN_PROGRESS"))

    # ------------------------------------------------------------------
    # [3b] resolve world id from a finished operation
    # ------------------------------------------------------------------
    async def get_world_id(self, provider_job_id: str) -> str | None:
        resp = await self._client.get(
            f"{self.api_base}/marble/v1/operations/{provider_job_id}"
        )
        self._raise_for_status(resp, "operations/{id}")
        data = resp.json()
        if not data.get("done"):
            return None
        response = data.get("response")
        if isinstance(response, dict):
            return response.get("world_id")
        return None

    # ------------------------------------------------------------------
    # [4] get_world + assets
    # ------------------------------------------------------------------
    async def get_world(
        self,
        provider_world_id: str,
        *,
        include_ply: bool = True,
    ) -> GeneratedWorld:
        resp = await self._client.get(
            f"{self.api_base}/marble/v1/worlds/{provider_world_id}"
        )
        self._raise_for_status(resp, "worlds/{id}")
        data = resp.json()
        assets = data.get("assets", {})

        asset_list: list[GeneratedAsset] = []
        # Panorama
        pano_url = assets.get("imagery", {}).get("pano_url")
        if pano_url:
            asset_list.append(
                GeneratedAsset(id=f"{provider_world_id}-pano", kind="panorama", format="png", url=pano_url)
            )
        # SPZ (multiple resolutions)
        spz_urls = assets.get("splats", {}).get("spz_urls", {})
        if isinstance(spz_urls, dict):
            for resolution, url in spz_urls.items():
                if isinstance(url, str) and url:
                    asset_list.append(
                        GeneratedAsset(
                            id=f"{provider_world_id}-spz-{resolution}",
                            kind="gaussian_splat",
                            format="spz",
                            url=url,
                        )
                    )
        # GLB collider mesh
        glb_url = assets.get("mesh", {}).get("collider_mesh_url")
        if glb_url:
            asset_list.append(
                GeneratedAsset(id=f"{provider_world_id}-glb", kind="textured_mesh", format="glb", url=glb_url)
            )
        # PLY (requires an extra :export call — cached so it only runs once)
        if include_ply:
            ply_url = await self._cached_ply_url(provider_world_id)
            if ply_url:
                asset_list.append(
                    GeneratedAsset(
                        id=f"{provider_world_id}-ply",
                        kind="gaussian_splat",
                        format="ply",
                        url=ply_url,
                    )
                )

        cost = data.get("cost") or {}
        credits = cost.get("total_credits") if isinstance(cost, dict) else None

        return GeneratedWorld(
            id=provider_world_id,
            provider="marble",
            provider_world_id=provider_world_id,
            model="marble-1.1",
            status="succeeded",
            preview_url=assets.get("thumbnail_url"),
            caption=assets.get("caption"),
            assets=asset_list,
            credits=float(credits) if credits is not None else None,
        )

    # ------------------------------------------------------------------
    # PLY export with per-world cache (avoid duplicate paid exports)
    # ------------------------------------------------------------------
    async def _cached_ply_url(self, provider_world_id: str) -> str | None:
        cached = self._ply_cache.get(provider_world_id)
        if cached is not None:
            return cached or None  # "" means "already tried and failed"
        try:
            url = await self.export_ply(provider_world_id)
            self._ply_cache[provider_world_id] = url
            return url
        except Exception as exc:
            # PLY is an optional extra — do not break the whole world.
            self._ply_cache[provider_world_id] = ""
            logger.warning(
                "PLY export skipped for %s: %s", provider_world_id, exc
            )
            return None

    async def list_assets(self, provider_world_id: str) -> list[GeneratedAsset]:
        world = await self.get_world(provider_world_id)
        return world.assets

    # ------------------------------------------------------------------
    # [5] Export PLY (optional, costs credits)
    # ------------------------------------------------------------------
    async def export_ply(self, provider_world_id: str) -> str:
        resp = await self._client.post(
            f"{self.api_base}/marble/v1/worlds/{provider_world_id}:export",
            json={"asset_type": "splats", "format": "ply", "resolution": "full_res"},
        )
        self._raise_for_status(resp, "worlds:export")
        data = resp.json()
        if data.get("done"):
            url = data.get("response", {}).get("url")
            if not url:
                raise MarbleProviderError("PLY 导出未返回下载地址。")
            return url
        # Not done -> poll the returned operation.
        operation_id = data["operation_id"]
        url = await self._poll_for_export_url(operation_id)
        return url

    async def _poll_for_export_url(self, operation_id: str) -> str:
        start = time.monotonic()
        wait = POLL_START_INTERVAL
        while True:
            await asyncio_sleep(wait)
            resp = await self._client.get(
                f"{self.api_base}/marble/v1/operations/{operation_id}"
            )
            self._raise_for_status(resp, "operations/{id}")
            data = resp.json()
            if data.get("done"):
                url = data.get("response", {}).get("url")
                if not url:
                    raise MarbleProviderError("PLY 导出完成后未返回下载地址。")
                return url
            if time.monotonic() - start > MAX_POLL_SECONDS:
                raise MarbleProviderError("PLY 导出超时。")
            wait = min(wait * 2, 60)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _raise_for_status(resp: httpx.Response, what: str) -> None:
        if resp.status_code < 400:
            return
        detail = ""
        try:
            body = resp.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            detail = str(body.get("message") or body.get("error") or body.get("detail") or "")
        raise MarbleProviderError(
            f"Marble API {what} 失败: HTTP {resp.status_code} {detail}".strip()
        )


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
