import { useEffect, useMemo, useState } from "react";
import {
  type XpertFileMemoryIndex,
  type XpertFileMemoryType,
  type XpertMemoryCandidate,
  type XpertMemoryRecord,
} from "../../types/xpert";
import {
  archiveXpertMemory,
  decideXpertMemoryCandidate,
  getXpertFileMemory,
  getXpertFileMemoryIndex,
  runXpertFileMemoryWriteback,
  updateXpertFileMemory,
  updateXpertMemoryCandidate,
} from "../../utils/xpertApi";

const MEMORY_TYPES: Array<{ id: XpertFileMemoryType | "all"; label: string }> = [
  { id: "all", label: "全部" },
  { id: "user", label: "用户" },
  { id: "feedback", label: "反馈" },
  { id: "project", label: "项目" },
  { id: "reference", label: "参考" },
];

interface MemoryDraft {
  type: XpertFileMemoryType;
  title: string;
  summary: string;
  content: string;
  tags: string;
}

interface FileMemoryPanelProps {
  xpertId: string;
  conversationId: string;
  memories: XpertMemoryRecord[];
  candidates: XpertMemoryCandidate[];
  onRefresh: () => Promise<void>;
  onError: (message: string) => void;
}

function memoryType(item: XpertMemoryRecord | XpertMemoryCandidate): XpertFileMemoryType {
  return item.type ?? item.memory_type ?? "project";
}

function memoryDraft(item: XpertMemoryRecord | XpertMemoryCandidate): MemoryDraft {
  return {
    type: memoryType(item),
    title: item.title ?? "",
    summary: item.summary ?? "",
    content: item.content,
    tags: item.tags.join(", "),
  };
}

function splitTags(value: string) {
  return value
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 20);
}

function typeLabel(type: XpertFileMemoryType) {
  return MEMORY_TYPES.find((item) => item.id === type)?.label ?? type;
}

function usageSummary(memory: XpertMemoryRecord) {
  const usage = memory.usage;
  if (!usage) return "尚无使用信号";
  return `召回 ${usage.recall_count} · 详情 ${usage.detail_read_count} · 有用度 ${usage.usefulness_score.toFixed(1)}`;
}

