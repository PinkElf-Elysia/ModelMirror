from __future__ import annotations

import sqlite3

import pytest
from jsonschema import Draft202012Validator

from server.rag.rag_service import RagRetrievalUnavailableError
from server.workflow_native.node_contracts import workflow_node_contract_registry
from server.workflow_native.retry_policy import (
    RETRYABLE_NODE_KINDS,
    WorkflowRetryPolicyError,
    effective_can_wait,
    retry_delay_seconds,
    retry_enabled,
    retry_wait_id,
    strict_retry_failure,
    validate_knowledge_retry_evidence,
    validate_retry_config,
    workflow_node_retries_enabled,
)
from server.workflow_native.schemas import (
    NativeWorkflowDefinition,
    NativeWorkflowNode,
)
from server.workflow_native.secure_http import WorkflowHttpRequestError
from server.workflow_native.validate import (
    validate_node_configuration,
    validate_workflow_graph,
)
from server.xperts.validation import validate_xpert_workflow_graph


def _http_data(**updates: object) -> dict[str, object]:
    data: dict[str, object] = {
        "kind": "http_request",
        "contractVersion": 2,
        "method": "GET",
        "url": "https://example.test/status",
        "queryItems": [],
        "headerItems": [],
        "bodyMode": "none",
        "formFields": [],
        "authType": "none",
        "timeoutSeconds": 30,
        "redirectLimit": 0,
        "responseLimitBytes": 1_048_576,
        "responseMode": "auto",
        "statusPolicy": "success_only",
        "outputVariable": "http_result",
        "retryMode": "transient",
        "maxAttempts": 3,
    }
    data.update(updates)
    return data


def _literal_assign(node_id: str, output_variable: str, value: object = "value") -> dict:
    return {
        "id": node_id,
        "type": "variable_assign",
        "data": {
            "kind": "variable_assign",
            "contractVersion": 2,
            "outputVariable": output_variable,
            "valueSource": "literal",
            "literalValue": value,
        },
    }


def _variable_copy(node_id: str, source_variable: str, output_variable: str) -> dict:
    return {
        "id": node_id,
        "type": "variable_assign",
        "data": {
            "kind": "variable_assign",
            "contractVersion": 2,
            "outputVariable": output_variable,
            "valueSource": "variable",
            "sourceVariable": source_variable,
        },
    }


def _knowledge_proposal(
    node_id: str,
    content_variable: str,
    output_variable: str = "proposal_receipt",
) -> dict:
    return {
        "id": node_id,
        "type": "knowledge_write_proposal",
        "data": {
            "kind": "knowledge_write_proposal",
            "contractVersion": 1,
            "knowledgeBaseId": "kb_test",
            "titleTemplate": "Synthetic proposal",
            "contentVariable": content_variable,
            "tags": [],
            "outputVariable": output_variable,
        },
    }


def _retry_resume_flow(
    *,
    entry: dict | None = None,
    between_entry_and_retry: list[dict] | None = None,
    after_retry: list[dict] | None = None,
) -> NativeWorkflowDefinition:
    entry_node = entry or {
        "id": "input",
        "type": "input",
        "data": {"kind": "input", "variableName": "user_input"},
    }
    prefix = list(between_entry_and_retry or [])
    suffix = list(after_retry or [])
    retry = {"id": "retry", "type": "http_request", "data": _http_data()}
    output_variable = (
        str(suffix[-1]["data"].get("outputVariable") or "http_result")
        if suffix
        else "http_result"
    )
    output = {
        "id": "output",
        "type": "output",
        "data": {"kind": "output", "outputVariable": output_variable},
    }
    nodes = [entry_node, *prefix, retry, *suffix, output]
    edges = [
        {
            "id": f"edge-{index}",
            "source": nodes[index]["id"],
            "target": nodes[index + 1]["id"],
        }
        for index in range(len(nodes) - 1)
    ]
    return NativeWorkflowDefinition.model_validate({"nodes": nodes, "edges": edges})


