"""Dispatch generations: synthetic sockets and Node VM, never real hosts."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

from server.tests.test_workflow_r8a_client_dispatch import setup_request
from server.xpert_runtime import ClientToolConnectionManager, ClientToolConflictError, ClientToolStore
from server.xpert_runtime import client_tool_api as api


def connect(store, host_id, connection_id):
    store.connect_host(host_id, connection_id=connection_id, version="synthetic", capabilities=[], schema_hashes={})


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["tool_accepted", "tool_result", "tool_error"])
@pytest.mark.parametrize("attack", ["old_connection", "old_attempt", "missing_attempt", "bool_attempt", "string_attempt", "valid_attempt"])
async def test_websocket_rejects_stale_receipt_without_settling(tmp_path, monkeypatch, kind, attack):
    store, host, request = setup_request(tmp_path, "host_page_read")
    monkeypatch.setattr(api, "_store", store)
    monkeypatch.setattr(api, "_connections", ClientToolConnectionManager(store))
    wakes = []
    monkeypatch.setattr(api, "_coordinator", SimpleNamespace(wake=lambda: wakes.append(True)))
    monkeypatch.setattr(store, "authenticate", lambda *_: host)
    before = []
    after = []
    errors = []

    class Socket:
        count = 0

        async def accept(self):
            pass

        async def close(self, **kwargs):
            pass

        async def send_json(self, payload):
            if payload["type"] == "error":
                errors.append(payload)
                after.append(asdict(store.require_request(request.request_id)))

        async def receive_json(self):
            self.count += 1
            if self.count == 1:
                return {"type": "authenticate", "host_id": host.host_id, "host_token": "synthetic"}
            if self.count == 2:
                connection = store.require_host(host.host_id).connection_id
                store.mark_dispatched(request.request_id, host_id=host.host_id, connection_id=connection)
                if attack == "old_connection":
                    connect(store, host.host_id, "replacement")
                else:
                    store.cancel_request(request.request_id)
                    store.retry_request(request.request_id)
                    store.mark_dispatched(request.request_id, host_id=host.host_id, connection_id=connection)
                before.append(asdict(store.require_request(request.request_id)))
                message = {"type": kind, "request_id": request.request_id,
                           "operation_id": request.operation_id, "tool_call_id": request.tool_call_id,
                           "result": "stale", "error": "stale", "dispatch_count": 1}
                if attack == "missing_attempt":
                    message.pop("dispatch_count")
                elif attack == "bool_attempt":
                    message["dispatch_count"] = True
                elif attack == "string_attempt":
                    message["dispatch_count"] = "2"
                elif attack == "valid_attempt":
                    message["dispatch_count"] = 2
                return message
            after.append(asdict(store.require_request(request.request_id)))
            raise WebSocketDisconnect()

    await api.connect_client_tool_host(Socket())
    if attack == "valid_attempt":
        assert errors == []
        assert after[0]["status"] == {"tool_accepted": "running", "tool_result": "completed", "tool_error": "failed"}[kind]
        assert len(wakes) == (1 if kind == "tool_accepted" else 2)
        return
    assert errors, "stale receipt was accepted by the production WebSocket handler"
    assert after == before
    assert wakes == [True], "only the initial connection may wake the coordinator"


def settle(store, host, request, count=1, connection="connection-1", kind="result"):
    common = dict(host_id=host.host_id, connection_id=connection, dispatch_count=count)
    if kind == "accepted":
        return store.mark_running(request.request_id, **common)
    common.update(operation_id=request.operation_id, tool_call_id=request.tool_call_id)
    if kind == "error":
        return store.fail_request(request.request_id, **common, error="synthetic")
    return store.complete_request(request.request_id, **common, result="fresh")


@pytest.mark.parametrize("kind", ["accepted", "result", "error"])
def test_bound_request_cannot_bypass_transport_identity(tmp_path, kind):
    store, host, request = setup_request(tmp_path, "host_page_read")
    store.mark_dispatched(request.request_id, host_id=host.host_id, connection_id="connection-1")
    before = store.snapshot_path.read_bytes()
    with pytest.raises(ClientToolConflictError):
        settle(store, host, request, connection=None, count=None, kind=kind)
    assert store.snapshot_path.read_bytes() == before


@pytest.mark.parametrize("count", [None, 1])
def test_first_dispatch_legacy_and_current_receipts(tmp_path, count):
    store, host, request = setup_request(tmp_path, "host_page_read")
    store.mark_dispatched(request.request_id, host_id=host.host_id, connection_id="connection-1")
    settle(store, host, request, count=count, kind="accepted")
    result = settle(store, host, request, count=count)
    assert result.status == "completed"
    before = store.snapshot_path.read_bytes()
    assert settle(store, host, request, count=count) == result
    assert store.snapshot_path.read_bytes() == before
    assert ClientToolStore(tmp_path).require_request(request.request_id).result == "fresh"


def test_restart_requires_new_connection_and_new_dispatch(tmp_path):
    store, host, request = setup_request(tmp_path, "host_page_read")
    store.mark_dispatched(request.request_id, host_id=host.host_id, connection_id="connection-1")
    restored = ClientToolStore(tmp_path)
    connect(restored, host.host_id, "connection-2")
    restored.mark_dispatched(request.request_id, host_id=host.host_id, connection_id="connection-2")
    with pytest.raises(ClientToolConflictError):
        settle(restored, host, request, count=1, connection="connection-2")
    assert settle(restored, host, request, count=2, connection="connection-2").result == "fresh"


@pytest.mark.parametrize("kind", ["accepted", "result", "error"])
@pytest.mark.parametrize("revoke", [False, True])
def test_offline_and_revoked_hosts_cannot_settle(tmp_path, kind, revoke):
    store, host, request = setup_request(tmp_path, "host_page_click")
    store.mark_dispatched(request.request_id, host_id=host.host_id, connection_id="connection-1")
    if revoke:
        store.revoke_host(host.host_id)
    else:
        store.disconnect_host(host.host_id, connection_id="connection-1")
    before = store.snapshot_path.read_bytes()
    with pytest.raises(ClientToolConflictError):
        settle(store, host, request, kind=kind)
    assert store.snapshot_path.read_bytes() == before
    assert store.require_request(request.request_id).status == ("dispatched" if revoke else "uncertain")


def test_failed_dispatch_persistence_does_not_advance_generation(tmp_path, monkeypatch):
    store, host, request = setup_request(tmp_path, "host_page_read")
    before = asdict(store.require_request(request.request_id))
    disk = store.snapshot_path.read_bytes()
    def fail():
        raise OSError("synthetic persistence failure")
    monkeypatch.setattr(store, "_persist_unlocked", fail)
    with pytest.raises(OSError):
        store.mark_dispatched(request.request_id, host_id=host.host_id, connection_id="connection-1")
    assert asdict(store.require_request(request.request_id)) == before
    assert store.snapshot_path.read_bytes() == disk


def test_unbound_legacy_request_cannot_be_settled_over_websocket(tmp_path):
    store, host, request = setup_request(tmp_path, "host_page_read")
    store.mark_dispatched(request.request_id)
    with pytest.raises(ClientToolConflictError):
        settle(store, host, request)
    # The old trusted in-process contract remains readable/executable.
    assert store.complete_request(request.request_id, host_id=host.host_id,
        operation_id=request.operation_id, tool_call_id=request.tool_call_id, result="legacy").status == "completed"


@pytest.mark.parametrize("client", ["chrome", "office"])
def test_actual_client_handlers_echo_attempt_and_do_not_relabel_cached_reads(client):
    node = shutil.which("node")
    assert node, "Node is required to verify actual client scripts"
    root = Path(__file__).resolve().parents[2]
    script = root / ("server/client_extension/service-worker.js" if client == "chrome" else "server/office_addin/taskpane.js")
    harness = r'''
const vm = require('node:vm'), fs = require('node:fs'), assert = require('node:assert/strict');
const local = {}, frames = [];
const event = {addListener(){}};
const ctx = vm.createContext({console, URL, setTimeout(){}, clearTimeout(){}, setInterval(){}, clearInterval(){},
  fetch: async()=>({ok:true,json:async()=>({tools:[]})}),
  chrome: {runtime:{onMessage:event,onStartup:event,onInstalled:event}, alarms:{onAlarm:event}, tabs:{onUpdated:event,onRemoved:event},
    storage:{local:{async get(keys){return Object.fromEntries(keys.map(k=>[k,local[k]]));},
      async set(value){Object.assign(local,value);},async remove(keys){keys.forEach(k=>delete local[k]);}}}},
  Office:{onReady(){}}, localStorage:{getItem(k){return local[k];},setItem(k,v){local[k]=v;}},
  frames, local, assert});
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
vm.runInContext(`send = value => frames.push(value);`, ctx);
const office = process.argv[2] === 'office';
vm.runInContext(office ? `state.binding={bound:true,binding_id:'synthetic'}; state.officeApp='word';
  state.capabilities=[{name:'office_word_snapshot',mutating:false},{name:'office_word_insert_text',mutating:true}];
  addOperation=()=>{}; let calls=0; executeOfficeTool=async()=>({text:'value-'+(++calls)});`
  : `let calls=0; executeTool=async()=>({output:'value-'+(++calls)});`, ctx);
(async()=>{
  for(const attempt of [1,2,2]) {
    ctx.request={request_id:'r',operation_id:'op',tool_call_id:'call',dispatch_count:attempt,
      tool_name:office?'office_word_snapshot':'host_page_read',mutating:false};
    await vm.runInContext('handleToolRequest(request)',ctx);
  }
  const results=frames.filter(f=>f.type==='tool_result');
  assert.deepEqual(results.map(f=>f.dispatch_count),[1,2,2]);
  assert.notEqual(results[0].result, results[1].result);
  assert.equal(results[1].result,results[2].result);
  assert.equal(vm.runInContext('calls',ctx),2);
  assert.deepEqual(frames.filter(f=>f.type==='tool_accepted').map(f=>f.dispatch_count),[1,2]);
  vm.runInContext(office ? 'executeOfficeTool=async()=>{throw Error("synthetic");}'
    : 'executeTool=async()=>{throw Error("synthetic");}',ctx);
  ctx.request={...ctx.request,dispatch_count:3};
  await vm.runInContext('handleToolRequest(request)',ctx);
  assert.equal(frames.at(-1).type,'tool_error'); assert.equal(frames.at(-1).dispatch_count,3);
  // A delayed callback must retain the request it received, not the newest generation.
  vm.runInContext(office ? 'let release; executeOfficeTool=()=>new Promise(r=>release=r);'
    : 'let release; executeTool=()=>new Promise(r=>release=r);',ctx);
  ctx.request={...ctx.request,dispatch_count:4};
  const pending=vm.runInContext('handleToolRequest(request)',ctx);
  await new Promise(r=>setImmediate(r));
  ctx.request={...ctx.request,dispatch_count:5};
  vm.runInContext(office ? 'release({text:"late"})' : 'release({output:"late"})',ctx);
  await pending;
  assert.equal(frames.at(-1).dispatch_count,4);
  // Mutating operations retain the original operation cache; no new write replay policy.
  vm.runInContext(office ? 'executeOfficeTool=async()=>({text:"mutation"})'
    : 'executeTool=async()=>({output:"mutation"})',ctx);
  ctx.request={request_id:'m',operation_id:'mutation',tool_call_id:'mc',dispatch_count:1,
    tool_name:office?'office_word_insert_text':'host_page_click',mutating:true};
  await vm.runInContext('handleToolRequest(request)',ctx);
  vm.runInContext(office ? 'executeOfficeTool=async()=>{throw Error("must not replay");}'
    : 'executeTool=async()=>{throw Error("must not replay");}',ctx);
  await vm.runInContext('handleToolRequest(request)',ctx);
  assert.equal(frames.at(-1).type,'tool_result'); assert.equal(frames.at(-1).dispatch_count,1);
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    result = subprocess.run([node, "-e", harness, str(script), client], text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
