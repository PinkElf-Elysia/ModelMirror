"""Bounded equivalence partitions for the existing comparison DSL.

Representatives cover predicate truth classes, not random examples. A partition
that cannot be completed within the bounds is rejected; it is never truncated.
This module does not execute workflow nodes or infer resource schemas.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from itertools import product
import math
from typing import Any, Callable

try:
    from server.workflow_native.control_data import typed_identity
    from server.workflow_native.node_contracts import WorkflowValueSchema
except ModuleNotFoundError:
    from workflow_native.control_data import typed_identity
    from workflow_native.node_contracts import WorkflowValueSchema


MAX_DOMAIN_VALUES = 256
MAX_PARTITION_STATES = 4096
MAX_PROOF_WORK = 2_000_000
MAX_SCHEMA_DEPTH = 16
MAX_SCHEMA_NODES = 256
_SCHEMA_FIELDS = {"type", "nullable", "items", "properties", "required", "any_of"}
_MISSING = object()


class ControlDomainUnproven(ValueError):
    pass


@dataclass
class _ProofBudget:
    remaining: int = MAX_PROOF_WORK

    def charge(self, cost: int = 1) -> None:
        self.remaining -= cost
        if self.remaining < 0:
            raise ControlDomainUnproven("CONTROL_PROOF_BUDGET: 输入划分超过有界计算预算。")


def _check_schema(schema: WorkflowValueSchema) -> None:
    # New Schema constraints need a proof implementation, not post-hoc filtering
    # of representatives that may omit an entire legal observation class.
    pending = [(schema, 0)]
    visited = 0
    while pending:
        current, depth = pending.pop()
        visited += 1
        if depth > MAX_SCHEMA_DEPTH or visited > MAX_SCHEMA_NODES:
            raise ControlDomainUnproven("CONTROL_SCHEMA_LIMIT: 输入类型超过证明深度或节点上限。")
        if type(current) is not WorkflowValueSchema or set(type(current).model_fields) != _SCHEMA_FIELDS:
            raise ControlDomainUnproven("CONTROL_SCHEMA_UNSUPPORTED: 输入类型包含尚未建模的约束。")
        children = list(current.any_of) + list(current.properties.values())
        if current.items is not None:
            children.append(current.items)
        pending.extend((child, depth + 1) for child in children)


def _bounded(values: list[Any]) -> list[Any]:
    unique = {typed_identity(value): value for value in values}
    if len(unique) > MAX_DOMAIN_VALUES:
        raise ControlDomainUnproven("CONTROL_DOMAIN_LIMIT: 输入等价类超过证明上限。")
    return list(unique.values())


def _accepts(schema: WorkflowValueSchema, value: Any) -> bool:
    try:
        schema.assert_value(value)
        return True
    except ValueError:
        return False


def _literals(rules: list[dict]) -> list[Any]:
    values = []
    for rule in rules:
        if rule.get("operator") in {"is_null", "contains"}:
            continue
        value = rule.get("value")
        values.extend(value if rule.get("operator") == "in" and isinstance(value, list) else [value])
    return values


def _numbers(rules: list[dict], *, integer: bool) -> list[Any]:
    boundaries = sorted(set(value for value in _literals(rules)
        if isinstance(value, (int, float)) and not isinstance(value, bool)))
    values: list[Any] = [0, -1, 1]
    try:
        for value in boundaries:
            if not math.isfinite(float(value)):
                raise ValueError
            values.extend([value, math.floor(value) - 1, math.ceil(value) + 1,
                           math.floor(value), math.ceil(value)])
            if not integer:
                values.extend([math.nextafter(float(value), -math.inf), math.nextafter(float(value), math.inf)])
        for low, high in zip(boundaries, boundaries[1:]):
            values.append(math.floor(low) + 1)
            if not integer:
                values.append(low / 2 + high / 2)
    except (ValueError, OverflowError):
        raise ControlDomainUnproven("CONTROL_NUMERIC_DOMAIN: 无法证明数值边界。") from None
    return [value for value in values if math.isfinite(float(value))
            and (not integer or isinstance(value, int))]


def _strings(rules: list[dict], budget: _ProofBudget) -> list[str]:
    equals = sorted(set(value for value in _literals(rules) if isinstance(value, str)))
    contains = sorted(set(rule["value"] for rule in rules
        if rule.get("operator") == "contains" and isinstance(rule.get("value"), str)))
    patterns = equals + contains
    if sum(map(len, patterns)) > MAX_PARTITION_STATES:
        raise ControlDomainUnproven("CONTROL_STRING_DOMAIN: 字符串规则超过证明上限。")
    budget.charge(sum(len(text) * (len(text) + 1) // 2 for text in patterns))
    alphabet = set("".join(patterns))
    other = "\x00"
    while other in alphabet:
        other = chr(ord(other) + 1)
    alphabet = sorted(alphabet | {other})
    prefixes = {text[:i] for text in contains for i in range(len(text) + 1)} | {""}
    equal_prefixes = {text[:i] for text in equals for i in range(len(text) + 1)}
    initial_mask = sum(1 << i for i, text in enumerate(contains) if not text)
    initial = ("", initial_mask, "" if equals else None)
    queue = deque([(initial, "")])
    visited = {initial}
    representatives: dict[tuple, str] = {}
    transition_cost = 1 + sum(map(len, prefixes)) + sum(map(len, contains)) + max(map(len, equals), default=0)
    # The DFA remembers substring matches and exact-literal prefixes. Characters
    # outside the finite alphabet are equivalent to `other` for every rule.
    while queue:
        (suffix, mask, equal_prefix), witness = queue.popleft()
        signature = (mask, equal_prefix if equal_prefix in equals else None)
        representatives.setdefault(signature, witness)
        for char in alphabet:
            budget.charge(transition_cost)
            tail = suffix + char
            next_mask = mask | sum(1 << i for i, text in enumerate(contains) if tail.endswith(text))
            next_suffix = max((prefix for prefix in prefixes if tail.endswith(prefix)), key=len)
            next_equal = equal_prefix + char if equal_prefix is not None else None
            if next_equal not in equal_prefixes:
                next_equal = None
            state = (next_suffix, next_mask, next_equal)
            if state not in visited:
                visited.add(state)
                if len(visited) > MAX_PARTITION_STATES:
                    raise ControlDomainUnproven("CONTROL_STRING_DOMAIN: 字符串状态超过证明上限。")
                queue.append((state, witness + char))
    return list(representatives.values())


def _arrays(schema: WorkflowValueSchema, rules: list[dict], seed: Callable, budget: _ProofBudget) -> list[Any]:
    item_schema = schema.items or WorkflowValueSchema()
    literals = [value for value in _literals(rules) if isinstance(value, list)]
    members = _bounded([rule.get("value") for rule in rules if rule.get("operator") == "contains"
                        and _accepts(item_schema, rule.get("value"))])
    if 2 ** len(members) > MAX_DOMAIN_VALUES:
        raise ControlDomainUnproven("CONTROL_ARRAY_DOMAIN: 数组成员组合超过证明上限。")
    others = [seed(item_schema), None, "", 0, True, {}, []]
    other = next((value for value in others if _accepts(item_schema, value)
                  and typed_identity(value) not in {typed_identity(item) for item in members}), _MISSING)
    # A valid value outside the tested members may require a non-default scalar.
    if other is _MISSING and item_schema.type in {"string", "integer", "number"}:
        member_rules = [{"operator": "equals", "value": item} for item in members]
        candidates = (_strings(member_rules, budget) if item_schema.type == "string" else
                      _numbers(member_rules, integer=item_schema.type == "integer"))
        other = next((value for value in candidates if _accepts(item_schema, value)
                      and typed_identity(value) not in {typed_identity(item) for item in members}), _MISSING)
    if other is _MISSING and item_schema.type in {"object", "array", "any"}:
        # Do not claim a finite item domain from a few complex seeds.
        raise ControlDomainUnproven("CONTROL_ARRAY_DOMAIN: 无法完整划分数组元素类型。")
    values: list[Any] = list(literals)
    different_length = 1 + max((len(value) for value in literals), default=0)
    if different_length > MAX_DOMAIN_VALUES:
        raise ControlDomainUnproven("CONTROL_ARRAY_DOMAIN: 数组常量超过证明上限。")
    for selected in product((False, True), repeat=len(members)):
        budget.charge(1 + different_length + len(members))
        items = [value for value, included in zip(members, selected) if included]
        values.append(items)
        extra = items[0] if items else other
        if extra is not _MISSING:
            values.append(items + [extra] * different_length)
    return values


def value_partition(
    schema: WorkflowValueSchema, predicates: list[tuple[str, dict]], *, seed: Callable,
) -> list[Any]:
    """Cover all legal values' observations for top-level fields and DSL rules."""
    _check_schema(schema)
    return _partition(schema, predicates, seed=seed, budget=_ProofBudget())