def _xpert_retry_history_flow(
    *,
    history_variable: str,
    upstream_variable: str = "",
) -> NativeWorkflowDefinition:
    nodes = [
        {
            "id": "input",
            "type": "input",
            "data": {"kind": "input", "variableName": "user_input"},
        }
    ]
    if upstream_variable:
        nodes.append(_literal_assign("producer", upstream_variable, "ephemeral"))
    nodes.extend(
        [
            {"id": "retry", "type": "http_request", "data": _http_data()},
            {
                "id": "agent",
                "type": "workflow_agent",
                "data": {
                    "kind": "workflow_agent",
                    "agentName": "primary-agent",
                    "modelId": "test/model",
                    "rolePrompt": "Reliable assistant",
                    "taskInput": (
                        f"{{{{{history_variable}}}}}"
                        + (f" {{{{{upstream_variable}}}}}" if upstream_variable else "")
                    ),
                    "outputVariable": "agent_output",
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "agent_output"},
            },
        ]
    )
    return NativeWorkflowDefinition.model_validate(
        {
            "nodes": nodes,
            "edges": [
                {
                    "id": f"edge-{index}",
                    "source": nodes[index]["id"],
                    "target": nodes[index + 1]["id"],
                }
                for index in range(len(nodes) - 1)
            ],
        }
    )


def test_retry_contract_metadata_is_limited_to_three_nodes() -> None:
    supported = {
        contract.kind
        for contract in workflow_node_contract_registry.list()
        if contract.retry.supported
    }

    assert supported == RETRYABLE_NODE_KINDS
    assert len(workflow_node_contract_registry.list()) == 55
    for kind in RETRYABLE_NODE_KINDS:
        contract = workflow_node_contract_registry.require(kind)
        assert contract.retry.modes == ("none", "transient")
        assert contract.retry.max_attempts == (2, 3)
        assert contract.retry.backoff_seconds == (5, 30)
        assert contract.planner.enabled is False
        assert effective_can_wait({}, contract) is False
        assert effective_can_wait({"retryMode": "transient"}, contract) is True


def test_xpert_retry_accepts_custom_runtime_history_input() -> None:
    workflow = _xpert_retry_history_flow(history_variable="chat_history")

    result = validate_xpert_workflow_graph(
        workflow,
        history_variable="chat_history",
    )

    assert result.valid is True, result.issues
    assert not any(
        issue.code == "node_retry_resume_variable_unavailable"
        for issue in result.issues
    )


def test_xpert_retry_still_rejects_pre_retry_node_output() -> None:
    workflow = _xpert_retry_history_flow(
        history_variable="chat_history",
        upstream_variable="ephemeral_value",
    )

    result = validate_xpert_workflow_graph(
        workflow,
        history_variable="chat_history",
    )

    assert result.valid is False
    assert any(
        issue.code == "node_retry_resume_variable_unavailable"
        for issue in result.issues
    )


def test_xpert_retry_rejects_node_overwrite_of_runtime_history_input() -> None:
    workflow = _xpert_retry_history_flow(
        history_variable="chat_history",
        upstream_variable="chat_history",
    )

    result = validate_xpert_workflow_graph(
        workflow,
        history_variable="chat_history",
    )

    assert result.valid is False
    assert any(
        issue.code == "runtime_input_variable_producer_conflict"
        for issue in result.issues
    )


def test_xpert_retry_accepts_sensitive_history_consumed_only_before_wait() -> None:
    workflow = _retry_resume_flow(
        between_entry_and_retry=[
            _knowledge_proposal("proposal", "chat_history"),
        ],
        after_retry=[
            _literal_assign("result", "final_result", "completed"),
        ],
    )

    result = validate_xpert_workflow_graph(
        workflow,
        history_variable="chat_history",
    )

    assert result.valid is True, result.issues
    assert not any(
        issue.code == "node_retry_resume_variable_unavailable"
        for issue in result.issues
    )


def test_xpert_retry_rejects_sensitive_history_read_after_wait() -> None:
    workflow = _retry_resume_flow(
        between_entry_and_retry=[
            _knowledge_proposal("proposal", "chat_history"),
        ],
        after_retry=[
            _variable_copy("consumer", "chat_history", "copied_history"),
        ],
    )

    result = validate_xpert_workflow_graph(
        workflow,
        history_variable="chat_history",
    )

    assert result.valid is False
    assert any(
        issue.code == "node_retry_resume_variable_unavailable"
        for issue in result.issues
    )


