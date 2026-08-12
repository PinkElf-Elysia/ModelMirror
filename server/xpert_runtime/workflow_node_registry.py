from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal


WorkflowNodeRegistryTab = Literal["workflow", "knowledge"]
WorkflowPlannerSupport = Literal[
    "full",
    "binding_only",
    "metadata_only",
    "unsupported",
]
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
    planner_enabled: bool = True
    planner_support: WorkflowPlannerSupport = "full"
    planner_default_data: dict[str, Any] = field(default_factory=dict)
    planner_config_constraints: dict[str, Any] = field(default_factory=dict)
    input_contract: dict[str, Any] = field(default_factory=dict)
    output_contract: dict[str, Any] = field(default_factory=dict)
    resource_requirements: list[str] = field(default_factory=list)
    deprecated: bool = False
    replacement_kind: str | None = None

    def to_payload(self) -> dict[str, Any]:
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
                "inputs": dict(self.input_contract),
                "outputs": dict(self.output_contract),
                "resources": list(self.resource_requirements),
                "deprecated": self.deprecated,
                "replacement_kind": self.replacement_kind,
            },
            "planner": {
                "enabled": self.enabled and self.planner_enabled,
                "support": self.planner_support,
                "default_data": {
                    "kind": self.kind,
                    "title": self.title,
                    **dict(self.planner_default_data),
                },
                "config_constraints": dict(self.planner_config_constraints),
            },
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
        self.version = "xpert-workflow-node-registry-v2"
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
                    kind="condition",
                    icon="IF",
                    title="路由",
                    description="按变量值判断，走“是/否”两条传送带。",
                    category="logic",
                    tags=["condition", "branch", "route"],
                ),
                WorkflowPaletteItem(
                    kind="iteration",
                    icon="LOOP",
                    title="迭代",
                    description="逐项渲染模板，汇总为一个 JSON 数组字符串。",
                    category="logic",
                    tags=["loop", "iteration"],
                ),
                WorkflowPaletteItem(
                    kind="list_operation",
                    icon="LIST",
                    title="列表操作",
                    description="对逗号分隔的列表做长度、拼接、首尾提取。",
                    category="logic",
                    tags=["list", "operator"],
                ),
                WorkflowPaletteItem(
                    kind="variable_aggregator",
                    icon="AGG",
                    title="变量聚合",
                    description="把多个变量汇总为文本或 JSON 字符串。",
                    category="logic",
                    tags=["aggregate", "variables"],
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
                    description="根据关键词规则把输入文本分类为预设类别。",
                    category="transform",
                    tags=["classifier", "question"],
                ),
                WorkflowPaletteItem(
                    kind="code",
                    icon="</>",
                    title="代码执行",
                    description="支持受限的字符串处理和 Python 执行能力。",
                    category="transform",
                    tags=["code", "python", "transform"],
                ),
                WorkflowPaletteItem(
                    kind="template_transform",
                    icon="T",
                    title="模板",
                    description="渲染长文本模板，适合生成报告或结构化草稿。",
                    category="transform",
                    tags=["template", "text"],
                ),
                WorkflowPaletteItem(
                    kind="parameter_extractor",
                    icon="{}",
                    title="参数提取器",
                    description="调用模型从文本中提取字段，输出 JSON 字符串。",
                    category="transform",
                    tags=["json", "extract"],
                ),
                WorkflowPaletteItem(
                    kind="json_serialize",
                    icon="JSON",
                    title="JSON 序列化",
                    description="将一个类型化工作流变量转换为 JSON 字符串。",
                    category="transform",
                    tags=["json", "serialize", "typed-value"],
                    planner_default_data={
                        "inputVariable": "json_value",
                        "outputVariable": "json_text",
                        "format": "compact",
                    },
                    planner_config_constraints={
                        "required": ["inputVariable", "outputVariable"],
                        "format": ["compact", "pretty"],
                    },
                    planner_enabled=False,
                ),
                WorkflowPaletteItem(
                    kind="json_deserialize",
                    icon="JSON",
                    title="JSON 反序列化",
                    description="将 JSON 字符串解析为真实的类型化工作流变量。",
                    category="transform",
                    tags=["json", "deserialize", "typed-value"],
                    planner_default_data={
                        "inputVariable": "json_text",
                        "outputVariable": "json_value",
                    },
                    planner_config_constraints={
                        "required": ["inputVariable", "outputVariable"],
                    },
                    planner_enabled=False,
                ),
                WorkflowPaletteItem(
                    kind="document_extractor",
                    icon="DOC",
                    title="文档提取器",
                    description="从当前工作流作用域的文件资产提取文本。",
                    category="transform",
                    tags=["document", "file"],
                    enabled=workflow_file_assets_enabled,
                    metadata=(
                        {}
                        if workflow_file_assets_enabled
                        else {
                            "status_reason": "Workflow 文件资产变量当前未启用。"
                        }
                    ),
                    planner_default_data={
                        "assetIdVariable": "document_asset_id",
                        "outputVariable": "document_text",
                    },
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
                    planner_default_data={
                        "versionPolicy": "pinned",
                        "pinnedVersion": None,
                    },
                    planner_config_constraints={
                        "required": ["xpertId", "toolName"],
                        "target_handle": "expert",
                    },
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
                    planner_default_data={
                        "versionPolicy": "pinned",
                        "pinnedVersion": None,
                    },
                    planner_config_constraints={
                        "required": ["toolsetId"],
                        "target_handle": "toolset",
                    },
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
                    planner_default_data={
                        "versionPolicy": "pinned",
                        "pinnedVersion": None,
                    },
                    planner_config_constraints={
                        "required": ["pluginId"],
                        "target_handle": "plugin",
                    },
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
                    title="HTTP",
                    description="调用 GET/POST 接口，把响应文本写入变量。",
                    category="tool",
                    tags=["http", "api"],
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
                    kind="agent",
                    icon="A",
                    title="Agent 节点",
                    description="模型驱动的任务执行节点，支持工具循环和直接回答。",
                    category="tool",
                    tags=["agent", "toolset"],
                ),
                WorkflowPaletteItem(
                    kind="workflow_agent",
                    icon="WA",
                    title="工作流智能体",
                    description="执行一个模型驱动的 Agent 步骤，并写入输出变量。",
                    category="tool",
                    tags=["workflow-agent", "agent"],
                    planner_default_data={
                        "toolMode": "none",
                        "maxIterations": "6",
                        "outputVariable": "agent_output",
                    },
                    planner_config_constraints={
                        "required": [
                            "modelId",
                            "rolePrompt",
                            "taskInput",
                            "outputVariable",
                        ],
                    },
                ),
                WorkflowPaletteItem(
                    kind="agent_task",
                    icon="TASK",
                    title="智能体任务",
                    description="创建 Agent Task Runtime 任务，输出 task_id。",
                    category="tool",
                    tags=["task", "agent-task"],
                ),
                WorkflowPaletteItem(
                    kind="agent_handoff",
                    icon="HAND",
                    title="任务移交",
                    description="把 Agent Task 显式移交给另一个智能体。",
                    category="tool",
                    tags=["handoff", "agent"],
                ),
                WorkflowPaletteItem(
                    kind="handoff_router",
                    icon="ROUTE",
                    title="移交路由器",
                    description="读取智能体输出，投递到目标 Agent 的 Handoff Inbox。",
                    category="tool",
                    tags=["handoff", "router"],
                ),
                WorkflowPaletteItem(
                    kind="time_tool",
                    icon="TIME",
                    title="时间工具",
                    description="获取当前时间、时间戳或格式化日期文本。",
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
                    planner_default_data={
                        "versionPolicy": "latest",
                        "selectFields": [],
                        "filter": None,
                        "sort": [],
                        "limit": 20,
                        "returnMode": "list",
                        "outputVariable": "table_records",
                    },
                    planner_config_constraints={
                        "required": ["tableId", "outputVariable"],
                        "versionPolicy": ["latest", "pinned"],
                        "returnMode": ["list", "first"],
                        "limit": {"minimum": 1, "maximum": 200},
                    },
                    planner_enabled=False,
                    metadata={"private_only": True, "side_effect": "read"},
                ),
                WorkflowPaletteItem(
                    kind="data_table_insert",
                    icon="DB+",
                    title="新增数据",
                    description="按类型化字段绑定向 Agent Table 插入一条记录。",
                    category="memory",
                    tags=["database", "agent-table", "insert", "typed-value"],
                    planner_default_data={
                        "versionPolicy": "latest",
                        "valueBindings": {},
                        "outputVariable": "inserted_record",
                    },
                    planner_config_constraints={
                        "required": ["tableId", "valueBindings", "outputVariable"],
                        "versionPolicy": ["latest", "pinned"],
                    },
                    planner_enabled=False,
                    metadata={"private_only": True, "side_effect": "write"},
                ),
                WorkflowPaletteItem(
                    kind="data_table_update",
                    icon="DB~",
                    title="更新数据",
                    description="使用非空条件批量更新 Agent Table，单次最多 100 行。",
                    category="memory",
                    tags=["database", "agent-table", "update", "typed-value"],
                    planner_default_data={
                        "versionPolicy": "latest",
                        "filter": None,
                        "valueBindings": {},
                        "outputVariable": "update_result",
                    },
                    planner_config_constraints={
                        "required": ["tableId", "filter", "valueBindings", "outputVariable"],
                        "versionPolicy": ["latest", "pinned"],
                        "maxAffectedRows": 100,
                    },
                    planner_enabled=False,
                    metadata={"private_only": True, "side_effect": "write"},
                ),
                WorkflowPaletteItem(
                    kind="data_table_delete",
                    icon="DB-",
                    title="删除数据",
                    description="使用非空条件删除 Agent Table 记录，单次最多 100 行。",
                    category="memory",
                    tags=["database", "agent-table", "delete", "typed-value"],
                    planner_default_data={
                        "versionPolicy": "latest",
                        "filter": None,
                        "outputVariable": "delete_result",
                    },
                    planner_config_constraints={
                        "required": ["tableId", "filter", "outputVariable"],
                        "versionPolicy": ["latest", "pinned"],
                        "maxAffectedRows": 100,
                    },
                    planner_enabled=False,
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
                    planner_default_data={"content": ""},
                    planner_enabled=False,
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
                    planner_support="binding_only",
                    planner_default_data={"topK": "5", "scoreThreshold": "0"},
                    planner_config_constraints={
                        "required": ["knowledgeBaseId"],
                        "target_handle": "knowledge",
                    },
                    input_contract={"binding": "knowledge_base"},
                    output_contract={"control_flow": False},
                    resource_requirements=["knowledge_base"],
                ),
                WorkflowPaletteItem(
                    kind="knowledge_retrieval",
                    icon="RAG",
                    title="知识检索",
                    description="检索指定知识库的活动版本并输出文本或类型化结果。",
                    category="transform",
                    tags=["rag", "knowledge", "retrieval"],
                    planner_enabled=False,
                    planner_support="unsupported",
                    planner_default_data={
                        "contractVersion": 2,
                        "knowledgeBaseId": "",
                        "queryVariable": "user_input",
                        "top_k": "5",
                        "returnMode": "result",
                        "outputVariable": "knowledge_result",
                    },
                    planner_config_constraints={
                        "required": [
                            "knowledgeBaseId",
                            "queryVariable",
                            "outputVariable",
                        ],
                        "return_mode": ["context", "result"],
                    },
                    input_contract={
                        "queryVariable": "string",
                        "knowledgeBaseId": "resource:knowledge_base",
                    },
                    output_contract={
                        "context": "string",
                        "result": "object",
                    },
                    resource_requirements=["knowledge_base"],
                ),
            ],
            placeholders=[],
        )
    )


workflow_node_registry = WorkflowNodeRegistry()
register_builtin_workflow_nodes(workflow_node_registry)
