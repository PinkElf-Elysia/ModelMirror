import { type WorkflowNodeKind } from "../../types/workflow";

export type WorkflowPaletteNodeKind = Exclude<WorkflowNodeKind, "runtime_middleware">;

export type WorkflowPaletteCategoryId =
  | "logic"
  | "transform"
  | "resource"
  | "tool"
  | "memory"
  | "other";

export type WorkflowRegistryTabId = "workflow" | "knowledge";

export interface WorkflowRegistryTab {
  id: WorkflowRegistryTabId;
  label: string;
}

export interface WorkflowPaletteItem {
  kind: WorkflowPaletteNodeKind;
  icon: string;
  title: string;
  description: string;
  category?: WorkflowPaletteCategoryId;
  tags?: string[];
  enabled?: boolean;
  metadata?: Record<string, unknown>;
}

export interface WorkflowPalettePlaceholder {
  id: string;
  icon: string;
  title: string;
  description: string;
  statusLabel: string;
  category?: WorkflowPaletteCategoryId;
  tags?: string[];
  enabled?: false;
  metadata?: Record<string, unknown>;
}

export interface WorkflowPaletteSection {
  id: WorkflowPaletteCategoryId;
  tab?: WorkflowRegistryTabId;
  label: string;
  description: string;
  items: WorkflowPaletteItem[];
  placeholders?: WorkflowPalettePlaceholder[];
}

export interface WorkflowKnowledgePipelinePalette {
  items: WorkflowPaletteItem[];
  placeholders: WorkflowPalettePlaceholder[];
}

export interface WorkflowNodeRegistryResponse {
  version: string;
  tabs: WorkflowRegistryTab[];
  sections: WorkflowPaletteSection[];
  knowledge_pipeline: WorkflowKnowledgePipelinePalette;
}

