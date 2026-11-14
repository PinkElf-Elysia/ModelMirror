"""Tenant-scoped voice assets and atomic dispatch budgets in the existing control store.

No schema migration. Admin controls reuse stored credentials; RPG never receives keys.
The real gate is off by default, including query/delete. No provider calls in this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from contextlib import contextmanager
from urllib.parse import urlsplit

from .repository import utc_now, RouterRepositoryError
from .service import RouterServiceError

MODELS = (
    "qwen-audio-3.1-tts-plus", "qwen-audio-3.1-tts-flash",
    "qwen-audio-3.0-tts-plus", "qwen-audio-3.0-tts-flash",
    "cosyvoice-v3.5-plus", "cosyvoice-v3.5-flash", "cosyvoice-v3-plus", "cosyvoice-v3-flash",
)
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def rejected(code, message, status=409):
    return RouterServiceError(code, message, status_code=status)


def cloud_base(value):
    p = urlsplit(value)
    host = p.hostname or ""
    if (p.scheme != "https" or p.port not in (None, 443) or p.username or p.password
            or p.query or p.fragment or p.path.rstrip("/") not in ("", "/api/v1", "/compatible-mode/v1")
            or not (host in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"}
                    or re.fullmatch(r"[a-zA-Z0-9-]+\.(cn-beijing|ap-southeast-1)\.maas\.aliyuncs\.com", host))):
        raise rejected("voice_connection_invalid", "请选择北京或新加坡的阿里云 Model Studio 连接。")
    return f"https://{host}/api/v1"


class VoiceControl:
    def __init__(self, router):
        self.router = router
        self.repo = router.repository
        self.prefix = "voice-library:" + digest(router.tenant_id) + ":"

    @staticmethod
    def enabled():
        return os.getenv("MODEL_CONTROL_VOICE_LIBRARY_ENABLED", "false").lower() == "true"

    @contextmanager
    def transaction(self):
        with self.repo._lock, self.repo._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def read(self, db, name, default=None):
        row = db.execute("SELECT value FROM router_metadata WHERE key=?", (self.prefix + name,)).fetchone()
        return json.loads(row["value"]) if row else default

    def write(self, db, name, value):
        db.execute("INSERT INTO router_metadata(key,value,updated_at) VALUES(?,?,?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                   (self.prefix + name, json.dumps(value, ensure_ascii=False), utc_now()))

    def policy(self, db):
        return self.read(db, "control", {"revision": 0, "enabled": False, "connectionId": "",
            "connectionFingerprint": "", "modelIds": [], "limit": 0, "spent": 0})

    def state(self):
        with self.transaction() as db:
            return {**self.policy(db), "featureEnabled": self.enabled(), "supportedModels": list(MODELS)}

    def configure(self, value):
        required = {"operationId", "expectedRevision", "enabled", "connectionId", "modelIds", "limit"}
        if (not isinstance(value, dict) or set(value) != required
                or not isinstance(value["operationId"], str) or not IDENTIFIER.fullmatch(value["operationId"])
                or type(value["expectedRevision"]) is not int or type(value["enabled"]) is not bool
                or type(value["limit"]) is not int or not 0 <= value["limit"] <= 100000
                or not isinstance(value["modelIds"], list) or not value["modelIds"]
                or any(m not in MODELS for m in value["modelIds"])
                or len(value["modelIds"]) != len(set(value["modelIds"]))):
            raise rejected("voice_control_invalid", "音色路由配置无效。", 422)
        connection, _, fingerprint = self.repo.get_connection_credential_snapshot(self.router.tenant_id, value["connectionId"])
        cloud_base(connection.base_url)
        if not connection.enabled or "audio" not in connection.scopes:
            raise rejected("voice_connection_invalid", "连接需要启用音频能力。")
        with self.transaction() as db:
            op = "config:" + digest(value["operationId"])
            prior = self.read(db, op)
            if prior:
                if prior["hash"] != digest(value):
                    raise rejected("voice_operation_conflict", "此操作编号已用于其他配置。")
                return self.policy(db)
            current = self.policy(db)
            if current["revision"] != value["expectedRevision"] or value["limit"] < current["spent"]:
                raise rejected("voice_control_changed", "配置已变化或额度小于已使用次数，请重新读取。")
            result = {"revision": current["revision"] + 1, "enabled": value["enabled"],
                "connectionId": connection.id, "connectionFingerprint": fingerprint,
                "modelIds": value["modelIds"], "limit": value["limit"], "spent": current["spent"]}
            self.write(db, "control", result)
            self.write(db, op, {"hash": digest(value)})
            return result

    def snapshot(self, model, asset=None):
        state = self.state()
        if not state["featureEnabled"] or not state["enabled"]:
            raise rejected("voice_control_disabled", "音色库真实调用尚未启用，请在统一模型设置中配置。")
        if model not in state["modelIds"] or state["spent"] >= state["limit"]:
            raise rejected("voice_model_or_budget_blocked", "模型未开放或音色调用额度已用完。")
        try:
            connection, key, fingerprint = self.repo.get_connection_credential_snapshot(self.router.tenant_id, state["connectionId"])
        except RouterRepositoryError:
            raise rejected("voice_connection_changed", "连接已不可用，请在统一控制面重新确认。") from None
        if not connection.enabled or "audio" not in connection.scopes or fingerprint != state["connectionFingerprint"]:
            raise rejected("voice_connection_changed", "连接已变化，请在统一控制面重新确认。")
        base = cloud_base(connection.base_url)
        if asset and (asset["model"] != model or asset["connectionId"] != connection.id or asset["connectionFingerprint"] != fingerprint):
            raise rejected("voice_binding_changed", "此音色与当前模型或账号不匹配。")
        return state, base, key

    def catalog(self):
        state = self.state()
        reason = None
        try:
            self.snapshot(state["modelIds"][0] if state["modelIds"] else "")
        except RouterServiceError as exc:
            reason = exc.code
        return {"available": reason is None, "reason": reason,
            "remaining": max(0, state["limit"] - state["spent"]),
            "models": [{"id": m, "name": m, "available": reason is None} for m in state["modelIds"]]}

    def assets(self):
        with self.transaction() as db:
            rows = db.execute("SELECT value FROM router_metadata WHERE key LIKE ? ORDER BY updated_at DESC", (self.prefix + "asset:%",)).fetchall()
        assets = []
        for row in rows:
            asset = json.loads(row["value"])
            usable = asset["state"] == "ready"
            try:
                self.snapshot(asset["model"], asset)
            except RouterServiceError:
                usable = False
            assets.append({**self.public(asset), "usable": usable})
        return assets

    @staticmethod
    def public(asset):
        return {k: v for k, v in asset.items() if k not in {"externalId", "connectionId", "connectionFingerprint"}}

    def asset(self, identifier):
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
            raise rejected("voice_not_found", "音色不存在。", 404)
        with self.transaction() as db:
            value = self.read(db, "asset:" + identifier)
        if not value:
            raise rejected("voice_not_found", "音色不存在。", 404)
        return value

    def claim(self, operation_id, request_hash, state, action, asset):
        """Idempotency, current credentials and quota checked in ONE transaction."""
        if not isinstance(operation_id, str) or not IDENTIFIER.fullmatch(operation_id):
            raise rejected("voice_operation_invalid", "请求编号无效。", 422)
        with self.transaction() as db:
            prior = self.read(db, "operation:" + operation_id)
            if prior:
                if prior["hash"] != request_hash:
                    raise rejected("voice_operation_conflict", "此请求编号已用于不同内容。")
                return prior, False
            current = self.policy(db)
            row = db.execute("SELECT * FROM router_connections WHERE tenant_id=? AND id=?", (self.router.tenant_id, current["connectionId"])).fetchone()
            if (not self.enabled() or not current["enabled"] or current["revision"] != state["revision"]
                    or current["spent"] >= current["limit"] or row is None or not row["enabled"]
                    or self.repo._connection_config_fingerprint_row(row) != state["connectionFingerprint"]):
                raise rejected("voice_control_changed", "路由或额度已变化，本次未发送。")
            if action != "create":
                latest = self.read(db, "asset:" + asset["id"])
                if latest != asset:
                    raise rejected("voice_asset_changed", "音色状态已变化，请重新读取。")
            jobs = db.execute("SELECT value FROM router_metadata WHERE key LIKE ?", (self.prefix + "operation:%",)).fetchall()
            for row in jobs:
                j = json.loads(row["value"])
                if j["assetId"] != asset["id"] or j["state"] != "unknown" or j.get("acknowledgedAt") or j.get("reconciledBy"):
                    continue
                # A bounded read-only lookup can recover an enrollment response
                # lost after dispatch. Never repeat the upload or bypass a pending query.
                recovery = action == "query" and not asset.get("externalId") and j["action"] == "create"
                if not recovery:
                    raise rejected("voice_outcome_unknown", "此音色有未确认的请求，请先核对记录。")
                if (datetime.now(timezone.utc) - datetime.fromisoformat(j["created"])).total_seconds() < 180:
                    raise rejected("voice_reconcile_wait", "请在创建请求发出三分钟后查询，避免与仍在执行的创建请求重叠。")
            current["spent"] += 1
            job = {"id": operation_id, "hash": request_hash, "action": action, "assetId": asset["id"],
                "state": "unknown", "created": utc_now(), "model": asset["model"]}
            self.write(db, "control", current)
            self.write(db, "operation:" + operation_id, job)
            if action == "create":
                self.write(db, "asset:" + asset["id"], asset)
            return job, True

    def finish(self, job, *, asset=None, error=None, reconcile_creation=False):
        with self.transaction() as db:
            if asset is not None:
                # A local rename can complete while a cloud operation is in flight.
                latest = self.read(db, "asset:" + asset["id"])
                if latest:
                    asset = {**asset, "name": latest["name"]}
                self.write(db, "asset:" + asset["id"], asset)
            if reconcile_creation and not error and asset and asset.get("externalId") and job["action"] == "query":
                rows = db.execute("SELECT value FROM router_metadata WHERE key LIKE ?", (self.prefix + "operation:%",)).fetchall()
                for row in rows:
                    prior = json.loads(row["value"])
                    if prior["assetId"] == asset["id"] and prior["action"] == "create" and prior["state"] == "unknown":
                        self.write(db, "operation:" + prior["id"], {**prior, "reconciledBy": job["id"], "reconciledAt": utc_now()})
            self.write(db, "operation:" + job["id"], {**job, "state": "failed" if error else "complete", "error": error})

    def operation(self, identifier):
        with self.transaction() as db:
            job = self.read(db, "operation:" + identifier)
        if not job:
            raise rejected("voice_operation_not_found", "请求记录不存在。", 404)
        return {k: v for k, v in job.items() if k != "hash"}

    def rename(self, identifier, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
            raise rejected("voice_name_invalid", "音色名称需要 1 至 40 个字。", 422)
        asset = self.asset(identifier)
        with self.transaction() as db:
            latest = self.read(db, "asset:" + identifier)
            latest["name"] = name.strip()
            self.write(db, "asset:" + identifier, latest)
        return self.public(latest)

    def acknowledge(self, identifier):
        # Explicit reconciliation never rewrites the outcome or refunds quota.
        # Wait beyond the bounded cloud timeout before allowing another request.
        with self.transaction() as db:
            job = self.read(db, "operation:" + identifier)
            if not job:
                raise rejected("voice_operation_not_found", "请求记录不存在。", 404)
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(job["created"].replace("Z", "+00:00"))).total_seconds()
            if job["state"] != "unknown" or age < 300:
                raise rejected("voice_reconcile_wait", "请等待至少五分钟并核对云端记录后再继续。")
            job["acknowledgedAt"] = utc_now()
            self.write(db, "operation:" + identifier, job)
        return self.operation(identifier)
