from __future__ import annotations

import pytest

from server.toolsets.api_compiler import (
    compare_toolsets,
    compile_odata,
    compile_openapi,
)
from server.toolsets.store import ToolsetValidationError


OPENAPI_DOCUMENT = {
    "openapi": "3.1.0",
    "servers": [{"url": "https://api.example.test/v1"}],
    "paths": {
        "/items/{item_id}": {
            "get": {
                "operationId": "get_item",
                "parameters": [
                    {
                        "name": "item_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "expand",
                        "in": "query",
                        "schema": {"type": "boolean"},
                    },
                ],
            }
        },
        "/items": {
            "post": {
                "operationId": "create_item",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"name": {"type": "string"}},
                                "required": ["name"],
                                "additionalProperties": False,
                            }
                        }
                    },
                },
            }
        },
    },
}

ODATA_METADATA = """\
<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="4.0"
  xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">
  <edmx:DataServices>
    <Schema Namespace="Catalog" xmlns="http://docs.oasis-open.org/odata/ns/edm">
      <EntityType Name="Product">
        <Key><PropertyRef Name="Id" /></Key>
        <Property Name="Id" Type="Edm.Int32" Nullable="false" />
        <Property Name="Name" Type="Edm.String" Nullable="false" />
        <Property Name="Price" Type="Edm.Decimal" />
      </EntityType>
      <EntityContainer Name="Container">
        <EntitySet Name="Products" EntityType="Catalog.Product" />
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""


def test_openapi_compiler_builds_read_and_mutating_tools() -> None:
    compiled = compile_openapi(OPENAPI_DOCUMENT)

    assert compiled.source_version == "3.1.0"
    assert compiled.base_url == "https://api.example.test/v1"
    tools = {tool.original_name: tool for tool in compiled.tools}
    assert set(tools) == {"get_item", "create_item"}
    get_tool = tools["get_item"]
    create_tool = tools["create_item"]
    assert get_tool.read_only is True
    assert get_tool.input_schema["required"] == ["path"]
    assert create_tool.read_only is False
    assert create_tool.requires_approval is True
    assert create_tool.input_schema["required"] == ["body"]
    assert create_tool.execution["method"] == "POST"


def test_openapi_compiler_rejects_external_refs() -> None:
    document = {
        "openapi": "3.0.3",
        "servers": [{"url": "https://api.example.test"}],
        "paths": {
            "/items": {
                "get": {
                    "operationId": "list_items",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "https://schemas.example.test/item.json"
                                    }
                                }
                            }
                        }
                    },
                }
            }
        },
    }

    with pytest.raises(ToolsetValidationError, match="External OpenAPI"):
        compile_openapi(document)


def test_odata_compiler_builds_controlled_entity_operations() -> None:
    compiled = compile_odata(
        ODATA_METADATA,
        base_url="https://odata.example.test/service",
    )

    tools = {tool.original_name: tool for tool in compiled.tools}
    assert set(tools) == {"list_Products", "get_Products", "create_Products"}
    assert tools["list_Products"].read_only is True
    assert tools["get_Products"].input_schema["properties"]["key"]["required"] == [
        "Id"
    ]
    assert tools["create_Products"].requires_approval is True
    assert "Id" not in tools["create_Products"].input_schema["properties"]["body"][
        "properties"
    ]


def test_schema_drift_marks_required_parameter_and_removed_operation_breaking() -> None:
    previous = compile_openapi(OPENAPI_DOCUMENT).tools
    changed_document = {
        **OPENAPI_DOCUMENT,
        "paths": {
            "/items/{item_id}": {
                "get": {
                    "operationId": "get_item",
                    "parameters": [
                        {
                            "name": "item_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "locale",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                }
            }
        },
    }
    current = compile_openapi(changed_document).tools

    drift = compare_toolsets(previous, current)

    assert drift["compatible"] is False
    assert "Operation removed: create_item" in drift["breaking"]
    assert any("locale" in message for message in drift["breaking"])
