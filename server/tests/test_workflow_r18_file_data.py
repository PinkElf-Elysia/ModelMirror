from __future__ import annotations

from datetime import UTC, datetime
import json
from types import SimpleNamespace

import httpx
import pytest

from server import main as main_module
from server.file_assets.contracts import FilePurpose
from server.file_assets.output_service import FileOutputService
from server.file_assets.output_renderer import FileOutputRenderer
from server.file_assets.service import FileAssetService
from server.workflow_native.file_data import (
    WorkflowFileDataError,
    build_file_output_render_spec,
    execute_object_transform,
    execute_time_v2,
    object_transform_variable_references,
    safe_file_output_variable,
    validate_file_output_config,
    validate_object_transform_config,
    validate_time_v2_config,
)


def object_config(operations: list[dict[str, object]]) -> dict[str, object]:
    return {
        "inputVariable": "source",
        "outputVariable": "result",
        "operations": operations,
    }


def time_config(operation: str, **values: object) -> dict[str, object]:
    return {
        "contractVersion": 2,
        "operation": operation,
        "timezone": "UTC",
        "outputVariable": "time_result",
        **values,
    }


def file_config(format_id: str, **values: object) -> dict[str, object]:
    return {
        "inputVariable": "content",
        "outputVariable": "output_file",
        "format": format_id,
        "filenameTemplate": "report",
        "titleTemplate": "",
        "columns": [],
        **values,
    }


def test_object_transform_applies_ordered_typed_steps_without_mutating_input() -> None:
    source = {"id": 7, "name": "Ada", "obsolete": True}
    config = object_config(
        [
            {
                "id": "step_1",
                "operation": "set",
                "targetField": "team",
                "binding": {"source": "variable", "variable": "selected_team"},
            },
            {
                "id": "step_2",
                "operation": "rename",
                "sourceField": "name",
                "targetField": "display_name",
            },
            {"id": "step_3", "operation": "remove", "targetField": "obsolete"},
            {
                "id": "step_4",
                "operation": "set_default",
                "targetField": "id",
                "binding": {"source": "literal", "valueType": "number", "value": 99},
            },
            {
                "id": "step_5",
                "operation": "keep_only",
                "fields": ["id", "display_name", "team"],
            },
        ]
    )

    assert object_transform_variable_references(config) == {"source", "selected_team"}
    assert execute_object_transform(
        source,
        config=config,
        variables={"selected_team": "platform"},
    ) == {"id": 7, "display_name": "Ada", "team": "platform"}
    assert source == {"id": 7, "name": "Ada", "obsolete": True}


@pytest.mark.parametrize(
    ("operations", "code"),
    [
        (
            [
                {"id": "same", "operation": "remove", "targetField": "a"},
                {"id": "same", "operation": "remove", "targetField": "b"},
            ],
            "OBJECT_OPERATION_ID_DUPLICATE",
        ),
        (
            [{"id": "step", "operation": "keep_only", "fields": ["a", "a"]}],
            "OBJECT_KEEP_FIELD_DUPLICATE",
        ),
    ],
)
def test_object_transform_rejects_ambiguous_contracts(
    operations: list[dict[str, object]], code: str
) -> None:
    with pytest.raises(WorkflowFileDataError, match=code):
        validate_object_transform_config(object_config(operations))


def test_object_transform_fails_on_missing_fields_and_rename_conflicts() -> None:
    with pytest.raises(WorkflowFileDataError, match="OBJECT_SOURCE_FIELD_MISSING"):
        execute_object_transform(
            {"a": 1},
            config=object_config(
                [{"id": "step", "operation": "rename", "sourceField": "missing", "targetField": "b"}]
            ),
            variables={},
        )
    with pytest.raises(WorkflowFileDataError, match="OBJECT_RENAME_CONFLICT"):
        execute_object_transform(
            {"a": 1, "b": 2},
            config=object_config(
                [{"id": "step", "operation": "rename", "sourceField": "a", "targetField": "b"}]
            ),
            variables={},
        )


