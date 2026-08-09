import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

interface SkillDraft {
  draft_id: string;
  name: string;
  slug: string;
  description: string;
  status: "draft" | "installed" | "archived";
  revision: number;
  content_revision: number;
  content_digest: string;
  install_state: "not_installed" | "current" | "outdated";
  installed_skill_id?: string | null;
  file_count?: number;
  skill_markdown?: string;
  files?: Record<string, string>;
  quality_required?: boolean;
  quality_status?:
    | "not_evaluated"
    | "running"
    | "accepted"
    | "eval_waived"
    | "outdated";
  creator_session_id?: string | null;
}

function qualityGatePassed(item: SkillDraft) {
  return !item.quality_required || item.quality_status === "accepted" || item.quality_status === "eval_waived";
}

function qualityLabel(item: SkillDraft) {
  if (!item.quality_required) return "";
  if (item.quality_status === "accepted") return "评测已接受";
  if (item.quality_status === "eval_waived") return "评测已豁免";
  if (item.quality_status === "running") return "评测中";
  if (item.quality_status === "outdated") return "评测已过期";
  return "待评测";
}

async function readError(response: Response) {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    return typeof payload.detail === "string"
      ? payload.detail
      : JSON.stringify(payload.detail ?? `HTTP ${response.status}`);
  } catch {
    return `HTTP ${response.status}`;
  }
}

