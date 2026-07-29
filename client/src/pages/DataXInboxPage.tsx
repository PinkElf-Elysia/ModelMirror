import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import type { DataXProposal } from "../types/datax";

type ProposalStatus = "pending" | "approved" | "rejected" | "cancelled";

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(String(payload.detail || `请求失败：${response.status}`));
  }
  return payload as T;
}

function statusLabel(status: ProposalStatus) {
  return {
    pending: "待审批",
    approved: "已批准",
    rejected: "已拒绝",
    cancelled: "已取消",
  }[status];
}

function statusTone(status: ProposalStatus) {
  if (status === "approved") return "bg-emerald-400/10 text-emerald-200";
  if (status === "rejected") return "bg-rose-400/10 text-rose-200";
  if (status === "cancelled") return "bg-white/5 text-slate-400";
  return "bg-amber-300/10 text-amber-100";
}

export default function DataXInboxPage() {
  const { projectId = "" } = useParams();
  const [items, setItems] = useState<DataXProposal[]>([]);
  const [status, setStatus] = useState<ProposalStatus>("pending");
  const [selectedId, setSelectedId] = useState("");
  const [title, setTitle] = useState("");
  const [payloadText, setPayloadText] = useState("{}");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    try {
      const query = new URLSearchParams({ project_id: projectId, status, limit: "200" });
      const result = await requestJson<{ items: DataXProposal[] }>(
        `/api/datax/indicator-proposals?${query.toString()}`,
      );
      setItems(result.items);
      setSelectedId((current) =>
        result.items.some((item) => item.proposal_id === current)
          ? current
          : result.items[0]?.proposal_id || "",
      );
      setError("");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "提案加载失败");
    }
  }

  useEffect(() => {
    void load();
  }, [projectId, status]);

  const selected = useMemo(
    () => items.find((item) => item.proposal_id === selectedId) || null,
    [items, selectedId],
  );

  useEffect(() => {
    if (!selected) {
      setTitle("");
      setPayloadText("{}");
      return;
    }
    setTitle(selected.title);
    setPayloadText(JSON.stringify(selected.payload, null, 2));
    setReason(selected.reason || "");
  }, [selected]);

  async function save() {
    if (!selected) return;
    setBusy("save");
    try {
      const payload = JSON.parse(payloadText) as Record<string, unknown>;
      await requestJson(`/api/datax/indicator-proposals/${selected.proposal_id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revision: selected.revision, title: title.trim(), payload }),
      });
      setNotice("提案已保存，revision 已更新。批准后只会生成指标草稿。 ");
      await load();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "提案保存失败");
    } finally {
      setBusy("");
    }
  }

  async function decide(action: "approve" | "reject" | "cancel") {
    if (!selected) return;
    setBusy(action);
    try {
      await requestJson(
        `/api/datax/indicator-proposals/${selected.proposal_id}/${action}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            revision: selected.revision,
            operator: "modelmirror-operator",
            reason: reason.trim(),
          }),
        },
      );
      setNotice(
        action === "approve"
          ? "提案已批准并生成指标草稿；仍需在指标页预览并显式发布。"
          : action === "reject"
            ? "提案已拒绝，未修改任何指标。"
            : "提案已取消。",
      );
      await load();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "提案处理失败");
    } finally {
      setBusy("");
    }
  }

  return (
    <PageContainer contentClassName="min-w-0" maxWidthClassName="max-w-[1720px]">
      <main className="pb-12">
        <header className="border-b border-white/10 pb-5">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-200">Data X / Indicator proposals</p>
              <h1 className="mt-2 text-2xl font-semibold text-white">指标提案 Inbox</h1>
              <p className="mt-2 max-w-3xl text-sm text-slate-400">Agent 只能提出版本化草稿。这里负责编辑与审批；发布仍在指标工作台显式完成。</p>
            </div>
            <div className="flex gap-2">
              <Link className="rounded-md border border-white/10 px-3 py-2 text-sm font-semibold text-slate-200 hover:bg-white/5" to={`/datax/${projectId}`}>返回项目</Link>
              <button className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-slate-950" onClick={() => void load()} type="button">刷新</button>
            </div>
          </div>
          <div className="mt-5 flex gap-1 overflow-x-auto">
            {(["pending", "approved", "rejected", "cancelled"] as ProposalStatus[]).map((value) => (
              <button className={`shrink-0 rounded-md px-3 py-2 text-xs font-semibold ${status === value ? "bg-cyan-300 text-slate-950" : "text-slate-400 hover:bg-white/5"}`} key={value} onClick={() => setStatus(value)} type="button">{statusLabel(value)}</button>
            ))}
          </div>
        </header>

        {error && <div className="mt-4 rounded-md bg-rose-500/10 px-3 py-2 text-sm text-rose-200" role="alert">{error}</div>}
        {notice && <div className="mt-4 rounded-md bg-emerald-400/10 px-3 py-2 text-sm text-emerald-100">{notice}</div>}

        <div className="mt-6 grid gap-6 lg:grid-cols-[330px_minmax(0,1fr)]">
          <aside className="max-h-[720px] overflow-y-auto border-y border-white/10">
            {items.map((item) => (
              <button className={`block w-full border-b border-white/10 px-3 py-3 text-left transition ${selectedId === item.proposal_id ? "bg-cyan-300/10" : "hover:bg-white/[0.035]"}`} key={item.proposal_id} onClick={() => setSelectedId(item.proposal_id)} type="button">
                <div className="flex items-start justify-between gap-3">
                  <span className="text-sm font-semibold text-white">{item.title}</span>
                  <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] ${statusTone(item.status)}`}>{statusLabel(item.status)}</span>
                </div>
                <p className="mt-2 text-xs text-slate-500">{item.proposal_type === "create" ? "创建指标" : "更新指标"} · revision {item.revision}</p>
                <p className="mt-1 truncate font-mono text-[10px] text-slate-600">{item.proposal_id}</p>
              </button>
            ))}
            {!items.length && <div className="px-4 py-20 text-center text-sm text-slate-500">当前筛选下没有提案。</div>}
          </aside>

          {selected ? (
            <section className="min-w-0 border-t border-white/10 pt-4">
              <div className="grid gap-4 md:grid-cols-2">
                <label className="text-xs font-semibold text-slate-300">提案标题<input className="mt-1 block w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white disabled:opacity-60" disabled={selected.status !== "pending"} onChange={(event) => setTitle(event.target.value)} value={title} /></label>
                <div className="text-xs text-slate-500"><p>来源智能体</p><p className="mt-2 font-mono text-slate-300">{selected.source_xpert_id || "-"}</p><p className="mt-1 font-mono text-[10px]">run {selected.source_run_id || "-"}</p></div>
              </div>
              <label className="mt-4 block text-xs font-semibold text-slate-300">指标草稿 JSON<textarea className="mt-1 min-h-[390px] w-full rounded-md border border-white/10 bg-ink-950 p-3 font-mono text-xs leading-5 text-slate-200 disabled:opacity-60" disabled={selected.status !== "pending"} onChange={(event) => setPayloadText(event.target.value)} spellCheck={false} value={payloadText} /></label>
              <label className="mt-4 block text-xs font-semibold text-slate-300">审批说明<textarea className="mt-1 min-h-20 w-full rounded-md border border-white/10 bg-ink-950 p-3 text-sm text-slate-200 disabled:opacity-60" disabled={selected.status !== "pending"} onChange={(event) => setReason(event.target.value)} value={reason} /></label>
              {selected.status === "pending" && (
                <div className="mt-4 flex flex-wrap gap-2">
                  <button className="rounded-md border border-white/10 px-3 py-2 text-sm font-semibold text-slate-200 hover:bg-white/5 disabled:opacity-50" disabled={Boolean(busy)} onClick={() => void save()} type="button">{busy === "save" ? "保存中..." : "保存编辑"}</button>
                  <button className="rounded-md bg-emerald-300 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50" disabled={Boolean(busy)} onClick={() => void decide("approve")} type="button">{busy === "approve" ? "批准中..." : "批准并生成草稿"}</button>
                  <button className="rounded-md bg-rose-400/15 px-3 py-2 text-sm font-semibold text-rose-100 disabled:opacity-50" disabled={Boolean(busy)} onClick={() => void decide("reject")} type="button">拒绝</button>
                  <button className="rounded-md px-3 py-2 text-sm font-semibold text-slate-500 hover:bg-white/5 disabled:opacity-50" disabled={Boolean(busy)} onClick={() => void decide("cancel")} type="button">取消</button>
                </div>
              )}
            </section>
          ) : (
            <div className="flex min-h-[520px] items-center justify-center border-y border-white/10 text-sm text-slate-500">选择一条提案查看详情。</div>
          )}
        </div>
      </main>
    </PageContainer>
  );
}
