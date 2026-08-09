from __future__ import annotations

import asyncio
import gc
import hashlib
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import time
import uuid
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from mcp.types import CallToolResult
import pytest

from server.file_assets import service as service_module
from server.file_assets.contracts import FilePurpose
from server.file_assets.document_parser import (
    LocalDocumentParseError,
    ParsedDocument,
    ParsedSection,
    parse_chat_document,
)
from server.file_assets.office_sidecar import (
    OFFICE_ADAPTER_ID,
    OFFICE_MARKER_NAME,
    OFFICE_MARKER_OWNER,
    OFFICE_RESULT_MAX_BYTES,
    OFFICE_TOOL_NAME,
    OfficeSidecarError,
    OfficeSidecarParser,
)
from server.file_assets.service import FileAssetService, FileAssetServiceError


def _payload(format_id: str = "docx") -> dict[str, Any]:
    section: dict[str, Any] = {
        "text": "安全解析结果",
        "heading_path": ["标题"],
    }
    if format_id == "pptx":
        section = {"text": "幻灯片正文", "slide": 1}
    return {
        "format": format_id,
        "title": "sidecar-must-not-control-title",
        "sections": [section],
        "warnings": [],
        "extracted_chars": len(section["text"]),
        "truncated": False,
    }


def _call_result(
    payload: dict[str, Any] | None = None,
    *,
    is_error: bool = False,
) -> CallToolResult:
    """Match the mcp==1.27 CallToolResult shape used by MCPClientManager."""

    return CallToolResult(
        content=[],
        structuredContent=payload if payload is not None else _payload(),
        isError=is_error,
    )


