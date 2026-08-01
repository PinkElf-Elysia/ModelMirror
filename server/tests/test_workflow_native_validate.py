from __future__ import annotations

import httpx
import pytest
import pytest_asyncio

from server.main import app, parse_workflow_tool_policy_list
from server.xpert_runtime.tool_policy import ToolPermissionPolicy


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


def linear_workflow() -> dict:
    return {
        "id": "draft",
        "title": "linear",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {
                    "kind": "input",
                    "variableName": "user_input",
                },
            },
            {
                "id": "llm",
                "type": "llm",
                "data": {
                    "kind": "llm",
                    "modelId": "openai/gpt-4o-mini",
                    "prompt": "请回答 {{user_input}}",
                    "outputVariable": "llm_output",
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {
                    "kind": "output",
                    "outputVariable": "llm_output",
                },
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "llm"},
            {"id": "e2", "source": "llm", "target": "output"},
        ],
    }


async def validate(client: httpx.AsyncClient, workflow: dict) -> dict:
    response = await client.post(
        "/api/workflow-native/validate",
        json={"workflow": workflow},
    )
    assert response.status_code == 200, response.text
    return response.json()


def issue_codes(data: dict) -> set[str]:
    return {issue["code"] for issue in data["issues"]}


@pytest.mark.asyncio
async def test_valid_linear_workflow_returns_topological_order(
    client: httpx.AsyncClient,
) -> None:
    data = await validate(client, linear_workflow())

    assert data["valid"] is True
    assert data["issues"] == []
    assert data["order"] == ["input", "llm", "output"]


@pytest.mark.asyncio
async def test_missing_input_and_output_nodes_are_structured_issues(
    client: httpx.AsyncClient,
) -> None:
    workflow = {
        "id": "draft",
        "title": "empty",
        "nodes": [],
        "edges": [],
    }

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert {"missing_input_node", "missing_output_node"}.issubset(issue_codes(data))


@pytest.mark.asyncio
async def test_invalid_edge_reference_is_reported(client: httpx.AsyncClient) -> None:
    workflow = linear_workflow()
    workflow["edges"].append({"id": "bad", "source": "input", "target": "missing"})

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "invalid_edge_reference" in issue_codes(data)


@pytest.mark.asyncio
async def test_cycle_graph_is_rejected(client: httpx.AsyncClient) -> None:
    workflow = linear_workflow()
    workflow["edges"].append({"id": "cycle", "source": "output", "target": "input"})

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "cycle_detected" in issue_codes(data)


@pytest.mark.asyncio
async def test_llm_missing_model_prompt_and_output_variable(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1]["data"] = {"kind": "llm"}

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert {
        "missing_llm_model",
        "missing_llm_prompt",
        "missing_llm_output_variable",
    }.issubset(issue_codes(data))


@pytest.mark.asyncio
async def test_missing_template_and_output_variables_are_reported(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1]["data"]["prompt"] = "请回答 {{missing_input}}"
    workflow["nodes"][2]["data"]["outputVariable"] = "missing_output"

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert {
        "missing_template_variable",
        "missing_output_variable_reference",
    }.issubset(issue_codes(data))