def test_plain_workflow_cannot_claim_xpert_runtime_history_input() -> None:
    workflow = _xpert_retry_history_flow(history_variable="conversation_history")

    result = validate_workflow_graph(workflow)

    assert result.valid is False
    assert any(
        issue.code == "node_retry_resume_variable_unavailable"
        for issue in result.issues
    )


def test_retry_schema_is_optional_and_rejects_unknown_values() -> None:
    http = workflow_node_contract_registry.require("http_request")
    v2 = http.config_schema["anyOf"][1]
    validator = Draft202012Validator(v2)

    assert not list(validator.iter_errors(_http_data()))
    assert not list(
        validator.iter_errors(
            {key: value for key, value in _http_data().items() if key not in {"retryMode", "maxAttempts"}}
        )
    )
    assert list(validator.iter_errors(_http_data(retryMode="always")))
    assert list(validator.iter_errors(_http_data(maxAttempts=4)))


@pytest.mark.parametrize(
    ("updates", "code"),
    [
        ({"retryMode": "always"}, "INVALID_NODE_RETRY_MODE"),
        ({"maxAttempts": 4}, "INVALID_NODE_RETRY_MAX_ATTEMPTS"),
        ({"contractVersion": 1}, "NODE_RETRY_HTTP_V2_REQUIRED"),
        ({"method": "POST"}, "NODE_RETRY_HTTP_GET_REQUIRED"),
        ({"bodyMode": "text"}, "NODE_RETRY_HTTP_BODY_FORBIDDEN"),
    ],
)
def test_static_http_retry_eligibility_is_fail_closed(
    updates: dict[str, object],
    code: str,
) -> None:
    with pytest.raises(WorkflowRetryPolicyError) as error:
        validate_retry_config(_http_data(**updates), node_kind="http_request")
    assert error.value.code == code


def test_missing_retry_fields_preserve_legacy_non_waiting_behavior() -> None:
    contract = workflow_node_contract_registry.require("http_request")
    data = {key: value for key, value in _http_data().items() if key not in {"retryMode", "maxAttempts"}}

    validate_retry_config(data, node_kind="http_request")
    assert retry_enabled(data) is False
    assert effective_can_wait(data, contract) is False


def test_graph_validation_rejects_retry_waits_for_rss_email_and_knowledge_proposal() -> None:
    for entry_kind, entry_data, expected in (
        (
            "rss_event_entry",
            {
                "kind": "rss_event_entry",
                "contractVersion": 1,
                "feedUrl": "https://example.test/feed.xml",
                "pollIntervalMinutes": 15,
                "eventVariable": "feed_event",
                "itemVariable": "feed_item",
            },
            "rss_persistent_wait_forbidden",
        ),
        (
            "email_event_entry",
            {
                "kind": "email_event_entry",
                "contractVersion": 1,
                "host": "imap.example.test",
                "credentialId": "cred_test",
                "pollIntervalMinutes": 15,
                "eventVariable": "email_event",
                "messageVariable": "email_message",
                "contentVariable": "email_content",
            },
            "email_persistent_wait_forbidden",
        ),
    ):
        workflow = NativeWorkflowDefinition.model_validate(
            {
                "nodes": [
                    {"id": "entry", "type": entry_kind, "data": entry_data},
                    {"id": "http", "type": "http_request", "data": _http_data()},
                    {
                        "id": "output",
                        "type": "output",
                        "data": {"kind": "output", "outputVariable": "http_result"},
                    },
                ],
                "edges": [
                    {"id": "edge-1", "source": "entry", "target": "http"},
                    {"id": "edge-2", "source": "http", "target": "output"},
                ],
            }
        )
        codes = {issue.code for issue in validate_workflow_graph(workflow).issues}
        assert expected in codes

    proposal_workflow = NativeWorkflowDefinition.model_validate(
        {
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {"kind": "input", "variableName": "user_input"},
                },
                {"id": "http", "type": "http_request", "data": _http_data()},
                {
                    "id": "proposal",
                    "type": "knowledge_write_proposal",
                    "data": {
                        "kind": "knowledge_write_proposal",
                        "contractVersion": 1,
                        "knowledgeBaseId": "kb_test",
                        "titleTemplate": "Proposed update",
                        "contentVariable": "http_result",
                        "tags": [],
                        "outputVariable": "proposal_receipt",
                    },
                },
                {
                    "id": "output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "proposal_receipt"},
                },
            ],
            "edges": [
                {"id": "edge-1", "source": "input", "target": "http"},
                {"id": "edge-2", "source": "http", "target": "proposal"},
                {"id": "edge-3", "source": "proposal", "target": "output"},
            ],
        }
    )
    codes = {
        issue.code for issue in validate_workflow_graph(proposal_workflow).issues
    }
    assert "knowledge_proposal_after_wait_forbidden" in codes


