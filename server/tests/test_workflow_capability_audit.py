from __future__ import annotations

import csv
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
    assert all(mapped[key]["模镜当前状态"] == "已实现" for key in (
        "scheduleTrigger", "webhook", "wait", "respondToWebhook"
    ))
    assert all("不复制代码" in row["许可证边界"] or "企业条目" in row["许可证边界"] for row in rows)

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
    assert f"{native_count - 4} 个 `NativeNodeKind`" in markdown
    assert f"{palette_count - 4} 个画布目录项" in markdown
    assert f"{compatibility_count} 个冻结 compatibility 合同" in markdown
    assert f"自研节点总数 {native_count}" in markdown
    assert f"画布目录项 {palette_count}" in markdown
