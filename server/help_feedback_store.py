"""帮助中心文章反馈的文件型 JSON Store。

用户对帮助文章的"有帮助 / 没帮助"评价写入本地 JSON 文件，
供维护者查询统计，并在文章底部展示实时评价标签。

防重复：同一 anonymous_id 对同一 slug 只能评价一次（DuplicateFeedbackError）。
数据最小化：只记录 slug、article_version、value、anonymous_id、created_at，
不记录 IP、真实身份或任何正文内容。anonymous_id 是前端生成的随机 UUID，
无法反查到具体用户。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any


class DuplicateFeedbackError(Exception):
    """同一 anonymous_id 对同一 slug 已评价过，拒绝重复写入。"""


class HelpFeedbackStore:
    """原子文件型反馈 Store。"""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("HELP_FEEDBACK_STORAGE_DIR", "").strip()
            or package_dir / "storage" / "help_feedback"
        )
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.storage_dir / "feedback.json"
        self._lock = threading.RLock()
        self._ensure_unlocked()

    def _ensure_unlocked(self) -> None:
        if not self.path.exists():
            self._write_unlocked([])

    def _read_unlocked(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _write_unlocked(self, items: list[dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)

    def add_feedback(
        self,
        *,
        slug: str,
        article_version: str,
        value: str,
        anonymous_id: str,
    ) -> dict[str, Any]:
        """写入一条评价。同一 anonymous_id + slug 已存在时抛 DuplicateFeedbackError。"""
        with self._lock:
            items = self._read_unlocked()
            for item in items:
                if (
                    item.get("anonymous_id") == anonymous_id
                    and item.get("slug") == slug
                ):
                    raise DuplicateFeedbackError(slug)
            record = {
                "id": f"{int(time.time() * 1000)}-{len(items)}",
                "slug": slug,
                "article_version": article_version,
                "value": value,
                "anonymous_id": anonymous_id,
                "created_at": int(time.time()),
            }
            items.append(record)
            self._write_unlocked(items)
            return record

    def stats(self, *, slug: str | None = None) -> dict[str, Any]:
        """返回评价统计。slug 为 None 时统计全部，否则统计指定文章。"""
        with self._lock:
            items = self._read_unlocked()
        if slug:
            items = [item for item in items if item.get("slug") == slug]
        total = len(items)
        helpful = sum(1 for item in items if item.get("value") == "helpful")
        return {"slug": slug, "total": total, "helpful": helpful}