class _FakeManager:
    def __init__(
        self,
        *,
        result: Any | None = None,
        connect_error: Exception | None = None,
        connect_delay: float = 0,
        call_error: Exception | None = None,
        call_delay: float = 0,
        on_call: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
    ) -> None:
        self.result = result or _call_result()
        self.connect_error = connect_error
        self.connect_delay = connect_delay
        self.call_error = call_error
        self.call_delay = call_delay
        self.on_call = on_call
        self.profiles: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.disconnections: list[str] = []

    async def connect_profile(self, **profile: Any) -> str:
        self.profiles.append(profile)
        if self.connect_delay:
            await asyncio.sleep(self.connect_delay)
        if self.connect_error is not None:
            raise self.connect_error
        return "session-1"

    async def call_tool(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        self.calls.append((session_id, tool_name, arguments))
        if self.on_call is not None:
            self.on_call(self.profiles[-1], arguments)
        if self.call_delay:
            await asyncio.sleep(self.call_delay)
        if self.call_error is not None:
            raise self.call_error
        return self.result

    async def disconnect(self, session_id: str) -> None:
        self.disconnections.append(session_id)


def _write_source(tmp_path: Path, suffix: str = ".blob") -> tuple[Path, bytes]:
    content = b"validated-ooxml-container"
    path = tmp_path / f"private-original-name{suffix}"
    path.write_bytes(content)
    return path, content


def test_bridge_stages_fixed_read_only_name_and_calls_only_opaque_id(
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "mcp-inputs"
    source, content = _write_source(tmp_path)
    observed: dict[str, Any] = {}

    def inspect_staging(profile: dict[str, Any], arguments: dict[str, Any]) -> None:
        workspace_id = profile["environment"]["MCP_FILE_WORKSPACE_ID"]
        workspace = input_root / workspace_id
        staged = workspace / "source.docx"
        marker = json.loads(
            (workspace / OFFICE_MARKER_NAME).read_text(encoding="utf-8")
        )
        observed.update(
            workspace=workspace,
            workspace_mode=stat.S_IMODE(workspace.stat().st_mode),
            source_mode=stat.S_IMODE(staged.stat().st_mode),
            marker_mode=stat.S_IMODE(
                (workspace / OFFICE_MARKER_NAME).stat().st_mode
            ),
            entries=sorted(path.name for path in workspace.iterdir()),
            source_bytes=staged.read_bytes(),
            marker=marker,
            arguments=dict(arguments),
        )

    manager = _FakeManager(on_call=inspect_staging)
    parser = OfficeSidecarParser(
        input_root=input_root,
        manager_factory=lambda: manager,
    )
    parsed = parser.parse(source, format_id="docx", title="展示名称.docx")

    assert parsed.title == "展示名称.docx"
    assert parsed.format == "docx"
    assert parsed.sections[0].heading_path == ("标题",)
    assert observed["entries"] == [OFFICE_MARKER_NAME, "source.docx"]
    assert observed["source_bytes"] == content
    assert observed["workspace_mode"] == 0o555
    assert observed["source_mode"] == 0o444
    assert observed["marker_mode"] == 0o444
    assert observed["marker"]["owner"] == OFFICE_MARKER_OWNER
    assert observed["marker"]["source_name"] == "source.docx"
    assert observed["marker"]["format_id"] == "docx"
    assert observed["marker"]["source_sha256"] == hashlib.sha256(content).hexdigest()

    profile = manager.profiles[0]
    workspace_id = profile["environment"]["MCP_FILE_WORKSPACE_ID"]
    expected_file_id = "mcpf_" + hashlib.sha256(
        f"{workspace_id}:source.docx".encode("utf-8")
    ).hexdigest()[:24]
    assert re.fullmatch(r"mcpws_[0-9a-f]{32}", workspace_id)
    assert profile["server_command"][-1] == OFFICE_ADAPTER_ID
    assert profile["network_policy"] == "catalog-files-none"
    assert profile["reconnect_attempts"] == 0
    assert profile["operation_timeout"] == 30
    assert manager.calls == [
        ("session-1", OFFICE_TOOL_NAME, {"file_id": expected_file_id})
    ]
    assert manager.disconnections == ["session-1"]
    assert source.name not in json.dumps(
        {"profile": profile, "call": manager.calls}, ensure_ascii=False
    )
    assert not observed["workspace"].exists()


def test_staged_workspace_is_readable_by_runtime_uid_65532(tmp_path: Path) -> None:
    if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("requires a root POSIX test container")

    input_root = Path("/tmp") / f"modelmirror-office-{uuid.uuid4().hex}"
    source, _ = _write_source(tmp_path)

    def verify_as_runtime_uid(
        profile: dict[str, Any], arguments: dict[str, Any]
    ) -> None:
        del arguments
        workspace_id = profile["environment"]["MCP_FILE_WORKSPACE_ID"]
        workspace = input_root / workspace_id
        script = (
            "from pathlib import Path; import sys; "
            "assert Path(sys.argv[1]).read_bytes(); "
            "assert Path(sys.argv[2]).read_bytes()"
        )

        def drop_privileges() -> None:
            os.setgid(65532)
            os.setuid(65532)

        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(workspace / "source.docx"),
                str(workspace / OFFICE_MARKER_NAME),
            ],
            check=False,
            capture_output=True,
            timeout=5,
            preexec_fn=drop_privileges,
        )
        assert completed.returncode == 0, completed.stderr.decode(
            "utf-8", errors="replace"
        )

    manager = _FakeManager(on_call=verify_as_runtime_uid)
    parser = OfficeSidecarParser(
        input_root=input_root,
        manager_factory=lambda: manager,
    )
    os.chmod(input_root, 0o755)
    try:
        parser.parse(source, format_id="docx", title=None)
    finally:
        input_root.rmdir()


def test_pptx_uses_fixed_suffix_and_slide_contract(tmp_path: Path) -> None:
    source, _ = _write_source(tmp_path, ".binary")
    input_root = tmp_path / "mcp-inputs"
    observed: list[str] = []

    def inspect_staging(profile: dict[str, Any], arguments: dict[str, Any]) -> None:
        del arguments
        workspace = input_root / profile["environment"]["MCP_FILE_WORKSPACE_ID"]
        observed.extend(sorted(path.name for path in workspace.iterdir()))

    manager = _FakeManager(
        result=_call_result(_payload("pptx")),
        on_call=inspect_staging,
    )
    parsed = OfficeSidecarParser(
        input_root=input_root,
        manager_factory=lambda: manager,
    ).parse(source, format_id="pptx", title="deck.pptx")

    assert observed == [OFFICE_MARKER_NAME, "source.pptx"]
    assert parsed.sections == (ParsedSection(text="幻灯片正文", slide=1),)


