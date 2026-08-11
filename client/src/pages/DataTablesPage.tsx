import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  Archive,
  ArrowRight,
  Database,
  Plus,
  RefreshCw,
  Search,
} from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import {
  AgentTableDefinition,
  AgentTableStatus,
  requestAgentTableJson,
} from "../types/agentTables";


const statusLabels: Record<AgentTableStatus, string> = {
  draft: "草稿",
  published: "已发布",
  archived: "已归档",
};


export default function DataTablesPage() {
  const navigate = useNavigate();
  const [tables, setTables] = useState<AgentTableDefinition[]>([]);
  const [query, setQuery] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [status, setStatus] = useState<"all" | AgentTableStatus>("all");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    try {
      const suffix = status === "all" ? "" : `?status=${status}`;
      const payload = await requestAgentTableJson<{
        items: AgentTableDefinition[];
      }>(`/api/data-tables${suffix}`);
      setTables(payload.items ?? []);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "数据表加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [status]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return tables;
    return tables.filter((table) =>
      `${table.name} ${table.description}`.toLocaleLowerCase().includes(needle),
    );
  }, [query, tables]);

  async function createTable(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      const table = await requestAgentTableJson<AgentTableDefinition>(
        "/api/data-tables",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: name.trim(),
            description: description.trim(),
            fields: [],
          }),
        },
      );
      navigate(`/data-tables/${table.table_id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建数据表失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageContainer>
      <div className="mx-auto w-full max-w-[1400px]">
        <header className="border-b border-white/10 pb-5">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <div className="flex items-center gap-2 text-xs font-semibold text-emerald-200">
                <Database aria-hidden="true" size={15} />
                Native Agent Table
              </div>
              <h1 className="mt-2 text-2xl font-semibold text-white">本地托管数据表</h1>
              <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-400">
                为私有工作流维护类型化业务记录。Schema 发布为不可变版本，数据仍只保存一份。
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Link
                className="rounded-md border border-white/10 px-3 py-2 text-sm font-semibold text-slate-200 hover:bg-white/5"
                to="/datax"
              >
                Data X 分析
              </Link>
              <Link
                className="rounded-md border border-white/10 px-3 py-2 text-sm font-semibold text-slate-200 hover:bg-white/5"
                to="/mcps"
              >
                外部数据库 MCP
              </Link>
              <button
                className="inline-flex h-10 w-10 items-center justify-center rounded-md border border-white/10 text-slate-200 hover:bg-white/5"
                onClick={() => void load()}
                title="刷新数据表"
                type="button"
              >
                <RefreshCw aria-hidden="true" size={17} />
              </button>
            </div>
          </div>
        </header>

        {error ? (
          <div className="mt-4 rounded-md border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-100" role="alert">
            {error}
          </div>
        ) : null}

        <div className="mt-6 grid gap-7 xl:grid-cols-[330px_minmax(0,1fr)]">
          <form className="self-start border-t border-white/10 pt-4" onSubmit={createTable}>
            <div className="flex items-center gap-2">
              <Plus aria-hidden="true" className="text-emerald-300" size={17} />
              <h2 className="text-sm font-semibold text-white">创建数据表</h2>
            </div>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              创建后进入 Schema 编辑页定义字段并发布首个版本。
            </p>
            <label className="mt-4 block text-xs font-semibold text-slate-300" htmlFor="agent-table-name">
              名称
            </label>
            <input
              className="mt-1 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none focus:border-emerald-300"
              id="agent-table-name"
              maxLength={160}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如：内容审核任务"
              value={name}
            />
            <label className="mt-3 block text-xs font-semibold text-slate-300" htmlFor="agent-table-description">
              说明
            </label>
            <textarea
              className="mt-1 min-h-24 w-full resize-y rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none focus:border-emerald-300"
              id="agent-table-description"
              maxLength={2000}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="记录表的用途和数据边界"
              value={description}
            />
            <button
              className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-md bg-emerald-300 px-3 py-2 text-sm font-semibold text-ink-950 hover:bg-emerald-200 disabled:opacity-50"
              disabled={busy || !name.trim()}
              type="submit"
            >
              <Plus aria-hidden="true" size={16} />
              {busy ? "创建中..." : "创建草稿"}
            </button>
          </form>

          <section>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-center gap-2">
                {(["all", "draft", "published", "archived"] as const).map((item) => (
                  <button
                    className={`rounded-md px-3 py-1.5 text-xs font-semibold ${
                      status === item
                        ? "bg-white text-ink-950"
                        : "border border-white/10 text-slate-300 hover:bg-white/5"
                    }`}
                    key={item}
                    onClick={() => setStatus(item)}
                    type="button"
                  >
                    {item === "all" ? "全部" : statusLabels[item]}
                  </button>
                ))}
              </div>
              <label className="relative block w-full sm:w-72">
                <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-2.5 text-slate-500" size={16} />
                <input
                  aria-label="搜索数据表"
                  className="w-full rounded-md border border-white/10 bg-white/[0.035] py-2 pl-9 pr-3 text-sm text-white outline-none focus:border-emerald-300"
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="搜索名称或说明"
                  value={query}
                />
              </label>
            </div>

            <div className="mt-3 grid gap-3 md:grid-cols-2">
              {loading ? (
                <div className="col-span-full py-12 text-center text-sm text-slate-500">正在加载数据表...</div>
              ) : null}
              {!loading
                ? filtered.map((table) => (
                    <Link
                      className="group rounded-lg border border-white/10 bg-white/[0.035] p-4 transition hover:border-emerald-300/35 hover:bg-emerald-300/[0.055]"
                      key={table.table_id}
                      to={`/data-tables/${table.table_id}`}
                    >
                      <div className="flex items-start justify-between gap-4">
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            {table.status === "archived" ? (
                              <Archive aria-hidden="true" className="text-slate-500" size={17} />
                            ) : (
                              <Database aria-hidden="true" className="text-emerald-300" size={17} />
                            )}
                            <h2 className="truncate text-sm font-semibold text-white">{table.name}</h2>
                          </div>
                          <p className="mt-2 line-clamp-2 min-h-10 text-xs leading-5 text-slate-500">
                            {table.description || "尚未添加数据表说明"}
                          </p>
                        </div>
                        <ArrowRight aria-hidden="true" className="shrink-0 text-slate-600 transition group-hover:translate-x-0.5 group-hover:text-emerald-200" size={18} />
                      </div>
                      <div className="mt-4 flex flex-wrap items-center gap-2 text-[11px]">
                        <span className="rounded-full bg-white/5 px-2 py-1 text-slate-300">{statusLabels[table.status]}</span>
                        <span className="text-slate-500">{table.fields.length} 个字段</span>
                        <span className="text-slate-500">
                          {table.active_schema_version ? `Schema v${table.active_schema_version}` : "尚未发布"}
                        </span>
                      </div>
                    </Link>
                  ))
                : null}
              {!loading && !filtered.length ? (
                <div className="col-span-full border-y border-white/10 py-14 text-center">
                  <p className="text-sm font-semibold text-slate-300">没有匹配的数据表</p>
                  <p className="mt-1 text-xs text-slate-500">创建草稿后定义字段并发布 Schema。</p>
                </div>
              ) : null}
            </div>
          </section>
        </div>
      </div>
    </PageContainer>
  );
}

