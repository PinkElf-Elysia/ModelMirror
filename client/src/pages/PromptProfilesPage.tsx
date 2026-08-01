import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import AuthoringProposalPanel from "../components/authoring/AuthoringProposalPanel";
import PageContainer from "../components/PageContainer";

interface PromptVersion {
  version: number;
  name: string;
  aliases: string[];
  published_at: number;
}

interface PromptProfile {
  id: string;
  slug: string;
  name: string;
  description: string;
  aliases: string[];
  template: string;
  argument_hint: string;
  tags: string[];
  public_app_allowed: boolean;
  status: "draft" | "published" | "archived";
  draft_revision: number;
  published_version: number | null;
  versions: PromptVersion[];
}

function errorMessage(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    return typeof detail === "string" ? detail : JSON.stringify(detail);
  }
  return fallback;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(errorMessage(payload, `请求失败：${response.status}`));
  return payload as T;
}

function splitValues(value: string) {
  return value.split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
}

export default function PromptProfilesPage() {
  const [searchParams] = useSearchParams();
  const [items, setItems] = useState<PromptProfile[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [draft, setDraft] = useState<PromptProfile | null>(null);
  const [previewArgs, setPreviewArgs] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  async function load(selectId?: string) {
    const payload = await request<{ items: PromptProfile[] }>("/api/prompt-profiles?limit=200");
    setItems(payload.items);
    const requestedId = searchParams.get("profile_id") ?? "";
    const nextId = selectId || selectedId || requestedId || payload.items[0]?.id || "";
    setSelectedId(nextId);
    setDraft(payload.items.find((item) => item.id === nextId) ?? null);
  }

  useEffect(() => {
    void load().catch((caught) => setError(caught instanceof Error ? caught.message : "Prompt 加载失败"));
  }, []);

  async function createProfile() {
    setBusy("create");
    setError("");
    try {
      const created = await request<PromptProfile>("/api/prompt-profiles", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: "新 Prompt Command",
          aliases: ["command"],
          template: "{{args}}",
        }),
      });
      await load(created.id);
      setNotice("Prompt 草稿已创建。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "创建失败");
    } finally {
      setBusy("");
    }
  }

  async function saveProfile() {
    if (!draft) return;
    setBusy("save");
    setError("");
    try {
      const updated = await request<PromptProfile>(`/api/prompt-profiles/${draft.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          revision: draft.draft_revision,
          name: draft.name,
          slug: draft.slug,
          description: draft.description,
          aliases: draft.aliases,
          template: draft.template,
          argument_hint: draft.argument_hint,
          tags: draft.tags,
          public_app_allowed: draft.public_app_allowed,
        }),
      });
      await load(updated.id);
      setNotice("Prompt 草稿已保存。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存失败");
    } finally {
      setBusy("");
    }
  }

  async function action(name: "validate" | "publish" | "archive") {
    if (!draft) return;
    setBusy(name);
    setError("");
    try {
      const body = name === "validate"
        ? undefined
        : JSON.stringify({ revision: draft.draft_revision });
      const result = await request<Record<string, unknown>>(
        `/api/prompt-profiles/${draft.id}/${name}`,
        {
          method: "POST",
          headers: body ? { "Content-Type": "application/json" } : undefined,
          body,
        },
      );
      if (name === "validate" && result.valid === false) {
        setNotice(`校验未通过：${JSON.stringify(result.issues ?? [])}`);
      } else {
        setNotice(name === "validate" ? "校验通过。" : name === "publish" ? "不可变版本已发布。" : "Prompt 已归档。");
      }
      await load(draft.id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "操作失败");
    } finally {
      setBusy("");
    }
  }

  const preview = useMemo(
    () => (draft?.template ?? "").replaceAll("{{args}}", previewArgs.slice(0, 8000)),
    [draft?.template, previewArgs],
  );

  return (
    <PageContainer activeResource="prompts" hideSidebar maxWidthClassName="max-w-[1560px]">
      <header className="mb-5 flex flex-col gap-3 border-b border-white/10 pb-5 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-200">Prompt Runtime</p>
          <h1 className="mt-2 text-2xl font-semibold text-white">Prompt Command</h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-400">编辑、发布并将固定版本命令绑定到智能体。命令模板只接受 <code>{"{{args}}"}</code>。</p>
        </div>
        <button className="rounded-md bg-cyan-300 px-4 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50" disabled={Boolean(busy)} onClick={() => void createProfile()} type="button">新建 Prompt</button>
      </header>

      {error || notice ? (
        <p className={`mb-4 rounded-md border px-3 py-2 text-xs ${error ? "border-rose-300/30 bg-rose-300/10 text-rose-100" : "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"}`}>{error || notice}</p>
      ) : null}

      <div className="grid gap-5 xl:grid-cols-[300px_minmax(0,1fr)]">
        <aside className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
          <div className="mb-3 flex items-center justify-between text-xs text-slate-400"><span>Profiles</span><span>{items.length}</span></div>
          <div className="space-y-2">
            {items.map((item) => (
              <button className={`w-full rounded-md border p-3 text-left ${item.id === selectedId ? "border-cyan-300/45 bg-cyan-300/10" : "border-white/10 bg-slate-950/45 hover:border-white/20"}`} key={item.id} onClick={() => { setSelectedId(item.id); setDraft(item); setNotice(""); setError(""); }} type="button">
                <div className="flex items-center justify-between gap-2"><span className="truncate text-sm font-semibold text-white">{item.name}</span><span className="text-[10px] text-slate-500">{item.published_version ? `v${item.published_version}` : item.status}</span></div>
                <p className="mt-1 truncate text-xs text-cyan-100">/{item.aliases[0] || "未配置命令"}</p>
              </button>
            ))}
            {!items.length ? <p className="rounded-md border border-dashed border-white/10 p-5 text-center text-xs text-slate-500">尚无 Prompt Profile</p> : null}
          </div>
        </aside>

        {draft ? (
          <section className="space-y-4 rounded-lg border border-white/10 bg-slate-950/55 p-4">
            <div className="grid gap-3 md:grid-cols-2">
              <label className="text-xs text-slate-400">名称<input className="mt-1 h-10 w-full rounded-md border border-white/10 bg-white/[0.04] px-3 text-sm text-white" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
              <label className="text-xs text-slate-400">Slug<input className="mt-1 h-10 w-full rounded-md border border-white/10 bg-white/[0.04] px-3 text-sm text-white" value={draft.slug} onChange={(event) => setDraft({ ...draft, slug: event.target.value })} /></label>
            </div>
            <label className="block text-xs text-slate-400">描述<textarea className="mt-1 min-h-20 w-full rounded-md border border-white/10 bg-white/[0.04] p-3 text-sm text-white" value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} /></label>
            <div className="grid gap-3 md:grid-cols-2">
              <label className="text-xs text-slate-400">命令别名，逗号分隔<input className="mt-1 h-10 w-full rounded-md border border-white/10 bg-white/[0.04] px-3 text-sm text-white" value={draft.aliases.join(", ")} onChange={(event) => setDraft({ ...draft, aliases: splitValues(event.target.value) })} /></label>
              <label className="text-xs text-slate-400">参数提示<input className="mt-1 h-10 w-full rounded-md border border-white/10 bg-white/[0.04] px-3 text-sm text-white" value={draft.argument_hint} onChange={(event) => setDraft({ ...draft, argument_hint: event.target.value })} /></label>
            </div>
            <label className="block text-xs text-slate-400">模板<textarea className="mt-1 min-h-44 w-full rounded-md border border-white/10 bg-white/[0.04] p-3 font-mono text-sm leading-6 text-white" value={draft.template} onChange={(event) => setDraft({ ...draft, template: event.target.value })} /></label>
            <div className="grid gap-3 md:grid-cols-2">
              <label className="text-xs text-slate-400">标签<input className="mt-1 h-10 w-full rounded-md border border-white/10 bg-white/[0.04] px-3 text-sm text-white" value={draft.tags.join(", ")} onChange={(event) => setDraft({ ...draft, tags: splitValues(event.target.value) })} /></label>
              <label className="flex items-center gap-2 self-end rounded-md border border-white/10 bg-white/[0.035] px-3 py-3 text-xs text-slate-300"><input checked={draft.public_app_allowed} onChange={(event) => setDraft({ ...draft, public_app_allowed: event.target.checked })} type="checkbox" />允许安全 Agent App 使用</label>
            </div>
            <div className="rounded-md border border-white/10 bg-black/20 p-3">
              <label className="text-xs text-slate-400">预览参数<input className="mt-1 h-10 w-full rounded-md border border-white/10 bg-white/[0.04] px-3 text-sm text-white" value={previewArgs} onChange={(event) => setPreviewArgs(event.target.value)} /></label>
              <pre className="mt-3 max-h-52 overflow-auto whitespace-pre-wrap rounded-md bg-black/25 p-3 text-xs leading-5 text-slate-300">{preview || "预览为空"}</pre>
            </div>
            <div className="border-t border-white/10 pt-4">
              <div className="mb-2 flex items-center justify-between">
                <p className="text-xs font-semibold text-slate-300">版本历史</p>
                <span className="text-[11px] text-slate-500">{draft.versions.length} 个不可变版本</span>
              </div>
              {draft.versions.length ? (
                <div className="divide-y divide-white/10 border-y border-white/10">
                  {[...draft.versions].reverse().map((version) => (
                    <div className="flex flex-wrap items-center justify-between gap-2 py-2 text-xs" key={version.version}>
                      <span className="font-semibold text-white">v{version.version}</span>
                      <span className="min-w-0 flex-1 truncate text-cyan-100">{version.aliases.map((alias) => `/${alias}`).join(" · ")}</span>
                      <span className="text-slate-500">{new Date(version.published_at).toLocaleString()}</span>
                    </div>
                  ))}
                </div>
              ) : <p className="text-xs text-slate-500">发布后会在这里保留固定模板与命令别名。</p>}
            </div>
            <div className="flex flex-wrap items-center gap-2 border-t border-white/10 pt-4">
              <Link className="rounded-md border border-violet-300/25 bg-violet-300/10 px-3 py-2 text-xs font-semibold text-violet-100" to={`/agents/evolution?prompt_profile_id=${draft.id}`}>优化模板</Link>
              <button className="rounded-md border border-white/10 px-3 py-2 text-xs font-semibold text-white" disabled={Boolean(busy)} onClick={() => void saveProfile()} type="button">保存草稿</button>
              <button className="rounded-md border border-white/10 px-3 py-2 text-xs font-semibold text-white" disabled={Boolean(busy)} onClick={() => void action("validate")} type="button">校验</button>
              <button className="rounded-md bg-emerald-300 px-3 py-2 text-xs font-semibold text-slate-950" disabled={Boolean(busy)} onClick={() => void action("publish")} type="button">发布版本</button>
              <button className="ml-auto rounded-md px-3 py-2 text-xs text-rose-200" disabled={Boolean(busy)} onClick={() => void action("archive")} type="button">归档</button>
              <span className="text-[11px] text-slate-500">revision {draft.draft_revision} · {draft.versions.length} versions</span>
            </div>
          </section>
        ) : null}
      </div>
      {draft ? (
        <div className="mt-5">
          <AuthoringProposalPanel
            kindPrefix="prompt_profile"
            onApplied={() => void load(draft.id)}
            targetId={draft.id}
            title="Prompt 进化提案"
          />
        </div>
      ) : null}
    </PageContainer>
  );
}
