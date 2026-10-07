from __future__ import annotations

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

try:
    from server.evaluations.write_evidence import (
        EvaluationEffectExpectation,
        evaluate_write_effects,
        safe_write_evidence,
    )
except ModuleNotFoundError:
    from evaluations.write_evidence import (
        EvaluationEffectExpectation,
        evaluate_write_effects,
        safe_write_evidence,
    )


CONTRACT_SHA = "c" * 64
REQUEST_SHA = "d" * 64
UNCHANGED_SHA = "a" * 64
SECRET = "private customer body must never leak"


def _contract(
    *,
    node_ref: str = "update_customer",
    table_id: str = "table_customers",
    operation: str = "update",
    schema_version: int = 3,
    contract_checksum: str = CONTRACT_SHA,
    schema_fields: list[str] | None = None,
    writable_fields: list[str] | None = None,
) -> dict:
    return {
        "node_ref": node_ref,
        "table_id": table_id,
        "operation": operation,
        "schema_version": schema_version,
        "contract_checksum": contract_checksum,
        "schema_fields": (
            ["name", "tier", "active"]
            if schema_fields is None
            else schema_fields
        ),
        "writable_fields": ["tier"] if writable_fields is None else writable_fields,
    }


def _expectation(
    *,
    node_ref: str = "update_customer",
    table_id: str = "table_customers",
    operation: str = "update",
    schema_version: int | None = 3,
    contract_checksum: str | None = CONTRACT_SHA,
    status: str = "applied",
    affected_count: int = 1,
    expected_before: dict | None = None,
    expected_after: dict | None = None,
    error_code: str | None = None,
) -> dict:
    return {
        "node_ref": node_ref,
        "table_id": table_id,
        "operation": operation,
        "schema_version": schema_version,
        "contract_checksum": contract_checksum,
        "status": status,
        "affected_count": affected_count,
        "expected_before": {"tier": "basic"} if expected_before is None else expected_before,
        "expected_after": {"tier": "pro"} if expected_after is None else expected_after,
        "error_code": error_code,
    }


