from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Callable

from .app_models import (
    XpertAppAccessGrant,
    XpertAppApiKey,
    XpertAppDefinition,
    XpertAppDeploymentRecord,
    XpertAppLimits,
    XpertAppPolicy,
)


APP_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class XpertAppError(Exception):
    """Base error raised by the Xpert App store."""


class XpertAppNotFoundError(XpertAppError):
    """Raised when an App deployment does not exist."""


class XpertAppValidationError(XpertAppError):
    """Raised when App metadata or a credential is invalid."""


class XpertAppConflictError(XpertAppError):
    """Raised when an App or slug already exists."""


class XpertAppAuthenticationError(XpertAppError):
    """Raised when a public credential cannot be authenticated."""


class XpertAppQuotaError(XpertAppError):
    """Raised when a public credential exceeds its configured quota."""


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_secret(prefix: str) -> str:
    return f"{prefix}{secrets.token_urlsafe(32)}"


def _secret_prefix(value: str) -> str:
    return value[:16]


def _utc_day(timestamp: float | None = None) -> str:
    return datetime.fromtimestamp(timestamp or time.time(), timezone.utc).date().isoformat()


class XpertAppStore:
    """Filesystem-backed Xpert App deployments and credential metadata."""

    def __init__(self, storage_dir: Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("XPERT_STORAGE_DIR")
            or package_dir / "storage"
        )
        self.storage_path = self.storage_dir / "xpert_apps.json"
        self._lock = threading.RLock()

    def create_app(
        self,
        *,
        xpert_id: str,
        slug: str,
        name: str,
        description: str = "",
        starters: list[str] | None = None,
    ) -> tuple[XpertAppDefinition, str]:
        clean_slug = self._normalize_slug(slug)
        clean_name = name.strip()
        if not clean_name:
            raise XpertAppValidationError("App name is required.")
        share_token = _new_secret("mmshare_")
        now = time.time()
        with self._lock:
            apps = self._read_unlocked()
            if any(item.xpert_id == xpert_id for item in apps):
                raise XpertAppConflictError("This Xpert already has an App deployment.")
            if any(item.slug == clean_slug for item in apps):
                raise XpertAppConflictError(f"App slug already exists: {clean_slug}")
            app = XpertAppDefinition(
                app_id=str(uuid.uuid4()),
                xpert_id=xpert_id,
                slug=clean_slug,
                name=clean_name[:120],
                description=description.strip()[:2000],
                starters=self._clean_list(starters, max_items=8, max_length=500),
                share_token_prefix=_secret_prefix(share_token),
                share_token_hash=_token_hash(share_token),
                created_at=now,
                updated_at=now,
            )
            apps.append(app)
            self._write_unlocked(apps)
            return app.model_copy(deep=True), share_token

    def get_app(self, app_id: str) -> XpertAppDefinition:
        with self._lock:
            return self._find_app_unlocked(self._read_unlocked(), app_id).model_copy(deep=True)

    def get_app_for_xpert(self, xpert_id: str) -> XpertAppDefinition:
        with self._lock:
            for app in self._read_unlocked():
                if app.xpert_id == xpert_id:
                    return app.model_copy(deep=True)
        raise XpertAppNotFoundError(f"Xpert App not found for Xpert: {xpert_id}")

    def resolve_app(self, slug: str) -> XpertAppDefinition:
        clean_slug = slug.strip().lower()
        with self._lock:
            for app in self._read_unlocked():
                if app.slug == clean_slug:
                    return app.model_copy(deep=True)
        raise XpertAppNotFoundError(f"Xpert App not found: {clean_slug}")

    def update_app(self, app_id: str, patch: dict) -> XpertAppDefinition:
        with self._lock:
            apps = self._read_unlocked()
            app = self._find_app_unlocked(apps, app_id)
            if patch.get("name") is not None:
                name = str(patch["name"]).strip()
                if not name:
                    raise XpertAppValidationError("App name is required.")
                app.name = name[:120]
            if patch.get("description") is not None:
                app.description = str(patch["description"]).strip()[:2000]
            if patch.get("starters") is not None:
                app.starters = self._clean_list(
                    patch["starters"], max_items=8, max_length=500
                )
            if patch.get("policy") is not None:
                app.policy = XpertAppPolicy.model_validate(patch["policy"])
            if patch.get("limits") is not None:
                app.limits = XpertAppLimits.model_validate(patch["limits"])
            app.updated_at = time.time()
            self._write_unlocked(apps)
            return app.model_copy(deep=True)

    def deploy_app(
        self,
        app_id: str,
        *,
        version: int,
        release_notes: str = "",
    ) -> XpertAppDefinition:
        now = time.time()
        with self._lock:
            apps = self._read_unlocked()
            app = self._find_app_unlocked(apps, app_id)
            app.deployment_revision += 1
            app.pinned_version = version
            app.status = "active"
            app.deployments.append(
                XpertAppDeploymentRecord(
                    revision=app.deployment_revision,
                    version=version,
                    release_notes=release_notes.strip()[:1000],
                    deployed_at=now,
                )
            )
            app.updated_at = now
            self._write_unlocked(apps)
            return app.model_copy(deep=True)

    def disable_app(self, app_id: str) -> XpertAppDefinition:
        with self._lock:
            apps = self._read_unlocked()
            app = self._find_app_unlocked(apps, app_id)
            app.status = "disabled"
            app.updated_at = time.time()
            self._write_unlocked(apps)
            return app.model_copy(deep=True)

    def rotate_share_token(self, app_id: str) -> tuple[XpertAppDefinition, str]:
        token = _new_secret("mmshare_")
        with self._lock:
            apps = self._read_unlocked()
            app = self._find_app_unlocked(apps, app_id)
            app.share_token_prefix = _secret_prefix(token)
            app.share_token_hash = _token_hash(token)
            app.share_usage_day = ""
            app.share_requests_today = 0
            app.share_last_used_at = None
            app.updated_at = time.time()
            self._write_unlocked(apps)
            return app.model_copy(deep=True), token

    def create_api_key(
        self,
        app_id: str,
        *,
        name: str,
        limits: XpertAppLimits | None = None,
        expires_at: float | None = None,
    ) -> tuple[XpertAppDefinition, XpertAppApiKey, str]:
        clean_name = name.strip()
        if not clean_name:
            raise XpertAppValidationError("API key name is required.")
        if expires_at is not None and expires_at <= time.time():
            raise XpertAppValidationError("API key expiry must be in the future.")
        token = _new_secret("mmapp_")
        now = time.time()
        with self._lock:
            apps = self._read_unlocked()
            app = self._find_app_unlocked(apps, app_id)
            key = XpertAppApiKey(
                key_id=str(uuid.uuid4()),
                name=clean_name[:80],
                prefix=_secret_prefix(token),
                key_hash=_token_hash(token),
                limits=(limits or app.limits).model_copy(deep=True),
                created_at=now,
                expires_at=expires_at,
            )
            app.api_keys.append(key)
            app.updated_at = now
            self._write_unlocked(apps)
            return app.model_copy(deep=True), key.model_copy(deep=True), token

    def revoke_api_key(self, app_id: str, key_id: str) -> XpertAppApiKey:
        with self._lock:
            apps = self._read_unlocked()
            app = self._find_app_unlocked(apps, app_id)
            key = self._find_key_unlocked(app, key_id)
            if key.revoked_at is None:
                key.revoked_at = time.time()
                app.updated_at = key.revoked_at
                self._write_unlocked(apps)
            return key.model_copy(deep=True)

    def authenticate(
        self,
        slug: str,
        credential: str,
        *,
        access_type: str,
        now: float | None = None,
    ) -> XpertAppAccessGrant:
        timestamp = now or time.time()
        day = _utc_day(timestamp)
        credential_hash = _token_hash(credential)
        with self._lock:
            apps = self._read_unlocked()
            app = next((item for item in apps if item.slug == slug), None)
            if app is None or app.status != "active" or app.pinned_version is None:
                raise XpertAppAuthenticationError("App access denied.")
            if access_type == "share":
                if not hmac.compare_digest(app.share_token_hash, credential_hash):
                    raise XpertAppAuthenticationError("App access denied.")
                if app.share_usage_day != day:
                    app.share_usage_day = day
                    app.share_requests_today = 0
                if app.share_requests_today >= app.limits.requests_per_day:
                    raise XpertAppQuotaError("Daily App request quota exceeded.")
                app.share_requests_today += 1
                app.share_last_used_at = timestamp
                grant = XpertAppAccessGrant(
                    app_id=app.app_id,
                    app_slug=app.slug,
                    access_type="share",
                    credential_id="share",
                    credential_prefix=app.share_token_prefix,
                    limits=app.limits,
                    requests_today=app.share_requests_today,
                )
            elif access_type == "api_key":
                key = next(
                    (
                        item
                        for item in app.api_keys
                        if item.revoked_at is None
                        and (item.expires_at is None or item.expires_at > timestamp)
                        and hmac.compare_digest(item.key_hash, credential_hash)
                    ),
                    None,
                )
                if key is None:
                    raise XpertAppAuthenticationError("App access denied.")
                if key.usage_day != day:
                    key.usage_day = day
                    key.requests_today = 0
                if key.requests_today >= key.limits.requests_per_day:
                    raise XpertAppQuotaError("Daily API key quota exceeded.")
                key.requests_today += 1
                key.last_used_at = timestamp
                grant = XpertAppAccessGrant(
                    app_id=app.app_id,
                    app_slug=app.slug,
                    access_type="api_key",
                    credential_id=key.key_id,
                    credential_prefix=key.prefix,
                    limits=key.limits,
                    requests_today=key.requests_today,
                )
            else:
                raise XpertAppAuthenticationError("App access denied.")
            app.updated_at = timestamp
            self._write_unlocked(apps)
            return grant

    def app_payload(self, app: XpertAppDefinition) -> dict:
        payload = app.model_dump(mode="json")
        payload.pop("share_token_hash", None)
        for key in payload.get("api_keys", []):
            key.pop("key_hash", None)
        return payload

    @staticmethod
    def key_payload(key: XpertAppApiKey) -> dict:
        payload = key.model_dump(mode="json")
        payload.pop("key_hash", None)
        return payload

    def _ensure_storage_unlocked(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        if not self.storage_path.exists():
            self._write_unlocked([])

    def _read_unlocked(self) -> list[XpertAppDefinition]:
        self._ensure_storage_unlocked()
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
            raw_items = payload.get("apps", []) if isinstance(payload, dict) else []
            return [XpertAppDefinition.model_validate(item) for item in raw_items]
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise XpertAppError("Xpert App storage is unreadable.") from exc

    def _write_unlocked(self, apps: list[XpertAppDefinition]) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "apps": [app.model_dump(mode="json") for app in apps],
        }
        temp_path = self.storage_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.storage_path)

    @staticmethod
    def _find_app_unlocked(
        apps: list[XpertAppDefinition], app_id: str
    ) -> XpertAppDefinition:
        for app in apps:
            if app.app_id == app_id:
                return app
        raise XpertAppNotFoundError(f"Xpert App not found: {app_id}")

    @staticmethod
    def _find_key_unlocked(app: XpertAppDefinition, key_id: str) -> XpertAppApiKey:
        for key in app.api_keys:
            if key.key_id == key_id:
                return key
        raise XpertAppNotFoundError(f"Xpert App API key not found: {key_id}")

    @staticmethod
    def _normalize_slug(value: str) -> str:
        slug = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower()).strip("-_")
        slug = slug[:64]
        if not slug or not APP_SLUG_PATTERN.fullmatch(slug):
            raise XpertAppValidationError(
                "App slug must use lowercase letters, numbers, '-' or '_'."
            )
        return slug

    @staticmethod
    def _clean_list(
        values: list[str] | None,
        *,
        max_items: int,
        max_length: int,
    ) -> list[str]:
        result: list[str] = []
        for value in values or []:
            clean = str(value).strip()[:max_length]
            if clean and clean not in result:
                result.append(clean)
            if len(result) >= max_items:
                break
        return result


