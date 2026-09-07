"""RPG-03 offline gate: isolated real ModelMirror HTTP and a loopback fake Provider.

Only the mock branch seeds qualification. No credentials or shared databases are copied.
All generated files and process output stay under the module's ignored work directory.
"""
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
BASE = "80221379cec850a2b25f5eeeb410233062f3e1ea"
MODEL = "rpg03/fake-text-v1"
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
        raise ValueError("RPG03_HARNESS_BASE_DRIFT")
    if git("branch", "--show-current").decode().strip() != "codex/ai-rpg-rpg03-runtime":
        raise ValueError("RPG03_HARNESS_BRANCH_DRIFT")
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
            raise ValueError("RPG03_HARNESS_SOURCE_PATH")
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


def module_source_binding() -> dict[str, object]:
    files = set((MODULE / "runtime").rglob("*.mjs"))
    files.update((MODULE / "tooling").glob("runtime-*.mjs"))
    files.update((MODULE / "tests").glob("runtime-*.mjs"))
    files.update([MODULE / "scripts" / "rpg03-cli.mjs", Path(__file__).resolve(), MODULE / "package.json", MODULE / "package-lock.json"])
    records = [{"path": file.relative_to(MODULE).as_posix(), "sha256": sha(file.read_bytes())} for file in sorted(files)]
    return {"sha256": sha(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()), "files": records}


class FakeState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls: list[dict[str, object]] = []


def fake_server(state: FakeState) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: object) -> None:
            pass

        def do_POST(self) -> None:
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 1_048_576:
                self.send_error(413)
                return
            raw = self.rfile.read(length)
            body = json.loads(raw)
            messages = body.get("messages", [])
            marker = messages[-1].get("content", "") if messages else ""
            is_cancel = marker == "RPG03_MOCK_CANCEL"
            normal = marker in ("RPG03_MOCK_NORMAL_ONE", "RPG03_MOCK_NORMAL_TWO", "RPG03_MOCK_CLI")
            if body.get("model") != MODEL or body.get("stream") is not True or not (0 < body.get("max_tokens", 0) <= 512) or not (is_cancel or normal):
                self.send_error(422)
                return
            with state.lock:
                call = {"sequence": len(state.calls) + 1, "kind": "cancel" if is_cancel else "normal", "inputSha256": sha(raw), "messageCount": len(messages), "disconnectObserved": False, "finished": False}
                state.calls.append(call)
            self.connection.settimeout(3)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Connection", "close")
            self.send_header("x-request-id", "rpg03-mock-" + str(call["sequence"]))
            self.end_headers()

            def emit(delta: dict[str, object], finish: str | None = None, usage: bool = False) -> None:
                event = {"id": "mock-" + str(call["sequence"]), "object": "chat.completion.chunk", "model": MODEL, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                if usage:
                    event["usage"] = {"prompt_tokens": 12, "completion_tokens": 30, "total_tokens": 42}
                self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8"))
                self.wfile.flush()

            try:
                if is_cancel:
                    emit({"role": "assistant", "content": '{"narrative":"'})
                    for _ in range(150):
                        time.sleep(0.02)
                        emit({"content": "测试草稿 "})
                else:
                    proposal = {"narrative": "Neutral scene " + str(call["sequence"]) + "。", "suggestedActions": [{"id": "suggestion.wait", "label": "Wait", "inputKind": "action", "text": "wait"}], "informationModules": [], "stateProposals": [], "uncertainties": []}
                    text = json.dumps(proposal, ensure_ascii=False, separators=(",", ":"))
                    emit({"role": "assistant", "content": text[:20]})
                    emit({"content": text[20:]})
                emit({}, "stop", True)
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                call["finished"] = True
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError, OSError):
                call["disconnectObserved"] = True
            finally:
                self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    return server


