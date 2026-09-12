from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import logging
import math
import re
import struct
from dataclasses import dataclass, field
from typing import Callable, Literal, Mapping, NoReturn
from urllib.parse import urlsplit

import httpx

from .egress import AuthorizedProviderTarget, ProviderEgressPolicy
from .provider_chat import ProviderChatEndpointResolver
from .repository import RouterRepositoryError
from .schemas import (
    MULTIMODAL_WORKLOAD_SHAPES,
    ConnectionKind,
    ProviderMultimodalAdapterContract,
    ProviderMultimodalSseRejectionReason,
    ProviderWorkloadExecutionShape,
)
from .service import ModelRouterService, RouterServiceError


PROVIDER_MULTIMODAL_PROTOCOL_VERSION = "modelmirror-provider-multimodal-v1"
R8B_EXECUTION_SHAPES: frozenset[ProviderWorkloadExecutionShape] = frozenset(
    {
        "chat_image_stream",
        "chat_document_stream",
        "vision_json_unary",
        "image_generation",
    }
)
R8C_EXECUTION_SHAPES: frozenset[ProviderWorkloadExecutionShape] = frozenset(
    {"audio_transcription", "audio_speech"}
)
R8D_EXECUTION_SHAPES: frozenset[ProviderWorkloadExecutionShape] = frozenset(
    {"chat_audio_input", "chat_audio_output", "audio_generation_stream"}
)
CHAT_AUDIO_PCM16_SAMPLE_RATE_HZ = 24_000
CHAT_AUDIO_PCM16_CHANNELS = 1
CHAT_AUDIO_PCM16_BITS_PER_SAMPLE = 16
CHAT_AUDIO_PCM16_BYTE_ORDER = "little"
CHAT_AUDIO_PCM16_MAX_BYTES = 25 * 1024 * 1024
CHAT_AUDIO_MAX_ENCODED_CHARS = CHAT_AUDIO_PCM16_MAX_BYTES * 4 // 3 + 16
CHAT_AUDIO_MAX_DELIVERY_TEXT_CHARS = 1024 * 1024
CHAT_AUDIO_MAX_SSE_EVENT_BYTES = 2 * 1024 * 1024
CHAT_AUDIO_MAX_SSE_STREAM_BYTES = 40 * 1024 * 1024
# The byte framer excludes the final event line ending and blank separator.
# Streaming pre-checks may temporarily include two CRLF pairs before framing.
CHAT_AUDIO_MAX_SSE_EVENT_FRAMING_BYTES = 4
CHAT_AUDIO_PCM16_PARAMETER_EVIDENCE = "empirical_route_playback_required_v1"
AUDIO_GENERATION_MIN_MEDIA_BYTES = 1024
AUDIO_GENERATION_MAX_MEDIA_BYTES = 25 * 1024 * 1024
AUDIO_GENERATION_MAX_ENCODED_CHARS = (
    AUDIO_GENERATION_MAX_MEDIA_BYTES * 4 // 3 + 16
)
AUDIO_GENERATION_MAX_SSE_STREAM_BYTES = (
    AUDIO_GENERATION_MAX_ENCODED_CHARS + 4 * 1024 * 1024
)
# OpenRouter may deliver the complete bounded audio payload in one SSE event.
# Keep that event inside the existing full-stream envelope; decoded media is
# still independently capped by AUDIO_GENERATION_MAX_MEDIA_BYTES.
AUDIO_GENERATION_MAX_SSE_EVENT_BYTES = AUDIO_GENERATION_MAX_SSE_STREAM_BYTES
AUDIO_GENERATION_MAX_COMPANION_TEXT_CHARS = 256 * 1024
AUDIO_GENERATION_MAX_NATIVE_FINISH_REASON_CHARS = 128
AUDIO_GENERATION_CONNECT_TIMEOUT_SECONDS = 15.0
AUDIO_GENERATION_READ_TIMEOUT_SECONDS = 180.0
AUDIO_GENERATION_WRITE_TIMEOUT_SECONDS = 180.0
AUDIO_GENERATION_POOL_TIMEOUT_SECONDS = 180.0
AUDIO_GENERATION_TOTAL_TIMEOUT_SECONDS = 300.0
OPENROUTER_AUDIO_GENERATION_REQUEST_CONTRACT = (
    "openrouter-audio-generation-chat-stream-v2"
)
_MAX_GENERATION_METADATA_BYTES = 256 * 1024
OPENROUTER_GENERATION_METADATA_REQUEST_TIMEOUT_SECONDS = 2.0
_OPENROUTER_GENERATION_ID_LOG_PATTERN = re.compile(
    r"([?&]id=)[^&\s]+",
    re.IGNORECASE,
)


def build_openrouter_audio_generation_payload(
    *,
    model_id: str,
    prompt: str,
    image_data_url: str | None = None,
) -> dict[str, object]:
    """Build the single OpenRouter music request used by certification and runtime."""

    content: str | list[dict[str, object]] = prompt
    if image_data_url:
        content = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": image_data_url},
            },
        ]
    return {
        "model": model_id,
        "stream": True,
        "messages": [{"role": "user", "content": content}],
    }


class _OpenRouterGenerationMetadataLogFilter(logging.Filter):
    """Keep HTTPX request telemetry without logging the opaque generation ID."""

    modelmirror_openrouter_generation_id_redactor = True

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if (
            record.name != "httpx"
            or not isinstance(args, tuple)
            or len(args) < 2
        ):
            return True
        url = str(args[1])
        redacted_url = url
        try:
            parsed = urlsplit(url)
            if parsed.hostname is not None:
                ipaddress.ip_address(parsed.hostname)
                redacted_url = f"{parsed.scheme}://[provider-address-redacted]"
        except ValueError:
            pass
        if "/generation?" in redacted_url:
            redacted_url = _OPENROUTER_GENERATION_ID_LOG_PATTERN.sub(
                r"\1[redacted]",
                redacted_url,
            )
        if redacted_url != url:
            clean_args = list(args)
            clean_args[1] = redacted_url
            record.args = tuple(clean_args)
        return True