def test_retry_resume_requires_value_before_use_even_if_redefined_later() -> None:
    workflow = _retry_resume_flow(
        between_entry_and_retry=[_literal_assign("before", "shared_value", "before")],
        after_retry=[
            _variable_copy("consumer", "shared_value", "copied_value"),
            _literal_assign("later", "shared_value", "after"),
        ],
    )

    codes = {issue.code for issue in validate_workflow_graph(workflow).issues}

    assert "node_retry_resume_variable_unavailable" in codes


def test_retry_resume_accepts_value_recreated_after_retry_before_use() -> None:
    workflow = _retry_resume_flow(
        after_retry=[
            _literal_assign("recreate", "shared_value", "after"),
            _variable_copy("consumer", "shared_value", "copied_value"),
        ],
    )

    codes = {issue.code for issue in validate_workflow_graph(workflow).issues}

    assert "node_retry_resume_variable_unavailable" not in codes


def test_retry_resume_rejects_ephemeral_schedule_event_reference() -> None:
    workflow = _retry_resume_flow(
        entry={
            "id": "schedule",
            "type": "scheduled_start",
            "data": {
                "kind": "scheduled_start",
                "scheduleType": "interval",
                "intervalSeconds": 30,
                "timezone": "UTC",
                "eventVariable": "schedule_event",
            },
        },
        after_retry=[_variable_copy("consumer", "schedule_event", "copied_event")],
    )

    codes = {issue.code for issue in validate_workflow_graph(workflow).issues}

    assert "node_retry_resume_variable_unavailable" in codes


def test_retry_resume_rejects_knowledge_proposal_sensitive_input() -> None:
    workflow = _retry_resume_flow(
        after_retry=[
            {
                "id": "proposal",
                "type": "knowledge_write_proposal",
                "data": {
                    "kind": "knowledge_write_proposal",
                    "contractVersion": 1,
                    "knowledgeBaseId": "kb_test",
                    "titleTemplate": "Synthetic proposal",
                    "contentVariable": "user_input",
                    "tags": [],
                    "outputVariable": "proposal_receipt",
                },
            }
        ],
    )

    codes = {issue.code for issue in validate_workflow_graph(workflow).issues}

    assert "node_retry_resume_variable_unavailable" in codes


def test_retry_resume_rejects_parallel_sibling_that_can_remain_queued() -> None:
    workflow = NativeWorkflowDefinition.model_validate(
        {
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {"kind": "input", "variableName": "user_input"},
                },
                _literal_assign("before", "private_value", "private"),
                {"id": "retry", "type": "http_request", "data": _http_data()},
                _variable_copy("sibling", "private_value", "copied_value"),
                {
                    "id": "retry-output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "http_result"},
                },
                {
                    "id": "sibling-output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "copied_value"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "input", "target": "before"},
                {"id": "e2", "source": "before", "target": "retry"},
                {"id": "e3", "source": "before", "target": "sibling"},
                {"id": "e4", "source": "retry", "target": "retry-output"},
                {"id": "e5", "source": "sibling", "target": "sibling-output"},
            ],
        }
    )

    codes = {issue.code for issue in validate_workflow_graph(workflow).issues}

    assert "node_retry_resume_variable_unavailable" in codes


def test_retry_resume_accepts_parallel_sibling_using_reconstructible_input() -> None:
    workflow = NativeWorkflowDefinition.model_validate(
        {
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {"kind": "input", "variableName": "user_input"},
                },
                {"id": "retry", "type": "http_request", "data": _http_data()},
                _variable_copy("sibling", "user_input", "copied_value"),
                {
                    "id": "retry-output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "http_result"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "input", "target": "retry"},
                {"id": "e2", "source": "input", "target": "sibling"},
                {"id": "e3", "source": "retry", "target": "retry-output"},
            ],
        }
    )

    result = validate_workflow_graph(workflow)
    codes = {issue.code for issue in result.issues}

    assert result.valid is True, result.issues
    assert "node_retry_resume_variable_unavailable" not in codes


