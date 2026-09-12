"""RPG05 HTTP regression wrapper derived from frozen RPG04 harness. Runs unchanged RPG04 HTTP test against RPG05 base, only loopback fake provider."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import ProxyHandler, Request, build_opener
from urllib.parse import urlsplit

MODULE = Path(__file__).resolve().parents[1]
REPOSITORY = MODULE.parents[1]
BASE = "81fc14f6e0dada2447a63c1e532ee55b1f914ded"
MODEL = "rpg04/fake-text-v1"
FORMAT_VERSION = "0.1.0"
STORAGE_NAMES = (
    "MODEL_ROUTER_STORAGE_DIR", "AGENT_TASK_STORAGE_DIR", "DATAX_STORAGE_DIR",
    "AGENT_TABLE_STORAGE_DIR", "TOOLSET_STORAGE_DIR", "XPERT_STORAGE_DIR",
    "PROMPT_PROFILE_STORAGE_DIR", "MCP_CATALOG_STORAGE_DIR", "FILE_ASSET_STORAGE_DIR",
    "RAG_STORAGE_DIR", "RAG_UPLOAD_DIR", "WORLD_SETTINGS_DIR", "PLUGIN_STORAGE_DIR",
    "SKILL_INSTALLED_DIR", "SKILL_TMP_DIR", "SANDBOX_WORKSPACE_ROOT", "BROWSER_DATA_ROOT",
    "CLIENT_TOOL_ARTIFACT_ROOT", "AGENT_WORKSPACE_ROOT",
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(value, output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        output.write("\n")


def git(*arguments: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(REPOSITORY), *arguments], stderr=subprocess.DEVNULL)


def isolated_environment(work: Path) -> dict[str, str]:
    environment = {key: os.environ[key] for key in ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC") if key in os.environ}
    for key in STORAGE_NAMES:
        environment[key] = str(work / "stores" / key.lower())
    environment["WORLD_STORAGE_DIR"] = str(work / "stores" / "world" / "world.json")
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1", "MODEL_CONTROL_CHAT_ENABLED": "true", "MODELMIRROR_DEFAULT_TENANT_ID": "local"})
    temporary = work / "temp"
    temporary.mkdir()
    environment.update({"TEMP": str(temporary), "TMP": str(temporary)})
    return environment


def server_records(destination_root: Path | None = None) -> list[dict[str, object]]:
    if git("rev-parse", "HEAD").decode().strip() != BASE:
        raise ValueError("RPG04_HARNESS_BASE_DRIFT")
    if git("branch", "--show-current").decode().strip() != "codex/ai-rpg-rpg05-ui":
        raise ValueError("RPG04_HARNESS_BRANCH_DRIFT")
    records = []
    for raw in git("ls-files", "-z", "--", "server").split(b"\0"):
        if not raw:
            continue
        relative = raw.decode("utf-8")
        parts = Path(relative).parts
        if ".." in parts or any(part in ("storage", "uploads", "__pycache__") or part.startswith(".env") for part in parts):
            continue
        source = REPOSITORY / relative
        if source.is_symlink() or not source.resolve().is_relative_to(REPOSITORY.resolve()) or not source.is_file():
            raise ValueError("RPG04_HARNESS_SOURCE_PATH")
        data = source.read_bytes()
        if destination_root is not None:
            destination = destination_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as output:
                output.write(data)
        records.append({"path": relative.replace("\\", "/"), "bytes": len(data), "sha256": sha(data)})
    records.sort(key=lambda item: item["path"])
    return records


def copy_candidate(work: Path) -> dict[str, object]:
    candidate = work / "candidate-code"
    records = server_records(candidate)
    # Prevent load_dotenv's ancestor search, without copying any existing .env.
    (candidate / "server" / ".env").write_text("", encoding="utf-8")
    encoded = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    receipt = {"baseSha": BASE, "candidateTreeSha256": sha(encoded), "files": records, "mainSha256": sha((candidate / "server" / "main.py").read_bytes())}
    write_json(work / "candidate-source.json", receipt)
    return receipt


def stop_owned_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def serve_mock(config_path: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    work = config_path.parent.resolve()
    if not work.is_relative_to((MODULE / ".rpg04-work").resolve()) or config.get("evidenceKind") != "mock":
        raise ValueError("RPG04_HARNESS_CONFIG")
    target = urlsplit(config["fakeBaseUrl"])
    if target.scheme != "http" or target.hostname != "127.0.0.1" or not target.port or target.path != "/v1" or target.username or target.password or target.query or target.fragment or config["baseUrl"] != "http://127.0.0.1:" + str(config["port"]):
        raise ValueError("RPG04_HARNESS_LOOPBACK_REQUIRED")
    code = work / "candidate-code"
    sys.path.insert(0, str(code))
    from server import main as main_module
    from server.model_router import ModelRouterService, RouterConnectionCreate, SQLiteRouterRepository, configure_model_router
    from server.model_router.chat_control import ProviderChatControlService
    from server.model_router.schemas import ProviderChatControlPolicyUpdate, ProviderChatControlRouteUpdate
    import uvicorn

    if not Path(main_module.__file__).resolve().is_relative_to(code.resolve()):
        raise ValueError("RPG04_HARNESS_IMPORT_BOUNDARY")
    repository = SQLiteRouterRepository(os.environ["MODEL_ROUTER_STORAGE_DIR"], master_key=b"x" * 32)
    service = ModelRouterService(repository)
    connection = repository.create_connection("local", RouterConnectionCreate(name="RPG04 loopback mock", kind="newapi", base_url=config["fakeBaseUrl"], api_key="rpg04-mock-token", scopes=["chat"]))
    observed = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    repository.save_test_result("local", connection.id, health="online", model_count=1, checked_at=observed)
    fingerprint = repository.connection_config_fingerprint("local", connection.id)
    refresh_id = "rpg04-mock-refresh"
    repository.claim_catalog_refresh("local", refresh_id=refresh_id, connection_id=connection.id, connection_fingerprint=fingerprint)
    repository.complete_catalog_refresh("local", refresh_id, connection_id=connection.id, models=[{"model_id": MODEL, "normalized_model_id": MODEL, "capability_state": "declared"}], offerings=[], model_count=1, truncated=False, catalog_fingerprint="rpg04-mock-catalog", observed_at=observed)
    certification, created = repository.claim_chat_certification("local", certification_id="rpg04-mock-cert", connection_id=connection.id, connection_fingerprint=fingerprint, contract_version="modelmirror-provider-chat-v1", capability="chat_text", requested_model=MODEL, idempotency_key_hash=sha(b"rpg04-mock-cert"))
    if not created:
        raise ValueError("RPG04_HARNESS_CERTIFICATE_COLLISION")
    repository.complete_chat_certification("local", str(certification["id"]), status="passed", checks={"capability_verified": True}, warning_codes=[], actual_model=MODEL)
    ProviderChatControlService(service).update_policy(ProviderChatControlPolicyUpdate(expected_revision=0, mode="newapi_preferred", stable_model_ids=[MODEL], routes=[ProviderChatControlRouteUpdate(capability="chat_text", connection_ids=[connection.id])]))
    configure_model_router(service)
    write_json(work / "service-binding.json", {"evidenceKind": "mock", "qualificationSeeded": True, "connectionId": connection.id, "connectionFingerprint": fingerprint, "modelId": MODEL, "serviceVersion": main_module.app.version, "uvicornVersion": uvicorn.__version__, "pythonVersion": sys.version.split()[0]})
    uvicorn.run(main_module.app, host="127.0.0.1", port=config["port"], lifespan="off", access_log=False, log_level="warning", timeout_graceful_shutdown=3)



class FakeState:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

def fake_server(state):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *args): pass
        def do_POST(self):
            if self.path != "/v1/chat/completions":
                self.send_error(404); return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1048576:
                self.send_error(413); return
            raw = self.rfile.read(length)
            body = json.loads(raw)
            turn = json.loads(body["messages"][-1]["content"])
            if body.get("model") != MODEL or body.get("stream") is not True or not 0 < body.get("max_tokens", 0) <= 2048 or turn.get("kind") != "current_turn":
                self.send_error(422); return
            marker = turn["input"]["text"]
            cancel = marker == "RPG04_MOCK_CANCEL"
            invalid = marker == "RPG04_MOCK_INVALID"
            with state.lock:
                call = {"sequence":len(state.calls)+1,"kind":"cancel" if cancel else "invalid" if invalid else "normal","inputSha256":sha(raw),"finished":False,"disconnectObserved":False}
                state.calls.append(call)
            self.connection.settimeout(3)
            self.send_response(200)
            self.send_header("Content-Type","text/event-stream; charset=utf-8")
            self.send_header("Connection","close")
            self.end_headers()
            def emit(content="", finish=None):
                event={"model":MODEL,"choices":[{"index":0,"delta":{"content":content},"finish_reason":finish}]}
                if finish: event["usage"]={"prompt_tokens":20,"completion_tokens":100,"total_tokens":120}
                self.wfile.write(("data: "+json.dumps(event,ensure_ascii=False)+"\n\n").encode())
                self.wfile.flush()
            try:
                if cancel:
                    emit('{"format":"modelmirror.ai-rpg.turn-exchange",')
                    for _ in range(200):
                        time.sleep(0.02); emit(" ")
                else:
                    proposal={"narrative":"Offline scene.","suggestedActions":[{"id":"suggestion.wait","label":"Wait","inputKind":"action","text":"wait"}],"informationModules":[],"stateProposals":[],"uncertainties":[]}
                    if turn["input"]["kind"] == "action":
                        proposal["stateProposals"]=[{"fieldRef":"state.rpg04.memory.stm","proposedValue":"Explicitly accepted offline scene."}]
                    exchange={"format":"modelmirror.ai-rpg.turn-exchange","formatVersion":"0.1.0","exchangeId":turn["exchangeId"],"cardPackageRef":turn["cardPackageRef"],"input":turn["input"],"proposal":proposal}
                    text="invalid output" if invalid else json.dumps(exchange,ensure_ascii=False,separators=(",",":"))
                    emit(text[:23]);emit(text[23:])
                emit(finish="stop")
                self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
                call["finished"]=True
            except OSError:
                call["disconnectObserved"]=True
            finally: self.close_connection=True
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    server.daemon_threads=True
    return server

def run_mock(port):
    if port in (8000,18303,18304) or not 1024 <= port <= 65535: raise ValueError("RPG04_PORT_FORBIDDEN")
    if sys.version_info[:2] != (3,12): raise ValueError("RPG04_PYTHON_VERSION")
    node=shutil.which("node")
    if not node or subprocess.check_output([node,"--version"],text=True).strip() != "v24.18.0": raise ValueError("RPG04_NODE_VERSION")
    with socket.socket() as probe: probe.bind(("127.0.0.1",port))
    root=MODULE/".rpg04-work";root.mkdir(exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix="rpg05-http-",dir=root))
    state=FakeState(); process=None; upstream=None; failure=None; candidate=None
    try:
        candidate=copy_candidate(work)
        environment=isolated_environment(work)
        upstream=fake_server(state)
        threading.Thread(target=upstream.serve_forever,daemon=True).start()
        fake_port=upstream.server_address[1]
        environment["MODEL_MIRROR_PROVIDER_INTERNAL_ALLOWLIST"]="127.0.0.1:"+str(fake_port)
        config={"evidenceKind":"mock","port":port,"fakeBaseUrl":"http://127.0.0.1:"+str(fake_port)+"/v1","baseUrl":"http://127.0.0.1:"+str(port),"modelId":MODEL,"workDirectory":str(work)}
        config_path=work/"harness-config.json";write_json(config_path,config)
        environment["RPG04_HARNESS_CONFIG"]=str(config_path)
        with (work/"server.log").open("xb") as log:
            process=subprocess.Popen([sys.executable,"-B",str(Path(__file__).resolve()),"--serve-mock",str(config_path)],cwd=work/"candidate-code",env=environment,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
            opener=build_opener(ProxyHandler({}));ready=False
            for _ in range(250):
                if process.poll() is not None: break
                try:
                    with opener.open(config["baseUrl"]+"/openapi.json",timeout=1) as response: ready=response.status==200
                    if ready: break
                except OSError: pass
                time.sleep(0.1)
            if not ready: raise ValueError("RPG04_SERVER_START_FAILED")
            with (work/"integration-test.log").open("xb") as testlog:
                result=subprocess.run([node,"--test","tests/context-http-integration.test.mjs"],cwd=MODULE,env=environment,stdout=testlog,stderr=subprocess.STDOUT,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode: raise ValueError("RPG04_HTTP_INTEGRATION_FAILED")
            if server_records()!=candidate["files"]: raise ValueError("RPG04_SOURCE_DRIFT")
    except Exception as error:
        failure=str(error) if isinstance(error,ValueError) and str(error).startswith("RPG04_") else "RPG04_HARNESS_FAILED"
    finally:
        stop_owned_process(process)
        if upstream: upstream.shutdown();upstream.server_close()
        record={"gate":"RPG05_OFFLINE_HTTP_OK" if failure is None else "RPG05_OFFLINE_HTTP_FAILED","failure":failure,"evidenceKind":"mock","realProviderDispatches":0,"baseSha":BASE,"candidateTreeSha256":candidate["candidateTreeSha256"] if candidate else None,"logicalRef":work.name,"fakeCalls":state.calls,"ownedProcessStopped":process is None or process.poll() is not None}
        write_json(work/"harness-receipt.json",record)
        print(json.dumps(record))
    return 1 if failure else 0

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--serve-mock",type=Path)
    parser.add_argument("--port",type=int,default=18407)
    args=parser.parse_args()
    if args.serve_mock: serve_mock(args.serve_mock)
    else: raise SystemExit(run_mock(args.port))
