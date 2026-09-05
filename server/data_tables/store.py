from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Protocol

try:
    from server.workflow_native.values import normalize_workflow_value
except ModuleNotFoundError:  # pragma: no cover - container import layout
    from workflow_native.values import normalize_workflow_value

from .models import (
    AgentTableAuditEntry,
    AgentTableDefinition,
    AgentTableDetail,
    AgentTableField,
    AgentTableRecord,
    AgentTableSchemaVersion,
    AgentTableValidationResult,
)


FIELD_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
MAX_FIELDS = 50
MAX_RECORD_BYTES = 256 * 1024
MAX_QUERY_LIMIT = 200
MAX_MUTATION_ROWS = 100
SYSTEM_FIELDS = {"record_id", "created_at", "updated_at", "revision"}
FILTER_OPERATORS = {
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "contains",
    "is_null",
}


class AgentTableError(Exception):
    pass


class AgentTableNotFoundError(AgentTableError):
    pass


class AgentTableConflictError(AgentTableError):
    pass


class AgentTableValidationError(AgentTableError):
    pass


class AgentTableBackend(Protocol):
    backend_name: str

    def list_tables(self, **kwargs: Any) -> list[AgentTableDefinition]: ...

    def create_table(self, **kwargs: Any) -> AgentTableDefinition: ...

    def get_table(self, table_id: str) -> AgentTableDefinition: ...

    def get_detail(self, table_id: str) -> AgentTableDetail: ...

    def resolve_schema_version(self, table_id: str, **kwargs: Any) -> AgentTableSchemaVersion: ...

    def query_records(self, table_id: str, **kwargs: Any) -> list[dict[str, Any]]: ...

    def create_record_for_schema(self, table_id: str, **kwargs: Any) -> dict[str, Any]: ...

    def update_records(self, table_id: str, **kwargs: Any) -> dict[str, int]: ...

    def delete_records(self, table_id: str, **kwargs: Any) -> dict[str, int]: ...


