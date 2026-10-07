from __future__ import annotations

import json
import math
import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    model_validator,
)


WriteOperation = Literal["insert", "update", "delete"]
WriteStatus = Literal["applied", "noop", "conflict", "not_executed"]

_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_NODE_REF_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_RESOURCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_FIELD_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SYSTEM_FIELDS = {"record_id", "created_at", "updated_at", "revision"}
_MAX_FIELDS = 50
_MAX_EFFECTS = 100
_MAX_JSON_BYTES = 256 * 1024
_MAX_PRIVATE_JSON_BYTES = 16 * 1024 * 1024


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EvaluationEffectExpectation(_StrictModel):
    node_ref: str = Field(pattern=_NODE_REF_PATTERN.pattern)
    operation: WriteOperation
    table_id: str = Field(pattern=_RESOURCE_ID_PATTERN.pattern)
    schema_version: int | None = Field(default=None, ge=1)
    contract_checksum: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN.pattern,
    )
    status: WriteStatus = "applied"
    affected_count: int = Field(default=1, ge=0, le=100)
    expected_before: dict[str, JsonValue] = Field(default_factory=dict)
    expected_after: dict[str, JsonValue] = Field(default_factory=dict)
    error_code: str | None = Field(
        default=None,
        pattern=_ERROR_CODE_PATTERN.pattern,
    )

    @model_validator(mode="after")
    def validate_expectation(self) -> "EvaluationEffectExpectation":
        fields = set(self.expected_before) | set(self.expected_after)
        _validate_business_fields(fields)
        if len(fields) > _MAX_FIELDS:
            raise ValueError("写入效果期望最多包含 50 个业务字段。")
        _validate_json_size(
            {
                "expected_before": self.expected_before,
                "expected_after": self.expected_after,
            },
            label="写入效果期望",
        )
        if self.status == "applied" and self.affected_count == 0:
            raise ValueError("applied 写入效果必须影响至少一行。")
        if self.status in {"noop", "conflict", "not_executed"} and self.affected_count != 0:
            raise ValueError(f"{self.status} 写入效果的 affected_count 必须为 0。")
        if self.status == "conflict" and self.error_code is None:
            raise ValueError("conflict 写入效果必须声明安全 error_code。")
        if self.operation == "delete" and self.expected_after:
            raise ValueError("delete 写入效果不能声明 expected_after。")
        if self.operation == "insert" and self.expected_before:
            raise ValueError("insert 写入效果不能声明 expected_before。")
        return self


class _WriteContract(_StrictModel):
    node_ref: str = Field(pattern=_NODE_REF_PATTERN.pattern)
    table_id: str = Field(pattern=_RESOURCE_ID_PATTERN.pattern)
    operation: WriteOperation
    schema_version: int = Field(ge=1)
    contract_checksum: str = Field(pattern=_SHA256_PATTERN.pattern)
    schema_fields: list[str] = Field(max_length=_MAX_FIELDS)
    writable_fields: list[str] = Field(max_length=_MAX_FIELDS)

    @model_validator(mode="after")
    def validate_fields(self) -> "_WriteContract":
        _validate_business_fields(set(self.schema_fields))
        _validate_business_fields(set(self.writable_fields))
        if len(self.schema_fields) != len(set(self.schema_fields)):
            raise ValueError("写入节点契约的 schema_fields 不得重复。")
        if len(self.writable_fields) != len(set(self.writable_fields)):
            raise ValueError("写入节点契约的 writable_fields 不得重复。")
        if not set(self.writable_fields).issubset(set(self.schema_fields)):
            raise ValueError("writable_fields 必须属于固定 schema_fields。")
        return self


class _WriteReceipt(_StrictModel):
    node_ref: str = Field(pattern=_NODE_REF_PATTERN.pattern)
    table_id: str = Field(pattern=_RESOURCE_ID_PATTERN.pattern)
    operation: WriteOperation
    schema_version: int = Field(ge=1)
    contract_checksum: str = Field(pattern=_SHA256_PATTERN.pattern)
    request_checksum: str = Field(pattern=_SHA256_PATTERN.pattern)
    status: Literal["applied", "noop", "conflict"]
    affected_count: int = Field(ge=0, le=100)
    replayed: bool
    error_code: str | None = Field(
        default=None,
        pattern=_ERROR_CODE_PATTERN.pattern,
    )

    @model_validator(mode="after")
    def validate_status(self) -> "_WriteReceipt":
        _validate_status_count(self.status, self.affected_count, self.error_code)
        return self


