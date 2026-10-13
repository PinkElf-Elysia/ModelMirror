"""Client screenshot commit and dispatch fencing, with synthetic local IO only."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import shutil
import subprocess
from threading import Thread

import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from server.tests.test_workflow_r8a_client_dispatch import setup_request
from server.tests.test_workflow_r8a_client_parent_permission import configure, parent_store
from server.xpert_runtime import ClientToolStore
from server.xpert_runtime import client_tool_api as api
from server.xpert_runtime import client_tool_store as store_module


def setup(tmp_path, monkeypatch, parent="waiting", tool="host_page_screenshot"):
    store, host, request = setup_request(tmp_path / "clients", tool)
    executions = parent_store(tmp_path, request, parent)
    _, coordinator = configure(monkeypatch, store, executions)
    monkeypatch.setattr(store, "authenticate", lambda *_: store.require_host(host.host_id))
    store.mark_dispatched(request.request_id, host_id=host.host_id, connection_id="connection-1")
    app = FastAPI()
    app.include_router(api.router)
    return store, host, request, executions, coordinator, TestClient(app)


def upload(client, host, request, count="1", data=b"synthetic-image"):
    headers = {"Authorization": "Bearer synthetic", "X-ModelMirror-Client-Host-Id": host.host_id}
    if count is not None:
        headers["X-ModelMirror-Dispatch-Count"] = count
    return client.post(f"/api/runtime/client-tool-requests/{request.request_id}/artifact",
                       headers=headers, files={"file": ("shot.png", data, "image/png")})


@pytest.mark.parametrize("attack", ["old_attempt", "missing_attempt", "bad_attempt", "connection",
                                    "revoked", "cancelled", "pending", "expired", "completed",
                                    "parent_cancelled", "wrong_run", "no_coordinator"])
def test_upload_rejects_invalid_dispatch_without_writing(tmp_path, monkeypatch, attack):
    store, host, request, executions, _, client = setup(
        tmp_path, monkeypatch, "wrong_run" if attack == "wrong_run" else "waiting")
    count = "1"
    if attack in {"old_attempt", "missing_attempt"}:
        store.cancel_request(request.request_id)
        store.retry_request(request.request_id)
        store.mark_dispatched(request.request_id, host_id=host.host_id, connection_id="connection-1")
        count = None if attack == "missing_attempt" else "1"
    elif attack == "bad_attempt":
        count = "true"
    elif attack == "connection":
        store.connect_host(host.host_id, connection_id="replacement", version="synthetic", capabilities=[], schema_hashes={})
    elif attack == "revoked":
        store.revoke_host(host.host_id)
    elif attack in {"cancelled", "pending"}:
        store.cancel_request(request.request_id)
        if attack == "pending":
            store.retry_request(request.request_id)
    elif attack == "expired":
        store.expire_due(now=store.require_request(request.request_id).expires_at + 1)
    elif attack == "completed":
        store.complete_request(request.request_id, host_id=host.host_id, operation_id=request.operation_id,
                               tool_call_id=request.tool_call_id, result="synthetic",
                               connection_id="connection-1", dispatch_count=1)
    elif attack == "parent_cancelled":
        executions.cancel(request.task_id)
    elif attack == "no_coordinator":
        monkeypatch.setattr(api, "_coordinator", None)
    before = store.snapshot_path.read_bytes()
    response = upload(client, host, request, count)
    assert response.status_code in {400, 401, 409, 422}, response.text
    assert store.snapshot_path.read_bytes() == before
    assert not store._artifacts
    assert not list(store.artifact_root.rglob("*.*"))


def test_failed_registration_preserves_memory_disk_and_removes_new_file(tmp_path, monkeypatch):
    store, host, request, _, _, client = setup(tmp_path, monkeypatch)
    before = deepcopy(store.require_request(request.request_id))
    disk = store.snapshot_path.read_bytes()
    attempts = []
    def broken():
        attempts.append(True)
        raise OSError("synthetic persistence failure")
    monkeypatch.setattr(store, "_persist_unlocked", broken)
    response = upload(client, host, request)
    assert response.status_code >= 400
    assert attempts == [True]
    assert store.require_request(request.request_id) == before
    assert not store._artifacts
    assert store.snapshot_path.read_bytes() == disk
    assert not list(store.artifact_root.rglob("*.*"))


def test_duplicate_upload_reuses_commit_and_different_content_conflicts(tmp_path, monkeypatch):
    store, host, request, _, _, client = setup(tmp_path, monkeypatch)
    first = upload(client, host, request)
    assert first.status_code == 200, first.text
    before = store.snapshot_path.read_bytes()
    second = upload(client, host, request)
    assert second.json() == first.json()
    assert store.snapshot_path.read_bytes() == before
    assert upload(client, host, request, data=b"different").status_code == 409
    assert len(store._artifacts) == 1
    restored = ClientToolStore(tmp_path / "clients")
    artifact = restored.require_artifact(first.json()["artifact_id"])
    assert restored.artifact_path(artifact).read_bytes() == b"synthetic-image"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["cancel_parent", "retry", "disconnect"])
async def test_upload_rechecks_after_async_body_read(tmp_path, monkeypatch, change):
    store, host, request, executions, _, _ = setup(tmp_path, monkeypatch)
    file = UploadFile(BytesIO(b"synthetic"), filename="shot.png", headers=Headers({"content-type": "image/png"}))
    original_read = file.read
    async def read(size):
        if change == "cancel_parent":
            executions.cancel(request.task_id)
        elif change == "retry":
            store.cancel_request(request.request_id)
            store.retry_request(request.request_id)
            store.mark_dispatched(request.request_id, host_id=host.host_id, connection_id="connection-1")
        else:
            store.disconnect_host(host.host_id, connection_id="connection-1")
        return await original_read(size)
    monkeypatch.setattr(file, "read", read)
    with pytest.raises(HTTPException) as error:
        await api.upload_client_tool_artifact(request.request_id, file, host.host_id, "Bearer synthetic")
    assert error.value.status_code == 409
    assert not store._artifacts


@pytest.mark.parametrize("count", [None, "1"])
def test_first_dispatch_and_legacy_upload_are_readable_after_reload(tmp_path, monkeypatch, count):
    store, host, request, _, _, client = setup(tmp_path, monkeypatch)
    response = upload(client, host, request, count)
    assert response.status_code == 200
    restored = ClientToolStore(tmp_path / "clients")
    artifact = restored.require_artifact(response.json()["artifact_id"])
    assert restored.artifact_path(artifact).read_bytes() == b"synthetic-image"
    assert restored.require_request(request.request_id).revision == 3


@pytest.mark.parametrize("stage", ["file_write", "file_replace", "snapshot_replace"])
def test_file_commit_failures_are_invisible_and_do_not_leak_paths(tmp_path, monkeypatch, stage):
    store, host, request, _, _, client = setup(tmp_path, monkeypatch)
    disk = store.snapshot_path.read_bytes()
    before = deepcopy(store.require_request(request.request_id))
    attempted = []
    write = Path.write_bytes
    replace = store_module.os.replace
    def broken_write(path, data):
        write(path, b"partial")
        attempted.append(True)
        raise OSError("synthetic-private-path")
    def broken_replace(source, target):
        if (stage == "snapshot_replace") == (Path(target) == store.snapshot_path):
            attempted.append(True)
            raise OSError("synthetic-private-path")
        return replace(source, target)
    if stage == "file_write":
        monkeypatch.setattr(Path, "write_bytes", broken_write)
    else:
        monkeypatch.setattr(store_module.os, "replace", broken_replace)
    response = upload(client, host, request)
    assert response.status_code == 503, response.text
    assert "synthetic-private" not in response.text
    assert attempted == [True]
    assert not store._artifacts
    assert store.require_request(request.request_id) == before
    assert store.snapshot_path.read_bytes() == disk
    assert not list(store.artifact_root.rglob("*.*"))
    assert not ClientToolStore(tmp_path / "clients")._artifacts


def test_new_attempt_does_not_reuse_previous_screenshot(tmp_path, monkeypatch):
    store, host, request, _, _, client = setup(tmp_path, monkeypatch)
    original = upload(client, host, request).json()
    store.cancel_request(request.request_id)
    store.retry_request(request.request_id)
    store.mark_dispatched(request.request_id, host_id=host.host_id, connection_id="connection-1")
    response = upload(client, host, request, "2", b"fresh")
    assert response.status_code == 200
    assert response.json()["artifact_id"] != original["artifact_id"]
    assert store.artifact_path(store.require_artifact(original["artifact_id"])).read_bytes() == b"synthetic-image"
    assert upload(client, host, request, "1").status_code == 409


@pytest.mark.parametrize("same_content", [True, False])
def test_reconnected_read_upload_gets_new_generation_artifact(tmp_path, monkeypatch, same_content):
    store, host, request, _, _, client = setup(tmp_path, monkeypatch)
    previous = upload(client, host, request).json()
    store.disconnect_host(host.host_id, connection_id="connection-1")
    store.connect_host(host.host_id, connection_id="replacement", version="synthetic", capabilities=[], schema_hashes={})
    store.mark_dispatched(request.request_id, host_id=host.host_id, connection_id="replacement")
    response = upload(client, host, request, "2", b"synthetic-image" if same_content else b"fresh")
    assert response.status_code == 200, response.text
    assert response.json()["artifact_id"] != previous["artifact_id"]
    restored = ClientToolStore(tmp_path / "clients")
    assert len(restored._artifacts) == 2
    assert restored.require_request(request.request_id).result_metadata["artifact_id"] == response.json()["artifact_id"]


def test_redispatch_save_failure_preserves_previous_artifact_reference(tmp_path, monkeypatch):
    store, host, request, _, _, client = setup(tmp_path, monkeypatch)
    previous = upload(client, host, request).json()
    store.disconnect_host(host.host_id, connection_id="connection-1")
    store.connect_host(host.host_id, connection_id="replacement", version="synthetic", capabilities=[], schema_hashes={})
    before = deepcopy(store.require_request(request.request_id))
    def broken():
        raise OSError("synthetic")
    monkeypatch.setattr(store, "_persist_unlocked", broken)
    with pytest.raises(OSError):
        store.mark_dispatched(request.request_id, host_id=host.host_id, connection_id="replacement")
    assert store.require_request(request.request_id) == before
    restored = ClientToolStore(tmp_path / "clients")
    assert restored.require_request(request.request_id).result_metadata["artifact_id"] == previous["artifact_id"]


def test_orphan_file_is_not_loaded_as_committed_artifact(tmp_path, monkeypatch):
    store, host, _, _, _, client = setup(tmp_path, monkeypatch)
    orphan = store.artifact_root / host.host_id / "cta_orphan.png"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"synthetic crash leftover")
    restored = ClientToolStore(tmp_path / "clients")
    monkeypatch.setattr(api, "_store", restored)
    assert client.get("/api/runtime/client-tool-artifacts/cta_orphan").status_code == 404
    assert orphan.read_bytes() == b"synthetic crash leftover"


def test_parent_and_client_locks_cover_file_and_metadata_commit(tmp_path, monkeypatch):
    store, host, request, executions, _, client = setup(tmp_path, monkeypatch)
    original = store._persist_unlocked
    acquired = []
    def persist():
        def check():
            for lock in (executions._lock, store._lock):
                held = lock.acquire(blocking=False)
                acquired.append(held)
                if held:
                    lock.release()
        worker = Thread(target=check)
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive()
        original()
    monkeypatch.setattr(store, "_persist_unlocked", persist)
    assert upload(client, host, request).status_code == 200
    assert acquired == [False, False]


@pytest.mark.asyncio
async def test_accepted_receipt_during_upload_is_not_a_new_dispatch(tmp_path, monkeypatch):
    store, host, request, _, _, _ = setup(tmp_path, monkeypatch)
    file = UploadFile(BytesIO(b"synthetic"), filename="shot.png", headers=Headers({"content-type": "image/png"}))
    original_read = file.read
    async def read(size):
        store.mark_running(request.request_id, host_id=host.host_id,
                           connection_id="connection-1", dispatch_count=1)
        return await original_read(size)
    monkeypatch.setattr(file, "read", read)
    result = await api.upload_client_tool_artifact(request.request_id, file, host.host_id, "Bearer synthetic", "1")
    assert store.require_artifact(result["artifact_id"]).size_bytes == 9
    assert store.require_request(request.request_id).status == "running"


def test_actual_extension_upload_sends_captured_dispatch_count():
    node = shutil.which("node")
    assert node, "Node is required for the real extension upload function"
    script = Path(__file__).resolve().parents[2] / "server/client_extension/service-worker.js"
    harness = r'''
const vm=require('node:vm'), fs=require('node:fs'), assert=require('node:assert/strict');
const event={addListener(){}}, uploads=[];
const ctx=vm.createContext({console, URL, FormData, Blob, setTimeout(){},clearTimeout(){},
  chrome:{runtime:{onMessage:event,onStartup:event,onInstalled:event},alarms:{onAlarm:event},
    tabs:{onUpdated:event,onRemoved:event},storage:{local:{async get(){return {hostId:'h',hostToken:'synthetic'};}}}},
  fetch:async(url,options)=>{
    if(url.endsWith('/capabilities')) return {ok:true,json:async()=>({tools:[]})};
    if(!options) return {blob:async()=>new Blob(['synthetic'],{type:'image/png'})};
    uploads.push({url,options}); return {ok:true,json:async()=>({artifact_id:'a'})};
  }});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);
(async()=>{
  const result=await vm.runInContext("uploadScreenshot({request_id:'r',dispatch_count:7},'data:synthetic')",ctx);
  assert.equal(result.artifact_id,'a'); assert.equal(uploads.length,1);
  assert.equal(uploads[0].options.headers['X-ModelMirror-Dispatch-Count'],'7');
  assert.equal(uploads[0].options.body.get('file').size,9);
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    result = subprocess.run([node, "-e", harness, str(script)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
