import { Handle, Position, type NodeProps } from "@xyflow/react";
import { type WorkflowNode } from "../../types/workflow";

const nodeMeta = {
  input: {
    icon: "📥",
    label: "输入工位",
    border: "border-emerald-300/40",
    bg: "bg-emerald-300/10",
    text: "text-emerald-100",
  },
  llm: {
    icon: "🤖",
    label: "LLM 工位",
    border: "border-brand-300/40",
    bg: "bg-brand-300/10",
    text: "text-brand-100",
  },
  condition: {
    icon: "🔀",
    label: "分流工位",
    border: "border-amber-300/45",
    bg: "bg-amber-300/10",
    text: "text-amber-100",
  },
  code: {
    icon: "🔧",
    label: "加工工位",
    border: "border-accent-300/40",
    bg: "bg-accent-300/10",
    text: "text-accent-100",
  },
  variable_assign: {
    icon: "🪄",
    label: "赋值工位",
    border: "border-fuchsia-300/40",
    bg: "bg-fuchsia-300/10",
    text: "text-fuchsia-100",
  },
  template_transform: {
    icon: "📝",
    label: "模板工位",
    border: "border-lime-300/40",
    bg: "bg-lime-300/10",
    text: "text-lime-100",
  },
  variable_aggregator: {
    icon: "🔗",
    label: "聚合工位",
    border: "border-orange-300/40",
    bg: "bg-orange-300/10",
    text: "text-orange-100",
  },
  parameter_extractor: {
    icon: "🎯",
    label: "提取工位",
    border: "border-rose-300/40",
    bg: "bg-rose-300/10",
    text: "text-rose-100",
  },
  knowledge_retrieval: {
    icon: "📚",
    label: "资料工位",
    border: "border-teal-300/40",
    bg: "bg-teal-300/10",
    text: "text-teal-100",
  },
  knowledge_citation: {
    icon: "🔖",
    label: "引用锚点",
    border: "border-teal-200/40",
    bg: "bg-teal-200/10",
    text: "text-teal-100",
  },
  document_extractor: {
    icon: "📄",
    label: "文档工位",
    border: "border-slate-300/40",
    bg: "bg-slate-300/10",
    text: "text-slate-100",
  },
  vision_understanding: {
    icon: "VIS",
    label: "视觉理解",
    border: "border-cyan-300/40",
    bg: "bg-cyan-300/10",
    text: "text-cyan-100",
  },
  human_intervention: {
    icon: "👤",
    label: "人工工位",
    border: "border-sky-300/40",
    bg: "bg-sky-300/10",
    text: "text-sky-100",
  },
  question_classifier: {
    icon: "🏷️",
    label: "分类",
    border: "border-yellow-300/40",
    bg: "bg-yellow-300/10",
    text: "text-yellow-100",
  },
  agent: {
    icon: "🤖",
    label: "Agent",
    border: "border-violet-400/40",
    bg: "bg-violet-400/10",
    text: "text-violet-100",
  },
  workflow_agent: {
    icon: "🧭",
    label: "工作流智能体",
    border: "border-cyan-300/40",
    bg: "bg-cyan-300/10",
    text: "text-cyan-100",
  },
  external_xpert: {
    icon: "XP",
    label: "外部智能体",
    border: "border-blue-300/40",
    bg: "bg-blue-300/10",
    text: "text-blue-100",
  },
  knowledge_base: {
    icon: "KB",
    label: "知识库资源",
    border: "border-teal-300/40",
    bg: "bg-teal-300/10",
    text: "text-teal-100",
  },
  toolset_resource: {
    icon: "TS",
    label: "Toolset 资源",
    border: "border-amber-300/40",
    bg: "bg-amber-300/10",
    text: "text-amber-100",
  },
  plugin_resource: {
    icon: "PL",
    label: "Plugin 资源",
    border: "border-violet-300/40",
    bg: "bg-violet-300/10",
    text: "text-violet-100",
  },
  agent_task: {
    icon: "▣",
    label: "Agent Task",
    border: "border-purple-300/40",
    bg: "bg-purple-300/10",
    text: "text-purple-100",
  },
  agent_handoff: {
    icon: "⇄",
    label: "Handoff",
    border: "border-fuchsia-300/40",
    bg: "bg-fuchsia-300/10",
    text: "text-fuchsia-100",
  },
  handoff_router: {
    icon: "↪",
    label: "Handoff Router",
    border: "border-pink-300/40",
    bg: "bg-pink-300/10",
    text: "text-pink-100",
  },
  mcp_tool: {
    icon: "🔧",
    label: "MCP Tool",
    border: "border-emerald-400/40",
    bg: "bg-emerald-400/10",
    text: "text-emerald-200",
  },
  time_tool: {
    icon: "🕒",
    label: "时间",
    border: "border-sky-400/40",
    bg: "bg-sky-400/10",
    text: "text-sky-200",
  },
  http_request: {
    icon: "🌐",
    label: "外联工位",
    border: "border-cyan-300/40",
    bg: "bg-cyan-300/10",
    text: "text-cyan-100",
  },
  list_operation: {
    icon: "📋",
    label: "列表工位",
    border: "border-sky-300/40",
    bg: "bg-sky-300/10",
    text: "text-sky-100",
  },
  iteration: {
    icon: "🔁",
    label: "迭代工位",
    border: "border-violet-300/40",
    bg: "bg-violet-300/10",
    text: "text-violet-100",
  },
  json_serialize: {
    icon: "{}",
    label: "JSON 序列化",
    border: "border-blue-300/40",
    bg: "bg-blue-300/10",
    text: "text-blue-100",
  },
  json_deserialize: {
    icon: "{·}",
    label: "JSON 反序列化",
    border: "border-indigo-300/40",
    bg: "bg-indigo-300/10",
    text: "text-indigo-100",
  },
  data_table_query: {
    icon: "DB",
    label: "数据表查询",
    border: "border-cyan-300/40",
    bg: "bg-cyan-300/10",
    text: "text-cyan-100",
  },
  data_table_insert: {
    icon: "DB+",
    label: "数据表新增",
    border: "border-emerald-300/40",
    bg: "bg-emerald-300/10",
    text: "text-emerald-100",
  },
  data_table_update: {
    icon: "DB~",
    label: "数据表更新",
    border: "border-amber-300/40",
    bg: "bg-amber-300/10",
    text: "text-amber-100",
  },
  data_table_delete: {
    icon: "DB-",
    label: "数据表删除",
    border: "border-rose-300/40",
    bg: "bg-rose-300/10",
    text: "text-rose-100",
  },
  annotation: {
    icon: "NOTE",
    label: "画布注释",
    border: "border-slate-300/40",
    bg: "bg-slate-300/10",
    text: "text-slate-100",
  },
  runtime_middleware: {
    icon: "▣",
    label: "中间件",
    border: "border-indigo-300/40",
    bg: "bg-indigo-300/10",
    text: "text-indigo-100",
  },
  output: {
    icon: "📤",
    label: "交付工位",
    border: "border-hire-300/45",
    bg: "bg-hire-300/10",
    text: "text-hire-100",
  },
};