def test_time_v2_uses_injected_clock_and_strict_timezone_arithmetic() -> None:
    assert execute_time_v2(
        time_config("now", timezone="Asia/Shanghai"),
        variables={},
        now=lambda: datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    ) == "2026-01-02T11:04:05+08:00"
    assert execute_time_v2(
        time_config("add", inputVariable="when", amount=1, unit="months"),
        variables={"when": "2025-01-31T12:00:00+00:00"},
    ) == "2025-02-28T12:00:00+00:00"
    assert execute_time_v2(
        time_config(
            "difference",
            inputVariable="left",
            rightVariable="right",
            unit="hours",
        ),
        variables={
            "left": "2025-01-02T12:00:00Z",
            "right": "2025-01-01T06:00:00Z",
        },
    ) == 30


def test_time_v2_rejects_dst_gap_fold_and_fractional_calendar_units() -> None:
    gap = time_config("to_iso", timezone="America/New_York", inputVariable="when")
    with pytest.raises(WorkflowFileDataError, match="TIME_DST_GAP"):
        execute_time_v2(gap, variables={"when": "2025-03-09T02:30:00"})
    with pytest.raises(WorkflowFileDataError, match="TIME_DST_FOLD"):
        execute_time_v2(gap, variables={"when": "2025-11-02T01:30:00"})
    with pytest.raises(WorkflowFileDataError, match="TIME_CALENDAR_AMOUNT_INVALID"):
        validate_time_v2_config(
            time_config("add", inputVariable="when", amount=1.5, unit="months")
        )


def test_file_output_builds_all_seven_formats_and_safe_metadata() -> None:
    scalar = {"title": "Quarterly", "count": 2}
    table = [{"name": "alpha", "score": 3}, {"name": "beta", "score": None}]
    columns = [
        {"id": "column_1", "field": "name", "label": "Name"},
        {"id": "column_2", "field": "score", "label": "Score"},
    ]
    for format_id in ("plain_text", "markdown", "json", "pdf", "docx"):
        spec = build_file_output_render_spec(
            file_config(format_id),
            value=scalar,
            rendered_filename="quarterly-report",
            rendered_title="Quarterly report",
        )
        assert spec["filename"].endswith(
            {"plain_text": ".txt", "markdown": ".md", "json": ".json", "pdf": ".pdf", "docx": ".docx"}[format_id]
        )
    csv_spec = build_file_output_render_spec(
        file_config("csv", columns=columns),
        value=table,
        rendered_filename="scores",
    )
    xlsx_spec = build_file_output_render_spec(
        file_config("xlsx", columns=columns),
        value=table,
        rendered_filename="scores",
    )
    assert csv_spec["rows"] == [["Name", "Score"], ["alpha", 3], ["beta", None]]
    assert xlsx_spec["sheets"][0]["rows"] == csv_spec["rows"]
    metadata = safe_file_output_variable(
        {
            "output_id": "output_1",
            "asset_id": "asset_1",
            "display_name": "scores.xlsx",
            "format": "xlsx",
            "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "byte_size": 100,
            "status": "completed",
            "scope_id": "must-not-leak",
            "producer_artifact_id": "must-not-leak",
        }
    )
    assert metadata["displayName"] == "scores.xlsx"
    assert "scope_id" not in metadata
    assert "producer_artifact_id" not in metadata


@pytest.mark.parametrize("name", ["../secret", "folder/report", r"folder\\report", "C:report"])
def test_file_output_rejects_path_like_rendered_names(name: str) -> None:
    with pytest.raises(WorkflowFileDataError, match="FILE_OUTPUT_FILENAME_UNSAFE"):
        build_file_output_render_spec(
            file_config("json"),
            value={"safe": True},
            rendered_filename=name,
        )


