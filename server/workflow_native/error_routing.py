from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal, Mapping


FailureClassification = Literal["transient", "permanent"]
ROUTABLE_NODE_KINDS = frozenset(
    {"http_request", "data_table_query", "knowledge_retrieval"}
)
FAILURE_ACTIONS = frozenset({"stop", "error_output"})


class WorkflowErrorRoutingConfigError(ValueError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


@dataclass(frozen=True, slots=True)
class RoutedNodeFailure:
    code: str
    classification: FailureClassification
    safe_message: str


def failure_action(data: Mapping[str, Any]) -> str:
    return str(data.get("failureAction") or "stop").strip()


def validate_error_routing_config(
    data: Mapping[str, Any],
    *,
    node_kind: str,
    output_variable: str,
) -> None:
    if node_kind not in ROUTABLE_NODE_KINDS:
        raise WorkflowErrorRoutingConfigError(
            "NODE_ERROR_ROUTING_UNSUPPORTED",
            "This node does not support a structured error output.",
        )
    action = failure_action(data)
    if action not in FAILURE_ACTIONS:
        raise WorkflowErrorRoutingConfigError(
            "NODE_FAILURE_ACTION_INVALID",
            "Failure handling must stop the workflow or use the error output.",
        )
    raw_error_variable = data.get("errorVariable")
    error_variable = str(raw_error_variable or "").strip()
    if action == "stop":
        if raw_error_variable not in {None, ""}:
            raise WorkflowErrorRoutingConfigError(
                "NODE_ERROR_VARIABLE_UNUSED",
                "Remove the error variable while failure handling stops the workflow.",
            )
        return
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", error_variable):
        raise WorkflowErrorRoutingConfigError(
            "NODE_ERROR_VARIABLE_INVALID",
            "Error output needs a valid workflow variable name.",
        )
    if error_variable == output_variable:
        raise WorkflowErrorRoutingConfigError(
            "NODE_ERROR_VARIABLE_CONFLICT",
            "Error and success outputs must use different variables.",
        )


def build_error_receipt(
    failure: RoutedNodeFailure,
    *,
    node_id: str,
    node_kind: str,
    attempts: int = 1,
    exhausted: bool = True,
) -> dict[str, Any]:
    return {
        "status": "failed",
        "code": failure.code,
        "classification": failure.classification,
        "nodeId": node_id,
        "nodeKind": node_kind,
        "attempts": attempts,
        "exhausted": exhausted,
        "message": failure.safe_message,
    }


_HTTP_ROUTABLE: dict[str, tuple[FailureClassification, str]] = {
    "HTTP_TIMEOUT": ("transient", "HTTP request timed out."),
    "HTTP_NETWORK_ERROR": ("transient", "HTTP request could not reach the remote service."),
    "HTTP_STATUS_NOT_SUCCESSFUL": ("permanent", "HTTP service returned an unsuccessful status."),
    "HTTP_RESPONSE_TOO_LARGE": ("permanent", "HTTP response exceeded the configured size limit."),
    "HTTP_RESPONSE_NOT_UTF8": ("permanent", "HTTP response was not supported UTF-8 text."),
    "HTTP_RESPONSE_JSON_INVALID": ("permanent", "HTTP response was not valid JSON."),
    "HTTP_BINARY_RESPONSE_FORBIDDEN": ("permanent", "HTTP response used an unsupported binary format."),
}

_HTTP_PERMISSION_STATUS_CODES = frozenset({401, 403})
_KNOWLEDGE_TRANSIENT_CODES = frozenset(
    {
        "rag_vector_backend_unavailable",
        "rag_vector_index_unavailable",
        "rag_fulltext_index_unavailable",
    }
)
SAFE_ROUTABLE_ERROR_CODES = frozenset(
    {
        *_HTTP_ROUTABLE,
        "DATA_TABLE_QUERY_BUSY",
        "KNOWLEDGE_RETRIEVAL_UNAVAILABLE",
    }
)


def route_http_error(error: Any) -> RoutedNodeFailure | None:
    code = str(getattr(error, "code", "") or "")
    spec = _HTTP_ROUTABLE.get(code)
    if spec is None:
        return None
    classification, message = spec
    status_code = getattr(error, "status_code", None)
    if code == "HTTP_STATUS_NOT_SUCCESSFUL":
        if status_code in _HTTP_PERMISSION_STATUS_CODES:
            return None
        if status_code in {408, 429, 502, 503, 504}:
            classification = "transient"
    return RoutedNodeFailure(code, classification, message)


def safe_http_fatal_message(error: Any) -> str:
    code = str(getattr(error, "code", "") or "")
    if "CREDENTIAL" in code or code == "HTTP_BASIC_CREDENTIAL_INVALID":
        return "The selected HTTP credential is unavailable or invalid."
    if code.startswith("HTTP_"):
        return "HTTP request was blocked by configuration or security policy."
    return "HTTP request failed."


def route_data_table_error(error: BaseException) -> RoutedNodeFailure | None:
    # SQLite exposes lock waits and busy timeouts as OperationalError. No other
    # database exception is safe to classify as an expected runtime failure.
    try:
        import sqlite3

        if isinstance(error, sqlite3.OperationalError):
            primary_code = getattr(error, "sqlite_errorcode", None)
            if isinstance(primary_code, int):
                primary_code &= 0xFF
            detail = " ".join(str(error).lower().split())
            lock_messages = {
                "database is locked",
                "database table is locked",
                "database schema is locked",
                "database is busy",
                "database table is busy",
                "database schema is busy",
            }
            if primary_code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or detail in lock_messages:
                return RoutedNodeFailure(
                    "DATA_TABLE_QUERY_BUSY",
                    "transient",
                    "Data table query was temporarily unavailable.",
                )
    except Exception:
        return None
    return None


def is_rag_retrieval_unavailable_error(error: BaseException) -> bool:
    """Accept only the RAG layer's concrete availability exception type."""

    try:
        from server.rag.rag_service import RagRetrievalUnavailableError
    except ModuleNotFoundError:
        from rag.rag_service import RagRetrievalUnavailableError
    return isinstance(error, RagRetrievalUnavailableError)


def route_knowledge_error(error: BaseException) -> RoutedNodeFailure | None:
    # Only the RAG layer's explicit, content-free availability errors are
    # routable. Missing resources, managed provider failures, and unknown
    # exceptions remain fatal.
    if not is_rag_retrieval_unavailable_error(error):
        return None
    code = str(getattr(error, "code", "") or "")
    if code not in _KNOWLEDGE_TRANSIENT_CODES:
        return None
    return RoutedNodeFailure(
        "KNOWLEDGE_RETRIEVAL_UNAVAILABLE",
        "transient",
        "Knowledge retrieval was temporarily unavailable.",
    )
