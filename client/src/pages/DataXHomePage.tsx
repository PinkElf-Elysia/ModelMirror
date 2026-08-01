import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import type { DataXProject } from "../types/datax";

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(String(payload.detail || `请求失败：${response.status}`));
  return payload as T;
}

export default function DataXHomePage() {
  const [projects, setProjects] = useState<DataXProject[]>([]);
  const [query, setQuery] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    try {
      const payload = await requestJson<{ items: DataXProject[] }>("/api/datax/projects");
      setProjects(payload.items);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Data X 项目加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return needle ? projects.filter((item) => `${item.name} ${item.description}`.toLocaleLowerCase().includes(needle)) : projects;
  }, [projects, query]);

  async function createProject(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      await requestJson("/api/datax/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), description: description.trim() }),
      });
      setName("");
      setDescription("");
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageContainer>
      <main className="mx-auto w-full max-w-[1500px] px-4 py-6 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-4 border-b border-white/10 pb-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="flex items-center gap-2 text-xs font-semibold text-cyan-200">
              <span className="h-2 w-2 rounded-full bg-cyan-300" />
              Data X 本地语义层
            </div>
            <h1 className="mt-2 text-2xl font-semibold text-white">指标项目</h1>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-400">导入文件快照，建立语义模型，并将经过审核的指标发布给智能体使用。</p>
          </div>
          <div className="flex items-center gap-2">
            <Link className="rounded-md border border-white/10 px-3 py-2 text-sm font-semibold text-slate-200 hover:bg-white/5" to="/studio">返回工作空间</Link>
            <button className="rounded-md bg-cyan-300 px-3 py-2 text-sm font-semibold text-slate-950 hover:bg-cyan-200" onClick={() => void load()} type="button">刷新项目</button>
          </div>
        </header>

        {error && <div className="mt-4 rounded-md bg-rose-500/10 px-3 py-2 text-sm text-rose-200" role="alert">{error}</div>}

        <div className="mt-6 grid gap-6 xl:grid-cols-[340px_minmax(0,1fr)]">
          <form className="self-start border-t border-white/10 pt-4" onSubmit={createProject}>
            <h2 className="text-sm font-semibold text-white">创建指标项目</h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">项目隔离 DuckDB 文件、源快照、语义模型和指标版本。</p>
            <label className="mt-4 block text-xs font-semibold text-slate-300">名称</label>
            <input className="mt-1 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none focus:border-cyan-300" maxLength={160} onChange={(event) => setName(event.target.value)} placeholder="销售分析" value={name} />
            <label className="mt-3 block text-xs font-semibold text-slate-300">说明</label>
            <textarea className="mt-1 min-h-24 w-full resize-y rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none focus:border-cyan-300" maxLength={1000} onChange={(event) => setDescription(event.target.value)} placeholder="数据范围和指标用途" value={description} />
            <button className="mt-3 w-full rounded-md bg-white px-3 py-2 text-sm font-semibold text-slate-950 hover:bg-slate-200 disabled:opacity-50" disabled={busy || !name.trim()} type="submit">{busy ? "创建中..." : "创建项目"}</button>
          </form>

          <section>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="text-sm text-slate-400">{projects.length} 个项目，{projects.filter((item) => item.status === "active").length} 个活动中</div>
              <input aria-label="搜索 Data X 项目" className="w-full rounded-md border border-white/10 bg-white/[0.035] px-3 py-2 text-sm text-white outline-none focus:border-cyan-300 sm:w-72" onChange={(event) => setQuery(event.target.value)} placeholder="搜索项目" value={query} />
            </div>
            <div className="mt-3 divide-y divide-white/10 border-y border-white/10">
              {loading ? <div className="py-10 text-center text-sm text-slate-500">正在加载项目...</div> : null}
              {!loading && filtered.map((project) => (
                <Link className="group grid gap-3 py-4 transition hover:bg-white/[0.025] sm:grid-cols-[minmax(0,1fr)_150px_36px] sm:items-center sm:px-3" key={project.project_id} to={`/datax/${project.project_id}`}>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <h2 className="truncate text-sm font-semibold text-white group-hover:text-cyan-100">{project.name}</h2>
                      <span className={`rounded-full px-2 py-0.5 text-[11px] ${project.status === "active" ? "bg-emerald-400/10 text-emerald-200" : "bg-white/5 text-slate-400"}`}>{project.status === "active" ? "活动" : "已归档"}</span>
                    </div>
                    <p className="mt-1 truncate text-xs text-slate-500">{project.description || "尚未添加项目说明"}</p>
                  </div>
                  <time className="text-xs text-slate-500">{new Date(project.updated_at * 1000).toLocaleString("zh-CN")}</time>
                  <span aria-hidden="true" className="text-right text-lg text-slate-500 group-hover:text-cyan-200">›</span>
                </Link>
              ))}
              {!loading && !filtered.length ? <div className="py-14 text-center"><p className="text-sm font-semibold text-slate-300">没有匹配的项目</p><p className="mt-1 text-xs text-slate-500">创建项目后即可导入 CSV、XLSX 或 Parquet。</p></div> : null}
            </div>
          </section>
        </div>
      </main>
    </PageContainer>
  );
}
