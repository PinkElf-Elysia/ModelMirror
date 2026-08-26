from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
import math
import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlparse

from fastapi import HTTPException, Request, Response, status
from pydantic import BaseModel, Field, SecretStr


PAIRING_SECRET_ENV = "MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET"
COOKIE_NAME = "modelmirror_provider_admin"
COOKIE_PATH = "/api/router"
RAG_COOKIE_NAME = "modelmirror_rag_admin"
RAG_COOKIE_PATH = "/api/rag"
SESSION_TTL_SECONDS = 8 * 60 * 60
MIN_PAIRING_SECRET_CHARS = 32
MAX_SESSIONS = 128
MAX_FAILED_ATTEMPTS = 5
FAILED_ATTEMPT_WINDOW_SECONDS = 5 * 60
logger = logging.getLogger("modelmirror.provider_admin")


class AdminPairingRequest(BaseModel):
    pairing_secret: SecretStr = Field(min_length=1, max_length=4096)


class AdminSessionResponse(BaseModel):
    configured: bool
    authenticated: bool
    expires_at: float | None = None
    csrf_token: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderControlPrincipal:
    tenant_id: str = "local"
    role: str = "provider_admin"


@dataclass(slots=True)
class _Session:
    csrf_token: str
    expires_at: float
    principal: ProviderControlPrincipal


def _public_error(
    code: str,
    message: str,
    status_code: int,
    *,
    headers: dict[str, str] | None = None,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
        headers=headers,
    )


