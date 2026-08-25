from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import get_args


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DIRECT_UPDATES = {
    "merge": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "data_merge",
        "判断说明": "自研数据合流依赖边到达账本，等待左右路径解析后执行；支持有界数组拼接和按 1–3 个顶层复合键的一对一 inner join，不宣称外连接或多对多 Join。",
    },
    "function": {
        "模镜建议节点名": "安全文本加工（遗留函数场景）",
        "模镜当前状态": "部分实现",
        "模镜对应节点": "code",
        "判断说明": "自研安全文本加工 V2 以预定义操作覆盖有限的遗留函数场景；不执行任意 JavaScript，因此不等同于通用函数节点。",
    },
    "functionItem": {
        "模镜建议节点名": "安全文本加工（逐项场景）",
        "模镜当前状态": "部分实现",
        "模镜对应节点": "code",
        "判断说明": "自研安全文本加工 V2 可与列表操作、迭代组合处理有限的逐项文本场景；不执行任意单项代码，因此保持部分实现。",
    },
    "renameKeys": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "object_transform",
        "判断说明": "自研对象整理已提供稳定步骤 ID 的顶层字段重命名，并对缺失字段和命名冲突失败关闭。",
    },
    "mcpClientTool": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "mcp_tool",
        "判断说明": "自研 MCP 工具 V2 固定服务器、单一工具与 Schema 指纹，使用类型化参数和脱敏审批；不扩展为完整工具集连接能力。",
    },
    "mcpClient": {
        "模镜当前状态": "部分实现",
        "模镜对应节点": "mcp_tool",
        "判断说明": "自研 MCP 工具 V2 只闭环一个固定工具调用；没有把整套动态 MCP Toolset 暴露为同等画布节点。",
    },
    "mcpRegistryClientTool": {
        "模镜当前状态": "部分实现",
        "模镜对应节点": "mcp_tool",
        "判断说明": "模镜有自有会话 Registry 和固定单工具解析，但不宣称具备参考项的完整注册表客户端节点语义。",
    },
    "messageAnAgent": {
        "模镜当前状态": "部分实现",
        "模镜对应节点": "agent_task / agent_handoff",
        "判断说明": "自研协作 V2 使用类型化任务凭证、固定接收目标和可恢复等待闭环；消息协议、目标体系与参考应用仍不同，因此保持受限实现。",
    },
    "memoryManager": {
        "模镜当前状态": "部分实现",
        "模镜对应节点": "workflow_agent",
        "判断说明": "智能体内部支持受控记忆读写配置，仅属于嵌入式覆盖；当前没有可独立连线和配置的记忆管理节点。",
    },
    "informationExtractor": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "parameter_extractor",
        "判断说明": "自研参数提取器 V2 将字段表或受限 JSON Schema 编译为严格输出合同，写入类型化对象或对象数组；非法、缺字段、错类型和超限输出均失败关闭。",
    },
    "outputParserStructured": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "parameter_extractor",
        "判断说明": "自研参数提取器 V2 对模型结果执行 JSON 解析与完整 Schema 校验，不以模型原文、空对象或部分字段伪装成功。",
    },
    "outputParserItemList": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "parameter_extractor",
        "判断说明": "自研参数提取器 V2 的对象列表形态输出真正的 JSON 对象数组，并逐项按同一严格 Schema 校验。",
    },
    "outputParserAutofixing": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "parameter_extractor",
        "判断说明": "用户显式启用时，自研参数提取器最多追加一次同模型修复调用；再次失败即终止且调用计入现有用量统计。",
    },
    "textClassifier": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "question_classifier",
        "判断说明": "自研问题分类器 V2 提供稳定类别 ID 与默认出口，按顺序首个规则命中，并可选择固定内部提示的模型分类或规则后模型兜底。",
    },
    "guardrails": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "runtime_middleware",
        "判断说明": "自研内容策略中间件对智能体文本输入和最终可见输出执行确定性字词、邮箱、保守电话与疑似凭据阻断或固定脱敏；不宣称多模态或语义安全分类。",
    },
    "convertToFile": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "file_output",
        "判断说明": "自研生成文件把类型化变量写成作用域内的 TXT、Markdown、JSON、CSV、PDF、DOCX 或 XLSX 资产；不提供任意服务器路径写入、PPTX 或二进制透传。",
    },
    "set": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "object_transform",
        "判断说明": "自研对象整理按稳定步骤 ID 顺序执行顶层字段设置、默认值、重命名、删除和保留；严格处理缺失字段与命名冲突。",
    },
    "dateTime": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "time_tool",
        "判断说明": "自研时间工具支持 IANA 时区、ISO 转换、格式化、日历运算、差值和周期边界，并拒绝 DST 不存在或歧义的本地时间。",
    },
    "limit": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "list_operation",
        "判断说明": "自研列表操作可按数量保留、跳过或按半开区间截取最多 10,000 项的类型化数组。",
    },
    "itemLists": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "list_operation",
        "判断说明": "自研列表操作统一提供长度、拼接、首末项、筛选、稳定排序、去重、保留、跳过与区间截取；新操作只接受真正的 JSON 数组。",
    },
    "extractFromFile": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "document_extractor",
        "判断说明": "自研文档提取器只读取当前经典工作流资产或私有智能体明确共享的附件并输出受限文本；不宣称结构化表格提取或任意磁盘读取。",
    },
    "httpRequest": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "http_request",
        "判断说明": "自研安全 HTTP 请求仅允许固定公网源站，使用结构化绑定与加密凭据，逐跳校验并绑定 DNS 结果，限制超时、重定向和响应大小；不支持 OAuth2、私网、二进制响应或自动重试。",
    },
    "if": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "condition",
        "判断说明": "自研类型化条件提供稳定是/否出口，支持九类严格比较；变量不存在、字段缺失或类型非法时失败关闭。",
    },
    "compareDatasets": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "dataset_compare",
        "判断说明": "自研数据集对照按 1–3 个顶层复合键比较两份对象数组，确定性输出新增、删除、变化与未变化统计。",
    },
    "errorTrigger": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "failure_event_entry",
        "判断说明": "自研失败处置入口显式订阅 1–50 个独立工作流项目，只接收激活后的脱敏失败摘要；原子派发、occurrence key 去重并抑制处理器递归触发。",
    },
    "executeWorkflowTrigger": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "workflow_call_entry",
        "判断说明": "自研私有子流程入口复用全局类型化输入声明，只接受内部固定版本同步调用，不生成公开远程调用接口。",
    },
    "executeWorkflow": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "invoke_workflow",
        "判断说明": "自研同步调用节点固定项目与发布版本，校验类型化输入、环路、深度和后代上限，并持久化父子执行关系与稳定 occurrence key。",
    },
    "scheduleTrigger": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "scheduled_start",
        "判断说明": "自研定时启动支持单次、30 秒以上间隔、五段 Cron、IANA 时区、latest misfire 与 skip overlap；画布以日期、时长单位和常用日历规则配置。",
    },
    "webhook": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "http_event_entry",
        "判断说明": "自研私有 HTTP 事件入口仅支持 POST、JSON/纯文本、哈希密钥、幂等键与限流；可收紧正文格式和大小，并将完整事件与正文登记为全局变量。",
    },
    "wait": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "suspend_wait",
        "判断说明": "自研挂起等待使用 durable continuation，支持时长单位或带 IANA 时区的日期时间，最长 30 天；HTTP 无回执链路可返回 202 后持久挂起，恢复后原始请求正文不可用。",
    },
    "respondToWebhook": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "http_event_reply",
        "判断说明": "自研 HTTP 事件回执支持常用语义状态或 200-599 自定义状态、文本/JSON 模板正文，必须是私有 HTTP 工作流终端节点。",
    },
    "stopAndError": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "terminate_error",
        "判断说明": "自研安全错误码与固定消息终止；禁止模板和出边。",
    },
    "switch": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "multi_route",
        "判断说明": "自研顺序首个命中分派；使用稳定出口 ID，固定默认出口。",
    },
    "filter": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "list_operation",
        "判断说明": "自研 1–10 条类型化 all/any 筛选规则。",
    },
    "sort": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "list_operation",
        "判断说明": "自研 1–3 个顶层字段稳定排序，支持空值位置。",
    },
    "removeDuplicates": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "list_operation",
        "判断说明": "自研深比较或最多五个顶层字段去重，保留首次出现项。",
    },
    "aggregate": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "data_aggregate",
        "判断说明": "自研对象数组分组聚合，输出稳定有序的新对象数组。",
    },
    "summarize": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "data_aggregate",
        "判断说明": "自研对象数组分组与 count/sum/avg/min/max 度量。",
    },
    "dataTable": {
        "模镜当前状态": "部分实现",
        "模镜对应节点": "data_table_query / data_table_insert / data_table_update / data_table_delete",
        "判断说明": "模镜已有自研 Agent Table 四类操作，但 Schema、过滤与产品语义不宣称和参考项等价。",
    },
    "stickyNote": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "annotation",
        "判断说明": "模镜已有画布注释节点；它只承载编辑元数据，不进入执行语义。",
    },
}