def test_retry_resume_rejects_branch_local_value_at_later_escape_cut() -> None:
    workflow = NativeWorkflowDefinition.model_validate(
        {
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {"kind": "input", "variableName": "user_input"},
                },
                _literal_assign("producer", "private_value"),
                {"id": "retry", "type": "http_request", "data": _http_data()},
                _variable_copy("consumer", "private_value", "copied_value"),
                {
                    "id": "output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "http_result"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "input", "target": "producer"},
                {"id": "e2", "source": "input", "target": "retry"},
                {"id": "e3", "source": "producer", "target": "consumer"},
                {"id": "e4", "source": "retry", "target": "output"},
            ],
        }
    )

    codes = {issue.code for issue in validate_workflow_graph(workflow).issues}

    assert "node_retry_resume_variable_unavailable" in codes


def test_retry_resume_rejects_runtime_state_on_parallel_escape_branch() -> None:
    workflow = NativeWorkflowDefinition.model_validate(
        {
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {"kind": "input", "variableName": "user_input"},
                },
                {
                    "id": "early-output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "user_input"},
                },
                {"id": "retry", "type": "http_request", "data": _http_data()},
                {
                    "id": "retry-output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "http_result"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "input", "target": "early-output"},
                {"id": "e2", "source": "input", "target": "retry"},
                {"id": "e3", "source": "retry", "target": "retry-output"},
            ],
        }
    )

    codes = {issue.code for issue in validate_workflow_graph(workflow).issues}

    assert "node_retry_resume_runtime_state_unavailable" in codes


def test_retry_resume_rejects_creator_handoff_state_from_agent_ancestor() -> None:
    workflow = NativeWorkflowDefinition.model_validate(
        {
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {"kind": "input", "variableName": "user_input"},
                },
                {
                    "id": "agent",
                    "type": "workflow_agent",
                    "data": {
                        "kind": "workflow_agent",
                        "agentName": "author",
                        "modelId": "openai/gpt-4o-mini",
                        "rolePrompt": "Draft a reusable skill.",
                        "taskInput": "{{user_input}}",
                        "outputVariable": "agent_output",
                    },
                },
                {"id": "retry", "type": "http_request", "data": _http_data()},
                {
                    "id": "output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "http_result"},
                },
                {
                    "id": "creator",
                    "type": "runtime_middleware",
                    "data": {
                        "kind": "runtime_middleware",
                        "runtimeMiddlewareId": "skill_creator",
                        "runtimeMiddlewareKind": "runtime_middleware.skill_creator",
                        "runtimeMiddlewareConfig": {
                            "authoring_mode": "creator_handoff"
                        },
                    },
                },
            ],
            "edges": [
                {"id": "e1", "source": "input", "target": "agent"},
                {"id": "e2", "source": "agent", "target": "retry"},
                {"id": "e3", "source": "retry", "target": "output"},
                {
                    "id": "bind-creator",
                    "source": "creator",
                    "target": "agent",
                    "sourceHandle": "middleware-binding",
                    "targetHandle": "middleware",
                },
            ],
        }
    )

    codes = {issue.code for issue in validate_workflow_graph(workflow).issues}

    assert "node_retry_resume_runtime_state_unavailable" in codes


def test_retry_resume_does_not_treat_exclusive_condition_branch_as_parallel() -> None:
    workflow = NativeWorkflowDefinition.model_validate(
        {
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {"kind": "input", "variableName": "payload"},
                },
                {
                    "id": "condition",
                    "type": "condition",
                    "data": {
                        "kind": "condition",
                        "contractVersion": 2,
                        "inputVariable": "payload",
                        "operator": "is_null",
                        "valueType": "null",
                    },
                },
                {"id": "retry", "type": "http_request", "data": _http_data()},
                _literal_assign("sibling", "branch_value", "not selected"),
                {
                    "id": "retry-output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "http_result"},
                },
                {
                    "id": "sibling-output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "branch_value"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "input", "target": "condition"},
                {
                    "id": "e2",
                    "source": "condition",
                    "sourceHandle": "true",
                    "target": "retry",
                },
                {
                    "id": "e3",
                    "source": "condition",
                    "sourceHandle": "false",
                    "target": "sibling",
                },
                {"id": "e4", "source": "retry", "target": "retry-output"},
                {"id": "e5", "source": "sibling", "target": "sibling-output"},
            ],
        }
    )

    codes = {issue.code for issue in validate_workflow_graph(workflow).issues}

    assert "node_retry_resume_variable_unavailable" not in codes


