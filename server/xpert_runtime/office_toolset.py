from __future__ import annotations

from typing import Any

from .capabilities import CapabilityRegistry
from .client_tool_store import ClientToolStore
from .client_toolset import ClientToolsetProvider
from .toolset import RuntimeTool, RuntimeToolCall, RuntimeToolError


OFFICE_POWERPOINT_TOOL_NAMES = {
    "office_powerpoint_snapshot",
    "office_powerpoint_select_slide",
    "office_powerpoint_add_slide",
    "office_powerpoint_delete_slide",
    "office_powerpoint_add_text_box",
    "office_powerpoint_add_shape",
    "office_powerpoint_update_shape",
    "office_powerpoint_delete_shape",
    "office_powerpoint_insert_image",
}
OFFICE_WORD_TOOL_NAMES = {
    "office_word_snapshot",
    "office_word_insert_text",
    "office_word_replace_selection",
    "office_word_insert_heading",
    "office_word_insert_table",
    "office_word_search_text",
}
OFFICE_EXCEL_TOOL_NAMES = {
    "office_excel_snapshot",
    "office_excel_get_range",
    "office_excel_set_range_values",
    "office_excel_add_worksheet",
    "office_excel_delete_worksheet",
    "office_excel_autofit_range",
    "office_excel_add_table",
}
OFFICE_TOOL_NAMES = (
    OFFICE_POWERPOINT_TOOL_NAMES | OFFICE_WORD_TOOL_NAMES | OFFICE_EXCEL_TOOL_NAMES
)
OFFICE_DELETE_TOOL_NAMES = {
    "office_powerpoint_delete_slide",
    "office_powerpoint_delete_shape",
    "office_excel_delete_worksheet",
}
OFFICE_MUTATING_TOOL_NAMES = OFFICE_TOOL_NAMES - {
    "office_powerpoint_snapshot",
    "office_powerpoint_select_slide",
    "office_word_snapshot",
    "office_word_search_text",
    "office_excel_snapshot",
    "office_excel_get_range",
}

OFFICE_TOOL_REQUIREMENTS: dict[str, list[tuple[str, str]]] = {
    **{
        name: [("WordApi", "1.1")]
        for name in OFFICE_WORD_TOOL_NAMES
    },
    **{
        name: [("ExcelApi", "1.1")]
        for name in OFFICE_EXCEL_TOOL_NAMES
    },
    "office_powerpoint_snapshot": [("PowerPointApi", "1.4")],
    "office_powerpoint_select_slide": [("PowerPointApi", "1.2")],
    "office_powerpoint_add_slide": [("PowerPointApi", "1.3")],
    "office_powerpoint_delete_slide": [("PowerPointApi", "1.3")],
    "office_powerpoint_add_text_box": [("PowerPointApi", "1.4")],
    "office_powerpoint_add_shape": [("PowerPointApi", "1.4")],
    "office_powerpoint_update_shape": [("PowerPointApi", "1.4")],
    "office_powerpoint_delete_shape": [("PowerPointApi", "1.4")],
    "office_powerpoint_insert_image": [
        ("PowerPointApi", "1.2"),
        ("ImageCoercion", "1.1"),
    ],
}


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> RuntimeTool:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return RuntimeTool(
        name=name,
        description=description,
        input_schema=schema,
        provider="office",
        server_id="modelmirror-office-host",
    )


