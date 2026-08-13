import {
  BookOpenText,
  ChevronRight,
  Code2,
  Settings,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { Link } from "react-router-dom";

interface WorkbenchEntry {
  accent: "amber" | "cyan" | "neutral";
  badge: string;
  description: string;
  href: string;
  icon: LucideIcon;
  title: string;
}

const workbenchEntries: WorkbenchEntry[] = [
  {
    accent: "amber",
    badge: "构建",
    description: "拖拽节点，编排并运行任务",
    href: "/workflow",
    icon: Workflow,
    title: "自定义工作流",
  },
  {
    accent: "cyan",
    badge: "知识",
    description: "上传资料，检索并用于问答",
    href: "/rag",
    icon: BookOpenText,
    title: "RAG 知识库",
  },
  {
    accent: "cyan",
    badge: "开发",
    description: "连接项目，完成代码任务",
    href: "/coding",
    icon: Code2,
    title: "Coding",
  },
  {
    accent: "neutral",
    badge: "管理",
    description: "管理连接、网关与功能开关",
    href: "/settings",
    icon: Settings,
    title: "系统设置",
  },
];

const entryTone: Record<WorkbenchEntry["accent"], string> = {
  amber:
    "border-hire-300/35 bg-[linear-gradient(105deg,rgba(251,146,60,0.18),rgba(15,23,42,0.72))] hover:border-hire-200/65 hover:bg-hire-300/20",
  cyan:
    "border-cyan-300/15 bg-white/[0.035] hover:border-cyan-300/35 hover:bg-cyan-300/[0.07]",
  neutral:
    "border-white/10 bg-white/[0.035] hover:border-white/25 hover:bg-white/[0.07]",
};

const iconTone: Record<WorkbenchEntry["accent"], string> = {
  amber: "border-hire-300/30 bg-hire-300/10 text-hire-100",
  cyan: "border-cyan-300/20 bg-cyan-300/[0.07] text-cyan-100",
  neutral: "border-white/15 bg-white/[0.055] text-slate-200",
};

function WorkbenchLinks() {
  return (
    <div className="mt-4 space-y-2.5">
      {workbenchEntries.map((entry) => {
        const Icon = entry.icon;

        return (
          <Link
            className={`group grid min-h-[88px] grid-cols-[36px_minmax(0,1fr)_auto] items-center gap-x-2 rounded-lg border px-2 py-3 transition duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-200/80 ${entryTone[entry.accent]}`}
            key={entry.href}
            to={entry.href}
          >
            <span
              className={`flex h-9 w-9 items-center justify-center rounded-md border ${iconTone[entry.accent]}`}
            >
              <Icon aria-hidden="true" size={19} strokeWidth={1.8} />
            </span>
            <span className="min-w-0">
              <span className="block whitespace-nowrap text-[13px] font-semibold text-white">
                {entry.title}
              </span>
              <span className="mt-1 block text-[11px] leading-4 text-slate-400">
                {entry.description}
              </span>
            </span>
            <span className="flex items-center gap-0.5 text-hire-100">
              <span className="rounded-full border border-white/10 bg-ink-950/35 px-1 py-0.5 text-[10px] font-medium text-slate-300">
                {entry.badge}
              </span>
              <ChevronRight
                aria-hidden="true"
                className="transition-transform duration-200 group-hover:translate-x-0.5"
                size={14}
              />
            </span>
          </Link>
        );
      })}
    </div>
  );
}

export default function ModelWorkbenchSidebar({
  compact = false,
}: {
  compact?: boolean;
}) {
  if (compact) {
    return (
      <details className="surface-panel rounded-lg p-4">
        <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 text-sm font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-200/80">
          <span>工作台入口</span>
          <span className="text-xs font-medium text-slate-400">4 个入口</span>
        </summary>
        <p className="mt-2 text-xs leading-5 text-slate-400">
          继续构建、检索与管理 AI 能力。
        </p>
        <WorkbenchLinks />
      </details>
    );
  }

  return (
    <section aria-label="工作台入口">
      <h2 className="text-base font-semibold text-white">工作台入口</h2>
      <p className="mt-1.5 text-xs leading-5 text-slate-400">
        继续构建、检索与管理 AI 能力。
      </p>
      <WorkbenchLinks />
    </section>
  );
}
