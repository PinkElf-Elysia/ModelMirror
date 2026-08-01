from __future__ import annotations

import io
import json
from pathlib import Path

import duckdb
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook

from server.datax.api import configure_datax, router
from server.datax.service import DataXService
from server.datax.store import DataXConflictError, DataXStore, DataXValidationError
from server.datax.toolset import DataXToolsetProvider
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.workflow_native.validate import validate_workflow_graph
from server.xpert_runtime.toolset import RuntimeToolCall, RuntimeToolError


def service(tmp_path: Path) -> DataXService:
    return DataXService(DataXStore(tmp_path / "datax"))


def import_csv(
    datax: DataXService,
    project_id: str,
    name: str,
    content: str,
):
    job = datax.import_source(project_id, file_name=name, content=content.encode("utf-8"))
    assert job.status == "ready"
    return datax.list_sources(project_id)[-1]


def create_single_model(datax: DataXService, project_id: str, source, *, name="Sales"):
    columns = source.profile["columns"]
    roles = {
        "amount": "measure",
        "quantity": "measure",
        "order_date": "time",
    }
    return datax.create_model(
        project_id,
        name=name,
        entities=[
            {
                "entity_id": "sales",
                "source_id": source.source_id,
                "alias": "sales",
                "label": "Sales",
            }
        ],
        fields=[
            {
                "field_id": f"field_{index}",
                "entity_id": "sales",
                "source_field": column["name"],
                "name": column["name"],
                "label": column["name"],
                "data_type": column["data_type"],
                "role": roles.get(column["name"], "dimension"),
            }
            for index, column in enumerate(columns)
        ],
    )


def create_basic_indicator(
    datax: DataXService,
    project_id: str,
    model_id: str,
    *,
    code: str = "revenue",
    aggregation: str = "sum",
    measure_field: str | None = "amount",
):
    item = datax.create_indicator(
        project_id,
        {
            "model_id": model_id,
            "code": code,
            "name": code.replace("_", " ").title(),
            "indicator_type": "basic",
            "aggregation": aggregation,
            "measure_field": measure_field,
        },
    )
    return datax.publish_indicator(item.indicator_id, revision=item.revision)


def test_import_job_is_persisted_before_execution_and_respects_lease(
    tmp_path: Path,
) -> None:
    datax = service(tmp_path)
    project = datax.create_project(name="Queued import")
    job = datax.create_import_job(
        project.project_id,
        file_name="queued.csv",
        content=b"region,amount\nEast,10\n",
    )
    assert job.status == "pending"
    assert datax.list_sources(project.project_id)[0].status == "pending"

    leased = job.model_copy(
        update={
            "status": "processing",
            "lease_token": "active-lease",
            "lease_expires_at": datax.store.now() + 60,
        }
    )
    datax.store.replace("import_jobs", leased, "job_id")
    assert datax.run_import_job(job.job_id).lease_token == "active-lease"

    restarted = DataXService(DataXStore(tmp_path / "datax"))
    assert restarted.recover_import_jobs() == 1
    completed = restarted.get_import_job(job.job_id)
    assert completed.status == "ready"
    assert completed.lease_token is None


def test_csv_import_publish_query_and_immutable_online_version(tmp_path: Path) -> None:
    datax = service(tmp_path)
    project = datax.create_project(name="Retail")
    source = import_csv(
        datax,
        project.project_id,
        "sales.csv",
        "region,amount,order_date\nEast,10,2026-01-01\nEast,20,2026-01-02\nWest,8,2026-01-03\n",
    )
    assert source.row_count == 3
    assert all("path" not in column for column in source.profile["columns"])
    model = create_single_model(datax, project.project_id, source)
    published = create_basic_indicator(datax, project.project_id, model.model_id)

    result = datax.query(
        project_id=project.project_id,
        model_id=model.model_id,
        indicator_codes=["revenue"],
        dimensions=["region"],
        order_by=[{"field": "region", "direction": "asc"}],
    )
    assert result.rows == [
        {"region": "East", "revenue": 30.0},
        {"region": "West", "revenue": 8.0},
    ]

    draft = datax.update_indicator(
        published.indicator_id,
        revision=published.revision,
        patch={"aggregation": "avg"},
    )
    assert draft.status == "draft"
    assert draft.current_version == 1
    online = datax.query(
        project_id=project.project_id,
        model_id=model.model_id,
        indicator_codes=["revenue"],
        dimensions=["region"],
        order_by=[{"field": "region", "direction": "asc"}],
    )
    assert online.rows[0]["revenue"] == 30.0
    assert datax.get_published_indicator(published.indicator_id).aggregation == "sum"


