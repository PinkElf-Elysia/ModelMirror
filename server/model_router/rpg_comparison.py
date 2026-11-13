"""Durable RPG batch holds sharing the ordinary authoring route counter.

No new tables, model calls, authentication probes or quota grants. All mutations
run in the existing SQLite transaction; spent attempts are never refunded here.
"""
from __future__ import annotations
import json
import re
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from .repository import RouterRepositoryError

FORMAT = "modelmirror.rpg.comparison-reservation/1"
ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
FINISHED = {"completed", "failed", "cancelled", "not_dispatched", "released"}


def fail(code, status=409):
    raise HTTPException(status, "rpg_comparison_" + code)


def valid_id(v):
    return isinstance(v, str) and ID.fullmatch(v)


def valid_hash(v):
    return isinstance(v, str) and HASH.fullmatch(v)


def key(tenant):
    from .rpg_authoring_route import _digest
    return "rpg-comparison:" + _digest(tenant)


def load(db, tenant):
    row = db.execute("SELECT value FROM router_metadata WHERE key=?", (key(tenant),)).fetchone()
    if row is None:
        return {"format": FORMAT, "revision": 0, "batches": []}
    try:
        s = json.loads(row["value"])
        if set(s) != {"format", "revision", "batches"} or s["format"] != FORMAT or type(s["revision"]) is not int or s["revision"] < 0 or not isinstance(s["batches"], list) or len(s["batches"]) > 1000:
            raise ValueError()
        ids = set()
        for b in s["batches"]:
            validate_plan(b["plan"])
            if set(b) != {"plan", "tasks"} or b["plan"]["id"] in ids or len(b["tasks"]) != len(b["plan"]["stages"]):
                raise ValueError()
            ids.add(b["plan"]["id"])
            for stage, t in zip(b["plan"]["stages"], b["tasks"]):
                if set(t) != {"id", "state", "requestHash", "resultHash", "attemptId"} or t["id"] != stage["id"] or t["state"] not in FINISHED | {"reserved", "claimed", "dispatching", "unknown"}:
                    raise ValueError()
                if t["state"] in {"reserved", "released"}:
                    if any(t[k] is not None for k in ("requestHash", "resultHash", "attemptId")):
                        raise ValueError()
                elif not valid_hash(t["requestHash"]):
                    raise ValueError()
                if t["attemptId"] is not None and not valid_id(t["attemptId"]):
                    raise ValueError()
                if t["resultHash"] is not None and not valid_hash(t["resultHash"]):
                    raise ValueError()
        return s
    except (KeyError, ValueError, TypeError, HTTPException):
        raise RouterRepositoryError("rpg_comparison_record_unreadable") from None


def save(db, tenant, state):
    from .rpg_authoring_route import _save
    state["revision"] += 1
    _save(db, key(tenant), state)


def held(s):
    return sum(t["state"] in {"reserved", "claimed"} for b in s["batches"] for t in b["tasks"])


def validate_plan(p):
    if not isinstance(p, dict) or set(p) != {"id", "hash", "sessionId", "operationId", "routeRevision", "stages"}:
        fail("plan_invalid", 422)
    if any(not valid_id(p[k]) for k in ("id", "sessionId", "operationId")) or not valid_hash(p["hash"]) or type(p["routeRevision"]) is not int or p["routeRevision"] < 1:
        fail("plan_invalid", 422)
    stages = p["stages"]
    if not isinstance(stages, list) or not 2 <= len(stages) <= 9:
        fail("plan_invalid", 422)
    ids, names = set(), set()
    for t in stages:
        if not isinstance(t, dict) or set(t) != {"id", "name", "phase", "purpose"} or not valid_id(t["id"]) or not valid_id(t["name"]) or t["id"] in ids or t["name"] in names:
            fail("plan_invalid", 422)
        ids.add(t["id"]); names.add(t["name"])
        if not isinstance(t['phase'], str) or not isinstance(t['purpose'], str):
            fail('plan_invalid', 422)
        if t["phase"] == "candidate":
            if t["purpose"] != "story":
                fail("plan_invalid", 422)
        elif t["phase"] not in {"before", "after"} or t["purpose"] not in {"summary", "compression", "memory"} or (t["phase"] == "before" and t["purpose"] == "memory") or t["name"] != t["purpose"] + "-" + t["phase"]:
            fail("plan_invalid", 422)
    if not 2 <= sum(t["phase"] == "candidate" for t in stages) <= 4:
        fail("plan_invalid", 422)
    return p