class XpertAppAccessController:
    """Single-process RPM and concurrency guard around persisted daily quotas."""

    def __init__(
        self,
        store: XpertAppStore,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.clock = clock
        self._lock = asyncio.Lock()
        self._windows: dict[str, deque[float]] = defaultdict(deque)
        self._active: dict[str, int] = defaultdict(int)

    async def authorize(
        self,
        slug: str,
        credential: str,
        *,
        access_type: str,
    ) -> XpertAppAccessGrant:
        grant = await asyncio.to_thread(
            self.store.authenticate,
            slug,
            credential,
            access_type=access_type,
        )
        identity = f"{grant.app_id}:{grant.credential_id}"
        now = self.clock()
        async with self._lock:
            window = self._windows[identity]
            while window and now - window[0] > 60:
                window.popleft()
            if len(window) >= grant.limits.requests_per_minute:
                raise XpertAppQuotaError("Requests per minute quota exceeded.")
            if self._active[identity] >= grant.limits.max_concurrency:
                raise XpertAppQuotaError("Concurrent request quota exceeded.")
            window.append(now)
            self._active[identity] += 1
        return grant

    async def release(self, grant: XpertAppAccessGrant) -> None:
        identity = f"{grant.app_id}:{grant.credential_id}"
        async with self._lock:
            self._active[identity] = max(0, self._active[identity] - 1)

    @asynccontextmanager
    async def access(
        self,
        slug: str,
        credential: str,
        *,
        access_type: str,
    ) -> AsyncIterator[XpertAppAccessGrant]:
        grant = await self.authorize(slug, credential, access_type=access_type)
        try:
            yield grant
        finally:
            await self.release(grant)
