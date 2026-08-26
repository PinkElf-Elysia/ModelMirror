from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal

try:
    from server.workflow_native.node_contracts import (
        NODE_CONTRACT_VERSION,
        workflow_node_contract_registry,
    )
except ModuleNotFoundError:
    from workflow_native.node_contracts import (
        NODE_CONTRACT_VERSION,
        workflow_node_contract_registry,
    )


WorkflowNodeRegistryTab = Literal["workflow", "knowledge"]
WorkflowNodeCategory = Literal[
    "logic",
    "transform",
    "resource",
    "tool",
    "memory",
    "other",
]


@dataclass(slots=True)
class WorkflowPaletteTab:
    id: WorkflowNodeRegistryTab
    label: str

    def to_payload(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label}


@dataclass(slots=True)
class WorkflowPaletteItem:
    kind: str
    title: str
    description: str
    icon: str
    category: WorkflowNodeCategory
    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        contract = workflow_node_contract_registry.require(self.kind)
        contract_payload = contract.to_safe_payload()
        input_ports = [
            port for port in contract_payload["ports"] if port["direction"] == "input"
        ]
        output_ports = [
            port for port in contract_payload["ports"] if port["direction"] == "output"
        ]
        return {
            "kind": self.kind,
            "title": self.title,
            "description": self.description,
            "icon": self.icon,
            "category": self.category,
            "tags": list(self.tags),
            "enabled": self.enabled,
            "metadata": dict(self.metadata),
            "contracts": {
                "inputs": input_ports,
                "outputs": output_ports,
                "resources": list(contract_payload["resources"]),
                "deprecated": contract.deprecated,
                "replacement_kind": contract.replacement_kind,
            },
            "planner": {
                "enabled": self.enabled and contract.planner.enabled,
                "support": contract.planner.support,
                "compilation_mode": contract.planner.compilation_mode,
                "ir_version": contract.planner.ir_version,
                "adapter_version": contract.planner.adapter_version,
                "contract_checksum": contract.checksum,
                "compiler_checksum": contract.compiler_checksum,
                "default_data": {
                    "kind": self.kind,
                    "title": self.title,
                    **dict(contract.planner.default_data),
                },
                "config_constraints": dict(contract.planner.config_constraints),
            },
            "contract": contract_payload,
        }


@dataclass(slots=True)
class WorkflowPalettePlaceholder:
    id: str
    title: str
    description: str
    icon: str
    category: WorkflowNodeCategory
    status_label: str = "待接入"
    tags: list[str] = field(default_factory=list)
    enabled: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "icon": self.icon,
            "category": self.category,
            "statusLabel": self.status_label,
            "tags": list(self.tags),
            "enabled": self.enabled,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class WorkflowPaletteSection:
    id: WorkflowNodeCategory
    tab: WorkflowNodeRegistryTab
    label: str
    description: str
    items: list[WorkflowPaletteItem] = field(default_factory=list)
    placeholders: list[WorkflowPalettePlaceholder] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tab": self.tab,
            "label": self.label,
            "description": self.description,
            "items": [item.to_payload() for item in self.items],
            "placeholders": [item.to_payload() for item in self.placeholders],
        }


@dataclass(slots=True)
class KnowledgePipelinePalette:
    items: list[WorkflowPaletteItem] = field(default_factory=list)
    placeholders: list[WorkflowPalettePlaceholder] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "items": [item.to_payload() for item in self.items],
            "placeholders": [item.to_payload() for item in self.placeholders],
        }


