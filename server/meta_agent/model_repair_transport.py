"""Single explicit repair call through the existing Meta Agent transports."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
from urllib.parse import urlsplit

from .headless_authoring import HeadlessAuthoringConflictError, HeadlessAuthoringError, canonical_checksum
from .model_repair import ModelRepairCallError, repair_request_body

_ROUTE_KEY = secrets.token_bytes(32)


class ModelRepairTransport:
    def __init__(self, gateway, *, legacy_config, legacy_completion):
        self.gateway = gateway
        self.legacy_config = legacy_config
        self.legacy_completion = legacy_completion

    def route_projection(self, model_id):
        mode = self.gateway.routing_mode()
        if mode == "degraded_required":
            raise HeadlessAuthoringConflictError("Managed 策略已失效，不能回退到其他网关。")
        if mode == "managed_required":
            policy = self.gateway.call_service.control.get_policy("meta_agent")
            binding = next((item for item in policy.bindings if item.model_id == model_id
                            and item.execution_shape == "chat_json_object" and item.valid), None)
            if binding is None:
                raise HeadlessAuthoringConflictError("所选精确模型没有有效的 Managed Binding。")
            return {"mode": mode, "label": f"Managed · {binding.provider_kind or 'provider'}",
                    "fingerprint": canonical_checksum({"policy": policy.policy_fingerprint,
                        "binding": binding.model_dump(mode="json", exclude={"connection_name", "reason_code"})})}
        return self._legacy_projection(self.legacy_config())

    @staticmethod
    def _legacy_projection(config):
        url, key = config
        parsed = urlsplit(url)
        if not url or not parsed.hostname or parsed.username or parsed.password:
            raise HeadlessAuthoringError("兼容网关未配置或地址无效，请先检查 Provider 设置。")
        return {"mode": "legacy", "label": f"兼容网关 · {parsed.hostname}",
                "fingerprint": hmac.new(_ROUTE_KEY, canonical_checksum({"endpoint": url, "credential": key}).encode("ascii"), hashlib.sha256).hexdigest()}

    async def complete(self, model_id, system, prompt, max_tokens, call_id, expected_route, dispatch_guard=None):
        if self.route_projection(model_id) != expected_route:
            raise HeadlessAuthoringConflictError("派发前路由已变化，本次未调用模型。")
        managed = None
        receipt = {"provider_dispatched": False, "response_received": False}
        expected_body = repair_request_body(model_id, system, prompt, max_tokens, expected_route["mode"])
        guard_failure = None

        def guard(body):
            nonlocal guard_failure
            if dispatch_guard is not None:
                try:
                    dispatch_guard()
                except HeadlessAuthoringError as exc:
                    receipt["provider_dispatched"] = False
                    guard_failure = ModelRepairCallError(receipt, reason_code="repair_basis_changed")
                    raise guard_failure from exc
            try:
                matches = self.route_projection(model_id) == expected_route and canonical_checksum(body) == canonical_checksum(expected_body)
            except HeadlessAuthoringError:
                matches = False
            if not matches:
                receipt["provider_dispatched"] = False
                raise ModelRepairCallError(receipt)
        try:
            if expected_route["mode"] == "managed_required":
                managed = self.gateway.start_run(parent_run_reference=call_id)
                # The run pins the current policy; recheck the confirmed policy too.
                if self.route_projection(model_id) != expected_route:
                    receipt["provider_dispatched"] = False
                    raise ModelRepairCallError(receipt)
                text = await managed.complete_json(logical_call_key=call_id, call_sequence=1, model_id=model_id,
                    system_prompt=system, user_prompt=prompt, temperature=0, max_tokens=max_tokens, request_guard=guard)
                row = managed.calls[-1]
                receipt = {"provider_dispatched": row.dispatched, "response_received": True,
                           "prompt_tokens": row.prompt_tokens, "completion_tokens": row.completion_tokens, "total_tokens": row.total_tokens}
                managed.finish("passed")
            else:
                config = self.legacy_config()
                if self._legacy_projection(config) != expected_route:
                    raise ModelRepairCallError(receipt)
                receipt["provider_dispatched"] = None
                async with asyncio.timeout(120):
                    text = await self.legacy_completion(model_id, system, prompt, max_tokens, receipt, guard, config)
                receipt.update(provider_dispatched=True, response_received=True)
            return {"text": text, "receipt": receipt}
        except asyncio.CancelledError:
            if managed is not None:
                managed.finish("uncertain", reason_code="explicit_repair_cancelled")
            raise
        except Exception as exc:
            if managed is not None:
                if managed.calls:
                    row = managed.calls[-1]
                    receipt.update(provider_dispatched=row.dispatched,
                                   response_received=row.dispatched and row.status in {"passed", "failed"},
                                   prompt_tokens=row.prompt_tokens, completion_tokens=row.completion_tokens, total_tokens=row.total_tokens)
                managed.finish("failed" if receipt["provider_dispatched"] is False or receipt["response_received"] else "uncertain",
                               reason_code="explicit_repair_call_failed")
            if guard_failure is not None and receipt["provider_dispatched"] is False:
                raise ModelRepairCallError(receipt, reason_code=guard_failure.reason_code) from exc
            if isinstance(exc, ModelRepairCallError):
                raise
            raise ModelRepairCallError(receipt) from exc