def test_retry_resume_detects_parallel_success_edges_from_routable_ancestor() -> None:
    upstream_data = _http_data(
        outputVariable="upstream_result",
        retryMode="none",
        maxAttempts=2,
        failureAction="error_output",
        errorVariable="upstream_error",
    )
    workflow = NativeWorkflowDefinition.model_validate(
        {
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {"kind": "input", "variableName": "user_input"},
                },
                {"id": "upstream", "type": "http_request", "data": upstream_data},
                {"id": "retry", "type": "http_request", "data": _http_data()},
                _variable_copy("sibling", "upstream_result", "copied_value"),
                {
                    "id": "upstream-error",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "upstream_error"},
                },
                {
                    "id": "retry-output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "http_result"},
                },
                {
                    "id": "sibling-output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "copied_value"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "input", "target": "upstream"},
                {"id": "e2", "source": "upstream", "target": "retry"},
                {"id": "e3", "source": "upstream", "target": "sibling"},
                {
                    "id": "e4",
                    "source": "upstream",
                    "sourceHandle": "error",
                    "target": "upstream-error",
                },
                {"id": "e5", "source": "retry", "target": "retry-output"},
                {"id": "e6", "source": "sibling", "target": "sibling-output"},
            ],
        }
    )

    codes = {issue.code for issue in validate_workflow_graph(workflow).issues}

    assert "node_retry_resume_variable_unavailable" in codes


def test_retry_resume_tracks_data_merge_inputs_by_handle_and_preserves_them_after_merge() -> None:
    workflow = NativeWorkflowDefinition.model_validate(
        {
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {"kind": "input", "variableName": "user_input"},
                },
                {"id": "retry", "type": "http_request", "data": _http_data()},
                _literal_assign("left", "left_rows", [{"id": 1}]),
                _literal_assign("right", "right_rows", [{"id": 2}]),
                {
                    "id": "merge",
                    "type": "data_merge",
                    "data": {
                        "kind": "data_merge",
                        "contractVersion": 1,
                        "mergeMode": "append",
                        "leftVariable": "left_rows",
                        "rightVariable": "right_rows",
                        "outputVariable": "merged_rows",
                        "keyFields": [],
                    },
                },
                _variable_copy("consumer", "left_rows", "left_copy"),
                {
                    "id": "output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "left_copy"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "input", "target": "retry"},
                {"id": "e2", "source": "retry", "target": "left"},
                {"id": "e3", "source": "retry", "target": "right"},
                {
                    "id": "e4",
                    "source": "left",
                    "target": "merge",
                    "targetHandle": "left",
                },
                {
                    "id": "e5",
                    "source": "right",
                    "target": "merge",
                    "targetHandle": "right",
                },
                {"id": "e6", "source": "merge", "target": "consumer"},
                {"id": "e7", "source": "consumer", "target": "output"},
            ],
        }
    )

    result = validate_workflow_graph(workflow)
    codes = {issue.code for issue in result.issues}

    assert result.valid is True, result.issues
    assert "node_retry_resume_variable_unavailable" not in codes


def test_retry_resume_rejects_data_merge_when_merge_can_remain_queued() -> None:
    workflow = NativeWorkflowDefinition.model_validate(
        {
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {"kind": "input", "variableName": "user_input"},
                },
                _literal_assign("left", "left_rows", [{"id": 1}]),
                _literal_assign("right", "right_rows", [{"id": 2}]),
                {"id": "retry", "type": "http_request", "data": _http_data()},
                {
                    "id": "merge",
                    "type": "data_merge",
                    "data": {
                        "kind": "data_merge",
                        "contractVersion": 1,
                        "mergeMode": "append",
                        "leftVariable": "left_rows",
                        "rightVariable": "right_rows",
                        "outputVariable": "merged_rows",
                        "keyFields": [],
                    },
                },
                {
                    "id": "output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "http_result"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "input", "target": "left"},
                {"id": "e2", "source": "input", "target": "right"},
                {"id": "e3", "source": "input", "target": "retry"},
                {
                    "id": "e4",
                    "source": "left",
                    "target": "merge",
                    "targetHandle": "left",
                },
                {
                    "id": "e5",
                    "source": "right",
                    "target": "merge",
                    "targetHandle": "right",
                },
                {"id": "e6", "source": "retry", "target": "output"},
            ],
        }
    )

    codes = {issue.code for issue in validate_workflow_graph(workflow).issues}

    assert "node_retry_resume_variable_unavailable" in codes


