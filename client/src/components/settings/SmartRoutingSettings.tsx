import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  CheckCircle2,
  Gauge,
  LoaderCircle,
  RefreshCw,
  SlidersHorizontal,
} from "lucide-react";

type RouterEngine = "sidecar" | "shadow" | "native_canary" | "native";
type RoutingMode =
  | "auto"
  | "fast"
  | "quality"
  | "cheap"
  | "reliable"
  | "offline";
type CompressionMode = "auto" | "off" | "standard" | "strong";

interface RouterPolicy {
  tenant_id: string;
  engine: RouterEngine;
  default_mode: RoutingMode;
  canary_percent: number;
  compression_mode: CompressionMode;
  updated_at?: string | null;
}

interface RouterStatus {
  connection_count: number;
  online_connection_count: number;
  model_count: number;
  ready: boolean;
}

interface MigrationGate {
  request_count: number;
  success_rate?: number | null;
  empty_stream_rate?: number | null;
  observed_days: number;
  request_gate_met: boolean;
  duration_gate_met: boolean;
  automatic_native_default_allowed: boolean;
}

interface RecentDecision {
  id: string;
  engine: string;
  strategy: string;
  model_id?: string | null;
  connection_name?: string | null;
  outcome?: string | null;
  created_at: string;
}

interface RouterDiagnostics {
  redacted: boolean;
  migration_gate: MigrationGate;
  breaker_summary: Record<string, number>;
  recent_decisions: RecentDecision[];
}

const MODE_OPTIONS: Array<{
  value: RoutingMode;
  label: string;
  description: string;
}> = [
  { value: "auto", label: "均衡推荐", description: "兼顾质量、速度与费用" },
  { value: "fast", label: "速度优先", description: "优先更快完成回答" },
  { value: "quality", label: "质量优先", description: "优先能力更强的模型" },
  { value: "cheap", label: "成本优先", description: "在满足任务时降低费用" },
  { value: "reliable", label: "稳定优先", description: "优先近期成功的模型" },
  { value: "offline", label: "本地优先", description: "优先本地与充足配额" },
];

const ENGINE_OPTIONS: Array<{
  value: RouterEngine;
  label: string;
  description: string;
}> = [
  {
    value: "sidecar",
    label: "稳定模式",
    description: "继续使用当前已验收的调度服务",
  },
  {
    value: "shadow",
    label: "对照观察",
    description: "只比较本地决策，不发起额外模型请求",
  },
  {
    value: "native_canary",
    label: "本地试运行",
    description: "按稳定会话比例逐步启用本地调度",
  },
  {
    value: "native",
    label: "本地默认",
    description: "达到稳定性门槛并人工验收后开放",
  },
];

const COMPRESSION_OPTIONS: Array<{
  value: CompressionMode;
  label: string;
  description: string;
}> = [
  { value: "auto", label: "自动推荐", description: "仅在接近上下文上限时优化" },
  { value: "off", label: "关闭", description: "始终保留原始上下文" },
  { value: "standard", label: "标准", description: "去重并压缩冗余工具与资料内容" },
  { value: "strong", label: "强力", description: "长对话时允许进一步摘要旧内容" },
];

async function readError(response: Response) {
  try {
    const payload = await response.json();
    if (typeof payload?.detail === "string") return payload.detail;
    if (typeof payload?.detail?.message === "string") {
      return payload.detail.message;
    }
  } catch {
    // Use a stable message without exposing an upstream response body.
  }
  return "操作未完成，请检查模型服务连接后重试。";
}

function percent(value?: number | null) {
  return value == null ? "暂无样本" : `${Math.round(value * 1000) / 10}%`;
}