@pytest.mark.asyncio
async def test_validate_variable_assign_node(client: httpx.AsyncClient) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "assign",
        "type": "variable_assign",
        "data": {
            "kind": "variable_assign",
            "variableName": "assigned_text",
            "template": "hello {{user_input}}",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "assigned_text"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "assign"},
        {"id": "e2", "source": "assign", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True

    workflow["nodes"][1]["data"].pop("variableName")
    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "missing_variable_assign_name" in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_http_request_required_fields(client: httpx.AsyncClient) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "http",
        "type": "http_request",
        "data": {
            "kind": "http_request",
            "url": "https://example.com?q={{user_input}}",
            "method": "GET",
            "headersJson": "{}",
            "outputVariable": "http_output",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "http_output"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "http"},
        {"id": "e2", "source": "http", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True

    workflow["nodes"][1]["data"]["headersJson"] = "{bad json"
    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "invalid_http_request_headers_json" in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_list_operation_operators(client: httpx.AsyncClient) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "list",
        "type": "list_operation",
        "data": {
            "kind": "list_operation",
            "inputVariable": "user_input",
            "operator": "length",
            "outputVariable": "list_output",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "list_output"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "list"},
        {"id": "e2", "source": "list", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True

    workflow["nodes"][1]["data"]["operator"] = "join"
    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "missing_list_operation_separator" in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_iteration_required_fields(client: httpx.AsyncClient) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "iteration",
        "type": "iteration",
        "data": {
            "kind": "iteration",
            "inputVariable": "user_input",
            "iterationVariable": "item",
            "itemTemplate": "done {{item}}",
            "outputVariable": "iteration_output",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "iteration_output"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "iteration"},
        {"id": "e2", "source": "iteration", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True

    workflow["nodes"][1]["data"].pop("itemTemplate")
    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "missing_iteration_template" in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_template_transform_required(client: httpx.AsyncClient) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "template",
        "type": "template_transform",
        "data": {
            "kind": "template_transform",
            "template": "result={{user_input}}",
            "outputVariable": "template_output",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "template_output"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "template"},
        {"id": "e2", "source": "template", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True

    workflow["nodes"][1]["data"].pop("template")
    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "missing_template_transform_template" in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_variable_aggregator_variables_non_empty(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "aggregator",
        "type": "variable_aggregator",
        "data": {
            "kind": "variable_aggregator",
            "variableNames": "user_input",
            "outputTemplate": "{name}={value}\n",
            "outputVariable": "aggregated_output",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "aggregated_output"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "aggregator"},
        {"id": "e2", "source": "aggregator", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True

    workflow["nodes"][1]["data"]["variableNames"] = ""
    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "missing_aggregator_variable_names_empty" in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_parameter_extractor_required_fields(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "extractor",
        "type": "parameter_extractor",
        "data": {
            "kind": "parameter_extractor",
            "inputVariable": "user_input",
            "schema": "name, email_address",
            "modelId": "deepseek/deepseek-chat",
            "outputVariable": "parameters_json",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "parameters_json"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "extractor"},
        {"id": "e2", "source": "extractor", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True

    workflow["nodes"][1]["data"].pop("modelId")
    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "missing_parameter_extractor_model_id" in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_knowledge_retrieval_top_k(client: httpx.AsyncClient) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "retrieval",
        "type": "knowledge_retrieval",
        "data": {
            "kind": "knowledge_retrieval",
            "queryVariable": "user_input",
            "top_k": "3",
            "outputVariable": "rag_context",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "rag_context"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "retrieval"},
        {"id": "e2", "source": "retrieval", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True

    workflow["nodes"][1]["data"]["top_k"] = "0"
    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "invalid_knowledge_retrieval_top_k" in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_knowledge_citation_node_ok(client: httpx.AsyncClient) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "citation",
        "type": "knowledge_citation",
        "data": {
            "kind": "knowledge_citation",
            "queryVariable": "user_input",
            "knowledgeBaseId": "",
            "top_k": "4",
            "outputVariable": "citation_anchors_json",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "citation_anchors_json"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "citation"},
        {"id": "e2", "source": "citation", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True
    assert data["issues"] == []


@pytest.mark.asyncio
async def test_validate_knowledge_citation_required_fields(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "citation",
        "type": "knowledge_citation",
        "data": {
            "kind": "knowledge_citation",
            "queryVariable": "",
            "top_k": "11",
            "outputVariable": "",
        },
    }
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "citation"},
        {"id": "e2", "source": "citation", "target": "output"},
    ]

    data = await validate(client, workflow)
    codes = issue_codes(data)

    assert data["valid"] is False
    assert "missing_knowledge_citation_query_variable" in codes
    assert "invalid_knowledge_citation_top_k" in codes
    assert "missing_knowledge_citation_output_variable" in codes


@pytest.mark.asyncio
async def test_knowledge_citation_query_variable_must_be_declared(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "citation",
        "type": "knowledge_citation",
        "data": {
            "kind": "knowledge_citation",
            "queryVariable": "missing_query",
            "top_k": "3",
            "outputVariable": "citation_anchors_json",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "citation_anchors_json"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "citation"},
        {"id": "e2", "source": "citation", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "missing_knowledge_citation_query_variable_reference" in issue_codes(data)


@pytest.mark.asyncio
async def test_knowledge_citation_output_variable_is_declared(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "citation",
        "type": "knowledge_citation",
        "data": {
            "kind": "knowledge_citation",
            "queryVariable": "user_input",
            "top_k": "2",
            "outputVariable": "citation_anchors_json",
        },
    }
    workflow["nodes"][2] = {
        "id": "template",
        "type": "template_transform",
        "data": {
            "kind": "template_transform",
            "template": "Citations: {{citation_anchors_json}}",
            "outputVariable": "final_text",
        },
    }
    workflow["nodes"].append(
        {
            "id": "output",
            "type": "output",
            "data": {
                "kind": "output",
                "outputVariable": "final_text",
            },
        }
    )
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "citation"},
        {"id": "e2", "source": "citation", "target": "template"},
        {"id": "e3", "source": "template", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True


@pytest.mark.asyncio
async def test_validate_document_extractor_required_fields(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "document",
        "type": "document_extractor",
        "data": {
            "kind": "document_extractor",
            "sourcePathVariable": "user_input",
            "outputVariable": "document_text",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "document_text"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "document"},
        {"id": "e2", "source": "document", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True

    workflow["nodes"][1]["data"].pop("sourcePathVariable")
    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "missing_document_extractor_source_path" in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_human_intervention_required_fields(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "human",
        "type": "human_intervention",
        "data": {
            "kind": "human_intervention",
            "prompt": "请确认：{{user_input}}",
            "outputVariable": "human_input",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "human_input"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "human"},
        {"id": "e2", "source": "human", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True

    workflow["nodes"][1]["data"].pop("prompt")
    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "missing_prompt" in issue_codes(data)

    workflow["nodes"][1]["data"]["prompt"] = "请确认：{{user_input}}"
    workflow["nodes"][1]["data"].pop("outputVariable")
    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "missing_output_variable" in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_human_intervention_template_reference(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "human",
        "type": "human-in-the-loop",
        "data": {
            "kind": "human-in-the-loop",
            "prompt": "请确认：{{missing_value}}",
            "outputVariable": "human_input",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "human_input"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "human"},
        {"id": "e2", "source": "human", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "missing_template_variable" in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_question_classifier_ok(client: httpx.AsyncClient) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "classifier",
        "type": "question_classifier",
        "data": {
            "kind": "question_classifier",
            "inputVariable": "user_input",
            "categories": '{"投诉":["差","投诉","退款"],"咨询":["咨询","如何","怎么"]}',
            "outputVariable": "category",
            "defaultCategory": "未知",
            "matchMode": "contains_any",
            "caseSensitive": "false",
            "useLlmFallback": "false",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "category"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "classifier"},
        {"id": "e2", "source": "classifier", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True


@pytest.mark.asyncio
async def test_validate_question_classifier_invalid_categories_json(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "classifier",
        "type": "question_classifier",
        "data": {
            "kind": "question_classifier",
            "inputVariable": "user_input",
            "categories": "{invalid json",
            "outputVariable": "category",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "category"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "classifier"},
        {"id": "e2", "source": "classifier", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "invalid_categories_json" in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_mcp_tool_ok_and_invalid_json(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "mcp",
        "type": "mcp_tool",
        "data": {
            "kind": "mcp_tool",
            "toolName": "filesystem_read_file",
            "argumentsJson": '{"path":"{{user_input}}"}',
            "outputVariable": "mcp_output",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "mcp_output"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "mcp"},
        {"id": "e2", "source": "mcp", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True

    workflow["nodes"][1]["data"]["argumentsJson"] = "{invalid json"
    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "invalid_arguments_json" in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_time_tool_ok_and_invalid_operation(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "time",
        "type": "time_tool",
        "data": {
            "kind": "time_tool",
            "operation": "now_iso",
            "formatString": "%Y-%m-%d %H:%M:%S",
            "outputVariable": "current_time",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "current_time"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "time"},
        {"id": "e2", "source": "time", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True

    workflow["nodes"][1]["data"]["operation"] = "unsupported_op"
    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "invalid_time_operation" in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_agent_ok(client: httpx.AsyncClient) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "agent",
        "type": "agent",
        "data": {
            "kind": "agent",
            "agentMode": "tool_first",
            "instruction": "请处理：{{user_input}}",
            "modelId": "deepseek/deepseek-chat",
            "toolNames": "",
            "outputVariable": "agent_output",
            "maxIterations": "5",
            "temperature": "0.7",
            "promptSuffix": "",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_output"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "agent"},
        {"id": "e2", "source": "agent", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True


@pytest.mark.asyncio
async def test_validate_agent_invalid_mode(client: httpx.AsyncClient) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "agent",
        "type": "agent",
        "data": {
            "kind": "agent",
            "agentMode": "autopilot",
            "instruction": "请处理：{{user_input}}",
            "modelId": "deepseek/deepseek-chat",
            "outputVariable": "agent_output",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_output"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "agent"},
        {"id": "e2", "source": "agent", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "invalid_agent_mode" in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_agent_task_ok(client: httpx.AsyncClient) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "agent_task",
        "type": "agent_task",
        "data": {
            "kind": "agent_task",
            "taskTitle": "Plan {{user_input}}",
            "taskInput": "Create a plan for {{user_input}}",
            "assignedAgent": "workflow-planner",
            "outputVariable": "agent_task_id",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_task_id"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "agent_task"},
        {"id": "e2", "source": "agent_task", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True
    assert data["issues"] == []


@pytest.mark.asyncio
async def test_validate_agent_task_missing_input_and_output_variable(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "agent_task",
        "type": "agent_task",
        "data": {
            "kind": "agent_task",
            "taskTitle": "Plan {{user_input}}",
            "assignedAgent": "workflow-planner",
        },
    }
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "agent_task"},
        {"id": "e2", "source": "agent_task", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert {
        "missing_agent_task_input",
        "missing_agent_task_output_variable",
    }.issubset(issue_codes(data))


@pytest.mark.asyncio
async def test_validate_agent_task_template_reference(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "agent_task",
        "type": "agent_task",
        "data": {
            "kind": "agent_task",
            "taskTitle": "Plan {{missing_value}}",
            "taskInput": "Create a plan for {{user_input}}",
            "assignedAgent": "workflow-planner",
            "outputVariable": "agent_task_id",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_task_id"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "agent_task"},
        {"id": "e2", "source": "agent_task", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "missing_agent_task_template_variable" in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_agent_task_output_can_be_used_downstream(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "agent_task",
        "type": "agent_task",
        "data": {
            "kind": "agent_task",
            "taskTitle": "Plan {{user_input}}",
            "taskInput": "Create a plan for {{user_input}}",
            "assignedAgent": "workflow-planner",
            "outputVariable": "agent_task_id",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_task_id"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "agent_task"},
        {"id": "e2", "source": "agent_task", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True
    assert "missing_output_variable_reference" not in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_runtime_middleware_ok(client: httpx.AsyncClient) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "middleware",
        "type": "runtime_middleware",
        "data": {
            "kind": "runtime_middleware",
            "title": "系统提示词注入器",
            "description": "运行时中间件原型节点。",
            "runtimeMiddlewareId": "system_prompt_injector",
            "runtimeMiddlewareKind": "runtime_middleware.system_prompt_injector",
            "runtimeMiddlewareConfig": {"system_prompt": "请基于 {{user_input}} 用一句话回答。"},
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "user_input"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "middleware"},
        {"id": "e2", "source": "middleware", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True


@pytest.mark.asyncio
async def test_validate_system_prompt_injector_missing_prompt(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "middleware",
        "type": "runtime_middleware",
        "data": {
            "kind": "runtime_middleware",
            "runtimeMiddlewareId": "system_prompt_injector",
            "runtimeMiddlewareKind": "runtime_middleware.system_prompt_injector",
            "runtimeMiddlewareConfig": {},
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "user_input"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "middleware"},
        {"id": "e2", "source": "middleware", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "missing_runtime_middleware_system_prompt" in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_system_prompt_injector_template_reference(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "middleware",
        "type": "runtime_middleware",
        "data": {
            "kind": "runtime_middleware",
            "runtimeMiddlewareId": "system_prompt_injector",
            "runtimeMiddlewareKind": "runtime_middleware.system_prompt_injector",
            "runtimeMiddlewareConfig": {
                "system_prompt": "请基于 {{missing_value}} 用一句话回答。",
            },
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "user_input"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "middleware"},
        {"id": "e2", "source": "middleware", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "missing_runtime_middleware_template_variable" in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_runtime_middleware_missing_metadata(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "middleware",
        "type": "runtime_middleware",
        "data": {
            "kind": "runtime_middleware",
            "title": "中间件节点",
        },
    }
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "middleware"},
        {"id": "e2", "source": "middleware", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is False
    codes = issue_codes(data)
    assert "missing_runtime_middleware_id" in codes
    assert "missing_runtime_middleware_kind" in codes


@pytest.mark.asyncio
async def test_valid_tool_policy_middleware(client: httpx.AsyncClient) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "policy",
        "type": "runtime_middleware",
        "data": {
            "kind": "runtime_middleware",
            "runtimeMiddlewareId": "tool_policy",
            "runtimeMiddlewareKind": "runtime_middleware.tool_policy",
            "runtimeMiddlewareConfig": {
                "denied_tools": "bad_tool",
                "allow_by_default": True,
            },
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "user_input"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "policy"},
        {"id": "e2", "source": "policy", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True
    assert "invalid_runtime_middleware_tool_policy" not in issue_codes(data)


@pytest.mark.asyncio
async def test_invalid_tool_policy_allow_by_default(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "policy",
        "type": "runtime_middleware",
        "data": {
            "kind": "runtime_middleware",
            "runtimeMiddlewareId": "tool_policy",
            "runtimeMiddlewareKind": "runtime_middleware.tool_policy",
            "runtimeMiddlewareConfig": {
                "allow_by_default": "not_a_boolean",
            },
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "user_input"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "policy"},
        {"id": "e2", "source": "policy", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "invalid_runtime_middleware_tool_policy" in issue_codes(data)


def test_tool_policy_textarea_tool_list_parser() -> None:
    assert parse_workflow_tool_policy_list("fetch\nsearch, read，write") == {
        "fetch",
        "search",
        "read",
        "write",
    }
    assert parse_workflow_tool_policy_list(["fetch", " search "]) == {
        "fetch",
        "search",
    }
    assert parse_workflow_tool_policy_list(None) == set()


def test_tool_policy_denied_tools_rejected() -> None:
    policy = ToolPermissionPolicy(
        denied_tools={"bad_tool"},
        allow_by_default=True,
    )

    assert policy.is_allowed("good_tool") is True
    assert policy.is_allowed("bad_tool") is False
    assert policy.is_allowed("unknown_tool") is True


def test_tool_policy_allow_by_default_false() -> None:
    policy = ToolPermissionPolicy(
        allowed_tools={"good_tool"},
        allow_by_default=False,
    )

    assert policy.is_allowed("good_tool") is True
    assert policy.is_allowed("bad_tool") is False
    assert policy.is_allowed("unknown_tool") is False


def middleware_binding_workflow() -> dict:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "workflow_agent",
        "type": "workflow_agent",
        "data": {
            "kind": "workflow_agent",
            "agentName": "bound-agent",
            "modelId": "openai/gpt-4o-mini",
            "rolePrompt": "Answer accurately.",
            "taskInput": "{{user_input}}",
            "outputVariable": "agent_output",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_output"
    workflow["nodes"].append(
        {
            "id": "compression",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "context_compression",
                "runtimeMiddlewareKind": "runtime_middleware.context_compression",
                "middlewarePriority": "20",
                "runtimeMiddlewareConfig": {
                    "max_context_tokens": 4096,
                    "trigger_ratio": 0.8,
                    "keep_recent_messages": 6,
                },
            },
        }
    )
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "workflow_agent"},
        {"id": "e2", "source": "workflow_agent", "target": "output"},
        {
            "id": "bind",
            "source": "compression",
            "target": "workflow_agent",
            "sourceHandle": "middleware-binding",
            "targetHandle": "middleware",
        },
    ]
    return workflow


@pytest.mark.asyncio
async def test_bound_middleware_does_not_participate_in_control_topology(
    client: httpx.AsyncClient,
) -> None:
    data = await validate(client, middleware_binding_workflow())

    assert data["valid"] is True
    assert "cycle_detected" not in issue_codes(data)
    assert data["order"].index("input") < data["order"].index("workflow_agent")
    assert data["order"].index("workflow_agent") < data["order"].index("output")
    assert "compression" not in data["order"]


@pytest.mark.asyncio
async def test_xpert_file_memory_validates_recall_mode_and_budgets(
    client: httpx.AsyncClient,
) -> None:
    workflow = middleware_binding_workflow()
    middleware = workflow["nodes"][-1]
    middleware["id"] = "file-memory"
    middleware["data"].update(
        {
            "runtimeMiddlewareId": "xpert_file_memory",
            "runtimeMiddlewareKind": "runtime_middleware.xpert_file_memory",
            "runtimeMiddlewareConfig": {
                "recall_mode": "unknown",
                "max_detail_chars_per_turn": 20_000,
                "max_detail_chars_per_session": 10_000,
            },
        }
    )
    workflow["edges"][-1]["source"] = "file-memory"

    invalid = await validate(client, workflow)
    codes = issue_codes(invalid)
    assert "invalid_xpert_file_memory_recall_mode" in codes
    assert "invalid_xpert_file_memory_budget" in codes

    middleware["data"]["runtimeMiddlewareConfig"].update(
        {
            "recall_mode": "hybrid",
            "max_detail_chars_per_session": 60_000,
        }
    )
    valid = await validate(client, workflow)
    assert valid["valid"] is True, valid["issues"]


@pytest.mark.asyncio
async def test_scheduler_requires_runtime_tool_mode(
    client: httpx.AsyncClient,
) -> None:
    workflow = middleware_binding_workflow()
    middleware = workflow["nodes"][-1]
    middleware["id"] = "scheduler"
    middleware["data"].update(
        {
            "runtimeMiddlewareId": "scheduler",
            "runtimeMiddlewareKind": "runtime_middleware.scheduler",
            "runtimeMiddlewareConfig": {
                "allow_agent_create": True,
                "default_timezone": "Asia/Shanghai",
                "max_runs_per_day": 10,
            },
        }
    )
    workflow["edges"][-1]["source"] = "scheduler"

    invalid = await validate(client, workflow)
    assert "scheduler_requires_runtime_tool_mode" in issue_codes(invalid)

    workflow["nodes"][1]["data"]["toolMode"] = "mcp_tools"
    valid = await validate(client, workflow)
    assert valid["valid"] is True, valid["issues"]


@pytest.mark.asyncio
@pytest.mark.parametrize("middleware_id", ["xpert_authoring", "skill_creator"])
async def test_authoring_middleware_requires_runtime_tool_mode_and_scoped_ids(
    client: httpx.AsyncClient,
    middleware_id: str,
) -> None:
    workflow = middleware_binding_workflow()
    middleware = workflow["nodes"][-1]
    middleware["id"] = middleware_id
    scope_key = (
        "allowed_xpert_ids"
        if middleware_id == "xpert_authoring"
        else "allowed_draft_ids"
    )
    middleware["data"].update(
        {
            "runtimeMiddlewareId": middleware_id,
            "runtimeMiddlewareKind": f"runtime_middleware.{middleware_id}",
            "runtimeMiddlewareConfig": {
                "allow_create": True,
                "allow_update": True,
                scope_key: "allowed-resource",
            },
        }
    )
    workflow["edges"][-1]["source"] = middleware_id

    invalid = await validate(client, workflow)
    assert "authoring_requires_runtime_tool_mode" in issue_codes(invalid)

    workflow["nodes"][1]["data"]["toolMode"] = "mcp_tools"
    valid = await validate(client, workflow)
    assert valid["valid"] is True, valid["issues"]


@pytest.mark.asyncio
async def test_automation_middleware_configuration_is_validated(
    client: httpx.AsyncClient,
) -> None:
    workflow = middleware_binding_workflow()
    middleware = workflow["nodes"][-1]
    middleware["id"] = "ralph"
    middleware["data"].update(
        {
            "runtimeMiddlewareId": "ralph_loop",
            "runtimeMiddlewareKind": "runtime_middleware.ralph_loop",
            "runtimeMiddlewareConfig": {
                "max_iterations": 0,
                "max_output_chars": 100,
            },
        }
    )
    workflow["edges"][-1]["source"] = "ralph"
    invalid_ralph = await validate(client, workflow)
    assert "invalid_runtime_middleware_config" in issue_codes(invalid_ralph)

    middleware["data"].update(
        {
            "runtimeMiddlewareId": "knowledge_writer",
            "runtimeMiddlewareKind": "runtime_middleware.knowledge_writer",
            "runtimeMiddlewareConfig": {
                "knowledge_base_id": "",
                "auto_propose_verified_output": True,
            },
        }
    )
    invalid_writer = await validate(client, workflow)
    assert "knowledge_writer_kb_required" in issue_codes(invalid_writer)

    middleware["data"].update(
        {
            "runtimeMiddlewareId": "plugin_hooks",
            "runtimeMiddlewareKind": "runtime_middleware.plugin_hooks",
            "runtimeMiddlewareConfig": {"skill_ids": "", "fail_closed": False},
        }
    )
    invalid_hooks = await validate(client, workflow)
    assert "plugin_hooks_skills_required" in issue_codes(invalid_hooks)


@pytest.mark.asyncio
async def test_middleware_binding_rejects_invalid_target(
    client: httpx.AsyncClient,
) -> None:
    workflow = middleware_binding_workflow()
    workflow["edges"][-1]["target"] = "output"

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "invalid_middleware_binding" in issue_codes(data)


@pytest.mark.asyncio
async def test_middleware_binding_rejects_multiple_agents(
    client: httpx.AsyncClient,
) -> None:
    workflow = middleware_binding_workflow()
    workflow["nodes"].append(
        {
            "id": "second_agent",
            "type": "workflow_agent",
            "data": {
                "kind": "workflow_agent",
                "agentName": "second-agent",
                "modelId": "openai/gpt-4o-mini",
                "rolePrompt": "Answer accurately.",
                "taskInput": "{{user_input}}",
                "outputVariable": "second_output",
            },
        }
    )
    workflow["edges"].append(
        {
            "id": "bind-second",
            "source": "compression",
            "target": "second_agent",
            "sourceHandle": "middleware-binding",
            "targetHandle": "middleware",
        }
    )

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "duplicate_middleware_binding" in issue_codes(data)


@pytest.mark.asyncio
async def test_middleware_binding_rejects_control_edge_mixing(
    client: httpx.AsyncClient,
) -> None:
    workflow = middleware_binding_workflow()
    workflow["edges"].append(
        {"id": "control", "source": "input", "target": "compression"}
    )

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "mixed_middleware_binding_and_control_flow" in issue_codes(data)


@pytest.mark.asyncio
async def test_middleware_binding_rejects_wrong_source_handle(
    client: httpx.AsyncClient,
) -> None:
    workflow = middleware_binding_workflow()
    workflow["edges"][-1].pop("sourceHandle")

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "invalid_middleware_binding" in issue_codes(data)


@pytest.mark.asyncio
async def test_structured_output_middleware_validates_schema_and_priority(
    client: httpx.AsyncClient,
) -> None:
    workflow = middleware_binding_workflow()
    middleware = workflow["nodes"][-1]
    middleware["data"].update(
        {
            "runtimeMiddlewareId": "structured_output",
            "runtimeMiddlewareKind": "runtime_middleware.structured_output",
            "middlewarePriority": "1001",
            "runtimeMiddlewareConfig": {
                "schema_json": '{"type":"object","properties":{"answer":{"type":"unknown"}}}',
                "repair_attempts": 2,
            },
        }
    )

    data = await validate(client, workflow)

    codes = issue_codes(data)
    assert data["valid"] is False
    assert "invalid_runtime_middleware_priority" in codes
    assert "invalid_runtime_middleware_structured_output_schema" in codes
    assert "invalid_runtime_middleware_config" in codes


@pytest.mark.asyncio
async def test_human_in_the_loop_middleware_validates_rules_and_timeout(
    client: httpx.AsyncClient,
) -> None:
    workflow = middleware_binding_workflow()
    middleware = workflow["nodes"][-1]
    middleware["data"].update(
        {
            "runtimeMiddlewareId": "human_in_the_loop",
            "runtimeMiddlewareKind": "runtime_middleware.human_in_the_loop",
            "runtimeMiddlewareConfig": {
                "interrupt_on_tools": "search,*",
                "final_confirmation": True,
                "timeout_seconds": 3600,
                "max_revision_rounds": 2,
            },
        }
    )

    data = await validate(client, workflow)
    assert data["valid"] is True

    middleware["data"]["runtimeMiddlewareConfig"] = {
        "interrupt_on_tools": "",
        "final_confirmation": False,
        "timeout_seconds": 10,
        "max_revision_rounds": 6,
    }
    data = await validate(client, workflow)
    codes = issue_codes(data)
    assert data["valid"] is False
    assert "inactive_runtime_middleware_hitl" in codes
    assert "invalid_runtime_middleware_config" in codes


@pytest.mark.asyncio
async def test_sandbox_shell_requires_bound_hitl_and_valid_config(
    client: httpx.AsyncClient,
) -> None:
    workflow = middleware_binding_workflow()
    sandbox = workflow["nodes"][-1]
    sandbox["id"] = "sandbox-shell"
    sandbox["data"].update(
        {
            "runtimeMiddlewareId": "sandbox_shell",
            "runtimeMiddlewareKind": "runtime_middleware.sandbox_shell",
            "runtimeMiddlewareConfig": {
                "allowed_commands": "python,node,rg",
                "timeout_seconds": 60,
                "require_approval": True,
            },
        }
    )
    workflow["edges"][-1]["source"] = "sandbox-shell"

    missing_hitl = await validate(client, workflow)
    assert "sandbox_shell_requires_hitl" in issue_codes(missing_hitl)

    workflow["nodes"].append(
        {
            "id": "sandbox-hitl",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "human_in_the_loop",
                "runtimeMiddlewareKind": "runtime_middleware.human_in_the_loop",
                "middlewarePriority": "30",
                "runtimeMiddlewareConfig": {
                    "interrupt_on_tools": "sandbox_shell",
                    "final_confirmation": False,
                },
            },
        }
    )
    workflow["edges"].append(
        {
            "id": "bind-sandbox-hitl",
            "source": "sandbox-hitl",
            "target": "workflow_agent",
            "sourceHandle": "middleware-binding",
            "targetHandle": "middleware",
        }
    )
    valid = await validate(client, workflow)
    assert valid["valid"] is True, valid["issues"]

    sandbox["data"]["runtimeMiddlewareConfig"]["allowed_commands"] = "bash"
    invalid = await validate(client, workflow)
    assert "invalid_runtime_middleware_sandbox_commands" in issue_codes(invalid)


@pytest.mark.asyncio
async def test_validate_agent_task_node_ok(client: httpx.AsyncClient) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "task",
        "type": "agent_task",
        "data": {
            "kind": "agent_task",
            "taskTitle": "处理 {{user_input}}",
            "taskInput": "请规划：{{user_input}}",
            "assignedAgent": "workflow-planner",
            "outputVariable": "agent_task_id",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_task_id"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "task"},
        {"id": "e2", "source": "task", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True
    assert data["issues"] == []


@pytest.mark.asyncio
async def test_agent_task_missing_required_fields(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "task",
        "type": "agent_task",
        "data": {
            "kind": "agent_task",
            "taskTitle": "处理 {{user_input}}",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "user_input"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "task"},
        {"id": "e2", "source": "task", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is False
    codes = issue_codes(data)
    assert "missing_agent_task_input" in codes
    assert "missing_agent_task_output_variable" in codes


@pytest.mark.asyncio
async def test_agent_task_template_variable_must_be_declared(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "task",
        "type": "agent_task",
        "data": {
            "kind": "agent_task",
            "taskTitle": "处理 {{user_input}}",
            "taskInput": "请规划：{{missing_input}}",
            "assignedAgent": "workflow-planner",
            "outputVariable": "agent_task_id",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_task_id"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "task"},
        {"id": "e2", "source": "task", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "missing_agent_task_template_variable" in issue_codes(data)


@pytest.mark.asyncio
async def test_agent_task_output_variable_is_available_downstream(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "task",
        "type": "agent_task",
        "data": {
            "kind": "agent_task",
            "taskTitle": "处理 {{user_input}}",
            "taskInput": "{{user_input}}",
            "outputVariable": "agent_task_id",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_task_id"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "task"},
        {"id": "e2", "source": "task", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True
    assert "missing_output_variable_reference" not in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_workflow_agent_node_ok(client: httpx.AsyncClient) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "workflow_agent",
        "type": "workflow_agent",
        "data": {
            "kind": "workflow_agent",
            "agentName": "research-agent",
            "modelId": "deepseek/deepseek-chat",
            "rolePrompt": "你是研究智能体，输入来自 {{user_input}}。",
            "taskInput": "请处理：{{user_input}}",
            "outputVariable": "agent_output",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_output"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "workflow_agent"},
        {"id": "e2", "source": "workflow_agent", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True
    assert data["issues"] == []


@pytest.mark.asyncio
async def test_workflow_agent_missing_required_fields(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "workflow_agent",
        "type": "workflow_agent",
        "data": {
            "kind": "workflow_agent",
            "agentName": "research-agent",
            "modelId": "deepseek/deepseek-chat",
            "rolePrompt": "你是研究智能体。",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "user_input"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "workflow_agent"},
        {"id": "e2", "source": "workflow_agent", "target": "output"},
    ]

    data = await validate(client, workflow)

    codes = issue_codes(data)
    assert data["valid"] is False
    assert "missing_workflow_agent_task_input" in codes
    assert "missing_workflow_agent_output_variable" in codes


@pytest.mark.asyncio
async def test_workflow_agent_template_variable_must_be_declared(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "workflow_agent",
        "type": "workflow_agent",
        "data": {
            "kind": "workflow_agent",
            "agentName": "research-agent",
            "modelId": "deepseek/deepseek-chat",
            "rolePrompt": "你是研究智能体，引用 {{missing_role}}。",
            "taskInput": "请处理：{{missing_task}}",
            "outputVariable": "agent_output",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_output"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "workflow_agent"},
        {"id": "e2", "source": "workflow_agent", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is False
    codes = issue_codes(data)
    assert "missing_workflow_agent_template_variable" in codes


@pytest.mark.asyncio
async def test_workflow_agent_output_variable_is_available_downstream(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "workflow_agent",
        "type": "workflow_agent",
        "data": {
            "kind": "workflow_agent",
            "agentName": "research-agent",
            "modelId": "deepseek/deepseek-chat",
            "rolePrompt": "你是研究智能体。",
            "taskInput": "{{user_input}}",
            "outputVariable": "agent_output",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_output"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "workflow_agent"},
        {"id": "e2", "source": "workflow_agent", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True
    assert "missing_output_variable_reference" not in issue_codes(data)


@pytest.mark.asyncio
async def test_workflow_agent_mcp_tool_mode_is_valid(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "workflow_agent",
        "type": "workflow_agent",
        "data": {
            "kind": "workflow_agent",
            "agentName": "tool-agent",
            "modelId": "deepseek/deepseek-chat",
            "rolePrompt": "你是工具智能体。",
            "taskInput": "{{user_input}}",
            "toolMode": "mcp_tools",
            "toolNames": "fetch",
            "maxIterations": "3",
            "outputVariable": "agent_output",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_output"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "workflow_agent"},
        {"id": "e2", "source": "workflow_agent", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True
    assert "invalid_workflow_agent_tool_mode" not in issue_codes(data)
    assert "invalid_workflow_agent_max_iterations" not in issue_codes(data)


@pytest.mark.asyncio
async def test_workflow_agent_knowledge_tools_are_valid(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "workflow_agent",
        "type": "workflow_agent",
        "data": {
            "kind": "workflow_agent",
            "agentName": "knowledge-agent",
            "modelId": "deepseek/deepseek-chat",
            "rolePrompt": "Use approved knowledge only.",
            "taskInput": "{{user_input}}",
            "toolMode": "mcp_tools",
            "knowledgeReadEnabled": "true",
            "knowledgeWriteEnabled": "true",
            "knowledgeBaseIds": "kb-one, kb-two",
            "outputVariable": "agent_output",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_output"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "workflow_agent"},
        {"id": "e2", "source": "workflow_agent", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True
    assert "workflow_agent_knowledge_tools_require_runtime_mode" not in issue_codes(data)
    assert "invalid_workflow_agent_knowledge_base_ids" not in issue_codes(data)


@pytest.mark.asyncio
async def test_workflow_agent_knowledge_tools_require_runtime_mode_and_scoped_kbs(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "workflow_agent",
        "type": "workflow_agent",
        "data": {
            "kind": "workflow_agent",
            "agentName": "knowledge-agent",
            "modelId": "deepseek/deepseek-chat",
            "rolePrompt": "Use approved knowledge only.",
            "taskInput": "{{user_input}}",
            "toolMode": "none",
            "knowledgeReadEnabled": "true",
            "knowledgeBaseIds": "",
            "outputVariable": "agent_output",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_output"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "workflow_agent"},
        {"id": "e2", "source": "workflow_agent", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "workflow_agent_knowledge_tools_require_runtime_mode" in issue_codes(data)
    assert "invalid_workflow_agent_knowledge_base_ids" in issue_codes(data)


@pytest.mark.asyncio
async def test_workflow_agent_invalid_tool_mode_and_iterations(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "workflow_agent",
        "type": "workflow_agent",
        "data": {
            "kind": "workflow_agent",
            "agentName": "tool-agent",
            "modelId": "deepseek/deepseek-chat",
            "rolePrompt": "你是工具智能体。",
            "taskInput": "{{user_input}}",
            "toolMode": "unknown",
            "maxIterations": "99",
            "outputVariable": "agent_output",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_output"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "workflow_agent"},
        {"id": "e2", "source": "workflow_agent", "target": "output"},
    ]

    data = await validate(client, workflow)

    codes = issue_codes(data)
    assert data["valid"] is False
    assert "invalid_workflow_agent_tool_mode" in codes
    assert "invalid_workflow_agent_max_iterations" in codes


@pytest.mark.asyncio
async def test_workflow_agent_runtime_strategy_fields_are_valid(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "workflow_agent",
        "type": "workflow_agent",
        "data": {
            "kind": "workflow_agent",
            "agentName": "strategy-agent",
            "modelId": "deepseek/deepseek-chat",
            "rolePrompt": "You are a workflow agent.",
            "taskInput": "{{user_input}}",
            "exceptionHandling": "empty_output",
            "disableOutput": "true",
            "fallbackModelId": "deepseek/deepseek-chat-fallback",
            "outputVariable": "agent_output",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_output"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "workflow_agent"},
        {"id": "e2", "source": "workflow_agent", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True
    assert "invalid_workflow_agent_exception_handling" not in issue_codes(data)


@pytest.mark.asyncio
async def test_workflow_agent_invalid_exception_handling(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "workflow_agent",
        "type": "workflow_agent",
        "data": {
            "kind": "workflow_agent",
            "agentName": "strategy-agent",
            "modelId": "deepseek/deepseek-chat",
            "rolePrompt": "You are a workflow agent.",
            "taskInput": "{{user_input}}",
            "exceptionHandling": "swallow_everything",
            "outputVariable": "agent_output",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_output"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "workflow_agent"},
        {"id": "e2", "source": "workflow_agent", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "invalid_workflow_agent_exception_handling" in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_agent_handoff_node_ok(client: httpx.AsyncClient) -> None:
    workflow = linear_workflow()
    workflow["nodes"] = [
        workflow["nodes"][0],
        {
            "id": "task",
            "type": "agent_task",
            "data": {
                "kind": "agent_task",
                "taskTitle": "Plan {{user_input}}",
                "taskInput": "{{user_input}}",
                "assignedAgent": "workflow-planner",
                "outputVariable": "agent_task_id",
            },
        },
        {
            "id": "handoff",
            "type": "agent_handoff",
            "data": {
                "kind": "agent_handoff",
                "taskIdVariable": "agent_task_id",
                "targetAgent": "reviewer-agent",
                "reason": "Please review {{user_input}}",
                "sourceAgent": "workflow",
                "outputVariable": "agent_handoff_id",
            },
        },
        {
            "id": "output",
            "type": "output",
            "data": {"kind": "output", "outputVariable": "agent_handoff_id"},
        },
    ]
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "task"},
        {"id": "e2", "source": "task", "target": "handoff"},
        {"id": "e3", "source": "handoff", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True
    assert data["issues"] == []


@pytest.mark.asyncio
async def test_agent_handoff_missing_required_fields(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "handoff",
        "type": "agent_handoff",
        "data": {"kind": "agent_handoff"},
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "user_input"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "handoff"},
        {"id": "e2", "source": "handoff", "target": "output"},
    ]

    data = await validate(client, workflow)

    codes = issue_codes(data)
    assert data["valid"] is False
    assert "missing_agent_handoff_task_id_variable" in codes
    assert "missing_agent_handoff_target_agent" in codes
    assert "missing_agent_handoff_reason" in codes
    assert "missing_agent_handoff_output_variable" in codes


@pytest.mark.asyncio
async def test_agent_handoff_reason_variable_must_be_declared(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"] = [
        workflow["nodes"][0],
        {
            "id": "task",
            "type": "agent_task",
            "data": {
                "kind": "agent_task",
                "taskTitle": "Plan {{user_input}}",
                "taskInput": "{{user_input}}",
                "outputVariable": "agent_task_id",
            },
        },
        {
            "id": "handoff",
            "type": "agent_handoff",
            "data": {
                "kind": "agent_handoff",
                "taskIdVariable": "agent_task_id",
                "targetAgent": "reviewer-agent",
                "reason": "Please review {{missing_input}}",
                "outputVariable": "agent_handoff_id",
            },
        },
        {
            "id": "output",
            "type": "output",
            "data": {"kind": "output", "outputVariable": "agent_handoff_id"},
        },
    ]
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "task"},
        {"id": "e2", "source": "task", "target": "handoff"},
        {"id": "e3", "source": "handoff", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "missing_agent_handoff_template_variable" in issue_codes(data)


@pytest.mark.asyncio
async def test_agent_handoff_task_id_variable_must_be_declared(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "handoff",
        "type": "agent_handoff",
        "data": {
            "kind": "agent_handoff",
            "taskIdVariable": "agent_task_id",
            "targetAgent": "reviewer-agent",
            "reason": "Please review {{user_input}}",
            "outputVariable": "agent_handoff_id",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_handoff_id"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "handoff"},
        {"id": "e2", "source": "handoff", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "missing_agent_handoff_task_id_reference" in issue_codes(data)


@pytest.mark.asyncio
async def test_validate_handoff_router_node_ok(client: httpx.AsyncClient) -> None:
    workflow = linear_workflow()
    workflow["nodes"] = [
        workflow["nodes"][0],
        {
            "id": "agent",
            "type": "workflow_agent",
            "data": {
                "kind": "workflow_agent",
                "agentName": "research-agent",
                "modelId": "deepseek/deepseek-chat",
                "rolePrompt": "You summarize input.",
                "taskInput": "{{user_input}}",
                "outputVariable": "agent_output",
            },
        },
        {
            "id": "router",
            "type": "handoff_router",
            "data": {
                "kind": "handoff_router",
                "sourceVariable": "agent_output",
                "taskTitle": "Review {{user_input}}",
                "targetAgent": "reviewer-agent",
                "sourceAgent": "workflow-agent",
                "reasonTemplate": "Please review {{agent_output}}",
                "outputVariable": "agent_handoff_id",
            },
        },
        {
            "id": "output",
            "type": "output",
            "data": {"kind": "output", "outputVariable": "agent_handoff_id"},
        },
    ]
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "agent"},
        {"id": "e2", "source": "agent", "target": "router"},
        {"id": "e3", "source": "router", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True
    assert data["issues"] == []


@pytest.mark.asyncio
async def test_handoff_router_missing_required_fields(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "router",
        "type": "handoff_router",
        "data": {"kind": "handoff_router"},
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "user_input"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "router"},
        {"id": "e2", "source": "router", "target": "output"},
    ]

    data = await validate(client, workflow)

    codes = issue_codes(data)
    assert data["valid"] is False
    assert "missing_handoff_router_source_variable" in codes
    assert "missing_handoff_router_task_title" in codes
    assert "missing_handoff_router_target_agent" in codes
    assert "missing_handoff_router_reason_template" in codes
    assert "missing_handoff_router_output_variable" in codes


@pytest.mark.asyncio
async def test_handoff_router_template_variable_must_be_declared(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "router",
        "type": "handoff_router",
        "data": {
            "kind": "handoff_router",
            "sourceVariable": "user_input",
            "taskTitle": "Review {{missing_title}}",
            "targetAgent": "reviewer-agent",
            "reasonTemplate": "Please review {{missing_reason}}",
            "outputVariable": "agent_handoff_id",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_handoff_id"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "router"},
        {"id": "e2", "source": "router", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is False
    assert "missing_handoff_router_template_variable" in issue_codes(data)


@pytest.mark.asyncio
async def test_handoff_router_output_variable_is_available_downstream(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "router",
        "type": "handoff_router",
        "data": {
            "kind": "handoff_router",
            "sourceVariable": "user_input",
            "taskTitle": "Review {{user_input}}",
            "targetAgent": "reviewer-agent",
            "reasonTemplate": "Please review {{user_input}}",
            "outputVariable": "agent_handoff_id",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_handoff_id"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "router"},
        {"id": "e2", "source": "router", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True
    assert "missing_output_variable_reference" not in issue_codes(data)


@pytest.mark.asyncio
async def test_handoff_router_auto_xpert_result_is_available_downstream(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "router",
        "type": "handoff_router",
        "data": {
            "kind": "handoff_router",
            "sourceVariable": "user_input",
            "taskTitle": "Delegate {{user_input}}",
            "targetAgent": "xpert:specialist",
            "reasonTemplate": "Complete {{user_input}}",
            "executionMode": "xpert_auto",
            "waitForCompletion": "true",
            "resultVariable": "handoff_result",
            "waitTimeoutSeconds": "120",
            "outputVariable": "agent_handoff_id",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "handoff_result"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "router"},
        {"id": "e2", "source": "router", "target": "output"},
    ]

    data = await validate(client, workflow)

    assert data["valid"] is True
    assert "missing_output_variable_reference" not in issue_codes(data)


@pytest.mark.asyncio
async def test_handoff_router_rejects_invalid_auto_execution_settings(
    client: httpx.AsyncClient,
) -> None:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "router",
        "type": "handoff_router",
        "data": {
            "kind": "handoff_router",
            "sourceVariable": "user_input",
            "taskTitle": "Delegate {{user_input}}",
            "targetAgent": "review-agent",
            "reasonTemplate": "Complete {{user_input}}",
            "executionMode": "xpert_auto",
            "waitForCompletion": "true",
            "resultVariable": "not valid",
            "waitTimeoutSeconds": "2",
            "outputVariable": "agent_handoff_id",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_handoff_id"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "router"},
        {"id": "e2", "source": "router", "target": "output"},
    ]

    data = await validate(client, workflow)
    codes = issue_codes(data)

    assert data["valid"] is False
    assert "invalid_handoff_router_xpert_target" in codes
    assert "invalid_handoff_router_result_variable" in codes
    assert "invalid_handoff_router_wait_timeout" in codes


@pytest.mark.asyncio
async def test_templates_endpoint_returns_starter_template(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/workflow-native/templates")

    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data) == 1
    assert data[0]["workflow"]["nodes"]


def browser_middleware_workflow(hitl_tools: str) -> dict:
    workflow = linear_workflow()
    workflow["nodes"][1] = {
        "id": "agent",
        "type": "workflow_agent",
        "data": {
            "kind": "workflow_agent",
            "agentName": "browser-agent",
            "modelId": "openai/gpt-4o-mini",
            "rolePrompt": "Use the private browser safely.",
            "taskInput": "{{user_input}}",
            "outputVariable": "agent_output",
            "toolMode": "mcp_tools",
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "agent_output"
    for edge in workflow["edges"]:
        if edge["source"] == "llm":
            edge["source"] = "agent"
        if edge["target"] == "llm":
            edge["target"] = "agent"
    workflow["nodes"].extend(
        [
            {
                "id": "browser",
                "type": "runtime_middleware",
                "data": {
                    "kind": "runtime_middleware",
                    "runtimeMiddlewareId": "browser_automation",
                    "runtimeMiddlewareKind": "runtime_middleware.browser_automation",
                    "runtimeMiddlewareConfig": {
                        "networkPolicy": "public_with_domain_approval",
                        "approvalMode": "mutating",
                        "persistSession": True,
                        "maxPages": 3,
                        "maxActions": 100,
                        "navigationTimeoutSeconds": 30,
                        "downloadLimitMb": 50,
                    },
                },
            },
            {
                "id": "hitl",
                "type": "runtime_middleware",
                "data": {
                    "kind": "runtime_middleware",
                    "runtimeMiddlewareId": "human_in_the_loop",
                    "runtimeMiddlewareKind": "runtime_middleware.human_in_the_loop",
                    "runtimeMiddlewareConfig": {
                        "interrupt_on_tools": hitl_tools,
                        "timeout_seconds": 3600,
                    },
                },
            },
        ]
    )
    workflow["edges"].extend(
        [
            {
                "id": "bind-browser",
                "source": "browser",
                "target": "agent",
                "sourceHandle": "middleware-binding",
                "targetHandle": "middleware",
            },
            {
                "id": "bind-hitl",
                "source": "hitl",
                "target": "agent",
                "sourceHandle": "middleware-binding",
                "targetHandle": "middleware",
            },
        ]
    )
    return workflow


@pytest.mark.asyncio
async def test_browser_middleware_requires_complete_hitl_coverage(
    client: httpx.AsyncClient,
) -> None:
    invalid = await validate(client, browser_middleware_workflow("browser_click"))
    assert "browser_automation_requires_hitl" in issue_codes(invalid)

    valid = await validate(client, browser_middleware_workflow("*"))
    assert valid["valid"] is True
    assert "browser_automation_requires_hitl" not in issue_codes(valid)


@pytest.mark.asyncio
async def test_browser_middleware_requires_runtime_tool_mode(
    client: httpx.AsyncClient,
) -> None:
    workflow = browser_middleware_workflow("*")
    agent = next(node for node in workflow["nodes"] if node["id"] == "agent")
    agent["data"]["toolMode"] = "none"

    data = await validate(client, workflow)

    assert "browser_automation_requires_runtime_tool_mode" in issue_codes(data)


@pytest.mark.asyncio
async def test_browser_middleware_validates_policy_limits_and_domains(
    client: httpx.AsyncClient,
) -> None:
    workflow = browser_middleware_workflow("*")
    browser = next(node for node in workflow["nodes"] if node["id"] == "browser")
    browser["data"]["runtimeMiddlewareConfig"].update(
        {
            "networkPolicy": "unrestricted",
            "maxPages": 9,
            "allowedDomains": "localhost, https://bad.example.com",
        }
    )
    data = await validate(client, workflow)
    codes = issue_codes(data)
    assert "invalid_runtime_middleware_browser_network_policy" in codes
    assert "invalid_runtime_middleware_config" in codes
    assert "invalid_runtime_middleware_browser_domains" in codes


def client_tools_middleware_workflow(hitl_tools: str) -> dict:
    workflow = browser_middleware_workflow(hitl_tools)
    browser = next(node for node in workflow["nodes"] if node["id"] == "browser")
    browser["id"] = "client-tools"
    browser["data"] = {
        "kind": "runtime_middleware",
        "runtimeMiddlewareId": "client_tools",
        "runtimeMiddlewareKind": "runtime_middleware.client_tools",
        "runtimeMiddlewareConfig": {
            "clientHostId": "host_test",
            "clientToolNames": "host_page_read,host_page_fill",
            "clientToolTimeoutSeconds": 1800,
            "requireBoundTab": True,
        },
    }
    binding = next(edge for edge in workflow["edges"] if edge["id"] == "bind-browser")
    binding["source"] = "client-tools"
    return workflow


@pytest.mark.asyncio
async def test_client_tools_require_runtime_mode_host_and_hitl(
    client: httpx.AsyncClient,
) -> None:
    invalid = await validate(client, client_tools_middleware_workflow("host_page_click"))
    assert "client_tools_requires_hitl" in issue_codes(invalid)

    valid = await validate(client, client_tools_middleware_workflow("host_page_fill"))
    assert valid["valid"] is True
    assert "client_tools_requires_hitl" not in issue_codes(valid)

    missing_host = client_tools_middleware_workflow("*")
    node = next(item for item in missing_host["nodes"] if item["id"] == "client-tools")
    node["data"]["runtimeMiddlewareConfig"]["clientHostId"] = ""
    data = await validate(client, missing_host)
    assert "client_tools_host_required" in issue_codes(data)


@pytest.mark.asyncio
async def test_client_tools_validate_timeout_and_runtime_tool_mode(
    client: httpx.AsyncClient,
) -> None:
    workflow = client_tools_middleware_workflow("*")
    node = next(item for item in workflow["nodes"] if item["id"] == "client-tools")
    node["data"]["runtimeMiddlewareConfig"]["clientToolTimeoutSeconds"] = 2
    agent = next(item for item in workflow["nodes"] if item["id"] == "agent")
    agent["data"]["toolMode"] = "none"

    data = await validate(client, workflow)
    codes = issue_codes(data)

    assert "client_tools_timeout_invalid" in codes
    assert "client_tools_requires_runtime_tool_mode" in codes


def office_automation_middleware_workflow(hitl_tools: str) -> dict:
    workflow = browser_middleware_workflow(hitl_tools)
    browser = next(node for node in workflow["nodes"] if node["id"] == "browser")
    browser["id"] = "office"
    browser["data"] = {
        "kind": "runtime_middleware",
        "runtimeMiddlewareId": "office_automation",
        "runtimeMiddlewareKind": "runtime_middleware.office_automation",
        "runtimeMiddlewareConfig": {
            "clientHostId": "office_host",
            "host": "word",
            "allowDeletes": False,
            "allowImageInsert": False,
            "timeoutSeconds": 1800,
            "requireBoundDocument": True,
        },
    }
    binding = next(edge for edge in workflow["edges"] if edge["id"] == "bind-browser")
    binding["source"] = "office"
    return workflow


@pytest.mark.asyncio
async def test_office_automation_requires_runtime_host_and_hitl(
    client: httpx.AsyncClient,
) -> None:
    invalid = await validate(
        client,
        office_automation_middleware_workflow("office_word_insert_text"),
    )
    assert "office_automation_requires_hitl" in issue_codes(invalid)

    valid = await validate(client, office_automation_middleware_workflow("*"))
    assert valid["valid"] is True, valid["issues"]
    assert "office_automation_requires_hitl" not in issue_codes(valid)

    missing_host = office_automation_middleware_workflow("*")
    office = next(node for node in missing_host["nodes"] if node["id"] == "office")
    office["data"]["runtimeMiddlewareConfig"]["clientHostId"] = ""
    data = await validate(client, missing_host)
    assert "office_automation_host_required" in issue_codes(data)


@pytest.mark.asyncio
async def test_office_automation_validates_scope_timeout_and_tool_mode(
    client: httpx.AsyncClient,
) -> None:
    workflow = office_automation_middleware_workflow("*")
    office = next(node for node in workflow["nodes"] if node["id"] == "office")
    office["data"]["runtimeMiddlewareConfig"].update(
        {"host": "outlook", "timeoutSeconds": 2}
    )
    agent = next(node for node in workflow["nodes"] if node["id"] == "agent")
    agent["data"]["toolMode"] = "none"

    data = await validate(client, workflow)
    codes = issue_codes(data)

    assert "office_automation_host_invalid" in codes
    assert "office_automation_timeout_invalid" in codes
    assert "office_automation_requires_runtime_tool_mode" in codes
