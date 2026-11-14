"""Persistent private voice assets. Only the central control plane can dispatch."""
from __future__ import annotations

import hashlib
import uuid
import asyncio

try:
    from server.model_router.voice_library import VoiceControl, digest, rejected
    from server.model_router.repository import utc_now
except ModuleNotFoundError:
    from model_router.voice_library import VoiceControl, digest, rejected
    from model_router.repository import utc_now

from .voice_cloud import VoiceCloud, validate_sample
from .tts import SpeechResult


class VoiceLibrary:
    def __init__(self, router, cloud=None):
        self.control = VoiceControl(router)
        self.cloud = cloud or VoiceCloud(router.egress_policy)

    async def create(self, *, operation_id, model, name, audio, consent):
        if consent is not True or not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
            raise rejected("voice_input_invalid", "请填写音色名称并确认样本使用权限。", 422)
        raw, info = validate_sample(audio)
        request_hash = digest({"action": "create", "model": model, "name": name, "sample": hashlib.sha256(raw).hexdigest(), "consent": True})
        prior = self._prior(operation_id, request_hash)
        if prior:
            return prior
        state, base, key = self.control.snapshot(model)
        target = await self.cloud.authorize(base, "create")
        identifier = "voice_" + uuid.uuid4().hex
        asset = {"id": identifier, "version": 1, "name": name.strip(), "model": model,
            "connectionId": state["connectionId"], "connectionFingerprint": state["connectionFingerprint"],
            "created": utc_now(), "state": "unknown", "sample": info,
            "sampleHash": hashlib.sha256(raw).hexdigest(), "consentAt": utc_now(), "externalId": None}
        job, fresh = self.control.claim(operation_id, request_hash, state, "create", asset)
        if not fresh:
            return self._result(job)
        try:
            result = await asyncio.wait_for(self.cloud.enroll(target, key, action="create", model=model, prefix=identifier[-10:], sample=raw), 120)
            asset.update(result)
            self.control.finish(job, asset=asset)
        except Exception:
            # Keep the durable unknown marker. Never upload again automatically.
            raise rejected("voice_outcome_unknown", "创建结果尚未确认，请核对音色记录；不会自动重发。", 502) from None
        return self._result(self.control.operation(operation_id))

    def _prior(self, operation_id, request_hash):
        with self.control.transaction() as db:
            prior = self.control.read(db, "operation:" + str(operation_id))
        if prior:
            if prior["hash"] != request_hash:
                raise rejected("voice_operation_conflict", "请求编号已用于不同内容。")
            return self._result(prior)
        return None

    def _result(self, job):
        return {"operation": self.control.operation(job["id"]), "asset": self.control.public(self.control.asset(job["assetId"]))}

    async def change(self, identifier, action, operation_id):
        if action not in {"query", "delete"}:
            raise rejected("voice_action_invalid", "未知音色操作。", 422)
        request_hash = digest({"action": action, "asset": identifier})
        prior = self._prior(operation_id, request_hash)
        if prior:
            return prior
        asset = self.control.asset(identifier)
        recover_creation = action == "query" and asset["state"] == "unknown" and not asset["externalId"]
        if asset["state"] == "deleted" or (not asset["externalId"] and not recover_creation):
            raise rejected("voice_not_ready", "音色不存在或仍待核对。")
        state, base, key = self.control.snapshot(asset["model"], asset)
        target = await self.cloud.authorize(base, action)
        job, fresh = self.control.claim(operation_id, request_hash, state, action, asset)
        if not fresh:
            return self._result(job)
        try:
            result = await asyncio.wait_for(
                self.cloud.recover_creation(target, key, model=asset["model"], prefix=identifier[-10:]) if recover_creation
                else self.cloud.enroll(target, key, action=action, model=asset["model"], prefix="", voice_id=asset["externalId"]), 120)
            asset.update(result)
            self.control.finish(job, asset=asset, reconcile_creation=recover_creation)
        except Exception:
            raise rejected("voice_outcome_unknown", "操作结果尚未确认，请核对调用记录；不会自动重发。", 502) from None
        return self._result(self.control.operation(operation_id))

    async def synthesize(self, *, asset_id, model, text, response_format, speed, operation_id):
        if (not isinstance(text, str) or not text.strip() or len(text) > 4000
                or response_format != "wav" or speed != 1):
            raise rejected("voice_speech_options_invalid", "克隆音色首版使用 WAV 和默认语速，文本上限 4,000 字。", 422)
        asset = self.control.asset(asset_id)
        if asset["state"] != "ready":
            raise rejected("voice_not_ready", "此音色尚未就绪或已被删除。")
        state, base, key = self.control.snapshot(model, asset)
        target = await self.cloud.authorize(base, "synthesize")
        job, fresh = self.control.claim(operation_id, digest({"action": "synthesize", "asset": asset_id, "text": text, "model": model}), state, "synthesize", asset)
        if not fresh:
            raise rejected("voice_speech_replay_blocked", "该生成请求已有记录，请从语音历史恢复。")
        try:
            raw = await asyncio.wait_for(self.cloud.synthesize(target, key, model=model, voice_id=asset["externalId"], text=text), 120)
            self.control.finish(job)
        except Exception:
            raise rejected("voice_outcome_unknown", "语音结果尚未确认，请核对生成历史。", 502) from None
        return SpeechResult(content=raw, requested_model=model, actual_model=model, provider="dashscope",
            request_id=operation_id, generation_id=None, output_bytes=len(raw), response_format="wav", execution_mode="managed")
