"""Bounded differential checks against the production comparison semantics."""
from itertools import product

import pytest
from pydantic import ValidationError

from server.meta_agent.control_domains import ControlDomainUnproven, value_partition
from server.meta_agent.control_flow import _schema_seed, analyze_control_flow
from server.meta_agent.schemas import GraphIntentControlEdgeV3
from server.tests.test_meta_planner_control_flow import _branch_intent
from server.workflow_native.control_data import WorkflowControlDataError, evaluate_typed_condition
from server.workflow_native.node_contracts import WorkflowValueSchema


def signatures(values, predicates):
    found = set()
    for value in values:
        result = []
        for field, rule in predicates:
            try:
                result.append(evaluate_typed_condition(value, field=field,
                    operator=rule["operator"], value_type=rule.get("valueType", "json"),
                    expected=rule.get("value")))
            except WorkflowControlDataError as exc:
                result.append(exc.code)
        found.add(tuple(result))
    return found


def assert_covered(schema, predicates, probes):
    proven = value_partition(schema, predicates, seed=_schema_seed)
    assert signatures(probes, predicates) <= signatures(proven, predicates)


@pytest.mark.parametrize("patterns", [("a", "b"), ("aa", "aba"), ("", "bb")])
def test_string_substrings_and_equality_cover_exhaustive_short_inputs(patterns):
    predicates = [("", {"operator": "contains", "valueType": "text", "value": value}) for value in patterns]
    predicates += [("", {"operator": "equals", "valueType": "text", "value": "ab"})]
    probes = ["".join(chars) for length in range(6) for chars in product("abc", repeat=length)]
    assert_covered(WorkflowValueSchema(type="string"), predicates, probes)


@pytest.mark.parametrize("item_schema,atoms", [
    (WorkflowValueSchema(type="integer"), [0, 1, 2]),
    (WorkflowValueSchema(type="boolean"), [False, True]),
    (WorkflowValueSchema(type="any"), [None, 0, True, "a"]),
])
def test_array_membership_and_exact_equality_cover_small_product(item_schema, atoms):
    predicates = [("", {"operator": "contains", "value": item}) for item in atoms[:2]]
    predicates += [("", {"operator": "equals", "value": atoms[:2]})]
    probes = [list(items) for length in range(5) for items in product(atoms, repeat=length)]
    assert_covered(WorkflowValueSchema(type="array", items=item_schema), predicates, probes)


def test_union_missing_null_and_object_equality_cover_small_product():
    schema = WorkflowValueSchema(type="object", properties={"score": WorkflowValueSchema(
        any_of=(WorkflowValueSchema(type="number"), WorkflowValueSchema(type="string")), nullable=True)})
    predicates = [("score", {"operator": "lt", "valueType": "number", "value": 5}),
                  ("score", {"operator": "is_null"}),
                  ("", {"operator": "equals", "value": {"score": 7}})]
    probes = [{}, *({"score": value} for value in (None, -1, 0, 5, 7, "a", "")), {"score": 7, "extra": True}]
    assert_covered(schema, predicates, probes)


@pytest.mark.parametrize("extra", [{"enum": [0, 7, 16]}, {"minLength": 1}, {"minItems": 2}])
def test_unmodeled_schema_constraints_cannot_enter_proof(extra):
    with pytest.raises(ValidationError):
        WorkflowValueSchema.model_validate({"type": "any", **extra})


def test_future_schema_extension_fails_closed_instead_of_filtering_witnesses():
    class FutureSchema(WorkflowValueSchema):
        min_items: int = 2

    with pytest.raises(ControlDomainUnproven, match="CONTROL_SCHEMA_UNSUPPORTED"):
        value_partition(FutureSchema(type="array"), [], seed=_schema_seed)


def test_large_string_alphabet_is_rejected_before_dfa_expansion():
    value = "".join(chr(0x100 + index) for index in range(2048))
    with pytest.raises(ControlDomainUnproven, match="CONTROL_PROOF_BUDGET"):
        value_partition(WorkflowValueSchema(type="string"),
                        [("", {"operator": "contains", "value": value})], seed=_schema_seed)


def test_schema_depth_is_bounded_before_seed_or_value_validation():
    schema = WorkflowValueSchema(type="integer")
    for _ in range(20):
        schema = WorkflowValueSchema(type="array", items=schema)
    with pytest.raises(ControlDomainUnproven, match="CONTROL_SCHEMA_LIMIT"):
        value_partition(schema, [], seed=_schema_seed)


def test_correlated_routes_use_joint_scenario_limit_not_independent_product():
    graph = _branch_intent()
    template = graph.nodes.pop(0)
    template.kind = "multi_route"
    template.config = {"routes": [
        {"label": value, "operator": "equals", "value_type": "text", "value": value}
        for value in ("a", "b", "c")
    ]}
    graph.control_edges = []
    for index in range(5):
        router = template.model_copy(deep=True)
        router.ref = f"route_{index}"
        graph.nodes.append(router)
        for outcome in ("case_1", "case_2", "case_3", "default"):
            graph.control_edges.append(GraphIntentControlEdgeV3(source_ref=router.ref,
                outcome_ref=outcome, target_ref="rejected" if outcome == "default" else "approved"))
    assert analyze_control_flow(graph)["scenario_count"] == 4