def test_retry_resume_accepts_exclusive_fallback_that_produces_same_join_value() -> None:
    workflow = NativeWorkflowDefinition.model_validate(
        {
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {"kind": "input", "variableName": "payload"},
                },
                {
                    "id": "condition",
                    "type": "condition",
                    "data": {
                        "kind": "condition",
                        "contractVersion": 2,
                        "inputVariable": "payload",
                        "operator": "equals",
                        "valueType": "text",
                        "value": "synthetic",
                    },
                },
                {"id": "retry", "type": "http_request", "data": _http_data()},
                _literal_assign("fallback", "http_result", {"fallback": True}),
                _variable_copy("consumer", "http_result", "selected_result"),
                {
                    "id": "output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "selected_result"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "input", "target": "condition"},
                {
                    "id": "e2",
                    "source": "condition",
                    "sourceHandle": "true",
                    "target": "retry",
                },
                {
                    "id": "e3",
                    "source": "condition",
                    "sourceHandle": "false",
                    "target": "fallback",
                },
                {"id": "e4", "source": "retry", "target": "consumer"},
                {"id": "e5", "source": "fallback", "target": "consumer"},
                {"id": "e6", "source": "consumer", "target": "output"},
            ],
        }
    )

    result = validate_workflow_graph(workflow)
    codes = {issue.code for issue in result.issues}

    assert result.valid is True, result.issues
    assert "node_retry_resume_variable_unavailable" not in codes


def test_validate_node_configuration_surfaces_safe_retry_errors() -> None:
    node = NativeWorkflowNode(
        id="http",
        type="http_request",
        data=_http_data(method="POST"),
    )

    codes = {issue.code for issue in validate_node_configuration(node, "http_request")}
    assert "node_retry_http_get_required" in codes


def test_strict_retry_classifier_does_not_guess_from_exception_text() -> None:
    timeout = WorkflowHttpRequestError("HTTP_TIMEOUT", "HTTP request timed out.")
    network = WorkflowHttpRequestError("HTTP_NETWORK_ERROR", "HTTP request failed.")
    protocol = WorkflowHttpRequestError(
        "HTTP_RESPONSE_PROTOCOL_INVALID",
        "HTTP response transport or decoding was invalid.",
    )
    other_http = WorkflowHttpRequestError(
        "HTTP_REQUEST_FAILED",
        "HTTP request failed before a valid response was received.",
    )
    dns = WorkflowHttpRequestError(
        "HTTP_DNS_UNAVAILABLE",
        "HTTP hostname resolution is temporarily unavailable.",
    )
    forbidden = WorkflowHttpRequestError(
        "HTTP_STATUS_NOT_SUCCESSFUL",
        "HTTP request returned an unsuccessful status.",
        status_code=403,
    )
    retryable_status = WorkflowHttpRequestError(
        "HTTP_STATUS_NOT_SUCCESSFUL",
        "HTTP request returned an unsuccessful status.",
        status_code=503,
    )
    guessed_lock = sqlite3.OperationalError("database is locked")
    coded_lock = sqlite3.OperationalError("redacted")
    coded_lock.sqlite_errorcode = sqlite3.SQLITE_BUSY

    assert strict_retry_failure("http_request", timeout) is not None
    assert strict_retry_failure("http_request", network) is not None
    assert strict_retry_failure("http_request", retryable_status) is not None
    assert strict_retry_failure("http_request", protocol) is None
    assert strict_retry_failure("http_request", other_http) is None
    assert strict_retry_failure("http_request", dns) is None
    assert strict_retry_failure("http_request", forbidden) is None
    assert strict_retry_failure("data_table_query", guessed_lock) is None
    assert strict_retry_failure("data_table_query", coded_lock) is not None

    index_missing = RagRetrievalUnavailableError(
        "rag_vector_index_unavailable", "safe"
    )
    backend_missing = RagRetrievalUnavailableError(
        "rag_vector_backend_unavailable", "safe"
    )
    assert strict_retry_failure("knowledge_retrieval", index_missing) is None
    assert strict_retry_failure("knowledge_retrieval", backend_missing) is not None

    imposter_type = type("RagRetrievalUnavailableError", (Exception,), {})
    imposter = imposter_type()
    imposter.code = "rag_vector_backend_unavailable"
    assert strict_retry_failure("knowledge_retrieval", imposter) is None


