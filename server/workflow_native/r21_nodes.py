from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from typing import Any, Iterable

from .control_data import MAX_COLLECTION_ITEMS, typed_identity
from .node_contracts import canonical_checksum
from .values import WorkflowValue, normalize_workflow_value


SCHEDULER_VERSION = 2
EDGE_OUTCOMES = {"arrived", "skipped"}
MAX_DATA_MERGE_OUTPUT_BYTES = 5 * 1_024 * 1_024
VARIABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class WorkflowR21Error(ValueError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(f"{code}: {safe_message}")
        self.code = code
        self.safe_message = safe_message


def _fail(code: str, message: str) -> None:
    raise WorkflowR21Error(code, message)


def _node_id(node: Any) -> str:
    return str(getattr(node, "id", "") or "")


def _node_kind(node: Any) -> str:
    data = getattr(node, "data", {})
    data = data if isinstance(data, dict) else {}
    return str(data.get("kind") or getattr(node, "type", "") or "")


def _edge_value(edge: Any, field: str) -> str:
    return str(getattr(edge, field, None) or "")


def workflow_scheduler_graph_checksum(
    nodes: Iterable[Any],
    control_edges: Iterable[Any],
) -> str:
    """Checksum only stable graph identity, never configuration or runtime values."""

    node_payload = sorted(
        ({"id": _node_id(node), "kind": _node_kind(node)} for node in nodes),
        key=lambda item: item["id"],
    )
    edge_payload = sorted(
        (
            {
                "id": _edge_value(edge, "id"),
                "source": _edge_value(edge, "source"),
                "target": _edge_value(edge, "target"),
                "sourceHandle": _edge_value(edge, "sourceHandle"),
                "targetHandle": _edge_value(edge, "targetHandle"),
            }
            for edge in control_edges
        ),
        key=lambda item: item["id"],
    )
    return canonical_checksum({"nodes": node_payload, "control_edges": edge_payload})


class WorkflowSchedulerV2:
    """Deterministic edge-resolution ledger for one acyclic workflow execution."""

    def __init__(
        self,
        *,
        nodes: Iterable[Any],
        control_edges: Iterable[Any],
        order_index: dict[str, int],
        restored: dict[str, Any] | None = None,
    ) -> None:
        self.nodes = list(nodes)
        self.edges = list(control_edges)
        self.node_ids = {_node_id(node) for node in self.nodes}
        self.order_index = dict(order_index)
        self.edge_by_id: dict[str, Any] = {}
        self.outgoing: dict[str, list[Any]] = defaultdict(list)
        self.incoming: dict[str, list[Any]] = defaultdict(list)
        for edge in self.edges:
            edge_id = _edge_value(edge, "id")
            if edge_id in self.edge_by_id:
                _fail(
                    "SCHEDULER_DUPLICATE_EDGE_ID",
                    "Workflow control edge IDs must be unique.",
                )
            self.edge_by_id[edge_id] = edge
            self.outgoing[_edge_value(edge, "source")].append(edge)
            self.incoming[_edge_value(edge, "target")].append(edge)
        for values in (*self.outgoing.values(), *self.incoming.values()):
            values.sort(key=lambda edge: _edge_value(edge, "id"))

        self.graph_checksum = workflow_scheduler_graph_checksum(self.nodes, self.edges)
        self.edge_outcomes: dict[str, str] = {}
        self.skipped_nodes: set[str] = set()
        if restored is not None:
            self._restore(restored)

    def _restore(self, restored: dict[str, Any]) -> None:
        if (
            type(restored.get("version")) is not int
            or restored.get("version") != SCHEDULER_VERSION
        ):
            _fail(
                "SCHEDULER_VERSION_INVALID",
                "Workflow continuation scheduler version is invalid.",
            )
        if (
            not isinstance(restored.get("graph_checksum"), str)
            or restored.get("graph_checksum") != self.graph_checksum
        ):
            _fail(
                "SCHEDULER_GRAPH_CHECKSUM_MISMATCH",
                "Workflow continuation does not match the current graph.",
            )
        raw_outcomes = restored.get("edge_outcomes")
        if not isinstance(raw_outcomes, dict):
            _fail(
                "SCHEDULER_EDGE_OUTCOMES_INVALID",
                "Workflow continuation edge outcomes are invalid.",
            )
        if any(
            not isinstance(edge_id, str) or not isinstance(outcome, str)
            for edge_id, outcome in raw_outcomes.items()
        ):
            _fail(
                "SCHEDULER_EDGE_OUTCOMES_INVALID",
                "Workflow continuation edge outcomes are invalid.",
            )
        outcomes = dict(raw_outcomes)
        if set(outcomes) - set(self.edge_by_id) or any(
            outcome not in EDGE_OUTCOMES for outcome in outcomes.values()
        ):
            _fail(
                "SCHEDULER_EDGE_OUTCOMES_INVALID",
                "Workflow continuation edge outcomes are invalid.",
            )
        raw_skipped = restored.get("skipped_nodes")
        if not isinstance(raw_skipped, list):
            _fail(
                "SCHEDULER_SKIPPED_NODES_INVALID",
                "Workflow continuation skipped nodes are invalid.",
            )
        if any(not isinstance(node_id, str) for node_id in raw_skipped):
            _fail(
                "SCHEDULER_SKIPPED_NODES_INVALID",
                "Workflow continuation skipped nodes are invalid.",
            )
        skipped = list(raw_skipped)
        if len(skipped) != len(set(skipped)) or set(skipped) - self.node_ids:
            _fail(
                "SCHEDULER_SKIPPED_NODES_INVALID",
                "Workflow continuation skipped nodes are invalid.",
            )
        self.edge_outcomes = outcomes
        self.skipped_nodes = set(skipped)

    def validate_resume_state(
        self,
        *,
        queue: Iterable[str],
        queued: set[str],
        executed: set[str],
    ) -> None:
        queue_items = [str(node_id) for node_id in queue]
        queue_set = set(queue_items)
        if (
            len(queue_items) != len(queue_set)
            or queue_set - self.node_ids
            or queued - self.node_ids
            or executed - self.node_ids
            or not queue_set.issubset(queued)
            or queue_set != queued - executed
            or self.skipped_nodes.intersection(executed | queued)
        ):
            _fail(
                "SCHEDULER_CONTINUATION_STATE_INVALID",
                "Workflow continuation queue state is invalid.",
            )
        for edge_id, outcome in self.edge_outcomes.items():
            edge = self.edge_by_id[edge_id]
            source = _edge_value(edge, "source")
            if outcome == "arrived" and source not in executed:
                _fail(
                    "SCHEDULER_CONTINUATION_STATE_INVALID",
                    "Workflow continuation edge state is inconsistent.",
                )
            if outcome == "skipped" and source not in executed | self.skipped_nodes:
                _fail(
                    "SCHEDULER_CONTINUATION_STATE_INVALID",
                    "Workflow continuation edge state is inconsistent.",
                )
        resolved_sources = executed | self.skipped_nodes
        if any(
            _edge_value(edge, "source") in resolved_sources
            and _edge_value(edge, "id") not in self.edge_outcomes
            for edge in self.edges
        ):
            _fail(
                "SCHEDULER_CONTINUATION_STATE_INVALID",
                "Workflow continuation is missing a resolved edge outcome.",
            )
        for node_id in queued | executed:
            incoming = self.incoming.get(node_id, [])
            outcomes = [
                self.edge_outcomes.get(_edge_value(edge, "id"))
                for edge in incoming
            ]
            if incoming and (
                any(outcome is None for outcome in outcomes)
                or not any(outcome == "arrived" for outcome in outcomes)
            ):
                _fail(
                    "SCHEDULER_CONTINUATION_STATE_INVALID",
                    "Workflow continuation contains an unreachable queued node.",
                )
        for node_id in self.skipped_nodes:
            incoming = self.incoming.get(node_id, [])
            if incoming and any(
                self.edge_outcomes.get(_edge_value(edge, "id")) != "skipped"
                for edge in incoming
            ):
                _fail(
                    "SCHEDULER_CONTINUATION_STATE_INVALID",
                    "Workflow continuation skipped-node state is inconsistent.",
                )

    def resolve_node(
        self,
        node_id: str,
        *,
        arrived_edge_ids: set[str],
        queued: set[str],
        executed: set[str],
    ) -> list[str]:
        outgoing = self.outgoing.get(node_id, [])
        outgoing_ids = {_edge_value(edge, "id") for edge in outgoing}
        if not arrived_edge_ids.issubset(outgoing_ids):
            _fail(
                "SCHEDULER_EDGE_SELECTION_INVALID",
                "Workflow selected an edge that does not leave the completed node.",
            )
        updates = {
            edge_id: "arrived" if edge_id in arrived_edge_ids else "skipped"
            for edge_id in outgoing_ids
        }
        return self._apply_outcomes(updates, queued=queued, executed=executed)

    def _apply_outcomes(
        self,
        updates: dict[str, str],
        *,
        queued: set[str],
        executed: set[str],
    ) -> list[str]:
        affected: set[str] = set()
        for edge_id, outcome in updates.items():
            current = self.edge_outcomes.get(edge_id)
            if current is not None and current != outcome:
                _fail(
                    "SCHEDULER_EDGE_OUTCOME_CONFLICT",
                    "Workflow edge outcome cannot change after it is resolved.",
                )
            self.edge_outcomes[edge_id] = outcome
            affected.add(_edge_value(self.edge_by_id[edge_id], "target"))

        pending_targets = deque(
            sorted(affected, key=lambda node_id: self.order_index.get(node_id, 10**9))
        )
        scheduled: set[str] = set()
        while pending_targets:
            target = pending_targets.popleft()
            incoming = self.incoming.get(target, [])
            outcomes = [
                self.edge_outcomes.get(_edge_value(edge, "id")) for edge in incoming
            ]
            if any(outcome is None for outcome in outcomes):
                continue
            if any(outcome == "arrived" for outcome in outcomes):
                if target not in queued | executed | self.skipped_nodes:
                    scheduled.add(target)
                continue
            if target in queued | executed:
                _fail(
                    "SCHEDULER_SKIPPED_NODE_ALREADY_SCHEDULED",
                    "A skipped workflow node was already scheduled.",
                )
            if target in self.skipped_nodes:
                continue
            self.skipped_nodes.add(target)
            for edge in self.outgoing.get(target, []):
                edge_id = _edge_value(edge, "id")
                current = self.edge_outcomes.get(edge_id)
                if current is not None and current != "skipped":
                    _fail(
                        "SCHEDULER_EDGE_OUTCOME_CONFLICT",
                        "A skipped workflow path conflicts with an arrived edge.",
                    )
                if current is None:
                    self.edge_outcomes[edge_id] = "skipped"
                    pending_targets.append(_edge_value(edge, "target"))

        return sorted(
            scheduled,
            key=lambda target: (self.order_index.get(target, 10**9), target),
        )

    def assert_drained(self) -> None:
        pending = set(self.edge_by_id) - set(self.edge_outcomes)
        if pending:
            _fail(
                "SCHEDULER_PENDING_EDGES_REMAIN",
                "Workflow ended with unresolved control-flow edges.",
            )

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": SCHEDULER_VERSION,
            "graph_checksum": self.graph_checksum,
            "edge_outcomes": dict(sorted(self.edge_outcomes.items())),
            "skipped_nodes": sorted(self.skipped_nodes),
        }

    def incoming_outcomes_by_handle(self, node_id: str) -> dict[str, str | None]:
        outcomes: dict[str, str | None] = {}
        for edge in self.incoming.get(node_id, []):
            handle = _edge_value(edge, "targetHandle")
            if handle in outcomes:
                _fail(
                    "DATA_MERGE_INPUT_HANDLES_INVALID",
                    "Data merge requires one unique edge for each input handle.",
                )
            outcomes[handle] = self.edge_outcomes.get(_edge_value(edge, "id"))
        return outcomes


