from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


FieldType = Literal["text", "textarea", "select", "boolean", "number", "json"]


@dataclass(slots=True)
class RuntimeMiddlewareField:
    """A canvas-renderable field definition for runtime middleware nodes."""

    name: str
    label: str
    type: FieldType
    required: bool = False
    default: Any | None = None
    options: list[str] | None = None
    placeholder: str | None = None
    description: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    rows: int | None = None


@dataclass(slots=True)
class RuntimeMiddlewareNode:
    """Metadata for one Xpert-style runtime middleware palette node."""

    id: str
    kind: str
    title: str
    description: str
    category: str
    icon: str
    fields: list[RuntimeMiddlewareField] = field(default_factory=list)
    enabled: bool = True
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    config_version: int = 1
    execution_status: str = "real"
    requires_tool_mode: str | None = None
    app_policy: str = "allowed"
    security_category: str = "general"


class RuntimeMiddlewareRegistry:
    """In-memory registry of middleware nodes available to the canvas palette."""

    def __init__(self) -> None:
        self._nodes: dict[str, RuntimeMiddlewareNode] = {}

    def register(self, node: RuntimeMiddlewareNode) -> None:
        self._nodes[node.id] = node

    def list(self) -> list[RuntimeMiddlewareNode]:
        return list(self._nodes.values())

    def get(self, node_id: str) -> RuntimeMiddlewareNode | None:
        return self._nodes.get(node_id)

    def categories(self) -> list[str]:
        return sorted({node.category for node in self._nodes.values()})

    def by_category(self) -> dict[str, list[RuntimeMiddlewareNode]]:
        grouped: dict[str, list[RuntimeMiddlewareNode]] = {}
        for node in self._nodes.values():
            grouped.setdefault(node.category, []).append(node)
        return grouped

    def app_forbidden_ids(self) -> set[str]:
        return {
            node.id
            for node in self._nodes.values()
            if node.app_policy == "forbidden"
            or bool(node.metadata.get("app_forbidden"))
        }