class WorkflowNodeRegistry:
    """Xpert-style metadata registry for classic workflow palette nodes."""

    def __init__(self) -> None:
        self.version = "xpert-workflow-node-registry-v4"
        self.contract_registry = workflow_node_contract_registry
        self._tabs: list[WorkflowPaletteTab] = []
        self._sections: list[WorkflowPaletteSection] = []
        self._knowledge_pipeline = KnowledgePipelinePalette()

    def set_tabs(self, tabs: list[WorkflowPaletteTab]) -> None:
        self._tabs = list(tabs)

    def register_section(self, section: WorkflowPaletteSection) -> None:
        self._sections.append(section)

    def set_knowledge_pipeline(self, palette: KnowledgePipelinePalette) -> None:
        self._knowledge_pipeline = palette

    def tabs(self) -> list[WorkflowPaletteTab]:
        return list(self._tabs)

    def sections(self) -> list[WorkflowPaletteSection]:
        return list(self._sections)

    def knowledge_pipeline(self) -> KnowledgePipelinePalette:
        return self._knowledge_pipeline

    def enabled_kinds(self) -> set[str]:
        kinds = {
            item.kind
            for section in self._sections
            for item in section.items
            if item.enabled
        }
        kinds.update(
            item.kind for item in self._knowledge_pipeline.items if item.enabled
        )
        return kinds

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "contract_version": NODE_CONTRACT_VERSION,
            "contract_checksum": self.contract_registry.checksum,
            "tabs": [tab.to_payload() for tab in self._tabs],
            "sections": [section.to_payload() for section in self._sections],
            "knowledge_pipeline": self._knowledge_pipeline.to_payload(),
        }