SOURCE_UPDATES = {
    "base:dist/nodes/Code/Code.node.js": {
        "模镜建议节点名": "安全文本加工",
        "模镜当前状态": "部分实现",
        "模镜对应节点": "code",
        "判断说明": "自研安全文本加工 V2 只提供预定义、无外部 IO 的文本操作，使用结构化输入输出并失败关闭；不执行用户编写的 JavaScript 或 Python，因此不等同于通用代码沙箱。",
    },
    "langchain:dist/nodes/code/Code.node.js": {
        "模镜建议节点名": "安全文本加工（模型编排场景）",
        "模镜当前状态": "部分实现",
        "模镜对应节点": "code",
        "判断说明": "自研安全文本加工 V2 只提供预定义、无外部 IO 的文本操作；不执行用户代码，也不开放模型工具沙箱，因此保持部分实现。",
    },
    "langchain:dist/nodes/agents/Agent/Agent.node.js": {
        "模镜当前状态": "部分实现",
        "模镜对应节点": "workflow_agent",
        "判断说明": "自研工作流智能体提供模型、工具和受控策略执行；旧 agent 已退役新增入口，且不宣称与参考项的全部策略和工具生态等价。",
    },
    "langchain:dist/nodes/agents/Agent/AgentTool.node.js": {
        "模镜当前状态": "部分实现",
        "模镜对应节点": "workflow_agent",
        "判断说明": "自研工作流智能体可在受控工具模式下执行，但没有复制参考工具节点的合同或完整生态，因此保持受限实现。",
    },
}