def register_builtin_middleware_nodes(
    registry: RuntimeMiddlewareRegistry,
) -> None:
    """Register ModelMirror's first built-in runtime middleware node metadata."""

    registry.register(
        RuntimeMiddlewareNode(
            id="system_prompt_injector",
            kind="runtime_middleware.system_prompt_injector",
            title="系统提示词注入器",
            description="在模型调用前自动注入系统提示词，支持覆盖或追加模式。",
            category="agent",
            icon="MessageSquare",
            fields=[
                RuntimeMiddlewareField(
                    name="system_prompt",
                    label="系统提示词",
                    type="textarea",
                    required=True,
                    rows=4,
                    placeholder="你是一个专业的...",
                ),
                RuntimeMiddlewareField(
                    name="override",
                    label="覆盖已有系统提示词",
                    type="boolean",
                    default=False,
                    description="开启后替换消息列表首条 system message。",
                ),
            ],
            tags=["agent", "prompt", "before_model"],
            metadata={
                "middleware_name": "system_prompt_injector",
                "runtime_hook": "before_model",
            },
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="xpert_authoring",
            kind="runtime_middleware.xpert_authoring",
            title="Xpert 自编写",
            description="读取授权草稿并创建版本化 Xpert 提案；批准后只写草稿，不自动发布。",
            category="agent",
            icon="Bot",
            fields=[
                RuntimeMiddlewareField(
                    name="allow_create",
                    label="允许创建提案",
                    type="boolean",
                    default=True,
                ),
                RuntimeMiddlewareField(
                    name="allow_update",
                    label="允许更新提案",
                    type="boolean",
                    default=True,
                ),
                RuntimeMiddlewareField(
                    name="allowed_xpert_ids",
                    label="允许读取/修改的 Xpert ID",
                    type="textarea",
                    rows=4,
                    description="逗号或换行分隔；当前 Xpert 始终可访问自身草稿。",
                ),
            ],
            tags=["agent", "authoring", "xpert", "proposal", "approval"],
            metadata={
                "middleware_name": "xpert_authoring",
                "runtime_hook": "agent_tools",
                "capability_name": "xpert_authoring_tools",
                "real_execution": True,
                "app_forbidden": True,
                "proposal_only": True,
            },
            requires_tool_mode="mcp_tools",
            app_policy="forbidden",
            security_category="authoring",
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="skill_creator",
            kind="runtime_middleware.skill_creator",
            title="Skill 创建器",
            description="创建或修改 Workspace Skill 提案；批准后生成草稿，仍需用户显式安装。",
            category="tool",
            icon="WandSparkles",
            fields=[
                RuntimeMiddlewareField(
                    name="allow_create",
                    label="允许创建提案",
                    type="boolean",
                    default=True,
                ),
                RuntimeMiddlewareField(
                    name="allow_update",
                    label="允许更新提案",
                    type="boolean",
                    default=True,
                ),
                RuntimeMiddlewareField(
                    name="allowed_draft_ids",
                    label="允许读取/修改的 Skill 草稿 ID",
                    type="textarea",
                    rows=4,
                    description="逗号或换行分隔。",
                ),
            ],
            tags=["agent", "skill", "authoring", "proposal", "sandbox"],
            metadata={
                "middleware_name": "skill_creator",
                "runtime_hook": "agent_tools",
                "capability_name": "skill_creator_tools",
                "real_execution": True,
                "app_forbidden": True,
                "proposal_only": True,
            },
            requires_tool_mode="mcp_tools",
            app_policy="forbidden",
            security_category="authoring",
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="event_recorder",
            kind="runtime_middleware.event_recorder",
            title="事件记录器",
            description="记录 chat/model/tool 生命周期事件到 Runtime Event Store，用于追踪和调试。",
            category="agent",
            icon="Activity",
            tags=["observability", "events"],
            metadata={
                "middleware_name": "event_recorder",
                "runtime_hook": "lifecycle",
            },
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="tool_policy",
            kind="runtime_middleware.tool_policy",
            title="工具权限策略",
            description="基于 allow/deny 列表控制哪些工具可以被调用。",
            category="tool",
            icon="Shield",
            fields=[
                RuntimeMiddlewareField(
                    name="allowed_tools",
                    label="允许的工具（每行一个）",
                    type="textarea",
                    placeholder="fetch\necho\nsearch",
                    description="留空表示不限制。白名单模式下仅允许列表中的工具。",
                    rows=4,
                ),
                RuntimeMiddlewareField(
                    name="denied_tools",
                    label="禁止的工具（每行一个）",
                    type="textarea",
                    placeholder="delete\neval",
                    description="优先级高于 allowed_tools。",
                    rows=4,
                ),
                RuntimeMiddlewareField(
                    name="allow_by_default",
                    label="默认放行",
                    type="boolean",
                    default=True,
                ),
            ],
            tags=["tool", "policy", "permission"],
            metadata={
                "middleware_name": "tool_policy",
                "runtime_hook": "tool_policy",
            },
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="tool_audit",
            kind="runtime_middleware.tool_audit",
            title="工具审计记录器",
            description="结构化记录工具调用：工具名、状态、耗时、输出长度、错误信息。",
            category="tool",
            icon="ClipboardList",
            fields=[
                RuntimeMiddlewareField(
                    name="max_records",
                    label="最大记录数",
                    type="number",
                    default=10000,
                    min_value=100,
                    max_value=100000,
                ),
            ],
            tags=["tool", "audit", "observability"],
            metadata={
                "middleware_name": "tool_audit",
                "runtime_hook": "tool_audit",
            },
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="mcp_tools",
            kind="runtime_middleware.mcp_tools",
            title="MCP 工具集",
            description="通过 Runtime Toolset Capability 调用已注册的 MCP 工具。",
            category="tool",
            icon="Puzzle",
            fields=[
                RuntimeMiddlewareField(
                    name="capability_name",
                    label="Capability 名称",
                    type="text",
                    default="mcp_tools",
                ),
                RuntimeMiddlewareField(
                    name="capability_required",
                    label="Capability 必须存在",
                    type="boolean",
                    default=True,
                ),
            ],
            tags=["tool", "mcp", "capability"],
            metadata={
                "capability_name": "mcp_tools",
                "runtime_hook": "capability",
            },
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="context_compression",
            kind="runtime_middleware.context_compression",
            title="上下文压缩",
            description="在上下文接近预算时总结旧消息，并保留最近对话与关键约束。",
            category="agent",
            icon="Shrink",
            fields=[
                RuntimeMiddlewareField(
                    name="max_context_tokens",
                    label="上下文预算（估算 token）",
                    type="number",
                    default=24000,
                    min_value=2048,
                    max_value=200000,
                ),
                RuntimeMiddlewareField(
                    name="trigger_ratio",
                    label="触发比例",
                    type="number",
                    default=0.8,
                    min_value=0.5,
                    max_value=0.95,
                ),
                RuntimeMiddlewareField(
                    name="keep_recent_messages",
                    label="保留最近消息数",
                    type="number",
                    default=8,
                    min_value=2,
                    max_value=40,
                ),
                RuntimeMiddlewareField(
                    name="summary_model_id",
                    label="摘要模型（留空使用 Agent 模型）",
                    type="text",
                    placeholder="openai/gpt-4.1-mini",
                ),
                RuntimeMiddlewareField(
                    name="summary_max_tokens",
                    label="摘要最大 token",
                    type="number",
                    default=1500,
                    min_value=256,
                    max_value=4000,
                ),
                RuntimeMiddlewareField(
                    name="max_tool_output_chars",
                    label="单条工具结果最大字符",
                    type="number",
                    default=4000,
                    min_value=500,
                    max_value=20000,
                ),
            ],
            tags=["agent", "context", "compression", "before_model"],
            metadata={
                "middleware_name": "context_compression",
                "runtime_hook": "before_model",
                "real_execution": True,
            },
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="xpert_file_memory",
            kind="runtime_middleware.xpert_file_memory",
            title="Xpert 文件记忆",
            description="按索引、摘要与正文三层召回长期记忆，并将写回限制为待审批候选。",
            category="memory",
            icon="FolderHeart",
            fields=[
                RuntimeMiddlewareField(
                    name="recall_mode",
                    label="召回模式",
                    type="select",
                    default="hybrid",
                    options=["deterministic", "model", "hybrid"],
                ),
                RuntimeMiddlewareField(
                    name="selector_model_id",
                    label="记忆选择模型（可选）",
                    type="text",
                ),
                RuntimeMiddlewareField(
                    name="selector_timeout_seconds",
                    label="选择超时（秒）",
                    type="number",
                    default=15,
                    min_value=1,
                    max_value=60,
                ),
                RuntimeMiddlewareField(
                    name="max_selected",
                    label="单轮正文记忆数",
                    type="number",
                    default=4,
                    min_value=1,
                    max_value=10,
                ),
                RuntimeMiddlewareField(
                    name="digest_limit",
                    label="摘要数量",
                    type="number",
                    default=10,
                    min_value=1,
                    max_value=30,
                ),
                RuntimeMiddlewareField(
                    name="max_detail_chars_per_turn",
                    label="单轮正文预算",
                    type="number",
                    default=20000,
                    min_value=1000,
                    max_value=40000,
                ),
                RuntimeMiddlewareField(
                    name="max_detail_chars_per_session",
                    label="单会话正文预算",
                    type="number",
                    default=60000,
                    min_value=1000,
                    max_value=200000,
                ),
                RuntimeMiddlewareField(
                    name="writeback_enabled",
                    label="生成写回候选",
                    type="boolean",
                    default=False,
                ),
                RuntimeMiddlewareField(
                    name="writeback_model_id",
                    label="写回模型（可选）",
                    type="text",
                ),
                RuntimeMiddlewareField(
                    name="max_candidates",
                    label="单次候选上限",
                    type="number",
                    default=3,
                    min_value=1,
                    max_value=3,
                ),
            ],
            tags=["agent", "memory", "file", "before_model", "after_agent"],
            metadata={
                "middleware_name": "xpert_file_memory",
                "runtime_hook": "before_model,after_agent",
                "real_execution": True,
            },
            config_version=1,
            execution_status="real",
            requires_tool_mode=None,
            app_policy="allowed",
            security_category="private_context",
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="structured_output",
            kind="runtime_middleware.structured_output",
            title="结构化输出",
            description="校验 Agent 最终输出是否符合 JSON Schema，并允许一次自动修复。",
            category="agent",
            icon="Braces",
            fields=[
                RuntimeMiddlewareField(
                    name="schema_json",
                    label="JSON Schema",
                    type="json",
                    required=True,
                    default={"type": "object"},
                    rows=8,
                ),
                RuntimeMiddlewareField(
                    name="repair_attempts",
                    label="自动修复次数",
                    type="number",
                    default=1,
                    min_value=0,
                    max_value=1,
                ),
            ],
            tags=["agent", "json", "schema", "after_model"],
            metadata={
                "middleware_name": "structured_output",
                "runtime_hook": "final_output",
                "real_execution": True,
            },
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="todo_planner",
            kind="runtime_middleware.todo_planner",
            title="Todo 规划",
            description="向 Agent 提供持久 Todo 工具，并在多步骤任务中维护执行计划。",
            category="agent",
            icon="ListTodo",
            fields=[
                RuntimeMiddlewareField(
                    name="max_items",
                    label="单作用域最大 Todo 数",
                    type="number",
                    default=50,
                    min_value=1,
                    max_value=100,
                ),
            ],
            tags=["agent", "todo", "planning", "tools"],
            metadata={
                "middleware_name": "todo_planner",
                "runtime_hook": "agent_tools",
                "capability_name": "todo_tools",
                "real_execution": True,
            },
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="llm_tool_selector",
            kind="runtime_middleware.llm_tool_selector",
            title="LLM 工具选择器",
            description="在 Agent 执行前从已授权工具中选出与当前任务最相关的最小集合。",
            category="tool",
            icon="ListFilter",
            fields=[
                RuntimeMiddlewareField(
                    name="selector_model_id",
                    label="选择模型（留空使用 Agent 模型）",
                    type="text",
                    placeholder="openai/gpt-4.1-mini",
                ),
                RuntimeMiddlewareField(
                    name="max_selected_tools",
                    label="最多选择工具数",
                    type="number",
                    default=8,
                    min_value=1,
                    max_value=20,
                ),
                RuntimeMiddlewareField(
                    name="always_include_tools",
                    label="始终保留工具（逗号或换行分隔）",
                    type="textarea",
                    rows=3,
                ),
            ],
            tags=["agent", "tool", "selector", "before_agent"],
            metadata={
                "middleware_name": "llm_tool_selector",
                "runtime_hook": "tool_selection",
                "real_execution": True,
            },
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="sandbox_files",
            kind="runtime_middleware.sandbox_files",
            title="隔离文件工作区",
            description="为 Agent 提供离线隔离的文件读写、搜索和产物发布能力。",
            category="tool",
            icon="FolderLock",
            fields=[
                RuntimeMiddlewareField(
                    name="quota_mb",
                    label="工作区配额（MB）",
                    type="number",
                    default=256,
                    min_value=16,
                    max_value=1024,
                ),
                RuntimeMiddlewareField(
                    name="copy_attachments",
                    label="复制本次附件到 inputs/",
                    type="boolean",
                    default=True,
                ),
            ],
            tags=["agent", "sandbox", "files", "artifacts"],
            metadata={
                "middleware_name": "sandbox_files",
                "runtime_hook": "agent_tools",
                "capability_name": "sandbox_tools",
                "network": "none",
                "real_execution": True,
            },
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="sandbox_shell",
            kind="runtime_middleware.sandbox_shell",
            title="隔离命令执行",
            description="在无网络 sidecar 中以 argv 方式执行受控 Python、Node、git 和 rg 命令。",
            category="tool",
            icon="SquareTerminal",
            fields=[
                RuntimeMiddlewareField(
                    name="allowed_commands",
                    label="允许命令（逗号或换行分隔）",
                    type="textarea",
                    default="python,python3,node,npm,npx,git,rg",
                    rows=3,
                ),
                RuntimeMiddlewareField(
                    name="timeout_seconds",
                    label="命令超时（秒）",
                    type="number",
                    default=60,
                    min_value=1,
                    max_value=300,
                ),
                RuntimeMiddlewareField(
                    name="require_approval",
                    label="每次命令需要人工审批",
                    type="boolean",
                    default=True,
                ),
            ],
            tags=["agent", "sandbox", "shell", "hitl"],
            metadata={
                "middleware_name": "sandbox_shell",
                "runtime_hook": "agent_tools",
                "capability_name": "sandbox_tools",
                "network": "none",
                "real_execution": True,
            },
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="skills_runtime",
            kind="runtime_middleware.skills_runtime",
            title="Skill 执行指导",
            description="按需读取已安装 Skill，并安全复制到隔离工作区供 Agent 显式执行。",
            category="tool",
            icon="BookOpenCheck",
            fields=[
                RuntimeMiddlewareField(
                    name="skill_ids",
                    label="已安装 Skill ID（逗号或换行分隔）",
                    type="textarea",
                    rows=4,
                ),
                RuntimeMiddlewareField(
                    name="auto_discover",
                    label="允许发现全部已安装 Skill",
                    type="boolean",
                    default=False,
                ),
            ],
            tags=["agent", "sandbox", "skills"],
            metadata={
                "middleware_name": "skills_runtime",
                "runtime_hook": "agent_tools",
                "capability_name": "sandbox_tools",
                "network": "none",
                "real_execution": True,
            },
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="human_in_the_loop",
            kind="runtime_middleware.human_in_the_loop",
            title="人机审批",
            description="在工具调用或最终输出前持久化暂停，等待人工批准、编辑或拒绝。",
            category="agent",
            icon="ShieldCheck",
            fields=[
                RuntimeMiddlewareField(
                    name="interrupt_on_tools",
                    label="需审批工具",
                    type="textarea",
                    default="",
                    rows=3,
                    placeholder="search, write_file，或 * 表示全部工具",
                    description="逗号或换行分隔；未列出的工具自动放行。",
                ),
                RuntimeMiddlewareField(
                    name="final_confirmation",
                    label="最终输出确认",
                    type="boolean",
                    default=False,
                ),
                RuntimeMiddlewareField(
                    name="allow_edit",
                    label="允许编辑工具参数",
                    type="boolean",
                    default=True,
                ),
                RuntimeMiddlewareField(
                    name="allow_reject",
                    label="允许拒绝工具调用",
                    type="boolean",
                    default=True,
                ),
                RuntimeMiddlewareField(
                    name="description_prefix",
                    label="审批说明",
                    type="textarea",
                    default="工具调用需要人工审批",
                    rows=2,
                ),
                RuntimeMiddlewareField(
                    name="timeout_seconds",
                    label="审批超时（秒）",
                    type="number",
                    default=3600,
                    min_value=30,
                    max_value=86400,
                ),
                RuntimeMiddlewareField(
                    name="max_revision_rounds",
                    label="最大修订轮次",
                    type="number",
                    default=1,
                    min_value=0,
                    max_value=5,
                ),
            ],
            tags=["agent", "approval", "hitl", "interrupt"],
            metadata={
                "middleware_name": "human_in_the_loop",
                "runtime_hook": "wrap_tool_call,after_agent",
                "durable": True,
                "fail_open": False,
            },
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="browser_automation",
            kind="runtime_middleware.browser_automation",
            title="隔离浏览器自动化",
            description="在独立 Playwright sidecar 中访问公共网站，首次域名和写操作均受持久审批保护。",
            category="tool",
            icon="GlobeLock",
            fields=[
                RuntimeMiddlewareField(
                    name="networkPolicy",
                    label="网络策略",
                    type="select",
                    default="public_with_domain_approval",
                    options=["public_with_domain_approval"],
                ),
                RuntimeMiddlewareField(
                    name="allowedDomains",
                    label="允许域名（可选）",
                    type="textarea",
                    rows=3,
                    placeholder="example.com\ndocs.example.com",
                    description="留空允许任意公网域名；首次访问仍需本会话授权。",
                ),
                RuntimeMiddlewareField(
                    name="blockedDomains",
                    label="阻止域名",
                    type="textarea",
                    rows=3,
                ),
                RuntimeMiddlewareField(
                    name="persistSession",
                    label="持久化 Cookie 与站点状态",
                    type="boolean",
                    default=True,
                ),
                RuntimeMiddlewareField(
                    name="maxPages",
                    label="最大页面数",
                    type="number",
                    default=3,
                    min_value=1,
                    max_value=3,
                ),
                RuntimeMiddlewareField(
                    name="maxActions",
                    label="单会话最大操作数",
                    type="number",
                    default=100,
                    min_value=1,
                    max_value=100,
                ),
                RuntimeMiddlewareField(
                    name="navigationTimeoutSeconds",
                    label="导航超时（秒）",
                    type="number",
                    default=30,
                    min_value=5,
                    max_value=120,
                ),
                RuntimeMiddlewareField(
                    name="downloadLimitMb",
                    label="单次下载上限（MB）",
                    type="number",
                    default=50,
                    min_value=1,
                    max_value=50,
                ),
                RuntimeMiddlewareField(
                    name="approvalMode",
                    label="审批模式",
                    type="select",
                    default="mutating",
                    options=["mutating"],
                    description="点击、填写、选择、按键、上传和下载必须经过 HITL。",
                ),
            ],
            tags=["agent", "browser", "playwright", "hitl", "network"],
            metadata={
                "middleware_name": "browser_automation",
                "runtime_hook": "agent_tools",
                "capability_name": "browser_tools",
                "network": "public_with_domain_approval",
                "real_execution": True,
                "app_forbidden": True,
            },
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="client_tools",
            kind="runtime_middleware.client_tools",
            title="客户端工具桥",
            description="将 Agent 工具请求持久化后派发到用户主动绑定的 Chrome 当前标签页，并从断点恢复执行。",
            category="tool",
            icon="MonitorSmartphone",
            fields=[
                RuntimeMiddlewareField(
                    name="clientHostId",
                    label="客户端宿主 ID",
                    type="text",
                    required=True,
                    placeholder="host_...",
                    description="在 Runtime Ops 完成 Chrome 扩展配对后填写。",
                ),
                RuntimeMiddlewareField(
                    name="clientToolNames",
                    label="允许的客户端工具",
                    type="textarea",
                    required=True,
                    rows=5,
                    default=(
                        "host_page_snapshot, host_page_read, host_page_click, "
                        "host_page_fill, host_page_select, host_page_press, "
                        "host_page_hover, host_page_scroll, host_page_wait_for, "
                        "host_page_navigate, host_page_screenshot"
                    ),
                    description="逗号或换行分隔；只能选择宿主声明且 schema 匹配的工具。",
                ),
                RuntimeMiddlewareField(
                    name="clientToolTimeoutSeconds",
                    label="客户端等待超时（秒）",
                    type="number",
                    default=1800,
                    min_value=30,
                    max_value=86400,
                ),
                RuntimeMiddlewareField(
                    name="requireBoundTab",
                    label="要求用户主动绑定当前标签页",
                    type="boolean",
                    default=True,
                ),
            ],
            tags=["agent", "client", "chrome", "hitl", "durable"],
            metadata={
                "middleware_name": "client_tools",
                "runtime_hook": "agent_tools",
                "capability_name": "client_tools",
                "durable": True,
                "real_execution": True,
                "app_forbidden": True,
            },
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="datax_indicators",
            kind="runtime_middleware.datax_indicators",
            title="Data X 语义指标",
            description="在显式项目和语义模型范围内查询已发布指标，并可创建需要人工审批的指标提案。",
            category="tool",
            icon="ChartNoAxesCombined",
            fields=[
                RuntimeMiddlewareField(
                    name="projectIds",
                    label="项目 ID",
                    type="textarea",
                    required=True,
                    rows=3,
                    placeholder="datax_project_...",
                    description="逗号或换行分隔，最多 10 个。",
                ),
                RuntimeMiddlewareField(
                    name="modelIds",
                    label="语义模型 ID",
                    type="textarea",
                    required=True,
                    rows=3,
                    placeholder="datax_model_...",
                    description="模型必须属于上方项目范围，最多 20 个。",
                ),
                RuntimeMiddlewareField(
                    name="allowProposals",
                    label="允许创建指标提案",
                    type="boolean",
                    default=False,
                    description="批准后只生成草稿，仍需用户显式发布。",
                ),
                RuntimeMiddlewareField(
                    name="maxResultRows",
                    label="最大结果行数",
                    type="number",
                    default=100,
                    min_value=1,
                    max_value=500,
                ),
            ],
            tags=["agent", "datax", "analytics", "indicators", "duckdb"],
            metadata={
                "middleware_name": "datax_indicators",
                "runtime_hook": "agent_tools",
                "capability_name": "datax_tools",
                "real_execution": True,
                "app_policy_field": "allow_datax_read",
            },
            config_version=1,
            execution_status="real",
            requires_tool_mode="mcp_tools",
            app_policy="conditional",
            security_category="scoped_data_read",
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="office_automation",
            kind="runtime_middleware.office_automation",
            title="Office 实时自动化",
            description="通过用户绑定的 Word、Excel 或 PowerPoint 当前文档执行 22 个受控 Office.js 工具。",
            category="tool",
            icon="PanelsTopLeft",
            fields=[
                RuntimeMiddlewareField(
                    name="clientHostId",
                    label="Office 宿主 ID",
                    type="text",
                    required=True,
                    placeholder="host_...",
                    description="在 Runtime Ops 配对 Office 加载项并绑定当前文档后选择。",
                ),
                RuntimeMiddlewareField(
                    name="host",
                    label="Office 宿主范围",
                    type="select",
                    default="all",
                    options=["all", "word", "excel", "powerpoint"],
                ),
                RuntimeMiddlewareField(
                    name="allowDeletes",
                    label="允许删除操作",
                    type="boolean",
                    default=False,
                    description="删除仍要求 HITL 覆盖且参数 confirm=true。",
                ),
                RuntimeMiddlewareField(
                    name="allowImageInsert",
                    label="允许 PowerPoint 插入图片产物",
                    type="boolean",
                    default=False,
                    description="仅允许读取同一运行作用域的 Sandbox 图片产物。",
                ),
                RuntimeMiddlewareField(
                    name="timeoutSeconds",
                    label="Office 等待超时（秒）",
                    type="number",
                    default=1800,
                    min_value=30,
                    max_value=86400,
                ),
                RuntimeMiddlewareField(
                    name="requireBoundDocument",
                    label="要求用户主动绑定当前文档",
                    type="boolean",
                    default=True,
                ),
            ],
            tags=["agent", "office", "word", "excel", "powerpoint", "hitl"],
            metadata={
                "middleware_name": "office_automation",
                "runtime_hook": "agent_tools",
                "capability_name": "office_tools",
                "durable": True,
                "real_execution": True,
                "app_forbidden": True,
            },
            config_version=1,
            execution_status="real",
            requires_tool_mode="mcp_tools",
            app_policy="forbidden",
            security_category="client_side_effect",
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="scheduler",
            kind="runtime_middleware.scheduler",
            title="定时任务调度",
            description="允许私有 Xpert 创建、暂停、恢复和立即运行固定版本的持久化计划。",
            category="agent",
            icon="CalendarClock",
            fields=[
                RuntimeMiddlewareField(
                    name="allow_agent_create",
                    label="允许 Agent 创建计划",
                    type="boolean",
                    default=True,
                ),
                RuntimeMiddlewareField(
                    name="default_timezone",
                    label="默认时区",
                    type="text",
                    default="UTC",
                    placeholder="Asia/Shanghai",
                ),
                RuntimeMiddlewareField(
                    name="max_runs_per_day",
                    label="每日运行上限",
                    type="number",
                    default=100,
                    min_value=1,
                    max_value=1000,
                ),
            ],
            tags=["agent", "automation", "scheduler", "cron"],
            metadata={
                "middleware_name": "scheduler",
                "runtime_hook": "agent_tools",
                "capability_name": "automation_tools",
                "durable": True,
                "real_execution": True,
                "app_forbidden": True,
            },
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="ralph_loop",
            kind="runtime_middleware.ralph_loop",
            title="Ralph 迭代循环",
            description="在最终输出前执行有界的行动与严格验证循环，直到完成或达到预算。",
            category="agent",
            icon="Repeat2",
            fields=[
                RuntimeMiddlewareField(
                    name="max_iterations",
                    label="最大迭代次数",
                    type="number",
                    default=5,
                    min_value=1,
                    max_value=20,
                ),
                RuntimeMiddlewareField(
                    name="verifier_model_id",
                    label="验证模型（留空沿用 Agent 模型）",
                    type="text",
                ),
                RuntimeMiddlewareField(
                    name="max_output_chars",
                    label="累计输出预算（字符）",
                    type="number",
                    default=60000,
                    min_value=4000,
                    max_value=200000,
                ),
            ],
            tags=["agent", "automation", "loop", "verification"],
            metadata={
                "middleware_name": "ralph_loop",
                "runtime_hook": "after_agent",
                "real_execution": True,
                "app_forbidden": True,
            },
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="knowledge_writer",
            kind="runtime_middleware.knowledge_writer",
            title="知识写入器",
            description="把显式工具调用或已验证的最终输出提交到现有 Knowledge Inbox 审批。",
            category="knowledge",
            icon="NotebookPen",
            fields=[
                RuntimeMiddlewareField(
                    name="knowledge_base_id",
                    label="目标知识库 ID",
                    type="text",
                    required=True,
                ),
                RuntimeMiddlewareField(
                    name="auto_propose_verified_output",
                    label="自动提议已验证输出",
                    type="boolean",
                    default=False,
                ),
                RuntimeMiddlewareField(
                    name="title_prefix",
                    label="提议标题前缀",
                    type="text",
                    default="Automation result",
                ),
            ],
            tags=["agent", "knowledge", "approval", "automation"],
            metadata={
                "middleware_name": "knowledge_writer",
                "runtime_hook": "agent_tools,after_agent",
                "capability_name": "knowledge_tools",
                "real_execution": True,
            },
        )
    )

    registry.register(
        RuntimeMiddlewareNode(
            id="plugin_hooks",
            kind="runtime_middleware.plugin_hooks",
            title="Skill 插件 Hook",
            description="在离线 Sandbox 中运行已安装 Skill 声明的会话与工具 Hook。",
            category="tool",
            icon="PlugZap",
            fields=[
                RuntimeMiddlewareField(
                    name="skill_ids",
                    label="已安装 Skill ID",
                    type="textarea",
                    required=True,
                    rows=4,
                ),
                RuntimeMiddlewareField(
                    name="fail_closed",
                    label="Hook 失败时阻断 Agent",
                    type="boolean",
                    default=False,
                ),
            ],
            tags=["agent", "skill", "hooks", "sandbox", "automation"],
            metadata={
                "middleware_name": "plugin_hooks",
                "runtime_hook": "before_agent,wrap_tool_call,after_agent",
                "capability_name": "sandbox_tools",
                "network": "none",
                "real_execution": True,
                "app_forbidden": True,
            },
        )
    )


runtime_middleware_registry = RuntimeMiddlewareRegistry()
register_builtin_middleware_nodes(runtime_middleware_registry)
