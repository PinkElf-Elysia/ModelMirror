from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.meta_agent.schemas import MetaPlannerScope
from server.workflow_native.node_contracts import (
    DataTableDeletePlannerConfig,
    DataTableInsertPlannerConfig,
    DataTableUpdatePlannerConfig,
    DataTableWriteGrant,
    NodePolicyService,
    validate_controlled_write_authority,
    workflow_node_contract_registry,
)


def _filter():
    return {"ref": "active", "field": "status", "operator": "eq", "value": "open"}


def _grant(**updates):
    return {"table_id": "table_demo", "operations": ["insert", "update"], "writable_fields": ["status"], **updates}


def test_write_grants_default_closed_and_independent_from_reads():
    assert MetaPlannerScope(data_table_ids=["table_demo"]).data_table_write_grants == []
    scope = MetaPlannerScope(data_table_write_grants=[_grant()])
    assert scope.data_table_ids == []
    assert scope.data_table_write_grants[0].max_affected_rows == 1


@pytest.mark.parametrize("updates", [
    {"operations": []}, {"operations": ["query"]}, {"operations": ["insert", "insert"]},
    {"writable_fields": []}, {"writable_fields": ["status", "status"]},
    {"writable_fields": ["revision"]}, {"writable_fields": ["record_id"]},
    {"writable_fields": ["table.field"]}, {"max_affected_rows": 0},
    {"max_affected_rows": 101}, {"max_affected_rows": True}, {"max_affected_rows": "2"},
    {"schema_version": 1}, {"operation_id": "forged"}, {"table_id": " table_demo"},
])
def test_grant_rejects_unsafe_or_ambiguous_input(updates):
    with pytest.raises(ValidationError):
        DataTableWriteGrant.model_validate(_grant(**updates))


def test_grant_canonical_order_and_duplicates():
    grant = DataTableWriteGrant.model_validate(_grant(operations=["update", "insert"], writable_fields=["status", "name"]))
    assert grant.operations == ["insert", "update"]
    assert grant.writable_fields == ["name", "status"]
    with pytest.raises(ValidationError):
        MetaPlannerScope(data_table_write_grants=[_grant(), _grant()])
    assert DataTableWriteGrant(table_id="table_demo", operations=["delete"]).writable_fields == []


@pytest.mark.parametrize("extra", ["tableId", "versionPolicy", "pinnedSchemaVersion", "sourceHandle", "valuesVariable", "expectedRecords", "writeGrant", "compiler_checksum", "failure_action", "retry_mode"])
def test_planner_write_config_cannot_inject_native_contract(extra):
    with pytest.raises(ValidationError):
        DataTableInsertPlannerConfig.model_validate({extra: "forged"})


def test_write_config_values_and_predicate_contracts():
    assert DataTableInsertPlannerConfig().value_source == "input"
    assert DataTableUpdatePlannerConfig(filter=_filter()).max_affected_rows == 1
    assert DataTableDeletePlannerConfig(filter=_filter()).max_affected_rows == 1
    with pytest.raises(ValidationError):
        DataTableInsertPlannerConfig(values={"status": "open"})
    with pytest.raises(ValidationError):
        DataTableInsertPlannerConfig(value_source="literal")
    for values in ({"revision": 2}, {"score": float("nan")}, {"content": "x" * (256 * 1024)}):
        with pytest.raises(ValidationError):
            DataTableInsertPlannerConfig(value_source="literal", values=values)
    with pytest.raises(ValidationError):
        DataTableUpdatePlannerConfig(filter=_filter(), value_source="literal", values={})
    for filter_value in (None, {}, {"kind": "group", "items": []}):
        with pytest.raises(ValidationError):
            DataTableDeletePlannerConfig(filter=filter_value)


def test_delete_predicate_budget_and_refs_are_shared_with_read_contract():
    duplicate = {"kind": "group", "items": [_filter(), _filter()]}
    with pytest.raises(ValidationError):
        DataTableDeletePlannerConfig(filter=duplicate)
    nested = _filter()
    for _ in range(3):
        nested = {"kind": "group", "items": [nested]}
    with pytest.raises(ValidationError):
        DataTableDeletePlannerConfig(filter=nested)


def _native(**updates):
    return {"contractVersion": 2, "tableId": "table_demo", "versionPolicy": "pinned", "pinnedSchemaVersion": 1, "pinnedSchemaChecksum": "a" * 64,
            "writeGrant": _grant(), "valueSource": "literal", "literalValues": {"status": "open"}, "maxAffectedRows": 1, **updates}


@pytest.mark.parametrize("updates", [
    {"tableId": "another"}, {"pinnedSchemaVersion": True}, {"pinnedSchemaChecksum": "bad"},
    {"versionPolicy": "latest"}, {"maxAffectedRows": 2}, {"retryMode": "transient"},
    {"failureAction": "error_output"}, {"literalValues": {"secret": "x"}},
    {"expectedRecords": []}, {"isolationContext": {}}, {"evaluationMode": True},
])
def test_versioned_authority_rejects_bypass(updates):
    with pytest.raises(ValueError):
        validate_controlled_write_authority("data_table_insert", _native(**updates))


def test_versioned_policy_does_not_enable_legacy_evaluation_or_public_apps():
    policy = NodePolicyService(workflow_node_contract_registry)
    assert not policy.decision("data_table_insert", "evaluation").allowed
    assert policy.decision("data_table_insert", "workflow").allowed
    conditional = policy.decision("data_table_insert", "evaluation", node_data=_native())
    assert conditional.allowed and conditional.conditional
    for entry in ("app", "evolution"):
        assert not policy.decision("data_table_insert", entry, node_data=_native()).allowed
    contract = workflow_node_contract_registry.require("data_table_insert")
    assert contract.effective_execution({"contractVersion": 2}).security_category == "controlled_private_write"
    assert contract.effective_execution({}).security_category == "private_data"