def test_xlsx_and_parquet_import_preserve_types(tmp_path: Path) -> None:
    datax = service(tmp_path)
    project = datax.create_project(name="Formats")

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["name", "amount", "enabled", "order_date"])
    sheet.append(["A", 12.5, True, "2026-01-02"])
    sheet.append(["B", 7, False, "2026-01-03"])
    payload = io.BytesIO()
    workbook.save(payload)
    xlsx = datax.import_source(
        project.project_id,
        file_name="typed.xlsx",
        content=payload.getvalue(),
    )
    assert xlsx.status == "ready"
    xlsx_source = datax.list_sources(project.project_id)[0]
    types = {item["name"]: item["data_type"] for item in xlsx_source.profile["columns"]}
    assert "DOUBLE" in types["amount"]
    assert "BOOLEAN" in types["enabled"]

    parquet_path = tmp_path / "source.parquet"
    connection = duckdb.connect()
    connection.execute(
        "COPY (SELECT 1::INTEGER AS id, 9.5::DOUBLE AS amount) TO ? (FORMAT PARQUET)",
        [str(parquet_path)],
    )
    connection.close()
    parquet_job = datax.import_source(
        project.project_id,
        file_name="typed.parquet",
        content=parquet_path.read_bytes(),
    )
    assert parquet_job.status == "ready"
    assert datax.list_sources(project.project_id)[1].row_count == 1


def test_semantic_join_filters_and_sql_injection_guards(tmp_path: Path) -> None:
    datax = service(tmp_path)
    project = datax.create_project(name="Joined")
    customers = import_csv(
        datax,
        project.project_id,
        "customers.csv",
        "customer_id,region\n1,East\n2,West\n",
    )
    orders = import_csv(
        datax,
        project.project_id,
        "orders.csv",
        "order_id,customer_id,amount\n10,1,20\n11,1,5\n12,2,9\n",
    )
    model = datax.create_model(
        project.project_id,
        name="Customer orders",
        entities=[
            {"entity_id": "customers", "source_id": customers.source_id, "alias": "c"},
            {"entity_id": "orders", "source_id": orders.source_id, "alias": "o"},
        ],
        joins=[
            {
                "left_entity_id": "customers",
                "left_field": "customer_id",
                "right_entity_id": "orders",
                "right_field": "customer_id",
                "join_type": "inner",
            }
        ],
        fields=[
            {"field_id": "region", "entity_id": "customers", "source_field": "region", "name": "region", "role": "dimension"},
            {"field_id": "amount", "entity_id": "orders", "source_field": "amount", "name": "amount", "role": "measure"},
        ],
    )
    create_basic_indicator(datax, project.project_id, model.model_id)
    result = datax.query(
        project_id=project.project_id,
        model_id=model.model_id,
        indicator_codes=["revenue"],
        dimensions=["region"],
        filters=[{"field": "region", "operator": "eq", "value": "East' OR 1=1 --"}],
    )
    assert result.rows == []
    with pytest.raises(DataXValidationError):
        datax.query(
            project_id=project.project_id,
            model_id=model.model_id,
            indicator_codes=["revenue"],
            dimensions=["region; DROP TABLE orders"],
        )


def test_derived_indicator_proposals_revision_and_publish_boundary(tmp_path: Path) -> None:
    datax = service(tmp_path)
    project = datax.create_project(name="Metrics")
    source = import_csv(datax, project.project_id, "metrics.csv", "amount,quantity\n10,2\n30,3\n")
    model = create_single_model(datax, project.project_id, source)
    create_basic_indicator(datax, project.project_id, model.model_id, code="revenue")
    create_basic_indicator(
        datax,
        project.project_id,
        model.model_id,
        code="orders",
        aggregation="sum",
        measure_field="quantity",
    )
    ratio = datax.create_indicator(
        project.project_id,
        {
            "model_id": model.model_id,
            "code": "average_order_value",
            "name": "AOV",
            "indicator_type": "derived",
            "formula": "revenue / orders",
        },
    )
    ratio = datax.publish_indicator(ratio.indicator_id, revision=ratio.revision)
    result = datax.query(
        project_id=project.project_id,
        model_id=model.model_id,
        indicator_codes=["average_order_value"],
    )
    assert result.rows[0]["average_order_value"] == 8.0
    with pytest.raises(DataXValidationError):
        datax.create_indicator(
            project.project_id,
            {
                "model_id": model.model_id,
                "code": "unsafe",
                "name": "Unsafe",
                "indicator_type": "derived",
                "formula": "__import__('os')",
            },
        )

    proposal = datax.create_proposal(
        project_id=project.project_id,
        model_id=model.model_id,
        title="Gross margin",
        payload={
            "model_id": model.model_id,
            "code": "gross_margin",
            "name": "Gross margin",
            "indicator_type": "basic",
            "aggregation": "sum",
            "measure_field": "amount",
        },
        source_run_id="run_1",
    )
    assert datax.create_proposal(
        project_id=project.project_id,
        model_id=model.model_id,
        title="Duplicate",
        payload=proposal.payload,
        source_run_id="run_1",
    ).proposal_id == proposal.proposal_id
    with pytest.raises(DataXConflictError):
        datax.update_proposal(proposal.proposal_id, revision=99, title="x", payload=None)
    approved = datax.resolve_proposal(
        proposal.proposal_id,
        revision=proposal.revision,
        action="approve",
        operator="tester",
    )
    assert approved.status == "approved"
    created = next(item for item in datax.list_indicators(project.project_id) if item.code == "gross_margin")
    assert created.status == "draft"
    assert created.current_version is None