def _install_httpx_generation_metadata_log_filter() -> None:
    logger = logging.getLogger("httpx")
    if any(
        getattr(item, "modelmirror_openrouter_generation_id_redactor", False)
        for item in logger.filters
    ):
        return
    logger.addFilter(_OpenRouterGenerationMetadataLogFilter())


_install_httpx_generation_metadata_log_filter()

# Fixed synthetic voice saying "Okay", generated offline as 8 kHz mono 16-bit PCM.
# Keeping the media in source makes certification deterministic and avoids network,
# user content, or runtime TTS dependencies.
_SYNTHETIC_AUDIO_PCM_BASE64 = (
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABAAAAAAD//wAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAIA/v8EAAUABAAEAAMABgAGAPn/+//0//7///8BAP//AQD//wAA/P/7//7/"
    "//8CAAkABgAMAAsACgAGAAMA//8CAAAA///8//n/DAAFABQA6v/N/9X/vf8aADYASABWADkAXQBHANn/vf9c/6j/0/8AABEAEgDl"
    "////2f/X/+f/0f8PAEYAYwC/ALEApgCNAFYAAgAJAOb/2//M/3f/iQBXAWkBXgEHAA8Amv/A/9//7P49ApcHRgkFCrsEkgBP/wD+"
    "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"
    "7/4H/3P+L/7y/Z/9y/2D/Wn99v1E/d794f2K/Sv+8f0A/on+IP7V/qj+6v4W/7kAcwFnAawE4ANKBLwGigSHBX0GUwT/BdEFuQRW"
    "BjUFXAVtBYYEwQStA6MDVQM3AukC5wFyASgCjgD1ANgAm/8gALL/4/6C/8v+t/7c/nL+iP5X/mT+Mf4y/ln+F/47/pv+8f3Q/nP+"
    "Xv5Q/4cA6//aAeIC2AGzA74DqwJaAzUEzgLKAyUEXANbBB0EDATdA/gDgQM3A6gDngL8AgMDFAKyAisCowH4AVUBBwEsAZAAjwBa"
    "ACwA8f/X/7D/bv9a/0T/9/4P/9z+yP7L/rP+of6u/qT+cf7K/7X/AwBxAS8BMwGCApUBBAJzApEBaQKsAgsC0QK4AmYCtQJQAmQC"
    "+AHkAe0BMAG5AU0BrQB/AV0AhQCTAHf/9f9W/wL/NP+n/r7+if5K/qf+Fv6N/pf+Xv4a/+b+EP+I/1L/nv/i/7r/GgD+/14ApwBy"
    "AAgB1gCtACUBmAC5AMoAUQC/AIwAVwCcAPD//P/o/53/l/+v/3L/m/+Y/03/Xf9C/zT/Uf80/3D/af91/6r/ZP+d/5L/gP+7/6T/"
    "xv/W/8//BAD//w8ARwA+AF4AbABAAHIAXgA/AH8ATgBeAIMAdgB3AJkAXQBsAF8ALwBcADMAQgA7ADMANwAPACcAAwAEABUA5/8H"
    "AA8AAQAQAOz/GAAPAPf/IQD+/xQAAADe/9v/2//E/8v/8//W/wEAIAD8/yYAFAAdADAAKgA0AFQAVQBhAGEAZABPAF4AVABLAEcA"
    "VABJAEgAUgA8ACsALQAPAP//CAD+/9//6v/c/8//y/+5/9f/xv/K/+j/z/8BAPr/9P8YAAIAFQAlAAkAOQAcABkALQAlACEAMwAz"
    "ACoAMQAlACkAJAAhABgAIwAgAB0AJQAbAA8AEwAEAPv/AgAAAOz/9P/v/+j/5f/c//P/5v/r//3/7f8OAAYAAwAWAAkAEwAWAA0A"
    "FwAOAA4ADwAOAA4ADwARAA0ADAAEAP//BAD5//7/AAD5////AwAAAAUAAgD8/wQA+/8BAAUAAAAHAAgACQAKAPz/BQD7////AAD8"
    "/wAA/v///wEA/f/+/wAA/v8AAAAAAwAEAAAAAgAFAAUABAAJAAkABgAIAAQAAgACAAEAAwABAAMAAAAAAAEA/v///wAA/P8AAAEA"
    "//8DAAEA//8AAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAAAAEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAD/////AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="
)


def _synthetic_wav_bytes() -> bytes:
    sample_rate = 8_000
    pcm = base64.b64decode(_SYNTHETIC_AUDIO_PCM_BASE64, validate=True)
    return b"".join(
        (
            b"RIFF",
            struct.pack("<I", 36 + len(pcm)),
            b"WAVEfmt ",
            struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16),
            b"data",
            struct.pack("<I", len(pcm)),
            pcm,
        )
    )


SYNTHETIC_AUDIO_WAV_BYTES = _synthetic_wav_bytes()
SYNTHETIC_AUDIO_WAV_BASE64 = base64.b64encode(
    SYNTHETIC_AUDIO_WAV_BYTES
).decode("ascii")