BASELINE_CORRECTIONS = {
    "set": {
        "模镜当前状态": "部分实现",
        "模镜对应节点": "variable_assign / template_transform",
        "判断说明": "现有变量赋值与模板变换只生成文本，尚未提供类型化对象字段新增、删除、重命名与默认值合同。",
    },
    "itemLists": {
        "模镜当前状态": "部分实现",
        "模镜对应节点": "list_operation",
        "判断说明": "现有列表节点已支持长度、拼接、首末项、类型化筛选、稳定排序与去重；遗留参考项不作为独立节点实现。",
    },
}

AUDIT_TRUST_CORRECTIONS = {
    "splitOut": {
        "模镜当前状态": "未实现",
        "模镜对应节点": "—",
        "判断说明": "变量打包把多个已存在变量组装为对象，不会把数组或对象拆成独立项目，因此不覆盖拆分语义。",
    },
    "moveBinaryData": {
        "模镜当前状态": "未实现",
        "模镜对应节点": "—",
        "判断说明": "类型化赋值不读取、生成或转换二进制内容，当前没有经过运行验证的二进制转换合同。",
    },
    "memoryManager": {
        "模镜当前状态": "部分实现",
        "模镜对应节点": "workflow_agent",
        "判断说明": "workflow_agent 内嵌受控记忆能力，但没有可独立连线、管理和迁移的记忆节点。",
    },
    "html": {
        "模镜当前状态": "未实现",
        "模镜对应节点": "—",
        "判断说明": "安全文本加工只执行大小写、固定替换和拼接，不具备 HTML 解析或转换合同。",
    },
    "htmlExtract": {
        "模镜当前状态": "未实现",
        "模镜对应节点": "—",
        "判断说明": "当前没有 HTML DOM 解析、选择器提取或结构化输出的节点合同和运行测试。",
    },
    "markdown": {
        "模镜当前状态": "未实现",
        "模镜对应节点": "—",
        "判断说明": "文本模板可以生成 Markdown 文本，但没有 Markdown 解析或格式转换合同。",
    },
    "xml": {
        "模镜当前状态": "未实现",
        "模镜对应节点": "—",
        "判断说明": "当前没有 XML 解析、序列化或 Schema 校验的节点合同和运行测试。",
    },
}