def serve_mock(config_path: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    work = config_path.parent.resolve()
    if not work.is_relative_to((MODULE / ".rpg03-work").resolve()) or config.get("evidenceKind") != "mock":
        raise ValueError("RPG03_HARNESS_CONFIG")
    target = urlsplit(config["fakeBaseUrl"])
    if target.scheme != "http" or target.hostname != "127.0.0.1" or not target.port or target.path != "/v1" or target.username or target.password or target.query or target.fragment or config["baseUrl"] != "http://127.0.0.1:" + str(config["port"]):
        raise ValueError("RPG03_HARNESS_LOOPBACK_REQUIRED")
    code = work / "candidate-code"
    sys.path.insert(0, str(code))
    from server import main as main_module
    from server.model_router import ModelRouterService, RouterConnectionCreate, SQLiteRouterRepository, configure_model_router
    from server.model_router.chat_control import ProviderChatControlService
    from server.model_router.schemas import ProviderChatControlPolicyUpdate, ProviderChatControlRouteUpdate
    import uvicorn

    if not Path(main_module.__file__).resolve().is_relative_to(code.resolve()):
        raise ValueError("RPG03_HARNESS_IMPORT_BOUNDARY")
    repository = SQLiteRouterRepository(os.environ["MODEL_ROUTER_STORAGE_DIR"], master_key=b"x" * 32)
    service = ModelRouterService(repository)
    connection = repository.create_connection("local", RouterConnectionCreate(name="RPG03 loopback mock", kind="newapi", base_url=config["fakeBaseUrl"], api_key="rpg03-mock-token", scopes=["chat"]))
    observed = "2026-09-05T00:00:00+00:00"
    repository.save_test_result("local", connection.id, health="online", model_count=1, checked_at=observed)
    fingerprint = repository.connection_config_fingerprint("local", connection.id)
    refresh_id = "rpg03-mock-refresh"
    repository.claim_catalog_refresh("local", refresh_id=refresh_id, connection_id=connection.id, connection_fingerprint=fingerprint)
    repository.complete_catalog_refresh("local", refresh_id, connection_id=connection.id, models=[{"model_id": MODEL, "normalized_model_id": MODEL, "capability_state": "declared"}], offerings=[], model_count=1, truncated=False, catalog_fingerprint="rpg03-mock-catalog", observed_at=observed)
    certification, created = repository.claim_chat_certification("local", certification_id="rpg03-mock-cert", connection_id=connection.id, connection_fingerprint=fingerprint, contract_version="modelmirror-provider-chat-v1", capability="chat_text", requested_model=MODEL, idempotency_key_hash=sha(b"rpg03-mock-cert"))
    if not created:
        raise ValueError("RPG03_HARNESS_CERTIFICATE_COLLISION")
    repository.complete_chat_certification("local", str(certification["id"]), status="passed", checks={"capability_verified": True}, warning_codes=[], actual_model=MODEL)
    ProviderChatControlService(service).update_policy(ProviderChatControlPolicyUpdate(expected_revision=0, mode="newapi_preferred", stable_model_ids=[MODEL], routes=[ProviderChatControlRouteUpdate(capability="chat_text", connection_ids=[connection.id])]))
    configure_model_router(service)
    write_json(work / "service-binding.json", {"evidenceKind": "mock", "qualificationSeeded": True, "connectionId": connection.id, "connectionFingerprint": fingerprint, "modelId": MODEL, "serviceVersion": main_module.app.version, "uvicornVersion": uvicorn.__version__, "pythonVersion": sys.version.split()[0]})
    uvicorn.run(main_module.app, host="127.0.0.1", port=config["port"], lifespan="off", access_log=False, log_level="warning", timeout_graceful_shutdown=3)


def run_cli(node: str, work: Path, environment: dict[str, str]) -> dict[str, object]:
    command = [node, str(MODULE / "scripts" / "rpg03-cli.mjs"), "--config", str(work / "cli-config.json")]
    output_lines: list[str] = []
    summaries = []
    for phase in ("initial", "resume"):
        with (work / ("cli-" + phase + "-stderr.log")).open("xb") as error_log:
            process = subprocess.Popen(command, cwd=MODULE, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=error_log, text=True, encoding="utf-8")
            received: queue.Queue[str | None] = queue.Queue()

            def read_stdout() -> None:
                for line in process.stdout:
                    received.put(line)
                received.put(None)

            reader = threading.Thread(target=read_stdout, daemon=True)
            reader.start()

            def execute(identifier: str, operation: str, value: object) -> dict[str, object]:
                process.stdin.write(json.dumps({"requestId": identifier, "operation": operation, "input": value}) + "\n")
                process.stdin.flush()
                deadline = time.monotonic() + 15
                while True:
                    try:
                        line = received.get(timeout=max(0.01, deadline - time.monotonic()))
                    except queue.Empty:
                        raise ValueError("RPG03_HARNESS_CLI_TIMEOUT") from None
                    if line is None or len(line) > 65536 or time.monotonic() > deadline:
                        raise ValueError("RPG03_HARNESS_CLI_OUTPUT")
                    output_lines.append(line)
                    result = json.loads(line)
                    if result.get("requestId") == identifier and result.get("kind") != "event":
                        if result.get("valid") is not True:
                            raise ValueError("RPG03_HARNESS_CLI_FAILED")
                        summaries.append({"requestId": identifier, "operation": operation, "revision": result["value"]["revision"], "turnCount": result["value"]["turnCount"]})
                        return result["value"]

            try:
                if phase == "initial":
                    created = execute("cli.create", "create", {"sessionId": "session.cli"})
                    generated = execute("cli.generate", "generate", {"sessionId": "session.cli", "generationId": "generation.cli", "exchangeId": "exchange.cli", "expectedRevision": created["revision"], "input": {"kind": "query", "text": "inspect"}, "messages": [{"role": "user", "content": "RPG03_MOCK_CLI"}], "modelId": MODEL, "settings": {"temperature": 0, "maxTokens": 512}})
                    if generated["status"] != "pending" or generated["turnCount"] != 0:
                        raise ValueError("RPG03_HARNESS_CLI_PENDING")
                    committed = execute("cli.commit", "commit", {"format": "modelmirror.ai-rpg.turn-commit", "formatVersion": "0.1.0", "sessionId": "session.cli", "generationId": "generation.cli", "exchangeId": "exchange.cli", "expectedRevision": generated["revision"], "acceptedStateFields": []})
                    read = execute("cli.read", "read", {"sessionId": "session.cli"})
                    if committed["turnCount"] != 1 or read["revision"] != committed["revision"]:
                        raise ValueError("RPG03_HARNESS_CLI_COMMIT")
                else:
                    resumed = execute("cli.resume", "resume", {"sessionId": "session.cli"})
                    if resumed["turnCount"] != 1 or resumed["revision"] != committed["revision"] + 1:
                        raise ValueError("RPG03_HARNESS_CLI_RECOVERY")
                process.stdin.close()
                if process.wait(timeout=5):
                    raise ValueError("RPG03_HARNESS_CLI_EXIT")
            finally:
                stop_owned_process(process)
                reader.join(timeout=3)
    output = "".join(output_lines)
    with (work / "cli-stdout.jsonl").open("x", encoding="utf-8") as saved:
        saved.write(output)
    return {"exitCode": 0, "commands": len(summaries), "summaries": summaries, "outputSha256": sha(output.encode()), "evidenceKind": "mock"}


def run_mock(port: int) -> Path:
    if not 1024 <= port <= 65535 or port == 8000:
        raise ValueError("RPG03_HARNESS_PORT_INVALID")
    if sys.version_info[:2] != (3, 12):
        raise ValueError("RPG03_HARNESS_PYTHON_VERSION")
    node = shutil.which("node")
    if not node or subprocess.check_output([node, "--version"], text=True).strip() != "v24.18.0":
        raise ValueError("RPG03_HARNESS_NODE_VERSION")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))
    root = MODULE / ".rpg03-work"
    root.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="h1-http-", dir=root))
    state = FakeState()
    candidate = None
    module_binding = None
    upstream = None
    thread = None
    server_process = None
    receipt = None
    failure = None
    try:
        candidate = copy_candidate(work)
        module_binding = module_source_binding()
        write_json(work / "module-source.json", module_binding)
        environment = isolated_environment(work)
        upstream = fake_server(state)
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        fake_port = upstream.server_address[1]
        environment["MODEL_MIRROR_PROVIDER_INTERNAL_ALLOWLIST"] = "127.0.0.1:" + str(fake_port)
        config = {"evidenceKind": "mock", "port": port, "fakeBaseUrl": "http://127.0.0.1:" + str(fake_port) + "/v1", "baseUrl": "http://127.0.0.1:" + str(port), "modelId": MODEL, "candidateTreeSha256": candidate["candidateTreeSha256"], "moduleSourceSha256": module_binding["sha256"], "sessionDirectory": str(work / "sessions"), "outputDirectory": str(work)}
        config_path = work / "harness-config.json"
        write_json(config_path, config)
        environment["RPG03_HARNESS_CONFIG"] = str(config_path)
        with (work / "server.log").open("wb") as server_log:
            server_process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "--serve-mock", str(config_path)], cwd=work / "candidate-code", env=environment, stdout=server_log, stderr=subprocess.STDOUT)
            opener = build_opener(ProxyHandler({}))
            started = False
            for _ in range(150):
                if server_process.poll() is not None:
                    break
                try:
                    with opener.open(Request(config["baseUrl"] + "/openapi.json"), timeout=1) as response:
                        started = response.status == 200
                    if started:
                        break
                except OSError:
                    pass
                time.sleep(0.1)
            if not started:
                raise ValueError("RPG03_HARNESS_SERVER_START_FAILED")
            with (work / "integration-test.log").open("wb") as log:
                result = subprocess.run([node, "--test", str(MODULE / "tests" / "runtime-integration.test.mjs")], cwd=MODULE, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=45)
            if result.returncode:
                raise ValueError("RPG03_HARNESS_INTEGRATION_FAILED")
            cli = run_cli(node, work, environment)
            for _ in range(100):
                if len(state.calls) == 4 and any(call["kind"] == "cancel" and call["disconnectObserved"] for call in state.calls):
                    break
                time.sleep(0.05)
            database = Path(environment["MODEL_ROUTER_STORAGE_DIR"]) / "router.sqlite3"
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
                connection.row_factory = sqlite3.Row
                runs = [dict(row) for row in connection.execute("SELECT id, policy_fingerprint, requested_model, actual_model, strategy, status, result_class, client_cancelled, hard_failure, prompt_tokens, completion_tokens, total_tokens FROM provider_chat_runs ORDER BY created_at, id")]
                attempts = [dict(row) for row in connection.execute("SELECT id, run_id, connection_id, provider_kind, dispatched, status, result_class, actual_model FROM provider_chat_attempts ORDER BY created_at, id")]
            if len(state.calls) != 4 or len(runs) != 4 or len(attempts) != 4 or sum(item["dispatched"] for item in attempts) != 4 or sum(item["status"] == "succeeded" for item in runs) != 3 or not any(item["client_cancelled"] == 1 for item in runs) or not any(call["kind"] == "cancel" and call["disconnectObserved"] for call in state.calls):
                raise ValueError("RPG03_HARNESS_EVIDENCE_MISMATCH")
            if module_source_binding() != module_binding or server_records() != candidate["files"]:
                raise ValueError("RPG03_HARNESS_SOURCE_DRIFT")
            receipt = {"format": "modelmirror.ai-rpg.offline-harness-receipt", "formatVersion": FORMAT_VERSION, "evidenceKind": "mock", "realProviderDispatches": 0, "websiteProbes": 0, "baseSha": BASE, "nodeVersion": "24.18.0", "candidateTreeSha256": candidate["candidateTreeSha256"], "moduleSourceSha256": module_binding["sha256"], "mainSha256": candidate["mainSha256"], "modelId": MODEL, "serviceBinding": json.loads((work / "service-binding.json").read_text(encoding="utf-8")), "integrationReceiptSha256": sha((work / "integration-receipt.json").read_bytes()), "cli": cli, "fakeCalls": state.calls, "serverRuns": runs, "serverAttempts": attempts, "gate": "RPG03_OFFLINE_HTTP_OK"}
    except Exception as error:
        failure = str(error) if isinstance(error, ValueError) and str(error).startswith("RPG03_") else "RPG03_HARNESS_FAILED"
    finally:
        try:
            stop_owned_process(server_process)
        except (OSError, subprocess.SubprocessError):
            failure = failure or "RPG03_HARNESS_PROCESS_CLEANUP_FAILED"
        try:
            if upstream is not None:
                if thread is not None and thread.is_alive():
                    upstream.shutdown()
                upstream.server_close()
            if thread is not None:
                thread.join(timeout=3)
        except (OSError, RuntimeError):
            failure = failure or "RPG03_HARNESS_UPSTREAM_CLEANUP_FAILED"
    if failure:
        try:
            write_json(work / "failure-receipt.json", {"code": failure, "evidenceKind": "mock", "realProviderDispatches": 0, "candidateTreeSha256": candidate["candidateTreeSha256"] if candidate else None, "moduleSourceSha256": module_binding["sha256"] if module_binding else None, "fakeCalls": state.calls})
        except OSError:
            print("RPG03_HARNESS_EVIDENCE_WRITE_FAILED evidence=" + work.name)
        print(failure + " evidence=" + work.name)
        raise SystemExit(1) from None
    receipt["ownedProcessesStopped"] = True
    write_json(work / "harness-receipt.json", receipt)
    return work


