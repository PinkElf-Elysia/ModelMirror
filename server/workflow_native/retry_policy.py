from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
import sqlite3
from typing import Any, TYPE_CHECKING

from .error_routing import (
    RoutedNodeFailure,
    is_rag_retrieval_unavailable_error,
    route_http_error,
)

if TYPE_CHECKING:
    from .node_contracts import NodeContract


RETRY_MODE_NONE = "none"
RETRY_MODE_TRANSIENT = "transient"
RETRY_MAX_ATTEMPTS = (2, 3)
RETRYABLE_NODE_KINDS = frozenset(
    {"http_request", "data_table_query", "knowledge_retrieval"}
)
RETRY_SAFE_ERROR_CODES: dict[str, frozenset[str]] = {
    "http_request": frozenset(
        {"HTTP_TIMEOUT", "HTTP_NETWORK_ERROR", "HTTP_STATUS_NOT_SUCCESSFUL"}
    ),
    "data_table_query": frozenset({"DATA_TABLE_QUERY_BUSY"}),
    "knowledge_retrieval": frozenset(
        {"KNOWLEDGE_RETRIEVAL_BUSY", "KNOWLEDGE_RETRIEVAL_UNAVAILABLE"}
    ),
}
_HTTP_RETRYABLE_CODES = frozenset({"HTTP_TIMEOUT", "HTTP_NETWORK_ERROR"})
_HTTP_RETRYABLE_STATUSES = frozenset({408, 429, 502, 503, 504})
_KNOWLEDGE_VECTOR_BACKEND_CODE = "rag_vector_backend_unavailable"
_HASH_EMBEDDING_MODEL = "deterministic-hash-v1"