class SQLiteAgentTableBackend:
    """Transactional SQLite backend for trusted local Agent Table resources."""

    backend_name = "sqlite"

    def __init__(self, storage_dir: str | Path) -> None:
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = self.storage_dir / "agent_tables.sqlite3"
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.database_path,
            timeout=10,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except Exception:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_table_definitions (
                    table_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    draft_revision INTEGER NOT NULL,
                    active_schema_version INTEGER,
                    fields_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_table_schema_versions (
                    table_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    draft_revision INTEGER NOT NULL,
                    fields_json TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    published_at REAL NOT NULL,
                    PRIMARY KEY (table_id, version),
                    FOREIGN KEY (table_id) REFERENCES agent_table_definitions(table_id)
                );
                CREATE TABLE IF NOT EXISTS agent_table_records (
                    table_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    data_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (table_id, record_id),
                    FOREIGN KEY (table_id) REFERENCES agent_table_definitions(table_id)
                );
                CREATE TABLE IF NOT EXISTS agent_table_operations (
                    table_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (table_id, operation_id),
                    FOREIGN KEY (table_id) REFERENCES agent_table_definitions(table_id)
                );
                CREATE TABLE IF NOT EXISTS agent_table_audit (
                    audit_id TEXT PRIMARY KEY,
                    table_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    record_id TEXT,
                    schema_version INTEGER,
                    affected_count INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY (table_id) REFERENCES agent_table_definitions(table_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_table_records_updated
                    ON agent_table_records(table_id, updated_at DESC, record_id);
                CREATE INDEX IF NOT EXISTS idx_agent_table_audit_created
                    ON agent_table_audit(table_id, created_at DESC);
                """
            )

    def list_tables(
        self,
        *,
        status: str | None = None,
        search: str = "",
        limit: int = 100,
    ) -> list[AgentTableDefinition]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if status:
            if status not in {"draft", "published", "archived"}:
                raise AgentTableValidationError("Invalid Agent Table status filter.")
            clauses.append("status = ?")
            parameters.append(status)
        term = search.strip().lower()
        if term:
            clauses.append("(LOWER(name) LIKE ? OR LOWER(description) LIKE ?)")
            escaped = term.replace("%", "\\%").replace("_", "\\_")
            parameters.extend([f"%{escaped}%", f"%{escaped}%"])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(max(1, min(int(limit), 500)))
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"""SELECT * FROM agent_table_definitions {where}
                    ORDER BY updated_at DESC, table_id LIMIT ?""",
                parameters,
            ).fetchall()
        return [self._table_from_row(row) for row in rows]

    def create_table(
        self,
        *,
        name: str,
        description: str = "",
        fields: list[dict[str, Any]] | None = None,
    ) -> AgentTableDefinition:
        now = time.time()
        table = AgentTableDefinition(
            table_id=f"table_{uuid.uuid4().hex}",
            name=self._required_text(name, "name", 160),
            description=str(description or "").strip()[:2000],
            fields=self._normalize_fields(fields or []),
            created_at=now,
            updated_at=now,
        )
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                """INSERT INTO agent_table_definitions
                    (table_id, name, description, status, draft_revision,
                     active_schema_version, fields_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                self._table_parameters(table),
            )
        return table.model_copy(deep=True)

    def get_table(self, table_id: str) -> AgentTableDefinition:
        with self._lock, self._connection() as connection:
            return self._load_table(connection, table_id).model_copy(deep=True)

    def get_detail(self, table_id: str) -> AgentTableDetail:
        with self._lock, self._connection() as connection:
            table = self._load_table(connection, table_id)
            versions = self._load_versions(connection, table_id)
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM agent_table_records WHERE table_id = ?",
                    (table_id,),
                ).fetchone()[0]
            )
        return AgentTableDetail(
            table=table,
            schema_versions=list(reversed(versions)),
            record_count=count,
        )

    def update_table(
        self,
        table_id: str,
        *,
        revision: int,
        patch: dict[str, Any],
    ) -> AgentTableDefinition:
        with self._lock, self._connection(write=True) as connection:
            table = self._load_table(connection, table_id)
            self._require_revision(table, revision)
            self._require_editable(table)
            if "name" in patch:
                table.name = self._required_text(patch["name"], "name", 160)
            if "description" in patch:
                table.description = str(patch["description"] or "").strip()[:2000]
            if "fields" in patch:
                table.fields = self._normalize_fields(
                    patch["fields"] or [], existing=table.fields
                )
                if table.active_schema_version is not None:
                    active = self._load_schema_version(
                        connection, table_id, table.active_schema_version
                    )
                    self._validate_compatible_evolution(active.fields, table.fields)
            table.draft_revision += 1
            table.updated_at = time.time()
            self._persist_table(connection, table)
            return table.model_copy(deep=True)

    def validate_table(
        self, table_id: str, *, revision: int | None = None
    ) -> AgentTableValidationResult:
        with self._lock, self._connection() as connection:
            table = self._load_table(connection, table_id)
            if revision is not None:
                self._require_revision(table, revision)
            issues = self._validation_issues(connection, table)
        return AgentTableValidationResult(
            valid=not issues,
            table_id=table.table_id,
            draft_revision=table.draft_revision,
            issues=issues,
        )

    def publish_table(
        self, table_id: str, *, revision: int
    ) -> AgentTableSchemaVersion:
        with self._lock, self._connection(write=True) as connection:
            table = self._load_table(connection, table_id)
            self._require_revision(table, revision)
            self._require_editable(table)
            issues = self._validation_issues(connection, table)
            if issues:
                raise AgentTableValidationError(
                    "; ".join(issue["message"] for issue in issues)
                )
            next_version = (
                int(
                    connection.execute(
                        """SELECT COALESCE(MAX(version), 0)
                           FROM agent_table_schema_versions WHERE table_id = ?""",
                        (table_id,),
                    ).fetchone()[0]
                )
                + 1
            )
            fields_json = self._json([field.model_dump() for field in table.fields])
            checksum = hashlib.sha256(fields_json.encode("utf-8")).hexdigest()
            published_at = time.time()
            version = AgentTableSchemaVersion(
                table_id=table_id,
                version=next_version,
                draft_revision=table.draft_revision,
                fields=table.fields,
                checksum=checksum,
                published_at=published_at,
            )
            connection.execute(
                """INSERT INTO agent_table_schema_versions
                   (table_id, version, draft_revision, fields_json, checksum, published_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    table_id,
                    next_version,
                    table.draft_revision,
                    fields_json,
                    checksum,
                    published_at,
                ),
            )
            table.status = "published"
            table.active_schema_version = next_version
            table.updated_at = published_at
            self._persist_table(connection, table)
            self._write_audit(
                connection,
                table_id=table_id,
                operation="schema.publish",
                schema_version=next_version,
            )
            return version.model_copy(deep=True)

    def archive_table(
        self, table_id: str, *, revision: int
    ) -> AgentTableDefinition:
        with self._lock, self._connection(write=True) as connection:
            table = self._load_table(connection, table_id)
            self._require_revision(table, revision)
            if table.status == "archived":
                return table.model_copy(deep=True)
            table.status = "archived"
            table.draft_revision += 1
            table.updated_at = time.time()
            self._persist_table(connection, table)
            self._write_audit(connection, table_id=table_id, operation="table.archive")
            return table.model_copy(deep=True)

    def list_schema_versions(self, table_id: str) -> list[AgentTableSchemaVersion]:
        with self._lock, self._connection() as connection:
            self._load_table(connection, table_id)
            return [
                version.model_copy(deep=True)
                for version in reversed(self._load_versions(connection, table_id))
            ]

    def get_schema_version(
        self, table_id: str, version: int | None = None
    ) -> AgentTableSchemaVersion:
        with self._lock, self._connection() as connection:
            table = self._load_table(connection, table_id)
            target = version or table.active_schema_version
            if target is None:
                raise AgentTableNotFoundError("Agent Table has no published schema.")
            return self._load_schema_version(
                connection, table_id, int(target)
            ).model_copy(deep=True)

    def resolve_schema_version(
        self,
        table_id: str,
        *,
        version_policy: str = "latest",
        pinned_version: int | None = None,
        write: bool = False,
    ) -> AgentTableSchemaVersion:
        with self._lock, self._connection() as connection:
            table = self._load_table(connection, table_id)
            if version_policy not in {"latest", "pinned"}:
                raise AgentTableValidationError(
                    "version_policy must be latest or pinned."
                )
            version = (
                int(pinned_version or 0)
                if version_policy == "pinned"
                else int(table.active_schema_version or 0)
            )
            if version < 1:
                raise AgentTableNotFoundError(
                    "Agent Table has no published schema for this version policy."
                )
            if write:
                if table.status == "archived":
                    raise AgentTableConflictError(
                        "Archived Agent Tables are read-only."
                    )
                if version != int(table.active_schema_version or 0):
                    raise AgentTableConflictError(
                        "Agent Table writes require the active schema version."
                    )
            return self._load_schema_version(
                connection, table_id, version
            ).model_copy(deep=True)

    def validate_workflow_node_contract(
        self,
        table_id: str,
        *,
        schema_version: int,
        kind: str,
        data: dict[str, Any],
    ) -> None:
        schema = self.get_schema_version(table_id, schema_version)
        business_fields = {field.name for field in schema.fields}
        readable_fields = business_fields | SYSTEM_FIELDS

        def require_field(field_name: object, *, readable: bool) -> None:
            name = str(field_name or "").strip()
            allowed = readable_fields if readable else business_fields
            if name not in allowed:
                raise AgentTableValidationError(
                    f"Agent Table schema {schema.version} has no field '{name or '<empty>'}'."
                )

        if kind == "data_table_query":
            for field_name in data.get("selectFields") or []:
                require_field(field_name, readable=False)
            for sort_item in data.get("sort") or []:
                if isinstance(sort_item, dict):
                    require_field(sort_item.get("field"), readable=True)
        if kind in {"data_table_insert", "data_table_update"}:
            bindings = data.get("valueBindings") or {}
            if isinstance(bindings, dict):
                for field_name in bindings:
                    require_field(field_name, readable=False)

        def check_filter(value: object) -> None:
            if not isinstance(value, dict):
                return
            items = value.get("items")
            if isinstance(items, list):
                for item in items:
                    check_filter(item)
                return
            require_field(value.get("field"), readable=True)

        check_filter(data.get("filter"))

    def query_records(
        self,
        table_id: str,
        *,
        schema_version: int,
        fields: list[str] | None = None,
        filter_tree: dict[str, Any] | None = None,
        sort: list[dict[str, Any]] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        bounded_limit = int(limit)
        if not 1 <= bounded_limit <= MAX_QUERY_LIMIT:
            raise AgentTableValidationError(
                f"Query limit must be between 1 and {MAX_QUERY_LIMIT}."
            )
        with self._lock, self._connection() as connection:
            table = self._load_table(connection, table_id)
            schema = self._load_schema_version(
                connection, table_id, int(schema_version)
            )
            selected = self._validate_selected_fields(fields, schema.fields)
            parameters: list[Any] = []
            where = self._compile_filter(
                filter_tree,
                schema.fields,
                parameters,
                allow_empty=True,
            )
            order_by = self._compile_sort(sort or [], schema.fields, parameters)
            parameters.append(bounded_limit)
            rows = connection.execute(
                "SELECT * FROM agent_table_records "
                "WHERE table_id = ? AND (" + where + ") "
                + order_by
                + " LIMIT ?",
                [table.table_id, *parameters],
            ).fetchall()
        return [
            self._flatten_record(self._record_from_row(row), selected)
            for row in rows
        ]

    def capture_evaluation_queries(
        self,
        queries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Execute a bounded fixture batch in one SQLite read snapshot."""

        if len(queries) > 1_000:
            raise AgentTableValidationError(
                "Evaluation fixture capture supports at most 1000 queries."
            )
        results: list[dict[str, Any]] = []
        with self._lock, self._connection() as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            for query in queries:
                fixture_key = str(query.get("fixture_key") or "").strip()
                table_id = str(query.get("table_id") or "").strip()
                schema_version = int(query.get("schema_version") or 0)
                bounded_limit = int(query.get("limit") or 0)
                if not fixture_key or not 1 <= bounded_limit <= MAX_QUERY_LIMIT:
                    raise AgentTableValidationError(
                        "Evaluation fixture query is missing a key or has an invalid limit."
                    )
                table = self._load_table(connection, table_id)
                schema = self._load_schema_version(
                    connection,
                    table_id,
                    schema_version,
                )
                if str(query.get("schema_checksum") or "") != schema.checksum:
                    raise AgentTableConflictError(
                        "Evaluation fixture schema checksum no longer matches."
                    )
                raw_fields = query.get("fields")
                fields = (
                    [str(item) for item in raw_fields]
                    if isinstance(raw_fields, list)
                    else None
                )
                selected = self._validate_selected_fields(fields, schema.fields)
                parameters: list[Any] = []
                where = self._compile_filter(
                    query.get("filter"),
                    schema.fields,
                    parameters,
                    allow_empty=True,
                )
                raw_sort = query.get("sort")
                order_by = self._compile_sort(
                    raw_sort if isinstance(raw_sort, list) else [],
                    schema.fields,
                    parameters,
                )
                parameters.append(bounded_limit)
                rows = connection.execute(
                    "SELECT * FROM agent_table_records "
                    "WHERE table_id = ? AND (" + where + ") "
                    + order_by
                    + " LIMIT ?",
                    [table.table_id, *parameters],
                ).fetchall()
                results.append(
                    {
                        "fixture_key": fixture_key,
                        "records": [
                            self._flatten_record(
                                self._record_from_row(row),
                                selected,
                            )
                            for row in rows
                        ],
                    }
                )
        return results

    def create_record_for_schema(
        self,
        table_id: str,
        *,
        schema_version: int,
        data: dict[str, Any],
        operation_id: str,
    ) -> dict[str, Any]:
        payload_hash = self._request_hash(
            "workflow.record.create",
            {"schema_version": schema_version, "data": data},
        )
        with self._lock, self._connection(write=True) as connection:
            replay = self._load_operation(
                connection,
                table_id,
                operation_id,
                "workflow.record.create",
                payload_hash,
            )
            if replay is not None:
                return replay
            table, schema = self._writable_schema(connection, table_id)
            if schema.version != int(schema_version):
                raise AgentTableConflictError(
                    "Agent Table writes require the active schema version."
                )
            normalized = self._validate_record_data(
                data, schema.fields, partial=False
            )
            now = time.time()
            record = AgentTableRecord(
                record_id=f"record_{uuid.uuid4().hex}",
                table_id=table_id,
                schema_version=schema.version,
                data=normalized,
                revision=1,
                created_at=now,
                updated_at=now,
            )
            connection.execute(
                """INSERT INTO agent_table_records
                   (table_id, record_id, schema_version, data_json, revision,
                    created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                self._record_parameters(record),
            )
            result = self._flatten_record(record, [field.name for field in schema.fields])
            self._save_operation(
                connection,
                table_id,
                operation_id,
                "workflow.record.create",
                payload_hash,
                result,
            )
            self._write_audit(
                connection,
                table_id=table_id,
                operation="workflow.record.create",
                record_id=record.record_id,
                schema_version=schema.version,
                affected_count=1,
            )
            return result

    def update_records(
        self,
        table_id: str,
        *,
        schema_version: int,
        filter_tree: dict[str, Any],
        data: dict[str, Any],
        operation_id: str,
    ) -> dict[str, int]:
        if not data:
            raise AgentTableValidationError("Update data cannot be empty.")
        payload_hash = self._request_hash(
            "workflow.records.update",
            {
                "schema_version": schema_version,
                "filter": filter_tree,
                "data": data,
            },
        )
        with self._lock, self._connection(write=True) as connection:
            replay = self._load_operation(
                connection,
                table_id,
                operation_id,
                "workflow.records.update",
                payload_hash,
            )
            if replay is not None:
                return {"matched": int(replay["matched"]), "affected": int(replay["affected"])}
            _, schema = self._writable_schema(connection, table_id)
            if schema.version != int(schema_version):
                raise AgentTableConflictError(
                    "Agent Table writes require the active schema version."
                )
            normalized_patch = self._validate_record_data(
                data, schema.fields, partial=True
            )
            rows = self._matching_rows(
                connection, table_id, schema.fields, filter_tree
            )
            if len(rows) > MAX_MUTATION_ROWS:
                raise AgentTableValidationError(
                    f"Update matches more than {MAX_MUTATION_ROWS} records."
                )
            for row in rows:
                record = self._record_from_row(row)
                merged = dict(record.data)
                merged.update(normalized_patch)
                record.data = self._validate_record_data(
                    merged, schema.fields, partial=False
                )
                record.schema_version = schema.version
                record.revision += 1
                record.updated_at = time.time()
                connection.execute(
                    """UPDATE agent_table_records SET schema_version = ?, data_json = ?,
                       revision = ?, updated_at = ? WHERE table_id = ? AND record_id = ?""",
                    (
                        record.schema_version,
                        self._json(record.data),
                        record.revision,
                        record.updated_at,
                        table_id,
                        record.record_id,
                    ),
                )
            result = {"matched": len(rows), "affected": len(rows)}
            self._save_operation(
                connection,
                table_id,
                operation_id,
                "workflow.records.update",
                payload_hash,
                result,
            )
            self._write_audit(
                connection,
                table_id=table_id,
                operation="workflow.records.update",
                schema_version=schema.version,
                affected_count=len(rows),
            )
            return result

    def delete_records(
        self,
        table_id: str,
        *,
        schema_version: int,
        filter_tree: dict[str, Any],
        operation_id: str,
    ) -> dict[str, int]:
        payload_hash = self._request_hash(
            "workflow.records.delete",
            {"schema_version": schema_version, "filter": filter_tree},
        )
        with self._lock, self._connection(write=True) as connection:
            replay = self._load_operation(
                connection,
                table_id,
                operation_id,
                "workflow.records.delete",
                payload_hash,
            )
            if replay is not None:
                return {"matched": int(replay["matched"]), "affected": int(replay["affected"])}
            _, schema = self._writable_schema(connection, table_id)
            if schema.version != int(schema_version):
                raise AgentTableConflictError(
                    "Agent Table writes require the active schema version."
                )
            rows = self._matching_rows(
                connection, table_id, schema.fields, filter_tree
            )
            if len(rows) > MAX_MUTATION_ROWS:
                raise AgentTableValidationError(
                    f"Delete matches more than {MAX_MUTATION_ROWS} records."
                )
            record_ids = [str(row["record_id"]) for row in rows]
            if record_ids:
                connection.executemany(
                    "DELETE FROM agent_table_records WHERE table_id = ? AND record_id = ?",
                    [(table_id, record_id) for record_id in record_ids],
                )
            result = {"matched": len(rows), "affected": len(rows)}
            self._save_operation(
                connection,
                table_id,
                operation_id,
                "workflow.records.delete",
                payload_hash,
                result,
            )
            self._write_audit(
                connection,
                table_id=table_id,
                operation="workflow.records.delete",
                schema_version=schema.version,
                affected_count=len(rows),
            )
            return result

    def list_records(
        self,
        table_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentTableRecord]:
        with self._lock, self._connection() as connection:
            self._load_table(connection, table_id)
            rows = connection.execute(
                """SELECT * FROM agent_table_records WHERE table_id = ?
                   ORDER BY updated_at DESC, record_id LIMIT ? OFFSET ?""",
                (table_id, max(1, min(int(limit), 500)), max(0, int(offset))),
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def create_record(
        self,
        table_id: str,
        *,
        data: dict[str, Any],
        operation_id: str | None = None,
    ) -> AgentTableRecord:
        payload_hash = self._request_hash("record.create", {"data": data})
        with self._lock, self._connection(write=True) as connection:
            replay = self._load_operation(
                connection, table_id, operation_id, "record.create", payload_hash
            )
            if replay is not None:
                return AgentTableRecord.model_validate(replay)
            table, schema = self._writable_schema(connection, table_id)
            normalized = self._validate_record_data(data, schema.fields, partial=False)
            now = time.time()
            record = AgentTableRecord(
                record_id=f"record_{uuid.uuid4().hex}",
                table_id=table_id,
                schema_version=schema.version,
                data=normalized,
                revision=1,
                created_at=now,
                updated_at=now,
            )
            connection.execute(
                """INSERT INTO agent_table_records
                   (table_id, record_id, schema_version, data_json, revision,
                    created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                self._record_parameters(record),
            )
            self._save_operation(
                connection, table_id, operation_id, "record.create", payload_hash, record
            )
            self._write_audit(
                connection,
                table_id=table.table_id,
                operation="record.create",
                record_id=record.record_id,
                schema_version=schema.version,
                affected_count=1,
            )
            return record.model_copy(deep=True)

    def update_record(
        self,
        table_id: str,
        record_id: str,
        *,
        revision: int,
        data: dict[str, Any],
        operation_id: str | None = None,
    ) -> AgentTableRecord:
        payload_hash = self._request_hash(
            "record.update",
            {"record_id": record_id, "revision": revision, "data": data},
        )
        with self._lock, self._connection(write=True) as connection:
            replay = self._load_operation(
                connection, table_id, operation_id, "record.update", payload_hash
            )
            if replay is not None:
                return AgentTableRecord.model_validate(replay)
            _, schema = self._writable_schema(connection, table_id)
            record = self._load_record(connection, table_id, record_id)
            if record.revision != revision:
                raise AgentTableConflictError(
                    f"Record revision conflict: expected {record.revision}, got {revision}."
                )
            merged = dict(record.data)
            merged.update(data)
            record.data = self._validate_record_data(
                merged, schema.fields, partial=False
            )
            record.schema_version = schema.version
            record.revision += 1
            record.updated_at = time.time()
            connection.execute(
                """UPDATE agent_table_records SET schema_version = ?, data_json = ?,
                   revision = ?, updated_at = ? WHERE table_id = ? AND record_id = ?""",
                (
                    record.schema_version,
                    self._json(record.data),
                    record.revision,
                    record.updated_at,
                    table_id,
                    record_id,
                ),
            )
            self._save_operation(
                connection, table_id, operation_id, "record.update", payload_hash, record
            )
            self._write_audit(
                connection,
                table_id=table_id,
                operation="record.update",
                record_id=record_id,
                schema_version=schema.version,
                affected_count=1,
            )
            return record.model_copy(deep=True)

    def delete_record(
        self,
        table_id: str,
        record_id: str,
        *,
        revision: int,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        payload_hash = self._request_hash(
            "record.delete", {"record_id": record_id, "revision": revision}
        )
        with self._lock, self._connection(write=True) as connection:
            replay = self._load_operation(
                connection, table_id, operation_id, "record.delete", payload_hash
            )
            if replay is not None:
                return replay
            _, schema = self._writable_schema(connection, table_id)
            record = self._load_record(connection, table_id, record_id)
            if record.revision != revision:
                raise AgentTableConflictError(
                    f"Record revision conflict: expected {record.revision}, got {revision}."
                )
            connection.execute(
                "DELETE FROM agent_table_records WHERE table_id = ? AND record_id = ?",
                (table_id, record_id),
            )
            result = {"deleted": True, "record_id": record_id}
            self._save_operation(
                connection, table_id, operation_id, "record.delete", payload_hash, result
            )
            self._write_audit(
                connection,
                table_id=table_id,
                operation="record.delete",
                record_id=record_id,
                schema_version=schema.version,
                affected_count=1,
            )
            return result

    def list_audit(
        self, table_id: str, *, limit: int = 100
    ) -> list[AgentTableAuditEntry]:
        with self._lock, self._connection() as connection:
            self._load_table(connection, table_id)
            rows = connection.execute(
                """SELECT * FROM agent_table_audit WHERE table_id = ?
                   ORDER BY created_at DESC, audit_id LIMIT ?""",
                (table_id, max(1, min(int(limit), 500))),
            ).fetchall()
        return [AgentTableAuditEntry.model_validate(dict(row)) for row in rows]

    def _writable_schema(
        self, connection: sqlite3.Connection, table_id: str
    ) -> tuple[AgentTableDefinition, AgentTableSchemaVersion]:
        table = self._load_table(connection, table_id)
        if table.status == "archived":
            raise AgentTableConflictError("Archived Agent Tables are read-only.")
        if table.active_schema_version is None:
            raise AgentTableConflictError(
                "Publish an Agent Table schema before writing records."
            )
        return table, self._load_schema_version(
            connection, table_id, table.active_schema_version
        )

    def _validation_issues(
        self, connection: sqlite3.Connection, table: AgentTableDefinition
    ) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        if not table.fields:
            issues.append(
                {"code": "fields_required", "message": "At least one field is required."}
            )
        try:
            self._normalize_fields(
                [field.model_dump() for field in table.fields], existing=table.fields
            )
        except AgentTableValidationError as exc:
            issues.append({"code": "invalid_fields", "message": str(exc)})
        if table.active_schema_version is not None:
            try:
                active = self._load_schema_version(
                    connection, table.table_id, table.active_schema_version
                )
                self._validate_compatible_evolution(active.fields, table.fields)
            except AgentTableValidationError as exc:
                issues.append({"code": "incompatible_schema", "message": str(exc)})
        return issues

    def _normalize_fields(
        self,
        values: list[dict[str, Any]],
        *,
        existing: list[AgentTableField] | None = None,
    ) -> list[AgentTableField]:
        if not isinstance(values, list):
            raise AgentTableValidationError("fields must be an array.")
        if len(values) > MAX_FIELDS:
            raise AgentTableValidationError(f"Agent Tables support at most {MAX_FIELDS} fields.")
        existing_by_id = {field.field_id: field for field in existing or []}
        existing_by_name = {field.name: field for field in existing or []}
        normalized: list[AgentTableField] = []
        names: set[str] = set()
        field_ids: set[str] = set()
        for raw in values:
            item = dict(raw)
            name = str(item.get("name") or "").strip()
            if not FIELD_NAME_PATTERN.fullmatch(name):
                raise AgentTableValidationError(
                    f"Invalid field name '{name or '<empty>'}'. Use ASCII letters, numbers, and underscores."
                )
            if name in SYSTEM_FIELDS:
                raise AgentTableValidationError(
                    f"Field name '{name}' is reserved for Agent Table metadata."
                )
            if name in names:
                raise AgentTableValidationError(f"Duplicate field name: {name}.")
            supplied_id = str(item.get("field_id") or "").strip()
            if supplied_id:
                previous = existing_by_id.get(supplied_id)
                if existing is not None and previous is None:
                    raise AgentTableValidationError(f"Unknown field ID: {supplied_id}.")
            else:
                previous = existing_by_name.get(name)
                supplied_id = previous.field_id if previous else f"field_{uuid.uuid4().hex}"
            if supplied_id in field_ids:
                raise AgentTableValidationError(f"Duplicate field ID: {supplied_id}.")
            item["field_id"] = supplied_id
            item["label"] = str(item.get("label") or "").strip()[:120]
            item["description"] = str(item.get("description") or "").strip()[:500]
            field = AgentTableField.model_validate(item)
            if field.has_default:
                field.default_value = self._validate_field_value(
                    field, field.default_value, path=f"default.{field.name}"
                )
            else:
                field.default_value = None
            normalized.append(field)
            names.add(name)
            field_ids.add(supplied_id)
        return normalized

    @staticmethod
    def _validate_compatible_evolution(
        published: list[AgentTableField], draft: list[AgentTableField]
    ) -> None:
        draft_by_id = {field.field_id: field for field in draft}
        published_ids = {field.field_id for field in published}
        for previous in published:
            current = draft_by_id.get(previous.field_id)
            if current is None:
                raise AgentTableValidationError(
                    f"Published field '{previous.name}' cannot be removed."
                )
            immutable = ("name", "data_type", "required", "has_default", "default_value")
            if any(getattr(previous, key) != getattr(current, key) for key in immutable):
                raise AgentTableValidationError(
                    f"Published field '{previous.name}' can only change its label or description."
                )
        for field in draft:
            if field.field_id not in published_ids and field.required and not field.has_default:
                raise AgentTableValidationError(
                    f"New required field '{field.name}' must define a default value."
                )

    def _validate_record_data(
        self,
        data: dict[str, Any],
        fields: list[AgentTableField],
        *,
        partial: bool,
    ) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise AgentTableValidationError("Record data must be an object.")
        field_by_name = {field.name: field for field in fields}
        unknown = sorted(set(data) - set(field_by_name))
        if unknown:
            raise AgentTableValidationError("Unknown record fields: " + ", ".join(unknown))
        normalized: dict[str, Any] = {}
        for field in fields:
            if field.name in data:
                value = data[field.name]
            elif partial:
                continue
            elif field.has_default:
                value = field.default_value
            elif field.required:
                raise AgentTableValidationError(f"Required field is missing: {field.name}.")
            else:
                continue
            if value is None and not field.required:
                normalized[field.name] = None
            elif value is None:
                raise AgentTableValidationError(f"Required field cannot be null: {field.name}.")
            else:
                normalized[field.name] = self._validate_field_value(
                    field, value, path=f"record.{field.name}"
                )
        encoded = self._json(normalized).encode("utf-8")
        if len(encoded) > MAX_RECORD_BYTES:
            raise AgentTableValidationError(
                f"Record body exceeds the {MAX_RECORD_BYTES} byte limit."
            )
        return normalized

    @staticmethod
    def _validate_field_value(field: AgentTableField, value: Any, *, path: str) -> Any:
        if field.data_type == "string":
            if not isinstance(value, str):
                raise AgentTableValidationError(f"{path} must be a string.")
            return value
        if field.data_type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise AgentTableValidationError(f"{path} must be an integer.")
            return value
        if field.data_type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise AgentTableValidationError(f"{path} must be a number.")
            if isinstance(value, float) and not math.isfinite(value):
                raise AgentTableValidationError(f"{path} must be a finite number.")
            return value
        if field.data_type == "boolean":
            if not isinstance(value, bool):
                raise AgentTableValidationError(f"{path} must be a boolean.")
            return value
        if field.data_type == "datetime":
            if not isinstance(value, str):
                raise AgentTableValidationError(f"{path} must be an ISO 8601 string.")
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise AgentTableValidationError(
                    f"{path} must be a valid ISO 8601 datetime."
                ) from exc
            return parsed.isoformat()
        if field.data_type == "json":
            try:
                return normalize_workflow_value(value, path=path)
            except ValueError as exc:
                raise AgentTableValidationError(str(exc)) from exc
        raise AgentTableValidationError(f"Unsupported field type: {field.data_type}.")

    def _load_table(
        self, connection: sqlite3.Connection, table_id: str
    ) -> AgentTableDefinition:
        row = connection.execute(
            "SELECT * FROM agent_table_definitions WHERE table_id = ?", (table_id,)
        ).fetchone()
        if row is None:
            raise AgentTableNotFoundError(f"Agent Table not found: {table_id}")
        return self._table_from_row(row)

    @staticmethod
    def _validate_selected_fields(
        fields: list[str] | None,
        schema_fields: list[AgentTableField],
    ) -> list[str]:
        allowed = {field.name for field in schema_fields}
        if not fields:
            return [field.name for field in schema_fields]
        if not isinstance(fields, list) or len(fields) > MAX_FIELDS:
            raise AgentTableValidationError(
                f"Query fields must contain at most {MAX_FIELDS} field names."
            )
        selected = [str(value).strip() for value in fields]
        unknown = sorted(set(selected) - allowed)
        if unknown:
            raise AgentTableValidationError(
                "Unknown query fields: " + ", ".join(unknown)
            )
        if len(selected) != len(set(selected)):
            raise AgentTableValidationError("Query fields cannot contain duplicates.")
        return selected

    def _matching_rows(
        self,
        connection: sqlite3.Connection,
        table_id: str,
        schema_fields: list[AgentTableField],
        filter_tree: dict[str, Any],
    ) -> list[sqlite3.Row]:
        parameters: list[Any] = []
        where = self._compile_filter(
            filter_tree,
            schema_fields,
            parameters,
            allow_empty=False,
        )
        return connection.execute(
            "SELECT * FROM agent_table_records WHERE table_id = ? AND ("
            + where
            + ") ORDER BY record_id LIMIT ?",
            [table_id, *parameters, MAX_MUTATION_ROWS + 1],
        ).fetchall()

    def _compile_filter(
        self,
        tree: dict[str, Any] | None,
        schema_fields: list[AgentTableField],
        parameters: list[Any],
        *,
        allow_empty: bool,
        depth: int = 0,
    ) -> str:
        if tree is None or tree == {}:
            if allow_empty:
                return "1 = 1"
            raise AgentTableValidationError(
                "Update and delete operations require a non-empty filter."
            )
        if not isinstance(tree, dict) or depth > 4:
            raise AgentTableValidationError("Invalid or overly deep filter tree.")
        if "items" in tree or "logic" in tree:
            logic = str(tree.get("logic") or "").lower()
            items = tree.get("items")
            if logic not in {"and", "or"} or not isinstance(items, list):
                raise AgentTableValidationError(
                    "Filter groups require logic=and|or and an items array."
                )
            if not 1 <= len(items) <= 20:
                raise AgentTableValidationError(
                    "Filter groups require between 1 and 20 items."
                )
            compiled = [
                self._compile_filter(
                    item,
                    schema_fields,
                    parameters,
                    allow_empty=False,
                    depth=depth + 1,
                )
                for item in items
            ]
            return "(" + f" {logic.upper()} ".join(compiled) + ")"

        field_name = str(tree.get("field") or "").strip()
        operator = str(tree.get("operator") or "").strip().lower()
        fields_by_name = {field.name: field for field in schema_fields}
        if field_name not in fields_by_name and field_name not in SYSTEM_FIELDS:
            raise AgentTableValidationError(
                f"Unknown filter field: {field_name or '<empty>'}."
            )
        if operator not in FILTER_OPERATORS:
            raise AgentTableValidationError(
                f"Unsupported filter operator: {operator or '<empty>'}."
            )
        expression = self._field_expression(field_name, parameters)
        if operator == "is_null":
            return f"{expression} IS NULL"
        if "value" not in tree:
            raise AgentTableValidationError(
                f"Filter operator {operator} requires a value."
            )
        raw_value = tree.get("value")
        if operator == "in":
            if not isinstance(raw_value, list) or not 1 <= len(raw_value) <= 100:
                raise AgentTableValidationError(
                    "The in operator requires an array with 1 to 100 values."
                )
            values = [
                self._normalize_filter_value(field_name, value, fields_by_name)
                for value in raw_value
            ]
            parameters.extend(values)
            return f"{expression} IN ({', '.join('?' for _ in values)})"
        value = self._normalize_filter_value(
            field_name, raw_value, fields_by_name
        )
        if operator == "contains":
            if not isinstance(value, str):
                raise AgentTableValidationError(
                    "The contains operator requires a string value."
                )
            parameters.append(value)
            return f"INSTR(CAST({expression} AS TEXT), ?) > 0"
        sql_operator = {
            "eq": "=",
            "ne": "!=",
            "gt": ">",
            "gte": ">=",
            "lt": "<",
            "lte": "<=",
        }[operator]
        parameters.append(value)
        return f"{expression} {sql_operator} ?"

    def _compile_sort(
        self,
        items: list[dict[str, Any]],
        schema_fields: list[AgentTableField],
        parameters: list[Any],
    ) -> str:
        if not isinstance(items, list) or len(items) > 5:
            raise AgentTableValidationError("Sort supports at most 5 fields.")
        allowed = {field.name for field in schema_fields} | SYSTEM_FIELDS
        compiled: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                raise AgentTableValidationError("Sort items must be objects.")
            field_name = str(item.get("field") or "").strip()
            direction = str(item.get("direction") or "asc").lower()
            if field_name not in allowed or direction not in {"asc", "desc"}:
                raise AgentTableValidationError("Invalid sort field or direction.")
            compiled.append(
                f"{self._field_expression(field_name, parameters)} {direction.upper()}"
            )
        if not compiled:
            return "ORDER BY updated_at DESC, record_id"
        return "ORDER BY " + ", ".join(compiled) + ", record_id"

    @staticmethod
    def _field_expression(field_name: str, parameters: list[Any]) -> str:
        if field_name in SYSTEM_FIELDS:
            return field_name
        parameters.append(f"$.{field_name}")
        return "json_extract(data_json, ?)"

    def _normalize_filter_value(
        self,
        field_name: str,
        value: Any,
        fields_by_name: dict[str, AgentTableField],
    ) -> Any:
        if value is None:
            raise AgentTableValidationError(
                "Use is_null instead of comparing filter values to null."
            )
        if field_name == "record_id":
            if not isinstance(value, str):
                raise AgentTableValidationError("record_id filters require a string.")
            return value
        if field_name in {"created_at", "updated_at"}:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise AgentTableValidationError(
                    f"{field_name} filters require a numeric timestamp."
                )
            return value
        if field_name == "revision":
            if isinstance(value, bool) or not isinstance(value, int):
                raise AgentTableValidationError("revision filters require an integer.")
            return value
        normalized = self._validate_field_value(
            fields_by_name[field_name], value, path=f"filter.{field_name}"
        )
        if isinstance(normalized, (dict, list)):
            return self._json(normalized)
        return normalized

    @staticmethod
    def _flatten_record(
        record: AgentTableRecord, selected_fields: list[str]
    ) -> dict[str, Any]:
        return {
            "record_id": record.record_id,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "revision": record.revision,
            **{
                name: record.data.get(name)
                for name in selected_fields
                if name in record.data
            },
        }

    def _load_versions(
        self, connection: sqlite3.Connection, table_id: str
    ) -> list[AgentTableSchemaVersion]:
        rows = connection.execute(
            """SELECT * FROM agent_table_schema_versions WHERE table_id = ?
               ORDER BY version""",
            (table_id,),
        ).fetchall()
        return [self._schema_from_row(row) for row in rows]

    def _load_schema_version(
        self, connection: sqlite3.Connection, table_id: str, version: int
    ) -> AgentTableSchemaVersion:
        row = connection.execute(
            """SELECT * FROM agent_table_schema_versions
               WHERE table_id = ? AND version = ?""",
            (table_id, version),
        ).fetchone()
        if row is None:
            raise AgentTableNotFoundError(
                f"Agent Table schema version not found: {table_id}@{version}"
            )
        return self._schema_from_row(row)

    def _load_record(
        self, connection: sqlite3.Connection, table_id: str, record_id: str
    ) -> AgentTableRecord:
        row = connection.execute(
            """SELECT * FROM agent_table_records
               WHERE table_id = ? AND record_id = ?""",
            (table_id, record_id),
        ).fetchone()
        if row is None:
            raise AgentTableNotFoundError(f"Agent Table record not found: {record_id}")
        return self._record_from_row(row)

    def _load_operation(
        self,
        connection: sqlite3.Connection,
        table_id: str,
        operation_id: str | None,
        operation: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        if not operation_id:
            return None
        self._validate_operation_id(operation_id)
        row = connection.execute(
            """SELECT operation, request_hash, response_json
               FROM agent_table_operations
               WHERE table_id = ? AND operation_id = ?""",
            (table_id, operation_id),
        ).fetchone()
        if row is None:
            return None
        if row["operation"] != operation or row["request_hash"] != request_hash:
            raise AgentTableConflictError(
                "The operation ID was already used with a different request."
            )
        return json.loads(row["response_json"])

    def _save_operation(
        self,
        connection: sqlite3.Connection,
        table_id: str,
        operation_id: str | None,
        operation: str,
        request_hash: str,
        response: AgentTableRecord | dict[str, Any],
    ) -> None:
        if not operation_id:
            return
        self._validate_operation_id(operation_id)
        payload = response.model_dump() if isinstance(response, AgentTableRecord) else response
        connection.execute(
            """INSERT INTO agent_table_operations
               (table_id, operation_id, operation, request_hash, response_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (table_id, operation_id, operation, request_hash, self._json(payload), time.time()),
        )

    def _write_audit(
        self,
        connection: sqlite3.Connection,
        *,
        table_id: str,
        operation: str,
        record_id: str | None = None,
        schema_version: int | None = None,
        affected_count: int = 0,
    ) -> None:
        connection.execute(
            """INSERT INTO agent_table_audit
               (audit_id, table_id, operation, record_id, schema_version,
                affected_count, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                f"audit_{uuid.uuid4().hex}",
                table_id,
                operation,
                record_id,
                schema_version,
                affected_count,
                time.time(),
            ),
        )

    @staticmethod
    def _table_from_row(row: sqlite3.Row) -> AgentTableDefinition:
        return AgentTableDefinition(
            table_id=row["table_id"],
            name=row["name"],
            description=row["description"],
            status=row["status"],
            draft_revision=row["draft_revision"],
            active_schema_version=row["active_schema_version"],
            fields=[AgentTableField.model_validate(value) for value in json.loads(row["fields_json"])],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _schema_from_row(row: sqlite3.Row) -> AgentTableSchemaVersion:
        return AgentTableSchemaVersion(
            table_id=row["table_id"],
            version=row["version"],
            draft_revision=row["draft_revision"],
            fields=[AgentTableField.model_validate(value) for value in json.loads(row["fields_json"])],
            checksum=row["checksum"],
            published_at=row["published_at"],
        )

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> AgentTableRecord:
        return AgentTableRecord(
            record_id=row["record_id"],
            table_id=row["table_id"],
            schema_version=row["schema_version"],
            data=json.loads(row["data_json"]),
            revision=row["revision"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _persist_table(
        self, connection: sqlite3.Connection, table: AgentTableDefinition
    ) -> None:
        connection.execute(
            """UPDATE agent_table_definitions SET name = ?, description = ?,
               status = ?, draft_revision = ?, active_schema_version = ?,
               fields_json = ?, updated_at = ? WHERE table_id = ?""",
            (
                table.name,
                table.description,
                table.status,
                table.draft_revision,
                table.active_schema_version,
                self._json([field.model_dump() for field in table.fields]),
                table.updated_at,
                table.table_id,
            ),
        )

    def _table_parameters(self, table: AgentTableDefinition) -> tuple[Any, ...]:
        return (
            table.table_id,
            table.name,
            table.description,
            table.status,
            table.draft_revision,
            table.active_schema_version,
            self._json([field.model_dump() for field in table.fields]),
            table.created_at,
            table.updated_at,
        )

    def _record_parameters(self, record: AgentTableRecord) -> tuple[Any, ...]:
        return (
            record.table_id,
            record.record_id,
            record.schema_version,
            self._json(record.data),
            record.revision,
            record.created_at,
            record.updated_at,
        )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _request_hash(self, operation: str, payload: dict[str, Any]) -> str:
        return hashlib.sha256(
            self._json({"operation": operation, "payload": payload}).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _required_text(value: Any, label: str, limit: int) -> str:
        text = str(value or "").strip()
        if not text:
            raise AgentTableValidationError(f"{label} is required.")
        return text[:limit]

    @staticmethod
    def _require_revision(table: AgentTableDefinition, revision: int) -> None:
        if table.draft_revision != revision:
            raise AgentTableConflictError(
                f"Agent Table revision conflict: expected {table.draft_revision}, got {revision}."
            )

    @staticmethod
    def _require_editable(table: AgentTableDefinition) -> None:
        if table.status == "archived":
            raise AgentTableConflictError("Archived Agent Tables cannot be edited.")

    @staticmethod
    def _validate_operation_id(operation_id: str) -> None:
        if not operation_id.strip() or len(operation_id) > 160:
            raise AgentTableValidationError("operation_id must contain 1 to 160 characters.")


class AgentTableStore:
    """Backend-neutral Agent Table facade; SQLite is the first implementation."""

    def __init__(
        self,
        storage_dir: str | Path | None = None,
        *,
        backend: AgentTableBackend | None = None,
    ) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("AGENT_TABLE_STORAGE_DIR", "").strip()
            or os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
            or package_dir / "storage"
        )
        self.backend: AgentTableBackend = backend or SQLiteAgentTableBackend(
            self.storage_dir
        )

    @property
    def backend_name(self) -> str:
        return self.backend.backend_name

    def __getattr__(self, name: str) -> Any:
        return getattr(self.backend, name)