export const workflowPaletteSections: WorkflowPaletteSection[] = [
  {
    id: "logic",
    label: "逻辑",
    description: "触发、路由、迭代和变量流转。",
    items: [
      {
        kind: "input",
        icon: "▶",
        title: "触发器",
        description: "定义流水线入口变量，默认 user_input。",
        tags: ["input", "start", "trigger"],
      },
      {
        kind: "condition",
        icon: "⌁",
        title: "路由",
        description: "按变量值判断，走“是/否”两条传送带。",
        tags: ["condition", "branch", "route"],
      },
      {
        kind: "iteration",
        icon: "↻",
        title: "迭代",
        description: "逐项渲染模板，汇总为一个 JSON 数组字符串。",
        tags: ["loop", "iteration"],
      },
      {
        kind: "list_operation",
        icon: "▾",
        title: "列表操作",
        description: "对逗号分隔的列表做长度、拼接、首尾提取。",
        tags: ["list", "operator"],
      },
      {
        kind: "variable_aggregator",
        icon: "⧉",
        title: "变量聚合",
        description: "把多个变量汇总为文本或 JSON 字符串。",
        tags: ["aggregate", "variables"],
      },
      {
        kind: "variable_assign",
        icon: "=",
        title: "变量赋值",
        description: "把模板渲染成一个变量，适合整理中间结果。",
        tags: ["assign", "variables"],
      },
    ],
  },
  {
    id: "transform",
    label: "转换",
    description: "分类、检索、模板、代码和最终回答。",
    items: [
      {
        kind: "question_classifier",
        icon: "◇",
        title: "问题分类器",
        description: "根据关键词规则把输入文本分类为预设类别。",
        tags: ["classifier", "question"],
      },
      {
        kind: "knowledge_retrieval",
        icon: "▥",
        title: "知识检索",
        description: "查询本地 RAG 资料库，把相关段落写入变量。",
        tags: ["rag", "knowledge"],
      },
      {
        kind: "code",
        icon: "</>",
        title: "代码执行",
        description: "只支持安全的内置字符串加工函数。",
        tags: ["code", "python", "transform"],
      },
      {
        kind: "template_transform",
        icon: "T",
        title: "模板",
        description: "渲染长文本模板，适合生成报告或结构化草稿。",
        tags: ["template", "text"],
      },
      {
        kind: "parameter_extractor",
        icon: "{}",
        title: "参数提取器",
        description: "调用模型从文本中提取字段，输出 JSON 字符串。",
        tags: ["json", "extract"],
      },
      {
        kind: "document_extractor",
        icon: "□",
        title: "文档提取器",
        description: "从当前工作流作用域的文件资产提取文本。",
        tags: ["document", "file"],
        enabled: false,
        metadata: {
          status_reason: "节点能力无法确认，已按默认关闭处理。",
        },
      },
      {
        kind: "llm",
        icon: "AI",
        title: "LLM 节点",
        description: "安排模型工位处理提示词，可引用 {{变量}}。",
        tags: ["model", "llm"],
      },
      {
        kind: "output",
        icon: "↵",
        title: "回答",
        description: "收尾交付最终变量，展示运行结果。",
        tags: ["output", "answer"],
      },
    ],
    placeholders: [
      {
        id: "json_serialize",
        icon: "JSON",
        title: "JSON 序列化",
        description: "待接入：把变量对象序列化为 JSON。",
        statusLabel: "待接入",
        tags: ["json", "serialize"],
      },
      {
        id: "json_deserialize",
        icon: "JSON",
        title: "JSON 反序列化",
        description: "待接入：把 JSON 字符串解析为结构化变量。",
        statusLabel: "待接入",
        tags: ["json", "deserialize"],
      },
    ],
  },
  {
    id: "resource",
    label: "资源",
    description: "把已发布智能体、知识库、Toolset 与 Plugin 绑定为当前智能体可调用资源。",
    items: [
      {
        kind: "external_xpert",
        icon: "XP",
        title: "外部智能体",
        description: "将已发布智能体作为同步协作者工具绑定到工作流智能体。",
        tags: ["xpert", "expert", "resource", "binding"],
      },
      {
        kind: "knowledge_base",
        icon: "KB",
        title: "知识库",
        description: "将知识库的检索、原文和引用能力绑定到工作流智能体。",
        tags: ["knowledge", "rag", "resource", "binding"],
      },
      {
        kind: "toolset_resource",
        icon: "TS",
        title: "MCP Toolset",
        description: "将已发布的固定版本 MCP Toolset 绑定到工作流智能体。",
        tags: ["mcp", "toolset", "resource", "binding"],
      },
      {
        kind: "plugin_resource",
        icon: "PL",
        title: "Plugin",
        description: "将已发布 Plugin 的 Prompt、Skill、Toolset 与中间件预设绑定到工作流智能体。",
        tags: ["plugin", "prompt", "skill", "toolset", "resource", "binding"],
      },
    ],
  },
  {
    id: "tool",
    label: "工具",
    description: "HTTP、MCP、智能体步骤和任务移交。",
    items: [
      {
        kind: "http_request",
        icon: "HTTP",
        title: "HTTP",
        description: "调用 GET/POST 接口，把响应文本写入变量。",
        tags: ["http", "api"],
      },
      {
        kind: "mcp_tool",
        icon: "◆",
        title: "工具调用",
        description: "调用已连接 MCP Server 暴露的工具。",
        tags: ["mcp", "tool"],
      },
      {
        kind: "agent",
        icon: "A",
        title: "Agent 节点",
        description: "模型驱动的任务执行节点，支持工具循环和直接回答。",
        tags: ["agent", "toolset"],
      },
      {
        kind: "workflow_agent",
        icon: "WA",
        title: "智能体工作流",
        description: "执行一个模型驱动的 Agent 步骤，并写入输出变量。",
        tags: ["workflow-agent", "agent"],
      },
      {
        kind: "agent_task",
        icon: "TASK",
        title: "智能体任务",
        description: "创建 Agent Task Runtime 任务，输出 task_id。",
        tags: ["task", "agent-task"],
      },
      {
        kind: "agent_handoff",
        icon: "⇄",
        title: "任务移交",
        description: "把 Agent Task 显式移交给另一个智能体。",
        tags: ["handoff", "agent"],
      },
      {
        kind: "handoff_router",
        icon: "↪",
        title: "移交路由器",
        description: "读取智能体输出，投递到目标 Agent 的 Handoff Inbox。",
        tags: ["handoff", "router"],
      },
      {
        kind: "time_tool",
        icon: "⌚",
        title: "时间工具",
        description: "获取当前时间、时间戳或格式化日期文本。",
        tags: ["time", "date"],
      },
      {
        kind: "human_intervention",
        icon: "人",
        title: "人工介入",
        description: "暂停流水线，等待用户补充文本后再继续执行。",
        tags: ["human", "approval"],
      },
    ],
  },
  {
    id: "memory",
    label: "记忆",
    description: "数据库、长期记忆和写入能力。",
    items: [],
    placeholders: [
      {
        id: "database_memory",
        icon: "DB",
        title: "数据库",
        description: "待接入：读写结构化数据和长期记忆。",
        statusLabel: "待接入",
        tags: ["database", "memory"],
      },
    ],
  },
  {
    id: "other",
    label: "其他",
    description: "画布辅助节点。",
    items: [],
    placeholders: [
      {
        id: "annotation",
        icon: "※",
        title: "注释",
        description: "待接入：仅用于画布说明，不参与运行。",
        statusLabel: "待接入",
        tags: ["note", "annotation"],
      },
    ],
  },
];

