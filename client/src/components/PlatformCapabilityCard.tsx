import {
  AppWindow,
  Bot,
  CalendarClock,
  ChartPie,
  ChevronRight,
  Layers3,
  Rocket,
  Settings2,
  UsersRound,
  Workflow,
  type LucideIcon,
} from "lucide-react";

export interface PlatformCapability {
  id: string;
  icon: string;
  title: string;
  summary: string;
  detail: string;
  tag: string;
  eta: string;
  statusLabel?: string;
  actionLabel?: string;
}

interface PlatformCapabilityCardProps {
  capability: PlatformCapability;
  featured?: boolean;
  onOpen: (capability: PlatformCapability) => void;
}

const capabilityIcons: Record<string, LucideIcon> = {
  "agent-workspace": AppWindow,
  "conversation-goals": CalendarClock,
  datax: ChartPie,
  "expert-squad": UsersRound,
  "meta-agent": Workflow,
  "xpert-automations": Settings2,
  "xpert-studio": Layers3,
};

function CapabilityIcon({
  capability,
  featured,
}: {
  capability: PlatformCapability;
  featured: boolean;
}) {
  const Icon = capabilityIcons[capability.id] ?? Bot;

  if (featured) {
    return (
      <span className="relative flex h-[76px] w-[76px] shrink-0 items-center justify-center rounded-2xl border border-hire-200/45 bg-[linear-gradient(145deg,rgba(251,146,60,0.22),rgba(8,17,36,0.86))] text-hire-100 shadow-[0_16px_38px_rgba(3,8,22,0.36)]">
        <Layers3 aria-hidden="true" size={42} strokeWidth={1.7} />
        <span className="absolute -bottom-1 -right-1 flex h-8 w-8 items-center justify-center rounded-lg border border-hire-100/35 bg-ink-950 text-hire-100 shadow-lg">
          <Rocket aria-hidden="true" size={18} strokeWidth={1.8} />
        </span>
      </span>
    );
  }

  return (
    <span
      className="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl border border-cyan-200/25 bg-[linear-gradient(145deg,rgba(34,211,238,0.14),rgba(9,21,43,0.98))] text-cyan-100 shadow-[0_10px_26px_rgba(2,8,23,0.34)]"
    >
      <Icon aria-hidden="true" size={30} strokeWidth={1.7} />
    </span>
  );
}

export default function PlatformCapabilityCard({
  capability,
  featured = false,
  onOpen,
}: PlatformCapabilityCardProps) {
  if (featured) {
    return (
      <article className="group relative isolate flex h-full min-h-[316px] flex-col overflow-hidden rounded-xl border border-hire-300/45 bg-[linear-gradient(135deg,rgba(74,31,18,0.72),rgba(6,12,28,0.96)_58%)] p-6">
        <div className="pointer-events-none absolute inset-y-0 right-0 w-2/5 opacity-35 [background-image:linear-gradient(rgba(251,146,60,0.25)_1px,transparent_1px),linear-gradient(90deg,rgba(251,146,60,0.25)_1px,transparent_1px)] [background-size:32px_32px] [mask-image:linear-gradient(to_left,black,transparent)]" />
        <CapabilityIcon capability={capability} featured />

        <h3 className="relative mt-6 text-2xl font-semibold text-white">
          {capability.title}
        </h3>
        <p className="relative mt-3 max-w-[34ch] text-sm leading-6 text-slate-300">
          {capability.summary}
        </p>

        <span className="relative mt-4 w-fit rounded-md border border-emerald-300/20 bg-emerald-300/10 px-2.5 py-1 text-xs font-semibold text-emerald-100">
          {capability.tag}
        </span>

        <button
          className="relative mt-auto flex min-h-11 w-fit items-center gap-3 rounded-lg bg-hire-300 px-5 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-100 active:scale-[0.98]"
          onClick={() => onOpen(capability)}
          type="button"
        >
          {capability.actionLabel ?? "打开工作台"}
          <ChevronRight aria-hidden="true" size={16} />
        </button>
      </article>
    );
  }

  return (
    <article className="group relative flex min-h-[104px] items-center gap-4 overflow-hidden rounded-xl border border-slate-400/15 bg-[linear-gradient(135deg,rgba(7,17,36,0.98),rgba(5,11,27,0.99))] px-4 py-3 shadow-[0_14px_30px_rgba(2,6,18,0.2)] transition duration-200 hover:border-cyan-300/30 hover:bg-[linear-gradient(135deg,rgba(9,25,48,0.99),rgba(6,13,30,1))]">
      <span className="pointer-events-none absolute inset-y-0 left-0 w-28 bg-cyan-300/[0.025] [mask-image:linear-gradient(to_right,black,transparent)]" />
      <CapabilityIcon capability={capability} featured={false} />

      <div className="relative min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold text-white sm:text-base">
            {capability.title}
          </h3>
          <span
            className={`rounded-md border px-2 py-0.5 text-[11px] font-semibold ${
              capability.tag === "可用"
                ? "border-emerald-300/20 bg-emerald-300/10 text-emerald-100"
                : "border-violet-300/20 bg-violet-300/10 text-violet-200"
            }`}
          >
            {capability.tag}
          </span>
        </div>
        <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-300/80 sm:text-sm">
          {capability.summary}
        </p>
      </div>

      <button
        aria-label={`${capability.actionLabel ?? "打开"}${capability.title}`}
        className="relative flex min-h-11 shrink-0 items-center gap-1 rounded-md px-2 text-xs font-semibold text-hire-100 transition hover:bg-hire-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-100 sm:px-3 sm:text-sm"
        onClick={() => onOpen(capability)}
        type="button"
      >
        <span className="hidden sm:inline">
          {capability.actionLabel ?? "打开"}
        </span>
        <ChevronRight
          aria-hidden="true"
          className="transition-transform group-hover:translate-x-0.5"
          size={16}
        />
      </button>
    </article>
  );
}
