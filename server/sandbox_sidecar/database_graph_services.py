"""Fixed read-only facades for the staged Wave-19B graph and vector services.

Only provider-documented HTTP endpoints are used.  Connection material and
resource names come from the reviewed sidecar configuration; tools cannot
select a URL, header, database, collection, or procedure endpoint.
"""

from __future__ import annotations

import json
import math
import re
from typing import Annotated, Any, Mapping, Protocol
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .database_contracts import MAX_OUTPUT_BYTES, SAFE_IDENTIFIER
from .database_data_services import _integer, _request_json, validate_vector


READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

MAX_MILVUS_COLLECTIONS = 200
MAX_MILVUS_ENTITIES = 100
MAX_GRAPH_ROWS = 100
MAX_QUERY_BYTES = 8 * 1024
MAX_PARAMETER_BYTES = 32 * 1024
PARAMETER_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
ENTITY_ID_TEXT = re.compile(r"[^\x00\r\n]{1,256}")

_NEO4J_START = re.compile(r"^(?:optional\s+match|match|with|unwind|return)\b", re.IGNORECASE)
_NEO4J_RETURN = re.compile(r"\breturn\b", re.IGNORECASE)
_NEO4J_DENIED = re.compile(
    r"\b(?:create|merge|delete|detach|set|remove|drop|alter|grant|deny|revoke|"
    r"call|load|use|show|terminate|start|stop|foreach|execute|admin|dbms|"
    r"yield|in\s+transactions)\b",
    re.IGNORECASE,
)
_NEO4J_EXTENSION_FUNCTION = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\s*\(", re.IGNORECASE
)

_ARCADE_START = re.compile(r"^(?:select|match|traverse)\b", re.IGNORECASE)
_ARCADE_DENIED = re.compile(
    r"\b(?:insert|update|delete|upsert|create|alter|drop|truncate|rebuild|"
    r"grant|revoke|backup|restore|export|import|align|check|repair|move|"
    r"begin|commit|rollback|savepoint|release|console|profile|explain|"
    r"execute|call|eval|script|batch|sleep|sysdate)\b",
    re.IGNORECASE,
)
_ARCADE_EXTERNAL = re.compile(
    r"\b(?:https?|file|ftp|s3|gs|azure|classpath)\s*:\s*//|"
    r"\b(?:load|read|write|open)\s*\(",
    re.IGNORECASE,
)

WAVE19B_UPSTREAM_LOCKS = {
    "zilliztech-mcp-server-milvus": {
        "version": "0.1.1",
        "commit": "a7e624f3057a0d739528bca3ed92504943224ceb",
        "license": "Apache-2.0",
    },
    "neo4j-contrib-mcp-neo4j": {
        "version": "mcp-neo4j-cypher-v0.6.0",
        "commit": "dbc01ba78f171851f2d57dcd125b028c29912fd1",
        "license": "MIT",
    },
    "arcadedata-arcadedb": {
        "version": "26.8.1",
        "commit": "87bdc67f1f0331fa2d07e932a550064c118eae70",
        "license": "Apache-2.0",
    },
}

WAVE19B_SCHEMA_SHA256 = {
    "zilliztech-mcp-server-milvus": "4ab513a696a50d5f215e165ea3c3d7eaab67684e4a2140b55b09fb7d4935516b",
    "neo4j-contrib-mcp-neo4j": "8ce58031d3cc32f4ef2a1b868280a62f3b8f913c00fb13d97864c8e341edc967",
    "arcadedata-arcadedb": "1a1176ef633813c4b75a597f32f95ab4c0becc626d941e47749e5c62e3b1ca05",
}


class GraphServiceContext(Protocol):
    adapter_id: str
    settings: Mapping[str, str | int]
    credentials: Mapping[str, str]


