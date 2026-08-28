from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import httpx
from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException

try:
    from server.workflow_native.secure_http import (
        PublicWorkflowResource,
        WorkflowHttpRequestError,
        fetch_public_workflow_resource,
        validate_public_workflow_url,
    )
except ModuleNotFoundError:
    from workflow_native.secure_http import (
        PublicWorkflowResource,
        WorkflowHttpRequestError,
        fetch_public_workflow_resource,
        validate_public_workflow_url,
    )


RSS_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
RSS_MAX_ITEM_BYTES = 256 * 1024
RSS_MAX_ITEMS = 200
RSS_SEEN_LIMIT = 10_000
RSS_ACCEPT = "application/rss+xml, application/atom+xml, application/xml, text/xml, text/plain;q=0.5"
_VARIABLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_SENSITIVE_QUERY_NAME = re.compile(
    r"(?:^|[_-])(?:auth|authorization|credential|password|passwd|secret|signature|sig|key|token|api[_-]?key|access[_-]?token|refresh[_-]?token)(?:$|[_-])",
    re.IGNORECASE,
)
_FORBIDDEN_XML = re.compile(
    rb"<!\s*(?:DOCTYPE|ENTITY)\b|<\s*(?:[A-Za-z_][\w.-]*:)?include\b[^>]*\b(?:href|parse)\s*=",
    re.IGNORECASE,
)
_CDATA_SECTION = re.compile(rb"<!\[CDATA\[.*?\]\]>", re.DOTALL)
_ATOM_NS = "http://www.w3.org/2005/Atom"
_CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"


