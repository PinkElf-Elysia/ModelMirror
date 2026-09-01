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


export interface WorkflowRetryAvailability {
  registryStatus?: "loading" | "ready" | "error";
  resourceStatus?: "loading" | "ready" | "error";
  featureEnabled?: boolean;
  featureDisabledReason?: string;
  eligible?: boolean;
  ineligibleReason?: string;
}


export function workflowRetryIneligibleReason(
  data: WorkflowNodeData,
  availability?: WorkflowRetryAvailability,
) {
  if (data.kind === "http_request") {
    if (Number(data.contractVersion ?? 1) !== 2) {
      return "只有 V2 安全 HTTP 请求可以配置重试。";
    }
    if ((data.method ?? "GET") !== "GET") {
      return "自动重试只允许固定 GET 请求，写方法不会自动重试。";
    }
    if ((data.bodyMode ?? "none") !== "none") {
      return "自动重试的 GET 请求不能携带请求正文。";
    }
  } else if (data.kind === "knowledge_retrieval") {
    if (Number(data.contractVersion ?? 1) !== 2) {
      return "只有 V2 知识检索可以配置重试。";
    }
  } else if (data.kind !== "data_table_query") {
    return "该节点不在自动重试白名单中。";
  }

  if (availability?.eligible === false) {
    return availability.ineligibleReason?.trim() || "当前资源不符合安全重试条件。";
  }
  return "";
}


