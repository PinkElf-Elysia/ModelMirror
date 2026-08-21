import { useState } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { type WorkflowNode } from "../../types/workflow";
import { isLegacySkillCreatorMiddleware } from "../../utils/skillCreatorMiddleware";

const nodeMeta = {
  input: {
    icon: "📥",
    label: "输入工位",
    border: "border-slate-300/40",
    bg: "bg-slate-300/10",
    text: "text-slate-100",
  },
  scheduled_start: {
    icon: "⏱",
    label: "定时启动",
    border: "border-amber-300/40",
    bg: "bg-amber-300/10",
    text: "text-amber-100",
  },
  http_event_entry: {
    icon: "POST",
    label: "HTTP 事件入口",
    border: "border-cyan-300/40",
    bg: "bg-cyan-300/10",
    text: "text-cyan-100",
  },
  failure_event_entry: {
    icon: "!",
    label: "失败处置入口",
    border: "border-rose-300/40",
    bg: "bg-rose-300/10",
    text: "text-rose-100",
  },
  workflow_call_entry: {
    icon: "IN",
    label: "子流程入口",
    border: "border-indigo-300/40",
    bg: "bg-indigo-300/10",
    text: "text-indigo-100",
  },
  invoke_workflow: {
    icon: "CALL",
    label: "调用工作流",
    border: "border-indigo-300/40",
    bg: "bg-indigo-300/10",
    text: "text-indigo-100",
  },
  suspend_wait: {
    icon: "WAIT",
    label: "挂起等待",
    border: "border-amber-300/40",
    bg: "bg-amber-300/10",
    text: "text-amber-100",
  },
  http_event_reply: {
    icon: "↩",
    label: "HTTP 事件回执",
    border: "border-cyan-300/40",
    bg: "bg-cyan-300/10",
    text: "text-cyan-100",
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
    border: "border-amber-300/40",
    bg: "bg-amber-300/10",
    text: "text-amber-100",
  },
  multi_route: {
    icon: "⇶",
    label: "多路分派",
    border: "border-amber-300/40",
    bg: "bg-amber-300/10",
    text: "text-amber-100",
  },
  terminate_error: {
    icon: "!",
    label: "主动终止",
    border: "border-rose-300/40",
    bg: "bg-rose-300/10",
    text: "text-rose-100",
  },
  code: {
    icon: "🔧",
    label: "加工工位",
    border: "border-brand-300/40",
    bg: "bg-brand-300/10",
    text: "text-brand-100",
  },
  variable_assign: {
    icon: "🪄",
    label: "赋值工位",
    border: "border-brand-300/40",
    bg: "bg-brand-300/10",
    text: "text-brand-100",
  },
  template_transform: {
    icon: "📝",
    label: "模板工位",
    border: "border-brand-300/40",
    bg: "bg-brand-300/10",
    text: "text-brand-100",
  },
  variable_aggregator: {
    icon: "🔗",
    label: "聚合工位",
    border: "border-brand-300/40",
    bg: "bg-brand-300/10",
    text: "text-brand-100",
  },
  parameter_extractor: {
    icon: "🎯",
    label: "提取工位",
    border: "border-brand-300/40",
    bg: "bg-brand-300/10",
    text: "text-brand-100",
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
    border: "border-teal-300/40",
    bg: "bg-teal-300/10",
    text: "text-teal-100",
  },
  document_extractor: {
    icon: "📄",
    label: "文档工位",
    border: "border-teal-300/40",
    bg: "bg-teal-300/10",
    text: "text-teal-100",
  },
  vision_understanding: {
    icon: "VIS",
    label: "视觉理解",
    border: "border-teal-300/40",
    bg: "bg-teal-300/10",
    text: "text-teal-100",
  },
  human_intervention: {
    icon: "👤",
    label: "人工工位",
    border: "border-slate-300/40",
    bg: "bg-slate-300/10",
    text: "text-slate-100",
  },
  question_classifier: {
    icon: "🏷️",
    label: "分类",
    border: "border-violet-300/40",
    bg: "bg-violet-300/10",
    text: "text-violet-100",
  },
  agent: {
    icon: "🤖",
    label: "Agent",
    border: "border-violet-300/40",
    bg: "bg-violet-300/10",
    text: "text-violet-100",
  },
  workflow_agent: {
    icon: "🧭",
    label: "工作流智能体",
    border: "border-violet-300/40",
    bg: "bg-violet-300/10",
    text: "text-violet-100",
  },
  external_xpert: {
    icon: "XP",
    label: "外部智能体",
    border: "border-violet-300/40",
    bg: "bg-violet-300/10",
    text: "text-violet-100",
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
    border: "border-sky-300/40",
    bg: "bg-sky-300/10",
    text: "text-sky-100",
  },
  plugin_resource: {
    icon: "PL",
    label: "Plugin 资源",
    border: "border-sky-300/40",
    bg: "bg-sky-300/10",
    text: "text-sky-100",
  },
  agent_task: {
    icon: "▣",
    label: "Agent Task",
    border: "border-violet-300/40",
    bg: "bg-violet-300/10",
    text: "text-violet-100",
  },
  agent_handoff: {
    icon: "⇄",
    label: "Handoff",
    border: "border-violet-300/40",
    bg: "bg-violet-300/10",
    text: "text-violet-100",
  },
  handoff_router: {
    icon: "↪",
    label: "Handoff Router",
    border: "border-violet-300/40",
    bg: "bg-violet-300/10",
    text: "text-violet-100",
  },
  mcp_tool: {
    icon: "🔧",
    label: "MCP Tool",
    border: "border-sky-300/40",
    bg: "bg-sky-300/10",
    text: "text-sky-100",
  },
  time_tool: {
    icon: "🕒",
    label: "时间",
    border: "border-sky-300/40",
    bg: "bg-sky-300/10",
    text: "text-sky-100",
  },
  http_request: {
    icon: "🌐",
    label: "外联工位",
    border: "border-sky-300/40",
    bg: "bg-sky-300/10",
    text: "text-sky-100",
  },
  list_operation: {
    icon: "📋",
    label: "列表工位",
    border: "border-sky-300/40",
    bg: "bg-sky-300/10",
    text: "text-sky-100",
  },
  data_aggregate: {
    icon: "Σ",
    label: "数据聚合",
    border: "border-brand-300/40",
    bg: "bg-brand-300/10",
    text: "text-brand-100",
  },
  iteration: {
    icon: "🔁",
    label: "迭代工位",
    border: "border-amber-300/40",
    bg: "bg-amber-300/10",
    text: "text-amber-100",
  },
  json_serialize: {
    icon: "{}",
    label: "JSON 序列化",
    border: "border-brand-300/40",
    bg: "bg-brand-300/10",
    text: "text-brand-100",
  },
  json_deserialize: {
    icon: "{·}",
    label: "JSON 反序列化",
    border: "border-brand-300/40",
    bg: "bg-brand-300/10",
    text: "text-brand-100",
  },
  data_table_query: {
    icon: "DB",
    label: "数据表查询",
    border: "border-sky-300/40",
    bg: "bg-sky-300/10",
    text: "text-sky-100",
  },
  data_table_insert: {
    icon: "DB+",
    label: "数据表新增",
    border: "border-sky-300/40",
    bg: "bg-sky-300/10",
    text: "text-sky-100",
  },
  data_table_update: {
    icon: "DB~",
    label: "数据表更新",
    border: "border-sky-300/40",
    bg: "bg-sky-300/10",
    text: "text-sky-100",
  },
  data_table_delete: {
    icon: "DB-",
    label: "数据表删除",
    border: "border-sky-300/40",
    bg: "bg-sky-300/10",
    text: "text-sky-100",
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
    border: "border-sky-300/40",
    bg: "bg-sky-300/10",
    text: "text-sky-100",
  },
  output: {
    icon: "📤",
    label: "交付工位",
    border: "border-slate-300/40",
    bg: "bg-slate-300/10",
    text: "text-slate-100",
  },
};

