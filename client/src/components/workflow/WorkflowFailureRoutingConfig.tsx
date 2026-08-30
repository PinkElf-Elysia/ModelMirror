import { AlertTriangle, ChevronDown } from "lucide-react";

import type {
  WorkflowEdge,
  WorkflowNode,
  WorkflowNodeData,
  WorkflowVariableDeclaration,
} from "../../types/workflow";
import type { WorkflowNodeContractProjection } from "./workflowNodeRegistry";
import WorkflowVariableField from "./WorkflowVariableField";


const inputClass =
  "modelmirror-form-control w-full rounded-lg border border-white/10 bg-[#0f1728] px-3 py-2 text-sm text-white outline-none transition placeholder:text-slate-300 hover:border-white/20 focus:border-cyan-300/50 focus:ring-4 focus:ring-cyan-300/10";


export default function WorkflowFailureRoutingConfig({
  contract,
  data,
  declarations = [],
  edges,
  node,
  nodes,
  onChange,
  onOpenVariableCenter,
}: {
  contract?: WorkflowNodeContractProjection | null;
  data: WorkflowNodeData;
  declarations?: WorkflowVariableDeclaration[];
  edges: WorkflowEdge[];
  node: WorkflowNode;
  nodes: WorkflowNode[];
  onChange: (patch: Partial<WorkflowNodeData>) => void;
  onOpenVariableCenter?: () => void;
}) {
  const enabled = (data.failureAction ?? "stop") === "error_output";
  const hasErrorEdge = edges.some(
    (edge) => edge.source === node.id && edge.sourceHandle === "error",
  );

  return (
    <details className="group border-t border-white/10 pt-4">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-lg px-1 py-1.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-200">
        <span>
          <span className="block text-xs font-semibold text-white">失败处理</span>
          <span className="mt-1 block text-[11px] leading-5 text-slate-300">
            {enabled ? "可处理的运行故障将进入红色错误出口。" : "节点失败时终止工作流。"}
          </span>
        </span>
        <ChevronDown className="shrink-0 text-slate-300 transition duration-200 group-open:rotate-180" size={15} />
      </summary>

      <div className="mt-3 space-y-3">
        <label className="block">
          <span className="mb-1.5 block text-xs font-semibold text-slate-200">发生运行故障时</span>
          <select
            className={inputClass}
            onChange={(event) => {
              const next = event.target.value as "stop" | "error_output";
              if (next === "stop" && hasErrorEdge) return;
              onChange(
                next === "error_output"
                  ? {
                      failureAction: next,
                      errorVariable: data.errorVariable || "node_error",
                    }
                  : { failureAction: next, errorVariable: undefined },
              );
            }}
            value={enabled ? "error_output" : "stop"}
          >
            <option className="bg-slate-950" disabled={hasErrorEdge} value="stop">
              终止工作流
            </option>
            <option className="bg-slate-950" value="error_output">进入错误分支</option>
          </select>
        </label>

        {enabled ? (
          <>
            <label className="block">
              <span className="mb-1.5 block text-xs font-semibold text-slate-200">错误结果变量</span>
              <WorkflowVariableField
                ariaLabel="错误结果变量"
                contract={contract}
                declarations={declarations}
                edges={edges}
                fieldName="errorVariable"
                inputClassName={inputClass}
                node={node}
                nodes={nodes}
                onChange={(value) => onChange({ errorVariable: value })}
                value={data.errorVariable ?? ""}
              />
            </label>
            <p className="text-[11px] leading-5 text-slate-300">
              仅网络、超时、外部状态或受支持的读取故障可进入此分支。凭据、权限、安全策略、配置错误和未知异常仍会终止工作流。
            </p>
            {!hasErrorEdge ? (
              <div className="flex items-start gap-2 rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-[11px] leading-5 text-amber-50">
                <AlertTriangle className="mt-0.5 shrink-0" size={14} />
                从节点右侧红色出口连接一个处理步骤后，工作流才能通过校验。
              </div>
            ) : null}
            {onOpenVariableCenter ? (
              <button
                className="text-left text-xs font-semibold text-cyan-200 underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-200"
                onClick={onOpenVariableCenter}
                type="button"
              >
                打开变量中心检查错误结果
              </button>
            ) : null}
          </>
        ) : null}

        {hasErrorEdge ? (
          <p
            aria-live="polite"
            className="text-[11px] leading-5 text-slate-300"
            role="status"
          >
            如需改回“终止工作流”，请先删除红色错误连线，避免静默丢失分支。
          </p>
        ) : null}
      </div>
    </details>
  );
}