export const knowledgePipelineItems: WorkflowPaletteItem[] = [
  {
    kind: "knowledge_citation",
    icon: "◇",
    title: "知识引用锚点",
    description: "查询本地 RAG，输出 CitationAnchor JSON。",
    tags: ["citation", "rag", "knowledge-pipeline"],
  },
];

export const knowledgePipelinePlaceholders: WorkflowPalettePlaceholder[] = [
  {
    id: "pipeline_source_default",
    icon: "TXT",
    title: "默认数据源",
    description: "待接入：从知识流水线数据源读取文件。",
    statusLabel: "待接入",
    tags: ["source", "data"],
  },
  {
    id: "pipeline_processor",
    icon: "PX",
    title: "处理器",
    description: "待接入：清洗、解析和转换文档内容。",
    statusLabel: "待接入",
    tags: ["processor"],
  },
  {
    id: "pipeline_splitter",
    icon: "¶",
    title: "分块器",
    description: "待接入：递归字符、Markdown 或父子分块。",
    statusLabel: "待接入",
    tags: ["splitter", "chunk"],
  },
  {
    id: "pipeline_vision",
    icon: "眼",
    title: "图像理解",
    description: "待接入：视觉语言模型处理图片内容。",
    statusLabel: "待接入",
    tags: ["vision", "image"],
  },
];

export function matchesWorkflowPaletteQuery(
  item: WorkflowPaletteItem | WorkflowPalettePlaceholder,
  query: string,
) {
  if (!query) {
    return true;
  }
  const haystack = [
    item.title,
    item.description,
    item.icon,
    ...(item.tags ?? []),
    "kind" in item ? item.kind : item.id,
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(query);
}

export const workflowNodeRegistryFallback: WorkflowNodeRegistryResponse = {
  version: "local-workflow-node-registry-fallback",
  tabs: [
    { id: "workflow", label: "工作流" },
    { id: "knowledge", label: "知识流水线" },
  ],
  sections: workflowPaletteSections,
  knowledge_pipeline: {
    items: knowledgePipelineItems,
    placeholders: knowledgePipelinePlaceholders,
  },
};

export async function fetchWorkflowNodeRegistry(): Promise<WorkflowNodeRegistryResponse> {
  const response = await fetch("/api/workflow/node-registry");
  if (!response.ok) {
    throw new Error(`Failed to fetch workflow node registry: ${response.status}`);
  }
  return response.json();
}