def test_file_output_rejects_nested_cells_and_duplicate_columns() -> None:
    columns = [
        {"id": "column_1", "field": "value", "label": "Value"},
        {"id": "column_2", "field": "value", "label": "Again"},
    ]
    with pytest.raises(WorkflowFileDataError, match="COLUMN_FIELD_DUPLICATE"):
        validate_file_output_config(file_config("csv", columns=columns))
    with pytest.raises(WorkflowFileDataError, match="TABLE_CELL_INVALID"):
        build_file_output_render_spec(
            file_config("xlsx", columns=columns[:1]),
            value=[{"value": {"nested": True}}],
            rendered_filename="nested",
        )


def _events(response: httpx.Response) -> list[dict[str, object]]:
    return [
        json.loads(line[5:].strip())
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "node_data", "input_value", "expected"),
    [
        (
            "object_transform",
            {
                "kind": "object_transform",
                "inputVariable": "source",
                "outputVariable": "result",
                "operations": [
                    {
                        "id": "step_1",
                        "operation": "rename",
                        "sourceField": "name",
                        "targetField": "display_name",
                    },
                    {
                        "id": "step_2",
                        "operation": "set",
                        "targetField": "active",
                        "binding": {
                            "source": "literal",
                            "valueType": "boolean",
                            "value": True,
                        },
                    },
                ],
            },
            {"name": "Ada", "id": 7},
            {"display_name": "Ada", "id": 7, "active": True},
        ),
        (
            "list_operation",
            {
                "kind": "list_operation",
                "inputVariable": "source",
                "outputVariable": "result",
                "operator": "slice",
                "startIndex": 1,
                "endIndex": 3,
            },
            ["zero", "one", "two", "three"],
            ["one", "two"],
        ),
        (
            "time_tool",
            {
                "kind": "time_tool",
                "contractVersion": 2,
                "operation": "to_iso",
                "timezone": "Asia/Shanghai",
                "inputVariable": "source",
                "outputVariable": "result",
            },
            "2026-01-02T03:04:05Z",
            "2026-01-02T11:04:05+08:00",
        ),
    ],
)
async def test_r18_typed_nodes_run_through_the_classic_workflow_stream(
    kind: str,
    node_data: dict[str, object],
    input_value: object,
    expected: object,
) -> None:
    workflow = {
        "id": f"r18-{kind}-runtime",
        "title": f"R1.8 {kind}",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "source"},
            },
            {"id": "operation", "type": kind, "data": node_data},
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "result"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "operation"},
            {"id": "e2", "source": "operation", "target": "output"},
        ],
    }
    response = await main_module._run_workflow_response(
        main_module.WorkflowRunRequest(
            workflow=workflow,
            inputs={"source": input_value},
        ),
        None,
    )
    end = await main_module.consume_workflow_stream(response)
    final_output = end["final_output"]
    if isinstance(expected, (dict, list)):
        assert json.loads(str(final_output)) == expected
    else:
        assert final_output == expected