@pytest.mark.parametrize(
    ("result", "expected_code"),
    [
        (
            SimpleNamespace(isError=False, structuredContent=None),
            "office_parser_invalid_output",
        ),
        (
            SimpleNamespace(
                isError=False,
                structuredContent={**_payload(), "unexpected": True},
            ),
            "office_parser_invalid_output",
        ),
        (
            SimpleNamespace(
                isError=False,
                structuredContent={
                    **_payload(),
                    "sections": [{"text": "内容", "path": "secret"}],
                },
            ),
            "office_parser_invalid_output",
        ),
        (
            SimpleNamespace(
                isError=False,
                structuredContent={**_payload(), "format": "pptx"},
            ),
            "office_parser_invalid_output",
        ),
        (
            SimpleNamespace(isError=True, structuredContent=_payload()),
            "office_parse_failed",
        ),
    ],
)
def test_bridge_rejects_non_contract_structured_content(
    tmp_path: Path,
    result: Any,
    expected_code: str,
) -> None:
    source, _ = _write_source(tmp_path)
    manager = _FakeManager(result=result)
    parser = OfficeSidecarParser(
        input_root=tmp_path / "mcp-inputs",
        manager_factory=lambda: manager,
    )

    with pytest.raises(OfficeSidecarError) as captured:
        parser.parse(source, format_id="docx", title=None)

    assert captured.value.status_code == 422
    assert captured.value.error_code == expected_code
    assert manager.disconnections == ["session-1"]
    assert not tuple((tmp_path / "mcp-inputs").glob("mcpws_*"))