def _mask_query(query: object, *, code: str) -> tuple[str, str]:
    if not isinstance(query, str):
        raise ValueError(code)
    clean = query.strip()
    if not clean or "\x00" in clean or len(clean.encode("utf-8")) > MAX_QUERY_BYTES:
        raise ValueError(code)
    masked: list[str] = []
    index = 0
    quote_char: str | None = None
    block_comment = False
    line_comment = False
    while index < len(clean):
        char = clean[index]
        next_char = clean[index + 1] if index + 1 < len(clean) else ""
        if line_comment:
            if char in "\r\n":
                line_comment = False
                masked.append(char)
            else:
                masked.append(" ")
            index += 1
            continue
        if block_comment:
            if char == "*" and next_char == "/":
                masked.extend((" ", " "))
                index += 2
                block_comment = False
            else:
                masked.append(" ")
                index += 1
            continue
        if quote_char is not None:
            masked.append(" ")
            if char == "\\" and index + 1 < len(clean):
                masked.append(" ")
                index += 2
                continue
            if char == quote_char:
                if index + 1 < len(clean) and clean[index + 1] == quote_char:
                    masked.append(" ")
                    index += 2
                    continue
                quote_char = None
            index += 1
            continue
        if char == "/" and next_char == "*":
            masked.extend((" ", " "))
            block_comment = True
            index += 2
            continue
        if (char == "/" and next_char == "/") or (char == "-" and next_char == "-"):
            masked.extend((" ", " "))
            line_comment = True
            index += 2
            continue
        if char in {"'", '"', "`"}:
            quote_char = char
            masked.append(" ")
            index += 1
            continue
        masked.append(char)
        index += 1
    if quote_char is not None or block_comment:
        raise ValueError(code)
    return clean, "".join(masked)


def validate_neo4j_cypher(query: object) -> str:
    clean, masked = _mask_query(query, code="invalid_neo4j_query")
    normalized = masked.strip()
    if ";" in masked or not _NEO4J_START.match(normalized) or not _NEO4J_RETURN.search(masked):
        raise ValueError("neo4j_query_denied")
    if _NEO4J_DENIED.search(masked) or _NEO4J_EXTENSION_FUNCTION.search(masked):
        raise ValueError("neo4j_query_denied")
    return clean


def validate_arcade_query(query: object) -> str:
    clean, masked = _mask_query(query, code="invalid_arcade_query")
    normalized = masked.strip()
    if ";" in masked or not _ARCADE_START.match(normalized):
        raise ValueError("arcade_query_denied")
    if _ARCADE_DENIED.search(masked) or _ARCADE_EXTERNAL.search(masked):
        raise ValueError("arcade_query_denied")
    return clean


def _validate_json_value(value: object, *, depth: int = 0) -> Any:
    if depth > 6:
        raise ValueError("query_parameters_too_deep")
    if value is None or isinstance(value, (bool, str, int)):
        if isinstance(value, str) and (len(value) > 4_096 or "\x00" in value):
            raise ValueError("invalid_query_parameters")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("invalid_query_parameters")
        return value
    if isinstance(value, list):
        if len(value) > 200:
            raise ValueError("invalid_query_parameters")
        return [_validate_json_value(child, depth=depth + 1) for child in value]
    if isinstance(value, dict):
        if len(value) > 100:
            raise ValueError("invalid_query_parameters")
        normalized: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            if not PARAMETER_NAME.fullmatch(key):
                raise ValueError("invalid_query_parameters")
            normalized[key] = _validate_json_value(child, depth=depth + 1)
        return normalized
    raise ValueError("invalid_query_parameters")


