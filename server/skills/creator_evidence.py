from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

try:
    from server.xpert_runtime.execution_store import (
        WorkflowExecution,
        WorkflowExecutionStore,
    )
    from server.xperts.context import XpertContextStore
except ModuleNotFoundError:
    from xpert_runtime.execution_store import WorkflowExecution, WorkflowExecutionStore
    from xperts.context import XpertContextStore


CREATOR_EVIDENCE_VERSION = "creator-evidence-v1"
CreatorEvidenceSourceKind = Literal["workflow_classic", "xpert_chat"]
CreatorEvidenceCandidateKind = Literal[
    "intent_summary",
    "successful_steps",
    "tool_names",
    "user_correction",
    "io_shape",
    "final_output_excerpt",
]

_SENSITIVE_KEY = re.compile(
    r"(?:api.?key|token|secret|password|authorization|cookie|credential|private.?key)",
    re.IGNORECASE,
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|token|secret|password|authorization|credential)"
    r"\s*[:=]\s*(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|(?:bearer\s+)?[^,;\r\n]+)"
)
_ENV_SECRET_ASSIGNMENT = re.compile(
    r"\b[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)\s*=\s*"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^,;\r\n]+)"
)
_BEARER_SECRET = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/-]{8,}")
_TOKEN_SECRET = re.compile(
    r"\b(?:sk-[a-zA-Z0-9_-]{8,}|ghp_[a-zA-Z0-9_]{8,}|"
    r"github_pat_[a-zA-Z0-9_]{8,}|xox[baprs]-[a-zA-Z0-9_-]{8,}|"
    r"AKIA[A-Z0-9]{8,})\b"
)
_PEM_SECRET = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_URL_CREDENTIALS = re.compile(r"(?i)\bhttps?://[^\s/@:]+:[^\s/@]+@")
_WINDOWS_PATH = re.compile(
    r"(?i)(?<![a-z0-9_])[a-z]:(?:\\|/)[^\r\n,;|\]\[(){}<>\"']+"
)
_UNC_PATH = re.compile(
    r"(?i)(?<![a-z0-9_])\\\\[^\\\r\n,;|\]\[(){}<>\"']+"
    r"\\[^\r\n,;|\]\[(){}<>\"']+"
)
_POSIX_PATH = re.compile(
    r"(?<![a-zA-Z0-9_])/(?:home|users|root|tmp|var|etc|opt|mnt|workspace|"
    r"volumes|srv|private|usr|run|data)"
    r"(?:/[^\r\n,;|\]\[(){}<>\"']+)+",
    re.IGNORECASE,
)
_CORRECTION_MARKER = re.compile(
    r"(?:更正|纠正|不是.+而是|请改|改成|应该是|actually|instead|correction|"
    r"change\s+(?:that|it)|not\s+.+but)",
    re.IGNORECASE,
)


