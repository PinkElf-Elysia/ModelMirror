from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from .projects import MAX_PROJECT_NAME_CHARS, MAX_PROJECTS, ProjectFeatures, ProjectKind


PROJECT_HOST_PROTOCOL = "modelmirror-coding-project-host-v1"
PROJECT_HOST_PLATFORM = "windows"
PAIRING_TTL_SECONDS = 300
SELECTION_TTL_SECONDS = 300
HEARTBEAT_STALE_SECONDS = 60

HOST_ID_PATTERN = re.compile(r"^phost_[a-f0-9]{32}$")
DEVICE_ID_PATTERN = re.compile(r"^pdev_[a-f0-9]{32}$")
PROJECT_ID_PATTERN = re.compile(r"^hostgit_[a-f0-9]{32}$")
REQUEST_ID_PATTERN = re.compile(r"^phreq_[a-f0-9]{32}$")
OBJECT_ID_PATTERN = re.compile(r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")


class ProjectHostError(RuntimeError):
    def __init__(self, code: str, message: str = "Project host request failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class ProjectHostPairing:
    pairing_id: str
    code_hash: str
    name: str
    created_at: float
    expires_at: float
    consumed: bool = False


@dataclass(slots=True)
class ProjectHost:
    host_id: str
    device_id: str
    name: str
    token_hash: str
    version: str
    platform: str
    status: Literal["online", "offline", "revoked"] = "offline"
    connection_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_heartbeat_at: float | None = None


@dataclass(slots=True)
class HostGitProject:
    project_id: str
    host_id: str
    name: str
    branch: str
    head: str
    state: Literal["available", "unavailable"] = "available"
    reason: str | None = None
    updated_at: float = field(default_factory=time.time)

    def to_public_dict(self, *, host_online: bool) -> dict[str, Any]:
        available = host_online and self.state == "available"
        reason = None if available else self.reason or "project_host_offline"
        return {
            "id": self.project_id,
            "name": self.name,
            "kind": ProjectKind.HOST_GIT.value,
            "state": "available" if available else "unavailable",
            "reason": reason,
            "branch": self.branch if available else None,
            "head": self.head[:12] if available else None,
            "features": ProjectFeatures.host_git().to_dict(),
            "writeback_reason": "project_host_writeback_not_available",
        }


@dataclass(slots=True)
class ProjectSelection:
    request_id: str
    host_id: str
    status: Literal["pending", "dispatched", "completed", "failed", "expired"]
    created_at: float
    expires_at: float
    project_id: str | None = None
    error: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status,
            "project_id": self.project_id,
            "error": self.error,
            "expires_at": self.expires_at,
        }


class ProjectHostStore:
    """Path-free server registry for paired Coding project hosts."""

    def __init__(
        self,
        state_path: str | Path | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.state_path = Path(state_path) if state_path else None
        self._clock = clock
        self._lock = threading.RLock()
        self._pairings: dict[str, ProjectHostPairing] = {}
        self._hosts: dict[str, ProjectHost] = {}
        self._projects: dict[str, HostGitProject] = {}
        self._selections: dict[str, ProjectSelection] = {}
        self._load()

    @staticmethod
    def _hash_secret(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def create_pairing(self, name: str = "本地项目助手") -> tuple[ProjectHostPairing, str]:
        normalized_name = _normalize_name(name)
        now = self._clock()
        code = f"{secrets.randbelow(100_000_000):08d}"
        pairing = ProjectHostPairing(
            pairing_id=f"phpair_{uuid.uuid4().hex}",
            code_hash=self._hash_secret(code),
            name=normalized_name,
            created_at=now,
            expires_at=now + PAIRING_TTL_SECONDS,
        )
        with self._lock:
            self._expire_unlocked(now)
            self._pairings[pairing.pairing_id] = pairing
        return pairing, code

    def consume_pairing(
        self,
        code: str,
        *,
        device_id: str,
        version: str,
        platform: str,
    ) -> tuple[ProjectHost, str]:
        if DEVICE_ID_PATTERN.fullmatch(device_id) is None:
            raise ProjectHostError("project_host_device_invalid")
        if platform != PROJECT_HOST_PLATFORM:
            raise ProjectHostError("project_host_platform_unsupported")
        if not _valid_version(version):
            raise ProjectHostError("project_host_version_invalid")
        now = self._clock()
        code_hash = self._hash_secret(str(code).strip())
        with self._lock:
            self._expire_unlocked(now)
            pairing = next(
                (
                    item
                    for item in self._pairings.values()
                    if not item.consumed
                    and item.expires_at > now
                    and hmac.compare_digest(item.code_hash, code_hash)
                ),
                None,
            )
            if pairing is None:
                raise ProjectHostError("project_host_pairing_invalid")
            existing = next(
                (item for item in self._hosts.values() if item.device_id == device_id),
                None,
            )
            if existing is None and any(
                item.status != "revoked" for item in self._hosts.values()
            ):
                raise ProjectHostError("project_host_already_paired")
            token = secrets.token_urlsafe(48)
            if existing is None:
                host = ProjectHost(
                    host_id=f"phost_{uuid.uuid4().hex}",
                    device_id=device_id,
                    name=pairing.name,
                    token_hash=self._hash_secret(token),
                    version=version,
                    platform=platform,
                    status="online",
                    last_heartbeat_at=now,
                    created_at=now,
                    updated_at=now,
                )
                self._hosts[host.host_id] = host
            else:
                host = existing
                host.name = pairing.name
                host.token_hash = self._hash_secret(token)
                host.version = version
                host.platform = platform
                host.status = "online"
                host.last_heartbeat_at = now
                host.updated_at = now
            pairing.consumed = True
            self._persist_unlocked()
            return host, token

    def authenticate(self, host_id: str, token: str) -> ProjectHost:
        with self._lock:
            host = self._hosts.get(host_id)
            if host is None or host.status == "revoked":
                raise ProjectHostError("project_host_unavailable")
            if not hmac.compare_digest(host.token_hash, self._hash_secret(token)):
                raise ProjectHostError("project_host_authentication_failed")
            return host

    def connect(self, host_id: str, token: str, *, connection_id: str, version: str) -> ProjectHost:
        with self._lock:
            host = self.authenticate(host_id, token)
            if not _valid_version(version):
                raise ProjectHostError("project_host_version_invalid")
            now = self._clock()
            host.version = version
            host.connection_id = connection_id
            host.status = "online"
            host.last_heartbeat_at = now
            host.updated_at = now
            self._persist_unlocked()
            return host

    def heartbeat(self, host_id: str, connection_id: str) -> ProjectHost:
        with self._lock:
            host = self.require_host(host_id)
            if host.connection_id != connection_id or host.status != "online":
                raise ProjectHostError("project_host_connection_replaced")
            host.last_heartbeat_at = host.updated_at = self._clock()
            return host

    def disconnect(self, host_id: str, connection_id: str) -> None:
        with self._lock:
            host = self._hosts.get(host_id)
            if host is None or host.connection_id != connection_id:
                return
            host.connection_id = None
            if host.status != "revoked":
                host.status = "offline"
            host.updated_at = self._clock()
            self._persist_unlocked()

    def revoke(self, host_id: str) -> ProjectHost:
        with self._lock:
            host = self.require_host(host_id)
            host.status = "revoked"
            host.connection_id = None
            host.updated_at = self._clock()
            self._projects = {
                project_id: project
                for project_id, project in self._projects.items()
                if project.host_id != host_id
            }
            self._selections = {
                request_id: selection
                for request_id, selection in self._selections.items()
                if selection.host_id != host_id
            }
            self._persist_unlocked()
            return host

    def require_host(self, host_id: str) -> ProjectHost:
        host = self._hosts.get(host_id)
        if host is None:
            raise ProjectHostError("project_host_not_found")
        return host

    def online_host(self) -> ProjectHost:
        with self._lock:
            self._expire_unlocked(self._clock())
            online = [item for item in self._hosts.values() if item.status == "online"]
            if len(online) != 1:
                raise ProjectHostError("project_host_offline")
            return online[0]

    def host_status(self) -> dict[str, Any]:
        with self._lock:
            self._expire_unlocked(self._clock())
            active = next(
                (item for item in self._hosts.values() if item.status != "revoked"),
                None,
            )
            return {
                "paired": active is not None,
                "available": bool(active and active.status == "online"),
                "host_id": active.host_id if active else None,
                "name": active.name if active else None,
                "version": active.version if active else None,
                "platform": active.platform if active else PROJECT_HOST_PLATFORM,
            }

    def register_project(self, host_id: str, value: dict[str, Any]) -> HostGitProject:
        project = _parse_project(host_id, value, now=self._clock())
        with self._lock:
            host = self.require_host(host_id)
            if host.status == "revoked":
                raise ProjectHostError("project_host_unavailable")
            if project.project_id not in self._projects and len(self._projects) >= MAX_PROJECTS:
                raise ProjectHostError("project_limit_exceeded")
            previous = self._projects.get(project.project_id)
            if previous is not None and previous.host_id != host_id:
                raise ProjectHostError("project_id_conflict")
            self._projects[project.project_id] = project
            self._persist_unlocked()
            return project

    def rename_project(self, project_id: str, name: str) -> HostGitProject:
        with self._lock:
            project = self.require_project(project_id)
            project.name = _normalize_name(name)
            project.updated_at = self._clock()
            self._persist_unlocked()
            return project

    def remove_project(self, project_id: str) -> HostGitProject:
        with self._lock:
            project = self.require_project(project_id)
            del self._projects[project_id]
            self._persist_unlocked()
            return project

    def require_project(self, project_id: str) -> HostGitProject:
        project = self._projects.get(project_id)
        if project is None:
            raise ProjectHostError("project_not_found")
        return project

    def list_projects(self) -> list[dict[str, Any]]:
        with self._lock:
            self._expire_unlocked(self._clock())
            return [
                project.to_public_dict(
                    host_online=(
                        (host := self._hosts.get(project.host_id)) is not None
                        and host.status == "online"
                    )
                )
                for project in sorted(self._projects.values(), key=lambda item: item.name.casefold())
            ]

    def create_selection(self, host_id: str | None = None) -> ProjectSelection:
        with self._lock:
            host = self.online_host() if host_id is None else self.require_host(host_id)
            if host.status != "online":
                raise ProjectHostError("project_host_offline")
            now = self._clock()
            selection = ProjectSelection(
                request_id=f"phreq_{uuid.uuid4().hex}",
                host_id=host.host_id,
                status="pending",
                created_at=now,
                expires_at=now + SELECTION_TTL_SECONDS,
            )
            self._selections[selection.request_id] = selection
            return selection

    def next_selection(self, host_id: str) -> ProjectSelection | None:
        with self._lock:
            self._expire_unlocked(self._clock())
            selection = next(
                (
                    item
                    for item in self._selections.values()
                    if item.host_id == host_id and item.status == "pending"
                ),
                None,
            )
            if selection is not None:
                selection.status = "dispatched"
            return selection

    def complete_selection(
        self,
        host_id: str,
        request_id: str,
        *,
        project: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> ProjectSelection:
        with self._lock:
            selection = self.require_selection(request_id)
            if selection.host_id != host_id:
                raise ProjectHostError("project_host_request_mismatch")
            if selection.status in {"completed", "failed"}:
                return selection
            if selection.status not in {"pending", "dispatched"}:
                raise ProjectHostError("project_host_request_expired")
            if project is not None and error is None:
                registered = self.register_project(host_id, project)
                selection.project_id = registered.project_id
                selection.status = "completed"
            elif error is not None and _valid_reason(error):
                selection.error = error
                selection.status = "failed"
            else:
                raise ProjectHostError("invalid_project_host_response")
            return selection

    def require_selection(self, request_id: str) -> ProjectSelection:
        selection = self._selections.get(request_id)
        if selection is None:
            raise ProjectHostError("project_host_request_not_found")
        return selection

    def _expire_unlocked(self, now: float) -> None:
        for pairing in self._pairings.values():
            if pairing.expires_at <= now:
                pairing.consumed = True
        for selection in self._selections.values():
            if selection.expires_at <= now and selection.status in {"pending", "dispatched"}:
                selection.status = "expired"
                selection.error = "project_selection_expired"
        for host in self._hosts.values():
            if (
                host.status == "online"
                and host.last_heartbeat_at is not None
                and now - host.last_heartbeat_at > HEARTBEAT_STALE_SECONDS
            ):
                host.status = "offline"
                host.connection_id = None

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.is_file():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1:
                raise ValueError("invalid state")
            for raw in payload.get("hosts", []):
                if not isinstance(raw, dict):
                    raise ValueError("invalid host state")
                sanitized = dict(raw)
                # Batch previews briefly persisted a token prefix. Ignore that
                # legacy field while ensuring it is never written again.
                sanitized.pop("token_prefix", None)
                host = ProjectHost(**sanitized)
                if HOST_ID_PATTERN.fullmatch(host.host_id) and DEVICE_ID_PATTERN.fullmatch(host.device_id):
                    host.status = "revoked" if host.status == "revoked" else "offline"
                    host.connection_id = None
                    self._hosts[host.host_id] = host
            for raw in payload.get("projects", []):
                project = HostGitProject(**raw)
                if PROJECT_ID_PATTERN.fullmatch(project.project_id) and project.host_id in self._hosts:
                    self._projects[project.project_id] = project
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProjectHostError("project_host_state_corrupt") from exc

    def _persist_unlocked(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "hosts": [asdict(item) for item in self._hosts.values()],
            "projects": [asdict(item) for item in self._projects.values()],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        temporary = self.state_path.with_name(f".{self.state_path.name}.{secrets.token_hex(8)}.tmp")
        try:
            temporary.write_text(encoded, encoding="utf-8")
            os.replace(temporary, self.state_path)
        finally:
            temporary.unlink(missing_ok=True)


def _parse_project(host_id: str, value: dict[str, Any], *, now: float) -> HostGitProject:
    expected = {"project_id", "name", "branch", "head", "state", "reason"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ProjectHostError("invalid_project_host_response")
    project_id = value.get("project_id")
    branch = value.get("branch")
    head = value.get("head")
    state = value.get("state")
    reason = value.get("reason")
    if (
        not isinstance(project_id, str)
        or PROJECT_ID_PATTERN.fullmatch(project_id) is None
        or not _valid_branch(branch)
        or not isinstance(head, str)
        or OBJECT_ID_PATTERN.fullmatch(head) is None
        or state not in {"available", "unavailable"}
        or (state == "available" and reason is not None)
        or (state == "unavailable" and not _valid_reason(reason))
    ):
        raise ProjectHostError("invalid_project_host_response")
    return HostGitProject(
        project_id=project_id,
        host_id=host_id,
        name=_normalize_name(value.get("name")),
        branch=branch,
        head=head,
        state=state,
        reason=reason,
        updated_at=now,
    )


def _normalize_name(value: Any) -> str:
    if not isinstance(value, str) or value != unicodedata.normalize("NFC", value):
        raise ProjectHostError("project_name_invalid")
    if not value or value != value.strip() or len(value) > MAX_PROJECT_NAME_CHARS:
        raise ProjectHostError("project_name_invalid")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ProjectHostError("project_name_invalid")
    return value


def _valid_branch(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 200
        and value == value.strip()
        and not any(unicodedata.category(character).startswith("C") for character in value)
    )


def _valid_reason(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) is not None


def _valid_version(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"1\.[0-9]+\.[0-9]+", value) is not None
