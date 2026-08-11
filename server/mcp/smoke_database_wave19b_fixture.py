"""Provision and verify the fixed disposable Wave-19B acceptance fixtures.

This helper is intentionally not a general database client.  Hosts, paths,
database names, collection names, account names, queries, and sample values
are constants.  Only disposable root/reader passwords arrive through the
environment and they are removed before any response is printed.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from typing import Any


ROOT_PASSWORD = os.environ.pop("MCP_DATABASE_WAVE19B_ROOT_PASSWORD", "")
READER_PASSWORD = os.environ.pop("MCP_DATABASE_WAVE19B_READER_PASSWORD", "")
if not ROOT_PASSWORD or not READER_PASSWORD:
    raise RuntimeError("wave19b_fixture_credentials_missing")


def _request(
    url: str,
    username: str,
    password: str,
    payload: dict[str, Any],
    *,
    allow_http_error: bool = False,
) -> tuple[int, dict[str, Any]]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status = response.status
            raw = response.read(512 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        if not allow_http_error:
            raise RuntimeError("wave19b_fixture_http_failed") from exc
        status = exc.code
        raw = exc.read(512 * 1024 + 1)
    if len(raw) > 512 * 1024:
        raise RuntimeError("wave19b_fixture_response_too_large")
    value = json.loads(raw.decode("utf-8")) if raw else {}
    if not isinstance(value, dict):
        raise RuntimeError("wave19b_fixture_response_invalid")
    return status, value


def _milvus(path: str, payload: dict[str, Any], *, reader: bool = False) -> dict[str, Any]:
    password = READER_PASSWORD if reader else ROOT_PASSWORD
    username = "modelmirror_reader" if reader else "root"
    status, value = _request(
        f"http://milvus.wave19b:19530{path}",
        username,
        password if reader else "Milvus",
        payload,
        allow_http_error=reader,
    )
    value["_status"] = status
    return value


def _milvus_expect_ok(
    path: str,
    payload: dict[str, Any],
    *,
    allow_already_exists: bool = False,
) -> None:
    value = _milvus(path, payload)
    if value.get("code") != 0:
        code = value.get("code")
        safe_code = code if isinstance(code, int) else -1
        message = str(value.get("message") or "").lower()
        if "already exist" in message:
            category = "already_exists"
        elif "parameter" in message or "invalid" in message:
            category = "invalid_parameter"
        elif "auth" in message or "permission" in message:
            category = "authorization"
        elif "privilege" in message:
            category = "privilege"
        elif "database" in message:
            category = "database"
        elif "object" in message:
            category = "object"
        elif "collection" in message:
            category = "collection"
        else:
            category = "unclassified"
        if allow_already_exists and category == "already_exists":
            return
        raise RuntimeError(f"wave19b_milvus_fixture_failed_{safe_code}_{category}")


def provision_milvus() -> None:
    listed = _milvus("/v2/vectordb/collections/list", {"dbName": "default"})
    collections = listed.get("data") if listed.get("code") == 0 else None
    if not isinstance(collections, list):
        raise RuntimeError("wave19b_milvus_list_failed")
    if "project_vectors" not in collections:
        _milvus_expect_ok(
            "/v2/vectordb/collections/create",
            {
                "dbName": "default",
                "collectionName": "project_vectors",
                "dimension": 4,
                "metricType": "COSINE",
                "idType": "Int64",
                "autoId": False,
                "primaryFieldName": "id",
                "vectorFieldName": "embedding",
                "enableDynamicField": True,
            },
        )
        _milvus_expect_ok(
            "/v2/vectordb/entities/insert",
            {
                "dbName": "default",
                "collectionName": "project_vectors",
                "data": [
                    {"id": 1, "embedding": [1.0, 0.0, 0.0, 0.0], "title": "Ada", "category": "person"},
                    {"id": 2, "embedding": [0.0, 1.0, 0.0, 0.0], "title": "Grace", "category": "person"},
                ],
            },
        )
        _milvus_expect_ok(
            "/v2/vectordb/collections/load",
            {"dbName": "default", "collectionName": "project_vectors"},
        )
    _milvus_expect_ok(
        "/v2/vectordb/users/create",
        {"userName": "modelmirror_reader", "password": READER_PASSWORD},
        allow_already_exists=True,
    )
    _milvus_expect_ok(
        "/v2/vectordb/roles/create",
        {"roleName": "modelmirror_reader_role"},
        allow_already_exists=True,
    )
    for object_type, object_name, privilege in (
        ("Collection", "project_vectors", "Query"),
        ("Collection", "project_vectors", "Search"),
        ("Global", "*", "DescribeCollection"),
        ("Global", "*", "ShowCollections"),
    ):
        _milvus_expect_ok(
            "/v2/vectordb/roles/grant_privilege",
            {
                "roleName": "modelmirror_reader_role",
                "objectType": object_type,
                "objectName": object_name,
                "privilege": privilege,
                "dbName": "default",
            },
        )
    _milvus_expect_ok(
        "/v2/vectordb/users/grant_role",
        {"userName": "modelmirror_reader", "roleName": "modelmirror_reader_role"},
    )
    described = _milvus(
        "/v2/vectordb/collections/describe",
        {"dbName": "default", "collectionName": "project_vectors"},
        reader=True,
    )
    if described.get("code") != 0:
        raise RuntimeError("wave19b_milvus_reader_failed")
    denied = _milvus(
        "/v2/vectordb/entities/insert",
        {
            "dbName": "default",
            "collectionName": "project_vectors",
            "data": [{"id": 99, "embedding": [0.0, 0.0, 1.0, 0.0]}],
        },
        reader=True,
    )
    if denied.get("code") == 0:
        raise RuntimeError("wave19b_milvus_reader_write_allowed")


def _arcade(
    path: str,
    payload: dict[str, Any],
    *,
    reader: bool = False,
    fixture_admin: bool = False,
    allow_error: bool = False,
) -> tuple[int, dict[str, Any]]:
    username = "modelmirror_reader" if reader else "root"
    password = READER_PASSWORD if reader else ROOT_PASSWORD
    if fixture_admin:
        username = "modelmirror_fixture_admin"
    return _request(
        f"http://arcadedb.wave19b:2480{path}",
        username,
        password,
        payload,
        allow_http_error=allow_error,
    )


def provision_arcadedb() -> None:
    for definition in (
        {
            "name": "modelmirror_fixture_admin",
            "password": ROOT_PASSWORD,
            "databases": {"wave19b": "admin"},
        },
        {
            "name": "modelmirror_reader",
            "password": READER_PASSWORD,
            "databases": {"wave19b": "readonly"},
        },
    ):
        user_definition = json.dumps(definition, separators=(",", ":"))
        status, value = _arcade(
            "/api/v1/server",
            {"command": f"create user {user_definition}"},
        )
        if status != 200 or value.get("result") != "ok":
            raise RuntimeError("wave19b_arcade_user_failed")
    for command in (
        "CREATE VERTEX TYPE Person IF NOT EXISTS",
        "DELETE FROM Person",
        "INSERT INTO Person SET name = 'Ada'",
        "INSERT INTO Person SET name = 'Grace'",
    ):
        status, value = _arcade(
            "/api/v1/command/wave19b",
            {"language": "sql", "command": command},
            fixture_admin=True,
        )
        if status != 200 or "result" not in value:
            raise RuntimeError("wave19b_arcade_fixture_failed")
    status, value = _arcade(
        "/api/v1/query/wave19b",
        {"language": "sql", "command": "SELECT count(*) AS records FROM Person", "serializer": "record"},
        reader=True,
    )
    if status != 200 or not value.get("result"):
        raise RuntimeError("wave19b_arcade_reader_failed")
    status, _ = _arcade(
        "/api/v1/command/wave19b",
        {"language": "sql", "command": "INSERT INTO Person SET name = 'Denied'"},
        reader=True,
        allow_error=True,
    )
    if status < 400:
        raise RuntimeError("wave19b_arcade_reader_write_allowed")


def main() -> None:
    provision_milvus()
    provision_arcadedb()
    print(
        json.dumps(
            {
                "ok": True,
                "milvus_native_reader_write": "denied",
                "arcadedb_native_reader_write": "denied",
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