def validate_data_merge_config(data: dict[str, Any]) -> tuple[str, str, str, str, list[str]]:
    if data.get("contractVersion") != 1 or isinstance(data.get("contractVersion"), bool):
        _fail(
            "DATA_MERGE_CONTRACT_VERSION_INVALID",
            "Data merge contractVersion must be 1.",
        )
    merge_mode = data.get("mergeMode")
    if merge_mode not in {"append", "keyed_join"}:
        _fail(
            "DATA_MERGE_MODE_INVALID",
            "Data merge mode must be append or keyed_join.",
        )
    variables: list[str] = []
    for field in ("leftVariable", "rightVariable", "outputVariable"):
        value = data.get(field)
        if not isinstance(value, str) or not VARIABLE_NAME_PATTERN.fullmatch(value):
            _fail(
                "DATA_MERGE_VARIABLE_INVALID",
                "Data merge variables must be valid identifiers.",
            )
        variables.append(value)
    left_variable, right_variable, output_variable = variables
    if left_variable == right_variable:
        _fail(
            "DATA_MERGE_INPUTS_MUST_DIFFER",
            "Data merge left and right variables must be different.",
        )
    if output_variable in {left_variable, right_variable}:
        _fail(
            "DATA_MERGE_OUTPUT_CONFLICT",
            "Data merge output cannot overwrite an input variable.",
        )
    raw_key_fields = data.get("keyFields", [])
    if not isinstance(raw_key_fields, list):
        _fail("DATA_MERGE_KEY_FIELDS_INVALID", "Data merge keyFields must be an array.")
    key_fields = [field for field in raw_key_fields if isinstance(field, str)]
    if len(key_fields) != len(raw_key_fields) or any(
        not field.strip() or len(field) > 64 for field in key_fields
    ):
        _fail(
            "DATA_MERGE_KEY_FIELDS_INVALID",
            "Data merge key fields must be non-empty top-level field names.",
        )
    if len(key_fields) != len(set(key_fields)):
        _fail(
            "DATA_MERGE_KEY_FIELDS_DUPLICATED",
            "Data merge key fields must be unique.",
        )
    if merge_mode == "keyed_join" and not 1 <= len(key_fields) <= 3:
        _fail(
            "DATA_MERGE_KEY_FIELD_COUNT_INVALID",
            "Keyed join requires between one and three key fields.",
        )
    if merge_mode == "append" and key_fields:
        _fail(
            "DATA_MERGE_APPEND_KEYS_FORBIDDEN",
            "Append mode does not accept key fields.",
        )
    return merge_mode, left_variable, right_variable, output_variable, key_fields


