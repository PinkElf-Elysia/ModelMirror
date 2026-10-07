"""Bounded, passive request/response/validator pairing. No file or network I/O."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from typing import Any, Iterator

from jsonschema import Draft202012Validator

from .node_adapters import get_planner_node_adapter
from .planner import extract_json_object_text
from .transport_evidence import TransportEvidence


MAX_TEXT_CHARS = 1_048_576
MAX_REPORT_CHARS = 65_536
MAX_CALLS = 3
MAX_NODES = 32
MAX_BINDINGS = 64
_STAGES = {"task_plan", "task_plan_v1", "capability_compile", "graph_patch_v1", "graph_intent_v3", "generation_recipe_v1", "recipe_edits_v1"}
_TYPES = {"any", "null", "string", "number", "integer", "boolean", "object", "array"}
_PUBLIC_KEYS = {
    "ir_version", "name", "description", "nodes", "tasks", "assumptions", "type",
    "properties", "required", "outputs", "inputs", "control_edges", "final_output",
    "resources", "middleware", "prompt_profile_ids", "operations", "goal",
    "filter", "kind", "logic", "items", "children", "field", "operator", "ref",
    "value", "value_type", "value_source", "values", "limit", "return_mode",
    "max_affected_rows", "select_fields", "order_by", "failure_action", "retry_mode",
    "role_prompt", "task_input", "model_id", "source_agent_id", "method_skill_ids",
    "format", "expected_schema", "severity", "error_code", "message",
    "generation_protocol_version", "control_flow", "node_ref", "branches", "steps", "paths",
}
_PUBLIC_PORTS = {"records", "values", "json", "value", "result", "user_input", "context"}
_ENUMS = {
    "kind": {"predicate", "group"}, "logic": {"and", "or"},
    "operator": {"eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "is_null"},
    "value_source": {"literal", "input", "none"}, "value_type": _TYPES,
}
_ACTIVE: ContextVar[GenerationEvidenceCall | None] = ContextVar("meta_planner_generation_evidence", default=None)


@lru_cache(maxsize=2)
def _patch_vocabulary(stage: str = "graph_patch_v1") -> tuple[set[str], set[str], int]:
    # Derive names from the actual strict command contract, not a second op registry.
    from .graph_patch import GraphPatchEnvelopeV1, GRAPH_PATCH_MAX_OPERATIONS

    if stage == "recipe_edits_v1":
        from .recipe_edits import RecipeEditsV1, MAX_RECIPE_EDITS
        schema, limit = RecipeEditsV1.model_json_schema(), MAX_RECIPE_EDITS
    else:
        schema, limit = GraphPatchEnvelopeV1.model_json_schema(), GRAPH_PATCH_MAX_OPERATIONS
    mapping = schema["properties"]["operations"]["items"]["discriminator"]["mapping"]
    fields = set().union(*(schema["$defs"][ref.rsplit("/", 1)[-1]]["properties"] for ref in mapping.values()))
    return set(mapping), fields, limit


def _checksum(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def _type(value: Any) -> str:
    return {type(None): "null", str: "string", bool: "boolean", int: "integer", float: "number", dict: "object", list: "array"}.get(type(value), "unsupported")


def _token(value: Any, allowed: set[str]) -> dict[str, str]:
    return {"value": value} if isinstance(value, str) and value in allowed else {"checksum": _checksum(value), "type": _type(value)}


def _filter_shape(value: Any, *, depth: int = 0, field: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {"type": _type(value)}
    # Only filter containers have structure; JSON literals are opaque, even if
    # their business keys happen to collide with public schema vocabulary.
    if field not in {"", "items", "children"}:
        if field in _ENUMS:
            result["enum"] = _token(value, _ENUMS[field])
        return result
    if depth >= 4:
        return {**result, "details_omitted": True}
    if isinstance(value, dict):
        result.update(count=len(value), fields=[
            {"key": _token(key, _PUBLIC_KEYS), "shape": _filter_shape(item, depth=depth + 1, field=key)}
            for key, item in sorted(value.items())[:20]
        ], details_omitted=len(value) > 20)
    elif isinstance(value, list):
        result.update(count=len(value), items=[_filter_shape(item, depth=depth + 1) for item in value[:20]], details_omitted=len(value) > 20)
    # Business literals, field names and predicate refs never leave this observer.
    return result


def _schema_shape(value: Any, depth: int = 0) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"type": _type(value)}
    result = {"type": _token(value.get("type", "any"), _TYPES), "nullable": value.get("nullable", False) is True}
    if depth >= 4:
        return {**result, "details_omitted": True}
    if value.get("items") is not None:
        result["items"] = _schema_shape(value["items"], depth + 1)
    choices = value.get("any_of", [])
    if isinstance(choices, (list, tuple)) and choices:
        result["any_of"] = [_schema_shape(item, depth + 1) for item in choices[:4]]
        result["details_omitted"] = len(choices) > 4
    # A full-schema hash distinguishes omitted details without exposing property names.
    result["properties_checksum"] = _checksum(value.get("properties", {}))
    result["required_checksum"] = _checksum(value.get("required", []))
    return result


def _structure(payload: Any, allowed_kinds: set[str], stage: str = "graph_patch_v1") -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"type": _type(payload)}
    result: dict[str, Any] = {"type": "object", "fields": [_token(key, _PUBLIC_KEYS) for key in sorted(payload)[:40]], "details_omitted": len(payload) > 40}
    if "operations" in payload:
        operations = payload["operations"]
        result["operations_type"] = _type(operations)
        if isinstance(operations, list):
            names, fields, limit = _patch_vocabulary("recipe_edits_v1" if stage == "recipe_edits_v1" else "graph_patch_v1")
            result.update(operation_count=len(operations), operations=[])
            result["details_omitted"] |= len(operations) > limit
            for index, operation in enumerate(operations[:limit]):
                entry = {"operation_index": index, "type": _type(operation)}
                if isinstance(operation, dict):
                    entry.update(
                        op=_token(operation.get("op"), names),
                        fields=[_token(key, fields) for key in sorted(operation)[:32]],
                        details_omitted=len(operation) > 32,
                    )
                    # Values, prompts, resource IDs and read-only state remain opaque.
                    if "config" in operation:
                        entry["config_type"] = _type(operation["config"])
                result["operations"].append(entry)
    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list):
        result["nodes_type"] = _type(raw_nodes)
        if "tasks" in payload:
            result["tasks_type"] = _type(payload["tasks"])
            if isinstance(payload["tasks"], list):
                result["task_count"] = len(payload["tasks"])
        return result
    nodes = []
    remaining = MAX_BINDINGS
    result.update(node_count=len(raw_nodes), details_omitted=result["details_omitted"] or len(raw_nodes) > MAX_NODES)
    for index, node in enumerate(raw_nodes[:MAX_NODES]):
        if not isinstance(node, dict):
            nodes.append({"node_index": index, "type": _type(node)})
            continue
        config = node.get("config", {})
        entry: dict[str, Any] = {
            "node_index": index, "kind": _token(node.get("kind"), allowed_kinds),
            "config_type": _type(config),
        }
        if isinstance(config, dict):
            entry["config_fields"] = [_token(key, _PUBLIC_KEYS) for key in sorted(config)[:32]]
            entry["details_omitted"] = len(config) > 32
            entry["filter_present"] = "filter" in config
            if "filter" in config:
                entry["filter"] = _filter_shape(config["filter"])
        for direction in ("inputs", "outputs"):
            bindings = node.get(direction, [])
            entry[f"{direction}_type"] = _type(bindings)
            if not isinstance(bindings, list):
                continue
            entry[f"{direction}_count"] = len(bindings)
            selected = bindings[:remaining]
            remaining -= len(selected)
            if len(selected) != len(bindings):
                result["details_omitted"] = True
            entry[direction] = [
                {"port": _token(item.get("port"), _PUBLIC_PORTS),
                 "variable_checksum": _checksum(item.get("variable")),
                 "source_ref_checksum": _checksum(item.get("source_ref")),
                 "source_port": _token(item.get("source_port"), _PUBLIC_PORTS),
                 "value_schema": _schema_shape(item.get("value_schema", {}))}
                if isinstance(item, dict) else {"type": _type(item)} for item in selected
            ]
        nodes.append(entry)
    result["nodes"] = nodes
    return result


def _decode(text: str) -> tuple[Any, str]:
    if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
        raise ValueError("EVIDENCE_TEXT_UNAVAILABLE")
    try:
        return json.loads(text), "strict_json"
    except json.JSONDecodeError:
        return json.loads(extract_json_object_text(text)), "extracted_json"


def _local_references_only(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"$ref", "$dynamicRef"} and (not isinstance(item, str) or not item.startswith("#")):
                raise ValueError("EVIDENCE_REMOTE_SCHEMA_FORBIDDEN")
            _local_references_only(item)
    elif isinstance(value, list):
        for item in value:
            _local_references_only(item)


def _omitted(value: Any) -> bool:
    if isinstance(value, dict):
        return value.get("details_omitted") is True or any(_omitted(item) for item in value.values())
    return isinstance(value, list) and any(_omitted(item) for item in value)


class GenerationEvidenceCall:
    def __init__(self, sequence: int, stage: str, intended_prompt: str) -> None:
        self.sequence = sequence
        self.stage = stage if stage in _STAGES else "unknown"
        self.schema: dict[str, Any] | None = None
        self.allowed_kinds: set[str] = set()
        self.observations: dict[str, dict[str, Any]] = {}
        self.observation_counts: dict[str, int] = {}
        try:
            prompt, _ = _decode(intended_prompt)
            self.intended_schema_checksum = _checksum(prompt.get("required_schema"))
        except Exception:
            self.intended_schema_checksum = None

    def _observe(self, key: str, operation: Any) -> None:
        if key not in {"request", "provider", "collector", "validator", "transport"}:
            return
        self.observation_counts[key] = self.observation_counts.get(key, 0) + 1
        try:
            self.observations[key] = operation()
        except Exception:
            # Passive evidence cannot replace the original request result or exception.
            self.observations[key] = {"status": "unavailable", "code": "EVIDENCE_COLLECTION_FAILED"}

    def request(self, payload: dict[str, Any] | bytes, route: str) -> None:
        def summarize() -> dict[str, Any]:
            if isinstance(payload, bytes) and len(payload) > MAX_TEXT_CHARS:
                raise ValueError("EVIDENCE_REQUEST_TOO_LARGE")
            body = json.loads(payload) if isinstance(payload, bytes) else payload
            encoded = json.dumps(body, ensure_ascii=True)
            if len(encoded) > MAX_TEXT_CHARS:
                raise ValueError("EVIDENCE_REQUEST_TOO_LARGE")
            messages = body.get("messages", [])
            last = next(item for item in reversed(messages) if item.get("role") == "user")
            prompt, _ = _decode(last.get("content"))
            schema = prompt.get("required_schema")
            if not isinstance(schema, dict):
                raise ValueError("EVIDENCE_SCHEMA_MISSING")
            _local_references_only(schema)
            self.schema = deepcopy(schema)
            self.allowed_kinds = {name.removeprefix("ModelNode_") for name in schema.get("$defs", {})
                                  if name.startswith("ModelNode_") and get_planner_node_adapter(name.removeprefix("ModelNode_")) is not None}
            checksum = _checksum(schema)
            return {
                "status": "observed", "route": route if route in {"legacy", "managed"} else "unknown",
                "request_checksum": _checksum(body), "model_id_checksum": _checksum(body.get("model")),
                "schema_checksum": checksum, "intended_schema_checksum": self.intended_schema_checksum,
                "contract_matches_intended": checksum == self.intended_schema_checksum,
            }
        self._observe("request", summarize)

    def _body(self, payload: Any, parse_mode: str) -> dict[str, Any]:
        encoded = json.dumps(payload, ensure_ascii=True)
        if len(encoded) > MAX_TEXT_CHARS:
            raise ValueError("EVIDENCE_BODY_TOO_LARGE")
        structure = _structure(payload, self.allowed_kinds, self.stage)
        return {
            "status": "observed", "parse_mode": parse_mode,
            "body_checksum": _checksum(payload), "structure_checksum": _checksum(structure),
            "schema_valid": Draft202012Validator(self.schema).is_valid(payload) if self.schema is not None else None,
            "structure": structure,
        }

    def text(self, key: str, text: str) -> None:
        self._observe(key, lambda: self._body(*_decode(text)))

    def provider(self, payload: dict[str, Any]) -> None:
        def summarize() -> dict[str, Any]:
            content = payload["choices"][0]["message"].get("content")
            if not isinstance(content, str):
                return {"status": "unavailable", "code": "PROVIDER_CONTENT_UNAVAILABLE"}
            return self._body(*_decode(content))
        self._observe("provider", summarize)

    def validator(self, payload: Any) -> None:
        self._observe("validator", lambda: self._body(payload, "validator_input"))

    def _compare(self, first: str, second: str) -> str:
        left, right = self.observations.get(first, {}), self.observations.get(second, {})
        if any(self.observation_counts.get(key, 0) > 1 for key in ("request", first, second)):
            return "ambiguous"
        if left.get("status") != "observed" or right.get("status") != "observed":
            return "unavailable"
        if _omitted(left["structure"]) or _omitted(right["structure"]):
            return "incomplete"
        return "same_structure" if left["structure_checksum"] == right["structure_checksum"] else "different_structure"

    def summary(self) -> dict[str, Any]:
        return {
            "call_sequence": self.sequence, "stage": self.stage, **deepcopy(self.observations),
            "observation_counts": dict(self.observation_counts),
            "provider_to_collector": self._compare("provider", "collector"),
            "collector_to_validator": self._compare("collector", "validator"),
        }


class GenerationEvidence:
    def __init__(self) -> None:
        self.calls: list[GenerationEvidenceCall] = []
        self.omitted_calls = 0
        self._last_call: GenerationEvidenceCall | None = None

    @contextmanager
    def capture(self, stage: str, intended_prompt: str) -> Iterator[GenerationEvidenceCall]:
        call = GenerationEvidenceCall(len(self.calls) + self.omitted_calls + 1, stage, intended_prompt)
        self._last_call = call
        if len(self.calls) < MAX_CALLS:
            self.calls.append(call)
        else:
            self.omitted_calls += 1
        token = _ACTIVE.set(call)
        try:
            yield call
        finally:
            _ACTIVE.reset(token)

    @property
    def last_call(self) -> GenerationEvidenceCall | None:
        return self._last_call

    def as_dict(self) -> dict[str, Any]:
        base = {"version": 1, "scope": "filter_ports_declared_types", "patch_operation_projection_version": 1, "full_payload_replay": False}
        try:
            result = {**base, "calls": [call.summary() for call in self.calls], "omitted_calls": self.omitted_calls}
            if len(json.dumps(result, ensure_ascii=True)) > MAX_REPORT_CHARS:
                structures: dict[str, Any] = {}
                for call in result["calls"]:
                    for key in ("provider", "collector", "validator"):
                        observed = call.get(key, {})
                        if "structure" in observed:
                            ref = observed["structure_checksum"]
                            structures[ref] = observed.pop("structure")
                            observed["structure_ref"] = ref
                result["structures"] = structures
                # Only evict individual oversized shapes after deduplication. Keep
                # counts and hashes, and downgrade only comparisons using that shape.
                for ref in sorted(structures, key=lambda key: (-len(json.dumps(structures[key], ensure_ascii=True)), key)):
                    if len(json.dumps(result, ensure_ascii=True)) <= MAX_REPORT_CHARS:
                        break
                    shape = structures[ref]
                    structures[ref] = {key: shape[key] for key in ("type", "node_count", "task_count", "operation_count") if key in shape}
                    structures[ref]["details_omitted"] = True
                    for call in result["calls"]:
                        for first, second in (("provider", "collector"), ("collector", "validator")):
                            comparison = f"{first}_to_{second}"
                            if any(call.get(key, {}).get("structure_ref") == ref for key in (first, second)):
                                if call[comparison] in {"same_structure", "different_structure"}:
                                    call[comparison] = "incomplete"
            if len(json.dumps(result, ensure_ascii=True)) > MAX_REPORT_CHARS:
                return {**base, "status": "unavailable", "code": "EVIDENCE_REPORT_TOO_LARGE", "calls": []}
            return result
        except Exception:
            return {**base, "status": "unavailable", "code": "EVIDENCE_SUMMARY_FAILED", "calls": []}


def observe_generation_request(payload: dict[str, Any] | bytes, route: str) -> None:
    call = _ACTIVE.get()
    if call is not None:
        call.request(payload, route)


def observe_generation_response(payload: dict[str, Any]) -> None:
    call = _ACTIVE.get()
    if call is not None:
        call.provider(payload)


def generation_transport(client_kwargs: dict[str, Any], *, enabled: bool) -> TransportEvidence:
    call = _ACTIVE.get() if enabled else None
    record = (lambda summary: call._observe("transport", lambda: summary)) if call is not None else None
    return TransportEvidence(client_kwargs, record)