def validate_query_parameters(value: object) -> dict[str, Any]:
    normalized = _validate_json_value(value)
    if not isinstance(normalized, dict):
        raise ValueError("invalid_query_parameters")
    if len(json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_PARAMETER_BYTES:
        raise ValueError("query_parameters_too_large")
    return normalized


def validate_graph_service_arguments(adapter_id: str, tool_name: str, arguments: Mapping[str, Any]) -> None:
    if adapter_id == "zilliztech-mcp-server-milvus":
        if tool_name == "get_entities":
            _entity_ids(arguments.get("ids"))
        if tool_name == "search_vectors":
            validate_vector(arguments.get("vector"))
        if "limit" in arguments:
            _integer(arguments["limit"], minimum=1, maximum=MAX_MILVUS_ENTITIES, code="invalid_limit")
    elif adapter_id == "neo4j-contrib-mcp-neo4j" and tool_name == "read_cypher":
        validate_neo4j_cypher(arguments.get("query"))
        validate_query_parameters(arguments.get("parameters", {}))
        if "max_rows" in arguments:
            _integer(arguments["max_rows"], minimum=1, maximum=MAX_GRAPH_ROWS, code="invalid_row_limit")
    elif adapter_id == "arcadedata-arcadedb":
        if tool_name == "describe_type":
            _identifier(arguments.get("type_name"), "invalid_type_name")
        if tool_name == "read_query":
            validate_arcade_query(arguments.get("query"))
            validate_query_parameters(arguments.get("parameters", {}))
            if "max_rows" in arguments:
                _integer(arguments["max_rows"], minimum=1, maximum=MAX_GRAPH_ROWS, code="invalid_row_limit")


def _identifier(value: object, code: str) -> str:
    clean = str(value or "").strip()
    if not SAFE_IDENTIFIER.fullmatch(clean):
        raise ValueError(code)
    return clean


def _entity_ids(value: object) -> list[str | int]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_MILVUS_ENTITIES:
        raise ValueError("invalid_entity_ids")
    normalized: list[str | int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            raise ValueError("invalid_entity_ids")
        if isinstance(item, str):
            if not ENTITY_ID_TEXT.fullmatch(item):
                raise ValueError("invalid_entity_ids")
            normalized.append(item)
        else:
            if item < -(2**63) or item > 2**63 - 1:
                raise ValueError("invalid_entity_ids")
            normalized.append(item)
    return normalized


def _output_fields(context: GraphServiceContext) -> list[str]:
    return [field for field in str(context.settings["output_fields"]).split(",") if field]


def _milvus_payload(context: GraphServiceContext, path: str, payload: Mapping[str, Any]) -> Any:
    value = _request_json(context, "POST", path, payload=payload)
    if not isinstance(value, dict) or value.get("code") != 0 or "data" not in value:
        raise ValueError("milvus_response_invalid")
    return value["data"]


def _milvus_base_payload(context: GraphServiceContext) -> dict[str, Any]:
    return {
        "dbName": str(context.settings["database"]),
        "collectionName": str(context.settings["collection"]),
    }


def build_milvus(context: GraphServiceContext) -> FastMCP:
    mcp = FastMCP("ModelMirror Milvus Read Only")

    @mcp.tool(annotations=READ_ONLY)
    def list_collections() -> dict[str, Any]:
        """List a bounded set of collections visible to the native read-only account."""
        data = _milvus_payload(
            context,
            "/v2/vectordb/collections/list",
            {"dbName": str(context.settings["database"])},
        )
        if not isinstance(data, list) or any(not isinstance(item, str) for item in data):
            raise ValueError("milvus_response_invalid")
        return {"collections": data[:MAX_MILVUS_COLLECTIONS], "truncated": len(data) > MAX_MILVUS_COLLECTIONS}

    @mcp.tool(annotations=READ_ONLY)
    def describe_collection() -> dict[str, Any]:
        """Describe only the project-bound collection."""
        data = _milvus_payload(
            context,
            "/v2/vectordb/collections/describe",
            _milvus_base_payload(context),
        )
        if not isinstance(data, dict):
            raise ValueError("milvus_response_invalid")
        return data

    @mcp.tool(annotations=READ_ONLY)
    def get_entities(ids: list[str | int]) -> dict[str, Any]:
        """Read at most 100 primary-key entities from the bound collection."""
        safe_ids = _entity_ids(ids)
        payload = _milvus_base_payload(context)
        payload.update(id=safe_ids, outputFields=_output_fields(context))
        data = _milvus_payload(context, "/v2/vectordb/entities/get", payload)
        if not isinstance(data, list):
            raise ValueError("milvus_response_invalid")
        return {"entities": data[:MAX_MILVUS_ENTITIES], "truncated": len(data) > MAX_MILVUS_ENTITIES}

    @mcp.tool(annotations=READ_ONLY)
    def search_vectors(vector: list[float], limit: int = 10) -> dict[str, Any]:
        """Search one bound vector field without filters or caller-selected output fields."""
        safe_vector = validate_vector(vector)
        safe_limit = _integer(limit, minimum=1, maximum=MAX_MILVUS_ENTITIES, code="invalid_limit")
        payload = _milvus_base_payload(context)
        payload.update(
            data=[safe_vector],
            annsField=str(context.settings["vector_field"]),
            outputFields=_output_fields(context),
            limit=safe_limit,
        )
        data = _milvus_payload(context, "/v2/vectordb/entities/search", payload)
        if not isinstance(data, list):
            raise ValueError("milvus_response_invalid")
        return {"matches": data[:safe_limit], "truncated": len(data) > safe_limit}

    return mcp


def preflight_milvus(context: GraphServiceContext) -> None:
    data = _milvus_payload(context, "/v2/vectordb/collections/describe", _milvus_base_payload(context))
    if not isinstance(data, dict) or data.get("collectionName") != context.settings["collection"]:
        raise RuntimeError("database_preflight_failed")


def _neo4j_query(
    context: GraphServiceContext,
    statement: str,
    parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    database = quote(str(context.settings["database"]), safe="")
    value = _request_json(
        context,
        "POST",
        f"/db/{database}/query/v2",
        payload={"statement": statement, "parameters": dict(parameters or {})},
    )
    if not isinstance(value, dict) or value.get("errors") or value.get("error"):
        raise ValueError("neo4j_response_invalid")
    query_type = value.get("queryType")
    if query_type is not None and query_type != "r":
        raise ValueError("database_readonly_violation")
    data = value.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("fields"), list) or not isinstance(data.get("values"), list):
        raise ValueError("neo4j_response_invalid")
    return data


def _neo4j_rows(data: Mapping[str, Any], limit: int) -> dict[str, Any]:
    fields = data.get("fields")
    values = data.get("values")
    if not isinstance(fields, list) or not isinstance(values, list) or any(not isinstance(field, str) for field in fields):
        raise ValueError("neo4j_response_invalid")
    rows: list[dict[str, Any]] = []
    for raw_row in values[: limit + 1]:
        if not isinstance(raw_row, list):
            raise ValueError("neo4j_response_invalid")
        rows.append({field: raw_row[index] if index < len(raw_row) else None for index, field in enumerate(fields)})
    return {"columns": fields, "rows": rows[:limit], "row_count": min(len(rows), limit), "truncated": len(rows) > limit}


def build_neo4j(context: GraphServiceContext) -> FastMCP:
    mcp = FastMCP("ModelMirror Neo4j Read Only")

    @mcp.tool(annotations=READ_ONLY)
    def get_schema() -> dict[str, Any]:
        """Read bounded label and relationship-type names through fixed built-in procedures."""
        statement = (
            "CALL { CALL db.labels() YIELD label RETURN collect(label)[..200] AS labels } "
            "CALL { CALL db.relationshipTypes() YIELD relationshipType "
            "RETURN collect(relationshipType)[..200] AS relationshipTypes } "
            "RETURN labels, relationshipTypes"
        )
        data = _neo4j_query(context, statement)
        payload = _neo4j_rows(data, 1)
        return payload["rows"][0] if payload["rows"] else {"labels": [], "relationshipTypes": []}

    @mcp.tool(annotations=READ_ONLY)
    def read_cypher(
        query: Annotated[str, Field(max_length=8192)],
        parameters: dict[str, Any] | None = None,
        max_rows: int = 50,
    ) -> dict[str, Any]:
        """Run one bounded read-only Cypher query; procedures and write clauses are denied."""
        clean = validate_neo4j_cypher(query)
        safe_parameters = validate_query_parameters(parameters or {})
        limit = _integer(max_rows, minimum=1, maximum=MAX_GRAPH_ROWS, code="invalid_row_limit")
        wrapped = f"CALL {{ {clean} }} RETURN * LIMIT {limit + 1}"
        return _neo4j_rows(_neo4j_query(context, wrapped, safe_parameters), limit)

    return mcp


def preflight_neo4j(context: GraphServiceContext) -> None:
    data = _neo4j_query(context, "RETURN 1 AS modelmirror_preflight")
    payload = _neo4j_rows(data, 1)
    if not payload["rows"] or payload["rows"][0].get("modelmirror_preflight") != 1:
        raise RuntimeError("database_preflight_failed")


def _arcade_query(
    context: GraphServiceContext,
    query: str,
    parameters: Mapping[str, Any],
    limit: int,
) -> list[Any]:
    database = quote(str(context.settings["database"]), safe="")
    value = _request_json(
        context,
        "POST",
        f"/api/v1/query/{database}",
        payload={
            "language": "sql",
            "command": query,
            "params": dict(parameters),
            "limit": limit,
            "serializer": "record",
        },
    )
    if not isinstance(value, dict) or not isinstance(value.get("result"), list):
        raise ValueError("arcade_response_invalid")
    return value["result"]


def _arcade_rows(rows: list[Any], limit: int) -> dict[str, Any]:
    selected = rows[:limit]
    encoded = json.dumps(selected, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES - 16 * 1024:
        raise ValueError("output_too_large")
    return {"rows": selected, "row_count": len(selected), "truncated": len(rows) > limit}


def build_arcadedb(context: GraphServiceContext) -> FastMCP:
    mcp = FastMCP("ModelMirror ArcadeDB Read Only")

    @mcp.tool(annotations=READ_ONLY)
    def list_types() -> dict[str, Any]:
        """List bounded schema type metadata from ArcadeDB's read-only virtual schema."""
        rows = _arcade_query(
            context,
            "SELECT name, type, records FROM schema:types ORDER BY name",
            {},
            MAX_GRAPH_ROWS + 1,
        )
        return _arcade_rows(rows, MAX_GRAPH_ROWS)

    @mcp.tool(annotations=READ_ONLY)
    def describe_type(type_name: Annotated[str, Field(max_length=128)]) -> dict[str, Any]:
        """Describe one validated schema type without accepting a raw schema query."""
        safe_name = _identifier(type_name, "invalid_type_name")
        rows = _arcade_query(
            context,
            "SELECT name, type, records, properties, indexes, parentTypes "
            "FROM schema:types WHERE name = :type_name LIMIT 1",
            {"type_name": safe_name},
            1,
        )
        return {"type": rows[0] if rows else None}

    @mcp.tool(annotations=READ_ONLY)
    def read_query(
        query: Annotated[str, Field(max_length=8192)],
        parameters: dict[str, Any] | None = None,
        max_rows: int = 50,
    ) -> dict[str, Any]:
        """Run one bounded SELECT, MATCH, or TRAVERSE through the query endpoint."""
        clean = validate_arcade_query(query)
        safe_parameters = validate_query_parameters(parameters or {})
        limit = _integer(max_rows, minimum=1, maximum=MAX_GRAPH_ROWS, code="invalid_row_limit")
        return _arcade_rows(_arcade_query(context, clean, safe_parameters, limit + 1), limit)

    return mcp


def preflight_arcadedb(context: GraphServiceContext) -> None:
    rows = _arcade_query(context, "SELECT 1 AS modelmirror_preflight", {}, 1)
    if not rows or not isinstance(rows[0], dict) or rows[0].get("modelmirror_preflight") != 1:
        raise RuntimeError("database_preflight_failed")
