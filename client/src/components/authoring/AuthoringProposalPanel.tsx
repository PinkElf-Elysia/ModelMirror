import { useCallback, useEffect, useState } from "react";

interface ProposalSummary {
  proposal_id: string;
  kind:
    | "xpert_create"
    | "xpert_update"
    | "prompt_profile_update"
    | "skill_create"
    | "skill_update";
  title: string;
  status: "pending" | "approved" | "rejected" | "cancelled" | "conflict";
  revision: number;
  apply_key: string;
  target_id?: string | null;
  source_type: string;
  source_id: string;
  source_xpert_id?: string | null;
  validation?: { valid?: boolean; issues?: Array<{ message?: string }> };
  payload?: Record<string, unknown>;
  payload_bytes?: number;
  applied_resource_id?: string | null;
  error?: string | null;
  updated_at: number;
}

async function readError(response: Response) {
  try {
    const data = (await response.json()) as { detail?: unknown };
    return typeof data.detail === "string"
      ? data.detail
      : JSON.stringify(data.detail ?? `HTTP ${response.status}`);
  } catch {
    return `HTTP ${response.status}`;
  }
}

export default function AuthoringProposalPanel({
  kindPrefix,
  onApplied,
  targetId,
  title = "自编写提案",
}: {
  kindPrefix?: "xpert" | "prompt_profile" | "skill";
  onApplied?: () => void;
  targetId?: string;
  title?: string;
}) {
  const [items, setItems] = useState<ProposalSummary[]>([]);
  const [selected, setSelected] = useState<ProposalSummary | null>(null);
  const [payloadText, setPayloadText] = useState("{}");
  const [proposalTitle, setProposalTitle] = useState("");
  const [status, setStatus] = useState("pending");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ limit: "100" });
      if (status) params.set("status", status);
      if (targetId) params.set("target_id", targetId);
      const response = await fetch(`/api/runtime/authoring-proposals?${params}`);
      if (!response.ok) throw new Error(await readError(response));
      const data = (await response.json()) as { items?: ProposalSummary[] };
      setItems(
        (data.items ?? [])
          .filter((item) => item.source_type !== "skill_creator")
          .filter((item) =>
            kindPrefix ? item.kind.startsWith(`${kindPrefix}_`) : true,
          ),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "提案加载失败");
    } finally {
      setLoading(false);
    }
  }, [kindPrefix, status, targetId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function open(item: ProposalSummary) {
    setBusy(item.proposal_id);
    setError("");
    try {
      const response = await fetch(
        `/api/runtime/authoring-proposals/${item.proposal_id}`,
      );
      if (!response.ok) throw new Error(await readError(response));
      const detail = (await response.json()) as ProposalSummary;
      setSelected(detail);
      setProposalTitle(detail.title);
      setPayloadText(JSON.stringify(detail.payload ?? {}, null, 2));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "提案详情加载失败");
    } finally {
      setBusy("");
    }
  }

  async function action(actionName: "save" | "validate" | "approve" | "reject") {
    if (!selected) return;
    setBusy(actionName);
    setError("");
    setNotice("");
    try {
      const url =
        actionName === "save"
          ? `/api/runtime/authoring-proposals/${selected.proposal_id}`
          : `/api/runtime/authoring-proposals/${selected.proposal_id}/${actionName}`;
      const body =
        actionName === "save"
          ? {
              revision: selected.revision,
              title: proposalTitle.trim(),
              payload: JSON.parse(payloadText) as Record<string, unknown>,
            }
          : {
              revision: selected.revision,
              ...(actionName === "approve"
                ? { apply_key: selected.apply_key }
                : {}),
              reason: actionName === "reject" ? "管理侧拒绝" : "",
            };
      const response = await fetch(url, {
        method: actionName === "save" ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error(await readError(response));
      const updated = (await response.json()) as ProposalSummary;
      setSelected(updated);
      setProposalTitle(updated.title);
      setPayloadText(JSON.stringify(updated.payload ?? {}, null, 2));
      setNotice(
        actionName === "approve"
          ? "提案已批准，仅写入草稿；仍需显式发布或安装。"
          : actionName === "reject"
            ? "提案已拒绝，目标资源未发生变化。"
            : actionName === "validate"
              ? updated.validation?.valid
                ? "提案校验通过。"
                : "提案校验未通过。"
              : "提案已保存。",
      );
      await load();
      if (actionName === "approve") onApplied?.();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "提案操作失败");
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="mb-5 rounded-lg border border-white/10 bg-white/[0.035] p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-white">{title}</h2>
          <p className="mt-1 text-xs text-slate-400">
            智能体只能提交版本化提案；批准后仍不会自动发布智能体、Prompt 或安装 Skill。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="h-9 rounded-md border border-white/10 bg-ink-950 px-2 text-xs text-slate-200"
            onChange={(event) => setStatus(event.target.value)}
            value={status}
          >
            <option value="pending">待审核</option>
            <option value="approved">已批准</option>
            <option value="rejected">已拒绝</option>
            <option value="conflict">有冲突</option>
            <option value="">全部</option>
          </select>
          <button
            className="h-9 rounded-md border border-white/10 px-3 text-xs font-semibold text-slate-300 hover:border-hire-300/35"
            onClick={() => void load()}
            type="button"
          >
            刷新
          </button>
        </div>
      </div>
      {error ? <p className="mt-3 rounded-md border border-rose-300/25 bg-rose-300/10 p-2 text-xs text-rose-100">{error}</p> : null}
      {notice ? <p className="mt-3 rounded-md border border-emerald-300/25 bg-emerald-300/10 p-2 text-xs text-emerald-100">{notice}</p> : null}
      <div className="mt-4 grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
        <div className="max-h-72 space-y-2 overflow-y-auto">
          {loading ? <p className="text-xs text-slate-500">正在加载提案...</p> : null}
          {!loading && items.length === 0 ? (
            <p className="rounded-md border border-dashed border-white/10 p-4 text-center text-xs text-slate-500">暂无匹配提案</p>
          ) : null}
          {items.map((item) => (
            <button
              className={`w-full rounded-md border p-3 text-left transition ${selected?.proposal_id === item.proposal_id ? "border-hire-300/45 bg-hire-300/10" : "border-white/10 bg-ink-950/55 hover:border-white/20"}`}
              key={item.proposal_id}
              onClick={() => void open(item)}
              type="button"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-xs font-semibold text-white">{item.title}</span>
                <span className="text-[10px] uppercase text-slate-500">{item.status}</span>
              </div>
              <p className="mt-1 text-[11px] text-slate-400">{item.kind} · revision {item.revision}</p>
            </button>
          ))}
        </div>
        {selected ? (
          <div className="min-w-0 rounded-md border border-white/10 bg-ink-950/45 p-3">
            <input
              className="h-9 w-full rounded-md border border-white/10 bg-white/[0.04] px-3 text-sm text-white outline-none focus:border-hire-300/50"
              disabled={selected.status !== "pending"}
              onChange={(event) => setProposalTitle(event.target.value)}
              value={proposalTitle}
            />
            <textarea
              className="mt-3 min-h-52 w-full resize-y rounded-md border border-white/10 bg-black/20 p-3 font-mono text-xs leading-5 text-slate-200 outline-none focus:border-hire-300/50"
              disabled={selected.status !== "pending"}
              onChange={(event) => setPayloadText(event.target.value)}
              value={payloadText}
            />
            {selected.error ? <p className="mt-2 text-xs text-rose-200">{selected.error}</p> : null}
            {selected.status === "pending" ? (
              <div className="mt-3 flex flex-wrap gap-2">
                <button className="rounded-md border border-white/10 px-3 py-2 text-xs font-semibold text-slate-200" disabled={Boolean(busy)} onClick={() => void action("save")} type="button">保存编辑</button>
                <button className="rounded-md border border-sky-300/25 bg-sky-300/10 px-3 py-2 text-xs font-semibold text-sky-100" disabled={Boolean(busy)} onClick={() => void action("validate")} type="button">校验</button>
                <button className="rounded-md bg-emerald-300 px-3 py-2 text-xs font-semibold text-ink-950" disabled={Boolean(busy)} onClick={() => void action("approve")} type="button">批准写入草稿</button>
                <button className="rounded-md border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-xs font-semibold text-rose-100" disabled={Boolean(busy)} onClick={() => void action("reject")} type="button">拒绝</button>
              </div>
            ) : null}
          </div>
        ) : (
          <div className="grid min-h-52 place-items-center rounded-md border border-dashed border-white/10 text-xs text-slate-500">选择提案查看与审核</div>
        )}
      </div>
    </section>
  );
}
