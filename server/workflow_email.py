from __future__ import annotations

import hashlib
import imaplib
import ipaddress
import json
import re
import socket
import ssl
from dataclasses import dataclass
from datetime import timezone
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Callable, Mapping


EMAIL_MAX_RAW_BYTES = 1024 * 1024
EMAIL_MAX_CONTENT_BYTES = 256 * 1024
EMAIL_MAX_PARTS = 100
EMAIL_MAX_DEPTH = 20
EMAIL_MAX_ADDRESSES = 50
EMAIL_MAX_UIDS_PER_POLL = 100
EMAIL_BOUNDARY_START = "[不可信外部邮件边界]"
EMAIL_BOUNDARY_END = "[边界结束]"
_HEADER_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class WorkflowEmailError(RuntimeError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


def _fail(code: str, message: str) -> None:
    raise WorkflowEmailError(code, message)


def validate_email_host(value: str) -> str:
    host = str(value or "").strip().rstrip(".").lower()
    if not host or len(host) > 253 or "{{" in host or "}}" in host:
        _fail("EMAIL_HOST_INVALID", "Email server must be a fixed public hostname.")
    try:
        host.encode("ascii")
    except UnicodeEncodeError as exc:
        raise WorkflowEmailError(
            "EMAIL_HOST_INVALID", "Email server must use an ASCII hostname."
        ) from exc
    if host == "localhost" or "." not in host:
        _fail("EMAIL_PRIVATE_TARGET_FORBIDDEN", "Local email servers are forbidden.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        _fail("EMAIL_HOST_INVALID", "Email server must be a hostname, not an IP address.")
    labels = host.split(".")
    if any(
        not label
        or len(label) > 63
        or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) is None
        for label in labels
    ):
        _fail("EMAIL_HOST_INVALID", "Email server hostname is invalid.")
    return host


def _is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(address.is_global) and not any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_reserved,
            address.is_multicast,
            address.is_unspecified,
        )
    )