function outputName(data: WorkflowNode["data"]) {
  if (data.kind === "input") return data.variableName ?? "user_input";
  if (data.kind === "condition") {
    return `${data.conditionVariable ?? "变量"} ${data.conditionOperator === "equals" ? "等于" : "包含"} ${data.conditionValue ?? "值"}`;
  }
  if (data.kind === "code") return data.codeOutputVariable ?? "code_output";
  if (data.kind === "variable_assign") {
    return `${data.variableName ?? "variable"} = ${data.template ?? "{{value}}"}`;
  }
  if (data.kind === "template_transform") {
    return `${data.outputVariable ?? "template_output"} <= template`;
  }
  if (data.kind === "variable_aggregator") {
    return `${data.variableNames ?? "变量列表"} -> ${data.outputVariable ?? "aggregated_output"}`;
  }
  if (data.kind === "parameter_extractor") {
    return `${data.inputVariable ?? "text"} -> ${data.outputVariable ?? "parameters_json"}`;
  }
  if (data.kind === "knowledge_retrieval") {
    return `${data.queryVariable ?? "query"} top ${data.top_k ?? "3"} -> ${data.outputVariable ?? "rag_context"}`;
  }
  if (data.kind === "knowledge_citation") {
    return `${data.queryVariable ?? "query"} top ${data.top_k ?? "4"} -> ${data.outputVariable ?? "citation_anchors_json"}`;
  }
  if (data.kind === "document_extractor") {
    const sourceVariable = data.assetIdVariable ?? data.sourcePathVariable;
    const sourceKind = data.assetIdVariable ? "asset" : "legacy path";
    return `${sourceKind}: ${sourceVariable ?? "未配置"} -> ${data.outputVariable ?? "document_text"}`;
  }
  if (data.kind === "vision_understanding") {
    return `${data.assetIdVariable ?? "visual_asset_id"} · ${data.pdfPageStrategy ?? "auto"} -> ${data.outputVariable ?? "vision_result"}`;
  }
  if (data.kind === "human_intervention") {
    return `等待输入 -> ${data.outputVariable ?? "human_input"}`;
  }
  if (data.kind === "question_classifier") {
    return `分类 → ${data.outputVariable ?? "category"}`;
  }
  if (data.kind === "agent") {
    return `🤖 ${data.agentMode ?? "tool_first"} → ${data.outputVariable ?? "agent_output"}`;
  }
  if (data.kind === "workflow_agent") {
    return `${data.agentName ?? "workflow-agent"} · ${data.toolMode ?? "none"} → ${data.outputVariable ?? "agent_output"}`;
  }
  if (data.kind === "external_xpert") {
    const version =
      data.versionPolicy === "pinned"
        ? `v${data.pinnedVersion ?? "?"}`
        : "current";
    return `${data.toolName ?? "external_xpert"} -> ${version}`;
  }
  if (data.kind === "knowledge_base") {
    return `${data.knowledgeBaseId || "选择知识库"} · top ${data.topK ?? "5"}`;
  }
  if (data.kind === "toolset_resource") {
    const version =
      data.versionPolicy === "pinned"
        ? `v${data.pinnedVersion ?? "?"}`
        : "current";
    return `${data.toolsetId || "选择 Toolset"} -> ${version}`;
  }
  if (data.kind === "plugin_resource") {
    const version =
      data.versionPolicy === "pinned"
        ? `v${data.pinnedVersion ?? "?"}`
        : "current";
    return `${data.pluginId || "选择 Plugin"} -> ${version}`;
  }
  if (data.kind === "agent_task") {
    return `${data.assignedAgent ?? "workflow-planner"} → ${data.outputVariable ?? "agent_task_id"}`;
  }
  if (data.kind === "agent_handoff") {
    const mode = data.executionMode === "xpert_auto" ? "auto" : "manual";
    return `${mode} · ${data.taskIdVariable ?? "agent_task_id"} -> ${data.targetAgent ?? "review-agent"}`;
  }
  if (data.kind === "handoff_router") {
    const mode = data.executionMode === "xpert_auto" ? "auto" : "manual";
    return `${mode} · ${data.sourceVariable ?? "agent_output"} -> ${data.targetAgent ?? "review-agent"}`;
  }
  if (data.kind === "mcp_tool") {
    return `🔧 ${data.toolName ?? "未选择"} → ${data.outputVariable ?? "mcp_output"}`;
  }
  if (data.kind === "time_tool") {
    return `🕒 ${data.operation ?? "now_iso"} → ${data.outputVariable ?? "current_time"}`;
  }
  if (data.kind === "http_request") {
    return `${data.method ?? "GET"} ${data.url ?? "https://example.com"} -> ${data.outputVariable ?? "http_output"}`;
  }
  if (data.kind === "list_operation") {
    return `${data.inputVariable ?? "items"} ${data.operator ?? "length"} -> ${data.outputVariable ?? "list_output"}`;
  }
  if (data.kind === "iteration") {
    return `${data.inputVariable ?? "items"} as ${data.iterationVariable ?? "item"} -> ${data.outputVariable ?? "iteration_output"}`;
  }
  if (data.kind === "json_serialize") {
    return `${data.inputVariable ?? "json_value"} -> ${data.outputVariable ?? "json_text"}`;
  }
  if (data.kind === "json_deserialize") {
    return `${data.inputVariable ?? "json_text"} -> ${data.outputVariable ?? "json_value"}`;
  }
  if (data.kind === "data_table_query") {
    return `${data.tableId || "选择数据表"} -> ${data.outputVariable ?? "table_records"}`;
  }
  if (data.kind === "data_table_insert") {
    return `${data.tableId || "选择数据表"} -> ${data.outputVariable ?? "inserted_record"}`;
  }
  if (data.kind === "data_table_update") {
    return `${data.tableId || "选择数据表"} -> ${data.outputVariable ?? "update_result"}`;
  }
  if (data.kind === "data_table_delete") {
    return `${data.tableId || "选择数据表"} -> ${data.outputVariable ?? "delete_result"}`;
  }
  if (data.kind === "annotation") {
    return "不参与运行";
  }
  if (data.kind === "runtime_middleware") {
    return `${data.runtimeMiddlewareId ?? "middleware"} → ${data.runtimeMiddlewareKind ?? "runtime"}`;
  }
  if (data.kind === "output") return data.outputVariable ?? "final_output";
  return data.outputVariable ?? "llm_output";
}