class _PrivateEffect(_StrictModel):
    before_records: list[dict[str, JsonValue]] = Field(max_length=100)
    after_records: list[dict[str, JsonValue]] = Field(max_length=100)
    untouched_before_checksum: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN.pattern,
    )
    untouched_after_checksum: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN.pattern,
    )

    @model_validator(mode="after")
    def validate_records(self) -> "_PrivateEffect":
        for record in [*self.before_records, *self.after_records]:
            _validate_json_size(record, label="单条私有写入记录")
        _validate_json_size(
            {
                "before_records": self.before_records,
                "after_records": self.after_records,
            },
            label="私有写入效果",
            maximum=_MAX_PRIVATE_JSON_BYTES,
        )
        return self


class _WriteEffect(_StrictModel):
    node_ref: str = Field(pattern=_NODE_REF_PATTERN.pattern)
    table_id: str = Field(pattern=_RESOURCE_ID_PATTERN.pattern)
    operation: WriteOperation
    schema_version: int = Field(ge=1)
    contract_checksum: str = Field(pattern=_SHA256_PATTERN.pattern)
    request_checksum: str = Field(pattern=_SHA256_PATTERN.pattern)
    status: Literal["applied", "noop", "conflict"]
    affected_count: int = Field(ge=0, le=100)
    output: JsonValue = None
    receipt: _WriteReceipt | None = None
    private_effect: _PrivateEffect
    replayed: bool
    error_code: str | None = Field(
        default=None,
        pattern=_ERROR_CODE_PATTERN.pattern,
    )

    @model_validator(mode="after")
    def validate_effect(self) -> "_WriteEffect":
        _validate_status_count(self.status, self.affected_count, self.error_code)
        _validate_json_size(self.output, label="写入输出")
        return self


def evaluate_write_effects(
    expectations: Any,
    effects: Any,
    contracts: Any,
    *,
    terminal_recorded: bool,
) -> dict[str, Any]:
    """Compare trusted private Agent Table captures with frozen write expectations."""

    if type(terminal_recorded) is not bool:
        return _failure_metric(0, reason="terminal_recorded 必须是严格布尔值。")

    try:
        expected = _validate_list(expectations, EvaluationEffectExpectation, "写入效果期望")
        fixed_contracts = _validate_list(contracts, _WriteContract, "写入节点契约")
        actual = _validate_list(effects, _WriteEffect, "私有写入效果", maximum=_MAX_EFFECTS)
    except (TypeError, ValueError, ValidationError) as exc:
        expected_count = len(expectations) if isinstance(expectations, list) else 0
        return _failure_metric(expected_count, reason=f"写入证据结构无效：{_safe_error(exc)}")

    expected_refs = [item.node_ref for item in expected]
    contract_refs = [item.node_ref for item in fixed_contracts]
    actual_refs = [item.node_ref for item in actual]
    structural_failures: list[str] = []
    if len(expected_refs) != len(set(expected_refs)):
        structural_failures.append("期望包含重复 node_ref")
    if len(contract_refs) != len(set(contract_refs)):
        structural_failures.append("契约包含重复 node_ref")
    if len(actual_refs) != len(set(actual_refs)):
        structural_failures.append("效果包含重复 node_ref")
    if set(contract_refs) != set(expected_refs):
        structural_failures.append("固定契约集合与期望节点不一致")
    if not set(actual_refs).issubset(set(expected_refs)):
        structural_failures.append("效果包含未知节点")
    if structural_failures:
        return _failure_metric(
            len(expected),
            reason="写入证据结构失败：" + "；".join(structural_failures),
        )

    contracts_by_ref = {item.node_ref: item for item in fixed_contracts}
    effects_by_ref = {item.node_ref: item for item in actual}
    matched = 0
    failed = 0
    missing = 0
    failure_codes: list[str] = []

    for expectation in expected:
        contract = contracts_by_ref[expectation.node_ref]
        effect = effects_by_ref.get(expectation.node_ref)
        contract_failures = _contract_failures(expectation, contract)
        if contract_failures:
            failed += 1
            failure_codes.extend(contract_failures)
            continue

        if expectation.status == "not_executed":
            if terminal_recorded and effect is None:
                matched += 1
            else:
                failed += 1
                failure_codes.append(
                    "not_executed_trace" if effect is None else "unexpected_effect"
                )
            continue

        if effect is None:
            missing += 1
            failure_codes.append("missing_effect")
            continue

        checks = _effect_failures(expectation, contract, effect)
        if checks:
            failed += 1
            failure_codes.extend(checks)
        else:
            matched += 1

    total = len(expected)
    passed = not failure_codes and matched == total
    score = 1.0 if total == 0 and not actual else (matched / total if total else 0.0)
    reason = f"写入效果匹配 {matched}/{total}，失败 {failed}，缺失 {missing}。"
    if failure_codes:
        unique_codes = list(dict.fromkeys(failure_codes))[:8]
        reason += " 原因：" + "、".join(unique_codes) + "。"
    return {
        "name": "workflow_effect_match",
        "score": round(max(0.0, min(score, 1.0)), 6),
        "passed": passed,
        "reason": reason[:500],
        "details": {"matched": matched, "failed": failed, "missing": missing},
    }


