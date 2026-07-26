import { useEffect, useState } from "react";
import PageContainer from "../components/PageContainer";

interface PluginVersion {
  version: number;
  prompts: unknown[];
  skills: unknown[];
  toolsets: unknown[];
  middleware_presets: unknown[];
  published_at: number;
}

interface PluginItem {
  id: string;
  name: string;
  slug: string;
  description: string;
  tags: string[];
  license: string;
  status: "draft" | "published" | "archived";
  draft_revision: number;
  published_version: number | null;
  manifest: Record<string, unknown>;
  file_count: number;
  total_bytes: number;
  versions: PluginVersion[];
}

function detail(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const value = (payload as { detail?: unknown }).detail;
    return typeof value === "string" ? value : JSON.stringify(value);
  }
  return fallback;
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(detail(payload, `请求失败：${response.status}`));
  return payload as T;
}

export default function PluginsPage() {
  const [items, setItems] = useState<PluginItem[]>([]);
  const [selected, setSelected] = useState<PluginItem | null>(null);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  async function load(selectedId?: string) {
    const payload = await json<{ items: PluginItem[] }>("/api/plugins?limit=200");
    setItems(payload.items);
    const id = selectedId || selected?.id || payload.items[0]?.id;
    setSelected(payload.items.find((item) => item.id === id) ?? null);
  }

  useEffect(() => {
    void load().catch((caught) => setError(caught instanceof Error ? caught.message : "Plugin 加载失败"));
  }, []);

  async function upload(file?: File) {
    if (!file) return;
    setBusy("import");
    setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      const created = await json<PluginItem>("/api/plugins/import", { method: "POST", body: form });
      await load(created.id);
      setNotice("Plugin ZIP 已导入为草稿。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "导入失败");
    } finally {
      setBusy("");
    }
  }

  async function save() {
    if (!selected) return;
    setBusy("save");
    setError("");
    try {
      const updated = await json<PluginItem>(`/api/plugins/${selected.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          revision: selected.draft_revision,
          name: selected.name,
          description: selected.description,
          tags: selected.tags,
          license: selected.license,
        }),
      });
      await load(updated.id);
      setNotice("Plugin 元数据已保存。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存失败");
    } finally {
      setBusy("");
    }
  }

  async function action(name: "validate" | "publish" | "archive") {
    if (!selected) return;
    setBusy(name);
    setError("");
    try {
      const body = name === "validate" ? undefined : JSON.stringify({ revision: selected.draft_revision });
      const result = await json<Record<string, unknown>>(`/api/plugins/${selected.id}/${name}`, {
        method: "POST",
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body,
      });
      if (name === "validate" && result.valid === false) {
        setNotice(`校验未通过：${JSON.stringify(result.issues ?? [])}`);
      } else {
        setNotice(name === "validate" ? "Plugin 校验通过。" : name === "publish" ? "不可变 Plugin 版本已发布。" : "Plugin 已归档。");
      }
      await load(selected.id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "操作失败");
    } finally {
      setBusy("");
    }
  }

  const manifest = selected?.manifest ?? {};
  const count = (key: string) => Array.isArray(manifest[key]) ? manifest[key].length : 0;

  return (
    <PageContainer activeResource="prompts" hideSidebar maxWidthClassName="max-w-[1560px]">
      <header className="mb-5 flex flex-col gap-3 border-b border-white/10 pb-5 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-violet-200">Declarative Package</p>
          <h1 className="mt-2 text-2xl font-semibold text-white">Plugin 工作台</h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-400">聚合 Prompt、Skill、固定 Toolset 与中间件预设。Plugin 不加载服务端动态代码。</p>
        </div>
        <label className="cursor-pointer rounded-md bg-violet-300 px-4 py-2 text-sm font-semibold text-slate-950">
          {busy === "import" ? "导入中..." : "导入 Plugin ZIP"}
          <input accept=".zip,application/zip" className="hidden" disabled={Boolean(busy)} onChange={(event) => void upload(event.target.files?.[0])} type="file" />
        </label>
      </header>

      {error || notice ? <p className={`mb-4 rounded-md border px-3 py-2 text-xs ${error ? "border-rose-300/30 bg-rose-300/10 text-rose-100" : "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"}`}>{error || notice}</p> : null}

      <div className="grid gap-5 xl:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
          <div className="mb-3 flex justify-between text-xs text-slate-400"><span>本地 Plugin</span><span>{items.length}</span></div>
          <div className="space-y-2">
            {items.map((item) => (
              <button className={`w-full rounded-md border p-3 text-left ${selected?.id === item.id ? "border-violet-300/45 bg-violet-300/10" : "border-white/10 bg-slate-950/45 hover:border-white/20"}`} key={item.id} onClick={() => setSelected(item)} type="button">
                <div className="flex items-center justify-between gap-2"><span className="truncate text-sm font-semibold text-white">{item.name}</span><span className="text-[10px] text-slate-500">{item.published_version ? `v${item.published_version}` : item.status}</span></div>
                <p className="mt-1 truncate text-xs text-slate-500">{item.slug} · {item.license || "license 未声明"}</p>
              </button>
            ))}
            {!items.length ? <p className="rounded-md border border-dashed border-white/10 p-5 text-center text-xs text-slate-500">导入包含 modelmirror-plugin.json 的 ZIP</p> : null}
          </div>
        </aside>

        {selected ? (
          <section className="space-y-4 rounded-lg border border-white/10 bg-slate-950/55 p-4">
            <div className="grid gap-3 md:grid-cols-2">
              <label className="text-xs text-slate-400">名称<input className="mt-1 h-10 w-full rounded-md border border-white/10 bg-white/[0.04] px-3 text-sm text-white" value={selected.name} onChange={(event) => setSelected({ ...selected, name: event.target.value })} /></label>
              <label className="text-xs text-slate-400">License<input className="mt-1 h-10 w-full rounded-md border border-white/10 bg-white/[0.04] px-3 text-sm text-white" value={selected.license} onChange={(event) => setSelected({ ...selected, license: event.target.value })} /></label>
            </div>
            <label className="block text-xs text-slate-400">描述<textarea className="mt-1 min-h-20 w-full rounded-md border border-white/10 bg-white/[0.04] p-3 text-sm text-white" value={selected.description} onChange={(event) => setSelected({ ...selected, description: event.target.value })} /></label>
            <label className="block text-xs text-slate-400">标签<input className="mt-1 h-10 w-full rounded-md border border-white/10 bg-white/[0.04] px-3 text-sm text-white" value={selected.tags.join(", ")} onChange={(event) => setSelected({ ...selected, tags: event.target.value.split(",").map((value) => value.trim()).filter(Boolean) })} /></label>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {[["Prompt", count("prompts")], ["Skill", count("skills")], ["Toolset", count("toolsets")], ["Middleware", count("middleware_presets")]].map(([label, value]) => (
                <div className="rounded-md border border-white/10 bg-white/[0.035] p-3" key={String(label)}><p className="text-xs text-slate-500">{label}</p><p className="mt-1 text-xl font-semibold text-white">{value}</p></div>
              ))}
            </div>
            <div className="rounded-md border border-white/10 bg-black/20 p-3">
              <p className="text-xs font-semibold text-slate-300">安全清单</p>
              <p className="mt-2 text-xs leading-5 text-slate-500">{selected.file_count} files · {(selected.total_bytes / 1024).toFixed(1)} KB · 不包含凭据 · 不加载 Python/Node 后端模块</p>
            </div>
            <div className="border-t border-white/10 pt-4">
              <div className="mb-2 flex items-center justify-between">
                <p className="text-xs font-semibold text-slate-300">版本历史</p>
                <span className="text-[11px] text-slate-500">{selected.versions.length} 个不可变版本</span>
              </div>
              {selected.versions.length ? (
                <div className="divide-y divide-white/10 border-y border-white/10">
                  {[...selected.versions].reverse().map((version) => (
                    <div className="flex flex-wrap items-center justify-between gap-2 py-2 text-xs" key={version.version}>
                      <span className="font-semibold text-white">v{version.version}</span>
                      <span className="text-slate-400">
                        {version.prompts.length} Prompt · {version.skills.length} Skill · {version.toolsets.length} Toolset
                      </span>
                      <span className="text-slate-500">{new Date(version.published_at).toLocaleString()}</span>
                    </div>
                  ))}
                </div>
              ) : <p className="text-xs text-slate-500">校验依赖并发布后，这里会保留固定资源清单。</p>}
            </div>
            <div className="flex flex-wrap items-center gap-2 border-t border-white/10 pt-4">
              <button className="rounded-md border border-white/10 px-3 py-2 text-xs font-semibold text-white" disabled={Boolean(busy)} onClick={() => void save()} type="button">保存元数据</button>
              <button className="rounded-md border border-white/10 px-3 py-2 text-xs font-semibold text-white" disabled={Boolean(busy)} onClick={() => void action("validate")} type="button">校验依赖</button>
              <button className="rounded-md bg-emerald-300 px-3 py-2 text-xs font-semibold text-slate-950" disabled={Boolean(busy)} onClick={() => void action("publish")} type="button">发布版本</button>
              <button className="ml-auto rounded-md px-3 py-2 text-xs text-rose-200" disabled={Boolean(busy)} onClick={() => void action("archive")} type="button">归档</button>
              <span className="text-[11px] text-slate-500">revision {selected.draft_revision} · {selected.versions.length} versions</span>
            </div>
          </section>
        ) : null}
      </div>
    </PageContainer>
  );
}