def self_test() -> None:
    """Three bounded failure-path checks; no HTTP server or Provider is started."""
    import contextlib
    import io
    from unittest.mock import patch

    calls = []

    class OwnedProcess:
        def poll(self):
            return None

        def terminate(self):
            calls.append("terminate")

        def kill(self):
            calls.append("kill")

        def wait(self, timeout):
            calls.append("wait")
            if calls.count("wait") == 1:
                raise subprocess.TimeoutExpired("owned-fixture", timeout)
            return 0

    stop_owned_process(OwnedProcess())
    assert calls == ["terminate", "wait", "kill", "wait"]
    root = MODULE / ".rpg03-work"
    root.mkdir(exist_ok=True)
    sample = Path(tempfile.mkdtemp(prefix="h1-self-test-", dir=root))
    (sample / "server").mkdir()
    (sample / "server" / "main.py").write_text("# main fixture\n", encoding="utf-8")
    other = sample / "server" / "other.py"
    other.write_text("# initial fixture\n", encoding="utf-8")

    def fixture_git(*arguments):
        if arguments[0] == "rev-parse":
            return BASE.encode()
        if arguments[0] == "branch":
            return b"codex/ai-rpg-rpg03-runtime"
        return b"server/main.py\0server/other.py\0"

    with patch.dict(globals(), REPOSITORY=sample, git=fixture_git):
        before = server_records()
        other.write_text("# changed fixture\n", encoding="utf-8")
        after = server_records()
        assert before[0] == after[0] and before != after
    previous = set(root.glob("h1-http-*"))
    with patch.dict(globals(), copy_candidate=lambda _work: (_ for _ in ()).throw(ValueError("RPG03_HARNESS_BASE_DRIFT"))):
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                run_mock(18303)
            except SystemExit as error:
                assert error.code == 1
            else:
                raise AssertionError("Expected failure")
    generated = set(root.glob("h1-http-*")) - previous
    assert len(generated) == 1
    failure = json.loads((generated.pop() / "failure-receipt.json").read_text(encoding="utf-8"))
    assert failure["code"] == "RPG03_HARNESS_BASE_DRIFT" and failure["fakeCalls"] == [] and failure["candidateTreeSha256"] is None
    print("RPG03_HARNESS_SELF_TEST_OK tests=3")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18303)
    parser.add_argument("--serve-mock", type=Path)
    parser.add_argument("--self-test", action="store_true")
    options = parser.parse_args()
    if options.self_test:
        self_test()
    elif options.serve_mock:
        serve_mock(options.serve_mock)
    else:
        try:
            completed = run_mock(options.port)
            print("RPG03_OFFLINE_HTTP_OK evidence=" + completed.name)
        except (OSError, ValueError, subprocess.SubprocessError):
            print("RPG03_HARNESS_PREFLIGHT_FAILED")
            raise SystemExit(1) from None
