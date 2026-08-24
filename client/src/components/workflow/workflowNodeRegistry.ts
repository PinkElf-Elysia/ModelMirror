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

export interface WorkflowValueSchemaProjection {
  type: "any" | "null" | "string" | "number" | "integer" | "boolean" | "object" | "array";
  nullable?: boolean;
  items?: WorkflowValueSchemaProjection | null;
  properties?: Record<string, WorkflowValueSchemaProjection>;
  required?: string[];
  any_of?: WorkflowValueSchemaProjection[];
}

export interface WorkflowNodePortProjection {
  name: string;
  direction: "input" | "output";
  value_schema: WorkflowValueSchemaProjection;
  required: boolean;
  cardinality: "one" | "many";
  binding: "variable" | "literal" | "resource" | "none";
}

export interface WorkflowNodeContractProjection {
  kind: WorkflowPaletteNodeKind | "runtime_middleware";
  contract_status: "complete" | "compatibility";
  config_schema: Record<string, unknown>;
  ports: WorkflowNodePortProjection[];
  edge: Record<string, unknown>;
  execution: Record<string, unknown>;
  availability: Record<string, unknown>;
  resources: Array<Record<string, unknown>>;
  planner: Record<string, unknown>;
  contract_version: number;
  checksum: string;
  compiler_checksum: string;
  deprecated?: boolean;
  replacement_kind?: string | null;
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
  contract?: WorkflowNodeContractProjection;
  planner?: Record<string, unknown>;
  contracts?: Record<string, unknown>;
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
  contract_version: number;
  contract_checksum: string;
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
        kind: "scheduled_start",
        icon: "TIME",
        title: "定时启动",
        description: "从已发布版本按单次、固定间隔或日历规则私有启动。",
        tags: ["schedule", "cron", "deployment"],
      },
      {
        kind: "http_event_entry",
        icon: "POST",
        title: "HTTP 事件入口",
        description: "接收带私有密钥与幂等键的 POST 事件。",
        tags: ["webhook", "http", "deployment"],
      },
      {
        kind: "failure_event_entry",
        icon: "FAIL",
        title: "失败处置入口",
        description: "监听所选已发布工作流的失败并接收脱敏事件。",
        tags: ["failure", "error", "deployment"],
      },
      {
        kind: "workflow_call_entry",
        icon: "CALL IN",
        title: "子流程入口",
        description: "声明仅供其他已发布工作流同步调用的私有入口。",
        tags: ["workflow", "call", "entry"],
      },
      {
        kind: "invoke_workflow",
        icon: "CALL",
        title: "调用已发布工作流",
        description: "同步调用已启用的固定工作流版本并接收结果。",
        tags: ["workflow", "call", "subworkflow"],
      },
      {
        kind: "suspend_wait",
        icon: "WAIT",
        title: "挂起等待",
        description: "持久挂起至指定持续时间或带时区时间点。",
        tags: ["wait", "timer", "continuation"],
      },
      {
        kind: "http_event_reply",
        icon: "REPLY",
        title: "HTTP 事件回执",
        description: "以文本或 JSON 终止 HTTP 事件工作流。",
        tags: ["webhook", "response", "terminal"],
      },
      {
        kind: "condition",
        icon: "⌁",
        title: "路由",
        description: "按变量值判断，走“是/否”两条传送带。",
        tags: ["condition", "branch", "route"],
      },
      {
        kind: "multi_route",
        icon: "ROUTE",
        title: "多路分派",
        description: "按顺序匹配 2 至 8 条类型化规则，并提供默认出口。",
        tags: ["switch", "route", "typed-value"],
      },
      {
        kind: "terminate_error",
        icon: "STOP",
        title: "主动终止",
        description: "使用固定安全错误码和消息终止当前执行。",
        tags: ["stop", "error", "terminal"],
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
        description: "对数组做筛选、排序和去重，并保留旧列表操作。",
        tags: ["list", "filter", "sort", "deduplicate", "typed-value"],
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
        description: "按稳定分类出口分派问题，可选择规则、模型或规则后模型。",
        tags: ["classifier", "question", "model", "stable-route"],
      },
      {
        kind: "code",
        icon: "</>",
        title: "安全文本加工",
        description: "把变量稳定转成文本，再执行受控的大小写、替换或拼接操作。",
        tags: ["text", "safe", "transform"],
      },
      {
        kind: "parameter_extractor",
        icon: "{}",
        title: "参数提取器",
        description: "调用模型提取字段，输出经 Schema 校验的 JSON 对象或对象列表。",
        tags: ["json", "extract", "schema", "typed-value"],
      },
      {
        kind: "data_aggregate",
        icon: "AGG",
        title: "数据聚合",
        description: "按顶层字段分组，并计算计数、求和、均值和极值。",
        tags: ["aggregate", "group", "measure", "typed-value"],
      },
      {
        kind: "data_merge",
        icon: "MERGE",
        title: "数据合流",
        description: "等待左右路径到达，再拼接数组或按复合键一对一合并。",
        tags: ["merge", "fan-in", "join", "typed-value"],
      },
      {
        kind: "dataset_compare",
        icon: "DIFF",
        title: "数据集对照",
        description: "按稳定复合键对照两份对象数组，识别新增、删除、变化和未变化项。",
        tags: ["dataset", "compare", "diff", "typed-value"],
      },
      {
        kind: "object_transform",
        icon: "OBJ",
        title: "对象整理",
        description: "按顺序设置、重命名、删除或保留对象顶层字段。",
        tags: ["object", "transform", "set", "rename", "typed-value"],
      },
      {
        kind: "json_serialize",
        icon: "JSON",
        title: "JSON 序列化",
        description: "把类型化变量序列化为 JSON 字符串。",
        tags: ["json", "serialize", "typed-value"],
      },
      {
        kind: "json_deserialize",
        icon: "JSON",
        title: "JSON 反序列化",
        description: "把 JSON 字符串解析为类型化变量。",
        tags: ["json", "deserialize", "typed-value"],
      },
      {
        kind: "document_extractor",
        icon: "□",
        title: "文档提取器",
        description: "提取经典工作流文件或私有智能体明确共享附件中的文本。",
        tags: ["document", "file"],
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
    placeholders: [],
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
        title: "安全 HTTP 请求",
        description: "以固定公网目标、加密凭据和结构化参数调用 HTTP 接口。",
        tags: ["http", "api", "credential", "public-only"],
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
        description: "获取、转换、运算和归整带时区的时间。",
        tags: ["time", "date", "timezone", "difference"],
      },
      {
        kind: "file_output",
        icon: "FILE",
        title: "生成文件",
        description: "把变量安全生成 TXT、Markdown、JSON、CSV、PDF、DOCX 或 XLSX。",
        tags: ["file", "document", "pdf", "docx", "xlsx", "csv"],
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
    items: [
      {
        kind: "data_table_query",
        icon: "DB?",
        title: "查询数据表",
        description: "读取固定 Schema 的 Agent Table 记录。",
        tags: ["database", "agent-table", "query"],
      },
      {
        kind: "data_table_insert",
        icon: "DB+",
        title: "新增数据",
        description: "向 Agent Table 插入一条类型化记录。",
        tags: ["database", "agent-table", "insert"],
      },
      {
        kind: "data_table_update",
        icon: "DB~",
        title: "更新数据",
        description: "按非空条件更新 Agent Table 记录。",
        tags: ["database", "agent-table", "update"],
      },
      {
        kind: "data_table_delete",
        icon: "DB-",
        title: "删除数据",
        description: "按非空条件删除 Agent Table 记录。",
        tags: ["database", "agent-table", "delete"],
      },
    ],
    placeholders: [],
  },
  {
    id: "other",
    label: "其他",
    description: "画布辅助节点。",
    items: [
      {
        kind: "annotation",
        icon: "※",
        title: "注释",
        description: "仅用于画布说明，不参与拓扑或运行。",
        tags: ["note", "annotation"],
      },
    ],
    placeholders: [],
  },
];

