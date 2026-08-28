from __future__ import annotations

import csv
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import get_args

from scripts.generate_workflow_capability_audit import current_registry_facts
from server.workflow_native.node_contracts import workflow_node_contract_registry
from server.workflow_native.schemas import NativeNodeKind


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


def test_capability_audit_rejects_stale_specialized_review(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    source = root / "docs" / "audits" / "n8n-node-capability-matrix.csv"
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    reviewed = next(row for row in rows if row["n8n内部标识"] == "manualTrigger")
    reviewed["判断说明"] += " 已变更"
    tampered = tmp_path / "tampered.csv"
    with tampered.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "generate_workflow_capability_audit.py"),
            "--source-csv",
            str(tampered),
            "--output-dir",
            str(tmp_path / "generated"),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "requires a fresh manual review" in result.stderr


def test_capability_audit_tracks_baseline_and_r1_nodes() -> None:
    root = Path(__file__).resolve().parents[2]
    csv_path = root / "docs" / "audits" / "n8n-node-capability-matrix.csv"
    markdown_path = root / "docs" / "audits" / "N8N_NODE_CAPABILITY_MATRIX.md"
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 563
    assert len({row["来源条目标识"] for row in rows}) == 563
    assert Counter(row["覆盖等级"] for row in rows) == {
        "exact": 36,
        "limited": 72,
        "composable": 271,
        "none": 184,
    }
    mapped = {row["n8n内部标识"]: row for row in rows}
    mapped_by_source = {row["来源条目标识"]: row for row in rows}
    assert mapped["scheduleTrigger"]["模镜对应节点"] == "scheduled_start"
    assert mapped["webhook"]["模镜对应节点"] == "http_event_entry"
    assert mapped["rssFeedReadTrigger"]["模镜对应节点"] == "rss_event_entry"
    assert mapped["rssFeedReadTrigger"]["覆盖等级"] == "exact"
    assert "首次启用建立无回放基线" in mapped["rssFeedReadTrigger"]["判断说明"]
    assert mapped["rssFeedRead"]["覆盖等级"] == "composable"
    assert {
        row["n8n内部标识"]
        for row in rows
        if row["n8n节点族"] == "触发节点"
        and row["纳入建议"] == "核心通用能力候选"
    } == {"emailReadImap", "localFileTrigger", "mcpTrigger", "sseTrigger"}
    assert all(
        mapped[key]["纳入建议"] == "按需消息基础设施连接器"
        for key in (
            "amqpTrigger",
            "awsSnsTrigger",
            "kafkaTrigger",
            "mqttTrigger",
            "postgresTrigger",
            "rabbitmqTrigger",
            "redisTrigger",
        )
    )
    assert all(
        mapped[key]["纳入建议"] == "按需厂商/应用连接器"
        for key in (
            "airtableTrigger",
            "githubTrigger",
            "slackTrigger",
            "stripeTrigger",
        )
    )
    assert all(
        mapped[key]["纳入建议"]
        == "平台级能力例外；不作为独立画布触发候选"
        for key in ("chat", "chatTrigger")
    )
    assert mapped["evaluationTrigger"]["纳入建议"] == "隔离审计，不作实现参考"
    assert mapped["e2eTestPollingTrigger"]["纳入建议"] == "排除测试/平台内部条目"
    assert mapped["n8nTrigger"]["纳入建议"] == "排除测试/平台内部条目"
    assert mapped["cron"]["纳入建议"] == "合并或排除隐藏/遗留条目"
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
    assert mapped["merge"]["模镜当前状态"] == "已实现"
    assert mapped["merge"]["模镜对应节点"] == "data_merge"
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
    assert mapped["memoryManager"]["模镜对应节点"] == "workflow_agent"
    assert "没有可独立连线" in mapped["memoryManager"]["判断说明"]
    assert mapped["messageAnAgent"]["模镜对应节点"] == "agent_task / agent_handoff"
    assert mapped["messageAnAgent"]["覆盖等级"] == "limited"
    for source_ref in (
        "langchain:dist/nodes/agents/Agent/Agent.node.js",
        "langchain:dist/nodes/agents/Agent/AgentTool.node.js",
    ):
        assert mapped_by_source[source_ref]["模镜对应节点"] == "workflow_agent"
        assert "agent /" not in mapped_by_source[source_ref]["模镜对应节点"]
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
    for key in (
        "splitOut",
        "moveBinaryData",
        "clearbit",
        "deepL",
        "hunter",
        "mindee",
    ):
        assert mapped[key]["模镜当前状态"] == "未实现"
        assert mapped[key]["模镜对应节点"] == "—"
        assert mapped[key]["覆盖等级"] == "none"
    for key in ("html", "htmlExtract", "markdown", "xml"):
        assert mapped[key]["模镜当前状态"] == "部分实现"
        assert mapped[key]["模镜对应节点"] == "document_extractor"
        assert mapped[key]["覆盖等级"] == "limited"
        assert mapped[key]["人工复核"] == "R2.4"
    for key in ("form", "formTrigger"):
        assert mapped[key]["模镜当前状态"] == "部分实现"
        assert mapped[key]["模镜对应节点"] == "form_event_entry"
        assert mapped[key]["覆盖等级"] == "limited"
        assert mapped[key]["人工复核"] == "R2.5"
    assert mapped["aiTransform"]["模镜对应节点"] == "llm / variable_assign"
    assert all(mapped[key]["模镜当前状态"] == "已实现" for key in (
        "scheduleTrigger", "webhook", "wait", "respondToWebhook", "errorTrigger",
        "executeWorkflowTrigger", "executeWorkflow", "stopAndError", "switch",
        "filter", "sort", "removeDuplicates", "aggregate", "summarize",
        "httpRequest", "if", "compareDatasets", "set", "convertToFile",
        "dateTime", "limit", "itemLists", "extractFromFile",
        "informationExtractor", "outputParserStructured", "outputParserItemList",
        "outputParserAutofixing", "textClassifier", "guardrails",
        "merge",
        "rssFeedReadTrigger",
    ))
    assert all("不复制代码" in row["许可证边界"] or "企业条目" in row["许可证边界"] for row in rows)
    expected_level = {
        "已实现": "exact",
        "部分实现": "limited",
        "通用节点可覆盖": "composable",
        "仅目录声明": "none",
        "仅运行目录声明": "none",
        "未实现": "none",
    }
    assert all(row["覆盖等级"] == expected_level[row["模镜当前状态"]] for row in rows)
    assert all(row["模镜证据"].strip() for row in rows)
    expected_review_round = {
        "splitInBatches": "R2.3",
        "html": "R2.4",
        "htmlExtract": "R2.4",
        "markdown": "R2.4",
        "xml": "R2.4",
        "form": "R2.5",
        "formTrigger": "R2.5",
        "rssFeedReadTrigger": "R2.7",
    }
    assert all(
        row["人工复核"]
        == expected_review_round.get(row["n8n内部标识"], "R2.2")
        for row in rows
        if row["覆盖等级"] in {"exact", "limited"}
    )
    assert mapped["splitInBatches"]["模镜当前状态"] == "部分实现"
    assert mapped["splitInBatches"]["模镜对应节点"] == "iteration"
    assert mapped["splitInBatches"]["覆盖等级"] == "limited"
    assert mapped["splitInBatches"]["人工复核"] == "R2.3"
    assert "最多 32 项" in mapped["splitInBatches"]["判断说明"]
    assert "图循环" in mapped["splitInBatches"]["判断说明"]
    assert all(
        len(row["复核指纹"]) == 64
        for row in rows
        if row["覆盖等级"] in {"exact", "limited"}
    )
    assert all(
        "非专用连接器" in row["模镜证据"]
        for row in rows
        if row["覆盖等级"] == "composable"
    )
    native_kinds = set(get_args(NativeNodeKind))
    complete_kinds = {
        contract.kind
        for contract in workflow_node_contract_registry.list()
        if contract.contract_status == "complete"
    }
    for row in rows:
        mapped_kinds = {
            item.strip()
            for item in row["模镜对应节点"].split("/")
            if item.strip() and item.strip() != "—"
        }
        assert mapped_kinds <= native_kinds
        if row["覆盖等级"] == "exact":
            assert mapped_kinds
            assert mapped_kinds <= complete_kinds
            assert "运行/测试" in row["模镜证据"]

    markdown = markdown_path.read_text(encoding="utf-8")
    facts = current_registry_facts()
    assert "911593f505b05b01037769f578e21f22d2a1c9af" in markdown
    assert "R0/R1/R1.5/R1.6/R1.7/R1.8/R1.9/R2.0/R2.1/R2.2/R2.3/R2.4/R2.5/R2.6/R2.7" in markdown
    assert "44、画布目录项 42" in markdown
    assert "R1.6 结果" in markdown
    assert "自研节点总数 47、画布目录项 45、当前 18 个" in markdown
    current_registry_line = (
        f"当前 Registry 事实：{facts.native} Native、"
        f"{facts.palette_registered} 个已登记 Palette 项、"
        f"默认 {facts.palette_draggable} 个可拖拽 Palette 项、"
        f"{facts.complete} 个完整合同、{facts.compatibility} 个 compatibility 合同、"
        f"{facts.planner} 个 Planner 节点"
    )
    assert current_registry_line in markdown
    assert f"Planner 可生成类型仍固定为 {facts.planner} 类" in markdown
    assert "R1.8 结果" in markdown
    assert facts.native == 54
    assert facts.palette_registered == 50
    assert facts.palette_draggable == 49
    assert facts.complete == 51
    assert facts.compatibility == 3
    assert facts.planner == 7
    assert facts.runtime_feature_gated == (
        "knowledge_write_proposal",
        "rss_event_entry",
    )
    assert "当前 Registry 事实：54 Native、50 个可新增 Palette 项" not in markdown
    assert (
        "默认运行功能门禁：2 个已登记项（`knowledge_write_proposal`、"
        "`rss_event_entry`）允许编辑但执行面关闭"
    ) in markdown
    assert "## 平台级能力例外（不计入画布节点覆盖状态）" in markdown
    assert "Xpert Chat" in markdown
    assert "Evaluation / Evolution" in markdown
    assert "`workflow_agent + toolset_resource`" in markdown
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
        "当时 50 Native、47 个可新增 Palette 项、42 个完整合同、"
        "8 个 compatibility 合同、7 个 Planner 节点"
    ) in markdown
    assert (
        "- R2.1 PR2 结果：新增完整合同 `data_merge`，并将经典运行器升级为带持久化"
        "边到达账本的 Scheduler V2；支持可靠 Fan-in、有界数组拼接和受限一对一 "
        "inner join；当时 51 Native、48 个可新增 Palette 项、43 个完整合同、"
        "8 个 compatibility 合同、7 个 Planner 节点"
    ) in markdown
    assert (
        "- R2.2 PR1 结果：将 `variable_aggregator` 提升为“变量打包”V2 完整合同"
    ) in markdown
    r22_pr1_snapshot = (
        "当时 51 Native、48 个可新增 Palette 项、44 个完整合同、"
        "7 个 compatibility 合同、7 个 Planner 节点"
    )
    assert r22_pr1_snapshot in markdown
    assert (
        "- R2.2 PR2 结果：将 `agent_task`、`agent_handoff`、`handoff_router` "
        "提升为类型化 V2 合同"
    ) in markdown
    r22_pr2_snapshot = (
        "当时 51 Native、47 个可新增 Palette 项、47 个完整合同、"
        "4 个 compatibility 合同、7 个 Planner 节点"
    )
    assert r22_pr2_snapshot in markdown
    assert r22_pr1_snapshot != r22_pr2_snapshot
    assert (
        "- R2.3 结果：不新增节点类型，将 `iteration` 提升为“批量处理”V2 完整合同"
    ) in markdown
    assert (
        "- R2.4 结果：不新增节点类型，将 `document_extractor` 升级为“内容解析”V3"
    ) in markdown
    assert (
        "- R2.5 结果：新增完整合同 `form_event_entry`，发布同源签名表单、"
        "严格类型字段与固定接受页"
    ) in markdown
    assert (
        "- R2.6 结果：新增完整合同 `knowledge_write_proposal`，只向 Knowledge "
        "Inbox 创建或复用待审批提议"
    ) in markdown
    assert (
        "- R2.7 结果：新增完整合同 `rss_event_entry`，以仅公网 HTTPS、逐跳安全校验、"
        "首次无回放基线和持久条目去重提供 RSS 2.0/Atom 1.0 订阅入口"
    ) in markdown
    assert (
        "- R2.2 PR1 结果：将 `variable_aggregator` 提升为“变量打包”V2 完整合同，"
        "修正元智能体新图的报告汇总，并为 563 行参考清单增加 "
        "exact/limited/composable/none 证据门禁；" + r22_pr2_snapshot
    ) not in markdown
    assert "覆盖等级用于表达证据强度" in markdown
    assert current_registry_line == (
        "当前 Registry 事实：54 Native、50 个已登记 Palette 项、"
        "默认 49 个可拖拽 Palette 项、51 个完整合同、"
        "3 个 compatibility 合同、7 个 Planner 节点"
    )