@pytest.mark.asyncio
async def test_file_output_runner_emits_only_safe_projection_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class StubOutputService:
        def render_spec(self, specification: dict[str, object], **context: object):
            calls.append({"specification": specification, **context})
            payload = {
                "output_id": "output_safe",
                "asset_id": "file_safe",
                "purpose": "workflow",
                "scope_id": "workflow:must-not-leak",
                "producer_kind": "workflow_node",
                "producer_artifact_id": "must-not-leak",
                "display_name": "report.md",
                "format": "markdown",
                "media_type": "text/markdown",
                "byte_size": 12,
                "preview_kind": "text",
                "status": "completed",
                "expires_at": None,
                "warnings": [],
                "source_run_id": str(context["source_run_id"]),
                "source_node_id": "file",
            }
            return SimpleNamespace(
                status="completed",
                display_name="report.md",
                byte_size=12,
                model_dump=lambda **_: payload,
            )

    monkeypatch.setattr(main_module, "get_file_output_service", lambda: StubOutputService())
    workflow = {
        "id": "r18-file-run",
        "title": "R1.8 file output",
        "nodes": [
            {"id": "input", "type": "input", "data": {"kind": "input", "variableName": "report_content"}},
            {
                "id": "file",
                "type": "file_output",
                "data": {
                    "kind": "file_output",
                    "inputVariable": "report_content",
                    "outputVariable": "generated_file",
                    "format": "markdown",
                    "filenameTemplate": "report",
                    "titleTemplate": "",
                    "columns": [],
                },
            },
            {"id": "output", "type": "output", "data": {"kind": "output", "outputVariable": "generated_file"}},
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "file"},
            {"id": "e2", "source": "file", "target": "output"},
        ],
    }
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {"report_content": "# Safe report"}},
        )

    assert response.status_code == 200, response.text
    events = _events(response)
    output_event = next(event for event in events if event.get("event") == "output_file")
    assert output_event["display_name"] == "report.md"
    assert "scope_id" not in output_event
    assert "producer_artifact_id" not in output_event
    assert "content" not in output_event
    assert "body" not in output_event
    end = next(event for event in events if event.get("event") == "workflow_end")
    result = json.loads(str(end["final_output"]))
    assert result["displayName"] == "report.md"
    assert "scope_id" not in result
    assert len(calls) == 1
    assert str(calls[0]["producer_artifact_id"]).endswith(":file")


@pytest.mark.asyncio
async def test_goal_and_handoff_runtime_cannot_reuse_private_xpert_file_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_calls: list[str] = []

    class ForbiddenOutputService:
        def render_spec(self, *_args: object, **_kwargs: object):
            service_calls.append("file_output")
            raise AssertionError("file output service must not be reached")

    class ForbiddenContextStore:
        def get_file(self, *_args: object, **_kwargs: object):
            service_calls.append("document_extractor")
            raise AssertionError("document context store must not be reached")

    def forbidden_legacy_reader(_path: str) -> str:
        service_calls.append("legacy_document")
        raise AssertionError("legacy document reader must not be reached")

    monkeypatch.setattr(main_module, "get_file_output_service", lambda: ForbiddenOutputService())
    monkeypatch.setattr(main_module, "xpert_context_store", ForbiddenContextStore())
    monkeypatch.setattr(main_module, "read_legacy_workflow_document", forbidden_legacy_reader)
    monkeypatch.setattr(main_module, "WORKFLOW_FILE_ASSETS_ENABLED", True)

    cases = [
        (
            "file_output",
            {
                "kind": "file_output",
                "inputVariable": "user_input",
                "outputVariable": "generated_file",
                "format": "markdown",
                "filenameTemplate": "report",
                "titleTemplate": "",
                "columns": [],
            },
            "generated_file",
            {"goal_id": "goal_1"},
        ),
        (
            "document_extractor",
            {
                "kind": "document_extractor",
                "assetIdVariable": "user_input",
                "outputVariable": "document_text",
            },
            "document_text",
            {"handoff_id": "handoff_1"},
        ),
        (
            "document_extractor",
            {
                "kind": "document_extractor",
                "sourcePathVariable": "user_input",
                "outputVariable": "document_text",
            },
            "document_text",
            {},
        ),
    ]
    for kind, data, output_variable, marker in cases:
        workflow = {
            "id": f"r18-{kind}-forbidden",
            "title": "Forbidden file runtime",
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {"kind": "input", "variableName": "user_input"},
                },
                {"id": "file-node", "type": kind, "data": data},
                {
                    "id": "output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": output_variable},
                },
            ],
            "edges": [
                {"id": "e1", "source": "input", "target": "file-node"},
                {"id": "e2", "source": "file-node", "target": "output"},
            ],
        }
        response = await main_module._run_workflow_response(
            main_module.WorkflowRunRequest(
                workflow=workflow,
                inputs={"user_input": "file_shared"},
            ),
            None,
            runtime_run_type="xpert",
            runtime_metadata={
                "xpert_id": "xpert_1",
                "conversation_id": "conversation_1",
                "file_asset_ids": ["file_shared"],
                "file_owner_xpert_id": "xpert_1",
                "file_conversation_id": "conversation_1",
                **marker,
            },
        )
        with pytest.raises(main_module.WorkflowStreamFailure):
            await main_module.consume_workflow_stream(response)

    assert service_calls == []


