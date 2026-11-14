"""Bounded, one-shot DashScope voice-enrollment and HTTP synthesis adapter."""
from __future__ import annotations

import base64
import io
import json
import re
import struct
import wave
from urllib.parse import urlsplit, urlunsplit

import httpx

try:
    from server.model_router.voice_library import rejected
except ModuleNotFoundError:
    from model_router.voice_library import rejected

MAX_SAMPLE = 10 * 1024 * 1024
MAX_AUDIO = 25 * 1024 * 1024


def normalize_cloud_wav(raw, content_length):
    """Finalize DashScope's observed streaming PCM header after complete download."""
    if (len(raw) >= 44 and raw[:4] == b"RIFF" and raw[8:16] == b"WAVEfmt "
            and raw[16:36] == struct.pack("<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16)
            and raw[36:40] == b"data"
            and struct.unpack_from("<I", raw, 4)[0] == 0x7FFFFFBF
            and struct.unpack_from("<I", raw, 40)[0] == 0x7FFFFF9B):
        # Do not infer completeness from EOF alone or silently repair ordinary
        # truncated WAVs. Require the provider's exact HTTP length and full frames.
        if str(len(raw)) != content_length or len(raw) <= 44 or (len(raw) - 44) % 2:
            raise ValueError("incomplete streaming audio")
        raw = bytearray(raw)
        struct.pack_into("<I", raw, 4, len(raw) - 8)
        struct.pack_into("<I", raw, 40, len(raw) - 44)
        return bytes(raw)
    return raw


def validate_sample(encoded):
    if not isinstance(encoded, str) or len(encoded) > MAX_SAMPLE * 4 // 3 + 4:
        raise rejected("voice_sample_invalid", "请上传不超过 10 MB 的 PCM16 WAV 音频。", 422)
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_SAMPLE:
            raise ValueError()
        with wave.open(io.BytesIO(raw), "rb") as wav:
            channels, width, rate, frames = wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
            duration = frames / rate
            if channels not in (1, 2) or width != 2 or not 16000 <= rate <= 96000 or not 5 <= duration <= 60:
                raise ValueError()
            if len(wav.readframes(frames)) != frames * channels * width:
                raise ValueError()
    except (ValueError, TypeError, EOFError, wave.Error, ZeroDivisionError):
        raise rejected("voice_sample_invalid", "参考音频需为完整 PCM16 WAV，5–60 秒、16kHz 以上。", 422) from None
    return raw, {"seconds": round(duration, 3), "sampleRate": rate, "channels": channels, "bytes": len(raw)}


class VoiceCloud:
    def __init__(self, egress, client_factory=None):
        self.egress = egress
        self.client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=60, follow_redirects=False, trust_env=False))

    async def authorize(self, base, action):
        suffix = "SpeechSynthesizer" if action == "synthesize" else "customization"
        return await self.egress.authorize(base + "/services/audio/tts/" + suffix)

    async def _send(self, target, key, payload):
        # Do not use the egress multi-address retry loop for mutating operations.
        async with self.client_factory() as client:
            request = client.build_request("POST", target.pinned_urls[0], json=payload,
                headers=target.request_headers({"Authorization": "Bearer " + key}), extensions=target.extensions)
            response = await client.send(request, stream=True, follow_redirects=False)
            try:
                chunks, size = [], 0
                async for part in response.aiter_bytes():
                    size += len(part)
                    if size > 256 * 1024:
                        raise ValueError("oversize response")
                    chunks.append(part)
                if not 200 <= response.status_code < 300:
                    # Upstream errors are not proof that no billable work happened.
                    raise ValueError("unconfirmed provider response")
                value = json.loads(b"".join(chunks))
                if not isinstance(value, dict) or not isinstance(value.get("output"), dict):
                    raise ValueError("invalid provider response")
                return value["output"]
            finally:
                await response.aclose()

    async def enroll(self, target, key, *, action, model, prefix, sample=None, voice_id=None):
        body = {"action": action + "_voice"}
        if action == "create":
            body.update(target_model=model, prefix=prefix, audio="data:audio/wav;base64," + base64.b64encode(sample).decode("ascii"))
        else:
            body["voice_id"] = voice_id
        output = await self._send(target, key, {"model": "voice-enrollment", "input": body})
        if action == "create":
            identifier = output.get("voice_id")
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", identifier):
                raise ValueError("invalid voice id")
            return {"externalId": identifier, "state": "processing"}
        if action == "query":
            status = output.get("status")
            if status not in {"OK", "DEPLOYING", "UNDEPLOYED"}:
                raise ValueError("unrecognized voice state")
            if output.get("target_model", model) != model or output.get("voice_id", voice_id) != voice_id:
                raise ValueError("voice identity mismatch")
            return {"state": {"OK": "ready", "DEPLOYING": "processing", "UNDEPLOYED": "unavailable"}[status]}
        return {"state": "deleted"}

    async def recover_creation(self, target, key, *, model, prefix):
        output = await self._send(target, key, {"model": "voice-enrollment", "input": {
            "action": "list_voice", "prefix": prefix, "page_index": 0, "page_size": 10}})
        voices = output.get("voice_list")
        if not isinstance(voices, list) or len(voices) != 1:
            raise ValueError("creation lookup is not unique")
        voice = voices[0]
        identifier, status = voice.get("voice_id"), voice.get("status")
        if (not isinstance(identifier, str)
                or not re.fullmatch(re.escape(model + "-" + prefix + "-") + r"[A-Za-z0-9_-]+", identifier)
                or voice.get("target_model", model) != model
                or status not in {"OK", "DEPLOYING", "UNDEPLOYED"}):
            raise ValueError("creation lookup identity mismatch")
        return {"externalId": identifier, "state": {"OK": "ready", "DEPLOYING": "processing", "UNDEPLOYED": "unavailable"}[status]}

    async def synthesize(self, target, key, *, model, voice_id, text):
        output = await self._send(target, key, {"model": model, "input": {
            "text": text, "voice": voice_id, "format": "wav", "sample_rate": 24000}})
        url = output.get("audio", {}).get("url")
        if not isinstance(url, str) or len(url) > 8192:
            raise ValueError("missing audio URL")
        p = urlsplit(url)
        # Some DashScope responses still use an HTTP OSS URL. Upgrade only
        # this recognized storage endpoint; never send an unsigned HTTP request.
        if (p.scheme == "http" and p.port is None and not p.username and not p.password
                and re.fullmatch(r"[a-zA-Z0-9.-]+\.oss-[a-zA-Z0-9-]+\.aliyuncs\.com", p.hostname or "")):
            p = p._replace(scheme="https")
        if (p.scheme != "https" or p.port not in (None, 443) or p.username or p.password or p.fragment
                or not (p.hostname or "").endswith(".aliyuncs.com")):
            raise ValueError("invalid audio URL")
        # Signed download URLs have a query. Pin the host without forwarding API keys.
        download = await self.egress.authorize(urlunsplit((p.scheme, p.netloc, p.path, "", "")))
        async with self.client_factory() as client:
            request = client.build_request("GET", download.pinned_urls[0] + ("?" + p.query if p.query else ""),
                headers=download.request_headers(), extensions=download.extensions)
            response = await client.send(request, stream=True, follow_redirects=False)
            try:
                if response.status_code != 200:
                    raise ValueError("audio not delivered")
                chunks, size = [], 0
                async for part in response.aiter_bytes():
                    size += len(part)
                    if size > MAX_AUDIO:
                        raise ValueError("audio too large")
                    chunks.append(part)
                raw = normalize_cloud_wav(b"".join(chunks), response.headers.get("content-length"))
                with wave.open(io.BytesIO(raw), "rb") as wav:
                    n = wav.getnframes()
                    if not n or wav.getsampwidth() != 2 or wav.getframerate() != 24000 or wav.getnchannels() != 1:
                        raise ValueError("audio format mismatch")
                    if len(wav.readframes(n)) != n * 2:
                        raise ValueError("truncated audio")
                return raw
            finally:
                await response.aclose()
