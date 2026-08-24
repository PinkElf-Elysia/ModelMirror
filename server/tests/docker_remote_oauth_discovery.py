"""One-shot real-metadata gate for the isolated OAuth and egress sidecars.

This harness deliberately performs discovery only. It never opens an
authorization page, registers a client, exchanges a code, or handles tokens.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
from urllib.parse import urlsplit


EGRESS_SOCKET = "/run/modelmirror-hub-egress/hub-egress.sock"
OAUTH_SOCKET = "/run/modelmirror-oauth/oauth.sock"
TARGET_ID = "mcphub_" + "a" * 32
MAX_RESPONSE_BYTES = 72 * 1024


def request(path: str, payload: dict[str, object]) -> dict[str, object]:
    client = socket.socket(socket.AF_UNIX)
    try:
        client.settimeout(25)
        client.connect(path)
        client.sendall(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            + b"\n"
        )
        raw = bytearray()
        while not raw.endswith(b"\n"):
            chunk = client.recv(4096)
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise RuntimeError("docker_oauth_response_too_large")
    finally:
        client.close()
    value = json.loads(bytes(raw).decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("docker_oauth_response_invalid")
    return value


def exchange(url: str, action: str, **extra: object) -> dict[str, object]:
    grant = request(
        EGRESS_SOCKET,
        {"action": "authorize", "candidate_id": TARGET_ID, "url": url},
    )
    if grant.get("ok") is not True:
        raise RuntimeError(str(grant.get("code") or "docker_oauth_egress_denied"))
    capability = str(grant.get("capability") or "")
    try:
        return request(
            OAUTH_SOCKET,
            {
                "action": action,
                "target_id": TARGET_ID,
                "url": url,
                "capability": capability,
                **extra,
            },
        )
    finally:
        request(EGRESS_SOCKET, {"action": "revoke", "capability": capability})


def first_document(
    urls: list[str], document_kind: str
) -> tuple[str, dict[str, object], str]:
    for url in urls:
        response = exchange(url, "fetch_json", document_kind=document_kind)
        if response.get("ok") is True and isinstance(response.get("document"), dict):
            return (
                url,
                response["document"],
                str(response.get("document_digest") or ""),
            )
        if response.get("code") not in {
            "mcp_remote_oauth_document_not_found",
            "mcp_remote_oauth_upstream_http",
        }:
            raise RuntimeError(str(response.get("code") or "docker_oauth_fetch_failed"))
    raise RuntimeError(f"docker_oauth_{document_kind}_missing")


def main() -> None:
    resource = os.environ["MCP_REMOTE_OAUTH_TEST_RESOURCE_URL"]
    parsed = urlsplit(resource)
    if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment:
        raise RuntimeError("docker_oauth_resource_invalid")
    origin = f"https://{parsed.hostname}"
    path = parsed.path if parsed.path != "/" else ""

    health = request(OAUTH_SOCKET, {"action": "health"})
    assert health == {
        "authorization_enabled": False,
        "ok": True,
        "protocol": "modelmirror-mcp-remote-oauth-v1",
        "token_storage_enabled": False,
    }

    probe = exchange(resource, "probe_resource")
    metadata_hint = str(probe.get("resource_metadata_url") or "")
    protected_urls = [
        item
        for item in (
            metadata_hint,
            f"{origin}/.well-known/oauth-protected-resource{path}",
            f"{origin}/.well-known/oauth-protected-resource",
        )
        if item
    ]
    protected_url, protected, protected_digest = first_document(
        list(dict.fromkeys(protected_urls)), "protected_resource_metadata"
    )
    if protected.get("resource") != resource:
        raise RuntimeError("docker_oauth_resource_mismatch")
    issuers = protected.get("authorization_servers")
    if not isinstance(issuers, list) or len(issuers) != 1:
        raise RuntimeError("docker_oauth_issuer_ambiguous")
    issuer = str(issuers[0]).rstrip("/")
    issuer_parts = urlsplit(issuer)
    issuer_origin = f"https://{issuer_parts.hostname}"
    issuer_path = issuer_parts.path.rstrip("/")
    metadata_urls = list(
        dict.fromkeys(
            (
                f"{issuer_origin}/.well-known/oauth-authorization-server{issuer_path}",
                f"{issuer_origin}/.well-known/openid-configuration{issuer_path}",
                f"{issuer}/.well-known/openid-configuration",
            )
        )
    )
    metadata_url, metadata, metadata_digest = first_document(
        metadata_urls, "authorization_server_metadata"
    )
    if metadata.get("issuer") != issuer:
        raise RuntimeError("docker_oauth_issuer_mismatch")
    if "S256" not in (metadata.get("code_challenge_methods_supported") or []):
        raise RuntimeError("docker_oauth_pkce_s256_required")
    if "authorization_code" not in (metadata.get("grant_types_supported") or []):
        raise RuntimeError("docker_oauth_grant_unsupported")
    if "code" not in (metadata.get("response_types_supported") or []):
        raise RuntimeError("docker_oauth_response_unsupported")

    evidence = {
        "authorization_enabled": False,
        "authorization_server_metadata_digest": metadata_digest,
        "authorization_server_metadata_url": metadata_url,
        "issuer": issuer,
        "pkce_method": "S256",
        "protected_resource_metadata_digest": protected_digest,
        "protected_resource_metadata_url": protected_url,
        "registration_endpoint_available": bool(metadata.get("registration_endpoint")),
        "resource": resource,
        "scopes_count": len(protected.get("scopes_supported") or []),
        "token_storage_enabled": False,
    }
    canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    evidence["evidence_digest"] = hashlib.sha256(canonical).hexdigest()
    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    main()