class CreatorEvidenceError(ValueError):
    """Fail-closed evidence lookup error with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)


@dataclass(frozen=True, slots=True)
class CreatorEvidenceCandidate:
    candidate_id: str
    kind: CreatorEvidenceCandidateKind
    title: str
    summary: str
    content_hash: str
    default_selected: bool

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CreatorEvidencePreview:
    version: str
    source_kind: CreatorEvidenceSourceKind
    source_task_id: str
    source_run_id: str
    source_title: str
    source_xpert_id: str | None
    source_conversation_id: str | None
    source_message_id: str | None
    preview_fingerprint: str
    candidates: tuple[CreatorEvidenceCandidate, ...]

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["candidates"] = [item.to_payload() for item in self.candidates]
        return payload


def build_creator_evidence_preview(
    execution_store: WorkflowExecutionStore,
    *,
    source_kind: CreatorEvidenceSourceKind | str,
    source_task_id: str,
    source_run_id: str,
    context_store: XpertContextStore | None = None,
    source_xpert_id: str | None = None,
    source_conversation_id: str | None = None,
    source_message_id: str | None = None,
) -> CreatorEvidencePreview:
    """Build a bounded preview from a trusted completed runtime execution.

    The returned payload never includes a full conversation, tool arguments, raw
    tool output, attachments, environment values, or physical file paths.
    """

    clean_kind = str(source_kind or "").strip()
    if clean_kind not in {"workflow_classic", "xpert_chat"}:
        raise CreatorEvidenceError(
            "source_not_supported",
            "Only classic workflow and private Xpert Chat runs can become Skill evidence.",
        )
    task_id = _required_identifier(source_task_id, "source_task_id")
    run_id = _required_identifier(source_run_id, "source_run_id")
    execution = execution_store.get(task_id)
    if execution is None:
        raise CreatorEvidenceError("source_not_found", "The source execution was not found.")
    _validate_execution(
        execution,
        source_kind=clean_kind,
        source_run_id=run_id,
    )

    bound_messages: list[Any] = []
    xpert_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    source_title = _workflow_title(execution)
    if clean_kind == "xpert_chat":
        if context_store is None:
            raise CreatorEvidenceError(
                "xpert_context_unavailable",
                "The Xpert conversation store is unavailable.",
            )
        xpert_id = _required_identifier(source_xpert_id, "source_xpert_id")
        conversation_id = _required_identifier(
            source_conversation_id,
            "source_conversation_id",
        )
        message_id = _required_identifier(source_message_id, "source_message_id")
        metadata = execution.runtime_metadata
        if (
            str(metadata.get("xpert_id") or "") != xpert_id
            or str(metadata.get("conversation_id") or "") != conversation_id
        ):
            raise CreatorEvidenceError(
                "xpert_source_mismatch",
                "The Xpert or conversation does not match the source execution.",
            )
        if any(
            metadata.get(key)
            for key in (
                "app_id",
                "agent_task_id",
                "handoff_id",
                "goal_id",
                "evaluation_run_id",
                "automation_execution_id",
                "external_xpert_source_id",
            )
        ):
            raise CreatorEvidenceError(
                "source_not_private_chat",
                "Only a direct private Xpert Chat run can become Skill evidence.",
            )
        try:
            conversation = context_store.get_conversation(xpert_id, conversation_id)
        except Exception as exc:
            raise CreatorEvidenceError(
                "xpert_source_not_found",
                "The linked Xpert conversation was not found.",
            ) from exc
        selected_message = next(
            (item for item in conversation.messages if item.message_id == message_id),
            None,
        )
        if (
            selected_message is None
            or selected_message.role != "assistant"
            or selected_message.source_task_id != task_id
            or selected_message.source_run_id != run_id
        ):
            raise CreatorEvidenceError(
                "source_message_mismatch",
                "The selected assistant message does not match the source execution.",
            )
        bound_messages = [
            item
            for item in conversation.messages
            if item.source_task_id == task_id and item.source_run_id == run_id
        ]
        source_title = _sanitize_text(conversation.title, max_chars=160) or source_title
    elif any((source_xpert_id, source_conversation_id, source_message_id)):
        raise CreatorEvidenceError(
            "source_scope_mismatch",
            "Xpert message scope cannot be attached to a classic workflow source.",
        )

    identity = {
        "version": CREATOR_EVIDENCE_VERSION,
        "source_kind": clean_kind,
        "source_task_id": task_id,
        "source_run_id": run_id,
        "source_xpert_id": xpert_id,
        "source_conversation_id": conversation_id,
        "source_message_id": message_id,
    }
    candidates = tuple(
        item
        for item in (
            _candidate(
                identity,
                "intent_summary",
                "目标摘要",
                _intent_summary(execution, bound_messages),
                default_selected=True,
            ),
            _candidate(
                identity,
                "successful_steps",
                "成功步骤",
                _successful_steps(execution),
                default_selected=True,
            ),
            _candidate(
                identity,
                "tool_names",
                "使用工具",
                _tool_names(execution),
                default_selected=True,
            ),
            _candidate(
                identity,
                "user_correction",
                "用户修正",
                _user_corrections(bound_messages),
                default_selected=True,
            ),
            _candidate(
                identity,
                "io_shape",
                "输入输出结构",
                _io_shape(execution),
                default_selected=True,
            ),
            _candidate(
                identity,
                "final_output_excerpt",
                "最终输出片段",
                _sanitize_text(execution.result or "", max_chars=1_600),
                default_selected=False,
            ),
        )
        if item is not None
    )
    preview_fingerprint = _digest(
        {
            **identity,
            "execution_revision": execution.revision,
            "completed_at": execution.completed_at,
            "candidate_hashes": [item.content_hash for item in candidates],
        }
    )
    return CreatorEvidencePreview(
        version=CREATOR_EVIDENCE_VERSION,
        source_kind=clean_kind,  # type: ignore[arg-type]
        source_task_id=task_id,
        source_run_id=run_id,
        source_title=source_title,
        source_xpert_id=xpert_id,
        source_conversation_id=conversation_id,
        source_message_id=message_id,
        preview_fingerprint=preview_fingerprint,
        candidates=candidates,
    )


def _validate_execution(
    execution: WorkflowExecution,
    *,
    source_kind: str,
    source_run_id: str,
) -> None:
    if execution.status != "completed":
        raise CreatorEvidenceError(
            "source_not_completed",
            "Only a completed execution can become Skill evidence.",
        )
    if execution.run_id != source_run_id:
        raise CreatorEvidenceError(
            "source_run_mismatch",
            "The source run changed; refresh before selecting evidence.",
        )
    if execution.source_kind != source_kind:
        raise CreatorEvidenceError(
            "source_kind_mismatch",
            "The execution does not have a trusted Creator source marker.",
        )
    expected_run_type = {
        "workflow_classic": "workflow",
        "xpert_chat": "xpert",
    }[source_kind]
    if execution.run_type != expected_run_type:
        raise CreatorEvidenceError(
            "source_run_type_mismatch",
            "The execution run type does not match its trusted source marker.",
        )


def _candidate(
    identity: dict[str, Any],
    kind: CreatorEvidenceCandidateKind,
    title: str,
    summary: str,
    *,
    default_selected: bool,
) -> CreatorEvidenceCandidate | None:
    clean_summary = _sanitize_text(summary, max_chars=4_000)
    if not clean_summary:
        return None
    content_hash = _digest({"kind": kind, "summary": clean_summary})
    candidate_id = "evidence_" + _digest(
        {**identity, "kind": kind, "content_hash": content_hash}
    )[:24]
    return CreatorEvidenceCandidate(
        candidate_id=candidate_id,
        kind=kind,
        title=title,
        summary=clean_summary,
        content_hash=content_hash,
        default_selected=default_selected,
    )


def _intent_summary(execution: WorkflowExecution, bound_messages: list[Any]) -> str:
    user_message = next(
        (
            str(item.content)
            for item in bound_messages
            if getattr(item, "role", "") == "user" and str(item.content).strip()
        ),
        "",
    )
    raw_intent = user_message or str(execution.inputs.get("user_input") or "")
    clean_intent = _sanitize_text(raw_intent, max_chars=1_200)
    title = _workflow_title(execution)
    if clean_intent and clean_intent != title:
        return f"{title}：{clean_intent}" if title else clean_intent
    return clean_intent or title


def _successful_steps(execution: WorkflowExecution) -> str:
    completed_node_ids = {
        str(event.get("node_id") or "")
        for event in execution.events
        if isinstance(event, dict)
        and event.get("event") == "node_end"
        and event.get("status") == "completed"
        and str(event.get("node_id") or "")
    }
    if not completed_node_ids:
        return ""
    raw_nodes = execution.workflow.get("nodes")
    if not isinstance(raw_nodes, list):
        return ""
    steps: list[str] = []
    for raw_node in raw_nodes[:40]:
        if not isinstance(raw_node, dict):
            continue
        if str(raw_node.get("id") or "") not in completed_node_ids:
            continue
        data = raw_node.get("data") if isinstance(raw_node.get("data"), dict) else {}
        kind = str(data.get("kind") or raw_node.get("type") or "step")[:80]
        if kind in {"input", "output", "runtime_middleware"}:
            continue
        label = str(
            data.get("title")
            or data.get("label")
            or raw_node.get("title")
            or raw_node.get("id")
            or kind
        )
        clean_label = _sanitize_text(label, max_chars=160)
        clean_kind = _sanitize_text(kind, max_chars=80)
        if clean_label:
            steps.append(f"{len(steps) + 1}. {clean_label}（{clean_kind or 'step'}）")
        if len(steps) >= 20:
            break
    return "\n".join(steps)


def _tool_names(execution: WorkflowExecution) -> str:
    names: list[str] = []
    for event in execution.events:
        if not isinstance(event, dict):
            continue
        clean = _sanitize_text(str(event.get("tool_name") or ""), max_chars=120)
        if clean and clean not in names:
            names.append(clean)
        if len(names) >= 30:
            break
    return "、".join(names)


def _user_corrections(bound_messages: list[Any]) -> str:
    corrections: list[str] = []
    for message in bound_messages:
        content = str(getattr(message, "content", "") or "")
        if getattr(message, "role", "") != "user" or not _CORRECTION_MARKER.search(content):
            continue
        clean = _sanitize_text(content, max_chars=800)
        if clean and clean not in corrections:
            corrections.append(clean)
        if len(corrections) >= 3:
            break
    return "\n".join(f"- {item}" for item in corrections)


def _io_shape(execution: WorkflowExecution) -> str:
    input_fields: list[dict[str, str]] = []
    for key, value in list(execution.inputs.items())[:80]:
        raw_name = str(key).strip()[:120]
        if not raw_name or _SENSITIVE_KEY.search(raw_name):
            continue
        if re.search(
            r"(?:history|context|memory|attachment|file_content)",
            raw_name,
            re.I,
        ):
            continue
        name = _sanitize_text(raw_name, max_chars=120)
        if not name:
            continue
        input_fields.append({"name": name, "type": _value_shape(value)})
        if len(input_fields) >= 30:
            break
    shape = {
        "inputs": input_fields,
        "output": {
            "type": "text",
            "present": bool(execution.result),
        },
    }
    return json.dumps(shape, ensure_ascii=False, separators=(",", ":"))


def _workflow_title(execution: WorkflowExecution) -> str:
    return _sanitize_text(
        str(
            execution.runtime_metadata.get("workflow_title")
            or execution.workflow.get("title")
            or ""
        ),
        max_chars=160,
    )


def _sanitize_text(value: str, *, max_chars: int) -> str:
    clean = str(value or "")
    clean = _PEM_SECRET.sub("[REDACTED]", clean)
    clean = _URL_CREDENTIALS.sub("https://[REDACTED]@", clean)
    clean = _SENSITIVE_ASSIGNMENT.sub("[REDACTED]", clean)
    clean = _ENV_SECRET_ASSIGNMENT.sub("[REDACTED]", clean)
    clean = _BEARER_SECRET.sub("Bearer [REDACTED]", clean)
    clean = _TOKEN_SECRET.sub("[REDACTED]", clean)
    clean = _WINDOWS_PATH.sub("[LOCAL_PATH]", clean)
    clean = _UNC_PATH.sub("[LOCAL_PATH]", clean)
    clean = _POSIX_PATH.sub("[LOCAL_PATH]", clean)
    clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", clean)
    clean = re.sub(r"[ \t]+", " ", clean)
    clean = re.sub(r"\s*\n\s*", "\n", clean).strip()
    return clean[: max(0, int(max_chars))]


def _required_identifier(value: str | None, field_name: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise CreatorEvidenceError(
            "source_identifier_missing",
            f"{field_name} is required.",
        )
    if len(clean) > 200 or not re.fullmatch(r"[A-Za-z0-9._:-]+", clean):
        raise CreatorEvidenceError(
            "source_identifier_invalid",
            f"{field_name} is invalid.",
        )
    return clean


def _value_shape(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "text"


def _digest(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
