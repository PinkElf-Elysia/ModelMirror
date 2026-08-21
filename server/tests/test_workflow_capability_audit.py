from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import get_args

from server.workflow_native.node_contracts import workflow_node_contract_registry
from server.workflow_native.schemas import NativeNodeKind
from server.xpert_runtime.workflow_node_registry import (
    WorkflowNodeRegistry,
    register_builtin_workflow_nodes,
)


def test_capability_audit_tracks_baseline_and_r1_nodes() -> None:
    root = Path(__file__).resolve().parents[2]
    csv_path = root / "docs" / "audits" / "n8n-node-capability-matrix.csv"
    markdown_path = root / "docs" / "audits" / "N8N_NODE_CAPABILITY_MATRIX.md"
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 563
    mapped = {row["n8n内部标识"]: row for row in rows}
    assert mapped["scheduleTrigger"]["模镜对应节点"] == "scheduled_start"
    assert mapped["webhook"]["模镜对应节点"] == "http_event_entry"
    assert mapped["wait"]["模镜对应节点"] == "suspend_wait"
    assert mapped["respondToWebhook"]["模镜对应节点"] == "http_event_reply"
    assert mapped["errorTrigger"]["模镜对应节点"] == "failure_event_entry"
    assert mapped["executeWorkflowTrigger"]["模镜对应节点"] == "workflow_call_entry"
    assert mapped["executeWorkflow"]["模镜对应节点"] == "invoke_workflow"
    assert mapped["stopAndError"]["模镜对应节点"] == "terminate_error"
    assert mapped["switch"]["模镜对应节点"] == "multi_route"
    assert mapped["filter"]["模镜对应节点"] == "list_operation"
    assert mapped["sort"]["模镜对应节点"] == "list_operation"
    assert mapped["removeDuplicates"]["模镜对应节点"] == "list_operation"
    assert mapped["aggregate"]["模镜对应节点"] == "data_aggregate"
    assert mapped["summarize"]["模镜对应节点"] == "data_aggregate"
    assert all(mapped[key]["模镜当前状态"] == "已实现" for key in (
        "scheduleTrigger", "webhook", "wait", "respondToWebhook", "errorTrigger",
        "executeWorkflowTrigger", "executeWorkflow", "stopAndError", "switch",
        "filter", "sort", "removeDuplicates", "aggregate", "summarize",
    ))
    assert all("不复制代码" in row["许可证边界"] or "企业条目" in row["许可证边界"] for row in rows)
    assert Counter(row["模镜当前状态"] for row in rows) == {
        "已实现": 18,
        "部分实现": 95,
        "通用节点可覆盖": 276,
        "未实现": 174,
    }

    markdown = markdown_path.read_text(encoding="utf-8")
    native_count = len(get_args(NativeNodeKind))
    registry = WorkflowNodeRegistry()
    register_builtin_workflow_nodes(registry)
    palette_count = len(
        {
            item.kind
            for section in registry.sections()
            for item in section.items
        }
        | {item.kind for item in registry.knowledge_pipeline().items}
    )
    compatibility_count = sum(
        contract.contract_status == "compatibility"
        for contract in workflow_node_contract_registry.list()
    )
    assert "911593f505b05b01037769f578e21f22d2a1c9af" in markdown
    assert "44、画布目录项 42" in markdown
    assert f"{compatibility_count} 个冻结 compatibility 合同" in markdown
    assert f"自研节点总数 {native_count}" in markdown
    assert f"画布目录项 {palette_count}" in markdown
    assert native_count == 47
    assert palette_count == 45
    assert compatibility_count == 18