def find(s, batch, task=None):
    b = next((b for b in s["batches"] if b["plan"]["id"] == batch), None)
    if b is None:
        fail("reservation_missing", 404)
    if task is None:
        return b
    t = next((t for t in b["tasks"] if t["id"] == task), None)
    if t is None:
        fail("stage_missing", 404)
    return b, t


def task_binding(value):
    v = value.get("comparison")
    if not isinstance(v, dict) or set(v) != {"batchId", "planHash", "stageId"} or not valid_id(v["batchId"]) or not valid_id(v["stageId"]) or not valid_hash(v["planHash"]):
        fail("binding_invalid", 422)
    return v


def validate_task(db, tenant, value):
    v = task_binding(value)
    b, t = find(load(db, tenant), v["batchId"], v["stageId"])
    stage = next(x for x in b["plan"]["stages"] if x["id"] == t["id"])
    if (b["plan"]["sessionId"] != value["sessionId"] or b["plan"]["hash"] != v["planHash"] or value["requestId"] != t["id"]
            or stage["purpose"] != value["purpose"] or t["requestHash"] != value["sourceRequestHash"] or t["state"] != "claimed"):
        fail("binding_conflict")
    return b, t


def consume(db, tenant, value, attempt_id):
    # Called in the same transaction as the ordinary spent counter and the
    # provider attempt's dispatched marker. No independent counter can drift.
    validate_task(db, tenant, value)
    s = load(db, tenant)
    _, t = find(s, value["comparison"]["batchId"], value["requestId"])
    t.update(state="dispatching", attemptId=attempt_id)
    save(db, tenant, s)


def settle_result(stable, value, result):
    if "comparison" not in value:
        return
    repo, tenant = stable.repository, stable.router_service.tenant_id
    with repo._lock, repo._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        s = load(db, tenant)
        b, t = find(s, value["comparison"]["batchId"], value["requestId"])
        if b["plan"]["hash"] != value["comparison"]["planHash"] or t["requestHash"] != value["sourceRequestHash"]:
            fail("binding_conflict")
        receipt = result["receipt"]
        state = "not_dispatched" if not receipt["dispatched"] else result["status"]
        if t["state"] in FINISHED and t["state"] != state:
            fail("result_conflict")
        if receipt["dispatched"] and t["attemptId"] != receipt["attemptId"]:
            fail("result_conflict")
        t["state"] = state
        save(db, tenant, s)


