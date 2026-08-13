import { Link } from "react-router-dom";

interface ComingSoonProps {
  title: string;
  icon: string;
  description: string;
  actionHint: string;
}

export default function ComingSoon({
  title,
  icon,
  description,
  actionHint,
}: ComingSoonProps) {
  return (
    <section className="relative overflow-hidden border-y border-hire-300/20 py-10 sm:py-14 lg:py-16">
      <div className="absolute inset-x-8 top-0 h-20 rounded-b-[50%] border-x border-b border-hire-300/25 bg-[linear-gradient(180deg,rgba(251,146,60,0.16),transparent)]" />
      <div className="absolute left-0 top-0 h-px w-full animate-pulse-line bg-[linear-gradient(90deg,transparent,rgba(251,146,60,0.82),rgba(253,186,116,0.72),transparent)]" />

      <div className="relative grid min-w-0 gap-8 lg:grid-cols-[minmax(0,1fr)_360px] lg:items-center">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-hire-200">
            新展区正在布置
          </p>
          <h1 className="mt-4 max-w-4xl text-4xl font-semibold tracking-normal text-white sm:text-6xl">
            {title}
          </h1>
          <p className="mt-5 max-w-2xl text-base leading-7 text-slate-300">
            {description}
          </p>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-400">
            {actionHint}
          </p>

          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              className="rounded-full bg-hire-300 px-5 py-2.5 text-sm font-semibold text-ink-950 shadow-[0_0_24px_rgba(251,146,60,0.18)] transition duration-200 hover:bg-hire-200 active:scale-[0.98]"
              to="/models"
            >
              先逛模型招聘会
            </Link>
            <Link
              className="rounded-full border border-white/10 bg-white/[0.055] px-5 py-2.5 text-sm font-semibold text-slate-100 transition duration-200 hover:border-brand-300/35 hover:bg-brand-300/10 hover:text-brand-100 active:scale-[0.98]"
              to="/agents"
            >
              去 Agent人才市场
            </Link>
          </div>
        </div>

        <div className="surface-card relative min-h-72 overflow-hidden rounded-lg p-6">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_30%_20%,rgba(251,146,60,0.18),transparent_34%),radial-gradient(circle_at_80%_80%,rgba(36,217,255,0.12),transparent_38%)]" />
          <div className="relative flex h-full min-h-60 flex-col justify-between">
            <div className="flex items-center justify-between">
              <span className="rounded-full border border-hire-300/30 bg-hire-300/10 px-3 py-1 text-xs font-semibold text-hire-100">
                招牌制作中
              </span>
              <span className="text-xs text-slate-400">ModelMirror</span>
            </div>
            <div className="mx-auto flex h-28 w-28 items-center justify-center rounded-lg border border-hire-300/30 bg-hire-300/10 text-5xl font-black text-hire-100 shadow-[0_0_36px_rgba(251,146,60,0.16)]">
              {icon}
            </div>
            <div className="rounded-lg border border-white/10 bg-white/[0.045] p-3">
              <p className="text-xs text-slate-400">摊位状态</p>
              <p className="mt-1 text-sm font-semibold text-white">
                货架、简历夹和招工牌都在路上
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
