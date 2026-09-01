from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import Any


MAX_CAPTURED_IMAGES = 5
MAX_CAPTURED_IMAGE_BYTES = 5 * 1024 * 1024
MAX_CAPTURED_AUDIO_BYTES = 25 * 1024 * 1024

_IMAGE_FORMATS = {
    "image/jpeg": ("jpeg", ".jpg"),
    "image/png": ("png", ".png"),
    "image/webp": ("webp", ".webp"),
}
_AUDIO_FORMATS = {
    "mp3": ("mp3", "audio/mpeg", ".mp3"),
    "wav": ("wav", "audio/wav", ".wav"),
    "flac": ("flac", "audio/flac", ".flac"),
    "m4a": ("m4a", "audio/mp4", ".m4a"),
    "ogg": ("ogg", "audio/ogg", ".ogg"),
    "webm": ("audio_webm", "audio/webm", ".webm"),
}


@dataclass(frozen=True, slots=True)
class CapturedChatMedia:
    kind: str
    format_id: str
    media_type: str
    filename: str
    content: bytes


class ChatMediaCapture:
    """Collect only provider-embedded media bytes from an SSE response."""

    def __init__(self) -> None:
        self._images: list[CapturedChatMedia] = []
        self._image_hashes: set[str] = set()
        self._audio_encoded_parts: list[str] = []
        self._audio_encoded_length = 0
        self._audio_invalid = False

    def consume_line(self, line: str) -> None:
        stripped = line.strip()
        if not stripped.startswith("data:"):
            return
        raw = stripped[5:].strip()
        if not raw or raw == "[DONE]":
            return
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        choices = payload.get("choices")
        if not isinstance(choices, list):
            return
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            candidate = choice.get("delta") or choice.get("message")
            if not isinstance(candidate, dict):
                continue
            self._capture_images(candidate.get("content"))
            self._capture_images(candidate.get("images"))
            audio = candidate.get("audio")
            if isinstance(audio, dict):
                self._capture_audio_chunk(audio.get("data"))

    def items(self, *, audio_format: str | None = None) -> tuple[CapturedChatMedia, ...]:
        result = list(self._images)
        audio = self._decoded_audio()
        if audio is not None and audio_format:
            profile = _AUDIO_FORMATS.get(str(audio_format).strip().lower())
            if profile is not None:
                format_id, media_type, suffix = profile
                result.append(
                    CapturedChatMedia(
                        kind="audio",
                        format_id=format_id,
                        media_type=media_type,
                        filename="modelmirror-response" + suffix,
                        content=audio,
                    )
                )
        return tuple(result[:MAX_CAPTURED_IMAGES])

    def _capture_images(self, value: Any) -> None:
        if len(self._images) >= MAX_CAPTURED_IMAGES:
            return
        for candidate in _data_image_urls(value):
            parsed = _decode_data_image(candidate)
            if parsed is None:
                continue
            media_type, content = parsed
            digest = hashlib.sha256(content).hexdigest()
            if digest in self._image_hashes:
                continue
            format_id, suffix = _IMAGE_FORMATS[media_type]
            self._image_hashes.add(digest)
            self._images.append(
                CapturedChatMedia(
                    kind="image",
                    format_id=format_id,
                    media_type=media_type,
                    filename=f"modelmirror-image-{len(self._images) + 1}{suffix}",
                    content=content,
                )
            )
            if len(self._images) >= MAX_CAPTURED_IMAGES:
                return

    def _capture_audio_chunk(self, value: Any) -> None:
        if self._audio_invalid or not isinstance(value, str) or not value:
            return
        self._audio_encoded_length += len(value)
        if self._audio_encoded_length > (
            MAX_CAPTURED_AUDIO_BYTES * 4 // 3 + 16
        ):
            self._audio_invalid = True
            self._audio_encoded_parts.clear()
            return
        self._audio_encoded_parts.append(value)

    def _decoded_audio(self) -> bytes | None:
        if self._audio_invalid or not self._audio_encoded_parts:
            return None
        try:
            decoded = b"".join(
                base64.b64decode(part, validate=True)
                for part in self._audio_encoded_parts
            )
        except (binascii.Error, ValueError):
            try:
                decoded = base64.b64decode(
                    "".join(self._audio_encoded_parts), validate=True
                )
            except (binascii.Error, ValueError):
                self._audio_invalid = True
                self._audio_encoded_parts.clear()
                return None
        if not decoded or len(decoded) > MAX_CAPTURED_AUDIO_BYTES:
            self._audio_invalid = True
            self._audio_encoded_parts.clear()
            return None
        return decoded


def _data_image_urls(value: Any):
    if isinstance(value, list):
        for item in value[:MAX_CAPTURED_IMAGES * 4]:
            yield from _data_image_urls(item)
        return
    if not isinstance(value, dict):
        return
    image_url = value.get("image_url")
    if isinstance(image_url, dict):
        url = image_url.get("url")
        if isinstance(url, str) and url.startswith("data:image/"):
            yield url
    url = value.get("url")
    if isinstance(url, str) and url.startswith("data:image/"):
        yield url
    for key in ("content", "images"):
        nested = value.get(key)
        if isinstance(nested, (dict, list)):
            yield from _data_image_urls(nested)


def _decode_data_image(value: str) -> tuple[str, bytes] | None:
    if len(value) > (MAX_CAPTURED_IMAGE_BYTES * 4 // 3) + 512:
        return None
    header, separator, encoded = value.partition(",")
    if separator != "," or not header.endswith(";base64"):
        return None
    media_type = header[5:-7].lower()
    if media_type not in _IMAGE_FORMATS:
        return None
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not content or len(content) > MAX_CAPTURED_IMAGE_BYTES:
        return None
    return media_type, content


__all__ = ["CapturedChatMedia", "ChatMediaCapture"]