export default function SkillDraftPanel({ onInstalled }: { onInstalled?: () => void }) {
  const [items, setItems] = useState<SkillDraft[]>([]);
  const [selected, setSelected] = useState<SkillDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/skills/drafts?limit=200");
      if (!response.ok) throw new Error(await readError(response));
      const data = (await response.json()) as { items?: SkillDraft[] };
      setItems(data.items ?? []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Skill 草稿加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function open(item: SkillDraft) {
    setBusy(item.draft_id);
    try {
      const response = await fetch(`/api/skills/drafts/${item.draft_id}`);
      if (!response.ok) throw new Error(await readError(response));
      setSelected((await response.json()) as SkillDraft);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Skill 草稿详情加载失败");
    } finally {
      setBusy("");
    }
  }

  async function action(actionName: "validate" | "install" | "archive") {
    if (!selected) return;
    setBusy(actionName);
    setError("");
    setNotice("");
    try {
      const response = await fetch(`/api/skills/drafts/${selected.draft_id}/${actionName}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_revision: selected.revision,
          expected_digest: selected.content_digest,
        }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const detailResponse = await fetch(`/api/skills/drafts/${selected.draft_id}`);
      if (!detailResponse.ok) throw new Error(await readError(detailResponse));
      setSelected((await detailResponse.json()) as SkillDraft);
      setNotice(
        actionName === "install"
          ? "Skill 已显式安装，可由 skills_runtime 在 Sandbox 中使用。"
          : actionName === "archive"
            ? "Skill 草稿已归档。"
            : "Skill 包校验通过。",
      );
      await load();
      if (actionName === "install") onInstalled?.();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Skill 草稿操作失败");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.035] p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-white">工作区 Skill 草稿</h2>
          <p className="mt-1 text-xs text-slate-400">批准提案只生成草稿；安装始终需要这里的显式操作。</p>
        </div>
        <button className="rounded-md border border-white/10 px-3 py-2 text-xs text-slate-300" onClick={() => void load()} type="button">刷新</button>
      </div>
      {error ? <p className="mt-3 rounded-md border border-rose-300/25 bg-rose-300/10 p-2 text-xs text-rose-100">{error}</p> : null}
      {notice ? <p className="mt-3 rounded-md border border-emerald-300/25 bg-emerald-300/10 p-2 text-xs text-emerald-100">{notice}</p> : null}
      <div className="mt-4 grid gap-4 lg:grid-cols-[340px_minmax(0,1fr)]">
        <div className="max-h-[520px] space-y-2 overflow-y-auto">
          {loading ? <p className="text-xs text-slate-500">正在加载...</p> : null}
          {!loading && items.length === 0 ? <p className="rounded-md border border-dashed border-white/10 p-5 text-center text-xs text-slate-500">暂无工作区草稿</p> : null}
          {items.map((item) => (
            <button className={`w-full rounded-md border p-3 text-left ${selected?.draft_id === item.draft_id ? "border-hire-300/45 bg-hire-300/10" : "border-white/10 bg-ink-950/55"}`} key={item.draft_id} onClick={() => void open(item)} type="button">
              <div className="flex items-center justify-between gap-2"><span className="truncate text-sm font-semibold text-white">{item.name}</span><span className="text-[10px] uppercase text-slate-500">{item.status}</span></div>
              <p className="mt-1 text-xs text-slate-400">{item.slug} · revision {item.revision}</p>
              <p className="mt-2 line-clamp-2 text-xs text-slate-500">{item.description}</p>
              {item.quality_required ? <p className={`mt-2 text-xs font-semibold ${qualityGatePassed(item) ? "text-emerald-200" : "text-amber-100"}`}>{qualityLabel(item)}</p> : null}
            </button>
          ))}
        </div>
        {selected ? (
          <article className="rounded-md border border-white/10 bg-ink-950/45 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div><h3 className="text-lg font-semibold text-white">{selected.name}</h3><p className="mt-1 text-xs text-slate-500">{selected.draft_id}</p></div>
              <span className="rounded-full border border-white/10 px-2 py-1 text-xs text-slate-300">{selected.status}</span>
            </div>
            <p className="mt-3 text-sm leading-6 text-slate-300">{selected.description}</p>
            <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-400">
              <span>{selected.file_count ?? 1 + Object.keys(selected.files ?? {}).length} 文件</span>
              <span>revision {selected.revision}</span>
              {selected.quality_required ? <span className={qualityGatePassed(selected) ? "text-emerald-200" : "text-amber-100"}>{qualityLabel(selected)}</span> : null}
              {selected.installed_skill_id ? <span className="text-emerald-200">已安装为 {selected.installed_skill_id}</span> : null}
            </div>
            {selected.quality_required && !qualityGatePassed(selected) ? (
              <div className="mt-4 rounded-md border border-amber-300/25 bg-amber-300/[0.08] p-3 text-xs leading-5 text-amber-100">
                <p>该草稿来自 Skill Creator，当前内容尚未通过质量门。完成对照评测，或对主观创作类 Skill 作出明确人工豁免后，才能安装。</p>
                {selected.creator_session_id ? (
                  <Link className="mt-2 inline-flex rounded-md border border-amber-200/30 px-3 py-2 font-semibold text-amber-50 hover:bg-amber-200/10" to={`/skills/create/${selected.creator_session_id}`}>前往 Creator 完成评测</Link>
                ) : null}
              </div>
            ) : null}
            <pre className="mt-4 max-h-72 overflow-auto whitespace-pre-wrap rounded-md border border-white/10 bg-black/20 p-3 text-xs leading-5 text-slate-300">{selected.skill_markdown}</pre>
            <div className="mt-4 flex flex-wrap gap-2">
              <button className="rounded-md border border-sky-300/25 bg-sky-300/10 px-3 py-2 text-xs font-semibold text-sky-100" disabled={Boolean(busy) || selected.status === "archived"} onClick={() => void action("validate")} type="button">校验包</button>
              <button className="rounded-md bg-emerald-300 px-3 py-2 text-xs font-semibold text-ink-950 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500" disabled={Boolean(busy) || selected.status !== "draft" || !qualityGatePassed(selected)} onClick={() => void action("install")} type="button">{selected.quality_required && !qualityGatePassed(selected) ? "通过质量门后可安装" : "显式安装"}</button>
              <button className="rounded-md border border-white/10 px-3 py-2 text-xs font-semibold text-slate-300" disabled={Boolean(busy) || selected.status === "archived"} onClick={() => void action("archive")} type="button">归档</button>
            </div>
          </article>
        ) : <div className="grid min-h-64 place-items-center rounded-md border border-dashed border-white/10 text-xs text-slate-500">选择草稿查看内容</div>}
      </div>
    </div>
  );
}
