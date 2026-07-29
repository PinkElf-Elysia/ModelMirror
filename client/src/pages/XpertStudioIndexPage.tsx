import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import AuthoringProposalPanel from "../components/authoring/AuthoringProposalPanel";
import { type XpertStatus, type XpertSummary } from "../types/xpert";
import { listXperts } from "../utils/xpertApi";

const statusOptions: Array<{ id: XpertStatus | "all"; label: string }> = [
  { id: "all", label: "全部" },
  { id: "draft", label: "草稿" },
  { id: "published", label: "已发布" },
  { id: "archived", label: "已归档" },
];

function formatDate(value: number) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value * 1000));
}

function statusCopy(status: XpertStatus) {
  if (status === "published") return "已发布";
  if (status === "archived") return "已归档";
  return "草稿";
}

function statusClass(status: XpertStatus) {
  if (status === "published") {
    return "border-emerald-300/25 bg-emerald-300/10 text-emerald-100";
  }
  if (status === "archived") {
    return "border-white/10 bg-white/[0.04] text-slate-400";
  }
  return "border-hire-300/25 bg-hire-300/10 text-hire-100";
}

function XpertCard({ xpert }: { xpert: XpertSummary }) {
  return (
    <article className="rounded-lg border border-white/10 bg-ink-950/72 p-4 transition hover:border-hire-300/35 hover:bg-surface-900/88">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-hire-300/25 bg-hire-300/10 text-xs font-bold text-hire-100">
              XP
            </span>
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold text-white">{xpert.name}</h2>
              <p className="truncate text-xs text-slate-500">/{xpert.slug}</p>
            </div>
          </div>
        </div>
        <span className={`shrink-0 rounded-full border px-2.5 py-1 text-[11px] font-semibold ${statusClass(xpert.status)}`}>
          {statusCopy(xpert.status)}
        </span>
      </div>

      <p className="mt-4 line-clamp-3 min-h-[3.75rem] text-xs leading-5 text-slate-400">
        {xpert.description || "尚未填写说明。进入 Studio 配置工作流、工具和知识能力。"}
      </p>

      <div className="mt-3 flex min-h-6 flex-wrap gap-1.5">
        {xpert.tags.length > 0 ? (
          xpert.tags.slice(0, 4).map((tag) => (
            <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[11px] text-slate-400" key={tag}>
              {tag}
            </span>
          ))
        ) : (
          <span className="text-[11px] text-slate-600">暂无标签</span>
        )}
      </div>

      <dl className="mt-4 grid grid-cols-3 gap-2 border-y border-white/10 py-3 text-center">
        <div>
          <dt className="text-[10px] text-slate-500">草稿 revision</dt>
          <dd className="mt-1 text-sm font-semibold text-white">{xpert.draft_revision}</dd>
        </div>
        <div>
          <dt className="text-[10px] text-slate-500">当前版本</dt>
          <dd className="mt-1 text-sm font-semibold text-white">
            {xpert.published_version ? `v${xpert.published_version}` : "-"}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] text-slate-500">版本数</dt>
          <dd className="mt-1 text-sm font-semibold text-white">{xpert.version_count}</dd>
        </div>
      </dl>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
        <span className="text-[11px] text-slate-500">更新于 {formatDate(xpert.updated_at)}</span>
        <div className="flex gap-2">
          {xpert.published_version ? (
            <Link
              className="rounded-full border border-emerald-300/25 bg-emerald-300/10 px-3 py-1.5 text-xs font-semibold text-emerald-100 transition hover:bg-emerald-300/20"
              to={`/agents/xpert/${xpert.id}/chat`}
            >
              运行
            </Link>
          ) : null}
          <Link
            className="rounded-full bg-hire-300 px-3 py-1.5 text-xs font-semibold text-ink-950 transition hover:bg-hire-200"
            to={`/agents/studio/${xpert.id}`}
          >
            编辑
          </Link>
        </div>
      </div>
    </article>
  );
}

