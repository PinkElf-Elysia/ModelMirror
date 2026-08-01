from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


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
    planner_enabled: bool = True
    planner_default_data: dict[str, Any] = field(default_factory=dict)
    planner_config_constraints: dict[str, Any] = field(default_factory=dict)

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
            "planner": {
                "enabled": self.enabled and self.planner_enabled,
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
        self.version = "xpert-workflow-node-registry-v1"
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
                    kind="knowledge_retrieval",
                    icon="RAG",
                    title="知识检索",
                    description="查询本地 RAG 资料库，把相关段落写入变量。",
                    category="transform",
                    tags=["rag", "knowledge"],
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
                    kind="document_extractor",
                    icon="DOC",
                    title="文档提取器",
                    description="从受限本地路径提取文本，供后续节点使用。",
                    category="transform",
                    tags=["document", "file"],
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
            placeholders=[
                WorkflowPalettePlaceholder(
                    id="json_serialize",
                    icon="JSON",
                    title="JSON 序列化",
                    description="把变量对象序列化为 JSON。",
                    category="transform",
                    tags=["json", "serialize"],
                ),
                WorkflowPalettePlaceholder(
                    id="json_deserialize",
                    icon="JSON",
                    title="JSON 反序列化",
                    description="把 JSON 字符串解析为结构化变量。",
                    category="transform",
                    tags=["json", "deserialize"],
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
                    kind="knowledge_base",
                    icon="KB",
                    title="知识库",
                    description="Bind one knowledge base to the agent's read-only knowledge tools.",
                    category="resource",
                    tags=["knowledge", "rag", "resource", "binding"],
                    planner_default_data={"topK": "5", "scoreThreshold": "0"},
                    planner_config_constraints={
                        "required": ["knowledgeBaseId"],
                        "target_handle": "knowledge",
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
            description="数据库、长期记忆和写入能力。",
            placeholders=[
                WorkflowPalettePlaceholder(
                    id="database_memory",
                    icon="DB",
                    title="数据库",
                    description="读写结构化数据和长期记忆。",
                    category="memory",
                    tags=["database", "memory"],
                )
            ],
        )
    )

    registry.register_section(
        WorkflowPaletteSection(
            id="other",
            tab="workflow",
            label="其他",
            description="画布辅助节点。",
            placeholders=[
                WorkflowPalettePlaceholder(
                    id="annotation",
                    icon="NOTE",
                    title="注释",
                    description="仅用于画布说明，不参与运行。",
                    category="other",
                    tags=["note", "annotation"],
                )
            ],
        )
    )

    registry.set_knowledge_pipeline(
        KnowledgePipelinePalette(
            items=[
                WorkflowPaletteItem(
                    kind="knowledge_citation",
                    icon="CITE",
                    title="知识引用锚点",
                    description="查询本地 RAG，输出 CitationAnchor JSON。",
                    category="transform",
                    tags=["citation", "rag", "knowledge-pipeline"],
                )
            ],
            placeholders=[
                WorkflowPalettePlaceholder(
                    id="pipeline_source_default",
                    icon="TXT",
                    title="默认数据源",
                    description="从知识流水线数据源读取文件。",
                    category="other",
                    tags=["source", "data"],
                ),
                WorkflowPalettePlaceholder(
                    id="pipeline_processor",
                    icon="PX",
                    title="处理器",
                    description="清洗、解析和转换文档内容。",
                    category="other",
                    tags=["processor"],
                ),
                WorkflowPalettePlaceholder(
                    id="pipeline_splitter",
                    icon="SPLIT",
                    title="分块器",
                    description="递归字符、Markdown 或父子分块。",
                    category="other",
                    tags=["splitter", "chunk"],
                ),
                WorkflowPalettePlaceholder(
                    id="pipeline_vision",
                    icon="VISION",
                    title="图像理解",
                    description="使用视觉语言模型处理图片内容。",
                    category="other",
                    tags=["vision", "image"],
                ),
            ],
        )
    )


workflow_node_registry = WorkflowNodeRegistry()
register_builtin_workflow_nodes(workflow_node_registry)
