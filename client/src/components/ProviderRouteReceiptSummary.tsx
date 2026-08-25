import type { ProviderRouteReceipt } from "./AgencyExpertTeamTypes";

const statusLabels: Record<ProviderRouteReceipt["status"], string> = {
  running: "执行中",
  passed: "通过",
  failed: "失败",
  uncertain: "结果不确定",
  cancelled: "已取消",
};

interface ProviderRouteReceiptSummaryProps {
  receipts?: ProviderRouteReceipt | ProviderRouteReceipt[] | null;
  title: string;
}

export default function ProviderRouteReceiptSummary({
  receipts,
  title,
}: ProviderRouteReceiptSummaryProps) {
  const items = Array.isArray(receipts) ? receipts : receipts ? [receipts] : [];
  if (!items.length) return null;
  const callCount = items.reduce((total, item) => total + item.call_count, 0);

  return (
    <section
      aria-label={`${title} Provider 控制面证据`}
      className="mt-3 rounded-lg border border-cyan-300/20 bg-cyan-300/[0.06] p-3 text-xs text-cyan-50"
    >
      <p className="font-semibold">
        {title}：已纳管 · {callCount} 次 Provider 调用
      </p>
      <ul className="mt-2 space-y-1 text-cyan-100/80">
        {items.map((item, index) => {
          const models = [...new Set(item.calls.map((call) => call.model_id).filter(Boolean))];
          return (
            <li key={item.run_reference || `${item.entry_id}-${index}`}>
              执行片段 {index + 1}：{statusLabels[item.status]} · {item.call_count} 次
              {models.length ? ` · ${models.join("、")}` : ""}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