export default function XpertStudioIndexPage() {
  const [items, setItems] = useState<XpertSummary[]>([]);
  const [status, setStatus] = useState<XpertStatus | "all">("all");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    document.title = "模镜 - Agent Studio";
    let cancelled = false;
    setLoading(true);
    listXperts({ limit: 200 })
      .then((result) => {
        if (!cancelled) {
          setItems(result.items);
          setError("");
        }
      })
      .catch((caught) => {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "智能体列表加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    return items.filter((item) => {
      const matchesStatus = status === "all" || item.status === status;
      const matchesSearch =
        !term ||
        [item.name, item.slug, item.description, ...item.tags]
          .join(" ")
          .toLowerCase()
          .includes(term);
      return matchesStatus && matchesSearch;
    });
  }, [items, search, status]);

  return (
    <PageContainer activeResource="agents" maxWidthClassName="max-w-[1680px]">
      <header className="mb-6 border-y border-hire-300/20 py-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full border border-hire-300/30 bg-hire-300/10 px-3 py-1 text-xs font-semibold text-hire-100">
                Agent Studio Beta
              </span>
              <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs text-slate-400">
                草稿与发布版本隔离
              </span>
            </div>
            <h1 className="mt-3 text-2xl font-semibold text-white sm:text-3xl">我的智能体</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
              用同一套工作流内核配置模型、Toolset、知识和 Handoff，发布后获得稳定的版本化聊天入口。
            </p>
          </div>
          <Link
            className="w-fit rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950 transition hover:bg-hire-200"
            to="/agents/studio/new"
          >
            + 创建智能体
          </Link>
        </div>
      </header>

      <section className="mb-5 grid gap-3 rounded-lg border border-white/10 bg-white/[0.045] p-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
        <label className="relative block">
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-xs font-semibold text-slate-500">搜索</span>
          <input
            className="h-11 w-full rounded-lg border border-white/10 bg-ink-950/72 pl-12 pr-3 text-sm text-white outline-none transition placeholder:text-slate-600 focus:border-hire-300/60 focus:ring-4 focus:ring-hire-300/10"
            onChange={(event) => setSearch(event.target.value)}
            placeholder="名称、slug、说明或标签"
            type="search"
            value={search}
          />
        </label>
        <div className="flex flex-wrap gap-2">
          {statusOptions.map((option) => (
            <button
              className={`rounded-full border px-3 py-2 text-xs font-semibold transition ${
                status === option.id
                  ? "border-hire-300 bg-hire-300 text-ink-950"
                  : "border-white/10 bg-white/[0.04] text-slate-400 hover:border-hire-300/30 hover:text-hire-100"
              }`}
              key={option.id}
              onClick={() => setStatus(option.id)}
              type="button"
            >
              {option.label}
            </button>
          ))}
        </div>
      </section>

      <AuthoringProposalPanel
        kindPrefix="xpert"
        onApplied={() => {
          void listXperts({ limit: 200 }).then((result) => setItems(result.items));
        }}
        title="智能体自编写提案"
      />

      {loading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((item) => (
            <div className="h-72 animate-pulse rounded-lg border border-white/10 bg-white/[0.04]" key={item} />
          ))}
        </div>
      ) : error ? (
        <div className="rounded-lg border border-rose-300/25 bg-rose-300/10 p-5 text-sm text-rose-100">{error}</div>
      ) : filtered.length > 0 ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {filtered.map((xpert) => <XpertCard key={xpert.id} xpert={xpert} />)}
        </div>
      ) : (
        <div className="rounded-lg border border-white/10 bg-white/[0.04] p-10 text-center">
          <p className="text-sm font-semibold text-white">没有匹配的智能体</p>
          <p className="mt-2 text-sm text-slate-400">创建一个默认工作流，或调整当前搜索和状态筛选。</p>
          <Link className="mt-4 inline-flex rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950" to="/agents/studio/new">
            创建第一个智能体
          </Link>
        </div>
      )}
    </PageContainer>
  );
}