def test_wait_identity_delay_and_feature_flag_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    first = retry_wait_id("task-1", "http-1", 2)
    assert first == retry_wait_id("task-1", "http-1", 2)
    assert first != retry_wait_id("task-1", "http-1", 3)
    assert first.startswith("node_retry:")
    assert retry_delay_seconds(2) == 5
    assert retry_delay_seconds(3) == 30
    assert retry_delay_seconds(2, 120) == 120
    assert retry_delay_seconds(2, 999) == 300
    monkeypatch.delenv("WORKFLOW_NODE_RETRIES_ENABLED", raising=False)
    assert workflow_node_retries_enabled() is False
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    assert workflow_node_retries_enabled() is True


def test_knowledge_retry_evidence_is_local_ready_and_version_bound() -> None:
    fulltext = {
        "version_id": "ragv_1",
        "kb_id": "kb_1",
        "status": "active",
        "index_schema_version": 3,
        "retrieval_profile": {
            "mode": "fulltext",
            "rerank_enabled": False,
            "rerank_provider": "none",
        },
        "lexical_index_ready": True,
        "index_contract": {"contract_version": "rag-index-contract-v3"},
    }
    first = validate_knowledge_retry_evidence("kb_1", fulltext, {})
    assert first == validate_knowledge_retry_evidence("kb_1", fulltext, {})
    assert first != validate_knowledge_retry_evidence(
        "kb_1", {**fulltext, "version_id": "ragv_2"}, {}
    )

    hash_vector = {
        **fulltext,
        "retrieval_profile": {
            "mode": "hybrid",
            "rerank_enabled": False,
            "rerank_provider": "none",
        },
        "embedding_profile": {
            "effective": {
                "provider": "hash",
                "model": "deterministic-hash-v1",
                "ready": True,
            },
            "embedding_space_fingerprint": "safe-space",
        },
        "vector_index_ready": True,
    }
    evidence = {"runtime_vector_backend_readiness": {"ready": True}}
    assert len(validate_knowledge_retry_evidence("kb_1", hash_vector, evidence)) == 64

    with pytest.raises(WorkflowRetryPolicyError) as remote:
        validate_knowledge_retry_evidence(
            "kb_1",
            {
                **hash_vector,
                "embedding_profile": {
                    "effective": {
                        "provider": "openai_compatible",
                        "model": "remote-model",
                        "ready": True,
                    }
                },
            },
            evidence,
        )
    assert remote.value.code == "NODE_RETRY_KNOWLEDGE_TARGET_INELIGIBLE"

    with pytest.raises(WorkflowRetryPolicyError) as rerank:
        validate_knowledge_retry_evidence(
            "kb_1",
            {
                **fulltext,
                "retrieval_profile": {
                    "mode": "fulltext",
                    "rerank_enabled": True,
                    "rerank_provider": "api",
                },
            },
            {},
        )
    assert rerank.value.code == "NODE_RETRY_KNOWLEDGE_RERANK_FORBIDDEN"

    for mismatched_evidence in (
        {"kb_id": "kb_other", "version_id": "ragv_1"},
        {"kb_id": "kb_1", "version_id": "ragv_other"},
        {"version_id": "ragv_1"},
    ):
        with pytest.raises(WorkflowRetryPolicyError) as mismatch:
            validate_knowledge_retry_evidence(
                "kb_1",
                fulltext,
                mismatched_evidence,
            )
        assert mismatch.value.code == "NODE_RETRY_KNOWLEDGE_TARGET_INVALID"