UNVERIFIED_VENDOR_NODE_IDS = {
    "clearbit",
    "cortex",
    "deepL",
    "dropcontact",
    "humanticAi",
    "hunter",
    "jinaAi",
    "lingvaNex",
    "mailcheck",
    "mindee",
    "openThesaurus",
    "peekalink",
    "uproc",
}

COMPOSABLE_NODE_WHITELIST = {
    "code",
    "http_request",
    "mcp_tool",
    "variable_assign",
}

EXACT_RUNTIME_EVIDENCE = {
    "annotation": "server/tests/test_workflow_node_contracts.py",
    "condition": "server/tests/test_workflow_r17_typed_data.py",
    "data_aggregate": "server/tests/test_workflow_control_data_nodes.py",
    "data_merge": "server/tests/test_workflow_r21_fanin_data_merge.py",
    "dataset_compare": "server/tests/test_workflow_r17_typed_data.py",
    "document_extractor": "server/tests/test_workflow_r18_file_data.py",
    "failure_event_entry": "server/tests/test_workflow_deployments.py",
    "file_output": "server/tests/test_workflow_r18_file_data.py",
    "http_event_entry": "server/tests/test_workflow_deployments.py",
    "http_event_reply": "server/tests/test_workflow_deployments.py",
    "http_request": "server/tests/test_workflow_r17_secure_http.py",
    "input": "server/tests/test_workflow_run_contract.py",
    "invoke_workflow": "server/tests/test_workflow_subworkflows.py",
    "list_operation": "server/tests/test_workflow_control_data_nodes.py",
    "mcp_tool": "server/tests/test_workflow_mcp_tool_runtime.py",
    "multi_route": "server/tests/test_workflow_control_data_nodes.py",
    "object_transform": "server/tests/test_workflow_r18_file_data.py",
    "parameter_extractor": "server/tests/test_workflow_typed_ai.py",
    "question_classifier": "server/tests/test_workflow_typed_ai.py",
    "runtime_middleware": "server/tests/test_workflow_content_policy_runtime.py",
    "scheduled_start": "server/tests/test_workflow_deployments.py",
    "suspend_wait": "server/tests/test_workflow_deployments.py",
    "terminate_error": "server/tests/test_workflow_control_data_nodes.py",
    "time_tool": "server/tests/test_workflow_r18_file_data.py",
    "workflow_call_entry": "server/tests/test_workflow_subworkflows.py",
}

COVERAGE_LEVEL_BY_STATUS = {
    "已实现": "exact",
    "部分实现": "limited",
    "通用节点可覆盖": "composable",
    "仅目录声明": "none",
    "仅运行目录声明": "none",
    "未实现": "none",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--record-specialized-review",
        action="store_true",
        help="Record review fingerprints after a real manual review of exact/limited rows.",
    )
    return parser.parse_args()


def status_bucket(value: str) -> str:
    if value == "已实现":
        return "已实现"
    if value == "部分实现":
        return "部分实现"
    if value == "通用节点可覆盖":
        return "通用覆盖"
    if value in {"仅目录声明", "仅运行目录声明"}:
        return "目录声明"
    return "未实现"