export default function WorkflowNodeCard({ data, selected }: NodeProps<WorkflowNode>) {
  const meta =
    nodeMeta[data.kind as keyof typeof nodeMeta] ?? nodeMeta.template_transform;

  return (
    <div
      className={`relative min-w-56 rounded-lg border bg-surface-900/95 p-3 text-slate-100 shadow-prism backdrop-blur-xl transition duration-200 ${
        selected
          ? "border-hire-200/70 ring-4 ring-hire-300/15"
          : meta.border
      }`}
    >
      {data.kind === "workflow_agent" ? (
        <>
          <Handle
            className="!h-3 !w-3 !border-2 !border-surface-900 !bg-slate-200"
            position={Position.Left}
            style={{ top: "24%" }}
            type="target"
          />
          <Handle
            className="!h-3 !w-3 !border-2 !border-surface-900 !bg-indigo-300"
            id="middleware"
            position={Position.Left}
            style={{ top: "94%" }}
            title="绑定 Agent 中间件"
            type="target"
          />
          <Handle
            className="!h-3 !w-3 !border-2 !border-surface-900 !bg-blue-300"
            id="expert"
            position={Position.Left}
            style={{ top: "38%" }}
            title="绑定外部智能体"
            type="target"
          />
          <Handle
            className="!h-3 !w-3 !border-2 !border-surface-900 !bg-teal-300"
            id="knowledge"
            position={Position.Left}
            style={{ top: "52%" }}
            title="绑定知识库"
            type="target"
          />
          <Handle
            className="!h-3 !w-3 !border-2 !border-surface-900 !bg-amber-300"
            id="toolset"
            position={Position.Left}
            style={{ top: "66%" }}
            title="绑定 Toolset"
            type="target"
          />
          <Handle
            className="!h-3 !w-3 !border-2 !border-surface-900 !bg-violet-300"
            id="plugin"
            position={Position.Left}
            style={{ top: "80%" }}
            title="绑定 Plugin"
            type="target"
          />
        </>
      ) : ![
          "input",
          "external_xpert",
          "knowledge_base",
          "toolset_resource",
          "plugin_resource",
          "annotation",
        ].includes(data.kind) ? (
        <Handle
          className="!h-3 !w-3 !border-2 !border-surface-900 !bg-slate-200"
          position={Position.Left}
          type="target"
        />
      ) : null}

      <div className="flex items-start gap-3">
        <span
          className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border ${meta.border} ${meta.bg} text-lg`}
        >
          {meta.icon}
        </span>
        <div className="min-w-0">
          <span
            className={`inline-flex rounded-full border px-2 py-0.5 text-[11px] font-semibold ${meta.border} ${meta.bg} ${meta.text}`}
          >
            {meta.label}
          </span>
          <h3 className="mt-2 line-clamp-2 text-sm font-semibold text-white">
            {data.title}
          </h3>
          <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-400">
            {data.description}
          </p>
        </div>
      </div>

      <div className="mt-3 rounded-md border border-white/10 bg-white/[0.045] px-2.5 py-2">
        <p className="text-[11px] text-slate-500">变量/判断</p>
        <p className="mt-0.5 truncate text-xs font-semibold text-slate-100">
          {outputName(data)}
        </p>
      </div>

      {data.kind === "condition" ? (
        <>
          <Handle
            className="!h-3 !w-3 !border-2 !border-surface-900 !bg-emerald-300"
            id="true"
            position={Position.Right}
            style={{ top: "38%" }}
            type="source"
          />
          <Handle
            className="!h-3 !w-3 !border-2 !border-surface-900 !bg-rose-300"
            id="false"
            position={Position.Right}
            style={{ top: "68%" }}
            type="source"
          />
          <div className="pointer-events-none absolute -right-12 top-[32%] text-[10px] font-semibold text-emerald-100">
            是
          </div>
          <div className="pointer-events-none absolute -right-12 top-[62%] text-[10px] font-semibold text-rose-100">
            否
          </div>
        </>
      ) : data.kind === "runtime_middleware" ? (
        <>
          <Handle
            className="!h-3 !w-3 !border-2 !border-surface-900 !bg-hire-300"
            position={Position.Right}
            style={{ top: "38%" }}
            title="控制流输出"
            type="source"
          />
          <Handle
            className="!h-3 !w-3 !border-2 !border-surface-900 !bg-indigo-300"
            id="middleware-binding"
            position={Position.Right}
            style={{ top: "72%" }}
            title="绑定到 workflow_agent"
            type="source"
          />
        </>
      ) : data.kind === "external_xpert" ? (
        <Handle
          className="!h-3 !w-3 !border-2 !border-surface-900 !bg-blue-300"
          id="expert-binding"
          position={Position.Right}
          title="绑定到 workflow_agent 的 expert 入口"
          type="source"
        />
      ) : data.kind === "knowledge_base" ? (
        <Handle
          className="!h-3 !w-3 !border-2 !border-surface-900 !bg-teal-300"
          id="knowledge-binding"
          position={Position.Right}
          title="绑定到 workflow_agent 的 knowledge 入口"
          type="source"
        />
      ) : data.kind === "toolset_resource" ? (
        <Handle
          className="!h-3 !w-3 !border-2 !border-surface-900 !bg-amber-300"
          id="toolset-binding"
          position={Position.Right}
          title="绑定到 workflow_agent 的 toolset 入口"
          type="source"
        />
      ) : data.kind === "plugin_resource" ? (
        <Handle
          className="!h-3 !w-3 !border-2 !border-surface-900 !bg-violet-300"
          id="plugin-binding"
          position={Position.Right}
          title="绑定到 workflow_agent 的 plugin 入口"
          type="source"
        />
      ) : !["output", "annotation"].includes(data.kind) ? (
        <Handle
          className="!h-3 !w-3 !border-2 !border-surface-900 !bg-hire-300"
          position={Position.Right}
          type="source"
        />
      ) : null}
    </div>
  );
}
