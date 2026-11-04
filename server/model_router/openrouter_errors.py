"""Bounded, allow-listed diagnostics for OpenRouter HTTP failures."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

import httpx

MAX_OPENROUTER_ERROR_ENVELOPE_BYTES = 16 * 1024
OPENROUTER_ERROR_ENVELOPE_CHUNK_BYTES = 4 * 1024
OPENROUTER_ERROR_ENVELOPE_TIMEOUT_SECONDS = 1.0


async def read_openrouter_error_code(
    response: httpx.Response, allowed_codes: Mapping[str, str],
) -> str | None:
    """Read a bounded error envelope and return only a caller-approved diagnostic."""

    media_type = response.headers.get("content-type", "").partition(";")[0]
    media_type = media_type.strip().lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        return None
    content_encoding = response.headers.get("content-encoding")
    if (
        content_encoding is not None
        and content_encoding.strip().lower() != "identity"
    ):
        return None

    declared_length = response.headers.get("content-length")
    if declared_length is not None:
        if (
            not declared_length.isascii()
            or not declared_length.isdigit()
            or len(declared_length)
            > len(str(MAX_OPENROUTER_ERROR_ENVELOPE_BYTES))
        ):
            return None
        parsed_length = int(declared_length)
        if parsed_length > MAX_OPENROUTER_ERROR_ENVELOPE_BYTES:
            return None

    chunks: list[bytes] = []
    total_bytes = 0
    try:
        async with asyncio.timeout(
            OPENROUTER_ERROR_ENVELOPE_TIMEOUT_SECONDS
        ):
            async for chunk in response.aiter_bytes(
                chunk_size=OPENROUTER_ERROR_ENVELOPE_CHUNK_BYTES
            ):
                total_bytes += len(chunk)
                if total_bytes > MAX_OPENROUTER_ERROR_ENVELOPE_BYTES:
                    return None
                chunks.append(chunk)
    except asyncio.CancelledError:
        # The HTTP status is already determinate. Optional subtype collection
        # must not downgrade it to an uncertain dispatched result.
        return None
    except (TimeoutError, httpx.HTTPError):
        return None

    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        parsed: dict[str, object] = {}
        for key, value in pairs:
            if key in parsed:
                raise ValueError("duplicate JSON object key")
            parsed[key] = value
        return parsed

    try:
        raw = b"".join(chunks).decode("utf-8", errors="strict")
        payload = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        TypeError,
        ValueError,
    ):
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    status_code = error.get("code")
    if (
        isinstance(status_code, bool)
        or not isinstance(status_code, int)
        or status_code != response.status_code
        or not isinstance(error.get("message"), str)
    ):
        return None
    metadata = error.get("metadata")
    if not isinstance(metadata, dict):
        return None
    error_type = metadata.get("error_type")
    if isinstance(error_type, str):
        return allowed_codes.get(error_type)
    # This older guardrail envelope has no error_type. Require the documented
    # message and structured patterns together; never retain a matched pattern.
    patterns = metadata.get("patterns")
    if (
        response.status_code == 403
        and error_type is None
        and error["message"] == "Request blocked: prompt injection patterns detected"
        and isinstance(patterns, list) and patterns
        and all(isinstance(p, str) and p for p in patterns)
    ):
        return allowed_codes.get("guardrail")
    return None
