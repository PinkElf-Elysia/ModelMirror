from __future__ import annotations

import base64
import asyncio
import ipaddress
import json
import re
import socket
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx
import httpcore
from httpcore._backends.auto import AutoBackend

from .values import WorkflowValue, normalize_workflow_value, workflow_value_to_text


HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
HTTP_BODY_MODES = {"none", "json", "text", "form"}
HTTP_RESPONSE_MODES = {"auto", "json", "text"}
HTTP_STATUS_POLICIES = {"success_only", "capture_all"}
HTTP_AUTH_TYPES = {"none", "api_key", "bearer", "basic"}
HTTP_BINDING_SOURCES = {"literal", "variable"}
HTTP_VALUE_TYPES = {"text", "number", "boolean", "null", "json"}
PROTECTED_HEADERS = {
    "authorization",
    "connection",
    "content-length",
    "cookie",
    "host",
    "proxy-authorization",
    "set-cookie",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
SAFE_RESPONSE_HEADERS = {
    "content-type",
    "content-language",
    "etag",
    "last-modified",
    "retry-after",
    "x-request-id",
}
MAX_STRUCTURED_ITEMS = 20
MIN_RESPONSE_BYTES = 1_024
MAX_RESPONSE_BYTES = 2 * 1_024 * 1_024
DEFAULT_RESPONSE_BYTES = 1_024 * 1_024
FIXED_DOH_URL = "https://1.1.1.1/dns-query"
FIXED_DOH_TIMEOUT_SECONDS = 5.0
FIXED_DOH_MAX_BYTES = 64 * 1_024
FIXED_DOH_MAX_ANSWERS = 64
SYNTHETIC_DNS_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_VARIABLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_ITEM_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_TEMPLATE_PATTERN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]{0,63})\s*}}")
_SENSITIVE_PARAMETER_NAME = re.compile(
    r"(?:authorization|cookie|credential|password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token)",
    re.IGNORECASE,
)