class WorkflowRetryPolicyError(ValueError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


def retry_mode(data: Mapping[str, Any]) -> str:
    value = data.get("retryMode")
    return RETRY_MODE_NONE if value is None or value == "" else str(value)


def retry_max_attempts(data: Mapping[str, Any]) -> int:
    value = data.get("maxAttempts")
    if value is None or value == "":
        return 2
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def retry_enabled(data: Mapping[str, Any]) -> bool:
    return retry_mode(data) == RETRY_MODE_TRANSIENT


retry_is_enabled = retry_enabled


def workflow_node_retries_enabled() -> bool:
    return os.getenv("WORKFLOW_NODE_RETRIES_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def retry_error_code_is_safe(node_kind: str, error_code: object) -> bool:
    return str(error_code or "") in RETRY_SAFE_ERROR_CODES.get(node_kind, frozenset())


def effective_can_wait(data: Mapping[str, Any], contract: "NodeContract") -> bool:
    """Return the configuration-aware durable-wait capability.

    Feature flags deliberately do not participate in this decision. A disabled
    runtime feature must not make a graph pass an entrypoint or subworkflow
    safety gate that it would fail once the feature is enabled.
    """

    return bool(
        contract.execution.can_wait
        or (contract.retry.supported and retry_enabled(data))
    )


def validate_retry_config(
    data: Mapping[str, Any],
    node_kind: str,
) -> None:
    mode = retry_mode(data)
    if mode not in {RETRY_MODE_NONE, RETRY_MODE_TRANSIENT}:
        raise WorkflowRetryPolicyError(
            "INVALID_NODE_RETRY_MODE",
            "retryMode must be none or transient.",
        )

    attempts = retry_max_attempts(data)
    if attempts not in RETRY_MAX_ATTEMPTS:
        raise WorkflowRetryPolicyError(
            "INVALID_NODE_RETRY_MAX_ATTEMPTS",
            "maxAttempts must be 2 or 3 and includes the first attempt.",
        )

    if mode == RETRY_MODE_NONE:
        return
    if node_kind not in RETRYABLE_NODE_KINDS:
        raise WorkflowRetryPolicyError(
            "NODE_RETRY_UNSUPPORTED",
            "This node does not support durable retry.",
        )

    validate_static_retry_eligibility(data, node_kind=node_kind)


def validate_static_retry_eligibility(
    data: Mapping[str, Any],
    node_kind: str,
) -> None:
    if not retry_enabled(data):
        return
    if node_kind not in RETRYABLE_NODE_KINDS:
        raise WorkflowRetryPolicyError(
            "NODE_RETRY_UNSUPPORTED",
            "This node does not support durable retry.",
        )
    if node_kind == "http_request":
        if data.get("contractVersion") != 2:
            raise WorkflowRetryPolicyError(
                "NODE_RETRY_HTTP_V2_REQUIRED",
                "HTTP retry requires the V2 HTTP request contract.",
            )
        if str(data.get("method") or "GET").strip().upper() != "GET":
            raise WorkflowRetryPolicyError(
                "NODE_RETRY_HTTP_GET_REQUIRED",
                "HTTP retry is limited to fixed GET requests.",
            )
        if str(data.get("bodyMode") or "none").strip() != "none":
            raise WorkflowRetryPolicyError(
                "NODE_RETRY_HTTP_BODY_FORBIDDEN",
                "HTTP retry does not allow a request body.",
            )
        return

    if node_kind == "data_table_query":
        return

    if node_kind == "knowledge_retrieval":
        if data.get("contractVersion") != 2:
            raise WorkflowRetryPolicyError(
                "NODE_RETRY_KNOWLEDGE_V2_REQUIRED",
                "Knowledge retry requires the V2 retrieval contract.",
            )
        # Whether the current active version is local fulltext/hash retrieval
        # cannot be proven from graph data. Publish, activation and runtime
        # must revalidate that target and bind its safe fingerprint.
        return

    raise WorkflowRetryPolicyError(
        "NODE_RETRY_UNSUPPORTED",
        "This node does not support durable retry.",
    )


def validate_retry_configuration(
    data: Mapping[str, Any],
    *,
    node_kind: str,
    contract: "NodeContract",
) -> None:
    """Compatibility wrapper used by graph validation.

    The contract check prevents a future registry drift from enabling a kind in
    one layer only; runtime and deployment callers can use the two stable
    validators above without importing the registry.
    """

    has_explicit_retry_config = (
        data.get("retryMode") not in {None, ""}
        or data.get("maxAttempts") not in {None, ""}
    )
    if has_explicit_retry_config and not contract.retry.supported:
        raise WorkflowRetryPolicyError(
            "NODE_RETRY_UNSUPPORTED",
            "This node does not support durable retry.",
        )
    validate_retry_config(data, node_kind=node_kind)


def retry_wait_id(task_id: str, node_id: str, next_attempt: int) -> str:
    if not task_id or not node_id or next_attempt not in RETRY_MAX_ATTEMPTS:
        raise WorkflowRetryPolicyError(
            "NODE_RETRY_WAIT_ID_INVALID",
            "Retry wait identity is invalid.",
        )
    digest = hashlib.sha256(
        f"{task_id}:{node_id}:{next_attempt}".encode("utf-8")
    ).hexdigest()
    return f"node_retry:{digest}"


def retry_delay_seconds(
    next_attempt: int,
    retry_after_seconds: int | None = None,
) -> int:
    if next_attempt == 2:
        fixed = 5
    elif next_attempt == 3:
        fixed = 30
    else:
        raise WorkflowRetryPolicyError(
            "NODE_RETRY_ATTEMPT_INVALID",
            "Retry attempt must be 2 or 3.",
        )
    if isinstance(retry_after_seconds, bool) or not isinstance(
        retry_after_seconds, (int, type(None))
    ):
        retry_after_seconds = None
    bounded = (
        min(300, max(0, retry_after_seconds))
        if isinstance(retry_after_seconds, int)
        else 0
    )
    return max(fixed, bounded)


def _sqlite_busy_or_locked(error: BaseException) -> bool:
    if not isinstance(error, sqlite3.OperationalError):
        return False
    primary_code = getattr(error, "sqlite_errorcode", None)
    return isinstance(primary_code, int) and (primary_code & 0xFF) in {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
    }


def strict_retry_failure(
    node_kind: str,
    error: BaseException,
) -> RoutedNodeFailure | None:
    """Classify only the explicitly retryable, content-free failure subset."""

    if node_kind == "http_request":
        code = str(getattr(error, "code", "") or "")
        status_code = getattr(error, "status_code", None)
        if code in _HTTP_RETRYABLE_CODES or (
            code == "HTTP_STATUS_NOT_SUCCESSFUL"
            and status_code in _HTTP_RETRYABLE_STATUSES
        ):
            routed = route_http_error(error)
            return (
                routed
                if routed is not None and routed.classification == "transient"
                else None
            )
        return None
    if node_kind == "data_table_query" and _sqlite_busy_or_locked(error):
        return RoutedNodeFailure(
            "DATA_TABLE_QUERY_BUSY",
            "transient",
            "Data table query was temporarily unavailable.",
        )
    if node_kind == "knowledge_retrieval":
        if _sqlite_busy_or_locked(error):
            return RoutedNodeFailure(
                "KNOWLEDGE_RETRIEVAL_BUSY",
                "transient",
                "Knowledge retrieval was temporarily unavailable.",
            )
        if is_rag_retrieval_unavailable_error(error) and str(
            getattr(error, "code", "") or ""
        ) == _KNOWLEDGE_VECTOR_BACKEND_CODE:
            return RoutedNodeFailure(
                "KNOWLEDGE_RETRIEVAL_UNAVAILABLE",
                "transient",
                "Knowledge retrieval was temporarily unavailable.",
            )
    return None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def validate_knowledge_retry_evidence(
    kb_id: str,
    active_version: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> str:
    """Validate safe RAG metadata and return a stable retry target fingerprint.

    Callers remain responsible for obtaining both mappings from the RAG
    service under its normal authorization boundary. This function performs no
    service or content access.
    """

    clean_kb_id = str(kb_id or "").strip()
    version_id = str(active_version.get("version_id") or "").strip()
    evidence_declares_target = any(
        key in evidence for key in ("kb_id", "version_id")
    )
    evidence_kb_id = str(evidence.get("kb_id") or "").strip()
    evidence_version_id = str(evidence.get("version_id") or "").strip()
    if (
        not clean_kb_id
        or str(active_version.get("kb_id") or "").strip() != clean_kb_id
        or not version_id
        or not (
            active_version.get("status") == "active"
            or active_version.get("active") is True
        )
        or (
            evidence_declares_target
            and (
                evidence_kb_id != clean_kb_id
                or evidence_version_id != version_id
            )
        )
    ):
        raise WorkflowRetryPolicyError(
            "NODE_RETRY_KNOWLEDGE_TARGET_INVALID",
            "The active knowledge version is unavailable for retry.",
        )

    retrieval = _mapping(
        active_version.get("retrieval_profile") or evidence.get("retrieval")
    )
    mode = str(retrieval.get("mode") or "").strip()
    if mode not in {"fulltext", "vector", "hybrid"}:
        raise WorkflowRetryPolicyError(
            "NODE_RETRY_KNOWLEDGE_MODE_INELIGIBLE",
            "Knowledge retry requires local full-text or hash retrieval.",
        )
    if bool(retrieval.get("rerank_enabled")) or str(
        retrieval.get("rerank_provider") or "none"
    ).strip() not in {"", "none"}:
        raise WorkflowRetryPolicyError(
            "NODE_RETRY_KNOWLEDGE_RERANK_FORBIDDEN",
            "Knowledge retry does not allow remote reranking.",
        )

    embedding_provider = "none"
    embedding_model = ""
    embedding_fingerprint = ""
    if mode == "fulltext":
        if active_version.get("lexical_index_ready") is not True:
            raise WorkflowRetryPolicyError(
                "NODE_RETRY_KNOWLEDGE_INDEX_NOT_READY",
                "The local knowledge index is not ready for retry.",
            )
    else:
        embedding = _mapping(
            active_version.get("embedding_profile") or evidence.get("embedding")
        )
        effective = _mapping(embedding.get("effective")) or embedding
        embedding_provider = str(effective.get("provider") or "").strip()
        embedding_model = str(effective.get("model") or "").strip()
        embedding_fingerprint = str(
            embedding.get("embedding_space_fingerprint")
            or active_version.get("embedding_space_fingerprint")
            or ""
        ).strip()
        runtime_readiness = _mapping(
            evidence.get("runtime_vector_backend_readiness")
            or evidence.get("vector_backend_readiness")
            or active_version.get("vector_backend_readiness")
        )
        if (
            embedding_provider != "hash"
            or embedding_model != _HASH_EMBEDDING_MODEL
            or effective.get("ready") is not True
            or active_version.get("vector_index_ready") is not True
            or runtime_readiness.get("ready") is not True
        ):
            raise WorkflowRetryPolicyError(
                "NODE_RETRY_KNOWLEDGE_TARGET_INELIGIBLE",
                "Knowledge retry requires a ready local hash retrieval target.",
            )

    safe_target = {
        "kb_id": clean_kb_id,
        "version_id": version_id,
        "mode": mode,
        "embedding_provider": embedding_provider,
        "embedding_model": embedding_model,
        "embedding_space_fingerprint": embedding_fingerprint,
        "index_schema_version": int(active_version.get("index_schema_version") or 0),
        "index_contract": _mapping(
            active_version.get("index_contract") or evidence.get("index_contract")
        ),
    }
    encoded = json.dumps(
        safe_target,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