def _normalize_array(value: object, *, side: str) -> list[WorkflowValue]:
    try:
        normalized = normalize_workflow_value(value, path=f"$.data_merge.{side}")
    except ValueError:
        _fail(
            "DATA_MERGE_INPUT_INVALID",
            "Data merge input contains an unsupported workflow value.",
        )
    if not isinstance(normalized, list):
        _fail(
            "DATA_MERGE_ARRAY_REQUIRED",
            "Data merge inputs must be JSON arrays.",
        )
    if len(normalized) > MAX_COLLECTION_ITEMS:
        _fail(
            "DATA_MERGE_INPUT_LIMIT_EXCEEDED",
            f"Each data merge input accepts at most {MAX_COLLECTION_ITEMS} items.",
        )
    return normalized


def _keyed_index(
    rows: list[WorkflowValue],
    *,
    side: str,
    key_fields: list[str],
) -> dict[tuple[Any, ...], dict[str, WorkflowValue]]:
    index: dict[tuple[Any, ...], dict[str, WorkflowValue]] = {}
    first_row_by_identity: dict[tuple[Any, ...], int] = {}
    side_label = "左侧" if side == "left" else "右侧"
    for row_number, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            _fail(
                "DATA_MERGE_OBJECT_ARRAY_REQUIRED",
                "Keyed join inputs must be JSON object arrays.",
            )
        if any(field not in row for field in key_fields):
            _fail(
                "DATA_MERGE_KEY_FIELD_MISSING",
                "Every keyed join row must contain every configured key field.",
            )
        key_values = [row[field] for field in key_fields]
        if any(isinstance(value, (dict, list)) for value in key_values):
            _fail(
                "DATA_MERGE_KEY_NOT_SCALAR",
                "Keyed join values must be scalar or null.",
            )
        identity = tuple(typed_identity(value) for value in key_values)
        if identity in index:
            _fail(
                "DATA_MERGE_KEY_NOT_UNIQUE",
                (
                    f"{side_label}数组第 {row_number} 条记录与第 "
                    f"{first_row_by_identity[identity]} 条记录的复合键重复。"
                ),
            )
        index[identity] = row
        first_row_by_identity[identity] = row_number
    return index


