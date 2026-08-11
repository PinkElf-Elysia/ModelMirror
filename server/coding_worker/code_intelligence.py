from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import unquote, urlsplit


MAX_LSP_MESSAGE_BYTES = 8 * 1024 * 1024
MAX_LSP_RESULTS = 2000
TYPESCRIPT_SERVER_PATH = "/usr/local/lib/node_modules/typescript/lib"


class CodeIntelligenceError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class _LspClient:
    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        repository: Path,
        language_id: str,
    ) -> None:
        if process.stdin is None or process.stdout is None:
            raise CodeIntelligenceError(
                "Language server streams are unavailable.",
                code="code_intelligence_unavailable",
            )
        self.process = process
        self.stdin = process.stdin
        self.stdout = process.stdout
        self.repository = repository
        self.language_id = language_id
        self._next_id = 1
        self.notifications: list[dict[str, Any]] = []
        self._typescript_progress_tokens: set[str | int] = set()
        self._typescript_project_ready = False

    async def initialize(self) -> None:
        root_uri = self.repository.as_uri()
        initialization_options: dict[str, Any] = {
            "disableAutomaticTypingAcquisition": True,
            "preferences": {"includePackageJsonAutoImports": "off"},
        }
        if self.language_id in {
            "typescript",
            "typescriptreact",
            "javascript",
            "javascriptreact",
        }:
            initialization_options["tsserver"] = {
                "path": TYPESCRIPT_SERVER_PATH,
                "useSyntaxServer": "never",
            }
        await self.request(
            "initialize",
            {
                "processId": None,
                "clientInfo": {"name": "ModelMirror", "version": "v15"},
                "rootUri": root_uri,
                "workspaceFolders": [{"uri": root_uri, "name": "workspace"}],
                "capabilities": {
                    "window": {"workDoneProgress": True},
                    "textDocument": {
                        "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                        "definition": {"linkSupport": True},
                        "references": {},
                        "hover": {"contentFormat": ["plaintext", "markdown"]},
                        "diagnostic": {},
                        "publishDiagnostics": {
                            "relatedInformation": False,
                            "versionSupport": True,
                        },
                    },
                    "workspace": {"workspaceFolders": True, "configuration": True},
                },
                "initializationOptions": initialization_options,
            },
            timeout=10,
        )
        await self.notify("initialized", {})

    async def open_document(self, target: Path, content: str) -> str:
        uri = target.as_uri()
        await self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": self.language_id,
                    "version": 1,
                    "text": content,
                }
            },
        )
        return uri

    async def request(
        self, method: str, params: dict[str, Any], *, timeout: float = 8
    ) -> Any:
        request_id = self._next_id
        self._next_id += 1
        await self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        deadline = monotonic() + timeout
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise CodeIntelligenceError(
                    "Language server request timed out.",
                    code="code_intelligence_timeout",
                )
            message = await asyncio.wait_for(self._read(), timeout=remaining)
            if message.get("id") == request_id and "method" not in message:
                error = message.get("error")
                if isinstance(error, dict):
                    raise CodeIntelligenceError(
                        "Language server rejected the request.",
                        code="code_intelligence_request_failed",
                    )
                return message.get("result")
            await self._handle_unsolicited(message)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def semantic_request(
        self, method: str, params: dict[str, Any], *, timeout: float = 10
    ) -> Any:
        result = await self.request(method, params, timeout=timeout)
        if (
            self.language_id
            in {"typescript", "typescriptreact", "javascript", "javascriptreact"}
            and result in (None, [], {})
            and not self._typescript_project_ready
        ):
            await self._wait_for_typescript_project(timeout=timeout)
            result = await self.request(method, params, timeout=timeout)
        return result

    async def collect_diagnostics(self, uri: str, *, timeout: float = 10) -> list[Any]:
        if self.language_id in {
            "typescript",
            "typescriptreact",
            "javascript",
            "javascriptreact",
        }:
            return await self._collect_typescript_diagnostics(uri, timeout=timeout)
        latest: list[Any] | None = None
        for message in reversed(self.notifications):
            diagnostics = self._published_diagnostics(message, uri)
            if diagnostics:
                return diagnostics
            if diagnostics is not None:
                latest = diagnostics
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            try:
                message = await asyncio.wait_for(
                    self._read(), timeout=min(0.5, deadline - monotonic())
                )
            except TimeoutError:
                continue
            await self._handle_unsolicited(message)
            diagnostics = self._published_diagnostics(message, uri)
            if diagnostics:
                return diagnostics
            if diagnostics is not None:
                latest = diagnostics
        return latest or []

    async def _collect_typescript_diagnostics(
        self, uri: str, *, timeout: float
    ) -> list[Any]:
        await self.semantic_request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": uri}},
            timeout=timeout,
        )
        diagnostics: list[Any] = []
        for command in (
            "syntacticDiagnosticsSync",
            "semanticDiagnosticsSync",
            "suggestionDiagnosticsSync",
        ):
            result: list[Any] | None = None
            response_status = "missing_response"
            for attempt in range(2):
                raw = await self.request(
                    "workspace/executeCommand",
                    {
                        "command": "typescript.tsserverRequest",
                        "arguments": [
                            command,
                            {"file": uri, "includeLinePosition": True},
                            {},
                        ],
                    },
                    timeout=timeout,
                )
                response_status = _typescript_diagnostic_response_status(raw)
                result = _typescript_diagnostic_response(raw)
                if result is not None:
                    break
                if attempt == 0:
                    await self._wait_for_typescript_project(timeout=timeout)
            if result is None:
                raise CodeIntelligenceError(
                    "TypeScript diagnostics are unavailable "
                    f"({command}:{response_status}).",
                    code="code_intelligence_invalid_response",
                )
            diagnostics.extend(result)
        return diagnostics[:MAX_LSP_RESULTS]

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self.request("shutdown", {}, timeout=2)
        with contextlib.suppress(Exception):
            await self.notify("exit", {})

    async def _handle_unsolicited(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if isinstance(method, str) and "id" in message:
            params = message.get("params")
            result: Any = None
            if method == "workspace/configuration":
                items = params.get("items", []) if isinstance(params, dict) else []
                result = [{} for _ in items] if isinstance(items, list) else []
            elif method == "workspace/workspaceFolders":
                result = [{"uri": self.repository.as_uri(), "name": "workspace"}]
            elif method == "workspace/applyEdit":
                result = {"applied": False, "failureReason": "read-only LSP"}
            await self._write({"jsonrpc": "2.0", "id": message["id"], "result": result})
        elif isinstance(method, str):
            self._record_progress(message)
            self.notifications.append(message)
            if len(self.notifications) > 256:
                self.notifications.pop(0)

    async def _wait_for_typescript_project(self, *, timeout: float) -> None:
        deadline = monotonic() + timeout
        while not self._typescript_project_ready and monotonic() < deadline:
            try:
                message = await asyncio.wait_for(
                    self._read(), timeout=min(0.5, deadline - monotonic())
                )
            except TimeoutError:
                continue
            await self._handle_unsolicited(message)

    def _record_progress(self, message: dict[str, Any]) -> None:
        if message.get("method") != "$/progress":
            return
        params = message.get("params")
        if not isinstance(params, dict):
            return
        token = params.get("token")
        value = params.get("value")
        if not isinstance(token, (str, int)) or not isinstance(value, dict):
            return
        kind = value.get("kind")
        title = value.get("title")
        if kind == "begin" and isinstance(title, str) and "JS/TS" in title:
            self._typescript_progress_tokens.add(token)
        elif kind == "end" and token in self._typescript_progress_tokens:
            self._typescript_progress_tokens.remove(token)
            if not self._typescript_progress_tokens:
                self._typescript_project_ready = True

    @staticmethod
    def _published_diagnostics(
        message: dict[str, Any], uri: str
    ) -> list[Any] | None:
        if message.get("method") != "textDocument/publishDiagnostics":
            return None
        params = message.get("params")
        if not isinstance(params, dict) or params.get("uri") != uri:
            return None
        diagnostics = params.get("diagnostics")
        return diagnostics if isinstance(diagnostics, list) else []

    async def _write(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
        if len(payload) > MAX_LSP_MESSAGE_BYTES:
            raise CodeIntelligenceError(
                "Language server request is too large.",
                code="code_intelligence_input_invalid",
            )
        self.stdin.write(
            f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload
        )
        await self.stdin.drain()

    async def _read(self) -> dict[str, Any]:
        content_length: int | None = None
        while True:
            line = await self.stdout.readline()
            if not line:
                raise CodeIntelligenceError(
                    "Language server exited unexpectedly.",
                    code="code_intelligence_unavailable",
                )
            if len(line) > 8192:
                raise CodeIntelligenceError(
                    "Language server frame is invalid.",
                    code="code_intelligence_invalid_response",
                )
            if line == b"\r\n":
                break
            name, separator, value = line.partition(b":")
            if separator and name.lower() == b"content-length":
                try:
                    content_length = int(value.strip())
                except ValueError as exc:
                    raise CodeIntelligenceError(
                        "Language server frame is invalid.",
                        code="code_intelligence_invalid_response",
                    ) from exc
        if content_length is None or not 0 <= content_length <= MAX_LSP_MESSAGE_BYTES:
            raise CodeIntelligenceError(
                "Language server frame is invalid.",
                code="code_intelligence_invalid_response",
            )
        payload = await self.stdout.readexactly(content_length)
        try:
            message = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CodeIntelligenceError(
                "Language server response is invalid.",
                code="code_intelligence_invalid_response",
            ) from exc
        if not isinstance(message, dict):
            raise CodeIntelligenceError(
                "Language server response is invalid.",
                code="code_intelligence_invalid_response",
            )
        return message


async def query_code_intelligence(
    *,
    repository: Path,
    relative_path: str,
    operation: str,
    line: int,
    character: int,
    environment: dict[str, str],
    runtime_root: Path,
) -> dict[str, Any]:
    target = repository.joinpath(*relative_path.split("/"))
    suffix = target.suffix.lower()
    if suffix == ".py":
        command = ("pyright-langserver", "--stdio")
        language_id = "python"
    elif suffix in {".ts", ".tsx", ".js", ".jsx"}:
        command = ("typescript-language-server", "--stdio")
        language_id = {
            ".ts": "typescript",
            ".tsx": "typescriptreact",
            ".js": "javascript",
            ".jsx": "javascriptreact",
        }[suffix]
    else:
        raise CodeIntelligenceError(
            "File language is unsupported.", code="code_intelligence_unsupported"
        )
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CodeIntelligenceError(
            "Code entry is unavailable.", code="code_intelligence_input_invalid"
        ) from exc
    home = runtime_root / "home"
    temporary = runtime_root / "tmp"
    home.mkdir(parents=True)
    temporary.mkdir()
    child_environment = {
        **environment,
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "TSS_LOG": "-level off",
        "PYRIGHT_PYTHON_FORCE_VERSION": "latest",
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).with_name("shell_sandbox.py")),
        "--repository",
        str(repository),
        "--home",
        str(home),
        "--temporary",
        str(temporary),
        "--cwd",
        str(repository),
        "--repository-read-only",
        "--",
        *command,
        cwd=repository,
        env=child_environment,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    stderr_task = asyncio.create_task(_drain_stderr(process.stderr))
    client = _LspClient(process, repository=repository, language_id=language_id)
    try:
        await client.initialize()
        uri = await client.open_document(target, content)
        document = {"uri": uri}
        position = {"line": line, "character": character}
        if operation == "symbols":
            raw = await client.semantic_request(
                "textDocument/documentSymbol", {"textDocument": document}
            )
            result = {"symbols": _normalize_symbols(raw)}
        elif operation == "definition":
            raw = await client.semantic_request(
                "textDocument/definition",
                {"textDocument": document, "position": position},
            )
            result = {"locations": _normalize_locations(raw, repository)}
        elif operation == "references":
            raw = await client.semantic_request(
                "textDocument/references",
                {
                    "textDocument": document,
                    "position": position,
                    "context": {"includeDeclaration": True},
                },
            )
            result = {"locations": _normalize_locations(raw, repository)}
        elif operation == "hover":
            raw = await client.semantic_request(
                "textDocument/hover",
                {"textDocument": document, "position": position},
            )
            result = {"hover": _normalize_hover(raw, repository)}
        elif operation == "diagnostics":
            diagnostics = await client.collect_diagnostics(uri)
            result = {
                "diagnostics": _normalize_diagnostics(diagnostics, repository)
            }
        else:
            raise CodeIntelligenceError(
                "Code intelligence operation is invalid.",
                code="code_intelligence_input_invalid",
            )
        return {"language": language_id, "path": relative_path, **result}
    finally:
        await client.close()
        await _terminate(process)
        await asyncio.gather(stderr_task, return_exceptions=True)


def _normalize_symbols(value: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []

    def visit(item: Any, depth: int) -> None:
        if len(output) >= MAX_LSP_RESULTS or depth > 32 or not isinstance(item, dict):
            return
        name = item.get("name")
        kind = item.get("kind")
        location = item.get("location")
        range_value = item.get("range")
        if range_value is None and isinstance(location, dict):
            range_value = location.get("range")
        normalized_range = _range(range_value)
        if isinstance(name, str) and isinstance(kind, int) and normalized_range:
            output.append(
                {
                    "name": name[:1024],
                    "kind": kind,
                    "range": normalized_range,
                    "selection_range": _range(item.get("selectionRange"))
                    or normalized_range,
                    "container_name": (
                        str(item["containerName"])[:1024]
                        if item.get("containerName") is not None
                        else None
                    ),
                }
            )
        children = item.get("children")
        if isinstance(children, list):
            for child in children:
                visit(child, depth + 1)

    if isinstance(value, list):
        for entry in value:
            visit(entry, 0)
    return output


def _normalize_locations(value: Any, repository: Path) -> list[dict[str, Any]]:
    values = value if isinstance(value, list) else [value]
    output: list[dict[str, Any]] = []
    for item in values[:MAX_LSP_RESULTS]:
        if not isinstance(item, dict):
            continue
        uri = item.get("uri") or item.get("targetUri")
        range_value = item.get("range") or item.get("targetSelectionRange")
        path = _relative_uri(uri, repository)
        normalized_range = _range(range_value)
        if path is not None and normalized_range is not None:
            output.append({"path": path, "range": normalized_range})
    return output


def _normalize_hover(value: Any, repository: Path) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    contents = value.get("contents")
    if isinstance(contents, dict):
        text = str(contents.get("value", ""))
    elif isinstance(contents, list):
        text = "\n".join(
            str(item.get("value", "")) if isinstance(item, dict) else str(item)
            for item in contents
        )
    else:
        text = str(contents or "")
    text = text.replace(str(repository), "<workspace>")[:65_536]
    return {"text": text, "range": _range(value.get("range"))}


def _typescript_diagnostic_response(value: Any) -> list[dict[str, Any]] | None:
    if (
        not isinstance(value, dict)
        or value.get("type") != "response"
        or value.get("success") is not True
    ):
        return None
    body = value.get("body", [])
    if body is None:
        body = []
    if not isinstance(body, list):
        return None
    output: list[dict[str, Any]] = []
    severities = {"error": 1, "warning": 2, "message": 3, "suggestion": 4}
    for item in body[:MAX_LSP_RESULTS]:
        if not isinstance(item, dict):
            continue
        start = item.get("startLocation") or item.get("start")
        end = item.get("endLocation") or item.get("end")
        message = item.get("message") or item.get("text")
        category = item.get("category")
        if (
            not isinstance(start, dict)
            or not isinstance(end, dict)
            or not isinstance(message, str)
            or category not in severities
        ):
            continue
        coordinates = (
            start.get("line"),
            start.get("offset"),
            end.get("line"),
            end.get("offset"),
        )
        if any(
            isinstance(coordinate, bool)
            or not isinstance(coordinate, int)
            or coordinate < 1
            for coordinate in coordinates
        ):
            continue
        output.append(
            {
                "range": {
                    "start": {
                        "line": coordinates[0] - 1,
                        "character": coordinates[1] - 1,
                    },
                    "end": {
                        "line": coordinates[2] - 1,
                        "character": coordinates[3] - 1,
                    },
                },
                "severity": severities[category],
                "code": item.get("code"),
                "message": message,
            }
        )
    return output


def _typescript_diagnostic_response_status(value: Any) -> str:
    if not isinstance(value, dict):
        return "invalid_container"
    if value.get("type") in {"noServer", "noContent", "cancelled"}:
        return str(value["type"])
    if value.get("type") != "response":
        return "unexpected_type"
    if value.get("success") is not True:
        return "request_failed"
    body = value.get("body", [])
    if body is not None and not isinstance(body, list):
        return "invalid_body"
    return "accepted"


def _normalize_diagnostics(value: Any, repository: Path) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return output
    for item in value[:MAX_LSP_RESULTS]:
        if not isinstance(item, dict):
            continue
        normalized_range = _range(item.get("range"))
        message = item.get("message")
        if normalized_range is None or not isinstance(message, str):
            continue
        output.append(
            {
                "range": normalized_range,
                "severity": item.get("severity", 3),
                "code": (
                    str(item["code"])[:128]
                    if item.get("code") is not None
                    else None
                ),
                "message": message.replace(str(repository), "<workspace>")[:16_384],
            }
        )
    return output


def _relative_uri(value: Any, repository: Path) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        return None
    try:
        target = Path(unquote(parsed.path)).resolve(strict=True)
        return target.relative_to(repository).as_posix()
    except (OSError, ValueError):
        return None


def _range(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    start = value.get("start")
    end = value.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        return None
    values = (
        start.get("line"),
        start.get("character"),
        end.get("line"),
        end.get("character"),
    )
    if any(
        isinstance(item, bool) or not isinstance(item, int) or item < 0
        for item in values
    ):
        return None
    return {
        "start": {"line": values[0], "character": values[1]},
        "end": {"line": values[2], "character": values[3]},
    }


async def _drain_stderr(reader: asyncio.StreamReader | None) -> bytes:
    if reader is None:
        return b""
    output = bytearray()
    while True:
        chunk = await reader.read(64 * 1024)
        if not chunk:
            break
        if len(output) < 1024 * 1024:
            remaining = 1024 * 1024 - len(output)
            output.extend(chunk[:remaining])
    return bytes(output)


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGINT)
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except TimeoutError:
        process.kill()
        await process.wait()
