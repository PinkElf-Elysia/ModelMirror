"""Loopback-only text trial UI. No application imports, SDK, or startup inference."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import signal
import sys
import threading
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Coroutine
from uuid import uuid4

from .adapter import AgentsAPIAdapter, AgentsAPIError, TurnResult, recorded_usage
from .smoke import CALL_SECONDS, CLEANUP_SECONDS, MARKER, cleanup_session, final_history, safe_error, source_receipt

MODEL = "gpt-6-astra"
PRESETS = (f"记住标记 {MARKER}，只回复 READY。", "返回上一轮记住的标记，只输出标记。")
DIRECTORY = Path(__file__).resolve().parent
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/logo.png": ("logo.png", "image/png"),
          "/user-round.svg": ("user-round.svg", "image/svg+xml")}


class TrialError(Exception):
    def __init__(self, category: str, status: int = 409):
        self.category, self.status = category, status


class DemoAdapter:
    """Explicit offline simulation. Never instantiates an HTTP client."""

    def __init__(self, on_text, delay: float = .18):
        self.on_text, self.delay = on_text, delay
        self.session_id = None
        self.turn_ids: list[str] = []
        self.results: list[TurnResult] = []
        self.requests: list[dict] = []
        self.create_attempted = False
        self.inputs_submitted = 0
        self.last_status = None
        self.deleted = False

    async def create_session(self, model, text):
        self.create_attempted = True
        self.session_id = "demo_session_" + uuid4().hex[:12]
        return await self.continue_session(text)

    async def continue_session(self, text):
        self.inputs_submitted += 1
        tid = "demo_turn_" + str(self.inputs_submitted)
        self.turn_ids.append(tid)
        self.last_status = "in_progress"
        answer = ("READY" if self.inputs_submitted == 1 else MARKER) if text in PRESETS else "离线演示已接收文本。此输出来自本地模拟，不是模型回答。"
        for index in range(1, len(answer) + 1):
            await asyncio.sleep(self.delay)
            self.on_text(tid, answer[:index])
        self.last_status = "completed"
        result = TurnResult(self.session_id, tid, "completed", answer, None, 0)
        self.results.append(result)
        return result

    async def retrieve_session(self, sid):
        if self.deleted:
            raise AgentsAPIError("http_error", 404)
        return {"id": sid, "status": "idle", "environment": {"type": "none"},
                "agent": {"model": MODEL, "tools": [], "multi_agent": {"enabled": False}}}

    async def retrieve_turn(self, sid, tid):
        return {"id": tid, "status": self.last_status, "subagent_id": None, "usage": None}

    async def list_items(self, sid):
        return [{"type": "message", "role": "assistant", "phase": "final_answer",
                 "turn_id": row.turn_id, "status": "completed",
                 "content": [{"type": "output_text", "text": row.text}]} for row in self.results]

    async def cancel_turn(self, sid):
        self.last_status = "cancelled"

    async def delete_session(self, sid):
        self.deleted = True
        return {"deleted": True}

    async def __aexit__(self, *_):
        pass


class Experiment:
    """One process, one session; all state and commands live on one asyncio loop."""

    def __init__(self, *, mode="preview", key="", adapter=None, receipt_path: Path | None = None,
                 metadata=None, call_seconds=CALL_SECONDS, cleanup_seconds=CLEANUP_SECONDS,
                 demo_delay=.18):
        if mode not in {"preview", "demo", "live"}:
            raise ValueError("invalid mode")
        self.mode, self.key = mode, key if mode == "live" else ""
        self.call_seconds, self.cleanup_seconds = call_seconds, cleanup_seconds
        self.receipt_path = receipt_path
        self.csrf = secrets.token_urlsafe(32)
        self.phase = "ready"
        self.task: asyncio.Task | None = None
        self.expiry: asyncio.Task | None = None
        self.deadline: float | None = None
        self.stop_reason = "interrupted"
        self.closed = False
        self.operations: dict[str, tuple[int, str]] = {}
        self.storage_error = None
        self.adapter = adapter
        if adapter is not None:
            adapter.on_text = self._on_text
        elif mode == "demo":
            self.adapter = DemoAdapter(self._on_text, demo_delay)
        elif mode == "live" and key:
            self.adapter = AgentsAPIAdapter(key, on_text=self._on_text)
        self.report: dict[str, Any] = {
            "schema_version": 2, "run_id": uuid4().hex,
            "evidence": {"live": "real_api", "demo": "offline_demo", "preview": "not_run"}[mode],
            "model": MODEL, "started_at": None, "status": "not_run", "invocation_status": "not_run",
            "limits": {"sessions": 1, "inputs": 2, "input_chars": 4096,
                       "call_seconds": call_seconds, "cleanup_seconds": cleanup_seconds},
            "turns": [], "history": [], "history_status": "not_run",
            "cleanup": {"status": "not_run", "deletion_confirmed": False,
                        "api_absent": False, "physical_cleanup": "unverified"},
            **(metadata or {}),
        }

    def _encoded(self, value) -> str:
        encoded = json.dumps(value, ensure_ascii=False, indent=2)
        if self.key:
            # Replace JSON-escaped as well as literal credential representations.
            encoded = encoded.replace(json.dumps(self.key)[1:-1], "[REDACTED]").replace(self.key, "[REDACTED]")
        return re.sub(r"sk-[A-Za-z0-9_-]{16,}", "[REDACTED]", encoded)

    def _record_ids(self):
        if self.adapter:
            self.report.update(session_id=self.adapter.session_id, turn_ids=list(self.adapter.turn_ids),
                               inputs_submitted=self.adapter.inputs_submitted, requests=self.adapter.requests)
            for index, row in enumerate(self.report["turns"]):
                if row["turn_id"] is None and index < len(self.adapter.turn_ids):
                    row["turn_id"] = self.adapter.turn_ids[index]

    def _checkpoint(self, *, strict=False):
        self._record_ids()
        if self.receipt_path is None:
            return  # Test harness only; CLI always supplies a path.
        try:
            self.receipt_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.receipt_path.with_suffix(".tmp")
            temporary.write_text(self._encoded(self.report) + "\n", encoding="utf-8")
            temporary.replace(self.receipt_path)
        except OSError:
            self.storage_error = "receipt_write_failed"
            if strict:
                raise TrialError("receipt_write_failed", 503) from None

    def _on_text(self, tid: str, text: str):
        if self.report["turns"]:
            self.report["turns"][-1].update(turn_id=tid, output=text)

    async def snapshot(self):
        previous = self.report.get("session_id")
        self._record_ids()
        if previous != self.report.get("session_id"):
            self._checkpoint()
        remaining = None if self.deadline is None else max(0, self.deadline - asyncio.get_running_loop().time())
        return json.loads(self._encoded({
            **self.report, "phase": self.phase, "mode": self.mode,
            "credentials_configured": bool(self.key), "presets": PRESETS,
            "remaining_seconds": round(remaining) if remaining is not None else None,
            "receipt_error": self.storage_error,
            "can_send": not self.closed and self.adapter is not None and not self.storage_error
                        and self.phase in {"ready", "awaiting_input"} and len(self.report["turns"]) < 2,
        }))

    async def submit(self, body):
        if not isinstance(body, dict) or set(body) != {"text", "next_turn", "operation_id"}:
            raise TrialError("invalid_request", 400)
        text, number, oid = body["text"], body["next_turn"], body["operation_id"]
        if (not isinstance(text, str) or not text.strip() or len(text) > 4096
                or type(number) is not int or number not in (1, 2)
                or not isinstance(oid, str) or not re.fullmatch(r"[a-fA-F0-9-]{32,36}", oid)):
            raise TrialError("invalid_input", 400)
        if (self.key and self.key in text) or re.search(r"sk-[A-Za-z0-9_-]{16,}", text):
            raise TrialError("credential_in_input", 400)
        if oid in self.operations:
            if self.operations[oid] != (number, text):
                raise TrialError("operation_conflict")
            return await self.snapshot()  # A duplicate acknowledgment never dispatches again.
        if (self.closed or self.storage_error or self.adapter is None or self.mode == "preview"
                or self.phase not in {"ready", "awaiting_input"}
                or number != len(self.report["turns"]) + 1
                or (self.deadline is not None and asyncio.get_running_loop().time() >= self.deadline)):
            raise TrialError("submission_blocked")
        self._checkpoint(strict=True)  # Writability is checked before any billable dispatch.
        self.operations[oid] = (number, text)
        self.report["turns"].append({"number": number, "operation_id": oid, "input": text, "output": "", "turn_id": None,
                                     "status": "in_progress", "usage": None, "expected_text_check": "not_applicable"})
        self.phase = "running"
        self.report["status"] = "in_progress"
        self.report["invocation_status"] = "in_progress"
        self.report["history_status"] = "not_run"
        if self.deadline is None:
            self.report["started_at"] = datetime.now(timezone.utc).isoformat()
            self.deadline = asyncio.get_running_loop().time() + self.call_seconds
            self.expiry = asyncio.create_task(self._expire())
        self._checkpoint()
        self.task = asyncio.create_task(self._run(text, number))
        return await self.snapshot()

    async def _inspect(self, result):
        sid = self.adapter.session_id
        state = await self.adapter.retrieve_session(sid)
        agent = state.get("agent")
        if (state.get("status") != "idle" or state.get("required_actions")
                or state.get("environment") != {"type": "none"} or not isinstance(agent, dict)
                or agent.get("tools") != [] or not isinstance(agent.get("multi_agent"), dict)
                or agent["multi_agent"].get("enabled") is not False):
            raise AgentsAPIError("session_configuration_mismatch")
        saved = await self.adapter.retrieve_turn(sid, result.turn_id)
        if saved.get("status") != "completed" or saved.get("subagent_id") is not None:
            raise AgentsAPIError("saved_turn_not_completed")
        items = await self.adapter.list_items(sid)
        history = []
        for row in self.report["turns"]:
            output = final_history(items, row["turn_id"])
            if output != row["output"]:
                raise AgentsAPIError("saved_history_mismatch")
            history.append({"turn_id": row["turn_id"], "text": output})
        self.report["history"] = history
        self.report["history_status"] = "passed"
        self.report["turns"][-1]["usage"] = recorded_usage(saved.get("usage")) or result.usage

    async def _run(self, text, number):
        row = self.report["turns"][-1]
        try:
            result = await (self.adapter.create_session(MODEL, text) if number == 1
                            else self.adapter.continue_session(text))
            row.update(turn_id=result.turn_id, output=result.text, status=result.status, usage=result.usage)
            expected = ("READY", MARKER)[number - 1]
            if all(r["input"] == PRESETS[r["number"] - 1] for r in self.report["turns"]):
                row["expected_text_check"] = "passed" if result.text == expected else "failed"
                if result.text != expected:
                    raise AgentsAPIError("preset_output_mismatch")
            await self._inspect(result)
            self.report["invocation_status"] = "passed"
            self.report["status"] = "completed" if number == 2 else "partial"
            self.phase = "awaiting_input"
            self._checkpoint()
            if number == 2:
                await self._cleanup()
        except (Exception, asyncio.CancelledError) as error:
            row["status"] = ("cancelled" if self.adapter.last_status == "cancelled" else
                             "failed" if self.adapter.last_status == "failed" else "unconfirmed")
            self.report["status"] = "failed"
            self.report["invocation_status"] = "failed"
            self.report["error"] = ({"category": self.stop_reason} if isinstance(error, asyncio.CancelledError)
                                    else safe_error(error))
            await self._cleanup()

    async def _cleanup(self):
        if self.report["cleanup"]["status"] != "not_run":
            return
        self.phase = "cleaning"
        if self.expiry and self.expiry is not asyncio.current_task():
            self.expiry.cancel()
        self._checkpoint()
        await cleanup_session(self.adapter, self.report, seconds=self.cleanup_seconds)
        if self.report["cleanup"]["status"] == "failed":
            self.report["status"] = "failed"
            self.report.setdefault("error", {"category": "cleanup_unconfirmed"})
        if self.report["cleanup"].get("observed_turn_status") == "cancelled" and self.report["turns"]:
            self.report["turns"][-1]["status"] = "cancelled"
        self.phase = "finished"
        self.report["finished_at"] = datetime.now(timezone.utc).isoformat()
        self._checkpoint()

    async def _expire(self):
        await asyncio.sleep(self.call_seconds)
        self.stop_reason = "deadline_exceeded"
        if self.task and not self.task.done():
            self._stop_active()
        else:
            self.report.update(status="failed", invocation_status="failed", error={"category": "deadline_exceeded"})
            self.task = asyncio.create_task(self._cleanup())

    def _stop_active(self):
        previous = self.task
        previous.cancel()
        self.phase = "cancelling"

        async def finish():
            await asyncio.gather(previous, return_exceptions=True)
            # Cancellation before the coroutine's first instruction bypasses its
            # exception handler. Still close this experiment without dispatching.
            if self.phase != "finished":
                self.report.update(status="failed", invocation_status="failed",
                                   error={"category": self.stop_reason})
                if self.report["turns"]:
                    self.report["turns"][-1]["status"] = "unconfirmed"
                await self._cleanup()
        self.task = asyncio.create_task(finish())

    async def end(self, *, cancel=False):
        if self.phase in {"cleaning", "finished", "cancelling"}:
            return await self.snapshot()
        if cancel and self.phase != "running":
            raise TrialError("no_active_turn")
        if self.task and not self.task.done():
            self.stop_reason = "cancel_requested"
            self._stop_active()  # Local stop and remote cancellation confirmation are separate.
        elif self.adapter is not None:
            self.phase = "cleaning"
            self.task = asyncio.create_task(self._cleanup())
        return await self.snapshot()

    async def receipt(self):
        self._checkpoint()
        return json.loads(self._encoded({**self.report, "receipt_error": self.storage_error}))

    async def close(self):
        self.closed = True
        await self.end()
        if self.task:
            await self.task
        if self.expiry:
            self.expiry.cancel()
            await asyncio.gather(self.expiry, return_exceptions=True)
        if self.adapter:
            await self.adapter.__aexit__(None, None, None)


class LoopThread:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def call(self, coroutine: Coroutine, timeout=5):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout)

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()


class TrialServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, port: int, bridge: LoopThread, experiment: Experiment):
        self.bridge, self.experiment = bridge, experiment
        super().__init__(("127.0.0.1", port), TrialHandler)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class TrialHandler(BaseHTTPRequestHandler):
    server: TrialServer
    server_version = "AgentsExperiment"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_):
        pass  # Never log paths, bodies, credentials, or request headers.

    def _send(self, status: int, body: bytes, content_type="application/json; charset=utf-8", *, download=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if download:
            self.send_header("Content-Disposition", 'attachment; filename="agents-api-receipt.json"')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Browser disconnects never cancel or replay provider input.

    def _json(self, status, value, *, download=False):
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), download=download)

    def _guard(self, *, api=False, mutation=False):
        if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
            raise TrialError("invalid_host", 403)
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            raise TrialError("invalid_origin", 403)
        # Native same-origin attachment downloads cannot add a custom header.
        # Cross-site navigations, missing Fetch Metadata and state reads still
        # require the header; all mutations also require Origin plus CSRF.
        attachment = (not mutation and self.path == "/api/receipt"
                      and self.headers.get("Sec-Fetch-Site") == "same-origin")
        if api and ((self.headers.get("X-Experiment-Client") != "1" and not attachment)
                    or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            raise TrialError("same_origin_required", 403)
        if mutation:
            token = self.headers.get("X-Experiment-CSRF", "")
            if (origin != self.server.origin or not token.isascii()
                    or not secrets.compare_digest(token, self.server.experiment.csrf)):
                raise TrialError("invalid_csrf", 403)

    def _handle(self, *, mutation=False):
        try:
            api = self.path.startswith("/api/")
            self._guard(api=api, mutation=mutation)
            if not mutation and self.path in STATIC:
                filename, mime = STATIC[self.path]
                self._send(200, (DIRECTORY / "ui" / filename).read_bytes(), mime)
                return
            if not mutation and self.path in {"/api/state", "/api/receipt"}:
                is_receipt = self.path == "/api/receipt"
                method = self.server.experiment.receipt if is_receipt else self.server.experiment.snapshot
                result = self.server.bridge.call(method())
                if not is_receipt:
                    result["csrf"] = self.server.experiment.csrf
                self._json(200, result, download=is_receipt)
                return
            if not mutation or self.path not in {"/api/input", "/api/cancel", "/api/cleanup"}:
                raise TrialError("not_found", 404)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise TrialError("json_required", 415)
            raw_length = self.headers.get("Content-Length", "")
            if self.headers.get("Transfer-Encoding") or not raw_length.isdigit() or not 0 < int(raw_length) <= 24576:
                raise TrialError("invalid_body_length", 413)
            body = json.loads(self.rfile.read(int(raw_length)).decode("utf-8"))
            if self.path == "/api/input":
                operation = self.server.experiment.submit(body)
            else:
                if body != {}:
                    raise TrialError("invalid_request", 400)
                operation = self.server.experiment.end(cancel=self.path == "/api/cancel")
            self._json(202, self.server.bridge.call(operation))
        except TrialError as error:
            self._json(error.status, {"error": {"category": error.category}})
        except (ValueError, UnicodeError):
            self._json(400, {"error": {"category": "invalid_json"}})
        except (FutureTimeout, TimeoutError):
            self._json(503, {"error": {"category": "local_state_unavailable"}})
        except Exception:
            self._json(500, {"error": {"category": "local_error"}})

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle(mutation=True)

    def do_OPTIONS(self):
        self._json(403, {"error": {"category": "cross_origin_disabled"}})


def main(argv=None):
    parser = argparse.ArgumentParser(description="独立 OpenAI Agents API 文本试验台，仅监听 127.0.0.1。默认只读预览。")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--demo", action="store_true", help="明确标注的离线模拟；不读取密钥、不调用 OpenAI。")
    modes.add_argument("--live", action="store_true", help="允许界面按钮触发一个真实会话，最多两轮。")
    parser.add_argument("--model", choices=[MODEL], help="--live 必须显式指定；不自动换模型。")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args(argv)
    if args.live and not args.model:
        parser.error("--live requires --model gpt-6-astra")
    if not 1 <= args.port <= 65535:
        parser.error("invalid port")
    mode = "live" if args.live else "demo" if args.demo else "preview"
    key = os.environ.get("OPENAI_API_KEY", "").strip() if args.live else ""
    if args.live and not key:
        parser.error("OPENAI_API_KEY is missing; no request was sent")
    root = DIRECTORY.parents[1]
    metadata = source_receipt(root)
    for name in ("web.py", "test_web.py", "ui/index.html", "ui/style.css", "ui/app.js", "ui/logo.png", "ui/user-round.svg"):
        metadata["source_sha256"][name] = hashlib.sha256((DIRECTORY / name).read_bytes()).hexdigest()
    path = root / "artifacts" / "openai-agents-api-ui" / (uuid4().hex + ".json")
    bridge = LoopThread()

    async def initialize():
        return Experiment(mode=mode, key=key, receipt_path=path, metadata=metadata)

    experiment = bridge.call(initialize())
    server = None
    try:
        server = TrialServer(args.port, bridge, experiment)
        def interrupt(*_):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, interrupt)
        print(f"{server.origin}/  mode={mode}  (no inference on startup)", flush=True)
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.server_close()  # serve_forever has exited; do not deadlock on shutdown().
        bridge.call(experiment.close(), timeout=CLEANUP_SECONDS + 5)
        bridge.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