def _partition(
    schema: WorkflowValueSchema, predicates: list[tuple[str, dict]], *, seed: Callable, budget: _ProofBudget,
) -> list[Any]:
    budget.charge(1 + len(predicates))
    roots = [rule for field, rule in predicates if not field]
    values: list[Any] = [None] if _accepts(schema, None) else []
    if schema.any_of:
        for variant in schema.any_of:
            values.extend(_partition(variant, predicates, seed=seed, budget=budget))
        return _bounded([value for value in values if _accepts(schema, value)])
    if schema.type == "any":
        for kind in ("string", "number", "boolean", "object", "array"):
            values.extend(_partition(WorkflowValueSchema(type=kind), predicates, seed=seed, budget=budget))
    elif schema.type in {"number", "integer"}:
        values.extend(_numbers(roots, integer=schema.type == "integer"))
    elif schema.type == "boolean":
        values.extend([False, True])
    elif schema.type == "string":
        values.extend(_strings(roots, budget))
    elif schema.type == "array":
        values.extend(_arrays(schema, roots, seed, budget))
    elif schema.type == "object":
        literals = [value for value in _literals(roots) if isinstance(value, dict)]
        values.extend(literals)
        fields = sorted({field for field, _ in predicates if field})
        domains = []
        for field in fields:
            field_rules = [("", rule) for name, rule in predicates if name == field]
            field_rules += [("", {"operator": "equals", "value": value[field]})
                            for value in literals if field in value]
            domain = _partition(schema.properties.get(field, WorkflowValueSchema()), field_rules, seed=seed, budget=budget)
            if field not in schema.required:
                domain.append(_MISSING)
            domains.append(domain)
        size = math.prod(len(domain) for domain in domains)
        if size > MAX_DOMAIN_VALUES:
            raise ControlDomainUnproven("CONTROL_OBJECT_DOMAIN: 字段组合超过证明上限。")
        base = seed(schema)
        if not isinstance(base, dict) or not _accepts(schema, base):
            raise ControlDomainUnproven("CONTROL_OBJECT_DOMAIN: unproven outcomes: unmatched; 无法构造合法对象。")
        extra_key = "__control_proof_other__"
        while extra_key in schema.properties or extra_key in fields or any(extra_key in value for value in literals):
            extra_key += "_"
        for selected in product(*domains):
            budget.charge(1 + len(fields))
            value = dict(base)
            for field, item in zip(fields, selected):
                if item is _MISSING:
                    value.pop(field, None)
                else:
                    value[field] = item
            values.append(value)
            values.append({**value, extra_key: None})
    return _bounded([value for value in values if _accepts(schema, value)])