def execute_data_merge(
    data: dict[str, Any],
    variables: dict[str, WorkflowValue],
    *,
    incoming_outcomes: dict[str, str | None] | None = None,
    max_output_bytes: int = MAX_DATA_MERGE_OUTPUT_BYTES,
) -> tuple[str, list[WorkflowValue]]:
    merge_mode, left_name, right_name, output_name, key_fields = (
        validate_data_merge_config(data)
    )
    if incoming_outcomes is not None:
        if set(incoming_outcomes) != {"left", "right"}:
            _fail(
                "DATA_MERGE_INPUT_HANDLES_INVALID",
                "Data merge requires exactly one left and one right input edge.",
            )
        if (
            incoming_outcomes["left"] != "arrived"
            or incoming_outcomes["right"] != "arrived"
        ):
            _fail(
                "DATA_MERGE_INPUT_NOT_REACHED",
                "数据合流需要左、右两条输入路径都到达；当前有一侧未到达。",
            )
    if left_name not in variables or right_name not in variables:
        _fail(
            "DATA_MERGE_VARIABLE_UNAVAILABLE",
            "Both data merge input variables must be available.",
        )
    left = _normalize_array(variables[left_name], side="left")
    right = _normalize_array(variables[right_name], side="right")
    if merge_mode == "append":
        result: list[WorkflowValue] = [*left, *right]
        if len(result) > MAX_COLLECTION_ITEMS:
            _fail(
                "DATA_MERGE_RESULT_LIMIT_EXCEEDED",
                f"Data merge output accepts at most {MAX_COLLECTION_ITEMS} items.",
            )
    else:
        left_index = _keyed_index(left, side="left", key_fields=key_fields)
        right_index = _keyed_index(right, side="right", key_fields=key_fields)
        result = []
        for row in left:
            assert isinstance(row, dict)
            identity = tuple(typed_identity(row[field]) for field in key_fields)
            matching = right_index.get(identity)
            if matching is None:
                continue
            result.append(
                {
                    "key": {field: row[field] for field in key_fields},
                    "left": left_index[identity],
                    "right": matching,
                }
            )
    try:
        encoded = json.dumps(
            result,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        _fail(
            "DATA_MERGE_OUTPUT_INVALID",
            "Data merge output cannot be represented as a workflow value.",
        )
    if len(encoded) > max(1, int(max_output_bytes)):
        _fail(
            "DATA_MERGE_OUTPUT_TOO_LARGE",
            "Data merge output exceeds the workflow value size limit.",
        )
    return output_name, result