def _receipt(effect: dict) -> dict:
    return {
        field: effect[field]
        for field in (
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
    }


def _effect(
    *,
    node_ref: str = "update_customer",
    table_id: str = "table_customers",
    operation: str = "update",
    schema_version: int = 3,
    contract_checksum: str = CONTRACT_SHA,
    status: str = "applied",
    affected_count: int = 1,
    before_records: list[dict] | None = None,
    after_records: list[dict] | None = None,
    replayed: bool = False,
    error_code: str | None = None,
) -> dict:
    if before_records is None:
        before_records = [
            {
                "record_id": "record-1",
                "created_at": 1.0,
                "updated_at": 1.0,
                "revision": 1,
                "name": "A",
                "tier": "basic",
                "active": True,
            }
        ]
    if after_records is None:
        after_records = [
            {
                "record_id": "record-1",
                "data": {"name": "A", "tier": "pro", "active": True},
                "created_at": 1.0,
                "updated_at": 2.0,
                "revision": 2,
            }
        ]
    effect = {
        "node_ref": node_ref,
        "table_id": table_id,
        "operation": operation,
        "schema_version": schema_version,
        "contract_checksum": contract_checksum,
        "request_checksum": REQUEST_SHA,
        "status": status,
        "affected_count": affected_count,
        "output": {"message": SECRET, "affected": affected_count},
        "private_effect": {
            "before_records": before_records,
            "after_records": after_records,
            "untouched_before_checksum": UNCHANGED_SHA,
            "untouched_after_checksum": UNCHANGED_SHA,
        },
        "replayed": replayed,
        "error_code": error_code,
    }
    effect["receipt"] = _receipt(effect)
    return effect


def _metric(expectations: list[dict], effects: list[dict], contracts: list[dict], *, terminal_recorded: bool = True) -> dict:
    return evaluate_write_effects(
        expectations,
        effects,
        contracts,
        terminal_recorded=terminal_recorded,
    )


def _change_effect_identity(effect: dict, field: str, value: object) -> dict:
    changed = deepcopy(effect)
    changed[field] = value
    changed["receipt"][field] = value
    return changed


def test_proper_insert_update_and_delete_match_private_effects() -> None:
    insert_expectation = _expectation(
        node_ref="create_customer",
        operation="insert",
        expected_before={},
        expected_after={"tier": "pro"},
    )
    insert_contract = _contract(node_ref="create_customer", operation="insert")
    insert_effect = _effect(
        node_ref="create_customer",
        operation="insert",
        before_records=[],
    )

    update_expectation = _expectation()
    update_contract = _contract()
    update_effect = _effect()

    delete_expectation = _expectation(
        node_ref="delete_customer",
        operation="delete",
        expected_before={"tier": "basic"},
        expected_after={},
    )
    delete_contract = _contract(
        node_ref="delete_customer",
        operation="delete",
        writable_fields=[],
    )
    delete_effect = _effect(
        node_ref="delete_customer",
        operation="delete",
        after_records=[],
    )

    result = _metric(
        [insert_expectation, update_expectation, delete_expectation],
        [insert_effect, update_effect, delete_effect],
        [insert_contract, update_contract, delete_contract],
    )

    assert result == {
        "name": "workflow_effect_match",
        "score": 1.0,
        "passed": True,
        "reason": "写入效果匹配 3/3，失败 0，缺失 0。",
        "details": {"matched": 3, "failed": 0, "missing": 0},
    }


def test_insert_then_delete_same_table_uses_distinct_node_refs() -> None:
    expectations = [
        _expectation(
            node_ref="create_customer",
            operation="insert",
            expected_before={},
            expected_after={"name": "A"},
        ),
        _expectation(
            node_ref="delete_customer",
            operation="delete",
            expected_before={"name": "A"},
            expected_after={},
        ),
    ]
    contracts = [
        _contract(node_ref="create_customer", operation="insert"),
        _contract(
            node_ref="delete_customer",
            operation="delete",
            writable_fields=[],
        ),
    ]
    effects = [
        _effect(node_ref="create_customer", operation="insert", before_records=[]),
        _effect(node_ref="delete_customer", operation="delete", after_records=[]),
    ]

    assert _metric(expectations, effects, contracts)["passed"] is True


def test_final_text_cannot_replace_a_missing_write_effect() -> None:
    expectation = _expectation(expected_after={"tier": "success"})

    result = _metric([expectation], [], [_contract()])

    assert result["score"] == 0.0
    assert result["details"] == {"matched": 0, "failed": 0, "missing": 1}
    assert "missing_effect" in result["reason"]


def test_affected_count_cannot_override_private_row_count() -> None:
    expectation = _expectation(
        affected_count=2,
        expected_before={"tier": "basic"},
        expected_after={"tier": "pro"},
    )
    effect = _effect(affected_count=2)

    result = _metric([expectation], [effect], [_contract()])

    assert result["passed"] is False
    assert "private_row_count" in result["reason"]


@pytest.mark.parametrize(
    ("field", "value", "failure"),
    [
        ("node_ref", "unknown_node", "未知节点"),
        ("table_id", "table_other", "effect_table_id"),
        ("schema_version", 4, "effect_schema_version"),
    ],
)
def test_unknown_node_table_or_schema_version_fails(field: str, value: object, failure: str) -> None:
    effect = _change_effect_identity(_effect(), field, value)

    result = _metric([_expectation()], [effect], [_contract()])

    assert result["score"] == 0.0
    assert failure in result["reason"]


def test_missing_or_extra_contract_and_duplicate_effects_fail_closed() -> None:
    expectation = _expectation()
    effect = _effect()

    assert _metric([expectation], [effect], [])["score"] == 0.0
    assert _metric(
        [expectation],
        [effect],
        [_contract(), _contract(node_ref="other_write")],
    )["score"] == 0.0
    duplicate = _metric([expectation], [effect, deepcopy(effect)], [_contract()])
    assert duplicate["score"] == 0.0
    assert "重复 node_ref" in duplicate["reason"]


def test_contract_requires_fixed_schema_fields_and_bounded_write_subset() -> None:
    missing_schema = _contract()
    missing_schema.pop("schema_fields")
    outside_schema = _contract(writable_fields=["unknown_field"])

    assert _metric([_expectation()], [_effect()], [missing_schema])["score"] == 0.0
    assert _metric([_expectation()], [_effect()], [outside_schema])["score"] == 0.0


def test_changed_untouched_hash_and_wrong_expected_value_fail() -> None:
    changed_hash = _effect()
    changed_hash["private_effect"]["untouched_after_checksum"] = "b" * 64
    wrong_value = _expectation(expected_after={"tier": "enterprise"})

    hash_result = _metric([_expectation()], [changed_hash], [_contract()])
    value_result = _metric([wrong_value], [_effect()], [_contract()])

    assert "untouched_checksum" in hash_result["reason"]
    assert "expected_after" in value_result["reason"]


def test_subset_write_grant_accepts_full_rows_and_schema_field_expectations() -> None:
    expectation = _expectation(
        expected_before={"name": "A", "tier": "basic"},
        expected_after={"name": "A", "tier": "pro"},
    )
    contract = _contract(writable_fields=["tier"])

    result = _metric([expectation], [_effect()], [contract])

    assert result["passed"] is True


def test_update_rejects_changes_to_nonwritable_business_fields() -> None:
    changed = _effect(
        after_records=[
            {
                "record_id": "record-1",
                "data": {"name": "B", "tier": "pro", "active": True},
                "created_at": 1.0,
                "updated_at": 2.0,
                "revision": 2,
            }
        ]
    )

    result = _metric(
        [_expectation()],
        [changed],
        [_contract(writable_fields=["tier"])],
    )

    assert result["passed"] is False
    assert "nonwritable_field_changed" in result["reason"]


def test_delete_accepts_complete_rows_with_an_empty_write_field_grant() -> None:
    result = _metric(
        [
            _expectation(
                node_ref="delete_customer",
                operation="delete",
                expected_before={"name": "A", "tier": "basic"},
                expected_after={},
            )
        ],
        [
            _effect(
                node_ref="delete_customer",
                operation="delete",
                after_records=[],
            )
        ],
        [
            _contract(
                node_ref="delete_customer",
                operation="delete",
                writable_fields=[],
            )
        ],
    )

    assert result["passed"] is True


def test_insert_accepts_schema_default_fields_outside_explicit_write_grant() -> None:
    effect = _effect(
        node_ref="create_customer",
        operation="insert",
        before_records=[],
        after_records=[
            {
                "record_id": "record-new",
                "name": "Generated default",
                "tier": "pro",
                "active": True,
            }
        ],
    )
    expectation = _expectation(
        node_ref="create_customer",
        operation="insert",
        expected_before={},
        expected_after={"active": True},
    )
    contract = _contract(
        node_ref="create_customer",
        operation="insert",
        writable_fields=["tier"],
    )

    assert _metric([expectation], [effect], [contract])["passed"] is True


def test_noop_does_not_satisfy_applied_but_can_match_explicit_noop() -> None:
    effect = _effect(
        status="noop",
        affected_count=0,
        before_records=[],
        after_records=[],
    )
    applied = _metric([_expectation()], [effect], [_contract()])
    noop = _metric(
        [
            _expectation(
                status="noop",
                affected_count=0,
                expected_before={},
                expected_after={},
            )
        ],
        [effect],
        [_contract()],
    )

    assert applied["passed"] is False
    assert "status" in applied["reason"]
    assert noop["passed"] is True


def test_conflict_requires_matching_safe_error_and_zero_rows() -> None:
    expectation = _expectation(
        status="conflict",
        affected_count=0,
        expected_before={},
        expected_after={},
        error_code="REVISION_CONFLICT",
    )
    effect = _effect(
        status="conflict",
        affected_count=0,
        before_records=[],
        after_records=[],
        error_code="REVISION_CONFLICT",
    )

    assert _metric([expectation], [effect], [_contract()])["passed"] is True
    wrong = deepcopy(effect)
    wrong["error_code"] = "OTHER_CONFLICT"
    wrong["receipt"]["error_code"] = "OTHER_CONFLICT"
    assert _metric([expectation], [wrong], [_contract()])["passed"] is False


def test_not_executed_requires_complete_terminal_trace_and_absent_effect() -> None:
    expectation = _expectation(
        status="not_executed",
        affected_count=0,
        expected_before={},
        expected_after={},
    )

    complete = _metric([expectation], [], [_contract()], terminal_recorded=True)
    incomplete = _metric([expectation], [], [_contract()], terminal_recorded=False)
    executed = _metric([expectation], [_effect()], [_contract()], terminal_recorded=True)

    assert complete["passed"] is True
    assert incomplete["passed"] is False
    assert executed["passed"] is False


def test_missing_and_forged_receipts_fail() -> None:
    missing = _effect()
    missing["receipt"] = None
    forged = _effect()
    forged["receipt"]["request_checksum"] = "e" * 64

    assert "missing_receipt" in _metric([_expectation()], [missing], [_contract()])["reason"]
    assert "forged_receipt" in _metric([_expectation()], [forged], [_contract()])["reason"]


def test_request_and_contract_checksums_are_strict_and_identity_bound() -> None:
    invalid_request = _effect()
    invalid_request["request_checksum"] = "not-a-sha"
    wrong_contract = _change_effect_identity(
        _effect(),
        "contract_checksum",
        "e" * 64,
    )

    assert _metric([_expectation()], [invalid_request], [_contract()])["score"] == 0.0
    result = _metric([_expectation()], [wrong_contract], [_contract()])
    assert result["score"] == 0.0
    assert "effect_contract_checksum" in result["reason"]


def test_replayed_effect_is_one_logical_effect_not_a_duplicate() -> None:
    replayed = _effect(replayed=True)

    assert _metric([_expectation()], [replayed], [_contract()])["passed"] is True
    assert _metric(
        [_expectation()],
        [replayed, deepcopy(replayed)],
        [_contract()],
    )["passed"] is False


def test_duplicate_record_ids_cannot_forge_affected_count() -> None:
    duplicate_before = [
        {
            "record_id": "record-1",
            "name": "A",
            "tier": "basic",
            "active": True,
        },
        {
            "record_id": "record-1",
            "name": "A",
            "tier": "basic",
            "active": True,
        },
    ]
    duplicate_after = [
        {
            "record_id": "record-1",
            "name": "A",
            "tier": "pro",
            "active": True,
        },
        {
            "record_id": "record-1",
            "name": "A",
            "tier": "pro",
            "active": True,
        },
    ]
    effect = _effect(
        affected_count=2,
        before_records=duplicate_before,
        after_records=duplicate_after,
    )
    expectation = _expectation(affected_count=2)

    result = _metric([expectation], [effect], [_contract()])

    assert result["passed"] is False
    assert "duplicate_record_id" in result["reason"]


def test_update_rejects_replacing_one_affected_record_with_another() -> None:
    effect = _effect(
        after_records=[
            {
                "record_id": "record-2",
                "name": "A",
                "tier": "pro",
                "active": True,
            }
        ]
    )

    result = _metric([_expectation()], [effect], [_contract()])

    assert result["passed"] is False
    assert "record_id_set" in result["reason"]


def test_each_side_requires_nonempty_unique_record_ids() -> None:
    missing = _effect(
        after_records=[{"name": "A", "tier": "pro", "active": True}]
    )
    empty = _effect(
        after_records=[
            {"record_id": " ", "name": "A", "tier": "pro", "active": True}
        ]
    )

    assert "record_id" in _metric([_expectation()], [missing], [_contract()])["reason"]
    assert "record_id" in _metric([_expectation()], [empty], [_contract()])["reason"]


@pytest.mark.parametrize(
    "invalid",
    [
        {**_expectation(), "allow_loose": True},
        {**_expectation(), "affected_count": "1"},
        _expectation(expected_after={"tier": float("nan")}),
        _expectation(expected_after={"record_id": "forged"}),
        _expectation(expected_after={"客户": "forged"}),
        _expectation(
            status="noop",
            affected_count=1,
            expected_before={},
            expected_after={},
        ),
        _expectation(
            status="conflict",
            affected_count=0,
            expected_before={},
            expected_after={},
        ),
        _expectation(
            operation="insert",
            expected_before={"name": "old"},
            expected_after={"name": "new"},
        ),
        _expectation(
            operation="delete",
            expected_before={"name": "old"},
            expected_after={"name": "new"},
        ),
    ],
)
def test_expectation_schema_rejects_nonfinite_extra_private_and_invalid_states(invalid: dict) -> None:
    with pytest.raises(ValidationError):
        EvaluationEffectExpectation.model_validate(invalid)


def test_unknown_business_field_and_private_field_injection_fail_metric() -> None:
    unknown_expectation = _expectation(expected_after={"secret_field": SECRET})
    injected_effect = _effect()
    injected_effect["private_payload"] = SECRET

    assert "unknown_expected_field" in _metric(
        [unknown_expectation],
        [_effect()],
        [_contract()],
    )["reason"]
    assert _metric([_expectation()], [injected_effect], [_contract()])["score"] == 0.0


@pytest.mark.parametrize("area", ["expectation", "effect", "contract"])
def test_private_unknown_keys_do_not_leak_via_validation_location(area: str) -> None:
    expectation, effect, contract = _expectation(), _effect(), _contract()
    {"expectation": expectation, "effect": effect, "contract": contract}[area][SECRET] = True
    metric = _metric([expectation], [effect], [contract])
    assert metric["score"] == 0.0
    assert SECRET not in json.dumps(metric, ensure_ascii=False)
    if area == "effect":
        with pytest.raises(ValueError) as error:
            safe_write_evidence([effect])
        assert SECRET not in str(error.value)


def test_safe_projection_contains_no_output_receipt_rows_or_field_values() -> None:
    effect = _effect()

    projected = safe_write_evidence([effect])
    serialized = json.dumps(projected, ensure_ascii=False)

    assert projected == [
        {
            "node_ref": "update_customer",
            "operation": "update",
            "table_id": "table_customers",
            "schema_version": 3,
            "contract_checksum": CONTRACT_SHA,
            "request_checksum": REQUEST_SHA,
            "untouched_before_checksum": UNCHANGED_SHA,
            "untouched_after_checksum": UNCHANGED_SHA,
            "affected_count": 1,
            "status": "applied",
            "replayed": False,
            "error_code": None,
        }
    ]
    for forbidden in (
        SECRET,
        "private_effect",
        "output",
        "receipt",
        "record_id",
        "record-1",
        "tier",
        "basic",
        "pro",
    ):
        assert forbidden not in serialized


def test_safe_projection_rejects_count_or_untouched_hash_injection() -> None:
    bad_count = _effect(affected_count=2)
    bad_hash = _effect()
    bad_hash["private_effect"]["untouched_after_checksum"] = "b" * 64

    with pytest.raises(ValueError):
        safe_write_evidence([bad_count])
    with pytest.raises(ValueError):
        safe_write_evidence([bad_hash])


def test_safe_projection_rejects_duplicate_or_replaced_record_ids() -> None:
    duplicate = _effect(
        operation="insert",
        affected_count=2,
        before_records=[],
        after_records=[
            {"record_id": "same", "name": "A", "tier": "pro", "active": True},
            {"record_id": "same", "name": "B", "tier": "pro", "active": True},
        ],
    )
    replaced = _effect(
        after_records=[
            {"record_id": "record-2", "name": "A", "tier": "pro", "active": True}
        ]
    )

    with pytest.raises(ValueError):
        safe_write_evidence([duplicate])
    with pytest.raises(ValueError):
        safe_write_evidence([replaced])


def test_private_capture_allows_100_large_records_under_16_mib() -> None:
    records = [
        {"record_id": f"record-{index}", "name": "A", "blob": "x" * 100_000}
        for index in range(100)
    ]
    effect = _effect(
        node_ref="create_many",
        operation="insert",
        affected_count=100,
        before_records=[],
        after_records=records,
    )
    expectation = _expectation(
        node_ref="create_many",
        operation="insert",
        affected_count=100,
        expected_before={},
        expected_after={"name": "A"},
    )
    contract = _contract(
        node_ref="create_many",
        operation="insert",
        schema_fields=["name", "blob"],
        writable_fields=["name"],
    )

    result = _metric([expectation], [effect], [contract])

    assert result["passed"] is True
    assert safe_write_evidence([effect])[0]["affected_count"] == 100


def test_private_capture_rejects_each_record_over_256_kib() -> None:
    effect = _effect(
        node_ref="create_large",
        operation="insert",
        before_records=[],
        after_records=[
            {"record_id": "record-large", "name": "A", "blob": "x" * 270_000}
        ],
    )
    expectation = _expectation(
        node_ref="create_large",
        operation="insert",
        expected_before={},
        expected_after={"name": "A"},
    )
    contract = _contract(
        node_ref="create_large",
        operation="insert",
        schema_fields=["name", "blob"],
        writable_fields=["name"],
    )

    assert _metric([expectation], [effect], [contract])["score"] == 0.0


def test_private_capture_rejects_combined_arrays_over_16_mib() -> None:
    records = [
        {"record_id": f"record-{index}", "name": "A", "blob": "x" * 170_000}
        for index in range(100)
    ]
    effect = _effect(
        node_ref="create_too_many",
        operation="insert",
        affected_count=100,
        before_records=[],
        after_records=records,
    )
    expectation = _expectation(
        node_ref="create_too_many",
        operation="insert",
        affected_count=100,
        expected_before={},
        expected_after={"name": "A"},
    )
    contract = _contract(
        node_ref="create_too_many",
        operation="insert",
        schema_fields=["name", "blob"],
        writable_fields=["name"],
    )

    assert _metric([expectation], [effect], [contract])["score"] == 0.0


def test_metric_details_are_bounded_and_never_contain_private_values() -> None:
    expectation = _expectation(expected_after={"tier": SECRET})
    result = _metric([expectation], [_effect()], [_contract()])
    serialized = json.dumps(result, ensure_ascii=False)

    assert set(result["details"]) == {"matched", "failed", "missing"}
    assert len(result["reason"]) <= 500
    assert SECRET not in serialized