def current_registry_counts() -> tuple[int, int, int, int, int]:
    from server.workflow_native.node_contracts import workflow_node_contract_registry
    from server.workflow_native.schemas import NativeNodeKind
    from server.xpert_runtime.workflow_node_registry import (
        WorkflowNodeRegistry,
        register_builtin_workflow_nodes,
    )

    contracts = workflow_node_contract_registry.list()
    registry = WorkflowNodeRegistry()
    register_builtin_workflow_nodes(registry)
    palette_kinds = {
        item.kind
        for section in registry.sections()
        for item in section.items
    } | {item.kind for item in registry.knowledge_pipeline().items}
    return (
        len(get_args(NativeNodeKind)),
        len(palette_kinds),
        sum(contract.contract_status == "complete" for contract in contracts),
        sum(contract.contract_status == "compatibility" for contract in contracts),
        sum(contract.planner.enabled for contract in contracts),
    )


def replace_retired_node_mappings(value: str) -> str:
    mapped: list[str] = []
    for item in str(value or "").split("/"):
        node_kind = item.strip()
        if node_kind == "template_transform":
            node_kind = "variable_assign"
        if node_kind and node_kind not in mapped:
            mapped.append(node_kind)
    return " / ".join(mapped)


def mapped_node_kinds(value: str) -> list[str]:
    return [
        item.strip()
        for item in str(value or "").split("/")
        if item.strip() and item.strip() != "—"
    ]


def audit_evidence(row: dict[str, str]) -> str:
    level = row["覆盖等级"]
    kinds = mapped_node_kinds(row.get("模镜对应节点", ""))
    if level == "exact":
        evidence = [EXACT_RUNTIME_EVIDENCE[kind] for kind in kinds]
        return f"NodeContract V3 complete；运行/测试：{'；'.join(dict.fromkeys(evidence))}"
    if level == "limited":
        return "受限子集；未覆盖语义见判断说明；对应合同与运行路径需按节点复核"
    if level == "composable":
        return "受控通用组合路径；非专用连接器；认证与调用闭环需按目标单独验证"
    return "无对应运行合同或仅保留名称目录证据"


def specialized_review_fingerprint(row: dict[str, str]) -> str:
    reviewed_fields = (
        "来源条目标识",
        "n8n内部标识",
        "模镜当前状态",
        "模镜对应节点",
        "判断说明",
        "覆盖等级",
        "模镜证据",
    )
    payload = "\n".join(str(row.get(field, "")) for field in reviewed_fields)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_audit_rows(rows: list[dict[str, str]]) -> None:
    from server.workflow_native.node_contracts import workflow_node_contract_registry

    contracts = {
        contract.kind: contract
        for contract in workflow_node_contract_registry.list()
    }
    if len({row.get("来源条目标识", "") for row in rows}) != len(rows):
        raise SystemExit("Capability audit contains duplicate source references")
    for row in rows:
        status = row.get("模镜当前状态", "")
        level = row.get("覆盖等级", "")
        if level != COVERAGE_LEVEL_BY_STATUS.get(status):
            raise SystemExit(
                f"Coverage level mismatch for {row.get('来源条目标识')}: {status} -> {level}"
            )
        kinds = mapped_node_kinds(row.get("模镜对应节点", ""))
        unknown = sorted(set(kinds) - set(contracts))
        if unknown:
            raise SystemExit(
                f"Unknown ModelMirror mappings for {row.get('来源条目标识')}: {unknown}"
            )
        if level == "exact":
            if not kinds or any(
                contracts[kind].contract_status != "complete" for kind in kinds
            ):
                raise SystemExit(
                    f"Exact coverage requires complete NodeContracts: {row.get('来源条目标识')}"
                )
            missing_evidence = sorted(set(kinds) - set(EXACT_RUNTIME_EVIDENCE))
            if missing_evidence:
                raise SystemExit(
                    f"Exact coverage lacks runtime/test evidence: {missing_evidence}"
                )
            missing_evidence_files = sorted(
                {
                    EXACT_RUNTIME_EVIDENCE[kind]
                    for kind in kinds
                    if not (ROOT / EXACT_RUNTIME_EVIDENCE[kind]).is_file()
                }
            )
            if missing_evidence_files:
                raise SystemExit(
                    "Exact coverage points to missing evidence files: "
                    f"{missing_evidence_files}"
                )
        elif level == "limited":
            if not kinds:
                raise SystemExit(
                    f"Limited coverage needs a mapped implementation: {row.get('来源条目标识')}"
                )
            if not any(
                marker in row.get("判断说明", "")
                for marker in ("不", "未", "无", "仅", "缺", "没有", "受限", "不同")
            ):
                raise SystemExit(
                    f"Limited coverage must state its semantic gap: {row.get('来源条目标识')}"
                )
        elif level == "composable":
            if not kinds or not set(kinds) <= COMPOSABLE_NODE_WHITELIST:
                raise SystemExit(
                    f"Composable coverage escaped the controlled whitelist: {row.get('来源条目标识')}"
                )
        elif kinds:
            raise SystemExit(
                f"None coverage cannot retain a node mapping: {row.get('来源条目标识')}"
            )
        if not row.get("模镜证据", "").strip():
            raise SystemExit(
                f"Capability row lacks evidence: {row.get('来源条目标识')}"
            )
        if level in {"exact", "limited"} and row.get("人工复核") != "R2.2":
            raise SystemExit(
                f"Specialized coverage was not manually reviewed: {row.get('来源条目标识')}"
            )
        if level in {"exact", "limited"} and row.get("复核指纹") != specialized_review_fingerprint(row):
            raise SystemExit(
                f"Specialized coverage changed after review: {row.get('来源条目标识')}"
            )


