"""Network-less OAuth discovery and fixed token-exchange sidecar for remote MCP."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .hub_server import (
    CAPABILITY_RE,
    CANDIDATE_ID_RE,
    HubSidecarError,
    LoopbackHubProxy,
    _close_writer,
    _json_bytes,
    _normalize_target,
    _peer_uid,
    _read_request,
    _write_response,
)


OAUTH_SOCKET = Path(
    os.getenv("MCP_REMOTE_OAUTH_SOCKET_PATH", "/run/modelmirror-oauth/oauth.sock")
)
MAX_METADATA_BYTES = 64 * 1024
DOCUMENT_KINDS = frozenset(
    {
        "protected_resource_metadata",
        "authorization_server_metadata",
        "client_id_metadata_document",
    }
)
RESOURCE_METADATA_RE = re.compile(
    r'(?:^|[\s,])resource_metadata="([^"\\]{1,4096})"', re.IGNORECASE
)
SCOPE_RE = re.compile(r'(?:^|[\s,])scope="([^"\\]{1,2048})"', re.IGNORECASE)
PKCE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


def _contract(
    request: dict[str, Any], *, action: str, extra: frozenset[str] = frozenset()
) -> tuple[str, str, str]:
    expected = {"action", "target_id", "url", "capability", *extra}
    if set(request) != expected or request.get("action") != action:
        raise HubSidecarError("mcp_remote_oauth_request_invalid")
    target_id = str(request.get("target_id") or "")
    capability = str(request.get("capability") or "")
    if (
        CANDIDATE_ID_RE.fullmatch(target_id) is None
        or CAPABILITY_RE.fullmatch(capability) is None
    ):
        raise HubSidecarError("mcp_remote_oauth_scope_denied")
    url, host = _normalize_target(request.get("url"))
    return url, host, capability


def _bearer_challenge(header: str) -> tuple[str, tuple[str, ...]]:
    if not header or len(header) > 8192 or "\r" in header or "\n" in header:
        return "", ()
    value = header.strip()
    if re.match(r"^Bearer(?:\s|$)", value, re.IGNORECASE) is None:
        return "", ()
    # Be deliberately conservative: once another authentication challenge
    # begins, a resource_metadata parameter can no longer be attributed to the
    # single Bearer challenge without a full RFC 9110 parser.
    if re.search(
        r",\s*[!#$%&'*+\-.^_`|~0-9A-Za-z]+(?:\s|$)", value, re.IGNORECASE
    ):
        raise HubSidecarError("mcp_remote_oauth_challenge_ambiguous")
    matches = RESOURCE_METADATA_RE.findall(value)
    if len(matches) > 1:
        raise HubSidecarError("mcp_remote_oauth_challenge_ambiguous")
    scope_matches = SCOPE_RE.findall(value)
    if len(scope_matches) > 1:
        raise HubSidecarError("mcp_remote_oauth_challenge_ambiguous")
    scopes: tuple[str, ...] = ()
    if scope_matches:
        values = tuple(scope_matches[0].split())
        if (
            not values
            or len(values) > 20
            or len(set(values)) != len(values)
            or any(
                len(item) > 200
                or any(ord(char) < 0x21 or ord(char) == 0x7F for char in item)
                for item in values
            )
        ):
            raise HubSidecarError("mcp_remote_oauth_challenge_ambiguous")
        scopes = tuple(sorted(values))
    return (matches[0] if matches else ""), scopes


def _resource_metadata(header: str) -> str:
    """Backward-compatible pure parser used by the sidecar regression harness."""

    return _bearer_challenge(header)[0]


async def _bounded_body(response: httpx.Response) -> bytes:
    declared = response.headers.get("content-length", "").strip()
    if declared:
        try:
            if int(declared) > MAX_METADATA_BYTES:
                raise HubSidecarError("mcp_remote_oauth_document_size_denied")
        except ValueError as exc:
            raise HubSidecarError("mcp_remote_oauth_document_invalid") from exc
    output = bytearray()
    async for chunk in response.aiter_bytes():
        output.extend(chunk)
        if len(output) > MAX_METADATA_BYTES:
            raise HubSidecarError("mcp_remote_oauth_document_size_denied")
    if not output:
        raise HubSidecarError("mcp_remote_oauth_document_invalid")
    return bytes(output)


def _valid_redirect_uri(value: Any) -> bool:
    raw = str(value or "")
    if not raw or len(raw) > 4096 or any(
        ord(char) < 0x20 or ord(char) == 0x7F for char in raw
    ):
        return False
    parsed = urlsplit(raw)
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme == "https" and parsed.hostname and (port or 443) == 443:
        return True
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "::1"}
        and port is not None
    )


def _project_document(document_kind: str, document: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "protected_resource_metadata": {
            "resource",
            "authorization_servers",
            "scopes_supported",
        },
        "authorization_server_metadata": {
            "issuer",
            "authorization_endpoint",
            "token_endpoint",
            "registration_endpoint",
            "revocation_endpoint",
            "scopes_supported",
            "code_challenge_methods_supported",
            "grant_types_supported",
            "response_types_supported",
            "token_endpoint_auth_methods_supported",
            "client_id_metadata_document_supported",
        },
        "client_id_metadata_document": {
            "client_id",
            "client_name",
            "token_endpoint_auth_method",
            "redirect_uris",
            "grant_types",
            "response_types",
        },
    }[document_kind]
    return {key: document[key] for key in sorted(allowed) if key in document}


class OAuthMetadataService:
    async def _client(
        self, capability: str, host: str
    ) -> tuple[LoopbackHubProxy, httpx.AsyncClient]:
        proxy = LoopbackHubProxy(capability, host)
        await proxy.start()
        client = httpx.AsyncClient(
            proxy=f"http://127.0.0.1:{proxy.port}",
            timeout=httpx.Timeout(connect=8, read=10, write=10, pool=5),
            follow_redirects=False,
            trust_env=False,
            headers={
                "User-Agent": "ModelMirror-MCP-OAuth-Discovery/1.0",
                "Accept": "application/json",
            },
        )
        return proxy, client

    async def probe_resource(
        self, url: str, host: str, capability: str
    ) -> dict[str, Any]:
        proxy, client = await self._client(capability, host)
        try:
            async with client.stream("GET", url) as response:
                if 300 <= response.status_code < 400:
                    raise HubSidecarError("hub_upstream_redirect_denied")
                challenge = (
                    response.headers.get("www-authenticate", "")
                    if response.status_code == 401
                    else ""
                )
                bearer_challenge = bool(
                    re.match(r"^Bearer(?:\s|$)", challenge.strip(), re.IGNORECASE)
                )
                metadata_url, challenge_scopes = _bearer_challenge(challenge)
                return {
                    "status_class": f"{response.status_code // 100}xx",
                    "bearer_challenge": bearer_challenge,
                    "resource_metadata_url": metadata_url,
                    "challenge_scopes": list(challenge_scopes),
                }
        except HubSidecarError:
            raise
        except httpx.TimeoutException as exc:
            raise HubSidecarError("mcp_remote_oauth_upstream_timeout") from exc
        except httpx.TransportError as exc:
            raise HubSidecarError("mcp_remote_oauth_upstream_unavailable") from exc
        finally:
            await client.aclose()
            await proxy.close()

    async def fetch_json(
        self, url: str, host: str, capability: str, document_kind: str
    ) -> dict[str, Any]:
        if document_kind not in DOCUMENT_KINDS:
            raise HubSidecarError("mcp_remote_oauth_document_kind_denied")
        proxy, client = await self._client(capability, host)
        try:
            async with client.stream("GET", url) as response:
                if response.status_code == 404:
                    raise HubSidecarError("mcp_remote_oauth_document_not_found")
                if 300 <= response.status_code < 400:
                    raise HubSidecarError("hub_upstream_redirect_denied")
                if response.status_code != 200:
                    raise HubSidecarError("mcp_remote_oauth_upstream_http")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type not in {"application/json"} and not content_type.endswith("+json"):
                    raise HubSidecarError("mcp_remote_oauth_content_type_denied")
                raw = await _bounded_body(response)
            try:
                document = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise HubSidecarError("mcp_remote_oauth_document_invalid") from exc
            if not isinstance(document, dict):
                raise HubSidecarError("mcp_remote_oauth_document_invalid")
            return {
                "document": _project_document(document_kind, document),
                "document_digest": hashlib.sha256(_json_bytes(document)).hexdigest(),
            }
        except HubSidecarError:
            raise
        except httpx.TimeoutException as exc:
            raise HubSidecarError("mcp_remote_oauth_upstream_timeout") from exc
        except httpx.TransportError as exc:
            raise HubSidecarError("mcp_remote_oauth_upstream_unavailable") from exc
        finally:
            await client.aclose()
            await proxy.close()

    async def register_public_client(
        self,
        url: str,
        host: str,
        capability: str,
        request_body: Any,
    ) -> dict[str, Any]:
        if not isinstance(request_body, dict) or set(request_body) != {
            "redirect_uris",
            "token_endpoint_auth_method",
            "grant_types",
            "response_types",
            "application_type",
            "client_name",
        }:
            raise HubSidecarError("mcp_remote_oauth_registration_invalid")
        if (
            request_body.get("token_endpoint_auth_method") != "none"
            or request_body.get("grant_types") != ["authorization_code"]
            or request_body.get("response_types") != ["code"]
            or request_body.get("application_type") != "native"
            or request_body.get("client_name")
            != "ModelMirror local MCP OAuth"
            or not isinstance(request_body.get("redirect_uris"), list)
            or len(request_body["redirect_uris"]) != 1
            or not _valid_redirect_uri(request_body["redirect_uris"][0])
            or len(_json_bytes(request_body)) > 8192
        ):
            raise HubSidecarError("mcp_remote_oauth_registration_invalid")
        proxy, client = await self._client(capability, host)
        dispatched = False
        try:
            dispatched = True
            async with client.stream(
                "POST",
                url,
                json=request_body,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            ) as response:
                if 400 <= response.status_code < 500:
                    raise HubSidecarError("mcp_remote_oauth_registration_rejected")
                if response.status_code != 201:
                    raise HubSidecarError(
                        "mcp_remote_oauth_registration_unknown_outcome"
                    )
                content_type = (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                if content_type != "application/json" and not content_type.endswith(
                    "+json"
                ):
                    raise HubSidecarError(
                        "mcp_remote_oauth_registration_unknown_outcome"
                    )
                raw = await _bounded_body(response)
            try:
                document = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise HubSidecarError(
                    "mcp_remote_oauth_registration_unknown_outcome"
                ) from exc
            if not isinstance(document, dict):
                raise HubSidecarError(
                    "mcp_remote_oauth_registration_unknown_outcome"
                )
            contains_secret = any(
                key in document
                for key in ("client_secret", "registration_access_token")
            ) or document.get("token_endpoint_auth_method") not in {None, "none"}
            client_id = str(document.get("client_id") or "")
            if not client_id or len(client_id) > 2048:
                raise HubSidecarError(
                    "mcp_remote_oauth_registration_unknown_outcome"
                )
            if contains_secret:
                return {"client_id": client_id, "contains_secret": True}
            if (
                document.get("redirect_uris") != request_body["redirect_uris"]
                or document.get("token_endpoint_auth_method") != "none"
                or document.get("grant_types") != ["authorization_code"]
                or document.get("response_types") != ["code"]
                or document.get("application_type", "native") != "native"
            ):
                raise HubSidecarError(
                    "mcp_remote_oauth_registration_unknown_outcome"
                )
            return {
                "client_id": client_id,
                "contains_secret": False,
                "registration_response_digest": hashlib.sha256(
                    _json_bytes(document)
                ).hexdigest(),
            }
        except HubSidecarError as exc:
            if dispatched and exc.code not in {
                "mcp_remote_oauth_registration_rejected",
                "mcp_remote_oauth_registration_unknown_outcome",
            }:
                raise HubSidecarError(
                    "mcp_remote_oauth_registration_unknown_outcome"
                ) from exc
            raise
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # Registration is a write. Any loss after dispatch is ambiguous and
            # must never be retried automatically.
            raise HubSidecarError("mcp_remote_oauth_registration_unknown_outcome") from exc
        finally:
            await client.aclose()
            await proxy.close()

    @staticmethod
    def _token_request(action: str, request_body: Any) -> dict[str, str]:
        if not isinstance(request_body, dict):
            raise HubSidecarError("mcp_remote_oauth_token_request_invalid")
        if action == "exchange_authorization_code":
            expected = {
                "grant_type",
                "code",
                "client_id",
                "redirect_uri",
                "code_verifier",
                "resource",
            }
            if (
                set(request_body) != expected
                or request_body.get("grant_type") != "authorization_code"
                or not _valid_redirect_uri(request_body.get("redirect_uri"))
                or PKCE_VERIFIER_RE.fullmatch(
                    str(request_body.get("code_verifier") or "")
                )
                is None
            ):
                raise HubSidecarError("mcp_remote_oauth_token_request_invalid")
            secret_fields = ("code", "code_verifier")
        elif action == "refresh_access_token":
            expected = {"grant_type", "refresh_token", "client_id", "resource"}
            if (
                set(request_body) != expected
                or request_body.get("grant_type") != "refresh_token"
            ):
                raise HubSidecarError("mcp_remote_oauth_token_request_invalid")
            secret_fields = ("refresh_token",)
        else:
            raise HubSidecarError("hub_action_denied")
        normalized: dict[str, str] = {}
        for key in expected:
            value = request_body.get(key)
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 20_000
                or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
            ):
                raise HubSidecarError("mcp_remote_oauth_token_request_invalid")
            normalized[key] = value
        try:
            normalized_resource, _resource_host = _normalize_target(
                normalized["resource"]
            )
        except HubSidecarError as exc:
            raise HubSidecarError("mcp_remote_oauth_token_request_invalid") from exc
        if normalized_resource != normalized["resource"]:
            raise HubSidecarError("mcp_remote_oauth_token_request_invalid")
        if any(len(normalized[key]) > 4096 for key in expected - set(secret_fields)):
            raise HubSidecarError("mcp_remote_oauth_token_request_invalid")
        if len(_json_bytes(normalized)) > 48 * 1024:
            raise HubSidecarError("mcp_remote_oauth_token_request_invalid")
        return normalized

    async def exchange_token(
        self,
        action: str,
        url: str,
        host: str,
        capability: str,
        request_body: Any,
    ) -> dict[str, Any]:
        body = self._token_request(action, request_body)
        proxy, client = await self._client(capability, host)
        dispatched = False
        try:
            dispatched = True
            async with client.stream(
                "POST",
                url,
                data=body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
            ) as response:
                if 300 <= response.status_code < 400:
                    raise HubSidecarError("hub_upstream_redirect_denied")
                if response.status_code in {400, 401}:
                    raise HubSidecarError(
                        "mcp_remote_oauth_authorization_rejected"
                        if action == "exchange_authorization_code"
                        else "mcp_remote_oauth_unauthorized"
                    )
                if response.status_code == 403:
                    raise HubSidecarError("mcp_remote_oauth_forbidden")
                if response.status_code == 429:
                    raise HubSidecarError("mcp_remote_oauth_rate_limited")
                if response.status_code != 200:
                    raise HubSidecarError(
                        "mcp_remote_oauth_token_exchange_unknown_outcome"
                        if action == "exchange_authorization_code"
                        else "mcp_remote_oauth_refresh_unknown_outcome"
                    )
                content_type = (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                if content_type != "application/json" and not content_type.endswith(
                    "+json"
                ):
                    raise HubSidecarError(
                        "mcp_remote_oauth_token_exchange_unknown_outcome"
                        if action == "exchange_authorization_code"
                        else "mcp_remote_oauth_refresh_unknown_outcome"
                    )
                raw = await _bounded_body(response)
            try:
                document = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise HubSidecarError(
                    "mcp_remote_oauth_token_exchange_unknown_outcome"
                    if action == "exchange_authorization_code"
                    else "mcp_remote_oauth_refresh_unknown_outcome"
                ) from exc
            if not isinstance(document, dict):
                raise HubSidecarError(
                    "mcp_remote_oauth_token_exchange_unknown_outcome"
                    if action == "exchange_authorization_code"
                    else "mcp_remote_oauth_refresh_unknown_outcome"
                )
            return {
                key: document[key]
                for key in ("access_token", "token_type", "expires_in", "refresh_token", "scope")
                if key in document
            }
        except HubSidecarError as exc:
            if dispatched and exc.code in {
                "mcp_remote_oauth_upstream_timeout",
                "mcp_remote_oauth_upstream_unavailable",
                "hub_upstream_redirect_denied",
            }:
                raise HubSidecarError(
                    "mcp_remote_oauth_token_exchange_unknown_outcome"
                    if action == "exchange_authorization_code"
                    else "mcp_remote_oauth_refresh_unknown_outcome"
                ) from exc
            raise
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise HubSidecarError(
                "mcp_remote_oauth_token_exchange_unknown_outcome"
                if action == "exchange_authorization_code"
                else "mcp_remote_oauth_refresh_unknown_outcome"
            ) from exc
        finally:
            for key in ("code", "code_verifier", "refresh_token"):
                if key in body:
                    body[key] = ""
            await client.aclose()
            await proxy.close()

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            uid = _peer_uid(writer)
            request = await _read_request(reader)
            action = str(request.get("action") or "")
            if action == "health" and set(request) == {"action"} and uid in {0, 65532}:
                response = {
                    "ok": True,
                    "protocol": "modelmirror-mcp-remote-oauth-v1",
                    "authorization_enabled": True,
                    "token_storage_enabled": False,
                }
            elif uid != 0:
                raise HubSidecarError("hub_peer_denied")
            elif action == "probe_resource":
                url, host, capability = _contract(request, action=action)
                response = {
                    "ok": True,
                    **await self.probe_resource(url, host, capability),
                }
            elif action == "fetch_json":
                url, host, capability = _contract(
                    request, action=action, extra=frozenset({"document_kind"})
                )
                response = {
                    "ok": True,
                    **await self.fetch_json(
                        url,
                        host,
                        capability,
                        str(request.get("document_kind") or ""),
                    ),
                }
            elif action == "register_public_client":
                url, host, capability = _contract(
                    request, action=action, extra=frozenset({"request_body"})
                )
                response = {
                    "ok": True,
                    **await self.register_public_client(
                        url, host, capability, request.get("request_body")
                    ),
                }
            elif action in {"exchange_authorization_code", "refresh_access_token"}:
                url, host, capability = _contract(
                    request, action=action, extra=frozenset({"request_body"})
                )
                response = {
                    "ok": True,
                    **await self.exchange_token(
                        action,
                        url,
                        host,
                        capability,
                        request.get("request_body"),
                    ),
                }
            else:
                raise HubSidecarError("hub_action_denied")
        except HubSidecarError as exc:
            response = {"ok": False, "code": exc.code}
        except Exception:
            response = {"ok": False, "code": "mcp_remote_oauth_sidecar_internal_error"}
        await _write_response(writer, response)
        await _close_writer(writer)


def _prepare_socket(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(metadata.st_mode):
        raise RuntimeError("mcp_remote_oauth_socket_path_unsafe")
    path.unlink()


async def run() -> None:
    _prepare_socket(OAUTH_SOCKET)
    server = await asyncio.start_unix_server(
        OAuthMetadataService().handle, path=str(OAUTH_SOCKET)
    )
    os.chmod(OAUTH_SOCKET, 0o660)
    async with server:
        await server.serve_forever()


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