class WorkflowHttpRequestError(RuntimeError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(f"{code}: {safe_message}")
        self.code = code
        self.safe_message = safe_message


def _fail(code: str, message: str) -> None:
    raise WorkflowHttpRequestError(code, message)


def is_http_request_v2(data: Mapping[str, Any]) -> bool:
    try:
        return int(data.get("contractVersion") or 1) == 2
    except (TypeError, ValueError):
        return False


def workflow_http_requests_enabled() -> bool:
    import os

    return os.getenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _canonical_hostname(hostname: object) -> str:
    raw = str(hostname or "").strip().rstrip(".").lower()
    if not raw or len(raw) > 253:
        _fail("HTTP_URL_INVALID", "HTTP URL hostname is invalid.")
    try:
        return ipaddress.ip_address(raw).compressed
    except ValueError:
        try:
            return raw.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise WorkflowHttpRequestError(
                "HTTP_URL_INVALID",
                "HTTP URL hostname is invalid.",
            ) from exc


def _validated_public_addresses(addresses: set[str] | tuple[str, ...]) -> tuple[str, ...]:
    if not addresses:
        _fail("HTTP_DNS_RESOLUTION_FAILED", "HTTP hostname has no usable addresses.")
    normalized: set[str] = set()
    for raw_address in addresses:
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError:
            _fail("HTTP_DNS_RESOLUTION_FAILED", "HTTP hostname returned an invalid address.")
        if not address.is_global:
            _fail("HTTP_PRIVATE_TARGET_FORBIDDEN", "Private or reserved network targets are forbidden.")
        normalized.add(address.compressed)
    return tuple(sorted(normalized))


def _parse_fixed_doh_answers(payload: object, expected_type: int) -> tuple[str, ...]:
    if not isinstance(payload, dict) or payload.get("Status") != 0:
        _fail("HTTP_DNS_RESOLUTION_FAILED", "Secure public DNS resolution failed.")
    answers = payload.get("Answer", [])
    if not isinstance(answers, list) or len(answers) > FIXED_DOH_MAX_ANSWERS:
        _fail("HTTP_DNS_RESOLUTION_FAILED", "Secure public DNS returned an invalid response.")
    addresses: list[str] = []
    for answer in answers:
        if not isinstance(answer, dict):
            _fail("HTTP_DNS_RESOLUTION_FAILED", "Secure public DNS returned an invalid response.")
        if answer.get("type") != expected_type:
            continue
        raw_address = answer.get("data")
        if not isinstance(raw_address, str):
            _fail("HTTP_DNS_RESOLUTION_FAILED", "Secure public DNS returned an invalid response.")
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError:
            _fail("HTTP_DNS_RESOLUTION_FAILED", "Secure public DNS returned an invalid response.")
        expected_version = 4 if expected_type == 1 else 6
        if address.version != expected_version:
            _fail("HTTP_DNS_RESOLUTION_FAILED", "Secure public DNS returned an invalid response.")
        addresses.append(address.compressed)
    return tuple(dict.fromkeys(addresses))


async def _resolve_fixed_public_dns(
    hostname: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, ...]:
    resolved: list[str] = []
    try:
        async with httpx.AsyncClient(
            transport=transport,
            timeout=FIXED_DOH_TIMEOUT_SECONDS,
            trust_env=False,
            follow_redirects=False,
            headers={"Accept": "application/dns-json", "Accept-Encoding": "identity"},
        ) as client:
            for record_name, record_type in (("A", 1), ("AAAA", 28)):
                async with client.stream(
                    "GET",
                    FIXED_DOH_URL,
                    params={"name": hostname, "type": record_name},
                ) as response:
                    if response.status_code != 200 or response.headers.get(
                        "content-encoding"
                    ):
                        _fail("HTTP_DNS_RESOLUTION_FAILED", "Secure public DNS resolution failed.")
                    media_type = (
                        response.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )
                    if media_type not in {"application/dns-json", "application/json"}:
                        _fail("HTTP_DNS_RESOLUTION_FAILED", "Secure public DNS returned an invalid response.")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > FIXED_DOH_MAX_BYTES:
                            _fail("HTTP_DNS_RESOLUTION_FAILED", "Secure public DNS returned an invalid response.")
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise WorkflowHttpRequestError(
                        "HTTP_DNS_RESOLUTION_FAILED",
                        "Secure public DNS returned an invalid response.",
                    ) from exc
                resolved.extend(_parse_fixed_doh_answers(payload, record_type))
    except WorkflowHttpRequestError:
        raise
    except (httpx.HTTPError, OSError) as exc:
        raise WorkflowHttpRequestError(
            "HTTP_DNS_RESOLUTION_FAILED",
            "Secure public DNS resolution failed.",
        ) from exc
    return _validated_public_addresses(tuple(resolved))


async def validate_public_workflow_url(
    url: str,
    network_policy: str,
) -> tuple[str, ...]:
    if network_policy != "public_only":
        _fail("HTTP_NETWORK_POLICY_INVALID", "Workflow HTTP requests require public-only networking.")
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        _fail("HTTP_URL_INVALID", "Only HTTP and HTTPS URLs are allowed.")
    if parsed.username or parsed.password:
        _fail("HTTP_URL_CREDENTIALS_FORBIDDEN", "Credentials embedded in URLs are forbidden.")
    hostname = _canonical_hostname(parsed.hostname)
    if hostname == "localhost" or hostname.endswith((".local", ".internal")):
        _fail("HTTP_PRIVATE_TARGET_FORBIDDEN", "Local and private hostnames are forbidden.")
    if hostname in {"metadata.google.internal", "host.docker.internal"}:
        _fail("HTTP_METADATA_TARGET_FORBIDDEN", "Metadata and host bridge addresses are forbidden.")
    try:
        literal_address = ipaddress.ip_address(hostname)
    except ValueError:
        literal_address = None
    if literal_address is not None:
        return _validated_public_addresses((literal_address.compressed,))
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        results = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except (OSError, ValueError) as exc:
        raise WorkflowHttpRequestError(
            "HTTP_DNS_RESOLUTION_FAILED",
            "HTTP hostname could not be resolved.",
        ) from exc
    addresses = {row[4][0] for row in results}
    parsed_addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    try:
        parsed_addresses = [ipaddress.ip_address(raw_address) for raw_address in addresses]
    except ValueError:
        _fail("HTTP_DNS_RESOLUTION_FAILED", "HTTP hostname returned an invalid address.")
    if parsed_addresses and all(
        isinstance(address, ipaddress.IPv4Address) and address in SYNTHETIC_DNS_NETWORK
        for address in parsed_addresses
    ):
        # Docker Desktop VPNs may return RFC 2544 benchmark addresses as DNS
        # placeholders. Resolve the public hostname through a fixed TLS DoH
        # endpoint, then retain the same public-address validation and TCP
        # pinning used for ordinary DNS results.
        return _validated_public_addresses(await _resolve_fixed_public_dns(hostname))
    return _validated_public_addresses(addresses)


class _PinnedPublicNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connect only to addresses approved by the immediately preceding DNS check.

    HTTP keeps the original hostname at the httpcore layer, so TLS SNI and
    certificate verification still use that hostname. Only the TCP destination
    is replaced, closing the validate-then-resolve DNS rebinding window.
    """

    def __init__(self) -> None:
        self._delegate = AutoBackend()
        self._approved: dict[tuple[str, int], tuple[str, ...]] = {}

    def approve(self, host: str, port: int, addresses: tuple[str, ...]) -> None:
        if not addresses:
            _fail("HTTP_DNS_RESOLUTION_FAILED", "HTTP hostname has no usable addresses.")
        self._approved[(_canonical_hostname(host), int(port))] = addresses[:8]

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = self._approved.get((_canonical_hostname(host), int(port)))
        if not addresses:
            _fail("HTTP_DNS_PIN_MISSING", "HTTP destination was not approved for connection.")
        last_error: Exception | None = None
        for address in addresses:
            try:
                return await self._delegate.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        _fail("HTTP_UNIX_SOCKET_FORBIDDEN", "Workflow HTTP requests cannot use Unix sockets.")

    async def sleep(self, seconds: float) -> None:
        await self._delegate.sleep(seconds)


class _PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    def __init__(self, backend: _PinnedPublicNetworkBackend) -> None:
        super().__init__(trust_env=False, retries=0)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=True, trust_env=False),
            max_connections=1,
            max_keepalive_connections=0,
            retries=0,
            network_backend=backend,
        )


def _literal_matches(value_type: str, value: WorkflowValue) -> bool:
    if value_type == "text":
        return isinstance(value, str)
    if value_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if value_type == "boolean":
        return isinstance(value, bool)
    if value_type == "null":
        return value is None
    return True


def validate_http_binding(binding: object, *, label: str) -> dict[str, Any]:
    if not isinstance(binding, dict):
        _fail("HTTP_BINDING_INVALID", f"{label} must use a literal or variable binding.")
    source = str(binding.get("source") or "").strip()
    if source not in HTTP_BINDING_SOURCES:
        _fail("HTTP_BINDING_SOURCE_INVALID", f"{label} binding source is invalid.")
    if source == "variable":
        variable = str(binding.get("variable") or "").strip()
        if not _VARIABLE_PATTERN.fullmatch(variable):
            _fail("HTTP_BINDING_VARIABLE_INVALID", f"{label} needs a variable identifier.")
        return binding
    value_type = str(binding.get("valueType") or "text").strip()
    if value_type not in HTTP_VALUE_TYPES:
        _fail("HTTP_BINDING_VALUE_TYPE_INVALID", f"{label} literal type is invalid.")
    if "value" not in binding:
        _fail("HTTP_BINDING_VALUE_MISSING", f"{label} literal value is missing.")
    try:
        value = normalize_workflow_value(binding.get("value"), path=f"$.{label}")
    except ValueError as exc:
        _fail("HTTP_BINDING_VALUE_INVALID", f"{label} literal value is invalid.")
    if not _literal_matches(value_type, value):
        _fail("HTTP_BINDING_VALUE_TYPE_MISMATCH", f"{label} literal value has the wrong type.")
    return binding


def _validate_structured_items(
    value: object,
    *,
    label: str,
    header_names: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_STRUCTURED_ITEMS:
        _fail(
            "HTTP_STRUCTURED_ITEMS_INVALID",
            f"{label} must contain at most {MAX_STRUCTURED_ITEMS} items.",
        )
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            _fail("HTTP_STRUCTURED_ITEM_INVALID", f"Each {label} item must be an object.")
        item_id = str(item.get("id") or "").strip()
        if not _ITEM_ID_PATTERN.fullmatch(item_id) or item_id in seen_ids:
            _fail("HTTP_STRUCTURED_ITEM_ID_INVALID", f"{label} item ids must be unique and stable.")
        seen_ids.add(item_id)
        name = str(item.get("name") or "").strip()
        if not 1 <= len(name) <= 128 or "{{" in name or "}}" in name:
            _fail("HTTP_STRUCTURED_ITEM_NAME_INVALID", f"{label} item names must be fixed text.")
        normalized_name = name.lower() if header_names else name
        if normalized_name in seen_names:
            _fail("HTTP_STRUCTURED_ITEM_NAME_DUPLICATE", f"{label} item names must be unique.")
        seen_names.add(normalized_name)
        if header_names:
            if not _HEADER_NAME_PATTERN.fullmatch(name):
                _fail("HTTP_HEADER_NAME_INVALID", "HTTP header name is invalid.")
            if name.lower() in PROTECTED_HEADERS:
                _fail("HTTP_PROTECTED_HEADER", "Protected HTTP headers cannot be configured.")
        if label in {"query", "header"} and _SENSITIVE_PARAMETER_NAME.search(name):
            _fail(
                "HTTP_PLAINTEXT_AUTH_PARAMETER_FORBIDDEN",
                "Authentication parameters must use an encrypted HTTP credential.",
            )
        validate_http_binding(item.get("binding"), label=f"{label} item")
    return value


def _validate_url_template(url: str) -> None:
    if not 1 <= len(url) <= 2_048:
        _fail("HTTP_URL_INVALID", "HTTP URL must contain 1 to 2048 characters.")
    scheme_separator = url.find("://")
    if scheme_separator <= 0:
        _fail("HTTP_URL_INVALID", "HTTP URL must use an explicit HTTP or HTTPS scheme.")
    authority_end_candidates = [
        index for marker in "/?#" if (index := url.find(marker, scheme_separator + 3)) >= 0
    ]
    authority_end = min(authority_end_candidates) if authority_end_candidates else len(url)
    fixed_origin = url[:authority_end]
    if "{{" in fixed_origin or "}}" in fixed_origin:
        _fail("HTTP_DYNAMIC_ORIGIN_FORBIDDEN", "HTTP scheme, host, and port must be fixed text.")
    templated = _TEMPLATE_PATTERN.sub("template", url)
    if "{{" in templated or "}}" in templated:
        _fail("HTTP_TEMPLATE_INVALID", "HTTP URL contains an invalid variable template.")
    try:
        parsed = urlsplit(templated)
        _ = parsed.port
    except ValueError:
        _fail("HTTP_URL_INVALID", "HTTP URL has an invalid port.")
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        _fail("HTTP_URL_INVALID", "HTTP URL must use HTTP or HTTPS and include a hostname.")
    if parsed.username or parsed.password:
        _fail("HTTP_URL_CREDENTIALS_FORBIDDEN", "Credentials embedded in URLs are forbidden.")


def validate_http_request_v2_config(data: Mapping[str, Any]) -> None:
    if not is_http_request_v2(data):
        _fail("HTTP_CONTRACT_VERSION_INVALID", "HTTP request contractVersion must be 2.")
    method = str(data.get("method") or "GET").strip().upper()
    if method not in HTTP_METHODS:
        _fail("HTTP_METHOD_INVALID", "HTTP method is not supported.")
    _validate_url_template(str(data.get("url") or "").strip())
    _validate_structured_items(data.get("queryItems", []), label="query")
    _validate_structured_items(data.get("headerItems", []), label="header", header_names=True)
    body_mode = str(data.get("bodyMode") or "none").strip()
    if body_mode not in HTTP_BODY_MODES:
        _fail("HTTP_BODY_MODE_INVALID", "HTTP body mode is invalid.")
    if method in {"GET", "DELETE"} and body_mode != "none":
        _fail("HTTP_METHOD_BODY_FORBIDDEN", "GET and DELETE requests cannot include a body.")
    if body_mode in {"json", "text"}:
        validate_http_binding(data.get("bodyBinding"), label="request body")
    elif data.get("bodyBinding") not in (None, {}):
        _fail("HTTP_BODY_BINDING_UNUSED", "The selected body mode does not accept a body binding.")
    form_fields = _validate_structured_items(data.get("formFields", []), label="form")
    if body_mode == "form" and not form_fields:
        _fail("HTTP_FORM_FIELDS_REQUIRED", "Form requests need at least one field.")
    if body_mode != "form" and form_fields:
        _fail("HTTP_FORM_FIELDS_UNUSED", "Form fields require form body mode.")
    auth_type = str(data.get("authType") or "none").strip()
    if auth_type not in HTTP_AUTH_TYPES:
        _fail("HTTP_AUTH_TYPE_INVALID", "HTTP authentication type is invalid.")
    credential_id = str(data.get("credentialId") or "").strip()
    if auth_type != "none" and not _VARIABLE_PATTERN.fullmatch(credential_id):
        # Credential ids use the same bounded identifier shape (for example cred_xxx).
        _fail("HTTP_CREDENTIAL_REQUIRED", "Selected authentication needs a credential reference.")
    if auth_type == "api_key":
        location = str(data.get("apiKeyLocation") or "header").strip()
        name = str(data.get("apiKeyName") or "").strip()
        if location not in {"header", "query"}:
            _fail("HTTP_API_KEY_LOCATION_INVALID", "API key location must be Header or Query.")
        if not 1 <= len(name) <= 128 or "{{" in name or "}}" in name:
            _fail("HTTP_API_KEY_NAME_INVALID", "API key name must be fixed text.")
        if location == "header" and (
            not _HEADER_NAME_PATTERN.fullmatch(name) or name.lower() in PROTECTED_HEADERS
        ):
            _fail("HTTP_API_KEY_HEADER_INVALID", "API key header name is invalid or protected.")
    try:
        timeout = int(data.get("timeoutSeconds") or 30)
        redirects = int(data.get("redirectLimit") or 0)
        response_limit = int(data.get("responseLimitBytes") or DEFAULT_RESPONSE_BYTES)
    except (TypeError, ValueError):
        _fail("HTTP_LIMIT_INVALID", "HTTP timeout, redirect, and response limits must be integers.")
    if not 1 <= timeout <= 60:
        _fail("HTTP_TIMEOUT_INVALID", "HTTP timeout must be between 1 and 60 seconds.")
    if not 0 <= redirects <= 3:
        _fail("HTTP_REDIRECT_LIMIT_INVALID", "HTTP redirect limit must be between 0 and 3.")
    if not MIN_RESPONSE_BYTES <= response_limit <= MAX_RESPONSE_BYTES:
        _fail("HTTP_RESPONSE_LIMIT_INVALID", "HTTP response limit must be between 1 KiB and 2 MiB.")
    if str(data.get("responseMode") or "auto") not in HTTP_RESPONSE_MODES:
        _fail("HTTP_RESPONSE_MODE_INVALID", "HTTP response mode is invalid.")
    if str(data.get("statusPolicy") or "success_only") not in HTTP_STATUS_POLICIES:
        _fail("HTTP_STATUS_POLICY_INVALID", "HTTP status policy is invalid.")
    if not _VARIABLE_PATTERN.fullmatch(str(data.get("outputVariable") or "").strip()):
        _fail("HTTP_OUTPUT_VARIABLE_INVALID", "HTTP outputVariable must be an identifier.")


def http_request_variable_references(data: Mapping[str, Any]) -> set[str]:
    references = set(_TEMPLATE_PATTERN.findall(str(data.get("url") or "")))
    for field in ("queryItems", "headerItems", "formFields"):
        for item in data.get(field, []) if isinstance(data.get(field), list) else []:
            binding = item.get("binding") if isinstance(item, dict) else None
            if isinstance(binding, dict) and binding.get("source") == "variable":
                references.add(str(binding.get("variable") or "").strip())
    binding = data.get("bodyBinding")
    if isinstance(binding, dict) and binding.get("source") == "variable":
        references.add(str(binding.get("variable") or "").strip())
    return {item for item in references if item}


def validate_http_request_credential(
    data: Mapping[str, Any],
    credential_lookup: Callable[[str], Any] | None,
) -> None:
    if not is_http_request_v2(data):
        return
    auth_type = str(data.get("authType") or "none").strip()
    if auth_type == "none":
        return
    if credential_lookup is None:
        _fail("HTTP_CREDENTIAL_VALIDATOR_UNAVAILABLE", "HTTP credential validation is unavailable.")
    credential_id = str(data.get("credentialId") or "").strip()
    try:
        record = credential_lookup(credential_id)
    except Exception as exc:
        raise WorkflowHttpRequestError(
            "HTTP_CREDENTIAL_UNAVAILABLE",
            "The selected HTTP credential is unavailable.",
        ) from exc
    if str(getattr(record, "status", "")) != "active":
        _fail("HTTP_CREDENTIAL_UNAVAILABLE", "The selected HTTP credential is unavailable.")
    if str(getattr(record, "catalog_project_id", "")).strip():
        _fail(
            "HTTP_CREDENTIAL_SCOPE_INVALID",
            "Catalog-scoped credentials cannot be used by workflow HTTP requests.",
        )
    if auth_type == "basic" and str(getattr(record, "kind", "")) != "generic":
        _fail("HTTP_BASIC_CREDENTIAL_INVALID", "Basic authentication requires a generic credential.")


def _resolve_binding(
    binding: Mapping[str, Any],
    variables: Mapping[str, WorkflowValue],
    *,
    label: str,
) -> WorkflowValue:
    if binding.get("source") == "variable":
        variable = str(binding.get("variable") or "").strip()
        if variable not in variables:
            _fail("HTTP_VARIABLE_UNAVAILABLE", f"{label} variable is unavailable.")
        return normalize_workflow_value(variables[variable], path=f"$.{variable}")
    return normalize_workflow_value(binding.get("value"), path=f"$.{label}")


def _scalar_text(value: WorkflowValue, *, label: str) -> str:
    if isinstance(value, (dict, list)):
        _fail("HTTP_SCALAR_VALUE_REQUIRED", f"{label} must resolve to a scalar value.")
    return "" if value is None else workflow_value_to_text(value)


def _redact_http_response_value(
    value: WorkflowValue,
    sensitive_tokens: set[str],
) -> WorkflowValue:
    tokens = sorted((token for token in sensitive_tokens if token), key=len, reverse=True)
    if isinstance(value, str):
        redacted = value
        for token in tokens:
            redacted = redacted.replace(token, "[REDACTED]")
        return redacted
    if isinstance(value, list):
        return [_redact_http_response_value(item, sensitive_tokens) for item in value]
    if isinstance(value, dict):
        return {
            str(_redact_http_response_value(key, sensitive_tokens)):
            _redact_http_response_value(item, sensitive_tokens)
            for key, item in value.items()
        }
    return value


def _render_url_template(template: str, variables: Mapping[str, WorkflowValue]) -> str:
    def replace(match: re.Match[str]) -> str:
        variable = match.group(1)
        if variable not in variables:
            _fail("HTTP_VARIABLE_UNAVAILABLE", "HTTP URL variable is unavailable.")
        value = _scalar_text(variables[variable], label="HTTP URL")
        return quote(value, safe="")

    rendered = _TEMPLATE_PATTERN.sub(replace, template)
    if "{{" in rendered or "}}" in rendered:
        _fail("HTTP_TEMPLATE_INVALID", "HTTP URL contains an invalid variable template.")
    return rendered


def _content_type_kind(raw: str) -> str:
    media_type = raw.split(";", 1)[0].strip().lower()
    if not media_type:
        return "text"
    if media_type in {"application/json", "application/dns-json"} or media_type.endswith("+json"):
        return "json"
    if (
        media_type.startswith("text/")
        or media_type == "application/xml"
        or media_type.endswith("+xml")
    ):
        return "text"
    _fail("HTTP_BINARY_RESPONSE_FORBIDDEN", "HTTP response must be JSON or UTF-8 text.")


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    return scheme, _canonical_hostname(parsed.hostname), parsed.port or (443 if scheme == "https" else 80)


async def execute_workflow_http_request(
    data: Mapping[str, Any],
    variables: Mapping[str, WorkflowValue],
    credentials: Any,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    url_validator: Callable[
        [str, str], Awaitable[tuple[str, ...] | None]
    ] = validate_public_workflow_url,
) -> dict[str, WorkflowValue]:
    validate_http_request_v2_config(data)
    validate_http_request_credential(data, credentials.get_public)
    method = str(data.get("method") or "GET").upper()
    url = _render_url_template(str(data.get("url") or ""), variables)
    query: list[tuple[str, str]] = []
    headers: dict[str, str] = {
        "Accept": "application/json, text/plain;q=0.9, text/*;q=0.8",
    }
    for field, target, label in (
        ("queryItems", query, "query"),
        ("headerItems", headers, "header"),
    ):
        for item in data.get(field, []):
            value = _scalar_text(
                _resolve_binding(item["binding"], variables, label=label),
                label=label,
            )
            if isinstance(target, list):
                target.append((str(item["name"]), value))
            else:
                target[str(item["name"])] = value
    auth_type = str(data.get("authType") or "none")
    sensitive_tokens: set[str] = set()
    if auth_type != "none":
        try:
            secret = credentials.resolve(str(data.get("credentialId") or ""))
        except Exception as exc:
            raise WorkflowHttpRequestError(
                "HTTP_CREDENTIAL_UNAVAILABLE",
                "The selected HTTP credential is unavailable.",
            ) from exc
        sensitive_tokens.update({secret, quote(secret, safe="")})
        if auth_type == "api_key":
            if str(data.get("apiKeyLocation") or "header") == "query":
                query.append((str(data.get("apiKeyName") or ""), secret))
            else:
                headers[str(data.get("apiKeyName") or "")] = secret
        elif auth_type == "bearer":
            headers["Authorization"] = f"Bearer {secret}"
        else:
            try:
                basic = json.loads(secret)
                username = str(basic["username"])
                password = str(basic["password"])
                if not username or not password or set(basic) - {"username", "password"}:
                    raise ValueError
            except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
                raise WorkflowHttpRequestError(
                    "HTTP_BASIC_CREDENTIAL_INVALID",
                    "Basic authentication credential must contain username and password.",
                ) from exc
            encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            sensitive_tokens.update(
                {
                    username,
                    password,
                    f"{username}:{password}",
                    encoded,
                    quote(encoded, safe=""),
                }
            )
            headers["Authorization"] = f"Basic {encoded}"

    body_mode = str(data.get("bodyMode") or "none")
    request_json: WorkflowValue | None = None
    request_content: str | None = None
    request_form: list[tuple[str, str]] | None = None
    if body_mode == "json":
        request_json = _resolve_binding(data["bodyBinding"], variables, label="request body")
        headers["Content-Type"] = "application/json"
    elif body_mode == "text":
        request_content = _scalar_text(
            _resolve_binding(data["bodyBinding"], variables, label="request body"),
            label="request body",
        )
        headers["Content-Type"] = "text/plain; charset=utf-8"
    elif body_mode == "form":
        request_form = []
        for item in data.get("formFields", []):
            request_form.append(
                (
                    str(item["name"]),
                    _scalar_text(
                        _resolve_binding(item["binding"], variables, label="form field"),
                        label="form field",
                    ),
                )
            )
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    timeout = int(data.get("timeoutSeconds") or 30)
    redirect_limit = int(data.get("redirectLimit") or 0)
    response_limit = int(data.get("responseLimitBytes") or DEFAULT_RESPONSE_BYTES)
    current_method = method
    current_url = str(httpx.URL(url).copy_merge_params(query)) if query else url
    current_json = request_json
    current_content = request_content
    current_form = request_form
    pinned_backend: _PinnedPublicNetworkBackend | None = None
    client_transport = transport
    if client_transport is None:
        pinned_backend = _PinnedPublicNetworkBackend()
        client_transport = _PinnedAsyncHTTPTransport(pinned_backend)
    try:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(
                transport=client_transport,
                timeout=httpx.Timeout(timeout),
                trust_env=False,
                follow_redirects=False,
            ) as client:
                for redirect_index in range(redirect_limit + 1):
                    approved_addresses = await url_validator(current_url, "public_only")
                    if pinned_backend is not None:
                        parsed_current = urlsplit(current_url)
                        if not approved_addresses:
                            _fail(
                                "HTTP_DNS_PIN_MISSING",
                                "HTTP destination validation did not approve an address.",
                            )
                        pinned_backend.approve(
                            _canonical_hostname(parsed_current.hostname),
                            parsed_current.port
                            or (443 if parsed_current.scheme.lower() == "https" else 80),
                            approved_addresses,
                        )
                    async with client.stream(
                        current_method,
                        current_url,
                        headers=headers,
                        json=current_json,
                        content=current_content,
                        data=current_form,
                    ) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location or redirect_index >= redirect_limit:
                                _fail(
                                    "HTTP_REDIRECT_LIMIT_EXCEEDED",
                                    "HTTP redirect limit was exceeded.",
                                )
                            redirected_url = urljoin(str(response.url), location)
                            if _origin(redirected_url) != _origin(str(response.url)):
                                _fail(
                                    "HTTP_CROSS_ORIGIN_REDIRECT",
                                    "Cross-origin HTTP redirects are forbidden.",
                                )
                            current_url = redirected_url
                            if response.status_code == 303 or (
                                response.status_code in {301, 302}
                                and current_method == "POST"
                            ):
                                current_method = "GET"
                                current_json = None
                                current_content = None
                                current_form = None
                                headers.pop("Content-Type", None)
                            continue
                        chunks: list[bytes] = []
                        received = 0
                        async for chunk in response.aiter_bytes():
                            received += len(chunk)
                            if received > response_limit:
                                _fail(
                                    "HTTP_RESPONSE_TOO_LARGE",
                                    "HTTP response exceeded the configured size limit.",
                                )
                            chunks.append(chunk)
                        body_bytes = b"".join(chunks)
                        content_type = str(response.headers.get("content-type") or "")
                        content_kind = _content_type_kind(content_type)
                        try:
                            body_text = body_bytes.decode("utf-8")
                        except UnicodeDecodeError as exc:
                            raise WorkflowHttpRequestError(
                                "HTTP_RESPONSE_NOT_UTF8",
                                "HTTP response must be UTF-8 text.",
                            ) from exc
                        response_mode = str(data.get("responseMode") or "auto")
                        parse_json = response_mode == "json" or (
                            response_mode == "auto" and content_kind == "json"
                        )
                        if parse_json:
                            try:
                                body: WorkflowValue = normalize_workflow_value(
                                    json.loads(body_text)
                                )
                            except (json.JSONDecodeError, ValueError) as exc:
                                raise WorkflowHttpRequestError(
                                    "HTTP_RESPONSE_JSON_INVALID",
                                    "HTTP response is not valid JSON.",
                                ) from exc
                        else:
                            body = body_text
                        if (
                            str(data.get("statusPolicy") or "success_only")
                            == "success_only"
                            and not 200 <= response.status_code < 300
                        ):
                            _fail(
                                "HTTP_STATUS_NOT_SUCCESSFUL",
                                f"HTTP request returned status {response.status_code}.",
                            )
                        safe_output = _redact_http_response_value({
                            "statusCode": response.status_code,
                            "ok": 200 <= response.status_code < 300,
                            "contentType": content_type.split(";", 1)[0].strip().lower(),
                            "headers": {
                                key.lower(): value
                                for key, value in response.headers.items()
                                if key.lower() in SAFE_RESPONSE_HEADERS
                            },
                            "receivedBytes": len(body_bytes),
                            "body": body,
                        }, sensitive_tokens)
                        assert isinstance(safe_output, dict)
                        return safe_output
    except WorkflowHttpRequestError:
        raise
    except httpx.TimeoutException as exc:
        raise WorkflowHttpRequestError("HTTP_TIMEOUT", "HTTP request timed out.") from exc
    except TimeoutError as exc:
        raise WorkflowHttpRequestError("HTTP_TIMEOUT", "HTTP request timed out.") from exc
    except (httpx.HTTPError, OSError) as exc:
        raise WorkflowHttpRequestError("HTTP_NETWORK_ERROR", "HTTP request failed.") from exc
    except Exception as exc:
        # URL validators and credential adapters may use their own exception classes.
        raise WorkflowHttpRequestError("HTTP_SECURITY_CHECK_FAILED", "HTTP request security check failed.") from exc
    _fail("HTTP_REQUEST_INCOMPLETE", "HTTP request did not complete.")