@pytest.mark.anyio
async def test_runtime_toolset_scope_and_app_write_guard(tmp_path: Path) -> None:
    datax = service(tmp_path)
    project = datax.create_project(name="Runtime")
    source = import_csv(datax, project.project_id, "runtime.csv", "amount\n4\n6\n")
    model = create_single_model(datax, project.project_id, source)
    indicator = create_basic_indicator(datax, project.project_id, model.model_id)
    provider = DataXToolsetProvider(datax)
    metadata = {
        "datax_project_ids": [project.project_id],
        "datax_model_ids": [model.model_id],
        "datax_allow_proposals": True,
        "datax_max_result_rows": 10,
        "run_type": "xpert",
        "run_id": "run_1",
    }
    result = await provider.call_tool(
        RuntimeToolCall(
            "datax_indicator_query",
            {
                "project_id": project.project_id,
                "model_id": model.model_id,
                "indicators": [indicator.code],
                "view": "kpi",
            },
            metadata,
        )
    )
    assert json.loads(result.output)["rows"][0]["revenue"] == 10.0
    assert result.metadata["content_types"] == ["text", "datax_result"]

    with pytest.raises(RuntimeToolError, match="Public Xpert Apps"):
        await provider.call_tool(
            RuntimeToolCall(
                "datax_indicator_propose_create",
                {
                    "project_id": project.project_id,
                    "model_id": model.model_id,
                    "title": "No write",
                    "indicator": {},
                },
                {**metadata, "run_type": "xpert_app"},
            )
        )
    other = datax.create_project(name="Other")
    with pytest.raises(RuntimeToolError):
        await provider.call_tool(
            RuntimeToolCall(
                "datax_indicator_query",
                {
                    "project_id": other.project_id,
                    "model_id": model.model_id,
                    "indicators": [indicator.code],
                },
                metadata,
            )
        )


def test_datax_api_shape_and_safety(tmp_path: Path) -> None:
    datax = service(tmp_path)
    configure_datax(datax)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    created = client.post("/api/datax/projects", json={"name": "API"})
    assert created.status_code == 200
    response = client.get("/api/datax/projects")
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == "modelmirror-datax-projects-v1"
    project_id = created.json()["project_id"]
    imported = client.post(
        f"/api/datax/projects/{project_id}/sources",
        files={"file": ("api.csv", b"region,amount\nEast,10\n", "text/csv")},
    )
    assert imported.status_code == 200
    assert imported.json()["status"] == "pending"
    completed = client.get(f"/api/datax/import-jobs/{imported.json()['job_id']}")
    assert completed.status_code == 200
    assert completed.json()["status"] == "ready"
    raw = json.dumps(payload)
    assert "DATAX_STORAGE_DIR" not in raw
    assert "duckdb" not in raw.casefold()
    assert "api_key" not in raw.casefold()


def test_datax_middleware_requires_runtime_mode_and_scopes() -> None:
    workflow = NativeWorkflowDefinition.model_validate(
        {
            "id": "datax-workflow",
            "title": "Data X workflow",
            "source": "classic",
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
                    "id": "agent",
                    "type": "workflow_agent",
                    "data": {
                        "kind": "workflow_agent",
                        "agentName": "Analyst",
                        "modelId": "openai/gpt-4o-mini",
                        "rolePrompt": "Analyze the requested indicators.",
                        "taskInput": "{{user_input}}",
                        "outputVariable": "agent_output",
                        "toolMode": "none",
                    },
                },
                {
                    "id": "datax",
                    "type": "runtime_middleware",
                    "data": {
                        "kind": "runtime_middleware",
                        "runtimeMiddlewareId": "datax_indicators",
                        "runtimeMiddlewareKind": "runtime_middleware.datax_indicators",
                        "middlewarePriority": "50",
                        "runtimeMiddlewareConfig": {
                            "projectIds": "project-1",
                            "modelIds": "model-1",
                            "allowProposals": True,
                            "maxResultRows": 100,
                        },
                    },
                },
                {
                    "id": "output",
                    "type": "output",
                    "data": {
                        "kind": "output",
                        "outputVariable": "agent_output",
                    },
                },
            ],
            "edges": [
                {"id": "e1", "source": "input", "target": "agent"},
                {"id": "e2", "source": "agent", "target": "output"},
                {
                    "id": "bind",
                    "source": "datax",
                    "target": "agent",
                    "sourceHandle": "middleware-binding",
                    "targetHandle": "middleware",
                },
            ],
        }
    )

    invalid = validate_workflow_graph(workflow)
    assert "datax_indicators_requires_runtime_tool_mode" in {
        issue.code for issue in invalid.issues
    }

    workflow.nodes[1].data["toolMode"] = "mcp_tools"
    valid = validate_workflow_graph(workflow)
    assert valid.valid is True, valid.issues

    workflow.nodes[2].data["runtimeMiddlewareConfig"]["projectIds"] = ""
    missing_scope = validate_workflow_graph(workflow)
    assert "datax_indicators_projects_required" in {
        issue.code for issue in missing_scope.issues
    }
