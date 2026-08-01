"""JSON-file-backed store for world-generation records.

Mirrors the RAG metadata.json pattern: a single JSON file holding all
records. Not suitable for heavy concurrency, but fine for the MVP.
Asset URLs may expire — always persist ``world_id`` / ``provider_world_id``
so assets can be re-fetched later.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from .models import GeneratedAsset, GeneratedWorld, WorldJob, WorldStatus


class WorldStore:
    """Persist and query world-generation records."""

    def __init__(self, storage_path: Path | None = None) -> None:
        root = Path(__file__).resolve().parent
        self.storage_path = storage_path or Path(
            os.getenv("WORLD_STORAGE_DIR", str(root / "storage" / "world_records.json"))
        )
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Internal read/write
    # ------------------------------------------------------------------
    def _read_all(self) -> dict[str, dict[str, Any]]:
        if not self.storage_path.exists():
            return {}
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_all(self, data: dict[str, dict[str, Any]]) -> None:
        self.storage_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def save_job(self, job: WorldJob, provider: str | None = None) -> None:
        """Create (or update) a record for a job."""

        with self._lock:
            records = self._read_all()
            existing = records.get(job.job_id, {})
            existing.update(
                {
                    "job_id": job.job_id,
                    "provider_job_id": job.provider_job_id,
                    "status": job.status,
                    "created_at": job.created_at,
                }
            )
            if provider:
                existing["provider"] = provider
            records[job.job_id] = existing
            self._write_all(records)

    def update_status(self, job_id: str, status: WorldStatus) -> None:
        with self._lock:
            records = self._read_all()
            if job_id in records:
                records[job_id]["status"] = status
                self._write_all(records)

    def attach_world(self, job_id: str, world: GeneratedWorld) -> None:
        """Attach the completed world (assets, caption, etc.) to a job."""

        with self._lock:
            records = self._read_all()
            if job_id not in records:
                return
            records[job_id].update(
                {
                    "status": world.status,
                    "world_id": world.provider_world_id or world.id,
                    "assets": [
                        {
                            "id": a.id,
                            "kind": a.kind,
                            "format": a.format,
                            "url": a.url,
                            "size_bytes": a.size_bytes,
                        }
                        for a in world.assets
                    ],
                    "preview_url": world.preview_url,
                    "caption": world.caption,
                    "credits": world.credits,
                    "estimated_cost_usd": world.estimated_cost_usd,
                    "completed_at": world.completed_at,
                }
            )
            self._write_all(records)

    def add_asset(self, job_id: str, asset: GeneratedAsset) -> None:
        """Persist an explicitly requested export without replacing other assets."""

        with self._lock:
            records = self._read_all()
            if job_id not in records:
                return
            assets = records[job_id].setdefault("assets", [])
            assets[:] = [item for item in assets if item.get("id") != asset.id]
            assets.append(
                {
                    "id": asset.id,
                    "kind": asset.kind,
                    "format": asset.format,
                    "url": asset.url,
                    "size_bytes": asset.size_bytes,
                }
            )
            self._write_all(records)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            records = self._read_all()
            record = records.get(job_id)
            return dict(record) if record else None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            records = self._read_all()
            return [
                dict(record) for record in sorted(
                    records.values(), key=lambda r: r.get("created_at", ""), reverse=True
                )
            ]

    def _new_job_id(self) -> str:
        return f"job-{uuid.uuid4().hex[:12]}"