export default function WorkflowNodeCard({ data, selected }: NodeProps<WorkflowNode>) {
  const meta =
    nodeMeta[data.kind as keyof typeof nodeMeta] ?? nodeMeta.template_transform;
  const [showInfo, setShowInfo] = useState(false);
  const runStatus = data.runStatus;
  const multiRoutes = data.kind === "multi_route" && Array.isArray(data.routes)
    ? data.routes.filter((route) => route.id && route.label)
    : [];
  const legacySkillCreator = isLegacySkillCreatorMiddleware(data);

  const statusClassName =
    runStatus === "running"
      ? "border-cyan-300/80 ring-2 ring-cyan-400/40"
      : runStatus === "error"
        ? "border-rose-400/80 ring-2 ring-rose-400/30"
        : selected
          ? "border-hire-200/70 ring-2 ring-hire-300/20"
          : meta.border;

  return (
    <div
      className={`relative rounded-lg border-2 bg-[#141c2e] p-2 text-slate-100 shadow-md transition duration-150 hover:bg-[#182238] active:scale-95 ${data.kind === "multi_route" ? "min-w-36" : "min-w-24"} ${statusClassName}`}
      style={data.kind === "multi_route" ? { minHeight: `${Math.max(132, (multiRoutes.length + 1) * 34)}px` } : undefined}
      onDoubleClick={() => setShowInfo((current) => !current)}
    >
      {runStatus === "running" ? (
        <span
          aria-label="运行中"
          className="absolute -right-1.5 -top-1.5 flex h-3.5 w-3.5 animate-pulse items-center justify-center rounded-full bg-cyan-400 shadow-[0_0_8px_rgba(34,211,238,0.8)]"
          title="运行中"
        />
      ) : null}
      {runStatus === "done" ? (
        <span
          aria-label="已完成"
          className="absolute -right-1.5 -top-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-emerald-400 text-[9px] font-bold text-ink-950 shadow"
          title="已完成"
        >
          ✓
        </span>
      ) : null}
      {runStatus === "error" ? (
        <span
          aria-label="运行异常"
          className="absolute -right-1.5 -top-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-rose-500 text-[9px] font-bold text-white shadow"
          title="运行异常"
        >
          !
        </span>
      ) : null}
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
          "scheduled_start",
          "http_event_entry",
          "failure_event_entry",
          "workflow_call_entry",
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

      <div className="flex flex-col items-center gap-1.5 py-1 text-center">
        <span
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border ${meta.border} ${meta.bg} text-lg`}
        >
          {meta.icon}
        </span>
        <h3 className="max-w-24 truncate text-xs font-semibold leading-tight text-white">
          {data.title}
        </h3>
        {legacySkillCreator ? (
          <span className="rounded-full border border-amber-300/30 bg-amber-300/10 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-amber-100">
            Legacy
          </span>
        ) : null}
      </div>

      {showInfo ? (
        <div className="absolute left-full top-0 z-20 ml-2 w-56 rounded-lg border border-white/15 bg-[#0d1424] p-3 shadow-lg">
          <h3 className="text-sm font-semibold text-white">{data.title}</h3>
          <p className="mt-1 text-xs leading-5 text-slate-300">
            {data.description}
          </p>
        </div>
      ) : null}

      {data.kind === "multi_route" ? (
        <>
          {multiRoutes.map((route, index) => {
            const top = `${((index + 1) / (multiRoutes.length + 2)) * 100}%`;
            return (
              <div key={route.id}>
                <Handle
                  className="!h-3 !w-3 !border-2 !border-surface-900 !bg-amber-300"
                  id={route.id}
                  position={Position.Right}
                  style={{ top }}
                  title={route.label}
                  type="source"
                />
                <div
                  className="pointer-events-none absolute left-full ml-2 max-w-24 truncate text-[10px] font-semibold text-amber-100"
                  style={{ top: `calc(${top} - 7px)` }}
                  title={route.label}
                >
                  {route.label}
                </div>
              </div>
            );
          })}
          <Handle
            className="!h-3 !w-3 !border-2 !border-surface-900 !bg-slate-300"
            id="default"
            position={Position.Right}
            style={{ top: `${((multiRoutes.length + 1) / (multiRoutes.length + 2)) * 100}%` }}
            title="默认出口"
            type="source"
          />
          <div
            className="pointer-events-none absolute left-full ml-2 text-[10px] font-semibold text-slate-200"
            style={{ top: `calc(${((multiRoutes.length + 1) / (multiRoutes.length + 2)) * 100}% - 7px)` }}
          >
            默认
          </div>
        </>
      ) : data.kind === "condition" ? (
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
      ) : !["output", "http_event_reply", "terminate_error", "annotation"].includes(data.kind) ? (
        <Handle
          className="!h-3 !w-3 !border-2 !border-surface-900 !bg-hire-300"
          position={Position.Right}
          type="source"
        />
      ) : null}
    </div>
  );
}
