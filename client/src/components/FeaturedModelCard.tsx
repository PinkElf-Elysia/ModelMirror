import type { ReactNode } from "react";

interface FeaturedModelCardProps {
  badge: string;
  description: string;
  footerAction: ReactNode;
  inputLabels: string[];
  mark: ReactNode;
  name: string;
  providerLabel: string;
  providerMark?: ReactNode;
  pricingLabel: string;
  subtitle: string;
  taskLabels: string[];
  taskOverflow?: number;
  topAction: ReactNode;
}

export default function FeaturedModelCard({
  badge,
  description,
  footerAction,
  inputLabels,
  mark,
  name,
  providerLabel,
  providerMark,
  pricingLabel,
  subtitle,
  taskLabels,
  taskOverflow = 0,
  topAction,
}: FeaturedModelCardProps) {
  return (
    <article
      className="group relative isolate flex h-full min-h-[340px] flex-col overflow-hidden rounded-lg border border-hire-300/55 bg-[linear-gradient(145deg,rgba(49,23,21,0.96),rgba(7,15,31,0.98)_42%,rgba(6,29,44,0.94))] shadow-[0_8px_8px_rgba(0,0,0,0.28)] transition duration-200 ease-out hover:-translate-y-1 hover:border-hire-200/80"
      data-featured-model-card="true"
    >
      <div className="pointer-events-none absolute inset-x-0 top-0 h-24 bg-[radial-gradient(circle_at_16%_0%,rgba(251,146,60,0.30),transparent_42%),linear-gradient(90deg,rgba(251,146,60,0.11),transparent_70%)]" />

      <div className="relative flex min-h-12 items-center justify-between gap-3 border-b border-hire-300/25 px-4 py-2.5">
        <span className="inline-flex min-h-7 items-center rounded-full border border-hire-200/45 bg-hire-300/15 px-3 text-xs font-semibold text-hire-50">
          {badge}
        </span>
        {topAction}
      </div>

      <div className="relative flex flex-1 flex-col px-4 py-4">
        <div className="flex items-center gap-3">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-white/15 bg-slate-950/65 text-white">
            {mark}
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="line-clamp-1 text-lg font-semibold leading-6 text-white">
                {name}
              </h2>
              <span className="rounded-md border border-hire-300/25 bg-hire-300/[0.08] px-2 py-0.5 text-[11px] font-semibold text-hire-100">
                官方
              </span>
            </div>
            <p className="mt-0.5 line-clamp-1 text-xs text-slate-300">{subtitle}</p>
          </div>
        </div>

        <p className="mt-3 line-clamp-2 text-sm leading-5 text-slate-300">
          {description}
        </p>

        <div className="mt-4 grid flex-1 grid-cols-[minmax(0,1.55fr)_minmax(8rem,1fr)] border-y border-white/10 py-3">
          <section className="min-w-0 pr-3" aria-label="可完成任务">
            <p className="text-xs font-medium text-slate-400">可完成任务</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {taskLabels.map((label) => (
                <span
                  className="inline-flex min-h-7 items-center rounded-md border border-violet-300/20 bg-violet-300/[0.07] px-2 text-xs text-violet-100"
                  key={label}
                >
                  {label}
                </span>
              ))}
              {taskOverflow > 0 ? (
                <span className="inline-flex min-h-7 items-center rounded-md border border-white/10 bg-white/[0.04] px-2 text-xs text-slate-300">
                  更多 +{taskOverflow}
                </span>
              ) : null}
            </div>
          </section>

          <section className="min-w-0 border-l border-white/10 pl-3" aria-label="可接收输入">
            <p className="text-xs font-medium text-slate-400">可接收输入</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {inputLabels.map((label) => (
                <span
                  className="inline-flex min-h-7 items-center rounded-md border border-cyan-300/20 bg-cyan-300/[0.07] px-2 text-xs text-cyan-100"
                  key={label}
                >
                  {label}
                </span>
              ))}
            </div>
          </section>
        </div>

        <div className="mt-3 flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2 text-xs text-slate-300">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center overflow-hidden rounded-md border border-white/10 bg-white/[0.055]">
              {providerMark ?? mark}
            </span>
            <span className="truncate">{providerLabel}</span>
            <span aria-hidden="true" className="text-slate-600">|</span>
            <span className="truncate text-slate-400">{pricingLabel}</span>
          </div>
          <div className="shrink-0">{footerAction}</div>
        </div>
      </div>
    </article>
  );
}