def test_private_xpert_document_requires_explicit_share_and_scoped_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    class StubContextStore:
        def get_file(self, owner: str, asset_id: str, *, conversation_id: str, include_archived: bool):
            calls.append((owner, asset_id, conversation_id))
            assert include_archived is True
            return SimpleNamespace(filename="notes.txt")

        @staticmethod
        def read_file_text(_asset: object) -> str:
            return "scoped facts"

    monkeypatch.setattr(main_module, "xpert_context_store", StubContextStore())
    metadata = {
        "file_asset_ids": ["file_shared"],
        "file_owner_xpert_id": "xpert_owner",
        "file_conversation_id": "conversation_1",
    }
    text = main_module.resolve_private_xpert_document_text(
        node_id="document",
        asset_id="file_shared",
        runtime_metadata=metadata,
    )
    assert calls == [("xpert_owner", "file_shared", "conversation_1")]
    assert "不可信的用户数据" in text
    assert "scoped facts" in text

    with pytest.raises(main_module.WorkflowDocumentFatalError, match="未显式共享"):
        main_module.resolve_private_xpert_document_text(
            node_id="document",
            asset_id="file_other",
            runtime_metadata=metadata,
        )
    assert calls == [("xpert_owner", "file_shared", "conversation_1")]


def test_output_service_validates_all_r18_specs_and_reuses_producer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "native")
    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "true")
    file_service = FileAssetService(storage_dir=tmp_path, mode="native")
    class StubIsolatedRenderer:
        @staticmethod
        def render(spec):
            if spec.format_id == "pdf":
                return b"%PDF-1.7\n%%EOF\n", ()
            return b"PK\x03\x04r18-safe-archive", ()

    service = FileOutputService(
        file_service,
        renderer=FileOutputRenderer(sidecar=StubIsolatedRenderer()),
    )
    table = [{"name": "alpha", "score": 3}, {"name": "beta", "score": None}]
    columns = [
        {"id": "column_1", "field": "name", "label": "Name"},
        {"id": "column_2", "field": "score", "label": "Score"},
    ]
    published = []
    for index, format_id in enumerate(
        ("plain_text", "markdown", "json", "csv", "pdf", "docx", "xlsx")
    ):
        value = table if format_id in {"csv", "xlsx"} else {"safe": True, "index": index}
        config = file_config(format_id, columns=columns if format_id in {"csv", "xlsx"} else [])
        spec = build_file_output_render_spec(
            config,
            value=value,
            rendered_filename=f"r18-{format_id}",
            rendered_title="R1.8 report",
        )
        result = service.render_spec(
            spec,
            purpose=FilePurpose.WORKFLOW,
            scope_id="workflow:r18-real-render",
            producer_kind="workflow_node",
            producer_artifact_id=f"run-1:node-{index}",
            source_run_id="run-1",
            source_message_id=f"run-{index}",
            source_node_id=f"node-{index}",
        )
        assert result.status == "completed"
        assert result.asset_id
        published.append(result)

    duplicate = service.render_spec(
        build_file_output_render_spec(
            file_config("plain_text"),
            value="ignored because producer is stable",
            rendered_filename="different-name",
        ),
        purpose=FilePurpose.WORKFLOW,
        scope_id="workflow:r18-real-render",
        producer_kind="workflow_node",
        producer_artifact_id="run-1:node-0",
        source_run_id="run-1",
        source_message_id="run-0",
        source_node_id="node-0",
    )
    assert duplicate.output_id == published[0].output_id
    assert len(file_service.blob_store.list_storage_keys()) == 7