class WorkflowRssError(RuntimeError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(f"{code}: {safe_message}")
        self.code = code
        self.safe_message = safe_message


def _fail(code: str, message: str) -> None:
    raise WorkflowRssError(code, message)


@dataclass(frozen=True, slots=True)
class NormalizedRssItem:
    item_key: str
    id: str | None
    title: str | None
    link: str | None
    published_at: str | None
    updated_at: str | None
    author: str | None
    summary: str | None
    content: str | None
    categories: list[str]

    def public_value(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("item_key", None)
        return {
            "id": payload["id"],
            "title": payload["title"],
            "link": payload["link"],
            "publishedAt": payload["published_at"],
            "updatedAt": payload["updated_at"],
            "author": payload["author"],
            "summary": payload["summary"],
            "content": payload["content"],
            "categories": payload["categories"],
        }


@dataclass(frozen=True, slots=True)
class ParsedRssFeed:
    format: str
    title: str | None
    items: tuple[NormalizedRssItem, ...]


@dataclass(frozen=True, slots=True)
class RssFetchResult:
    status_code: int
    etag: str | None
    last_modified: str | None
    feed: ParsedRssFeed | None


def validate_rss_config(data: Mapping[str, Any]) -> dict[str, Any]:
    try:
        version = int(data.get("contractVersion") or 0)
    except (TypeError, ValueError):
        version = 0
    if version != 1:
        _fail("RSS_CONTRACT_VERSION_INVALID", "RSS entry contractVersion must be 1.")
    feed_url = validate_rss_feed_url(str(data.get("feedUrl") or ""))
    try:
        interval = int(data.get("pollIntervalMinutes") or 15)
    except (TypeError, ValueError) as exc:
        raise WorkflowRssError(
            "RSS_POLL_INTERVAL_INVALID",
            "RSS polling interval must be an integer number of minutes.",
        ) from exc
    if not 5 <= interval <= 1440:
        _fail("RSS_POLL_INTERVAL_INVALID", "RSS polling interval must be between 5 and 1440 minutes.")
    event_variable = str(data.get("eventVariable") or "").strip()
    item_variable = str(data.get("itemVariable") or "").strip()
    if not _VARIABLE_PATTERN.fullmatch(event_variable) or not _VARIABLE_PATTERN.fullmatch(item_variable):
        _fail("RSS_VARIABLE_INVALID", "RSS output variables must use valid workflow variable names.")
    if event_variable == item_variable:
        _fail("RSS_VARIABLE_CONFLICT", "RSS event and item variables must be different.")
    return {
        "contractVersion": 1,
        "feedUrl": feed_url,
        "pollIntervalMinutes": interval,
        "eventVariable": event_variable,
        "itemVariable": item_variable,
    }


def validate_rss_feed_url(value: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 2048 or "{{" in clean or "}}" in clean:
        _fail("RSS_URL_INVALID", "RSS feed URL must be a fixed HTTPS URL up to 2048 characters.")
    parsed = urlsplit(clean)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.fragment:
        _fail("RSS_URL_INVALID", "RSS feed URL must be a fixed HTTPS URL without a fragment.")
    if parsed.username or parsed.password:
        _fail("RSS_URL_CREDENTIALS_FORBIDDEN", "Credentials embedded in RSS URLs are forbidden.")
    try:
        parsed.port
    except ValueError as exc:
        raise WorkflowRssError("RSS_URL_INVALID", "RSS feed URL contains an invalid port.") from exc
    hostname = str(parsed.hostname or "").strip().lower().rstrip(".")
    if (
        hostname == "localhost"
        or hostname.endswith((".local", ".internal"))
        or hostname in {"metadata.google.internal", "host.docker.internal"}
    ):
        _fail("RSS_PRIVATE_TARGET_FORBIDDEN", "Local, private, and metadata RSS targets are forbidden.")
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        _fail("RSS_PRIVATE_TARGET_FORBIDDEN", "Local, private, and reserved RSS targets are forbidden.")
    for name, _value in parse_qsl(parsed.query, keep_blank_values=True):
        if _SENSITIVE_QUERY_NAME.search(name):
            _fail("RSS_URL_SECRET_QUERY_FORBIDDEN", "RSS feed URLs cannot contain credential-like query parameters.")
    return clean


def rss_feed_fingerprint(feed_url: str) -> str:
    clean = validate_rss_feed_url(feed_url)
    parsed = urlsplit(clean)
    hostname = str(parsed.hostname or "").lower()
    port = parsed.port
    netloc = hostname if port in {None, 443} else f"{hostname}:{port}"
    canonical = urlunsplit(("https", netloc, parsed.path or "/", parsed.query, ""))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _text(element: Any | None) -> str | None:
    if element is None:
        return None
    value = "".join(element.itertext()).strip()
    return value or None


def _direct_child(element: Any, name: str, namespace: str | None = None) -> Any | None:
    expected = f"{{{namespace}}}{name}" if namespace else name
    for child in list(element):
        if child.tag == expected or (namespace is None and _local_name(str(child.tag)) == name):
            return child
    return None


def _normalized_date(value: str | None) -> str | None:
    if not value:
        return None
    clean = value.strip()
    parsed: datetime | None = None
    try:
        parsed = parsedate_to_datetime(clean)
    except (TypeError, ValueError, OverflowError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(clean.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _absolute_link(value: str | None) -> str | None:
    if not value:
        return None
    clean = value.strip()
    parsed = urlsplit(clean)
    return clean if parsed.scheme.lower() in {"http", "https"} and parsed.hostname else None


def _item_key(*, source_id: str | None, link: str | None, fallback: Mapping[str, Any]) -> str:
    if source_id:
        identity = f"id\0{source_id.strip()}"
    elif link:
        identity = f"link\0{link}"
    else:
        identity = "fallback\0" + json.dumps(
            fallback,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _validate_item_size(item: NormalizedRssItem) -> None:
    encoded = json.dumps(item.public_value(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > RSS_MAX_ITEM_BYTES:
        _fail("RSS_ITEM_TOO_LARGE", "An RSS item exceeded the 256 KiB normalized item limit.")


def _rss_item(element: Any) -> NormalizedRssItem:
    source_id = _text(_direct_child(element, "guid"))
    title = _text(_direct_child(element, "title"))
    link = _absolute_link(_text(_direct_child(element, "link")))
    published = _normalized_date(
        _text(_direct_child(element, "pubDate"))
        or _text(_direct_child(element, "published"))
    )
    updated = _normalized_date(_text(_direct_child(element, "updated")))
    author = _text(_direct_child(element, "author"))
    summary = _text(_direct_child(element, "description"))
    content = _text(_direct_child(element, "encoded", _CONTENT_NS)) or summary
    categories = [
        value
        for child in list(element)
        if _local_name(str(child.tag)) == "category"
        for value in [_text(child)]
        if value
    ]
    item = NormalizedRssItem(
        item_key=_item_key(
            source_id=source_id,
            link=link,
            fallback={"title": title, "publishedAt": published, "content": content},
        ),
        id=source_id,
        title=title,
        link=link,
        published_at=published,
        updated_at=updated,
        author=author,
        summary=summary,
        content=content,
        categories=categories,
    )
    _validate_item_size(item)
    return item


def _atom_text(element: Any, name: str) -> str | None:
    return _text(_direct_child(element, name, _ATOM_NS))


def _atom_item(element: Any) -> NormalizedRssItem:
    source_id = _atom_text(element, "id")
    title = _atom_text(element, "title")
    link: str | None = None
    for child in list(element):
        if child.tag != f"{{{_ATOM_NS}}}link":
            continue
        relation = str(child.attrib.get("rel") or "alternate").strip().lower()
        candidate = _absolute_link(str(child.attrib.get("href") or ""))
        if candidate and relation == "alternate":
            link = candidate
            break
    published = _normalized_date(_atom_text(element, "published"))
    updated = _normalized_date(_atom_text(element, "updated"))
    author_element = _direct_child(element, "author", _ATOM_NS)
    author = _text(_direct_child(author_element, "name", _ATOM_NS)) if author_element is not None else None
    summary = _atom_text(element, "summary")
    content = _atom_text(element, "content") or summary
    categories = [
        value
        for child in list(element)
        if child.tag == f"{{{_ATOM_NS}}}category"
        for value in [str(child.attrib.get("term") or "").strip()]
        if value
    ]
    item = NormalizedRssItem(
        item_key=_item_key(
            source_id=source_id,
            link=link,
            fallback={"title": title, "publishedAt": published or updated, "content": content},
        ),
        id=source_id,
        title=title,
        link=link,
        published_at=published,
        updated_at=updated,
        author=author,
        summary=summary,
        content=content,
        categories=categories,
    )
    _validate_item_size(item)
    return item


def parse_rss_feed(body: bytes, content_type: str) -> ParsedRssFeed:
    if len(body) > RSS_MAX_RESPONSE_BYTES:
        _fail("RSS_RESPONSE_TOO_LARGE", "RSS response exceeded the 2 MiB limit.")
    media_type = str(content_type or "").split(";", 1)[0].strip().lower()
    allowed = (
        media_type in {"application/rss+xml", "application/atom+xml", "application/xml", "text/xml", "text/plain"}
        or media_type.endswith("+xml")
    )
    if not allowed:
        _fail("RSS_CONTENT_TYPE_INVALID", "RSS responses must contain RSS, Atom, or XML text.")
    if _FORBIDDEN_XML.search(_CDATA_SECTION.sub(b"", body)):
        _fail("RSS_XML_EXTERNAL_REFERENCE_FORBIDDEN", "RSS XML cannot contain DTD, entities, or XInclude directives.")
    try:
        root = DefusedElementTree.fromstring(body)
    except (DefusedXmlException, ValueError, TypeError) as exc:
        raise WorkflowRssError("RSS_XML_UNSAFE", "RSS XML contains a forbidden construct.") from exc
    except Exception as exc:
        raise WorkflowRssError("RSS_XML_INVALID", "RSS response is not valid XML.") from exc
    root_name = _local_name(str(root.tag)).lower()
    items: list[NormalizedRssItem]
    title: str | None
    format_name: str
    if root_name == "rss" and str(root.attrib.get("version") or "").startswith("2"):
        channel = _direct_child(root, "channel")
        if channel is None:
            _fail("RSS_FORMAT_INVALID", "RSS 2.0 feed is missing its channel.")
        title = _text(_direct_child(channel, "title"))
        elements = [child for child in list(channel) if _local_name(str(child.tag)) == "item"]
        items = [_rss_item(element) for element in elements]
        format_name = "rss2"
    elif root.tag == f"{{{_ATOM_NS}}}feed":
        title = _atom_text(root, "title")
        elements = [child for child in list(root) if child.tag == f"{{{_ATOM_NS}}}entry"]
        items = [_atom_item(element) for element in elements]
        format_name = "atom1"
    else:
        _fail("RSS_FORMAT_UNSUPPORTED", "Only RSS 2.0 and Atom 1.0 feeds are supported.")
    if len(items) > RSS_MAX_ITEMS:
        _fail("RSS_TOO_MANY_ITEMS", "RSS feed exceeded the 200 item limit.")
    seen: set[str] = set()
    for item in items:
        if item.item_key in seen:
            _fail("RSS_DUPLICATE_ITEM_IDENTITY", "RSS feed contains duplicate item identities.")
        seen.add(item.item_key)
    return ParsedRssFeed(format=format_name, title=title, items=tuple(items))


async def fetch_rss_feed(
    feed_url: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    url_validator: Callable[[str, str], Awaitable[tuple[str, ...]]] | None = None,
) -> RssFetchResult:
    clean_url = validate_rss_feed_url(feed_url)
    headers = {"Accept": RSS_ACCEPT}
    if etag:
        headers["If-None-Match"] = str(etag)[:1024]
    if last_modified:
        headers["If-Modified-Since"] = str(last_modified)[:1024]
    kwargs: dict[str, Any] = {
        "headers": headers,
        "timeout_seconds": 30,
        "redirect_limit": 3,
        "response_limit_bytes": RSS_MAX_RESPONSE_BYTES,
        "require_https": True,
        "transport": transport,
    }
    target_validator = url_validator or validate_public_workflow_url

    async def validate_rss_target(url: str, policy: str) -> tuple[str, ...]:
        try:
            validate_rss_feed_url(url)
        except WorkflowRssError as exc:
            raise WorkflowHttpRequestError(exc.code, exc.safe_message) from exc
        return await target_validator(url, policy)

    kwargs["url_validator"] = validate_rss_target
    try:
        response: PublicWorkflowResource = await fetch_public_workflow_resource(clean_url, **kwargs)
    except WorkflowHttpRequestError as exc:
        raise WorkflowRssError(f"RSS_{exc.code}", exc.safe_message) from exc
    if response.status_code == 304:
        return RssFetchResult(
            status_code=304,
            etag=(response.headers.get("etag") or etag or "")[:1024] or None,
            last_modified=(response.headers.get("last-modified") or last_modified or "")[:1024] or None,
            feed=None,
        )
    if not 200 <= response.status_code < 300:
        _fail("RSS_HTTP_STATUS_INVALID", f"RSS endpoint returned status {response.status_code}.")
    return RssFetchResult(
        status_code=response.status_code,
        etag=str(response.headers.get("etag") or "")[:1024] or None,
        last_modified=str(response.headers.get("last-modified") or "")[:1024] or None,
        feed=parse_rss_feed(response.body, response.content_type),
    )


def rss_item_content_length(item: NormalizedRssItem | Mapping[str, Any]) -> int:
    payload = item.public_value() if isinstance(item, NormalizedRssItem) else dict(item)
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