def resolve_public_email_ips(
    host: str,
    *,
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
) -> tuple[str, ...]:
    clean = validate_email_host(host)
    try:
        answers = resolver(clean, 993, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise WorkflowEmailError(
            "EMAIL_DNS_FAILED", "Email server DNS resolution failed."
        ) from exc
    addresses = tuple(dict.fromkeys(str(item[4][0]) for item in answers if item[4]))
    if not addresses or any(not _is_public_ip(item) for item in addresses):
        _fail(
            "EMAIL_PRIVATE_TARGET_FORBIDDEN",
            "Email server resolved to a local, private, or reserved address.",
        )
    return addresses


def validate_email_config(data: Mapping[str, Any]) -> dict[str, Any]:
    if data.get("contractVersion") != 1:
        _fail("EMAIL_CONTRACT_VERSION_INVALID", "Email entry contractVersion must be 1.")
    host = validate_email_host(str(data.get("host") or ""))
    credential_id = str(data.get("credentialId") or "").strip()
    if re.fullmatch(r"cred_[0-9a-f]{32}", credential_id) is None:
        _fail("EMAIL_CREDENTIAL_INVALID", "Select an encrypted email credential.")
    interval = data.get("pollIntervalMinutes", 15)
    if type(interval) is not int or not 5 <= interval <= 1440:
        _fail(
            "EMAIL_POLL_INTERVAL_INVALID",
            "Email polling interval must be between 5 and 1440 minutes.",
        )
    variables = [
        str(data.get(name) or "").strip()
        for name in ("eventVariable", "messageVariable", "contentVariable")
    ]
    if any(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", item) is None for item in variables):
        _fail("EMAIL_VARIABLE_INVALID", "Email output variables must be valid workflow variable names.")
    if len(set(variables)) != len(variables):
        _fail("EMAIL_VARIABLE_CONFLICT", "Email output variables must be different.")
    return {
        **dict(data),
        "host": host,
        "credentialId": credential_id,
        "pollIntervalMinutes": interval,
        "eventVariable": variables[0],
        "messageVariable": variables[1],
        "contentVariable": variables[2],
    }


def parse_email_credential(value: str) -> tuple[str, str]:
    try:
        payload = json.loads(str(value or ""))
    except json.JSONDecodeError as exc:
        raise WorkflowEmailError(
            "EMAIL_CREDENTIAL_INVALID", "Email credential is not valid JSON."
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {"username", "password"}:
        _fail(
            "EMAIL_CREDENTIAL_INVALID",
            "Email credential must contain only username and password.",
        )
    username = payload.get("username")
    password = payload.get("password")
    if (
        not isinstance(username, str)
        or not isinstance(password, str)
        or not username.strip()
        or not password
        or len(username) > 320
        or len(password) > 4096
        or any(ord(char) < 32 for char in username + password)
    ):
        _fail("EMAIL_CREDENTIAL_INVALID", "Email credential fields are invalid.")
    return username.strip(), password


class _SafeHtmlText(HTMLParser):
    _BLOCKED = {"script", "style", "form", "iframe", "object", "embed", "svg", "math"}
    _BREAKS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() in self._BLOCKED:
            self.depth += 1
        elif not self.depth and tag.lower() in self._BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._BLOCKED and self.depth:
            self.depth -= 1
        elif not self.depth and tag.lower() in self._BREAKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.depth:
            self.parts.append(data)

    def text(self) -> str:
        return "\n".join(
            line.strip()
            for line in re.sub(r"[ \t\r\f\v]+", " ", "".join(self.parts)).splitlines()
            if line.strip()
        )


def _decoded_text(part: Message) -> str:
    try:
        raw = part.get_payload(decode=True)
    except Exception as exc:
        raise WorkflowEmailError("EMAIL_MIME_INVALID", "Email MIME body is invalid.") from exc
    if raw is None:
        payload = part.get_payload()
        if not isinstance(payload, str):
            _fail("EMAIL_MIME_INVALID", "Email MIME body is invalid.")
        return payload
    charset = part.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset, errors="strict")
    except (LookupError, UnicodeDecodeError) as exc:
        raise WorkflowEmailError("EMAIL_CHARSET_INVALID", "Email character set is unsupported or invalid.") from exc


def _walk_parts(message: Message) -> list[tuple[Message, int]]:
    result: list[tuple[Message, int]] = []
    stack: list[tuple[Message, int]] = [(message, 0)]
    while stack:
        part, depth = stack.pop()
        if depth > EMAIL_MAX_DEPTH:
            _fail("EMAIL_MIME_TOO_DEEP", "Email MIME nesting exceeded the safe limit.")
        result.append((part, depth))
        if len(result) > EMAIL_MAX_PARTS:
            _fail("EMAIL_MIME_TOO_MANY_PARTS", "Email MIME part count exceeded the safe limit.")
        if part.is_multipart():
            children = list(part.iter_parts())
            stack.extend((child, depth + 1) for child in reversed(children))
    return result


def _addresses(message: Message, name: str) -> list[dict[str, str]]:
    raw_values = message.get_all(name, [])
    values = getaddresses([str(item) for item in raw_values])
    if len(values) > EMAIL_MAX_ADDRESSES:
        _fail("EMAIL_TOO_MANY_ADDRESSES", "Email address count exceeded the safe limit.")
    result: list[dict[str, str]] = []
    for display, address in values:
        clean_address = address.strip()
        if not clean_address or "\r" in clean_address or "\n" in clean_address:
            continue
        result.append({"name": display.strip()[:200], "address": clean_address[:320]})
    return result


def _date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class NormalizedEmailMessage:
    message: dict[str, Any]
    content: str
    raw_bytes: int


def parse_email_message(raw: bytes) -> NormalizedEmailMessage:
    if len(raw) > EMAIL_MAX_RAW_BYTES:
        _fail("EMAIL_MESSAGE_TOO_LARGE", "Email exceeded the 1 MiB message limit.")
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception as exc:
        raise WorkflowEmailError("EMAIL_MIME_INVALID", "Email MIME structure is invalid.") from exc
    if any(
        "\r" in str(value)
        or "\n" in str(value)
        or _HEADER_CONTROL_PATTERN.search(str(value)) is not None
        for value in message.values()
    ):
        _fail("EMAIL_HEADER_INVALID", "Email contains an unsafe header value.")
    parts = _walk_parts(message)
    plain: str | None = None
    html: str | None = None
    attachment_count = 0
    for part, _depth in parts:
        if part.is_multipart():
            continue
        disposition = str(part.get_content_disposition() or "inline").lower()
        if disposition == "attachment" or part.get_filename():
            attachment_count += 1
            continue
        content_type = part.get_content_type().lower()
        if content_type == "text/plain" and plain is None:
            plain = _decoded_text(part)
        elif content_type == "text/html" and html is None:
            html = _decoded_text(part)
    text = plain
    if text is None and html is not None:
        parser = _SafeHtmlText()
        try:
            parser.feed(html)
            parser.close()
        except Exception as exc:
            raise WorkflowEmailError("EMAIL_HTML_INVALID", "Email HTML body is invalid.") from exc
        text = parser.text()
    clean_text = (text or "").strip()
    content = f"{EMAIL_BOUNDARY_START}\n{clean_text}\n{EMAIL_BOUNDARY_END}"
    if len(content.encode("utf-8")) > EMAIL_MAX_CONTENT_BYTES:
        _fail("EMAIL_CONTENT_TOO_LARGE", "Normalized email content exceeded 256 KiB.")
    message_id = str(message.get("Message-ID") or "").strip() or None
    if message_id and (len(message_id) > 998 or "\r" in message_id or "\n" in message_id):
        _fail("EMAIL_HEADER_INVALID", "Email Message-ID is invalid.")
    return NormalizedEmailMessage(
        message={
            "messageId": message_id,
            "subject": str(message.get("Subject") or "").strip()[:998],
            "from": _addresses(message, "from"),
            "to": _addresses(message, "to"),
            "cc": _addresses(message, "cc"),
            "replyTo": _addresses(message, "reply-to"),
            "sentAt": _date(str(message.get("Date") or "") or None),
            "sizeBytes": len(raw),
            "hasAttachments": attachment_count > 0,
            "attachmentCount": attachment_count,
        },
        content=content,
        raw_bytes=len(raw),
    )


def email_source_fingerprint(host: str, credential_id: str) -> str:
    clean_host = validate_email_host(host)
    clean_credential = str(credential_id or "").strip()
    return hashlib.sha256(f"{clean_host}:993:{clean_credential}:INBOX".encode("utf-8")).hexdigest()


def email_message_key(uidvalidity: int, uid: int) -> str:
    return "sha256:" + hashlib.sha256(f"{uidvalidity}:{uid}".encode("ascii")).hexdigest()


class _PinnedIMAP4SSL(imaplib.IMAP4_SSL):
    def __init__(self, host: str, address: str, *, timeout: float = 30.0) -> None:
        self._pinned_address = address
        context = ssl.create_default_context()
        super().__init__(host=host, port=993, ssl_context=context, timeout=timeout)

    def _create_socket(self, timeout: float):  # type: ignore[override]
        raw = socket.create_connection((self._pinned_address, 993), timeout)
        try:
            wrapped = self.ssl_context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise
        peer = str(wrapped.getpeername()[0])
        if (
            ipaddress.ip_address(peer) != ipaddress.ip_address(self._pinned_address)
            or not _is_public_ip(peer)
        ):
            wrapped.close()
            raise WorkflowEmailError(
                "EMAIL_DNS_REBINDING_FORBIDDEN",
                "Email server connection did not match the validated public address.",
            )
        return wrapped


@dataclass(frozen=True, slots=True)
class EmailMailboxSnapshot:
    uidvalidity: int
    message_count: int
    highest_uid: int
    uids: tuple[int, ...]


class SecureImapClient:
    def __init__(
        self,
        host: str,
        credential_value: str,
        *,
        connector: Callable[..., imaplib.IMAP4_SSL] | None = None,
    ) -> None:
        self.host = validate_email_host(host)
        self.username, self.password = parse_email_credential(credential_value)
        self.connector = connector

    def _connect(self) -> imaplib.IMAP4_SSL:
        addresses = resolve_public_email_ips(self.host)
        client: imaplib.IMAP4_SSL | None = None
        try:
            client = (
                self.connector(self.host, addresses[0], timeout=30.0)
                if self.connector is not None
                else _PinnedIMAP4SSL(self.host, addresses[0], timeout=30.0)
            )
            status, _ = client.login(self.username, self.password)
            if status != "OK":
                _fail("EMAIL_AUTH_FAILED", "Email server authentication failed.")
            status, _ = client.select("INBOX", readonly=True)
            if status != "OK":
                _fail("EMAIL_MAILBOX_UNAVAILABLE", "INBOX could not be opened read-only.")
            return client
        except WorkflowEmailError:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    pass
            raise
        except (imaplib.IMAP4.error, OSError, ssl.SSLError) as exc:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    pass
            raise WorkflowEmailError("EMAIL_CONNECTION_FAILED", "Email server connection failed.") from exc

    @staticmethod
    def _uidvalidity(client: imaplib.IMAP4_SSL) -> int:
        raw = client.response("UIDVALIDITY")[1]
        if not raw:
            _fail("EMAIL_UIDVALIDITY_INVALID", "Email server did not provide UIDVALIDITY.")
        try:
            value = int(raw[0])
        except (TypeError, ValueError) as exc:
            raise WorkflowEmailError("EMAIL_UIDVALIDITY_INVALID", "Email UIDVALIDITY is invalid.") from exc
        if value <= 0:
            _fail("EMAIL_UIDVALIDITY_INVALID", "Email UIDVALIDITY is invalid.")
        return value

    def snapshot(self, *, after_uid: int | None = None) -> EmailMailboxSnapshot:
        client = self._connect()
        try:
            status, payload = client.uid("search", None, "ALL")
            if status != "OK":
                _fail("EMAIL_SEARCH_FAILED", "Email UID search failed.")
            raw = payload[0] if payload else b""
            uid_tokens = bytes(raw or b"").split()
            if any(not item.isdigit() for item in uid_tokens):
                _fail("EMAIL_UID_ORDER_INVALID", "Email server returned invalid UIDs.")
            uids = tuple(int(item) for item in uid_tokens)
            if tuple(sorted(set(uids))) != uids:
                _fail("EMAIL_UID_ORDER_INVALID", "Email server returned invalid UID order.")
            highest = uids[-1] if uids else 0
            selected = (
                uids[-EMAIL_MAX_UIDS_PER_POLL:]
                if after_uid is None
                else tuple(item for item in uids if item > after_uid)[:EMAIL_MAX_UIDS_PER_POLL]
            )
            return EmailMailboxSnapshot(
                self._uidvalidity(client), len(uids), highest, selected
            )
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def fetch(self, uid: int) -> NormalizedEmailMessage:
        client = self._connect()
        try:
            status, size_payload = client.uid(
                "fetch", str(int(uid)), "(RFC822.SIZE)"
            )
            size_text = b" ".join(
                bytes(item[0] if isinstance(item, tuple) else item)
                for item in size_payload
                if isinstance(item, (bytes, bytearray, tuple))
            )
            size_match = re.search(rb"RFC822\.SIZE\s+(\d+)", size_text)
            if status != "OK" or size_match is None:
                _fail(
                    "EMAIL_MESSAGE_SIZE_UNAVAILABLE",
                    "Email message size could not be verified.",
                )
            if int(size_match.group(1)) > EMAIL_MAX_RAW_BYTES:
                _fail("EMAIL_MESSAGE_TOO_LARGE", "Email exceeded the 1 MiB message limit.")
            status, payload = client.uid("fetch", str(int(uid)), "(BODY.PEEK[])")
            if status != "OK":
                _fail("EMAIL_MESSAGE_UNAVAILABLE", "Email message could not be read.")
            raw = next(
                (
                    bytes(item[1])
                    for item in payload
                    if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], (bytes, bytearray))
                ),
                b"",
            )
            if not raw:
                _fail("EMAIL_MESSAGE_UNAVAILABLE", "Email message no longer exists.")
            return parse_email_message(raw)
        finally:
            try:
                client.logout()
            except Exception:
                pass