def register_builtin_workflow_nodes(registry: WorkflowNodeRegistry) -> None:
    """Register classic workflow palette metadata without changing execution."""

    workflow_file_assets_enabled = (
        os.getenv("WORKFLOW_FILE_ASSETS_ENABLED", "false").strip().lower()
        in {"1", "true", "yes", "on"}
        and os.getenv("FILE_ASSET_STORE_MODE", "legacy").strip().lower()
        in {"shadow", "native"}
    )
    file_output_assets_enabled = (
        os.getenv("FILE_OUTPUT_ASSETS_ENABLED", "false").strip().lower()
        in {"1", "true", "yes", "on"}
    )

    registry.set_tabs(
        [
            WorkflowPaletteTab(id="workflow", label="工作流"),
            WorkflowPaletteTab(id="knowledge", label="知识流水线"),
        ]
    )

    registry.register_section(
        WorkflowPaletteSection(
            id="logic",
            tab="workflow",
            label="逻辑",
            description="触发、路由、迭代和变量流转。",
            items=[
                WorkflowPaletteItem(
                    kind="input",
                    icon="IN",
                    title="触发器",
                    description="定义流水线入口变量，默认 user_input。",
                    category="logic",
                    tags=["input", "start", "trigger"],
                ),
                WorkflowPaletteItem(
                    kind="scheduled_start",
                    icon="TIME",
                    title="定时启动",
                    description="从已发布版本按单次、固定间隔或日历规则私有启动。",
                    category="logic",
                    tags=["schedule", "cron", "deployment"],
                    metadata={"classic_only": True, "planner_enabled": False},
                ),
                WorkflowPaletteItem(
                    kind="http_event_entry",
                    icon="POST",
                    title="HTTP 事件入口",
                    description="接收带私有密钥与幂等键的 POST 事件。",
                    category="logic",
                    tags=["webhook", "http", "deployment"],
                    metadata={"classic_only": True, "planner_enabled": False},
                ),
                WorkflowPaletteItem(
                    kind="form_event_entry",
                    icon="FORM",
                    title="表单提交入口",
                    description="发布同源签名表单并接收严格类型化提交。",
                    category="logic",
                    tags=["form", "submission", "deployment"],
                    metadata={"classic_only": True, "planner_enabled": False},
                ),
                WorkflowPaletteItem(
                    kind="failure_event_entry",
                    icon="FAIL",
                    title="失败处置入口",
                    description="监听所选已发布工作流的失败并接收脱敏事件。",
                    category="logic",
                    tags=["failure", "error", "deployment"],
                    metadata={"classic_only": True, "planner_enabled": False},
                ),
                WorkflowPaletteItem(
                    kind="workflow_call_entry",
                    icon="CALL IN",
                    title="子流程入口",
                    description="声明仅供其他已发布工作流同步调用的私有入口。",
                    category="logic",
                    tags=["workflow", "call", "entry"],
                    metadata={"classic_only": True, "planner_enabled": False},
                ),
                WorkflowPaletteItem(
                    kind="invoke_workflow",
                    icon="CALL",
                    title="调用已发布工作流",
                    description="同步调用已启用的固定工作流版本并接收结果。",
                    category="logic",
                    tags=["workflow", "call", "subworkflow"],
                    metadata={"classic_only": True, "planner_enabled": False},
                ),
                WorkflowPaletteItem(
                    kind="suspend_wait",
                    icon="WAIT",
                    title="挂起等待",
                    description="持久挂起至指定持续时间或带时区时间点。",
                    category="logic",
                    tags=["wait", "timer", "continuation"],
                    metadata={"classic_only": True, "planner_enabled": False},
                ),
                WorkflowPaletteItem(
                    kind="http_event_reply",
                    icon="REPLY",
                    title="HTTP 事件回执",
                    description="以文本或 JSON 终止 HTTP 事件工作流。",
                    category="logic",
                    tags=["webhook", "response", "terminal"],
                    metadata={"classic_only": True, "planner_enabled": False},
                ),
                WorkflowPaletteItem(
                    kind="condition",
                    icon="IF",
                    title="路由",
                    description="按变量值判断，走“是/否”两条传送带。",
                    category="logic",
                    tags=["condition", "branch", "route"],
                ),
                WorkflowPaletteItem(
                    kind="multi_route",
                    icon="ROUTE",
                    title="多路分派",
                    description="按顺序匹配 2 至 8 条类型化规则，并提供默认出口。",
                    category="logic",
                    tags=["switch", "route", "typed-value"],
                    metadata={"planner_enabled": False},
                ),
                WorkflowPaletteItem(
                    kind="terminate_error",
                    icon="STOP",
                    title="主动终止",
                    description="使用固定安全错误码和消息终止当前执行。",
                    category="logic",
                    tags=["stop", "error", "terminal"],
                    metadata={"planner_enabled": False},
                ),
                WorkflowPaletteItem(
                    kind="iteration",
                    icon="LOOP",
                    title="批量处理",
                    description="逐项渲染安全模板，或按顺序调用固定版本子流程。",
                    category="logic",
                    tags=["loop", "iteration"],
                ),
                WorkflowPaletteItem(
                    kind="list_operation",
                    icon="LIST",
                    title="列表操作",
                    description="对数组做筛选、排序、去重、截取和跳过，并保留旧列表操作。",
                    category="logic",
                    tags=["list", "filter", "sort", "deduplicate", "typed-value"],
                    metadata={"planner_enabled": False},
                ),
                WorkflowPaletteItem(
                    kind="variable_aggregator",
                    icon="AGG",
                    title="变量打包",
                    description="把多个类型化变量深复制到一个 JSON 对象。",
                    category="logic",
                    tags=["pack", "variables", "typed-value"],
                    metadata={"planner_enabled": False},
                ),
                WorkflowPaletteItem(
                    kind="variable_assign",
                    icon="=",
                    title="变量赋值",
                    description="把模板渲染成一个变量，适合整理中间结果。",
                    category="logic",
                    tags=["assign", "variables"],
                ),
            ],
        )
    )

    registry.register_section(
        WorkflowPaletteSection(
            id="transform",
            tab="workflow",
            label="转换",
            description="分类、检索、模板、代码和最终回答。",
            items=[
                WorkflowPaletteItem(
                    kind="question_classifier",
                    icon="CLS",
                    title="问题分类器",
                    description="按稳定分类出口分派问题，可选择规则、模型或规则后模型。",
                    category="transform",
                    tags=["classifier", "question", "model", "stable-route"],
                ),
                WorkflowPaletteItem(
                    kind="code",
                    icon="TXT",
                    title="安全文本加工",
                    description="执行确定性的大写、小写、替换或拼接操作。",
                    category="transform",
                    tags=["text", "transform", "safe"],
                ),
                WorkflowPaletteItem(
                    kind="parameter_extractor",
                    icon="{}",
                    title="参数提取器",
                    description="调用模型提取字段，输出经 Schema 校验的 JSON 对象或对象列表。",
                    category="transform",
                    tags=["json", "extract", "schema", "typed-value"],
                ),
                WorkflowPaletteItem(
                    kind="data_aggregate",
                    icon="AGG",
                    title="数据聚合",
                    description="按顶层字段分组，并计算计数、求和、均值和极值。",
                    category="transform",
                    tags=["aggregate", "group", "measure", "typed-value"],
                    metadata={"planner_enabled": False},
                ),
                WorkflowPaletteItem(
                    kind="data_merge",
                    icon="MERGE",
                    title="数据合流",
                    description="等待左右路径到达，再拼接数组或按复合键一对一合并。",
                    category="transform",
                    tags=["merge", "fan-in", "join", "typed-value"],
                    metadata={"planner_enabled": False},
                ),
                WorkflowPaletteItem(
                    kind="dataset_compare",
                    icon="DIFF",
                    title="数据集对照",
                    description="按稳定复合键对照两份对象数组，识别新增、删除、变化和未变化项。",
                    category="transform",
                    tags=["dataset", "compare", "diff", "typed-value"],
                    metadata={"planner_enabled": False},
                ),
                WorkflowPaletteItem(
                    kind="object_transform",
                    icon="OBJ",
                    title="对象转换",
                    description="按顺序设置默认值、重命名、删除或保留 JSON 对象的顶层字段。",
                    category="transform",
                    tags=["object", "set", "rename", "typed-value"],
                    metadata={"planner_enabled": False},
                ),
                WorkflowPaletteItem(
                    kind="json_serialize",
                    icon="JSON",
                    title="JSON 序列化",
                    description="将一个类型化工作流变量转换为 JSON 字符串。",
                    category="transform",
                    tags=["json", "serialize", "typed-value"],
                ),
                WorkflowPaletteItem(
                    kind="json_deserialize",
                    icon="JSON",
                    title="JSON 反序列化",
                    description="将 JSON 字符串解析为真实的类型化工作流变量。",
                    category="transform",
                    tags=["json", "deserialize", "typed-value"],
                ),
                WorkflowPaletteItem(
                    kind="document_extractor",
                    icon="DOC",
                    title="内容解析",
                    description="把安全 HTTP 响应或明确共享的文件解析为结构化内容。",
                    category="transform",
                    tags=["content", "html", "markdown", "xml", "file"],
                    enabled=True,
                    metadata={
                        "planner_enabled": False,
                        "file_asset_mode_enabled": workflow_file_assets_enabled,
                        **(
                            {}
                            if workflow_file_assets_enabled
                            else {
                                "file_asset_mode_reason": "Workflow 文件资产变量当前未启用。"
                            }
                        ),
                    },
                ),
                WorkflowPaletteItem(
                    kind="file_output",
                    icon="FILE",
                    title="生成文件",
                    description="把类型化变量安全生成 TXT、Markdown、JSON、CSV、PDF、DOCX 或 XLSX 文件。",
                    category="transform",
                    tags=["file", "export", "document", "spreadsheet"],
                    enabled=file_output_assets_enabled,
                    metadata=(
                        {"private_only": True, "planner_enabled": False}
                        if file_output_assets_enabled
                        else {
                            "private_only": True,
                            "planner_enabled": False,
                            "status_reason": "统一文件输出当前未启用。",
                        }
                    ),
                ),
                WorkflowPaletteItem(
                    kind="llm",
                    icon="LLM",
                    title="LLM 节点",
                    description="安排模型工位处理提示词，可引用 {{变量}}。",
                    category="transform",
                    tags=["model", "llm"],
                ),
                WorkflowPaletteItem(
                    kind="output",
                    icon="OUT",
                    title="回答",
                    description="收尾交付最终变量，展示运行结果。",
                    category="transform",
                    tags=["output", "answer"],
                ),
            ],
        )
    )

    registry.register_section(
        WorkflowPaletteSection(
            id="resource",
            tab="workflow",
            label="资源",
            description="Bind published Xperts and active knowledge bases to one workflow agent.",
            items=[
                WorkflowPaletteItem(
                    kind="external_xpert",
                    icon="XP",
                    title="外部 Xpert",
                    description="Expose a pinned published Xpert as a synchronous collaborator tool.",
                    category="resource",
                    tags=["xpert", "expert", "resource", "binding"],
                ),
                WorkflowPaletteItem(
                    kind="toolset_resource",
                    icon="TS",
                    title="MCP Toolset",
                    description=(
                        "Bind one immutable published MCP Toolset version to a "
                        "workflow agent."
                    ),
                    category="resource",
                    tags=["mcp", "toolset", "resource", "binding"],
                ),
                WorkflowPaletteItem(
                    kind="plugin_resource",
                    icon="PLG",
                    title="Plugin",
                    description=(
                        "Bind one immutable declarative Plugin version with its "
                        "Prompt, Skill, Toolset, and middleware resources."
                    ),
                    category="resource",
                    tags=["plugin", "prompt", "skill", "toolset", "binding"],
                ),
            ],
        )
    )

    registry.register_section(
        WorkflowPaletteSection(
            id="tool",
            tab="workflow",
            label="工具",
            description="HTTP、MCP、智能体步骤和任务移交。",
            items=[
                WorkflowPaletteItem(
                    kind="http_request",
                    icon="HTTP",
                    title="安全 HTTP 请求",
                    description="以固定公网目标、加密凭据和结构化参数调用 HTTP 接口。",
                    category="tool",
                    tags=["http", "api", "credential", "public-only"],
                    metadata={"planner_enabled": False},
                ),
                WorkflowPaletteItem(
                    kind="mcp_tool",
                    icon="MCP",
                    title="工具调用",
                    description="调用已连接 MCP Server 暴露的工具。",
                    category="tool",
                    tags=["mcp", "tool"],
                ),
                WorkflowPaletteItem(
                    kind="workflow_agent",
                    icon="WA",
                    title="工作流智能体",
                    description="执行一个模型驱动的 Agent 步骤，并写入输出变量。",
                    category="tool",
                    tags=["workflow-agent", "agent"],
                ),
                WorkflowPaletteItem(
                    kind="agent_task",
                    icon="TASK",
                    title="创建协作任务",
                    description="登记可追踪任务并输出类型化任务凭证。",
                    category="tool",
                    tags=["task", "agent-task"],
                ),
                WorkflowPaletteItem(
                    kind="agent_handoff",
                    icon="HAND",
                    title="移交已有任务",
                    description="把上游任务凭证交给人工队列或固定版本智能体。",
                    category="tool",
                    tags=["handoff", "agent"],
                ),
                WorkflowPaletteItem(
                    kind="handoff_router",
                    icon="ROUTE",
                    title="创建并移交任务",
                    description="原子创建任务并交给人工队列或固定版本智能体。",
                    category="tool",
                    tags=["handoff", "router"],
                ),
                WorkflowPaletteItem(
                    kind="time_tool",
                    icon="TIME",
                    title="时间工具",
                    description="按 IANA 时区获取、格式化、增减、对照和归整日期时间。",
                    category="tool",
                    tags=["time", "date"],
                ),
                WorkflowPaletteItem(
                    kind="human_intervention",
                    icon="HITL",
                    title="人工介入",
                    description="暂停流水线，等待用户补充文本后再继续执行。",
                    category="tool",
                    tags=["human", "approval"],
                ),
            ],
        )
    )

    registry.register_section(
        WorkflowPaletteSection(
            id="memory",
            tab="workflow",
            label="记忆",
            description="本地托管 Agent Table 的类型化查询与写入能力。",
            items=[
                WorkflowPaletteItem(
                    kind="data_table_query",
                    icon="DB?",
                    title="查询数据表",
                    description="按字段、条件和排序读取固定 Schema 的 Agent Table 记录。",
                    category="memory",
                    tags=["database", "agent-table", "query", "typed-value"],
                    metadata={"private_only": True, "side_effect": "read"},
                ),
                WorkflowPaletteItem(
                    kind="data_table_insert",
                    icon="DB+",
                    title="新增数据",
                    description="按类型化字段绑定向 Agent Table 插入一条记录。",
                    category="memory",
                    tags=["database", "agent-table", "insert", "typed-value"],
                    metadata={"private_only": True, "side_effect": "write"},
                ),
                WorkflowPaletteItem(
                    kind="data_table_update",
                    icon="DB~",
                    title="更新数据",
                    description="使用非空条件批量更新 Agent Table，单次最多 100 行。",
                    category="memory",
                    tags=["database", "agent-table", "update", "typed-value"],
                    metadata={"private_only": True, "side_effect": "write"},
                ),
                WorkflowPaletteItem(
                    kind="data_table_delete",
                    icon="DB-",
                    title="删除数据",
                    description="使用非空条件删除 Agent Table 记录，单次最多 100 行。",
                    category="memory",
                    tags=["database", "agent-table", "delete", "typed-value"],
                    metadata={"private_only": True, "side_effect": "write"},
                ),
            ],
        )
    )

    registry.register_section(
        WorkflowPaletteSection(
            id="other",
            tab="workflow",
            label="其他",
            description="画布辅助节点。",
            items=[
                WorkflowPaletteItem(
                    kind="annotation",
                    icon="NOTE",
                    title="注释",
                    description="仅保存画布说明，不进入拓扑、执行或运行记录。",
                    category="other",
                    tags=["note", "annotation", "canvas"],
                    metadata={
                        "ports": [],
                        "runtime": "ignored",
                        "max_content_length": 20_000,
                    },
                )
            ],
        )
    )

    registry.set_knowledge_pipeline(
        KnowledgePipelinePalette(
            items=[
                WorkflowPaletteItem(
                    kind="knowledge_base",
                    icon="KB",
                    title="知识库",
                    description="将一个知识库绑定到工作流智能体的只读知识工具。",
                    category="resource",
                    tags=["knowledge", "rag", "resource", "binding"],
                ),
                WorkflowPaletteItem(
                    kind="knowledge_retrieval",
                    icon="RAG",
                    title="知识检索",
                    description="检索指定知识库的活动版本并输出文本或类型化结果。",
                    category="transform",
                    tags=["rag", "knowledge", "retrieval"],
                ),
                WorkflowPaletteItem(
                    kind="vision_understanding",
                    icon="VISION",
                    title="视觉理解",
                    description=(
                        "读取当前私有运行显式共享的图片或扫描 PDF，输出 OCR、"
                        "视觉描述、表格和图表等类型化结果。"
                    ),
                    category="knowledge-pipeline",
                    tags=["vision", "ocr", "image", "pdf", "typed-value"],
                    metadata={
                        "private_only": True,
                        "supported_formats": ["png", "jpeg", "webp", "pdf"],
                        "side_effect": "external_model_read",
                    },
                ),
            ],
            placeholders=[],
        )
    )


workflow_node_registry = WorkflowNodeRegistry()
register_builtin_workflow_nodes(workflow_node_registry)