export default function FileMemoryPanel({
  xpertId,
  conversationId,
  memories,
  candidates,
  onRefresh,
  onError,
}: FileMemoryPanelProps) {
  const [index, setIndex] = useState<XpertFileMemoryIndex | null>(null);
  const [typeFilter, setTypeFilter] = useState<XpertFileMemoryType | "all">("all");
  const [search, setSearch] = useState("");
  const [selectedMemory, setSelectedMemory] = useState<XpertMemoryRecord | null>(null);
  const [selectedCandidate, setSelectedCandidate] = useState<XpertMemoryCandidate | null>(null);
  const [draft, setDraft] = useState<MemoryDraft | null>(null);
  const [busy, setBusy] = useState(false);

  async function refreshIndex() {
    try {
      setIndex(await getXpertFileMemoryIndex(xpertId));
    } catch {
      setIndex(null);
    }
  }

  useEffect(() => {
    void refreshIndex();
  }, [xpertId, memories.length, candidates.length]);

  const visibleMemories = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return memories.filter((memory) => {
      if (
        typeFilter !== "all" &&
        (memory.scope !== "xpert" || memoryType(memory) !== typeFilter)
      ) return false;
      if (!needle) return true;
      return [memory.title, memory.summary, memory.content, memory.tags.join(" ")]
        .filter(Boolean)
        .join(" ")
        .toLocaleLowerCase()
        .includes(needle);
    });
  }, [memories, search, typeFilter]);

  const actionableCandidates = candidates.filter((item) =>
    item.status === "pending" || item.status === "conflict",
  );

  async function runAction(action: () => Promise<unknown>) {
    setBusy(true);
    onError("");
    try {
      await action();
      await Promise.all([onRefresh(), refreshIndex()]);
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : "文件记忆操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function openMemory(memory: XpertMemoryRecord) {
    if (memory.scope !== "xpert") {
      setSelectedMemory(memory);
      setSelectedCandidate(null);
      setDraft(memoryDraft(memory));
      return;
    }
    setBusy(true);
    try {
      const detail = await getXpertFileMemory(xpertId, memory.memory_id);
      setSelectedMemory(detail);
      setSelectedCandidate(null);
      setDraft(memoryDraft(detail));
      await onRefresh();
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : "记忆详情加载失败");
    } finally {
      setBusy(false);
    }
  }

  function openCandidate(candidate: XpertMemoryCandidate) {
    setSelectedCandidate(candidate);
    setSelectedMemory(null);
    setDraft(memoryDraft(candidate));
  }

  async function saveMemory() {
    if (!selectedMemory || !draft || selectedMemory.scope !== "xpert" || !selectedMemory.revision) return;
    await runAction(async () => {
      const updated = await updateXpertFileMemory(xpertId, selectedMemory.memory_id, {
        revision: selectedMemory.revision!,
        type: draft.type,
        title: draft.title,
        summary: draft.summary,
        content: draft.content,
        tags: splitTags(draft.tags),
      });
      setSelectedMemory(updated);
      setDraft(memoryDraft(updated));
    });
  }

  async function saveCandidate() {
    if (!selectedCandidate || !draft || !selectedCandidate.revision) return;
    await runAction(async () => {
      const updated = await updateXpertMemoryCandidate(xpertId, selectedCandidate.candidate_id, {
        revision: selectedCandidate.revision!,
        type: draft.type,
        title: draft.title,
        summary: draft.summary,
        content: draft.content,
        tags: splitTags(draft.tags),
      });
      setSelectedCandidate(updated);
      setDraft(memoryDraft(updated));
    });
  }

  async function decideCandidate(action: "approve" | "reject") {
    if (!selectedCandidate) return;
    await runAction(async () => {
      await decideXpertMemoryCandidate(
        xpertId,
        selectedCandidate.candidate_id,
        action,
        selectedCandidate.revision,
      );
      setSelectedCandidate(null);
      setDraft(null);
    });
  }

  const typeCounts = index?.type_counts;

  return (
    <section>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-xs font-semibold text-white">类型化文件记忆</h3>
          <p className="mt-1 text-[10px] leading-4 text-slate-500">
            长期记忆按智能体隔离；自动整理只生成候选，批准后才写入。
          </p>
        </div>
        <button
          className="shrink-0 rounded-md border border-cyan-300/20 bg-cyan-300/10 px-2 py-1 text-[10px] font-semibold text-cyan-100 disabled:opacity-50"
          disabled={busy || !conversationId}
          onClick={() => void runAction(() => runXpertFileMemoryWriteback(xpertId, conversationId))}
          type="button"
        >
          {busy ? "处理中" : "整理本次对话"}
        </button>
      </div>

      <div className="mt-3 grid grid-cols-4 gap-1.5">
        {(["user", "feedback", "project", "reference"] as XpertFileMemoryType[]).map((type) => (
          <div className="border-l border-white/10 pl-2" key={type}>
            <p className="text-[9px] text-slate-500">{typeLabel(type)}</p>
            <p className="mt-0.5 text-xs font-semibold text-slate-200">{typeCounts?.[type] ?? 0}</p>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[9px] text-slate-600">
        index rev {index?.index_revision ?? 0} · {index?.signal_count ?? 0} signals
      </p>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {MEMORY_TYPES.map((type) => (
          <button
            className={`rounded-md px-2 py-1 text-[10px] font-semibold transition ${
              typeFilter === type.id
                ? "bg-cyan-300 text-ink-950"
                : "border border-white/10 bg-white/[0.035] text-slate-400 hover:text-white"
            }`}
            key={type.id}
            onClick={() => setTypeFilter(type.id)}
            type="button"
          >
            {type.label}
          </button>
        ))}
      </div>
      <input
        className="mt-2 w-full rounded-md border border-white/10 bg-ink-950/60 px-2.5 py-2 text-[11px] text-white outline-none focus:border-cyan-300/50"
        onChange={(event) => setSearch(event.target.value)}
        placeholder="搜索标题、摘要、正文或标签"
        value={search}
      />

      {actionableCandidates.length > 0 ? (
        <div className="mt-4">
          <div className="flex items-center justify-between">
            <h4 className="text-[11px] font-semibold text-amber-100">待确认候选</h4>
            <span className="text-[10px] text-slate-500">{actionableCandidates.length}</span>
          </div>
          <div className="mt-2 divide-y divide-white/10 border-y border-white/10">
            {actionableCandidates.map((candidate) => (
              <button
                className="w-full px-1 py-2.5 text-left transition hover:bg-white/[0.035]"
                key={candidate.candidate_id}
                onClick={() => openCandidate(candidate)}
                type="button"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[11px] font-semibold text-slate-100">
                    {candidate.title || candidate.summary || "未命名候选"}
                  </span>
                  <span className={candidate.status === "conflict" ? "text-[9px] text-rose-300" : "text-[9px] text-amber-200"}>
                    {candidate.status === "conflict" ? "冲突" : candidate.action === "update" ? "修订" : "新增"}
                  </span>
                </div>
                <p className="mt-1 line-clamp-2 text-[10px] leading-4 text-slate-500">
                  {candidate.summary || candidate.content}
                </p>
              </button>
            ))}
          </div>
        </div>
      ) : null}

      <div className="mt-4">
        <div className="flex items-center justify-between">
          <h4 className="text-[11px] font-semibold text-white">已生效记忆</h4>
          <span className="text-[10px] text-slate-500">{visibleMemories.length}</span>
        </div>
        <div className="mt-2 max-h-72 divide-y divide-white/10 overflow-y-auto border-y border-white/10">
          {visibleMemories.map((memory) => (
            <button
              className="w-full px-1 py-2.5 text-left transition hover:bg-white/[0.035]"
              key={memory.memory_id}
              onClick={() => void openMemory(memory)}
              type="button"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-[11px] font-semibold text-slate-100">
                  {memory.title || memory.summary || memory.content.slice(0, 60)}
                </span>
                <span className="shrink-0 text-[9px] text-cyan-200">
                  {memory.scope === "conversation" ? "会话" : typeLabel(memoryType(memory))}
                </span>
              </div>
              <p className="mt-1 line-clamp-2 text-[10px] leading-4 text-slate-500">
                {memory.summary || memory.content}
              </p>
              <p className="mt-1 text-[9px] text-slate-600">{usageSummary(memory)}</p>
            </button>
          ))}
          {visibleMemories.length === 0 ? (
            <p className="py-5 text-center text-xs text-slate-500">没有符合条件的记忆</p>
          ) : null}
        </div>
      </div>

      {draft && (selectedMemory || selectedCandidate) ? (
        <div className="mt-4 border-t border-white/10 pt-3">
          <div className="flex items-center justify-between gap-2">
            <h4 className="text-[11px] font-semibold text-white">
              {selectedCandidate ? "编辑记忆候选" : "记忆详情"}
            </h4>
            <button className="text-[10px] text-slate-500 hover:text-white" onClick={() => { setDraft(null); setSelectedMemory(null); setSelectedCandidate(null); }} type="button">
              关闭
            </button>
          </div>
          {selectedCandidate?.status === "conflict" ? (
            <p className="mt-2 rounded-md border border-rose-300/20 bg-rose-300/[0.06] px-2.5 py-2 text-[10px] leading-4 text-rose-200">
              目标记忆已被修改，本候选不会覆盖人工编辑。请拒绝后重新生成候选。
            </p>
          ) : null}
          <div className="mt-3 grid grid-cols-[100px_minmax(0,1fr)] gap-2">
            <select
              className="rounded-md border border-white/10 bg-ink-950/70 px-2 py-1.5 text-[11px] text-white outline-none"
              disabled={selectedMemory?.scope === "conversation"}
              onChange={(event) => setDraft((current) => current ? { ...current, type: event.target.value as XpertFileMemoryType } : current)}
              value={draft.type}
            >
              {MEMORY_TYPES.slice(1).map((type) => <option key={type.id} value={type.id}>{type.label}</option>)}
            </select>
            <input className="rounded-md border border-white/10 bg-ink-950/70 px-2 py-1.5 text-[11px] text-white outline-none" disabled={selectedMemory?.scope === "conversation"} onChange={(event) => setDraft((current) => current ? { ...current, title: event.target.value } : current)} placeholder="标题" value={draft.title} />
          </div>
          <input className="mt-2 w-full rounded-md border border-white/10 bg-ink-950/70 px-2 py-1.5 text-[11px] text-white outline-none" disabled={selectedMemory?.scope === "conversation"} onChange={(event) => setDraft((current) => current ? { ...current, summary: event.target.value } : current)} placeholder="摘要" value={draft.summary} />
          <textarea className="mt-2 min-h-28 w-full resize-y rounded-md border border-white/10 bg-ink-950/70 px-2 py-2 text-[11px] leading-5 text-white outline-none" disabled={selectedMemory?.scope === "conversation"} onChange={(event) => setDraft((current) => current ? { ...current, content: event.target.value } : current)} value={draft.content} />
          <input className="mt-2 w-full rounded-md border border-white/10 bg-ink-950/70 px-2 py-1.5 text-[11px] text-white outline-none" disabled={selectedMemory?.scope === "conversation"} onChange={(event) => setDraft((current) => current ? { ...current, tags: event.target.value } : current)} placeholder="标签，用逗号分隔" value={draft.tags} />
          <div className="mt-3 flex items-center justify-between gap-3">
            <p className="text-[9px] text-slate-600">
              rev {selectedCandidate?.revision ?? selectedMemory?.revision ?? 1}
              {selectedMemory?.canonical_ref ? ` · ${selectedMemory.canonical_ref}` : ""}
            </p>
            <div className="flex gap-2">
              {selectedCandidate ? (
                <>
                  <button className="rounded-md px-2 py-1 text-[10px] text-rose-200 disabled:opacity-50" disabled={busy} onClick={() => void decideCandidate("reject")} type="button">拒绝</button>
                  <button className="rounded-md border border-white/10 px-2 py-1 text-[10px] text-slate-200 disabled:opacity-50" disabled={busy || selectedCandidate.status === "conflict"} onClick={() => void saveCandidate()} type="button">保存修改</button>
                  <button className="rounded-md bg-emerald-300 px-2 py-1 text-[10px] font-semibold text-ink-950 disabled:opacity-50" disabled={busy || selectedCandidate.status === "conflict"} onClick={() => void decideCandidate("approve")} type="button">批准</button>
                </>
              ) : selectedMemory?.scope === "xpert" ? (
                <>
                  <button className="rounded-md px-2 py-1 text-[10px] text-rose-200 disabled:opacity-50" disabled={busy} onClick={() => void runAction(async () => { await archiveXpertMemory(xpertId, selectedMemory.memory_id, selectedMemory.revision); setSelectedMemory(null); setDraft(null); })} type="button">归档</button>
                  <button className="rounded-md bg-cyan-300 px-2 py-1 text-[10px] font-semibold text-ink-950 disabled:opacity-50" disabled={busy} onClick={() => void saveMemory()} type="button">保存</button>
                </>
              ) : (
                <button className="rounded-md px-2 py-1 text-[10px] text-rose-200 disabled:opacity-50" disabled={busy} onClick={() => void runAction(async () => { await archiveXpertMemory(xpertId, selectedMemory!.memory_id, selectedMemory!.revision); setSelectedMemory(null); setDraft(null); })} type="button">归档会话记忆</button>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