def safe_write_evidence(effects: Any) -> list[dict[str, Any]]:
    """Project private effects into bounded, record-free report evidence."""

    try:
        validated = _validate_list(
            effects,
            _WriteEffect,
            "私有写入效果",
            maximum=_MAX_EFFECTS,
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValueError(f"私有写入效果不符合严格契约：{_safe_error(exc)}") from exc
    refs = [item.node_ref for item in validated]
    if len(refs) != len(set(refs)):
        raise ValueError("私有写入效果包含重复 node_ref。")

    projected: list[dict[str, Any]] = []
    for effect in validated:
        receipt_failures = _receipt_failures(effect)
        if receipt_failures:
            raise ValueError("私有写入效果缺少有效后端回执。")
        private = effect.private_effect
        expected_before, expected_after = _operation_counts(
            effect.operation,
            effect.status,
            effect.affected_count,
        )
        if (
            private.untouched_before_checksum is None
            or private.untouched_after_checksum is None
            or private.untouched_before_checksum != private.untouched_after_checksum
            or len(private.before_records) != expected_before
            or len(private.after_records) != expected_after
        ):
            raise ValueError("私有写入效果的行计数或未触及记录哈希无效。")
        before_ids, before_id_failures = _record_ids(private.before_records)
        after_ids, after_id_failures = _record_ids(private.after_records)
        if before_id_failures or after_id_failures:
            raise ValueError("私有写入效果的 record_id 无效或重复。")
        if (
            effect.status == "applied"
            and effect.operation == "update"
            and before_ids != after_ids
        ):
            raise ValueError("Update 私有写入效果的 record_id 集合不一致。")
        projected.append(
            {
                "node_ref": effect.node_ref,
                "operation": effect.operation,
                "table_id": effect.table_id,
                "schema_version": effect.schema_version,
                "contract_checksum": effect.contract_checksum,
                "request_checksum": effect.request_checksum,
                "untouched_before_checksum": private.untouched_before_checksum,
                "untouched_after_checksum": private.untouched_after_checksum,
                "affected_count": effect.affected_count,
                "status": effect.status,
                "replayed": effect.replayed,
                "error_code": effect.error_code,
            }
        )
    return json.loads(json.dumps(projected, ensure_ascii=False, allow_nan=False))


def _validate_list(
    value: Any,
    model: type[BaseModel],
    label: str,
    *,
    maximum: int = _MAX_EFFECTS,
) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{label}必须是最多 {maximum} 项的数组。")
    return [model.model_validate(item) for item in value]


def _contract_failures(
    expectation: EvaluationEffectExpectation,
    contract: _WriteContract,
) -> list[str]:
    failures: list[str] = []
    if contract.table_id != expectation.table_id:
        failures.append("contract_table")
    if contract.operation != expectation.operation:
        failures.append("contract_operation")
    if expectation.schema_version is not None and contract.schema_version != expectation.schema_version:
        failures.append("contract_schema")
    if expectation.contract_checksum is not None and contract.contract_checksum != expectation.contract_checksum:
        failures.append("contract_checksum")
    expected_fields = set(expectation.expected_before) | set(expectation.expected_after)
    if not expected_fields.issubset(set(contract.schema_fields)):
        failures.append("unknown_expected_field")
    return failures


def _effect_failures(
    expectation: EvaluationEffectExpectation,
    contract: _WriteContract,
    effect: _WriteEffect,
) -> list[str]:
    failures: list[str] = []
    for field in ("table_id", "operation", "schema_version", "contract_checksum"):
        if getattr(effect, field) != getattr(contract, field):
            failures.append(f"effect_{field}")
    if effect.status != expectation.status:
        failures.append("status")
    if effect.affected_count != expectation.affected_count:
        failures.append("affected_count")
    if expectation.error_code is not None and effect.error_code != expectation.error_code:
        failures.append("error_code")
    failures.extend(_receipt_failures(effect))

    private = effect.private_effect
    if (
        private.untouched_before_checksum is None
        or private.untouched_after_checksum is None
        or private.untouched_before_checksum != private.untouched_after_checksum
    ):
        failures.append("untouched_checksum")

    before, before_failures = _normalize_records(
        private.before_records,
        set(contract.schema_fields),
    )
    after, after_failures = _normalize_records(
        private.after_records,
        set(contract.schema_fields),
    )
    failures.extend(before_failures)
    failures.extend(after_failures)
    expected_before_count, expected_after_count = _operation_counts(
        effect.operation,
        effect.status,
        effect.affected_count,
    )
    if len(before) != expected_before_count or len(after) != expected_after_count:
        failures.append("private_row_count")
    if any(
        not _contains_subset(row, expectation.expected_before)
        for _, row in before
    ):
        failures.append("expected_before")
    if any(
        not _contains_subset(row, expectation.expected_after)
        for _, row in after
    ):
        failures.append("expected_after")
    if effect.status == "applied" and effect.operation == "update":
        before_by_id = dict(before)
        after_by_id = dict(after)
        if set(before_by_id) != set(after_by_id):
            failures.append("record_id_set")
        elif _nonwritable_fields_changed(
            before_by_id,
            after_by_id,
            set(contract.schema_fields) - set(contract.writable_fields),
        ):
            failures.append("nonwritable_field_changed")
    return list(dict.fromkeys(failures))


def _receipt_failures(effect: _WriteEffect) -> list[str]:
    receipt = effect.receipt
    if receipt is None:
        return ["missing_receipt"]
    fields = (
        "node_ref",
        "table_id",
        "operation",
        "schema_version",
        "contract_checksum",
        "request_checksum",
        "status",
        "affected_count",
        "replayed",
        "error_code",
    )
    return ["forged_receipt"] if any(
        getattr(receipt, field) != getattr(effect, field) for field in fields
    ) else []


def _normalize_records(
    records: list[dict[str, Any]],
    schema_fields: set[str],
) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
    normalized: list[tuple[str, dict[str, Any]]] = []
    failures: list[str] = []
    seen_ids: set[str] = set()
    for record in records:
        record_id = record.get("record_id")
        if (
            not isinstance(record_id, str)
            or not record_id.strip()
            or len(record_id) > 200
        ):
            failures.append("record_id")
            continue
        if record_id in seen_ids:
            failures.append("duplicate_record_id")
        seen_ids.add(record_id)

        wrapped = (
            isinstance(record.get("data"), dict)
            and set(record).issubset(_SYSTEM_FIELDS | {"data"})
        )
        if wrapped:
            business = dict(record["data"])
        else:
            business = {
                key: value for key, value in record.items() if key not in _SYSTEM_FIELDS
            }
        try:
            _validate_business_fields(set(business))
            _validate_json_size(business, label="写入记录")
        except ValueError:
            failures.append("record_shape")
            continue
        if not set(business).issubset(schema_fields):
            failures.append("unknown_record_field")
            continue
        normalized.append((record_id, business))
    return normalized, list(dict.fromkeys(failures))


def _record_ids(records: list[dict[str, Any]]) -> tuple[set[str], list[str]]:
    record_ids: set[str] = set()
    failures: list[str] = []
    for record in records:
        record_id = record.get("record_id")
        if (
            not isinstance(record_id, str)
            or not record_id.strip()
            or len(record_id) > 200
        ):
            failures.append("record_id")
            continue
        if record_id in record_ids:
            failures.append("duplicate_record_id")
        record_ids.add(record_id)
    return record_ids, list(dict.fromkeys(failures))


def _nonwritable_fields_changed(
    before_by_id: dict[str, dict[str, Any]],
    after_by_id: dict[str, dict[str, Any]],
    nonwritable_fields: set[str],
) -> bool:
    for record_id in before_by_id:
        before = before_by_id[record_id]
        after = after_by_id[record_id]
        for field in nonwritable_fields:
            if (field in before) != (field in after):
                return True
            if field in before and _canonical_json(before[field]) != _canonical_json(
                after[field]
            ):
                return True
    return False


def _operation_counts(operation: str, status: str, count: int) -> tuple[int, int]:
    if status != "applied":
        return 0, 0
    if operation == "insert":
        return 0, count
    if operation == "delete":
        return count, 0
    return count, count


def _contains_subset(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(
        key in actual and _canonical_json(actual[key]) == _canonical_json(value)
        for key, value in expected.items()
    )


def _validate_status_count(status: str, count: int, error_code: str | None) -> None:
    if status == "applied" and count == 0:
        raise ValueError("applied 写入效果必须影响至少一行。")
    if status in {"noop", "conflict"} and count != 0:
        raise ValueError(f"{status} 写入效果的 affected_count 必须为 0。")
    if status == "conflict" and error_code is None:
        raise ValueError("conflict 写入效果必须包含安全 error_code。")


def _validate_business_fields(fields: set[str]) -> None:
    if len(fields) > _MAX_FIELDS:
        raise ValueError("业务字段数量超过 50。")
    for field in fields:
        if field in _SYSTEM_FIELDS or not _FIELD_NAME_PATTERN.fullmatch(field):
            raise ValueError("业务字段名必须是非系统 ASCII Schema 字段。")


def _validate_json_size(
    value: Any,
    *,
    label: str,
    maximum: int = _MAX_JSON_BYTES,
) -> None:
    _validate_json_value(value, depth=0, active=set())
    try:
        encoded = _canonical_json(value).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}不是安全 JSON。") from exc
    if len(encoded) > maximum:
        limit = "16 MiB" if maximum == _MAX_PRIVATE_JSON_BYTES else "256 KiB"
        raise ValueError(f"{label}超过 {limit}。")


def _validate_json_value(value: Any, *, depth: int, active: set[int]) -> None:
    if depth > 20:
        raise ValueError("JSON 嵌套层级过深。")
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("JSON 数值必须有限。")
        return
    if isinstance(value, (list, dict)):
        identity = id(value)
        if identity in active:
            raise ValueError("JSON 不能包含循环引用。")
        active.add(identity)
        try:
            items = value.values() if isinstance(value, dict) else value
            if isinstance(value, dict) and any(type(key) is not str for key in value):
                raise ValueError("JSON 对象键必须是字符串。")
            for item in items:
                _validate_json_value(item, depth=depth + 1, active=active)
        finally:
            active.remove(identity)
        return
    raise ValueError("值不是安全 JSON 类型。")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _failure_metric(expected_count: int, *, reason: str) -> dict[str, Any]:
    return {
        "name": "workflow_effect_match",
        "score": 0.0,
        "passed": False,
        "reason": reason[:500],
        "details": {
            "matched": 0,
            "failed": max(1, min(expected_count, _MAX_EFFECTS)),
            "missing": 0,
        },
    }


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        errors = exc.errors(include_url=False, include_input=False)
        if errors:
            # A validation location can contain arbitrary private JSON keys.
            return str(errors[0].get("type") or "validation_error")[:64]
    return str(exc).splitlines()[0][:160]


__all__ = [
    "EvaluationEffectExpectation",
    "evaluate_write_effects",
    "safe_write_evidence",
]