_slide = {"slideIndex": {"type": "integer", "minimum": 1, "maximum": 10000}}
_geometry = {
    "left": {"type": "number", "minimum": 0, "maximum": 10000},
    "top": {"type": "number", "minimum": 0, "maximum": 10000},
    "width": {"type": "number", "minimum": 1, "maximum": 10000},
    "height": {"type": "number", "minimum": 1, "maximum": 10000},
}
_shape_target = {
    **_slide,
    "shapeId": {"type": "string", "maxLength": 200},
    "shapeName": {"type": "string", "maxLength": 300},
}
_style = {
    "name": {"type": "string", "maxLength": 300},
    "text": {"type": "string", "maxLength": 20000},
    "fillColor": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
    "lineColor": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
}
_worksheet = {"worksheetName": {"type": "string", "maxLength": 100}}
_address = {"type": "string", "minLength": 1, "maxLength": 100}
_matrix = {
    "type": "array",
    "maxItems": 1000,
    "items": {
        "type": "array",
        "maxItems": 200,
        "items": {"type": ["string", "number", "boolean", "null"]},
    },
}


OFFICE_TOOLS = [
    _tool(
        "office_powerpoint_snapshot",
        "Inspect the active presentation and return bounded slide and shape metadata.",
        {**_slide, "maxShapes": {"type": "integer", "minimum": 1, "maximum": 200}},
    ),
    _tool(
        "office_powerpoint_select_slide",
        "Select a slide by its one-based index.",
        _slide,
        ["slideIndex"],
    ),
    _tool(
        "office_powerpoint_add_slide",
        "Append a slide to the presentation.",
        {
            "slideMasterId": {"type": "string", "maxLength": 200},
            "layoutId": {"type": "string", "maxLength": 200},
        },
    ),
    _tool(
        "office_powerpoint_delete_slide",
        "Delete a slide after explicit confirmation.",
        {**_slide, "confirm": {"type": "boolean", "const": True}},
        ["slideIndex", "confirm"],
    ),
    _tool(
        "office_powerpoint_add_text_box",
        "Add a bounded text box to a slide.",
        {**_slide, **_geometry, **_style},
        ["text"],
    ),
    _tool(
        "office_powerpoint_add_shape",
        "Add a geometric shape to a slide.",
        {
            **_slide,
            **_geometry,
            **_style,
            "shapeType": {"type": "string", "maxLength": 80},
        },
    ),
    _tool(
        "office_powerpoint_update_shape",
        "Update a shape selected by stable id or name.",
        {**_shape_target, **_geometry, **_style},
    ),
    _tool(
        "office_powerpoint_delete_shape",
        "Delete a shape after explicit confirmation.",
        {**_shape_target, "confirm": {"type": "boolean", "const": True}},
        ["confirm"],
    ),
    _tool(
        "office_powerpoint_insert_image",
        "Insert a scoped runtime artifact image into a slide.",
        {
            **_slide,
            **_geometry,
            "artifact_id": {"type": "string", "minLength": 1, "maxLength": 200},
        },
        ["artifact_id"],
    ),
    _tool(
        "office_word_snapshot",
        "Inspect bounded selection and body text from the active Word document.",
        {"maxCharacters": {"type": "integer", "minimum": 1, "maximum": 20000}},
    ),
    _tool(
        "office_word_insert_text",
        "Insert plain text at the start or end of the Word body.",
        {
            "text": {"type": "string", "maxLength": 20000},
            "location": {"type": "string", "enum": ["Start", "End"]},
        },
        ["text"],
    ),
    _tool(
        "office_word_replace_selection",
        "Replace the current Word selection with plain text.",
        {"text": {"type": "string", "maxLength": 20000}},
        ["text"],
    ),
    _tool(
        "office_word_insert_heading",
        "Insert a heading paragraph into the Word body.",
        {
            "text": {"type": "string", "maxLength": 2000},
            "level": {"type": "integer", "minimum": 1, "maximum": 9},
            "location": {"type": "string", "enum": ["Start", "End"]},
        },
        ["text"],
    ),
    _tool(
        "office_word_insert_table",
        "Insert a bounded table into the Word body.",
        {
            "values": _matrix,
            "rowCount": {"type": "integer", "minimum": 1, "maximum": 200},
            "columnCount": {"type": "integer", "minimum": 1, "maximum": 50},
            "location": {"type": "string", "enum": ["Start", "End"]},
        },
    ),
    _tool(
        "office_word_search_text",
        "Search Word body text and return bounded snippets.",
        {
            "query": {"type": "string", "minLength": 1, "maxLength": 1000},
            "matchCase": {"type": "boolean"},
            "matchWholeWord": {"type": "boolean"},
            "maxResults": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        ["query"],
    ),
    _tool(
        "office_excel_snapshot",
        "Inspect worksheets and a bounded used-range preview.",
        {
            "maxRows": {"type": "integer", "minimum": 1, "maximum": 200},
            "maxColumns": {"type": "integer", "minimum": 1, "maximum": 100},
        },
    ),
    _tool(
        "office_excel_get_range",
        "Read values and formatted text from a bounded Excel range.",
        {**_worksheet, "address": _address},
        ["address"],
    ),
    _tool(
        "office_excel_set_range_values",
        "Write a two-dimensional value matrix to an Excel range.",
        {**_worksheet, "address": _address, "values": _matrix},
        ["address", "values"],
    ),
    _tool(
        "office_excel_add_worksheet",
        "Add a worksheet to the active workbook.",
        {"name": {"type": "string", "maxLength": 100}},
    ),
    _tool(
        "office_excel_delete_worksheet",
        "Delete a worksheet after explicit confirmation.",
        {
            "name": {"type": "string", "minLength": 1, "maxLength": 100},
            "confirm": {"type": "boolean", "const": True},
        },
        ["name", "confirm"],
    ),
    _tool(
        "office_excel_autofit_range",
        "Autofit rows and columns for a range or used range.",
        {**_worksheet, "address": _address},
    ),
    _tool(
        "office_excel_add_table",
        "Create an Excel table from a bounded range.",
        {
            **_worksheet,
            "address": _address,
            "hasHeaders": {"type": "boolean"},
            "name": {"type": "string", "maxLength": 100},
        },
        ["address"],
    ),
]


class OfficeToolsetProvider(ClientToolsetProvider):
    def __init__(self, store: ClientToolStore) -> None:
        super().__init__(
            store,
            tools=OFFICE_TOOLS,
            capability_name="office_tools",
            config_key="office_automation_config",
            host_type="office",
        )

    def _request_for_call(self, call: RuntimeToolCall):
        metadata = dict(call.metadata or {})
        config = dict(metadata.get("office_automation_config") or {})
        host_scope = str(config.get("host") or "all").strip().lower()
        if host_scope not in {"word", "excel", "powerpoint", "all"}:
            raise RuntimeToolError(call.tool_name, "Invalid Office host scope.")
        tool_app = call.tool_name.split("_")[1] if call.tool_name.startswith("office_") else ""
        if host_scope != "all" and tool_app != host_scope:
            raise RuntimeToolError(call.tool_name, "Office tool is outside the configured host scope.")
        if call.tool_name in OFFICE_DELETE_TOOL_NAMES:
            if not bool(config.get("allowDeletes")) or call.arguments.get("confirm") is not True:
                raise RuntimeToolError(call.tool_name, "Delete requires allowDeletes and confirm=true.")
        if call.tool_name == "office_powerpoint_insert_image" and not bool(
            config.get("allowImageInsert")
        ):
            raise RuntimeToolError(call.tool_name, "PowerPoint image insertion is disabled.")
        request = super()._request_for_call(call)
        host = self.store.require_host(request.host_id)
        if host.office_app and tool_app != host.office_app:
            raise RuntimeToolError(
                call.tool_name,
                f"Connected Office host is {host.office_app}, not {tool_app}.",
                code="office_host_app_mismatch",
            )
        return request


def register_office_toolset_capability(
    registry: CapabilityRegistry, provider: OfficeToolsetProvider
) -> None:
    registry.register(
        "office_tools",
        provider,
        description="Durable Word, Excel, and PowerPoint Office.js client tools.",
    )