def main() -> None:
    args = parse_args()
    with args.source_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    if len(rows) != 563:
        raise SystemExit(f"Expected 563 reference rows, found {len(rows)}")
    for row in rows:
        correction = BASELINE_CORRECTIONS.get(row.get("n8n内部标识", ""))
        if correction:
            row.update(correction)
        update = DIRECT_UPDATES.get(row.get("n8n内部标识", ""))
        if update:
            row.update(update)
        trust_correction = AUDIT_TRUST_CORRECTIONS.get(
            row.get("n8n内部标识", "")
        )
        if trust_correction:
            row.update(trust_correction)
        if row.get("n8n内部标识", "") in UNVERIFIED_VENDOR_NODE_IDS:
            row.update(
                {
                    "模镜当前状态": "未实现",
                    "模镜对应节点": "—",
                    "判断说明": (
                        "当前没有该厂商服务经过验证的认证、请求和响应合同；"
                        "通用模型节点不能替代专用服务连接器。"
                    ),
                }
            )
        source_update = SOURCE_UPDATES.get(row.get("来源条目标识", ""))
        if source_update:
            row.update(source_update)
        row["模镜对应节点"] = replace_retired_node_mappings(
            row.get("模镜对应节点", "")
        )
        row["许可证边界"] = (
            "仅名称/节点类型能力参考；不复制代码、参数 Schema、文案、图标、测试或 UI"
            if ".ee" not in row.get("来源条目标识", "")
            else "企业条目仅保留名称审计；排除实现参考"
        )
        row["覆盖等级"] = COVERAGE_LEVEL_BY_STATUS.get(
            row.get("模镜当前状态", ""),
            "none",
        )
        row["模镜证据"] = audit_evidence(row)
        if row["覆盖等级"] in {"exact", "limited"}:
            fingerprint = specialized_review_fingerprint(row)
            if args.record_specialized_review:
                row["人工复核"] = "R2.2"
                row["复核指纹"] = fingerprint
            elif row.get("人工复核") != "R2.2" or row.get("复核指纹") != fingerprint:
                raise SystemExit(
                    "Specialized coverage requires a fresh manual review: "
                    f"{row.get('来源条目标识')}"
                )
        else:
            row["人工复核"] = ""
            row["复核指纹"] = ""
    for fieldname in ("覆盖等级", "人工复核", "模镜证据", "复核指纹"):
        if fieldname not in fieldnames:
            fieldnames.append(fieldname)
    validate_audit_rows(rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "n8n-node-capability-matrix.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    statuses = Counter(status_bucket(row["模镜当前状态"]) for row in rows)
    domains: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        domains[row["能力域"]][status_bucket(row["模镜当前状态"])] += 1
        domains[row["能力域"]]["总数"] += 1
    ee_count = sum(".ee" in row.get("来源条目标识", "") for row in rows)
    direct_rows = [
        row
        for row in rows
        if row.get("n8n内部标识") in DIRECT_UPDATES
        or row.get("n8n内部标识") in AUDIT_TRUST_CORRECTIONS
        or row.get("n8n内部标识") in UNVERIFIED_VENDOR_NODE_IDS
        or row.get("来源条目标识") in SOURCE_UPDATES
    ]
    native_count, palette_count, complete_count, compatibility_count, planner_count = (
        current_registry_counts()
    )

    domain_lines = []
    for domain, counts in domains.items():
        domain_lines.append(
            f"| {domain} | {counts['总数']} | {counts['已实现']} | "
            f"{counts['部分实现']} | {counts['通用覆盖']} | "
            f"{counts['目录声明']} | {counts['未实现']} |"
        )
    direct_lines = [
        f"| {row['能力域']} | {row['模镜建议节点名']} | {row['模镜对应节点']} | "
        f"{row['n8n原名参考']} | {row['模镜当前状态']} |"
        for row in direct_rows
    ]
    markdown = f"""# 工作流能力域与节点类型对照审计（#213 + R0/R1/R1.5/R1.6/R1.7/R1.8/R1.9/R2.0/R2.1/R2.2）

- 审计日期：2026-08-24
- 唯一基线：PR #213 合并提交 `911593f505b05b01037769f578e21f22d2a1c9af`
- R0 基线事实：NodeContract V3、37 个 `NativeNodeKind`、35 个画布目录项、20 个冻结 compatibility 合同
- R1 结果：新增 4 个完整合同，并将既有 `llm` 提升为完整合同；自研节点总数 41、画布目录项 39、当前 19 个冻结 compatibility 合同；四节点与 `llm` Planner 均关闭
- R1.5 PR1 结果：新增完整合同 `failure_event_entry`；自研节点总数 42、画布目录项 40、compatibility 白名单不增长；Planner 关闭且 Xpert 内嵌入口禁止
- R1.5 PR2 结果：新增完整合同 `workflow_call_entry` 与 `invoke_workflow`；自研节点总数 44、画布目录项 42、compatibility 白名单不增长；仅支持私有同步固定版本调用，Planner 关闭且 Xpert 内嵌入口禁止
- R1.6 结果：新增完整合同 `terminate_error`、`multi_route`、`data_aggregate`，并将 `list_operation` 提升为完整合同；自研节点总数 47、画布目录项 45、当前 18 个冻结 compatibility 合同；四类均允许经典工作流和 Xpert 使用，Planner 关闭
- R1.7 结果：新增完整合同 `dataset_compare`，并将 `http_request`、`condition` 提升为完整合同；自研节点总数 48、画布目录项 46、当前 16 个冻结 compatibility 合同；Planner 仍固定为 7 类
- R1.8 结果：新增完整合同 `file_output`、`object_transform`，并将 `document_extractor`、`time_tool` 提升为完整合同，同时扩展 `list_operation`；自研节点总数 50、画布目录项 48、当前 14 个冻结 compatibility 合同；文件节点仅允许经典工作流和私有 Xpert，Planner 仍固定为 7 类
- R1.9 结果：不新增普通节点，将 `parameter_extractor`、`question_classifier` 提升为完整 V2 合同，并在既有 `runtime_middleware` 下增加 `content_policy` 文本策略；自研节点总数 50、画布目录项 48、当前 12 个冻结 compatibility 合同，Planner 仍固定为 7 类
- R2.0 结果：不新增普通节点，将 `human_intervention`、`mcp_tool`、`variable_assign` 提升为完整 V2 合同，并退役旧知识引用新增入口；当前 50 Native、48 个可新增 Palette 项、41 个完整合同、9 个 compatibility 合同、7 个 Planner 节点
- R2.1 PR1 结果：不新增 `NativeNodeKind`，将 `code` 提升为只执行预定义操作的“安全文本加工 V2”完整合同，并从 Palette 移除退役 `template_transform`；旧草稿和既有激活版本继续兼容，模板文本能力由 `variable_assign` V2 承接；当时 50 Native、47 个可新增 Palette 项、42 个完整合同、8 个 compatibility 合同、7 个 Planner 节点
- R2.1 PR2 结果：新增完整合同 `data_merge`，并将经典运行器升级为带持久化边到达账本的 Scheduler V2；支持可靠 Fan-in、有界数组拼接和受限一对一 inner join；当时 51 Native、48 个可新增 Palette 项、43 个完整合同、8 个 compatibility 合同、7 个 Planner 节点
- R2.2 PR1 结果：将 `variable_aggregator` 提升为“变量打包”V2 完整合同，修正元智能体新图的报告汇总，并为 563 行参考清单增加 exact/limited/composable/none 证据门禁；当前 {native_count} Native、{palette_count} 个可新增 Palette 项、{complete_count} 个完整合同、{compatibility_count} 个 compatibility 合同、{planner_count} 个 Planner 节点
- R2.2 PR2 结果：将 `agent_task`、`agent_handoff`、`handoff_router` 提升为类型化 V2 合同，新增 occurrence 幂等索引、原子 Router 与持久 Handoff 恢复，并退役旧 `agent` 新增入口；当前 {native_count} Native、{palette_count} 个可新增 Palette 项、{complete_count} 个完整合同、{compatibility_count} 个 compatibility 合同、{planner_count} 个 Planner 节点
- 参考清单：563 条节点名称/类型，其中 `.ee` {ee_count} 条仅保留名称审计

## 结论与许可证边界

本表只把节点名称和粗粒度能力类型作为事实输入，最终分类使用模镜自己的能力域、节点名、合同和运行语义。括号列仅保留参考原名。未复制或改写 n8n 代码、参数 Schema、文案、图标、测试或 UI；`.ee` 条目排除实现参考。此工程边界降低但不能替代正式法律意见。

R1 为单实例、原子文件持久化版本，不宣称多 Worker、HA 或多租户就绪。私有 HTTP 原始入站载荷不进入触发记录或运行事件；进入 timer continuation 前，事件和正文变量会替换为大小、哈希与“恢复后不可用”标记。无同步回执的 HTTP 链路可先返回 202 再持久挂起；HTTP 回执上游仍禁止挂起，HTTP 发布版本仍禁止运行时中间件和其他交互式 continuation。为支持幂等重复返回，用户显式配置的回执正文会作为回执保存，因此回显入站数据属于用户可见的持久化选择。

## 状态汇总

- 已实现：{statuses['已实现']}
- 部分实现：{statuses['部分实现']}
- 通用节点可覆盖：{statuses['通用覆盖']}（不等于已有专用连接器）
- 目录声明：{statuses['目录声明']}
- 未实现：{statuses['未实现']}

覆盖等级用于表达证据强度：`exact` 只允许完整 NodeContract 且必须绑定运行/测试证据；`limited` 必须写明语义缺口；`composable` 只表示受控通用组合路径，不代表专用连接器；`none` 表示没有运行合同。

| 能力域 | 总数 | 已实现 | 部分实现 | 通用覆盖 | 目录声明 | 未实现 |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(domain_lines)}

## 本轮直接闭环

| 模镜能力域 | 模镜自主节点名 | 内部 ID | 原名仅供参考 | 当前状态 |
|---|---|---|---|---|
{chr(10).join(direct_lines)}

完整逐条对照见 [n8n-node-capability-matrix.csv](./n8n-node-capability-matrix.csv)。

## 门禁

- `/api/workflow/node-registry` 是新增节点的唯一权威目录；Registry 故障时本地目录全部只读。
- 前端 `WorkflowNodeKind`、后端 `NativeNodeKind`、NodeContract Registry 必须完全一致。
- Palette 必须是 NodeContract 合法子集；每个启用项必须有默认数据和配置入口。
- compatibility 合同不得超过 #213 冻结白名单；新节点必须直接提供完整合同。
- Planner 只接受完整合同、匹配 checksum 且显式启用的节点；R1–R2.2 增量节点均禁止 Planner 自动生成，Planner 可生成类型仍固定为 {planner_count} 类。
"""
    (args.output_dir / "N8N_NODE_CAPABILITY_MATRIX.md").write_text(
        markdown,
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