export const knowledgePipelineItems: WorkflowPaletteItem[] = [
  {
    kind: "knowledge_base",
    icon: "KB",
    title: "知识库",
    description: "将一个知识库绑定到工作流智能体的只读知识工具。",
    tags: ["knowledge", "rag", "resource", "binding"],
  },
  {
    kind: "knowledge_retrieval",
    icon: "▥",
    title: "知识检索",
    description: "检索指定知识库的活动版本并输出文本或类型化结果。",
    tags: ["rag", "knowledge", "retrieval"],
  },
  {
    kind: "vision_understanding",
    icon: "VISION",
    title: "视觉理解",
    description: "读取私有运行显式共享的图片或扫描 PDF，输出类型化视觉结果。",
    tags: ["vision", "ocr", "image", "pdf", "typed-value"],
  },
];

export const knowledgePipelinePlaceholders: WorkflowPalettePlaceholder[] = [];

function disableFallbackItems(
  items: WorkflowPaletteItem[],
): WorkflowPaletteItem[] {
  return items.map((item) => ({
    ...item,
    enabled: false,
    metadata: {
      ...(item.metadata ?? {}),
      status_reason: "节点注册表不可用，无法确认当前执行契约。",
    },
  }));
}

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
  contract_version: 0,
  contract_checksum: "",
  tabs: [
    { id: "workflow", label: "工作流" },
    { id: "knowledge", label: "知识流水线" },
  ],
  sections: workflowPaletteSections.map((section) => ({
    ...section,
    items: disableFallbackItems(section.items),
  })),
  knowledge_pipeline: {
    items: disableFallbackItems(knowledgePipelineItems),
    placeholders: knowledgePipelinePlaceholders,
  },
};

function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{64}$/i.test(value);
}

export function hasNodeContractV3(
  registry: WorkflowNodeRegistryResponse,
): boolean {
  if (
    registry.version !== "xpert-workflow-node-registry-v4" ||
    registry.contract_version !== 3 ||
    !isSha256(registry.contract_checksum)
  ) {
    return false;
  }
  const items = [
    ...registry.sections.flatMap((section) => section.items),
    ...registry.knowledge_pipeline.items,
  ].filter((item) => item.enabled !== false);
  if (items.length === 0) {
    return false;
  }
  return items.every(
    (item) =>
      item.contract?.contract_version === 3 &&
      item.contract.kind === item.kind &&
      isSha256(item.contract.checksum) &&
      isSha256(item.contract.compiler_checksum),
  );
}

export async function fetchWorkflowNodeRegistry(): Promise<WorkflowNodeRegistryResponse> {
  const response = await fetch("/api/workflow/node-registry");
  if (!response.ok) {
    throw new Error(`Failed to fetch workflow node registry: ${response.status}`);
  }
  const payload = (await response.json()) as WorkflowNodeRegistryResponse;
  if (!hasNodeContractV3(payload)) {
    throw new Error("Workflow node registry does not provide NodeContract V3.");
  }
  return payload;
}