export default function SmartRoutingSettings() {
  const [policy, setPolicy] = useState<RouterPolicy | null>(null);
  const [status, setStatus] = useState<RouterStatus | null>(null);
  const [diagnostics, setDiagnostics] = useState<RouterDiagnostics | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [policyResponse, statusResponse, diagnosticsResponse] =
        await Promise.all([
          fetch("/api/router/policy"),
          fetch("/api/router/status"),
          fetch("/api/router/diagnostics"),
        ]);
      for (const response of [
        policyResponse,
        statusResponse,
        diagnosticsResponse,
      ]) {
        if (!response.ok) throw new Error(await readError(response));
      }
      setPolicy((await policyResponse.json()) as RouterPolicy);
      setStatus((await statusResponse.json()) as RouterStatus);
      setDiagnostics(
        (await diagnosticsResponse.json()) as RouterDiagnostics,
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法读取智能调度设置。",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const nativeReady =
    diagnostics?.migration_gate.automatic_native_default_allowed ?? false;
  const selectedEngine = useMemo(
    () => ENGINE_OPTIONS.find((item) => item.value === policy?.engine),
    [policy?.engine],
  );

  const save = useCallback(async () => {
    if (!policy) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/router/policy", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(policy),
      });
      if (!response.ok) throw new Error(await readError(response));
      setPolicy((await response.json()) as RouterPolicy);
      setNotice("设置已保存，新的智能调度会话将使用此配置。");
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存设置失败。");
    } finally {
      setSaving(false);
    }
  }, [load, policy]);

  return (
    <section className="mb-6 overflow-hidden rounded-lg border border-white/10 bg-ink-950/82 shadow-prism">
      <div className="flex flex-col gap-3 border-b border-white/10 bg-white/[0.035] px-5 py-5 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex items-center gap-2 text-hire-100">
            <SlidersHorizontal className="h-4 w-4" aria-hidden="true" />
            <p className="text-sm font-semibold">智能调度与上下文优化</p>
          </div>
          <h2 className="mt-2 text-2xl font-semibold text-white">
            按任务自动选择合适模型
          </h2>
          <p className="mt-2 max-w-[72ch] text-sm leading-6 text-slate-300">
            普通使用推荐保持“均衡推荐”和“自动推荐”。本地试运行按会话稳定分流，
            同一次对话不会来回切换。
          </p>
        </div>
        <div
          className={`inline-flex w-fit items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-semibold ${
            status?.ready
              ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-200"
              : "border-amber-300/25 bg-amber-300/10 text-amber-100"
          }`}
        >
          <Activity className="h-3.5 w-3.5" aria-hidden="true" />
          {status?.ready
            ? `${status.online_connection_count} 个连接可用`
            : "等待可用连接"}
        </div>
      </div>

      {loading || !policy ? (
        <div className="flex items-center gap-3 p-5 text-sm text-slate-300">
          <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
          正在读取设置…
        </div>
      ) : (
        <div className="grid lg:grid-cols-3">
          <fieldset className="border-b border-white/10 p-5 lg:border-b-0 lg:border-r">
            <legend className="flex items-center gap-2 text-sm font-semibold text-white">
              <Gauge className="h-4 w-4 text-brand-200" aria-hidden="true" />
              智能调度
            </legend>
            <p className="mt-2 text-xs leading-5 text-slate-400">
              选择你更看重的回答结果。
            </p>
            <div className="mt-4 space-y-2">
              {MODE_OPTIONS.map((option) => (
                <label
                  className={`flex cursor-pointer gap-3 rounded-lg border p-3 transition ${
                    policy.default_mode === option.value
                      ? "border-brand-300/40 bg-brand-300/10"
                      : "border-white/10 bg-white/[0.035] hover:border-white/20"
                  }`}
                  key={option.value}
                >
                  <input
                    checked={policy.default_mode === option.value}
                    className="mt-1 accent-cyan-300"
                    name="routing-mode"
                    onChange={() =>
                      setPolicy((current) =>
                        current
                          ? { ...current, default_mode: option.value }
                          : current,
                      )
                    }
                    type="radio"
                  />
                  <span>
                    <span className="block text-sm font-medium text-white">
                      {option.label}
                    </span>
                    <span className="mt-0.5 block text-xs leading-5 text-slate-400">
                      {option.description}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          <fieldset className="border-b border-white/10 p-5 lg:border-b-0 lg:border-r">
            <legend className="text-sm font-semibold text-white">
              上下文优化
            </legend>
            <p className="mt-2 text-xs leading-5 text-slate-400">
              系统提示、最新问题、代码和结构化内容始终受保护。
            </p>
            <div className="mt-4 space-y-2">
              {COMPRESSION_OPTIONS.map((option) => (
                <label
                  className={`flex cursor-pointer gap-3 rounded-lg border p-3 transition ${
                    policy.compression_mode === option.value
                      ? "border-hire-300/35 bg-hire-300/10"
                      : "border-white/10 bg-white/[0.035] hover:border-white/20"
                  }`}
                  key={option.value}
                >
                  <input
                    checked={policy.compression_mode === option.value}
                    className="mt-1 accent-orange-300"
                    name="compression-mode"
                    onChange={() =>
                      setPolicy((current) =>
                        current
                          ? { ...current, compression_mode: option.value }
                          : current,
                      )
                    }
                    type="radio"
                  />
                  <span>
                    <span className="block text-sm font-medium text-white">
                      {option.label}
                    </span>
                    <span className="mt-0.5 block text-xs leading-5 text-slate-400">
                      {option.description}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          <div className="p-5">
            <label className="block text-sm font-semibold text-white">
              运行方式
              <select
                className="mt-3 w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2.5 text-sm text-white outline-none transition focus:border-cyan-300/60"
                onChange={(event) =>
                  setPolicy((current) =>
                    current
                      ? {
                          ...current,
                          engine: event.target.value as RouterEngine,
                          canary_percent:
                            event.target.value === "native_canary"
                              ? Math.max(10, current.canary_percent)
                              : current.canary_percent,
                        }
                      : current,
                  )
                }
                value={policy.engine}
              >
                {ENGINE_OPTIONS.map((option) => (
                  <option
                    disabled={
                      (option.value === "native_canary" && !status?.ready) ||
                      (option.value === "native" &&
                        (!status?.ready || !nativeReady))
                    }
                    key={option.value}
                    value={option.value}
                  >
                    {option.label}
                    {option.value === "native_canary" && !status?.ready
                      ? "（需先连接模型服务）"
                      : option.value === "native" && !nativeReady
                      ? "（未达门槛）"
                      : ""}
                  </option>
                ))}
              </select>
            </label>
            <p className="mt-2 text-xs leading-5 text-slate-400">
              {selectedEngine?.description}
            </p>

            {policy.engine === "native_canary" ? (
              <label className="mt-5 block text-sm font-semibold text-white">
                本地试运行比例：{policy.canary_percent}%
                <input
                  aria-label="本地试运行比例"
                  className="mt-3 w-full accent-cyan-300"
                  max="100"
                  min="0"
                  onChange={(event) =>
                    setPolicy((current) =>
                      current
                        ? {
                            ...current,
                            canary_percent: Number(event.target.value),
                          }
                        : current,
                    )
                  }
                  step="10"
                  type="range"
                  value={policy.canary_percent}
                />
                <span className="mt-1 flex justify-between text-xs font-normal text-slate-500">
                  <span>0%</span>
                  <span>100%</span>
                </span>
              </label>
            ) : null}

            <div className="mt-5 rounded-lg border border-white/10 bg-white/[0.035] p-3 text-xs leading-5 text-slate-400">
              已发现 {status?.model_count ?? 0} 个可调用模型；
              {diagnostics?.migration_gate.request_count ?? 0}/500 次试运行，
              {diagnostics?.migration_gate.observed_days ?? 0}/14 天观察。
            </div>

            <button
              className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-full bg-brand-300 px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-brand-200 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={
                saving ||
                (policy.engine === "native_canary" && !status?.ready) ||
                (policy.engine === "native" &&
                  (!status?.ready || !nativeReady))
              }
              onClick={() => void save()}
              type="button"
            >
              {saving ? (
                <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
              )}
              保存调度设置
            </button>
          </div>
        </div>
      )}

      {error || notice ? (
        <div
          aria-live="polite"
          className={`border-t px-5 py-3 text-sm ${
            error
              ? "border-rose-300/20 bg-rose-300/10 text-rose-100"
              : "border-emerald-300/20 bg-emerald-300/10 text-emerald-100"
          }`}
        >
          {error || notice}
        </div>
      ) : null}

      <details className="border-t border-white/10 bg-slate-950/35">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4 text-sm font-semibold text-slate-200">
          <span>运行诊断（高级）</span>
          <Activity className="h-4 w-4 text-slate-400" aria-hidden="true" />
        </summary>
        <div className="grid gap-4 border-t border-white/10 p-5 text-sm md:grid-cols-3">
          <div className="flex items-center justify-between gap-3 md:col-span-3">
            <p className="text-xs leading-5 text-slate-400">
              只展示脱敏后的健康与运行记录。
            </p>
            <button
              className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-brand-300/30 hover:text-brand-100"
              onClick={() => void load()}
              type="button"
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              刷新
            </button>
          </div>
          <div className="rounded-lg border border-white/10 bg-white/[0.035] p-4">
            <p className="text-xs text-slate-400">成功率</p>
            <p className="mt-2 text-xl font-semibold text-white">
              {percent(diagnostics?.migration_gate.success_rate)}
            </p>
          </div>
          <div className="rounded-lg border border-white/10 bg-white/[0.035] p-4">
            <p className="text-xs text-slate-400">空响应率</p>
            <p className="mt-2 text-xl font-semibold text-white">
              {percent(diagnostics?.migration_gate.empty_stream_rate)}
            </p>
          </div>
          <div className="rounded-lg border border-white/10 bg-white/[0.035] p-4">
            <p className="text-xs text-slate-400">本地默认门槛</p>
            <p className="mt-2 text-sm font-semibold text-white">
              {nativeReady ? "自动门槛已达到，仍需人工验收" : "继续本地试运行"}
            </p>
          </div>
          <div className="md:col-span-3">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">
              最近调度
            </p>
            <div className="mt-2 overflow-x-auto rounded-lg border border-white/10">
              <table className="w-full min-w-[620px] text-left text-xs">
                <thead className="bg-white/[0.04] text-slate-400">
                  <tr>
                    <th className="px-3 py-2 font-medium">时间</th>
                    <th className="px-3 py-2 font-medium">策略</th>
                    <th className="px-3 py-2 font-medium">模型</th>
                    <th className="px-3 py-2 font-medium">服务</th>
                    <th className="px-3 py-2 font-medium">结果</th>
                  </tr>
                </thead>
                <tbody>
                  {(diagnostics?.recent_decisions ?? []).slice(0, 10).map((item) => (
                    <tr className="border-t border-white/10" key={item.id}>
                      <td className="px-3 py-2 text-slate-400">
                        {new Date(item.created_at).toLocaleString("zh-CN")}
                      </td>
                      <td className="px-3 py-2 text-slate-300">{item.strategy}</td>
                      <td className="max-w-56 truncate px-3 py-2 text-white">
                        {item.model_id || "未选择"}
                      </td>
                      <td className="px-3 py-2 text-slate-300">
                        {item.connection_name || "未选择"}
                      </td>
                      <td className="px-3 py-2 text-slate-300">
                        {item.outcome || "待完成"}
                      </td>
                    </tr>
                  ))}
                  {!diagnostics?.recent_decisions.length ? (
                    <tr>
                      <td className="px-3 py-4 text-slate-400" colSpan={5}>
                        尚无本地试运行记录。
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
            <p className="mt-3 text-xs leading-5 text-slate-500">
              诊断记录已脱敏，不包含 API 密钥、完整提示词或上游错误体。
            </p>
          </div>
        </div>
      </details>
    </section>
  );
}
