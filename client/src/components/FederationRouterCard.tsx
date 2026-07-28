import { Link } from "react-router-dom";

export const federationRouteId = "model-federation";
export const federationFallbackModelId = "openai/gpt-5.6-sol";

export default function FederationRouterCard() {
  return (
    <article className="group relative isolate flex h-full min-h-[340px] flex-col overflow-hidden rounded-lg border border-hire-200/55 bg-[linear-gradient(145deg,rgba(67,20,7,0.86),rgba(6,9,22,0.92)_48%,rgba(8,51,68,0.78))] p-0 shadow-[0_0_0_1px_rgba(253,186,116,0.28),0_22px_52px_rgba(124,45,18,0.34)] transition duration-300 ease-out hover:-translate-y-1 hover:border-hire-100/75 hover:shadow-[0_0_0_1px_rgba(253,186,116,0.42),0_24px_60px_rgba(251,146,60,0.24)]">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_24%_16%,rgba(253,186,116,0.25),transparent_34%),radial-gradient(circle_at_82%_86%,rgba(36,217,255,0.16),transparent_38%)] opacity-90" />
      <div className="relative border-b border-hire-200/25 bg-hire-300/15 px-5 py-4">
        <div className="flex items-center justify-between gap-3">
          <span className="rounded-full border border-hire-100/40 bg-hire-200/15 px-3 py-1 text-xs font-semibold text-hire-50">
            平台推荐
          </span>
          <span className="rounded-full border border-brand-300/25 bg-brand-300/10 px-3 py-1 text-xs font-medium text-brand-100">
            省钱路线
          </span>
        </div>
      </div>

      <div className="relative flex flex-1 flex-col p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-hire-200/35 bg-hire-200/10 px-2.5 py-1 text-xs font-semibold text-hire-100">
              <span className="h-1.5 w-1.5 rounded-full bg-hire-200" />
              我来自 ModelMirror 服务台
            </span>
            <h2 className="mt-4 text-xl font-semibold leading-7 text-white">
              模型联邦智能路由器
            </h2>
            <p className="mt-1 text-xs text-hire-100/80">
              候选人编号：model-federation
            </p>
          </div>
          <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-lg border border-hire-200/35 bg-hire-300/15 text-2xl font-black text-hire-50 shadow-[0_0_28px_rgba(251,146,60,0.2)]">
            联
          </div>
        </div>

        <p className="mt-5 text-sm leading-6 text-slate-200">
          不确定用哪个模型？交给我们的智能路由，自动判断你的问题，调度最合适的模型来回答，省钱又高效。
        </p>

        <div className="mt-5 grid grid-cols-3 gap-3 text-sm">
          <div className="rounded-lg border border-hire-200/20 bg-hire-200/10 p-3">
            <p className="text-[11px] text-hire-100/80">岗位</p>
            <p className="mt-1 font-semibold text-white">调度员</p>
          </div>
          <div className="rounded-lg border border-white/10 bg-white/[0.045] p-3">
            <p className="text-[11px] text-slate-400">薪资策略</p>
            <p className="mt-1 font-semibold text-lime-100">择优</p>
          </div>
          <div className="rounded-lg border border-white/10 bg-white/[0.045] p-3">
            <p className="text-[11px] text-slate-400">状态</p>
            <p className="mt-1 font-semibold text-hire-100">会诊室</p>
          </div>
        </div>

        <div className="mt-auto flex flex-wrap items-center gap-2 pt-5">
          <Link
            className="rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950 shadow-[0_0_24px_rgba(251,146,60,0.22)] transition duration-200 hover:bg-hire-200 active:scale-[0.98]"
            to="/chat/auto"
          >
            交给智能路由
          </Link>
          <span className="rounded-full border border-white/10 bg-white/[0.055] px-2.5 py-1 text-xs font-semibold text-slate-200">
            自动选择合适模型
          </span>
        </div>
      </div>
    </article>
  );
}
