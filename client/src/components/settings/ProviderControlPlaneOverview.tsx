import { useEffect, useState } from "react";
import { Activity, CircleAlert, Database, Server } from "lucide-react";

interface OperationCount {
  operation: string;
  total: number;
  invocable: number;
  stale: number;
  blocked: number;
}

interface Overview {
  provider_count: number;
  online_provider_count: number;
  discovered_model_count: number;
  stale_model_count: number;
  operation_counts: OperationCount[];
  blocking_reason_codes: string[];
  default_qualification: "not_evaluated";
}

interface RuntimeEnvironmentSummary {
  llm_gateway_configured: boolean;
  openrouter_configured: boolean;
  model_gateway_ready: boolean;
}

const REASON_LABELS: Record<string, string> = {
  provider_not_configured: "尚未配置 Provider",
  provider_not_online: "没有已连通的 Provider",
  catalog_not_available: "尚未生成运行时目录",
  catalog_contains_stale_evidence: "目录包含过期证据",
};

const OPERATION_LABELS: Record<string, string> = {
  chat: "文本 Chat",
  analyze_image: "图像理解",
  generate_image: "图像生成",
  transcribe: "语音转写",
  synthesize_speech: "语音合成",
  realtime_voice: "实时语音",
  analyze_video: "视频理解",
  generate_video: "视频生成",
  generate_world: "世界生成",
};

export default function ProviderControlPlaneOverview() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [runtimeEnvironment, setRuntimeEnvironment] = useState<RuntimeEnvironmentSummary | null>(null);
  const [runtimeEnvironmentUnavailable, setRuntimeEnvironmentUnavailable] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/router/control-plane/overview", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("无法读取统一控制面总览。");
        return (await response.json()) as Overview;
      })
      .then((payload) => {
        if (!controller.signal.aborted) setOverview(payload);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "无法读取统一控制面总览。");
        }
      });
    void fetch("/api/runtime/environment-summary", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("无法读取普通 Chat 环境路径。");
        return (await response.json()) as RuntimeEnvironmentSummary;
      })
      .then((payload) => {
        if (!controller.signal.aborted) setRuntimeEnvironment(payload);
      })
      .catch(() => {
        if (!controller.signal.aborted) setRuntimeEnvironmentUnavailable(true);
      });
    return () => controller.abort();
  }, []);

  if (error) {
    return <p className="rounded-lg border border-rose-300/20 bg-rose-300/10 p-4 text-sm text-rose-100" role="alert">{error}</p>;
  }
  if (!overview) {
    return <p className="rounded-lg border border-white/10 bg-ink-950/82 p-5 text-sm text-slate-300">正在汇总 Provider、目录与运行准备度…</p>;
  }

  const metrics = [
    { label: "Provider", value: `${overview.online_provider_count}/${overview.provider_count}`, hint: "已连通 / 已配置", icon: Server },
    { label: "运行时模型", value: overview.discovered_model_count, hint: `${overview.stale_model_count} 个目录证据过期`, icon: Database },
    { label: "可调用 operation", value: overview.operation_counts.reduce((sum, item) => sum + item.invocable, 0), hint: "按模型与 operation 计数", icon: Activity },
  ];

  const runtimePath = runtimeEnvironment?.llm_gateway_configured
    ? { value: "LLM Gateway 已配置", hint: "普通 /api/chat 当前仍读取 LLM_GATEWAY_URL / KEY；R4 不改变该路由。", ready: true }
    : runtimeEnvironment?.openrouter_configured
      ? { value: "OpenRouter 环境回退", hint: "普通 /api/chat 当前仍读取 OPENROUTER_API_KEY；R4 不改变该路由。", ready: true }
      : { value: runtimeEnvironmentUnavailable ? "状态读取失败" : "尚未配置", hint: runtimeEnvironmentUnavailable ? "无法读取脱敏环境状态；Provider 控制面仍可独立使用。" : "受管 Provider 只形成控制面证据，不会在 R4 自动接管 /api/chat；Chat 迁移属于 Round 5。", ready: false };

  return (
    <div className="space-y-5">
      <div className="grid gap-4 md:grid-cols-3">
        {metrics.map(({ label, value, hint, icon: Icon }) => (
          <article className="rounded-lg border border-white/10 bg-ink-950/82 p-5" key={label}>
            <div className="flex items-center gap-2 text-hire-100"><Icon className="h-4 w-4" /><span className="text-xs font-semibold uppercase tracking-[0.18em]">{label}</span></div>
            <p className="mt-3 text-3xl font-semibold text-white">{value}</p>
            <p className="mt-1 text-xs text-slate-400">{hint}</p>
          </article>
        ))}
      </div>

      <section className={`rounded-lg border p-5 ${runtimePath.ready ? "border-emerald-300/20 bg-emerald-300/10" : "border-amber-300/20 bg-amber-300/10"}`}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className={`text-xs font-semibold uppercase tracking-[0.18em] ${runtimePath.ready ? "text-emerald-100" : "text-amber-100"}`}>当前普通 Chat 环境路径</p>
            <h2 className="mt-2 text-lg font-semibold text-white">{runtimePath.value}</h2>
            <p className="mt-1 text-sm leading-6 text-slate-300">{runtimePath.hint}</p>
          </div>
          <span className="rounded-full border border-white/10 bg-white/[0.045] px-3 py-1 text-xs text-slate-200">与受管 Provider 分离</span>
        </div>
      </section>

      <section className="rounded-lg border border-white/10 bg-ink-950/82 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-white">逐 operation 准备度</h2>
            <p className="mt-1 text-sm text-slate-400">可发现、可连接、已认证与可试运行分别计算；本页不触发刷新或模型调用。</p>
          </div>
          <span className="rounded-full border border-amber-300/25 bg-amber-300/10 px-3 py-1 text-xs text-amber-100">默认数据面资格未评估</span>
        </div>
        {overview.operation_counts.length ? (
          <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {overview.operation_counts.map((item) => (
              <div className="rounded-lg border border-white/10 bg-white/[0.035] p-4" key={item.operation}>
                <p className="font-medium text-white">{OPERATION_LABELS[item.operation] ?? item.operation}</p>
                <p className="mt-2 text-sm text-slate-300"><span className="text-emerald-200">{item.invocable} 可调用</span> · {item.blocked} 阻塞 · {item.stale} 过期</p>
              </div>
            ))}
          </div>
        ) : <p className="mt-4 text-sm text-slate-400">人工刷新 Provider 目录后，这里会出现逐 operation 证据。</p>}
      </section>

      {overview.blocking_reason_codes.length ? (
        <section className="rounded-lg border border-amber-300/20 bg-amber-300/10 p-5">
          <div className="flex items-center gap-2 text-amber-100"><CircleAlert className="h-4 w-4" /><h2 className="font-semibold">当前阻塞项</h2></div>
          <ul className="mt-3 space-y-1 text-sm text-amber-50/80">
            {overview.blocking_reason_codes.map((reason) => <li key={reason}>• {REASON_LABELS[reason] ?? reason}</li>)}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
