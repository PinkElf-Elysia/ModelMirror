from __future__ import annotations

import csv
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import get_args

from server.workflow_native.node_contracts import workflow_node_contract_registry
from server.workflow_native.schemas import NativeNodeKind
from server.xpert_runtime.workflow_node_registry import (
    WorkflowNodeRegistry,
    register_builtin_workflow_nodes,
)


def test_capability_audit_generator_is_idempotent(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    audit_dir = root / "docs" / "audits"
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "generate_workflow_capability_audit.py"),
            "--source-csv",
            str(audit_dir / "n8n-node-capability-matrix.csv"),
            "--output-dir",
            str(tmp_path),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for filename in (
        "n8n-node-capability-matrix.csv",
        "N8N_NODE_CAPABILITY_MATRIX.md",
    ):
        assert (tmp_path / filename).read_bytes() == (audit_dir / filename).read_bytes()


def test_capability_audit_tracks_baseline_and_r1_nodes() -> None:
    root = Path(__file__).resolve().parents[2]
    csv_path = root / "docs" / "audits" / "n8n-node-capability-matrix.csv"
    markdown_path = root / "docs" / "audits" / "N8N_NODE_CAPABILITY_MATRIX.md"
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 563
    assert len({row["来源条目标识"] for row in rows}) == 563
    mapped = {row["n8n内部标识"]: row for row in rows}
    mapped_by_source = {row["来源条目标识"]: row for row in rows}
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
    assert mapped["httpRequest"]["模镜对应节点"] == "http_request"
    assert mapped["if"]["模镜对应节点"] == "condition"
    assert mapped["compareDatasets"]["模镜对应节点"] == "dataset_compare"
    assert mapped["set"]["模镜当前状态"] == "已实现"
    assert mapped["set"]["模镜对应节点"] == "object_transform"
    assert mapped["convertToFile"]["模镜对应节点"] == "file_output"
    assert mapped["dateTime"]["模镜对应节点"] == "time_tool"
    assert mapped["limit"]["模镜对应节点"] == "list_operation"
    assert mapped["extractFromFile"]["模镜对应节点"] == "document_extractor"
    assert mapped["informationExtractor"]["模镜对应节点"] == "parameter_extractor"
    assert mapped["outputParserStructured"]["模镜对应节点"] == "parameter_extractor"
    assert mapped["outputParserItemList"]["模镜对应节点"] == "parameter_extractor"
    assert mapped["outputParserAutofixing"]["模镜对应节点"] == "parameter_extractor"
    assert mapped["textClassifier"]["模镜对应节点"] == "question_classifier"
    assert mapped["guardrails"]["模镜对应节点"] == "runtime_middleware"
    assert mapped["renameKeys"]["模镜当前状态"] == "已实现"
    assert mapped["renameKeys"]["模镜对应节点"] == "object_transform"
    assert mapped["mcpClientTool"]["模镜当前状态"] == "已实现"
    assert mapped["mcpClientTool"]["模镜对应节点"] == "mcp_tool"
    assert mapped["mcpClient"]["模镜当前状态"] == "部分实现"
    assert mapped["mcpRegistryClientTool"]["模镜当前状态"] == "部分实现"
    assert mapped["memoryManager"]["模镜当前状态"] == "部分实现"
    assert "没有可独立连线" in mapped["memoryManager"]["判断说明"]
    assert "区间截取" in mapped["itemLists"]["判断说明"]
    for source_ref in (
        "base:dist/nodes/Function/Function.node.js",
        "base:dist/nodes/FunctionItem/FunctionItem.node.js",
        "base:dist/nodes/Code/Code.node.js",
        "langchain:dist/nodes/code/Code.node.js",
    ):
        code_row = mapped_by_source[source_ref]
        assert code_row["模镜当前状态"] == "部分实现"
        assert code_row["模镜对应节点"] == "code"
        assert code_row["模镜建议节点名"].startswith("安全文本加工")
        assert "安全文本加工 V2" in code_row["判断说明"]
        assert "受控代码 V2" not in code_row["判断说明"]
        assert "不执行" in code_row["判断说明"]
    assert all("template_transform" not in row["模镜对应节点"] for row in rows)
    assert mapped["moveBinaryData"]["模镜对应节点"] == "variable_assign"
    assert mapped["html"]["模镜对应节点"] == "code / variable_assign"
    assert mapped["aiTransform"]["模镜对应节点"] == "llm / variable_assign"
    assert all(mapped[key]["模镜当前状态"] == "已实现" for key in (
        "scheduleTrigger", "webhook", "wait", "respondToWebhook", "errorTrigger",
        "executeWorkflowTrigger", "executeWorkflow", "stopAndError", "switch",
        "filter", "sort", "removeDuplicates", "aggregate", "summarize",
        "httpRequest", "if", "compareDatasets", "set", "convertToFile",
        "dateTime", "limit", "itemLists", "extractFromFile",
        "informationExtractor", "outputParserStructured", "outputParserItemList",
        "outputParserAutofixing", "textClassifier", "guardrails",
    ))
    assert all("不复制代码" in row["许可证边界"] or "企业条目" in row["许可证边界"] for row in rows)
    assert Counter(row["模镜当前状态"] for row in rows) == {
        "已实现": 34,
        "部分实现": 82,
        "通用节点可覆盖": 276,
        "未实现": 171,
    }
    native_kinds = set(get_args(NativeNodeKind))
    assert {
        row["模镜对应节点"]
        for row in rows
        if row["模镜当前状态"] == "已实现"
    } <= native_kinds

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
    complete_count = sum(
        contract.contract_status == "complete"
        for contract in workflow_node_contract_registry.list()
    )
    planner_count = sum(
        contract.planner.enabled
        for contract in workflow_node_contract_registry.list()
    )
    assert "911593f505b05b01037769f578e21f22d2a1c9af" in markdown
    assert "R0/R1/R1.5/R1.6/R1.7/R1.8/R1.9/R2.0/R2.1 PR1" in markdown
    assert "44、画布目录项 42" in markdown
    assert "R1.6 结果" in markdown
    assert "自研节点总数 47、画布目录项 45、当前 18 个" in markdown
    assert f"{compatibility_count} 个 compatibility 合同" in markdown
    assert f"自研节点总数 {native_count}" in markdown
    assert f"{palette_count} 个可新增 Palette 项" in markdown
    assert f"Planner 可生成类型仍固定为 {planner_count} 类" in markdown
    assert "R1.8 结果" in markdown
    assert native_count == 50
    assert palette_count == 47
    assert complete_count == 42
    assert compatibility_count == 8
    assert planner_count == 7
    assert "R1.9 结果" in markdown
    assert (
        "- R2.0 结果：不新增普通节点，将 `human_intervention`、`mcp_tool`、"
        "`variable_assign` 提升为完整 V2 合同，并退役旧知识引用新增入口；"
        "当前 50 Native、48 个可新增 Palette 项、41 个完整合同、"
        "9 个 compatibility 合同、7 个 Planner 节点"
    ) in markdown
    assert (
        "- R2.1 PR1 结果：不新增 `NativeNodeKind`，将 `code` 提升为只执行预定义"
        "操作的“安全文本加工 V2”完整合同，并从 Palette 移除退役 `template_transform`；"
        "旧草稿和既有激活版本继续兼容，模板文本能力由 `variable_assign` V2 承接；"
        "当前 50 Native、47 个可新增 Palette 项、42 个完整合同、"
        "8 个 compatibility 合同、7 个 Planner 节点"
    ) in markdown
