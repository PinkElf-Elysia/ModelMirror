"""Opt-in, tenant-scoped RPG authoring route; never changes global Chat policy.

Configuration and the monotonic dispatch counter use the existing metadata
store. Dispatch marks and quota consumption share one SQLite transaction with
current credential/qualification checks. No schema migration or provider retry.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import time
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import Depends, HTTPException, Request
from .call_context import ProviderCallContextError
from .call_context_storage import verify_context
from .chat_control import ProviderChatControlService
from .chat_stable import ProviderChatStableService, ProviderChatStableDispatch, ProviderChatStablePreflight
from .provider_chat import ProviderChatTarget
from .repository import RouterRepositoryError, utc_now
from .service import RouterServiceError
from . import rpg_comparison as comparison

FORMAT = "modelmirror.rpg.authoring-route/1"
STRATEGY = "rpg_authoring_explicit"
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")

def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()

def enabled():
    return os.getenv("RPG_AUTHORING_ROUTING_ENABLED", "").lower() == "true"

def _key(tenant):
    return "rpg-authoring-route:" + _digest(tenant)

def _initial():
    return {"format": FORMAT, "revision": 0, "enabled": False, "connectionId": None,
            "connectionFingerprint": None, "modelIds": [], "limit": 0, "spent": 0}

def _read(db, tenant):
    row = db.execute("SELECT value FROM router_metadata WHERE key = ?", (_key(tenant),)).fetchone()
    if row is None:
        return _initial()
    try:
        value = json.loads(row["value"])
        if (set(value) != set(_initial()) or value["format"] != FORMAT
                or type(value["revision"]) is not int or value["revision"] < 1
                or type(value["enabled"]) is not bool
                or type(value["spent"]) is not int or type(value["limit"]) is not int
                or not 0 <= value["spent"] <= value["limit"] <= 100000
                or not isinstance(value["modelIds"], list) or not 1 <= len(value["modelIds"]) <= 100
                or any(not isinstance(m,str) or not 1 <= len(m) <= 200 for m in value["modelIds"])
                or not _ID.fullmatch(value["connectionId"])
                or not re.fullmatch(r"[a-f0-9]{64}", value["connectionFingerprint"])):
            raise ValueError()
        return value
    except (ValueError, TypeError, KeyError):
        raise RouterRepositoryError("rpg_authoring_route_unreadable") from None

def _save(db, key, value):
    db.execute("INSERT INTO router_metadata(key,value,updated_at) VALUES(?,?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
               (key, json.dumps(value, ensure_ascii=False, separators=(",", ":")), utc_now()))

def fingerprint(value):
    return _digest({k:v for k,v in value.items() if k != "spent"})

class AuthoringRouteControl(ProviderChatControlService):
    feature_enabled = staticmethod(enabled)

    def state(self):
        with self.repository._lock, self.repository._connect() as db:
            return _read(db, self.router_service.tenant_id)

    def configure(self, value):
        if not enabled():
            raise RouterServiceError("rpg_authoring_routing_disabled", "创作专用路由未启用。", status_code=503)
        fields = {"operationId", "expectedRevision", "enabled", "connectionId", "modelIds", "limit"}
        if (not isinstance(value, dict) or set(value) != fields
                or not isinstance(value["operationId"], str) or not _ID.fullmatch(value["operationId"])
                or type(value["expectedRevision"]) is not int or value["expectedRevision"] < 0
                or type(value["enabled"]) is not bool or not isinstance(value["connectionId"], str)
                or not _ID.fullmatch(value["connectionId"])
                or type(value["limit"]) is not int or not 0 <= value["limit"] <= 100000
                or not isinstance(value["modelIds"], list) or not 1 <= len(value["modelIds"]) <= 100
                or any(not isinstance(m,str) or not 1 <= len(m) <= 200 or any(ord(c)<32 for c in m) for m in value["modelIds"])
                or len(set(value["modelIds"])) != len(value["modelIds"])):
            raise RouterServiceError("rpg_authoring_route_invalid", "创作路由配置无效。", status_code=422)
        tenant = self.router_service.tenant_id
        opkey = _key(tenant) + ":op:" + _digest(value["operationId"])
        with self.repository._lock, self.repository._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT value FROM router_metadata WHERE key = ?", (opkey,)).fetchone()
            if prior:
                saved = json.loads(prior["value"])
                if saved["requestHash"] != _digest(value):
                    raise RouterServiceError("rpg_authoring_route_operation_conflict", "操作ID已用于不同配置。", status_code=409)
                return saved["result"]
            current = _read(db, tenant)
            if value["expectedRevision"] != current["revision"] or value["limit"] < current["spent"] + comparison.held(comparison.load(db, tenant)):
                raise RouterServiceError("rpg_authoring_route_revision_or_budget_conflict", "配置已改变或额度小于已用次数。", status_code=409)
            row = db.execute("SELECT * FROM router_connections WHERE tenant_id=? AND id=?", (tenant,value["connectionId"])).fetchone()
            if row is None or not row["enabled"] or "chat" not in json.loads(row["scopes_json"]):
                raise RouterServiceError("rpg_authoring_route_connection_invalid", "所选连接不可用。", status_code=409)
            result = {"format":FORMAT,"revision":current["revision"]+1,"enabled":value["enabled"],
                      "connectionId":value["connectionId"],"connectionFingerprint":self.repository._connection_config_fingerprint_row(row),
                      "modelIds":list(value["modelIds"]),"limit":value["limit"],"spent":current["spent"]}
            _save(db,_key(tenant),result);_save(db,opkey,{"requestHash":_digest(value),"result":result})
            return result

    def peek_policy(self):
        state = self.state()
        return SimpleNamespace(policy_fingerprint=fingerprint(state), effective_mode=STRATEGY,
                               revision=state["revision"], state=state, authoring_model_ids=state["modelIds"])

    get_policy = peek_policy

    def route_admission(self, policy, model_id, *, scoped_certified=False):
        if not enabled() or not policy.state["enabled"]:
            return False, "rpg_authoring_routing_disabled"
        if model_id not in policy.state["modelIds"]:
            return False, "rpg_authoring_model_not_allowed"
        return True, None

    @staticmethod
    def route_candidates(policy, capability):
        return [policy.state["connectionId"]] if capability == "chat_text" and policy.state["enabled"] else []

class AuthoringStableService(ProviderChatStableService):
    def __init__(self, router_service, *, transport=None):
        super().__init__(router_service)
        self.control = AuthoringRouteControl(router_service)
        self._comparison_requests = {}
        if transport is not None:
            self.transport = transport

    async def begin_rpg_authoring_certified(self, model_id, connection_id, *, comparison_request=None):
        policy = self.control.peek_policy()
        admitted, reason = self.control.route_admission(policy, model_id, scoped_certified=True)
        if not admitted or connection_id not in self.control.route_candidates(policy, "chat_text"):
            return ProviderChatStablePreflight(intercepted=True,error_code=reason or "rpg_authoring_selection_changed")
        with self.repository._lock, self.repository._connect() as db:
            holds = comparison.held(comparison.load(db, self.router_service.tenant_id))
            if comparison_request is not None:
                batch, _ = comparison.validate_task(db, self.router_service.tenant_id, comparison_request)
                if batch["plan"]["routeRevision"] != policy.revision:
                    return ProviderChatStablePreflight(intercepted=True, error_code="rpg_authoring_selection_changed")
            elif policy.state["spent"] + holds >= policy.state["limit"]:
                return ProviderChatStablePreflight(intercepted=True,error_code="rpg_authoring_budget_exhausted")
        reconciled = self.repository.reconcile_chat_control_completions(self.router_service.tenant_id)
        if int(reconciled.get("pending",0)):
            return ProviderChatStablePreflight(intercepted=True,error_code="provider_chat_completion_reconciliation_pending")
        bindings, reason = self._scoped_bindings_for_connection(policy,model_id=model_id,connection_id=connection_id,required_capabilities=("chat_text",))
        if bindings is None:
            return ProviderChatStablePreflight(intercepted=True,error_code=reason)
        connection, key, current = self.repository.get_connection_credential_snapshot(self.router_service.tenant_id,connection_id)
        if current != policy.state["connectionFingerprint"]:
            return ProviderChatStablePreflight(intercepted=True,error_code="rpg_authoring_selection_changed")
        target = ProviderChatTarget.create(source="managed",provider_kind=connection.kind,base_url=connection.base_url,api_key=key,connection_id=connection_id)
        authorized = await self.transport.authorize_managed_target(target)
        tenant=self.router_service.tenant_id;run_id="chatrun_"+uuid.uuid4().hex;attempt_id="chatattempt_"+uuid.uuid4().hex
        context = self._call_context("rpg_scoped", run_id, attempt_id, policy.revision)
        self.repository.claim_chat_control_run(tenant,run_id=run_id,policy_fingerprint=policy.policy_fingerprint,capability="chat_text",requested_model=model_id,strategy=STRATEGY,gateway="rpg_scoped",is_real_user=True,primary_newapi=connection.kind=="newapi")
        self.repository.claim_chat_control_attempt(tenant,attempt_id=attempt_id,run_id=run_id,capability="chat_text",position=0,connection_id=connection_id,provider_kind=connection.kind,call_context=context)
        self._call_origins[attempt_id] = ("rpg_scoped", context)
        if comparison_request is not None:
            self._comparison_requests[attempt_id] = comparison_request
        return ProviderChatStablePreflight(intercepted=True,dispatch=ProviderChatStableDispatch(
            run_id=run_id,attempt_id=attempt_id,policy_fingerprint=policy.policy_fingerprint,strategy=STRATEGY,capability="chat_text",
            requested_model=model_id,target=target,connection_fingerprint=current,authorized=authorized,position=0,reason_codes=(),
            certification_id=bindings["chat_text"].certification_id,started_at=time.perf_counter(),required_certifications=(bindings["chat_text"],),gateway="rpg_scoped",call_context=context))

    def mark_dispatched(self, dispatch):
        tenant=self.router_service.tenant_id;repo=self.repository
        try:
            self.ensure_dispatch_current(dispatch)
            if not enabled() or dispatch.strategy != STRATEGY or dispatch.gateway != "rpg_scoped" or dispatch.capability != "chat_text":
                raise RouterRepositoryError("rpg_authoring_route_changed")
            with repo._lock, repo._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                state=_read(db,tenant)
                saved_context = verify_context(db, tenant, dispatch.attempt_id, dispatch.call_context)
                if saved_context.policy_revision != state["revision"]:
                    raise RouterRepositoryError("rpg_authoring_route_changed")
                if (not state["enabled"] or fingerprint(state)!=dispatch.policy_fingerprint
                        or state["connectionId"]!=dispatch.target.connection_id
                        or state["connectionFingerprint"]!=dispatch.connection_fingerprint
                        or dispatch.requested_model not in state["modelIds"]):
                    raise RouterRepositoryError("rpg_authoring_route_changed")
                batch_request = self._comparison_requests.get(dispatch.attempt_id)
                holds = comparison.held(comparison.load(db, tenant))
                if state["spent"] >= state["limit"] or (batch_request is None and state["spent"] + holds >= state["limit"]):
                    raise RouterRepositoryError("rpg_authoring_budget_exhausted")
                if batch_request is not None:
                    comparison.validate_task(db, tenant, batch_request)
                run=db.execute("SELECT * FROM provider_chat_runs WHERE tenant_id=? AND id=?",(tenant,dispatch.run_id)).fetchone()
                attempt=db.execute("SELECT * FROM provider_chat_attempts WHERE tenant_id=? AND id=?",(tenant,dispatch.attempt_id)).fetchone()
                if (run is None or attempt is None or run["gateway"]!="rpg_scoped" or run["strategy"]!=STRATEGY
                        or run["policy_fingerprint"]!=dispatch.policy_fingerprint or run["requested_model"]!=dispatch.requested_model
                        or run["capability"]!="chat_text" or run["status"]!="running"
                        or attempt["run_id"]!=dispatch.run_id or attempt["connection_id"]!=state["connectionId"]
                        or attempt["provider_kind"]!=dispatch.target.provider_kind or attempt["capability"]!="chat_text"
                        or attempt["status"]!="running" or attempt["dispatched"] or attempt["position"]!=0):
                    raise RouterRepositoryError("rpg_authoring_dispatch_conflict")
                cache={};expirations=[]
                repo._validate_current_dispatch_certification(db,tenant_id=tenant,capability="chat_text",connection_id=state["connectionId"],
                    model_id=dispatch.requested_model,certification_id=dispatch.certification_id,contract_version="modelmirror-provider-chat-v1",
                    certification_max_age_seconds=self._certification_max_age_seconds(),expected_connection_id=state["connectionId"],
                    expected_provider_kind=dispatch.target.provider_kind,connection_cache=cache,expirations=expirations,require_exact_model=True)
                now=datetime.now(UTC)
                if cache.get((state["connectionId"],dispatch.requested_model))!=dispatch.connection_fingerprint or not expirations or now>=min(expirations):
                    raise RouterRepositoryError("rpg_authoring_route_changed")
                db.execute("UPDATE provider_chat_attempts SET dispatched=1,updated_at=? WHERE tenant_id=? AND id=?",(now.isoformat(),tenant,dispatch.attempt_id))
                if batch_request is not None:
                    comparison.consume(db, tenant, batch_request, dispatch.attempt_id)
                _save(db,_key(tenant),{**state,"spent":state["spent"]+1})
        except (RouterRepositoryError, RouterServiceError, ProviderCallContextError) as exc:
            code = getattr(exc, "code", None) or str(exc)
            self.fail_undispatched(dispatch,error_code=code)
            raise RouterServiceError(code,"创作路由、额度或模型资格已改变。",status_code=409) from exc


def register_route_api(router, auth, dependency, unique):
    @router.get("/authoring/route",dependencies=[Depends(auth)])
    def state(stable=Depends(dependency)):
        if not enabled():
            raise HTTPException(503,"rpg_authoring_routing_disabled")
        return AuthoringRouteControl(stable.router_service).state()

    @router.put("/authoring/route",dependencies=[Depends(auth)])
    async def configure(request:Request,stable=Depends(dependency)):
        data=bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>32768:
                raise HTTPException(413,"rpg_authoring_route_invalid")
        try:
            value=json.loads(data,object_pairs_hook=unique,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            return AuthoringRouteControl(stable.router_service).configure(value)
        except (ValueError,UnicodeError):
            raise HTTPException(422,"rpg_authoring_route_invalid") from None
        except RouterServiceError as exc:
            raise HTTPException(exc.status_code,exc.code) from None