def is_complete_wav(
    content: bytes,
    *,
    expected_sample_rate_hz: int | None = None,
    expected_channels: int | None = None,
    expected_bits_per_sample: int | None = None,
) -> bool:
    """Return whether content is one complete PCM-style RIFF/WAVE container."""

    if (
        len(content) < 44
        or content[:4] != b"RIFF"
        or content[8:12] != b"WAVE"
        or int.from_bytes(content[4:8], "little") + 8 != len(content)
    ):
        return False
    offset = 12
    fmt_observed = False
    data_observed = False
    block_align: int | None = None
    data_size: int | None = None
    while offset + 8 <= len(content):
        chunk_id = content[offset : offset + 4]
        chunk_size = int.from_bytes(content[offset + 4 : offset + 8], "little")
        chunk_end = offset + 8 + chunk_size
        if chunk_end > len(content):
            return False
        if chunk_id == b"fmt ":
            if fmt_observed:
                return False
            if chunk_size < 16:
                return False
            (
                audio_format,
                channels,
                sample_rate,
                byte_rate,
                observed_block_align,
                bits_per_sample,
            ) = struct.unpack_from("<HHIIHH", content, offset + 8)
            bytes_per_sample = bits_per_sample // 8
            if (
                audio_format != 1
                or channels < 1
                or channels > 8
                or sample_rate < 8_000
                or sample_rate > 192_000
                or bits_per_sample not in {8, 16, 24, 32}
                or observed_block_align != channels * bytes_per_sample
                or byte_rate != sample_rate * observed_block_align
                or (
                    expected_sample_rate_hz is not None
                    and sample_rate != expected_sample_rate_hz
                )
                or (
                    expected_channels is not None
                    and channels != expected_channels
                )
                or (
                    expected_bits_per_sample is not None
                    and bits_per_sample != expected_bits_per_sample
                )
            ):
                return False
            block_align = observed_block_align
            fmt_observed = True
        elif chunk_id == b"data":
            if data_observed:
                return False
            if not fmt_observed or chunk_size <= 0:
                return False
            data_size = chunk_size
            data_observed = True
        offset = chunk_end + (chunk_size % 2)
    return bool(
        offset == len(content)
        and fmt_observed
        and data_observed
        and block_align
        and data_size
        and data_size % block_align == 0
    )


def is_valid_chat_audio_pcm16(content: bytes) -> bool:
    """Validate the bounded raw PCM16 transport shape, not its audible meaning."""

    if (
        not content
        or len(content) > CHAT_AUDIO_PCM16_MAX_BYTES
        or len(content) % 2 != 0
    ):
        return False
    if content.startswith((b"RIFF", b"ID3", b"OggS", b"fLaC")):
        return False
    if _starts_with_two_mpeg_layer3_frames(content):
        return False
    return True


def load_strict_json_object(raw: str) -> dict[str, object]:
    """Decode one JSON object while rejecting duplicate keys at every depth."""

    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        parsed: dict[str, object] = {}
        for key, value in pairs:
            if key in parsed:
                raise ValueError("duplicate JSON object key")
            parsed[key] = value
        return parsed

    payload = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    if not isinstance(payload, dict):
        raise ValueError("expected one JSON object")
    return payload


