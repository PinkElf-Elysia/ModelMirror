from __future__ import annotations

import asyncio
import contextlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .projects import MAX_PROJECTS, ProjectKind


MAX_PROJECT_SOURCE_FRAME_BYTES = 64 * 1024
PROJECT_SOURCE_TIMEOUT_SECONDS = 15.0
PROJECT_SOURCE_ACQUIRE_TIMEOUT_SECONDS = 130.0
SAFE_LOCAL_PROJECT_ID = re.compile(r"^local-[a-f0-9]{24}$")
SAFE_INTERNAL_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SAFE_OBJECT_ID = re.compile(r"^[a-f0-9]{40}|[a-f0-9]{64}$")
SAFE_PUBLIC_OBJECT_ID = re.compile(r"^[a-f0-9]{7,12}$")
SAFE_FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")


class ProjectSourceClientError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class CodingProjectSourceClient:
    """Server-side client for the private, fixed-root project source broker."""

    def __init__(
        self,
        socket_path: str | Path,
        *,
        timeout: float = PROJECT_SOURCE_TIMEOUT_SECONDS,
    ) -> None:
        self._socket_path = str(socket_path)
        self._timeout = timeout

    async def health(self) -> dict[str, Any]:
        return await self._request({"action": "health"})

    async def list_projects(self) -> list[dict[str, Any]]:
        response = await self._request({"action": "list"})
        projects = response.get("projects")
        if not isinstance(projects, list) or len(projects) > MAX_PROJECTS:
            raise ProjectSourceClientError(
                "Project source returned an invalid catalog.",
                code="invalid_project_source_response",
            )
        return [_validate_public_project(item) for item in projects]

    async def check(self, project_id: str, expected_head: str) -> dict[str, Any]:
        response = await self._request(
            {
                "action": "check",
                "project_id": project_id,
                "expected_head": expected_head,
            }
        )
        return _validate_public_project(response.get("project"))

    async def acquire(
        self,
        project_id: str,
        *,
        expected_head: str | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            {
                "action": "acquire",
                "project_id": project_id,
                "expected_head": expected_head,
            },
            timeout=PROJECT_SOURCE_ACQUIRE_TIMEOUT_SECONDS,
        )
        lease = response.get("lease")
        if not isinstance(lease, dict):
            raise ProjectSourceClientError(
                "Project source omitted its lease.",
                code="invalid_project_source_response",
            )
        expected = {
            "lease_id",
            "project_id",
            "name",
            "branch",
            "head",
            "fingerprint",
            "file_count",
            "total_bytes",
            "hidden_files",
            "created_at",
        }
        if set(lease) != expected or lease.get("project_id") != project_id:
            raise ProjectSourceClientError(
                "Project source lease is invalid.",
                code="invalid_project_source_response",
            )
        if (
            not _valid_local_id(lease.get("project_id"))
            or not _valid_internal_id(lease.get("lease_id"))
            or not _valid_name(lease.get("name"))
            or not _valid_branch(lease.get("branch"))
            or not isinstance(lease.get("head"), str)
            or SAFE_OBJECT_ID.fullmatch(lease["head"]) is None
            or not isinstance(lease.get("fingerprint"), str)
            or SAFE_FINGERPRINT.fullmatch(lease["fingerprint"]) is None
            or not _valid_count(lease.get("file_count"), maximum=20_000)
            or not _valid_count(lease.get("total_bytes"), maximum=192 * 1024 * 1024)
            or not _valid_count(lease.get("hidden_files"), maximum=20_000)
            or not isinstance(lease.get("created_at"), (int, float))
            or isinstance(lease.get("created_at"), bool)
            or not math.isfinite(float(lease["created_at"]))
        ):
            raise ProjectSourceClientError(
                "Project source lease is invalid.",
                code="invalid_project_source_response",
            )
        return {"kind": ProjectKind.LOCAL_CLONE.value, **lease}

    async def release(self, project_id: str, lease_id: str) -> bool:
        response = await self._request(
            {
                "action": "release",
                "project_id": project_id,
                "lease_id": lease_id,
            }
        )
        released = response.get("released")
        if not isinstance(released, bool):
            raise ProjectSourceClientError(
                "Project source omitted its release result.",
                code="invalid_project_source_response",
            )
        return released

    async def _request(
        self,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(
                    self._socket_path,
                    limit=MAX_PROJECT_SOURCE_FRAME_BYTES + 1,
                ),
                timeout=self._timeout,
            )
        except (OSError, TimeoutError) as exc:
            raise ProjectSourceClientError(
                "Project source is unavailable.",
                code="project_source_unavailable",
            ) from exc
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8") + b"\n"
            if len(encoded) > MAX_PROJECT_SOURCE_FRAME_BYTES:
                raise ProjectSourceClientError(
                    "Project source request is too large.",
                    code="invalid_request",
                )
            writer.write(encoded)
            await writer.drain()
            raw = await asyncio.wait_for(
                reader.readline(),
                timeout=timeout or self._timeout,
            )
            if not raw or len(raw) > MAX_PROJECT_SOURCE_FRAME_BYTES:
                raise ProjectSourceClientError(
                    "Project source response is invalid.",
                    code="invalid_project_source_response",
                )
            try:
                response = json.loads(raw.decode("utf-8", errors="strict"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ProjectSourceClientError(
                    "Project source response is invalid.",
                    code="invalid_project_source_response",
                ) from exc
            if not isinstance(response, dict):
                raise ProjectSourceClientError(
                    "Project source response is invalid.",
                    code="invalid_project_source_response",
                )
            if response.get("ok") is not True:
                code = response.get("code")
                raise ProjectSourceClientError(
                    "Project source request failed.",
                    code=(
                        code
                        if isinstance(code, str) and 1 <= len(code) <= 64
                        else "project_source_failed"
                    ),
                )
            return response
        except TimeoutError as exc:
            raise ProjectSourceClientError(
                "Project source request timed out.",
                code="project_source_timeout",
            ) from exc
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


def _validate_public_project(value: Any) -> dict[str, Any]:
    expected = {"id", "name", "kind", "state", "reason", "branch", "head", "features"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ProjectSourceClientError(
            "Project source returned an invalid project.",
            code="invalid_project_source_response",
        )
    if (
        value.get("kind") != ProjectKind.LOCAL_CLONE.value
        or not _valid_local_id(value.get("id"))
        or not _valid_name(value.get("name"))
        or value.get("state") not in {"available", "unavailable"}
        or (
            value.get("state") == "available"
            and (
                value.get("reason") is not None
                or not _valid_branch(value.get("branch"))
                or not isinstance(value.get("head"), str)
                or SAFE_PUBLIC_OBJECT_ID.fullmatch(value["head"]) is None
            )
        )
        or (
            value.get("state") == "unavailable"
            and (
                not _valid_internal_id(value.get("reason"))
                or value.get("branch") is not None
                or value.get("head") is not None
            )
        )
        or value.get("features")
        != {
            "chat": True,
            "draft": True,
            "diff": True,
            "download": True,
            "recovery": True,
            "verification": False,
            "apply": False,
            "commit": False,
            "publish": False,
        }
    ):
        raise ProjectSourceClientError(
            "Project source returned an invalid project.",
            code="invalid_project_source_response",
        )
    return dict(value)


def _valid_local_id(value: Any) -> bool:
    return isinstance(value, str) and SAFE_LOCAL_PROJECT_ID.fullmatch(value) is not None


def _valid_internal_id(value: Any) -> bool:
    return isinstance(value, str) and SAFE_INTERNAL_ID.fullmatch(value) is not None


def _valid_name(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and 1 <= len(value) <= 80
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _valid_branch(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 255
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _valid_count(value: Any, *, maximum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= maximum
    )