def test_connect_and_call_share_one_operation_deadline(tmp_path: Path) -> None:
    source, _ = _write_source(tmp_path)
    manager = _FakeManager(connect_delay=0.03, call_delay=0.03)
    parser = OfficeSidecarParser(
        input_root=tmp_path / "mcp-inputs",
        manager_factory=lambda: manager,
        operation_timeout=0.05,
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        started = time.perf_counter()
        with pytest.raises(OfficeSidecarError) as captured:
            parser.parse(source, format_id="docx", title=None)
        elapsed = time.perf_counter() - started
        gc.collect()

    assert captured.value.status_code == 503
    assert captured.value.error_code == "office_parser_timeout"
    assert manager.profiles[0]["reconnect_attempts"] == 0
    assert len(manager.calls) == 1
    assert manager.disconnections == []
    assert elapsed < 0.2
    assert not tuple((tmp_path / "mcp-inputs").glob("mcpws_*"))


def test_bridge_rejects_structured_content_over_two_mib(tmp_path: Path) -> None:
    source, _ = _write_source(tmp_path)
    payload = _payload()
    payload["warnings"] = ["x" * (OFFICE_RESULT_MAX_BYTES + 1)]
    manager = _FakeManager(
        result=SimpleNamespace(isError=False, structuredContent=payload)
    )
    parser = OfficeSidecarParser(
        input_root=tmp_path / "mcp-inputs",
        manager_factory=lambda: manager,
    )

    with pytest.raises(OfficeSidecarError) as captured:
        parser.parse(source, format_id="docx", title=None)

    assert captured.value.status_code == 422
    assert captured.value.error_code == "office_parser_output_too_large"
    assert manager.disconnections == ["session-1"]


def test_sidecar_unavailable_is_redacted_503_and_workspace_is_cleaned(
    tmp_path: Path,
) -> None:
    source, _ = _write_source(tmp_path)
    manager = _FakeManager(connect_error=RuntimeError("private socket path"))
    parser = OfficeSidecarParser(
        input_root=tmp_path / "mcp-inputs",
        manager_factory=lambda: manager,
    )

    with pytest.raises(OfficeSidecarError) as captured:
        parser.parse(source, format_id="docx", title="private.docx")

    assert captured.value.status_code == 503
    assert captured.value.error_code == "office_parser_unavailable"
    assert "private" not in captured.value.message
    assert manager.profiles[0]["reconnect_attempts"] == 0
    assert manager.calls == []
    assert manager.disconnections == []
    assert not tuple((tmp_path / "mcp-inputs").glob("mcpws_*"))


def test_timeout_disconnects_without_retry_and_cleans_workspace(tmp_path: Path) -> None:
    source, _ = _write_source(tmp_path)
    manager = _FakeManager(call_delay=1)
    parser = OfficeSidecarParser(
        input_root=tmp_path / "mcp-inputs",
        manager_factory=lambda: manager,
        operation_timeout=0.01,
    )

    with pytest.raises(OfficeSidecarError) as captured:
        parser.parse(source, format_id="docx", title=None)

    assert captured.value.status_code == 503
    assert captured.value.error_code == "office_parser_timeout"
    assert manager.profiles[0]["reconnect_attempts"] == 0
    assert len(manager.calls) == 1
    assert manager.disconnections == []
    assert not tuple((tmp_path / "mcp-inputs").glob("mcpws_*"))


def test_call_failure_disconnects_without_retry_and_cleans_workspace(
    tmp_path: Path,
) -> None:
    source, _ = _write_source(tmp_path)
    manager = _FakeManager(call_error=RuntimeError("sidecar internal path"))
    parser = OfficeSidecarParser(
        input_root=tmp_path / "mcp-inputs",
        manager_factory=lambda: manager,
    )

    with pytest.raises(OfficeSidecarError) as captured:
        parser.parse(source, format_id="docx", title=None)

    assert captured.value.status_code == 503
    assert captured.value.error_code == "office_parser_unavailable"
    assert len(manager.calls) == 1
    assert manager.disconnections == ["session-1"]
    assert not tuple((tmp_path / "mcp-inputs").glob("mcpws_*"))


def _write_marker(workspace: Path, *, owner: str, created_at: float) -> None:
    workspace.mkdir(parents=True)
    marker = workspace / OFFICE_MARKER_NAME
    marker.write_text(
        json.dumps(
            {
                "owner": owner,
                "workspace_id": workspace.name,
                "created_at": created_at,
            }
        ),
        encoding="utf-8",
    )
    os.utime(marker, (created_at, created_at))


def test_orphan_cleanup_only_removes_old_owned_workspace(tmp_path: Path) -> None:
    now = time.time()
    root = tmp_path / "mcp-inputs"
    root.mkdir()
    old_owned = root / ("mcpws_" + "1" * 32)
    fresh_owned = root / ("mcpws_" + "2" * 32)
    wrong_owner = root / ("mcpws_" + "3" * 32)
    no_marker = root / ("mcpws_" + "4" * 32)
    unrelated = root / "catalog-workspace"
    _write_marker(
        old_owned,
        owner=OFFICE_MARKER_OWNER,
        created_at=now - 1_901,
    )
    _write_marker(
        fresh_owned,
        owner=OFFICE_MARKER_OWNER,
        created_at=now,
    )
    _write_marker(
        wrong_owner,
        owner="another-subsystem",
        created_at=now - 1_901,
    )
    no_marker.mkdir()
    unrelated.mkdir()

    parser = OfficeSidecarParser(input_root=root, now=lambda: now)

    assert not old_owned.exists()
    assert fresh_owned.exists()
    assert wrong_owner.exists()
    assert no_marker.exists()
    assert unrelated.exists()
    assert parser.cleanup_orphans() == ()


def test_staging_failure_removes_new_unmarked_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _ = _write_source(tmp_path)
    input_root = tmp_path / "mcp-inputs"
    parser = OfficeSidecarParser(input_root=input_root)

    def fail_atomic_write(path: Path, content: bytes) -> None:
        del path, content
        raise OSError("simulated write interruption")

    monkeypatch.setattr(parser, "_atomic_write", fail_atomic_write)
    with pytest.raises(OfficeSidecarError) as captured:
        parser.parse(source, format_id="docx", title=None)

    assert captured.value.error_code == "office_parser_staging_failed"
    assert not tuple(input_root.glob("mcpws_*"))


def test_parse_chat_document_uses_injected_bridge_and_preserves_503(
    tmp_path: Path,
) -> None:
    path, _ = _write_source(tmp_path)
    expected = ParsedDocument(
        format="docx",
        title="display.docx",
        sections=(ParsedSection(text="正文"),),
        extracted_chars=2,
    )

    class SuccessfulParser:
        def parse(self, source: Path, *, format_id: str, title: str | None) -> Any:
            assert source == path
            assert format_id == "docx"
            assert title == "display.docx"
            return expected

    assert (
        parse_chat_document(
            path,
            format_id="docx",
            title="display.docx",
            office_parser=SuccessfulParser(),
        )
        == expected
    )

    class UnavailableParser:
        def parse(self, source: Path, *, format_id: str, title: str | None) -> Any:
            del source, format_id, title
            raise OfficeSidecarError(
                503,
                "office_parser_unavailable",
                "Office 隔离解析暂不可用，请稍后重试。",
            )

    with pytest.raises(LocalDocumentParseError) as captured:
        parse_chat_document(
            path,
            format_id="docx",
            title="display.docx",
            office_parser=UnavailableParser(),
        )
    assert captured.value.status_code == 503
    assert captured.value.error_code == "office_parser_unavailable"


def test_file_asset_service_preserves_bridge_503(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_FILE_INPUT_ENABLED", "true")
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "native")
    service = FileAssetService(storage_dir=tmp_path / "assets", mode="native")
    uploaded = service.upload(
        io.BytesIO(b"safe text"),
        purpose=FilePurpose.CHAT,
        scope_id="chat-session",
        filename="notes.txt",
        declared_media_type="text/plain",
    )

    def unavailable(*args: Any, **kwargs: Any) -> ParsedDocument:
        del args, kwargs
        raise LocalDocumentParseError(
            "office_parser_unavailable",
            "Office 隔离解析暂不可用，请稍后重试。",
            status_code=503,
        )

    monkeypatch.setattr(service_module, "parse_chat_document", unavailable)
    with pytest.raises(FileAssetServiceError) as captured:
        service.parse_asset(
            uploaded.asset_id,
            purpose=FilePurpose.CHAT,
            scope_id="chat-session",
        )
    assert captured.value.status_code == 503
    assert captured.value.error_code == "office_parser_unavailable"


def test_bridge_can_run_from_an_existing_asyncio_loop(tmp_path: Path) -> None:
    source, _ = _write_source(tmp_path)
    manager = _FakeManager()
    parser = OfficeSidecarParser(
        input_root=tmp_path / "mcp-inputs",
        manager_factory=lambda: manager,
    )

    async def invoke() -> ParsedDocument:
        return parser.parse(source, format_id="docx", title=None)

    parsed = asyncio.run(invoke())
    assert parsed.format == "docx"
    assert manager.disconnections == ["session-1"]


@pytest.mark.skipif(
    os.getenv("MODELMIRROR_TEST_OFFICE_BRIDGE") != "1",
    reason="requires the dedicated network-free mcp-files sidecar",
)
def test_real_socket_landlock_bridge_for_docx_pptx_and_cleanup() -> None:
    """Exercise API bridge -> file_proxy -> Unix socket -> Landlock worker."""

    assert importlib.util.find_spec("docx") is None
    assert importlib.util.find_spec("pptx") is None
    fixture_root = Path(os.environ["MODELMIRROR_OFFICE_FIXTURE_ROOT"])
    input_root = Path(os.environ["MCP_FILE_INPUT_ROOT"])
    parser = OfficeSidecarParser(input_root=input_root)

    docx = parser.parse(
        fixture_root / "golden.docx",
        format_id="docx",
        title="display.docx",
    )
    assert docx.title == "display.docx"
    docx_text = "\n".join(section.text for section in docx.sections)
    assert "Overview" in docx_text
    assert "the source [link target: https://example.com/report]" in docx_text
    assert "[image]" in docx_text

    pptx = parser.parse(
        fixture_root / "golden.pptx",
        format_id="pptx",
        title="display.pptx",
    )
    assert pptx.title == "display.pptx"
    assert [section.slide for section in pptx.sections] == [1, 2]
    assert "[speaker notes]\nConfirm launch checklist." in pptx.sections[0].text
    assert "[image]" in pptx.sections[0].text

    with pytest.raises(OfficeSidecarError) as malformed:
        parser.parse(
            fixture_root / "broken.docx",
            format_id="docx",
            title="broken.docx",
        )
    assert malformed.value.status_code == 422
    assert malformed.value.error_code == "office_parse_failed"

    timeout_parser = OfficeSidecarParser(
        input_root=input_root,
        operation_timeout=0.01,
    )
    with pytest.raises(OfficeSidecarError) as timed_out:
        timeout_parser.parse(
            fixture_root / "golden.pptx",
            format_id="pptx",
            title=None,
        )
    assert timed_out.value.status_code == 503
    assert timed_out.value.error_code == "office_parser_timeout"
    assert not tuple(input_root.glob("mcpws_*"))