class R8DAudioSseContractError(ValueError):
    """Bounded structural failure shared by R8D certification and runtime."""

    def __init__(
        self,
        code: str,
        *,
        finish_reason: str | None = None,
        rejection_reason: ProviderMultimodalSseRejectionReason | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.finish_reason = finish_reason
        self.rejection_reason: ProviderMultimodalSseRejectionReason | None = (
            rejection_reason
            if rejection_reason is not None
            else "unclassified"
            if code == "invalid_sse"
            else None
        )


@dataclass(slots=True)
class R8DAudioSseEventBuffer:
    """Frame R8D SSE bytes while preserving CRLF state across chunks."""

    max_event_bytes: int
    _buffer: bytearray = field(default_factory=bytearray, init=False, repr=False)
    _raw_event_bytes: int = field(default=0, init=False, repr=False)
    _pending_cr: bool = field(default=False, init=False, repr=False)
    _last_was_lf: bool = field(default=False, init=False, repr=False)
    _last_lf_width: int = field(default=0, init=False, repr=False)
    _finished: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_event_bytes <= 0:
            raise ValueError("max_event_bytes must be positive")

    @property
    def buffered_input_bytes(self) -> int:
        return self._raw_event_bytes

    def feed(self, chunk: bytes) -> list[str]:
        if self._finished:
            raise R8DAudioSseContractError(
                "invalid_sse",
                rejection_reason="event_after_buffer_finish",
            )
        events: list[str] = []
        for value in chunk:
            if self._pending_cr:
                if value == 0x0A:
                    self._raw_event_bytes += 1
                    self._pending_cr = False
                    self._append_lf(events, source_width=2)
                    self._check_pending_bound()
                    continue
                self._pending_cr = False
                self._append_lf(events, source_width=1)
            self._raw_event_bytes += 1
            if value == 0x0D:
                self._pending_cr = True
            elif value == 0x0A:
                self._append_lf(events, source_width=1)
            else:
                self._buffer.append(value)
                self._last_was_lf = False
                self._last_lf_width = 0
            self._check_pending_bound()
        return events

    def finish(self) -> list[str]:
        if self._finished:
            return []
        self._finished = True
        events: list[str] = []
        if self._pending_cr:
            self._pending_cr = False
            self._append_lf(events, source_width=1)
        if self._buffer.strip():
            if self._raw_event_bytes > self.max_event_bytes:
                raise R8DAudioSseContractError(
                    "sse_event_too_large",
                    rejection_reason="event_too_large",
                )
            events.append(self._decode_event(bytes(self._buffer)))
        self._buffer.clear()
        self._raw_event_bytes = 0
        self._last_was_lf = False
        self._last_lf_width = 0
        return events

    def _append_lf(self, events: list[str], *, source_width: int) -> None:
        self._buffer.append(0x0A)
        if not self._last_was_lf:
            self._last_was_lf = True
            self._last_lf_width = source_width
            return
        event_input_bytes = (
            self._raw_event_bytes - self._last_lf_width - source_width
        )
        if event_input_bytes > self.max_event_bytes:
            raise R8DAudioSseContractError(
                "sse_event_too_large",
                rejection_reason="event_too_large",
            )
        events.append(self._decode_event(bytes(self._buffer[:-2])))
        self._buffer.clear()
        self._raw_event_bytes = 0
        self._last_was_lf = False
        self._last_lf_width = 0

    def _check_pending_bound(self) -> None:
        # A valid separator consumes at most two CRLF pairs (four input bytes).
        if self._raw_event_bytes > self.max_event_bytes + 4:
            raise R8DAudioSseContractError(
                "sse_event_too_large",
                rejection_reason="event_too_large",
            )

    @staticmethod
    def _decode_event(event: bytes) -> str:
        try:
            return event.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise R8DAudioSseContractError(
                "invalid_sse",
                rejection_reason="utf8_decode_failed",
            ) from exc


@dataclass(frozen=True, slots=True)
class R8DAudioSseFrame:
    payload: Mapping[str, object] | None
    choice: Mapping[str, object] | None
    delta: Mapping[str, object] | None
    usage: Mapping[str, object] | None
    usage_counts: tuple[int, int, int] | None
    finish_reason: str | None
    done: bool = False
    terminal_replay: bool = False


@dataclass(slots=True)
class R8DAudioSseContract:
    """Validate the common streamed envelope without retaining media content."""

    execution_shape: Literal[
        "chat_audio_input",
        "chat_audio_output",
        "audio_generation_stream",
    ]
    expected_model: str
    actual_model: str | None = None
    generation_id: str | None = None
    finish_reason: str | None = None
    empty_finish_reason_observed: bool = False
    done_observed: bool = False
    terminal_usage_replay_observed: bool = False
    usage_counts: tuple[int, int, int] | None = None

    @property
    def done_only_terminal_observed(self) -> bool:
        """Accept DONE-only termination only for the versioned Output contract."""

        return bool(
            self.execution_shape == "chat_audio_output"
            and self.done_observed
            and self.finish_reason is None
            and not self.empty_finish_reason_observed
        )

    @property
    def safe_terminal_observed(self) -> bool:
        return bool(
            self.done_observed
            and not (
                self.execution_shape == "chat_audio_output"
                and self.empty_finish_reason_observed
            )
            and (
                self.finish_reason == "stop"
                or self.done_only_terminal_observed
            )
        )

    def consume_event(self, event: str) -> R8DAudioSseFrame | None:
        data_lines: list[str] = []
        for line in event.split("\n"):
            stripped = line.lstrip()
            while stripped.startswith("\ufeff"):
                stripped = stripped[1:].lstrip()
            if not stripped or stripped.startswith(":"):
                continue
            if stripped.startswith("event:") and stripped[6:].strip():
                self._fail(
                    "reserved_sse_event",
                    rejection_reason="reserved_event_type",
                )
            if stripped.startswith("data:"):
                data_lines.append(stripped[5:].lstrip())
        if not data_lines:
            return None
        return self._consume_data("\n".join(data_lines))

    def _consume_data(self, data: str) -> R8DAudioSseFrame:
        if self.done_observed:
            self._fail("invalid_sse", rejection_reason="data_after_done")
        if data == "[DONE]":
            self.done_observed = True
            return R8DAudioSseFrame(
                payload=None,
                choice=None,
                delta=None,
                usage=None,
                usage_counts=None,
                finish_reason=None,
                done=True,
            )
        try:
            payload = load_strict_json_object(data)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise R8DAudioSseContractError(
                "invalid_sse",
                rejection_reason="invalid_json_object",
            ) from exc
        if "error" in payload:
            self._fail(
                "stream_error",
                rejection_reason="provider_error_envelope",
            )
        if self.terminal_usage_replay_observed:
            self._fail(
                "invalid_sse",
                rejection_reason="data_after_terminal_replay",
            )

        self._observe_identity(payload)
        if "choices" not in payload:
            self._fail("invalid_sse", rejection_reason="missing_choices")
        choices = payload["choices"]
        if not isinstance(choices, list) or len(choices) > 1:
            self._fail("invalid_sse", rejection_reason="invalid_choices_shape")

        usage = payload.get("usage")
        usage_mapping: Mapping[str, object] | None = None
        usage_counts: tuple[int, int, int] | None = None
        if usage is not None:
            usage_mapping, usage_counts = self._validate_usage(
                usage,
                require_complete=False,
            )

        if not choices:
            usage_mapping, usage_counts = self._validate_usage(
                usage,
                require_complete=True,
            )
            if (
                self.finish_reason != "stop"
                or self.terminal_usage_replay_observed
            ):
                self._fail(
                    "invalid_sse",
                    rejection_reason="unexpected_usage_terminal",
                )
            self.terminal_usage_replay_observed = True
            self.usage_counts = usage_counts
            return R8DAudioSseFrame(
                payload=payload,
                choice=None,
                delta=None,
                usage=usage_mapping,
                usage_counts=usage_counts,
                finish_reason=None,
                terminal_replay=True,
            )

        choice = choices[0]
        if not isinstance(choice, dict):
            self._fail("invalid_sse", rejection_reason="invalid_choice_shape")
        choice_index = choice.get("index")
        if choice_index is not None and (
            isinstance(choice_index, bool)
            or not isinstance(choice_index, int)
            or choice_index != 0
        ):
            self._fail("invalid_sse", rejection_reason="invalid_choice_index")
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            self._fail("invalid_sse", rejection_reason="invalid_delta_shape")
        candidate_finish = self._validate_finish_reasons(choice)

        if self.finish_reason is not None:
            replay_usage, replay_counts = self._validate_usage(
                usage,
                require_complete=True,
            )
            if (
                self.finish_reason != "stop"
                or candidate_finish != "stop"
                or self.terminal_usage_replay_observed
                or isinstance(choice_index, bool)
                or not isinstance(choice_index, int)
                or choice_index != 0
                or not set(choice).issubset(
                    {
                        "index",
                        "delta",
                        "finish_reason",
                        "native_finish_reason",
                        "logprobs",
                    }
                )
                or (
                    self.execution_shape != "audio_generation_stream"
                    and choice.get("native_finish_reason") not in (None, "stop")
                )
                or choice.get("logprobs") is not None
                or not set(delta).issubset({"content", "role"})
                or delta.get("content") not in (None, "")
                or delta.get("role") not in (None, "assistant")
            ):
                self._fail(
                    "invalid_sse",
                    rejection_reason="invalid_terminal_replay_shape",
                )
            self.terminal_usage_replay_observed = True
            self.usage_counts = replay_counts
            return R8DAudioSseFrame(
                payload=payload,
                choice=choice,
                delta=delta,
                usage=replay_usage,
                usage_counts=replay_counts,
                finish_reason=candidate_finish,
                terminal_replay=True,
            )

        if candidate_finish is not None:
            self.finish_reason = candidate_finish
            if candidate_finish != "stop":
                self._fail(
                    {
                        "error": "finish_error",
                        "content_filter": "finish_filter",
                        "length": "finish_length",
                    }.get(candidate_finish, "invalid_finish_reason"),
                    finish_reason=candidate_finish,
                )

        if self.execution_shape == "audio_generation_stream":
            content = delta.get("content")
            if not set(delta).issubset({"role", "content", "audio"}):
                self._fail(
                    "invalid_sse",
                    rejection_reason="unexpected_audio_generation_delta",
                )
            if delta.get("role") not in (None, "assistant"):
                self._fail("invalid_sse", rejection_reason="invalid_role")
            if content is not None and (
                not isinstance(content, str)
                or len(content) > AUDIO_GENERATION_MAX_COMPANION_TEXT_CHARS
            ):
                self._fail(
                    "invalid_sse",
                    rejection_reason="invalid_companion_content",
                )
        else:
            content = delta.get("content")
            if content is not None and not isinstance(content, str):
                self._fail("invalid_sse", rejection_reason="invalid_content_type")
            if (
                delta.get("refusal") not in (None, "")
                or delta.get("tool_calls") not in (None, [])
                or delta.get("function_call") not in (None, {})
            ):
                self._fail(
                    "invalid_sse",
                    rejection_reason="invalid_content_type",
                )

        if "audio" in delta:
            audio = delta["audio"]
            if audio is not None and not isinstance(audio, dict):
                self._fail("invalid_sse", rejection_reason="invalid_audio_shape")
            if isinstance(audio, dict):
                for key in ("data", "transcript"):
                    if (
                        key in audio
                        and audio[key] is not None
                        and not isinstance(audio[key], str)
                    ):
                        self._fail(
                            "invalid_sse",
                            rejection_reason="invalid_audio_field_type",
                        )

        if usage_counts is not None:
            self.usage_counts = usage_counts
        return R8DAudioSseFrame(
            payload=payload,
            choice=choice,
            delta=delta,
            usage=usage_mapping,
            usage_counts=usage_counts,
            finish_reason=candidate_finish,
        )

    def _observe_identity(self, payload: Mapping[str, object]) -> None:
        item_generation_id = self._optional_identifier(
            payload,
            "id",
            max_length=200,
        )
        if (
            item_generation_id is not None
            and self.generation_id is not None
            and item_generation_id != self.generation_id
        ):
            self._fail("invalid_sse", rejection_reason="generation_id_mismatch")
        self.generation_id = self.generation_id or item_generation_id

        model = self._optional_identifier(payload, "model", max_length=512)
        if model is None:
            return
        if (
            model != self.expected_model
            or (self.actual_model is not None and model != self.actual_model)
        ):
            self._fail("model_mismatch")
        self.actual_model = model

    def _validate_usage(
        self,
        value: object,
        *,
        require_complete: bool,
    ) -> tuple[Mapping[str, object], tuple[int, int, int] | None]:
        if not isinstance(value, dict) or not value:
            self._fail("invalid_sse", rejection_reason="invalid_usage_shape")
        token_values: dict[str, int] = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if key not in value:
                continue
            candidate = value[key]
            if (
                isinstance(candidate, bool)
                or not isinstance(candidate, int)
                or candidate < 0
                or candidate > (1 << 63) - 1
            ):
                self._fail("invalid_sse", rejection_reason="invalid_usage_value")
            token_values[key] = candidate
        cost = value.get("cost")
        if cost is not None and (
            isinstance(cost, bool)
            or not isinstance(cost, (int, float))
            or (isinstance(cost, float) and not math.isfinite(cost))
            or cost < 0
            or cost > (1 << 63) - 1
        ):
            self._fail("invalid_sse", rejection_reason="invalid_usage_value")
        if self.execution_shape != "audio_generation_stream" and set(
            token_values
        ) != {"prompt_tokens", "completion_tokens", "total_tokens"}:
            self._fail("invalid_sse", rejection_reason="incomplete_usage")
        if require_complete and set(token_values) != {
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
        }:
            self._fail(
                "invalid_sse",
                rejection_reason="incomplete_terminal_usage",
            )
        counts: tuple[int, int, int] | None = None
        if set(token_values) == {
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
        }:
            counts = (
                token_values["prompt_tokens"],
                token_values["completion_tokens"],
                token_values["total_tokens"],
            )
            if counts[2] != counts[0] + counts[1]:
                self._fail("invalid_sse", rejection_reason="usage_total_mismatch")
        elif not token_values and cost is None:
            self._fail("invalid_sse", rejection_reason="invalid_usage_shape")
        return value, counts

    def _validate_finish_reasons(
        self,
        choice: Mapping[str, object],
    ) -> str | None:
        finish = choice.get("finish_reason")
        if finish is None:
            normalized_finish = None
        elif finish == "":
            self.empty_finish_reason_observed = True
            normalized_finish = None
        elif isinstance(finish, str):
            normalized_finish = finish
        else:
            self._fail("invalid_finish_type")
        native_finish = choice.get("native_finish_reason")
        if native_finish not in (None, ""):
            if not isinstance(native_finish, str) or normalized_finish is None:
                self._fail(
                    "invalid_sse",
                    rejection_reason="native_finish_mismatch",
                )
            if self.execution_shape == "audio_generation_stream":
                if len(native_finish) > AUDIO_GENERATION_MAX_NATIVE_FINISH_REASON_CHARS:
                    self._fail(
                        "invalid_sse",
                        rejection_reason="native_finish_mismatch",
                    )
            elif native_finish != normalized_finish:
                self._fail(
                    "invalid_sse",
                    rejection_reason="native_finish_mismatch",
                )
        return normalized_finish

    def _optional_identifier(
        self,
        payload: Mapping[str, object],
        key: str,
        *,
        max_length: int,
    ) -> str | None:
        if key not in payload or payload[key] is None:
            return None
        value = payload[key]
        if not isinstance(value, str):
            self._fail("invalid_sse", rejection_reason="invalid_identifier_type")
        candidate = value.strip()
        if not candidate or len(candidate) > max_length:
            self._fail("invalid_sse", rejection_reason="invalid_identifier_value")
        return candidate

    @staticmethod
    def _fail(
        code: str,
        *,
        finish_reason: str | None = None,
        rejection_reason: ProviderMultimodalSseRejectionReason | None = None,
    ) -> NoReturn:
        raise R8DAudioSseContractError(
            code,
            finish_reason=finish_reason,
            rejection_reason=rejection_reason,
        )


def _starts_with_two_mpeg_layer3_frames(content: bytes) -> bool:
    """Reject a bare MP3 stream without treating one valid PCM sample as MP3."""

    def frame_length(offset: int) -> int | None:
        if len(content) - offset < 4:
            return None
        header = int.from_bytes(content[offset : offset + 4], "big")
        version_bits = (header >> 19) & 0x3
        layer_bits = (header >> 17) & 0x3
        bitrate_index = (header >> 12) & 0xF
        sample_rate_index = (header >> 10) & 0x3
        if (
            header >> 21 != 0x7FF
            or version_bits == 0b01
            or layer_bits != 0b01
            or bitrate_index in {0, 0xF}
            or sample_rate_index == 0b11
            or header & 0x3 == 0b10
        ):
            return None
        sample_rates = {
            0b11: (44_100, 48_000, 32_000),
            0b10: (22_050, 24_000, 16_000),
            0b00: (11_025, 12_000, 8_000),
        }
        mpeg1_bitrates = (
            0, 32, 40, 48, 56, 64, 80, 96,
            112, 128, 160, 192, 224, 256, 320, 0,
        )
        mpeg2_bitrates = (
            0, 8, 16, 24, 32, 40, 48, 56,
            64, 80, 96, 112, 128, 144, 160, 0,
        )
        sample_rate = sample_rates[version_bits][sample_rate_index]
        bitrate = (
            mpeg1_bitrates[bitrate_index]
            if version_bits == 0b11
            else mpeg2_bitrates[bitrate_index]
        )
        coefficient = 144_000 if version_bits == 0b11 else 72_000
        return coefficient * bitrate // sample_rate + ((header >> 9) & 0x1)

    first_length = frame_length(0)
    return bool(
        first_length is not None
        and first_length >= 4
        and first_length < len(content)
        and frame_length(first_length) is not None
    )


def chat_audio_pcm16_to_wav(content: bytes) -> bytes:
    """Wrap one verified raw PCM16 transport payload for WAV delivery."""

    if not is_valid_chat_audio_pcm16(content):
        raise ValueError("invalid chat audio PCM16 payload")
    block_align = CHAT_AUDIO_PCM16_CHANNELS * (CHAT_AUDIO_PCM16_BITS_PER_SAMPLE // 8)
    byte_rate = CHAT_AUDIO_PCM16_SAMPLE_RATE_HZ * block_align
    wav = b"".join(
        (
            b"RIFF",
            struct.pack("<I", 36 + len(content)),
            b"WAVEfmt ",
            struct.pack(
                "<IHHIIHH",
                16,
                1,
                CHAT_AUDIO_PCM16_CHANNELS,
                CHAT_AUDIO_PCM16_SAMPLE_RATE_HZ,
                byte_rate,
                block_align,
                CHAT_AUDIO_PCM16_BITS_PER_SAMPLE,
            ),
            b"data",
            struct.pack("<I", len(content)),
            content,
        )
    )
    if not is_complete_wav(
        wav,
        expected_sample_rate_hz=CHAT_AUDIO_PCM16_SAMPLE_RATE_HZ,
        expected_channels=CHAT_AUDIO_PCM16_CHANNELS,
        expected_bits_per_sample=CHAT_AUDIO_PCM16_BITS_PER_SAMPLE,
    ):
        raise ValueError("failed to build chat audio WAV delivery")
    return wav


@dataclass(frozen=True, slots=True)
class MultimodalAdapterSpec:
    contract: ProviderMultimodalAdapterContract
    execution_shape: ProviderWorkloadExecutionShape
    provider_kinds: frozenset[ConnectionKind]
    required_scopes: tuple[str, ...]
    certification_mode: Literal["sync", "async", "browser_assisted"]


@dataclass(frozen=True, slots=True)
class ProviderMultimodalTarget:
    provider_kind: ConnectionKind
    connection_id: str
    adapter_contract: ProviderMultimodalAdapterContract
    execution_shape: ProviderWorkloadExecutionShape
    endpoint_url: str
    generation_metadata_url: str | None
    _api_key: str = field(repr=False, compare=False)

    @classmethod
    def create(
        cls,
        *,
        provider_kind: ConnectionKind,
        connection_id: str,
        base_url: str,
        api_key: str,
        adapter_contract: ProviderMultimodalAdapterContract,
        execution_shape: ProviderWorkloadExecutionShape,
    ) -> "ProviderMultimodalTarget":
        multimodal_adapter_spec(adapter_contract, execution_shape)
        api_base = ProviderChatEndpointResolver.resolve(base_url).base_url
        if adapter_contract == "openrouter_images_v1":
            endpoint_url = f"{api_base}/images"
        elif adapter_contract == "openai_compatible_images_generations_v1":
            endpoint_url = f"{api_base}/images/generations"
        elif adapter_contract in {
            "openrouter_audio_transcription_json_v1",
            "openai_compatible_audio_transcription_multipart_v1",
        }:
            endpoint_url = f"{api_base}/audio/transcriptions"
        elif adapter_contract in {
            "openrouter_audio_speech_v1",
            "openai_compatible_audio_speech_v1",
        }:
            endpoint_url = f"{api_base}/audio/speech"
        else:
            endpoint_url = f"{api_base}/chat/completions"
        return cls(
            provider_kind=provider_kind,
            connection_id=connection_id,
            adapter_contract=adapter_contract,
            execution_shape=execution_shape,
            endpoint_url=endpoint_url,
            generation_metadata_url=(
                f"{api_base}/generation" if provider_kind == "openrouter" else None
            ),
            _api_key=str(api_key or ""),
        )

    def authorization_headers(
        self, extra: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        headers = dict(extra or {})
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers


class ProviderMultimodalTransport:
    """One-address transport for a qualified multimodal Adapter endpoint."""

    def __init__(self, egress_policy: ProviderEgressPolicy) -> None:
        self.egress_policy = egress_policy

    async def authorize(
        self, target: ProviderMultimodalTarget
    ) -> AuthorizedProviderTarget:
        return await self.egress_policy.authorize(target.endpoint_url)

    @staticmethod
    def build_authorized_json_request(
        client: httpx.AsyncClient,
        target: ProviderMultimodalTarget,
        authorized: AuthorizedProviderTarget,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Request:
        return client.build_request(
            "POST",
            authorized.pinned_urls[0],
            headers=authorized.request_headers(target.authorization_headers(headers)),
            extensions=authorized.extensions,
            json=dict(payload),
        )

    @staticmethod
    def build_authorized_multipart_request(
        client: httpx.AsyncClient,
        target: ProviderMultimodalTarget,
        authorized: AuthorizedProviderTarget,
        *,
        data: Mapping[str, str],
        files: Mapping[str, tuple[str, bytes, str]],
    ) -> httpx.Request:
        return client.build_request(
            "POST",
            authorized.pinned_urls[0],
            headers=authorized.request_headers(target.authorization_headers()),
            extensions=authorized.extensions,
            data=dict(data),
            files=dict(files),
        )

    @staticmethod
    async def send_authorized(
        client: httpx.AsyncClient, request: httpx.Request
    ) -> httpx.Response:
        return await client.send(request, stream=True, follow_redirects=False)

    async def fetch_openrouter_generation_model(
        self,
        client: httpx.AsyncClient,
        target: ProviderMultimodalTarget,
        generation_id: str,
        *,
        timeout_seconds: float = (
            OPENROUTER_GENERATION_METADATA_REQUEST_TIMEOUT_SECONDS
        ),
        on_dispatch: Callable[[], None] | None = None,
    ) -> str | None:
        """Resolve the actual model with one bounded, DNS-pinned metadata GET."""

        metadata_url = target.generation_metadata_url
        clean_generation_id = str(generation_id or "").strip()
        if (
            target.provider_kind != "openrouter"
            or not metadata_url
            or not clean_generation_id
        ):
            return None
        clean_timeout_seconds = max(0.001, float(timeout_seconds))
        authorized = await self.egress_policy.authorize(metadata_url)
        extensions = dict(authorized.extensions)
        extensions["timeout"] = {
            phase: clean_timeout_seconds
            for phase in ("connect", "read", "write", "pool")
        }
        request = client.build_request(
            "GET",
            authorized.pinned_urls[0],
            headers=authorized.request_headers(target.authorization_headers()),
            extensions=extensions,
            params={"id": clean_generation_id},
        )
        response: httpx.Response | None = None
        try:
            async with asyncio.timeout(clean_timeout_seconds):
                if on_dispatch is not None:
                    on_dispatch()
                response = await client.send(
                    request, stream=True, follow_redirects=False
                )
                if not 200 <= response.status_code < 300:
                    return None
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > _MAX_GENERATION_METADATA_BYTES:
                        return None
                    chunks.append(chunk)
                try:
                    payload = json.loads(b"".join(chunks))
                except (TypeError, ValueError):
                    return None
                data = payload.get("data") if isinstance(payload, dict) else None
                model = data.get("model") if isinstance(data, dict) else None
                return (
                    str(model).strip()
                    if isinstance(model, str) and model.strip()
                    else None
                )
        finally:
            if response is not None:
                await response.aclose()


_OPENAI_COMPATIBLE_KINDS: frozenset[ConnectionKind] = frozenset(
    {"newapi", "openai_compatible", "openai"}
)


MULTIMODAL_ADAPTER_SPECS: dict[
    ProviderMultimodalAdapterContract, MultimodalAdapterSpec
] = {
    "openrouter_chat_multimodal_v1": MultimodalAdapterSpec(
        "openrouter_chat_multimodal_v1",
        "chat_image_stream",
        frozenset({"openrouter"}),
        ("chat", "image"),
        "sync",
    ),
    "openai_compatible_chat_multimodal_v1": MultimodalAdapterSpec(
        "openai_compatible_chat_multimodal_v1",
        "vision_json_unary",
        _OPENAI_COMPATIBLE_KINDS,
        ("chat", "image"),
        "sync",
    ),
    "openrouter_chat_native_pdf_v1": MultimodalAdapterSpec(
        "openrouter_chat_native_pdf_v1",
        "chat_document_stream",
        frozenset({"openrouter"}),
        ("chat", "document"),
        "sync",
    ),
    "openrouter_images_v1": MultimodalAdapterSpec(
        "openrouter_images_v1",
        "image_generation",
        frozenset({"openrouter"}),
        ("image",),
        "sync",
    ),
    "openai_compatible_images_generations_v1": MultimodalAdapterSpec(
        "openai_compatible_images_generations_v1",
        "image_generation",
        _OPENAI_COMPATIBLE_KINDS,
        ("image",),
        "sync",
    ),
    "openrouter_audio_transcription_json_v1": MultimodalAdapterSpec(
        "openrouter_audio_transcription_json_v1",
        "audio_transcription",
        frozenset({"openrouter"}),
        ("audio",),
        "sync",
    ),
    "openai_compatible_audio_transcription_multipart_v1": MultimodalAdapterSpec(
        "openai_compatible_audio_transcription_multipart_v1",
        "audio_transcription",
        _OPENAI_COMPATIBLE_KINDS,
        ("audio",),
        "sync",
    ),
    "openrouter_audio_speech_v1": MultimodalAdapterSpec(
        "openrouter_audio_speech_v1",
        "audio_speech",
        frozenset({"openrouter"}),
        ("audio",),
        "sync",
    ),
    "openai_compatible_audio_speech_v1": MultimodalAdapterSpec(
        "openai_compatible_audio_speech_v1",
        "audio_speech",
        _OPENAI_COMPATIBLE_KINDS,
        ("audio",),
        "sync",
    ),
    "openrouter_chat_audio_v1": MultimodalAdapterSpec(
        "openrouter_chat_audio_v1",
        "chat_audio_input",
        frozenset({"openrouter"}),
        ("chat", "audio"),
        "sync",
    ),
    "openrouter_audio_generation_stream_v1": MultimodalAdapterSpec(
        "openrouter_audio_generation_stream_v1",
        "audio_generation_stream",
        frozenset({"openrouter"}),
        ("audio",),
        "sync",
    ),
    "openrouter_chat_video_v1": MultimodalAdapterSpec(
        "openrouter_chat_video_v1",
        "video_analysis_unary",
        frozenset({"openrouter"}),
        ("chat", "video"),
        "sync",
    ),
    "openrouter_video_jobs_v1": MultimodalAdapterSpec(
        "openrouter_video_jobs_v1",
        "video_generation_async",
        frozenset({"openrouter"}),
        ("video",),
        "async",
    ),
    "openai_realtime_sdp_v1": MultimodalAdapterSpec(
        "openai_realtime_sdp_v1",
        "realtime_voice_session",
        frozenset({"openai"}),
        ("realtime",),
        "browser_assisted",
    ),
}


_SHAPE_ALIASES: dict[
    tuple[ProviderMultimodalAdapterContract, ProviderWorkloadExecutionShape],
    ProviderWorkloadExecutionShape,
] = {
    ("openai_compatible_chat_multimodal_v1", "chat_image_stream"): (
        "chat_image_stream"
    ),
    ("openrouter_chat_multimodal_v1", "vision_json_unary"): (
        "vision_json_unary"
    ),
    ("openrouter_chat_audio_v1", "chat_audio_output"): "chat_audio_output",
    ("openrouter_chat_video_v1", "chat_video_stream"): "chat_video_stream",
}


def multimodal_adapter_spec(
    contract: ProviderMultimodalAdapterContract,
    execution_shape: ProviderWorkloadExecutionShape,
) -> MultimodalAdapterSpec:
    spec = MULTIMODAL_ADAPTER_SPECS[contract]
    if execution_shape == spec.execution_shape:
        return spec
    if (contract, execution_shape) in _SHAPE_ALIASES:
        return MultimodalAdapterSpec(
            contract=spec.contract,
            execution_shape=execution_shape,
            provider_kinds=spec.provider_kinds,
            required_scopes=spec.required_scopes,
            certification_mode=spec.certification_mode,
        )
    raise RouterServiceError(
        "provider_multimodal_adapter_shape_mismatch",
        "所选 Adapter 与执行形态不匹配。",
        status_code=422,
    )


def validate_multimodal_adapter(
    *,
    contract: ProviderMultimodalAdapterContract,
    execution_shape: ProviderWorkloadExecutionShape,
    provider_kind: ConnectionKind,
    scopes: list[str],
) -> MultimodalAdapterSpec:
    if execution_shape not in MULTIMODAL_WORKLOAD_SHAPES:
        raise RouterServiceError(
            "provider_multimodal_execution_shape_required",
            "该 Adapter 只能用于多模态执行形态。",
            status_code=422,
        )
    spec = multimodal_adapter_spec(contract, execution_shape)
    if provider_kind not in spec.provider_kinds:
        raise RouterServiceError(
            "provider_multimodal_adapter_provider_mismatch",
            "所选 Provider 类型不支持该 Adapter。",
            status_code=422,
        )
    missing = [scope for scope in spec.required_scopes if scope not in scopes]
    if missing:
        raise RouterServiceError(
            f"connection_{missing[0]}_scope_required",
            "连接缺少该多模态 Adapter 所需的 scope。",
            status_code=409,
        )
    return spec


class ProviderMultimodalCertificationSessionService:
    """Persist safe orchestration state; protocol runners land with R8B-R8F."""

    def __init__(self, router_service: ModelRouterService) -> None:
        self.router_service = router_service
        self.repository = router_service.repository

    def refresh(self, certification_id: str) -> None:
        session = self.repository.get_multimodal_certification_session(
            self.router_service.tenant_id,
            certification_id=certification_id,
        )
        if session is None:
            raise RouterServiceError(
                "provider_multimodal_certification_session_not_found",
                "未找到该多模态资格会话。",
                status_code=404,
            )
        if not session.get("upstream_operation_id"):
            raise RouterServiceError(
                "provider_multimodal_certification_result_uncertain",
                "资格提交结果待确认；同一幂等键不会重新发送。",
                status_code=409,
            )
        raise RouterServiceError(
            "provider_multimodal_certification_refresh_not_integrated",
            "该异步 Adapter 将在对应 R8 数据面批次接入，只读轮询尚未开放。",
            status_code=409,
        )

    def realtime_not_integrated(self, connection_id: str) -> None:
        try:
            connection = self.repository.get_connection(
                self.router_service.tenant_id, connection_id
            )
        except RouterRepositoryError as exc:
            raise RouterServiceError(
                "provider_multimodal_connection_missing",
                "未找到所选 Managed 连接。",
                status_code=404,
            ) from exc
        validate_multimodal_adapter(
            contract="openai_realtime_sdp_v1",
            execution_shape="realtime_voice_session",
            provider_kind=connection.kind,
            scopes=connection.scopes,
        )
        raise RouterServiceError(
            "provider_realtime_certification_not_integrated",
            "Realtime 浏览器辅助认证将在 R8F 接入；本批次不会创建付费会话。",
            status_code=409,
        )

    def realtime_complete_not_integrated(self, certification_id: str) -> None:
        session = self.repository.get_multimodal_certification_session(
            self.router_service.tenant_id,
            certification_id=certification_id,
        )
        if session is None:
            raise RouterServiceError(
                "provider_multimodal_certification_session_not_found",
                "未找到该 Realtime 资格会话。",
                status_code=404,
            )
        raise RouterServiceError(
            "provider_realtime_certification_not_integrated",
            "Realtime 浏览器辅助认证将在 R8F 接入；本批次不会保存媒体确认。",
            status_code=409,
        )