def register_comparison_api(router, auth, dependency, unique):
    def service(stable):
        from .rpg_authoring_route import AuthoringStableService, enabled
        if not enabled() or not isinstance(stable, AuthoringStableService):
            fail("routing_required", 503)
        return stable.repository, stable.router_service.tenant_id

    async def body(request):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 65536:
                fail("body_capacity", 413)
        try:
            v = json.loads(data, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(v, dict):
                raise ValueError()
            return v
        except (ValueError, TypeError, UnicodeError, RecursionError):
            fail("body_invalid", 422)

    def reply(value):
        return JSONResponse(value, headers={"Cache-Control": "no-store"})

    @router.get("/authoring/comparison-budget", dependencies=[Depends(auth)])
    def status(stable=Depends(dependency)):
        from .rpg_authoring_route import _read
        repo, tenant = service(stable)
        with repo._lock, repo._connect() as db:
            route, s = _read(db, tenant), load(db, tenant)
            reserved = held(s)
            return reply({"format": FORMAT, "level": "control", "limit": route["limit"], "used": route["spent"], "reserved": reserved,
                          "remaining": route["limit"] - route["spent"] - reserved, "revision": s["revision"], "routeRevision": route["revision"]})

    @router.post("/authoring/comparison-reservations", dependencies=[Depends(auth)])
    async def reserve(request: Request, stable=Depends(dependency)):
        from .rpg_authoring_route import _read
        p = validate_plan(await body(request)); repo, tenant = service(stable)
        with repo._lock, repo._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            route, s = _read(db, tenant), load(db, tenant)
            old = next((b for b in s["batches"] if b["plan"]["id"] == p["id"]), None)
            if old:
                if old["plan"] != p:
                    fail("plan_conflict")
                return reply(old)
            if not route["enabled"] or route["revision"] != p["routeRevision"]:
                fail("route_changed")
            if len(s["batches"]) >= 1000:
                fail("record_capacity", 413)
            if route["limit"] - route["spent"] - held(s) < len(p["stages"]):
                fail("budget_exhausted")
            b = {"plan": p, "tasks": [{"id": t["id"], "state": "reserved", "requestHash": None, "resultHash": None, "attemptId": None} for t in p["stages"]]}
            s["batches"].append(b); save(db, tenant, s)
            return reply(b)

    @router.get("/authoring/comparison-reservations/{batch_id}", dependencies=[Depends(auth)])
    def read(batch_id: str, stable=Depends(dependency)):
        if not valid_id(batch_id):
            fail("identity_invalid", 422)
        repo, tenant = service(stable)
        with repo._lock, repo._connect() as db:
            b = next((b for b in load(db, tenant)["batches"] if b["plan"]["id"] == batch_id), None)
            return reply(b)

    @router.post("/authoring/comparison-reservations/{batch_id}/{action}", dependencies=[Depends(auth)])
    async def mutate(batch_id: str, action: str, request: Request, stable=Depends(dependency)):
        v = await body(request)
        fields = {"stageId", "requestHash"} if action == "claim" else {"stageId"} if action == "release" else {"stageId", "requestHash", "status", "resultHash"} if action == "settle" else None
        if fields is None or set(v) != fields or not valid_id(batch_id) or not valid_id(v.get("stageId")):
            fail("action_invalid", 422)
        if action in {"claim", "settle"} and not valid_hash(v["requestHash"]):
            fail("action_invalid", 422)
        if action == "settle" and (not isinstance(v["status"], str) or v["status"] not in FINISHED - {"released"} or not valid_hash(v["resultHash"])):
            fail("action_invalid", 422)
        repo, tenant = service(stable)
        with repo._lock, repo._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            s = load(db, tenant); b, t = find(s, batch_id, v["stageId"])
            if action == "claim":
                if t["state"] != "reserved":
                    if t["requestHash"] != v["requestHash"] or t["state"] == "released":
                        fail("claim_conflict")
                    return reply({"dispatch": False, "state": t["state"]})
                t.update(state="claimed", requestHash=v["requestHash"])
                save(db, tenant, s)
                return reply({"dispatch": True, "state": "dispatching"})
            if action == "release":
                if t["state"] not in {"reserved", "released"}:
                    fail("dispatch_retained")
                t["state"] = "released"
            else:
                if t["requestHash"] != v["requestHash"]:
                    fail("claim_conflict")
                if t["resultHash"] is not None and (t["state"] != v["status"] or t["resultHash"] != v["resultHash"]):
                    fail("result_conflict")
                if t["state"] == "claimed" and v["status"] == "not_dispatched":
                    # Atomically fence a queued HTTP request. If execution has
                    # crossed mark_dispatched this state cannot still be claimed.
                    t["state"] = "not_dispatched"
                if t["state"] != v["status"]:
                    fail("result_unconfirmed")
                t["resultHash"] = v["resultHash"]
            save(db, tenant, s)
            return reply(t)
