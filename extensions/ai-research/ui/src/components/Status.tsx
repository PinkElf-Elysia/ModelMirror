import { AlertTriangle, CheckCircle2, CircleDashed, Clock3, XCircle } from "lucide-react";

const labels: Record<string, string> = {
  ready: "就绪",
  degraded: "降级",
  not_ready: "未就绪",
  queued: "排队中",
  running: "运行中",
  terminal: "已终止",
  success: "成功",
  task_error: "任务错误",
  cancelled: "已取消",
  infrastructure_error: "基础设施错误",
  pending: "待同步",
  synced: "已同步",
  failed: "失败",
  verified: "完整性已验证",
  started: "已启动",
  error: "错误",
};

function tone(value: string) {
  if (["ready", "success", "synced", "verified"].includes(value)) {
    return { className: "border-[#275c43] bg-[#10291d] text-[#91e0af]", Icon: CheckCircle2 };
  }
  if (["degraded", "queued", "pending", "started"].includes(value)) {
    return { className: "border-[#6b572a] bg-[#2b2414] text-[#f2cf83]", Icon: Clock3 };
  }
  if (["running"].includes(value)) {
    return { className: "border-[#286264] bg-[#102c2e] text-[#83ddda]", Icon: CircleDashed };
  }
  if (["not_ready", "task_error", "infrastructure_error", "failed", "error"].includes(value)) {
    return { className: "border-[#704044] bg-[#2b1719] text-[#f2a2a2]", Icon: XCircle };
  }
  return { className: "border-[var(--border-strong)] bg-[#171b20] text-[#c1c9ce]", Icon: AlertTriangle };
}

export function Status({ value, label }: { value: string | null; label?: string }) {
  if (!value) return <span className="text-[var(--muted)]">—</span>;
  const { className, Icon } = tone(value);
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-1 text-xs font-semibold ${className}`}>
      <Icon aria-hidden="true" size={13} />
      {label ?? labels[value] ?? value}
    </span>
  );
}

export function statusLabel(value: string | null) {
  return value ? labels[value] ?? value : "—";
}
