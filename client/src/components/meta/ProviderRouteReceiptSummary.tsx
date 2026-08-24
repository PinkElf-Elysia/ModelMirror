export interface ProviderRouteCallReceipt {
  call_sequence: number;
  model_id: string;
  actual_model?: string | null;
  dispatched?: boolean;
  status: "passed" | "failed" | "uncertain" | "cancelled";
  error_code?: string | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
}

export interface ProviderRouteReceipt {
  contract_version: string;
  entry_id: "meta_agent";
  routing_mode: "managed_required";
  run_reference: string;
  status: "running" | "passed" | "failed" | "uncertain" | "cancelled";
  call_count: number;
  reason_codes: string[];
  calls: ProviderRouteCallReceipt[];
}

const statusLabels: Record<ProviderRouteReceipt["status"], string> = {
  running: "调用中",
  passed: "已纳管",
  failed: "纳管调用失败",
  uncertain: "调用结果待确认",
  cancelled: "调用已取消",
};

const callStatusLabels: Record<ProviderRouteCallReceipt["status"], string> = {
  passed: "成功",
  failed: "失败",
  uncertain: "待确认",
  cancelled: "已取消",
};

function statusClass(status: ProviderRouteReceipt["status"]) {
  if (status === "passed") return "border-emerald-300/25 bg-emerald-300/10 text-emerald-100";
  if (status === "uncertain") return "border-amber-300/25 bg-amber-300/10 text-amber-100";
  if (status === "running") return "border-cyan-300/25 bg-cyan-300/10 text-cyan-100";
  return "border-rose-300/25 bg-rose-300/10 text-rose-100";
}

export default function ProviderRouteReceiptSummary({
  receipt,
}: {
  receipt: ProviderRouteReceipt | null | undefined;
}) {
  if (!receipt) return null;
  const modelCount = new Set(
    receipt.calls
      .filter((call) => call.dispatched !== false)
      .map((call) => call.model_id),
  ).size;

  return (
    <details className="rounded-lg border border-white/10 bg-white/[0.035] text-xs text-slate-300">
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 px-3 py-2.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50">
        <span className={`rounded-full border px-2 py-0.5 font-semibold ${statusClass(receipt.status)}`}>
          {statusLabels[receipt.status]}
        </span>
        <span>{receipt.call_count} 次模型调用</span>
        <span className="text-slate-500">{modelCount} 个精确模型</span>
        <span className="ml-auto font-mono text-[10px] text-slate-500">
          {receipt.run_reference.slice(-12)}
        </span>
      </summary>
      <div className="border-t border-white/10 px-3 py-2.5">
        <p className="mb-2 text-slate-400">
          仅显示脱敏路由结果。连接、凭据和完整证据仅在设置中查看。
        </p>
        <div className="space-y-1.5">
          {receipt.calls.map((call) => (
            <div
              className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md bg-slate-950/35 px-2.5 py-2"
              key={`${call.call_sequence}-${call.model_id}`}
            >
              <span className="font-semibold text-slate-200">
                {call.dispatched === false ? "预检" : "调用"} {call.call_sequence}
              </span>
              <span className="min-w-0 truncate font-mono text-[10px] text-slate-400">
                {call.model_id}
              </span>
              <span>{callStatusLabels[call.status]}</span>
              {call.dispatched === false ? (
                <span className="text-amber-200">未派发</span>
              ) : null}
              {call.total_tokens != null ? (
                <span className="ml-auto text-slate-500">{call.total_tokens} tokens</span>
              ) : null}
              {call.error_code ? (
                <span className="w-full font-mono text-[10px] text-rose-200">
                  {call.error_code}
                </span>
              ) : null}
            </div>
          ))}
        </div>
      </div>
    </details>
  );
}