export default function WorkflowFailureRoutingConfig({
  contract,
  data,
  declarations = [],
  edges,
  node,
  nodes,
  onChange,
  onOpenVariableCenter,
  retryAvailability,
}: {
  contract?: WorkflowNodeContractProjection | null;
  data: WorkflowNodeData;
  declarations?: WorkflowVariableDeclaration[];
  edges: WorkflowEdge[];
  node: WorkflowNode;
  nodes: WorkflowNode[];
  onChange: (patch: Partial<WorkflowNodeData>) => void;
  onOpenVariableCenter?: () => void;
  retryAvailability?: WorkflowRetryAvailability;
}) {
  const enabled = (data.failureAction ?? "stop") === "error_output";
  const retryEnabled = (data.retryMode ?? "none") === "transient";
  const maxAttempts = data.maxAttempts === 3 ? 3 : 2;
  const retryBlockedReason = retryEnabled
    ? workflowRetryIneligibleReason(data, retryAvailability)
    : "";
  const retryFeatureBlocked = retryEnabled && retryAvailability?.featureEnabled === false;
  const retryAvailabilityUnknown = retryEnabled && (
    retryAvailability?.registryStatus !== "ready"
    || typeof retryAvailability?.featureEnabled !== "boolean"
    || (
      data.kind === "knowledge_retrieval"
      && (
        retryAvailability?.resourceStatus !== "ready"
        || typeof retryAvailability?.eligible !== "boolean"
      )
    )
  );
  const retrySummary = retryFeatureBlocked
    ? `自动重试当前不可运行：${retryAvailability?.featureDisabledReason?.trim() || "当前环境未开启节点重试。"}`
    : retryBlockedReason
    ? `自动重试配置需修正：${retryBlockedReason}`
    : retryAvailabilityUnknown
      ? "自动重试资格尚未确认，运行和激活前必须完成检查。"
      : retryEnabled
        ? `临时故障最多尝试 ${maxAttempts} 次，${enabled ? "仍未成功时进入错误出口。" : "仍未成功时终止工作流。"}`
        : enabled
          ? "可处理的运行故障将进入红色错误出口。"
          : "节点失败时终止工作流。";
  const hasErrorEdge = edges.some(
    (edge) => edge.source === node.id && edge.sourceHandle === "error",
  );

  return (
    <details className="group border-t border-white/10 pt-4">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-lg px-1 py-1.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-200">
        <span>
          <span className="block text-xs font-semibold text-white">失败处理</span>
          <span className="mt-1 block text-[11px] leading-5 text-slate-300">
            {retrySummary}
          </span>
        </span>
        <ChevronDown className="shrink-0 text-slate-300 transition duration-200 group-open:rotate-180" size={15} />
      </summary>

      <div className="mt-3 space-y-3">
        <label className="block">
          <span className="mb-1.5 block text-xs font-semibold text-slate-200">自动重试</span>
          <select
            className={inputClass}
            onChange={(event) => {
              const retryMode = event.target.value as "none" | "transient";
              onChange({
                retryMode,
                ...(retryMode === "transient" ? { maxAttempts } : {}),
              });
            }}
            value={retryEnabled ? "transient" : "none"}
          >
            <option className="bg-slate-950" value="none">不自动重试</option>
            <option className="bg-slate-950" value="transient">仅在确认是临时故障时重试</option>
          </select>
        </label>

        {retryEnabled ? (
          <>
            <label className="block">
              <span className="mb-1.5 block text-xs font-semibold text-slate-200">
                最多尝试次数（含首次）
              </span>
              <select
                className={inputClass}
                onChange={(event) => onChange({
                  maxAttempts: Number(event.target.value) === 3 ? 3 : 2,
                })}
                value={maxAttempts}
              >
                <option className="bg-slate-950" value={2}>2 次（首次 + 1 次重试）</option>
                <option className="bg-slate-950" value={3}>3 次（首次 + 2 次重试）</option>
              </select>
            </label>
            <p className="text-[11px] leading-5 text-slate-300">
              第 2 次前等待 5 秒，第 3 次前等待 30 秒。HTTP 429 的整数 Retry-After 可以延长等待，但最长为 300 秒。
            </p>
            <p className="rounded-lg border border-cyan-300/20 bg-cyan-300/5 px-3 py-2 text-[11px] leading-5 text-cyan-50">
              为避免把业务数据写入重试记录，等待期间不会保存前序节点生成的结果。若重试节点或其后续步骤仍需要这些结果，请把对应查询或检索移到重试节点之后；发布前会检查这项约束。
            </p>
            {data.kind === "http_request" && data.statusPolicy === "capture_all" ? (
              <p className="text-[11px] leading-5 text-cyan-100">
                当前会捕获全部 HTTP 状态，因此非 2xx 响应属于正常结果；只有连接失败或超时等合格故障可能重试。
              </p>
            ) : null}
            {data.kind === "http_request" && data.statusPolicy !== "capture_all" ? (
              <p className="text-[11px] leading-5 text-slate-300">
                仅连接失败、超时以及 408、429、502、503、504 会重试；DNS、SSRF、TLS、凭据和响应解析错误会直接失败。
              </p>
            ) : null}
            {retryAvailability?.featureEnabled === false ? (
              <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-[11px] leading-5 text-amber-50">
                当前环境未开启节点重试。配置可以保存在草稿中，但激活、私有 Xpert 发布和实际运行会被阻止。
                {retryAvailability.featureDisabledReason ? (
                  <span className="mt-1 block text-amber-100/80">
                    {retryAvailability.featureDisabledReason}
                  </span>
                ) : null}
              </div>
            ) : null}
            {retryAvailabilityUnknown ? (
              <div
                aria-live="polite"
                className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-[11px] leading-5 text-amber-50"
                role="status"
              >
                {retryAvailability?.registryStatus === "error"
                  ? "节点目录暂不可用，无法确认重试功能与资格。草稿可以继续编辑，但运行或激活会被服务端阻止。"
                  : data.kind === "knowledge_retrieval" && retryAvailability?.resourceStatus === "error"
                    ? "知识库状态暂不可用，无法确认本地检索资格。草稿可以继续编辑，但运行或激活会被服务端阻止。"
                    : "正在确认重试功能和资源资格；确认完成前不会把当前配置视为可运行。"}
              </div>
            ) : null}
            {retryBlockedReason ? (
              <div
                aria-live="polite"
                className="flex items-start gap-2 rounded-lg border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-[11px] leading-5 text-rose-50"
                role="alert"
              >
                <AlertTriangle className="mt-0.5 shrink-0" size={14} />
                <span>
                  当前配置不能运行自动重试：{retryBlockedReason} 系统不会静默关闭该配置，请修正后再激活或运行。
                </span>
              </div>
            ) : null}
            {data.kind === "data_table_query" ? (
              <p className="text-[11px] leading-5 text-slate-300">
                仅真实的 SQLite BUSY 或 LOCKED 存储繁忙会重试，普通查询错误不会重试。
              </p>
            ) : null}
            {data.kind === "knowledge_retrieval" && !retryBlockedReason ? (
              <p className="text-[11px] leading-5 text-slate-300">
                仅本地全文检索，或使用本地 hash embedding 且没有远程重排的向量/混合检索可重试；发布、激活和恢复时都会复检活动版本。
              </p>
            ) : null}
          </>
        ) : null}

        <label className="block">
          <span className="mb-1.5 block text-xs font-semibold text-slate-200">
            {retryEnabled ? "重试仍未成功时" : "发生运行故障时"}
          </span>
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