class ProviderAdminAuth:
    def __init__(self, pairing_secret: str | None = None) -> None:
        configured_secret = (
            pairing_secret
            if pairing_secret is not None
            else os.getenv(PAIRING_SECRET_ENV, "")
        ).strip()
        self._secret_digest = (
            hashlib.sha256(configured_secret.encode("utf-8")).digest()
            if len(configured_secret) >= MIN_PAIRING_SECRET_CHARS
            else None
        )
        self._sessions: dict[str, _Session] = {}
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.RLock()

    @property
    def configured(self) -> bool:
        return self._secret_digest is not None

    def status(self, request: Request) -> AdminSessionResponse:
        session = self._session_from_request(request)
        return AdminSessionResponse(
            configured=self.configured,
            authenticated=session is not None,
            expires_at=session.expires_at if session else None,
            csrf_token=session.csrf_token if session else None,
        )

    def pair(
        self,
        request: Request,
        response: Response,
        pairing_secret: str,
    ) -> AdminSessionResponse:
        if not self.configured:
            raise _public_error(
                "admin_pairing_not_configured",
                "Provider 管理面尚未配置配对密钥。",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        client_key = self._client_key(request)
        source = self._source_fingerprint(client_key)
        now = time.time()
        with self._lock:
            failures = self._active_failures(client_key, now)
            if len(failures) >= MAX_FAILED_ATTEMPTS:
                retry_after = max(
                    1,
                    math.ceil(
                        failures[0] + FAILED_ATTEMPT_WINDOW_SECONDS - now
                    ),
                )
                logger.warning("provider_admin_pairing_rate_limited source=%s", source)
                raise _public_error(
                    "admin_pairing_rate_limited",
                    "配对尝试过多，请稍后重试。",
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    headers={"Retry-After": str(retry_after)},
                )
            candidate = hashlib.sha256(pairing_secret.encode("utf-8")).digest()
            if not hmac.compare_digest(candidate, self._secret_digest or b""):
                failures.append(now)
                logger.warning("provider_admin_pairing_failed source=%s", source)
                raise _public_error(
                    "admin_pairing_failed",
                    "配对信息无效。",
                    status.HTTP_401_UNAUTHORIZED,
                )
            failures.clear()
            self._purge_expired(now)
            if len(self._sessions) >= MAX_SESSIONS:
                oldest = min(
                    self._sessions,
                    key=lambda key: self._sessions[key].expires_at,
                )
                self._sessions.pop(oldest, None)
            token = secrets.token_urlsafe(32)
            token_hash = self._token_hash(token)
            session = _Session(
                csrf_token=secrets.token_urlsafe(32),
                expires_at=now + SESSION_TTL_SECONDS,
                principal=ProviderControlPrincipal(),
            )
            self._sessions[token_hash] = session
        logger.info("provider_admin_pairing_succeeded source=%s", source)
        secure = self._secure_cookie_required(request)
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            secure=secure,
            samesite="strict",
            path=COOKIE_PATH,
        )
        response.set_cookie(
            RAG_COOKIE_NAME,
            token,
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            secure=secure,
            samesite="strict",
            path=RAG_COOKIE_PATH,
        )
        return AdminSessionResponse(
            configured=True,
            authenticated=True,
            expires_at=session.expires_at,
            csrf_token=session.csrf_token,
        )

    def require(self, request: Request) -> ProviderControlPrincipal:
        if not self.configured:
            raise _public_error(
                "admin_pairing_not_configured",
                "Provider 管理面尚未配置配对密钥。",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        session = self._session_from_request(request)
        if session is None:
            raise _public_error(
                "admin_session_required",
                "请先完成 Provider 管理员配对。",
                status.HTTP_401_UNAUTHORIZED,
            )
        return session.principal

    def require_csrf(self, request: Request) -> ProviderControlPrincipal:
        principal = self.require(request)
        session = self._session_from_request(request)
        supplied = request.headers.get("X-ModelMirror-CSRF", "")
        if session is None or not hmac.compare_digest(supplied, session.csrf_token):
            raise _public_error(
                "admin_csrf_invalid",
                "管理会话校验失败，请刷新页面后重试。",
                status.HTTP_403_FORBIDDEN,
            )
        return principal

    def logout(self, request: Request, response: Response) -> None:
        self.require_csrf(request)
        token = self._token_from_request(request)
        if token:
            with self._lock:
                self._sessions.pop(self._token_hash(token), None)
        logger.info(
            "provider_admin_session_ended source=%s",
            self._source_fingerprint(self._client_key(request)),
        )
        response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, samesite="strict")
        response.delete_cookie(RAG_COOKIE_NAME, path=RAG_COOKIE_PATH, samesite="strict")

    def _session_from_request(self, request: Request) -> _Session | None:
        token = self._token_from_request(request)
        if not token:
            return None
        now = time.time()
        token_hash = self._token_hash(token)
        with self._lock:
            self._purge_expired(now)
            return self._sessions.get(token_hash)

    @staticmethod
    def _token_from_request(request: Request) -> str:
        return str(
            request.cookies.get(COOKIE_NAME)
            or request.cookies.get(RAG_COOKIE_NAME)
            or ""
        )

    def _active_failures(self, client_key: str, now: float) -> deque[float]:
        failures = self._failures[client_key]
        cutoff = now - FAILED_ATTEMPT_WINDOW_SECONDS
        while failures and failures[0] <= cutoff:
            failures.popleft()
        return failures

    def _purge_expired(self, now: float) -> None:
        for token_hash in [
            key for key, session in self._sessions.items() if session.expires_at <= now
        ]:
            self._sessions.pop(token_hash, None)

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _client_key(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    @staticmethod
    def _source_fingerprint(client_key: str) -> str:
        return hashlib.sha256(client_key.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _secure_cookie_required(request: Request) -> bool:
        scheme = request.url.scheme.lower()
        origin = request.headers.get("origin", "").strip()
        origin_url = urlparse(origin) if origin else None
        if scheme == "https":
            return True
        if scheme != "http" or (origin_url and origin_url.scheme != "http"):
            raise _public_error(
                "admin_https_required",
                "Provider 管理配对仅允许 HTTPS 或本机回环地址。",
                status.HTTP_403_FORBIDDEN,
            )
        host = (origin_url.hostname if origin_url else request.url.hostname) or ""
        try:
            loopback = ip_address(host).is_loopback
        except ValueError:
            loopback = host.casefold() == "localhost"
        if not loopback:
            raise _public_error(
                "admin_https_required",
                "Provider 管理配对仅允许 HTTPS 或本机回环地址。",
                status.HTTP_403_FORBIDDEN,
            )
        return False


_auth: ProviderAdminAuth | None = None


def get_provider_admin_auth() -> ProviderAdminAuth:
    global _auth
    if _auth is None:
        _auth = ProviderAdminAuth()
    return _auth


def reset_provider_admin_auth() -> None:
    global _auth
    _auth = None


def require_provider_admin(request: Request) -> ProviderControlPrincipal:
    return get_provider_admin_auth().require(request)


def require_provider_admin_csrf(request: Request) -> ProviderControlPrincipal:
    return get_provider_admin_auth().require_csrf(request)
