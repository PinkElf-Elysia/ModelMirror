import { Link } from "react-router-dom";

interface CapabilityItem {
  icon: string;
  title: string;
  description: string;
  badge: string;
  href?: string;
  actionLabel?: string;
}

const capabilities: CapabilityItem[] = [
  {
    icon: "🛠️",
    title: "工作流（经典自研）",
    description: "默认使用模镜本地的 React Flow 画布，支持节点拖拽、静态图校验和本地运行。",
    badge: "更多",
    href: "/workflow",
    actionLabel: "进入工作流",
  },
  {
    icon: "元",
    title: "元智能体 Beta",
    description: "从自然语言目标生成 Agent 工作流草稿，并导入经典画布试运行。",
    badge: "Beta",
    href: "/agents/meta-agent",
    actionLabel: "打开工作台",
  },
  {
    icon: "📚",
    title: "RAG 资料库",
    description: "通过本地知识库提供文档上传、切分、检索测试和 RAG 问答。",
    badge: "稳定接入",
    href: "/rag",
    actionLabel: "打开资料库",
  },
  {
    icon: "🔌",
    title: "系统设置",
    description: "管理模型服务连接、网关状态和本地运行配置。",
    badge: "可配置",
    href: "/settings",
    actionLabel: "进入设置",
  },
];

interface SystemCapabilityBarProps {
  compact?: boolean;
}

function CapabilityBadge({ badge }: { badge: string }) {
  return (
    <span className="rounded-full border border-white/10 bg-white/[0.055] px-2 py-0.5 text-[11px] font-medium text-slate-300">
      {badge}
    </span>
  );
}

function CapabilityCard({ item }: { item: CapabilityItem }) {
  const content = (
    <div className="rounded-lg border border-white/10 bg-white/[0.045] p-3 transition duration-200 hover:border-hire-300/30 hover:bg-hire-300/10">
      <div className="flex items-center gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-hire-300/25 bg-hire-300/10 text-sm font-semibold text-hire-100">
          {item.icon}
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-white">{item.title}</p>
          <div className="mt-1">
            <CapabilityBadge badge={item.badge} />
          </div>
        </div>
      </div>
      <p className="mt-3 text-xs leading-5 text-slate-400">{item.description}</p>
      {item.href && item.actionLabel ? (
        <span className="mt-3 inline-flex rounded-full border border-hire-300/25 bg-hire-300/10 px-2.5 py-1 text-xs font-semibold text-hire-100">
          {item.actionLabel}
        </span>
      ) : null}
    </div>
  );

  if (item.href) {
    return (
      <Link className="block" to={item.href}>
        {content}
      </Link>
    );
  }

  return content;
}

export default function SystemCapabilityBar({
  compact = false,
}: SystemCapabilityBarProps) {
  if (compact) {
    return (
      <details className="surface-panel rounded-lg p-4 xl:hidden">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-sm font-semibold text-white">
          <span>服务台 · 更多能力</span>
          <span className="rounded-full border border-hire-300/30 bg-hire-300/10 px-2.5 py-1 text-xs text-hire-100">
            自研路径
          </span>
        </summary>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          {capabilities.map((item) => (
            <CapabilityCard item={item} key={item.title} />
          ))}
        </div>
      </details>
    );
  }

  return (
    <section>
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-white">模镜服务台</p>
          <p className="mt-1 text-xs leading-5 text-slate-400">
            工作流默认使用经典自研画布，RAG本地知识库已开放，其他能力按模块持续扩展。
          </p>
        </div>
        <span className="rounded-full border border-hire-300/30 bg-hire-300/10 px-2.5 py-1 text-xs font-semibold text-hire-100">
          更多
        </span>
      </div>

      <div className="mt-4 space-y-3">
        {capabilities.map((item) => (
          <CapabilityCard item={item} key={item.title} />
        ))}
      </div>
    </section>
  );
}
