import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";

type ProposalStatus = "pending" | "approved" | "rejected";

interface KnowledgeBase {
  id: string;
  name: string;
}

interface KnowledgeWriteProposal {
  proposal_id: string;
  kb_id: string;
  title: string;
  content: string;
  tags: string[];
  status: ProposalStatus;
  revision: number;
  source_xpert_id?: string | null;
  source_conversation_id?: string | null;
  source_goal_id?: string | null;
  source_handoff_id?: string | null;
  source_run_id?: string | null;
  document_id?: string | null;
  job_id?: string | null;
  candidate_version_id?: string | null;
  build_status?: string | null;
  candidate_ready: boolean;
  candidate_active: boolean;
  decision_reason?: string | null;
  last_error?: string | null;
  created_at: number;
  updated_at: number;
}

function timestamp(value: number) {
  return new Date(value * 1000).toLocaleString("zh-CN", { hour12: false });
}

function statusClass(status: string) {
  if (status === "approved" || status === "succeeded") {
    return "border-emerald-300/25 bg-emerald-300/10 text-emerald-100";
  }
  if (status === "rejected" || status === "failed") {
    return "border-rose-300/25 bg-rose-300/10 text-rose-100";
  }
  return "border-amber-300/25 bg-amber-300/10 text-amber-100";
}

async function responseError(response: Response, fallback: string) {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    return typeof payload.detail === "string" ? payload.detail : fallback;
  } catch {
    return fallback;
  }
}

export default function KnowledgeInboxPage() {
  const { kbId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const [knowledgeBase, setKnowledgeBase] = useState<KnowledgeBase | null>(null);
  const [status, setStatus] = useState<"all" | ProposalStatus>("pending");
  const [proposals, setProposals] = useState<KnowledgeWriteProposal[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [tags, setTags] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const selected = useMemo(
    () => proposals.find((item) => item.proposal_id === selectedId) ?? proposals[0] ?? null,
    [proposals, selectedId],
  );

  useEffect(() => {
    if (!kbId) return;
    void loadInbox(searchParams.get("proposal_id") || undefined);
  }, [kbId, status]);

  useEffect(() => {
    if (!selected) return;
    setSelectedId(selected.proposal_id);
    setTitle(selected.title);
    setContent(selected.content);
    setTags(selected.tags.join(", "));
    setRejectReason("");
  }, [selected?.proposal_id, selected?.revision]);

  async function loadInbox(preferredId?: string) {
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({ kb_id: kbId, limit: "100" });
      if (status !== "all") query.set("status", status);
      const [kbResponse, proposalResponse] = await Promise.all([
        fetch("/api/rag/knowledge_bases"),
        fetch(`/api/rag/knowledge-write-proposals?${query.toString()}`),
      ]);
      if (!kbResponse.ok) throw new Error(await responseError(kbResponse, "知识库暂不可用。"));
      if (!proposalResponse.ok) {
        throw new Error(await responseError(proposalResponse, "Knowledge Inbox 暂不可用。"));
      }
      const kbPayload = (await kbResponse.json()) as { knowledge_bases: KnowledgeBase[] };
      const proposalPayload = (await proposalResponse.json()) as {
        proposals: KnowledgeWriteProposal[];
      };
      setKnowledgeBase(kbPayload.knowledge_bases.find((item) => item.id === kbId) ?? null);
      setProposals(proposalPayload.proposals);
      setSelectedId((current) => {
        const requested = preferredId || current;
        return proposalPayload.proposals.some((item) => item.proposal_id === requested)
          ? requested
          : proposalPayload.proposals[0]?.proposal_id ?? "";
      });
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Knowledge Inbox 加载失败。");
    } finally {
      setLoading(false);
    }
  }

  async function saveProposal() {
    if (!selected || selected.status !== "pending") return;
    setBusy("save");
    setError("");
    try {
      const response = await fetch(
        `/api/rag/knowledge-write-proposals/${selected.proposal_id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            expected_revision: selected.revision,
            title,
            content,
            tags: tags.split(",").map((item) => item.trim()).filter(Boolean),
          }),
        },
      );
      if (!response.ok) throw new Error(await responseError(response, "保存提议失败。"));
      setNotice("提议已保存，活动知识版本未改变。");
      await loadInbox(selected.proposal_id);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "保存提议失败。");
    } finally {
      setBusy("");
    }
  }

  async function decide(action: "approve" | "reject") {
    if (!selected || selected.status !== "pending") return;
    setBusy(action);
    setError("");
    try {
      const response = await fetch(
        `/api/rag/knowledge-write-proposals/${selected.proposal_id}/${action}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            expected_revision: selected.revision,
            reason: action === "reject" ? rejectReason : "",
          }),
        },
      );
      if (!response.ok) {
        throw new Error(
          await responseError(response, action === "approve" ? "批准提议失败。" : "拒绝提议失败。"),
        );
      }
      setNotice(
        action === "approve"
          ? "已批准并创建候选构建任务；通过评估并推广前，活动版本保持不变。"
          : "已拒绝；未创建文档、构建任务或候选版本。",
      );
      await loadInbox();
    } catch (decisionError) {
      setError(decisionError instanceof Error ? decisionError.message : "处理提议失败。");
    } finally {
      setBusy("");
    }
  }

  return (
    <PageContainer>
      <div className="space-y-5 pb-12">
        <header className="flex flex-wrap items-start justify-between gap-4 border-b border-white/10 pb-5">
          <div>
            <p className="text-xs font-semibold uppercase text-sky-200">Knowledge Agent</p>
            <h1 className="mt-2 text-2xl font-bold text-white">Knowledge Inbox</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
              {knowledgeBase?.name ?? kbId} 的知识写入审批中心。批准只会构建候选版本，永不自动激活。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link className="rounded-md border border-white/10 px-3 py-2 text-sm text-slate-200 hover:bg-white/5" to="/rag">
              返回知识库
            </Link>
            <Link className="rounded-md border border-white/10 px-3 py-2 text-sm text-slate-200 hover:bg-white/5" to={`/rag/${kbId}/pipeline`}>
              流水线
            </Link>
            <Link className="rounded-md bg-sky-300 px-3 py-2 text-sm font-semibold text-slate-950 hover:bg-sky-200" to={`/rag/${kbId}/evaluation`}>
              评估与推广
            </Link>
          </div>
        </header>

        <div className="flex flex-wrap items-center gap-2">
          {(["all", "pending", "approved", "rejected"] as const).map((item) => (
            <button
              className={`rounded-md border px-3 py-2 text-xs font-semibold ${status === item ? "border-sky-300/40 bg-sky-300/15 text-sky-100" : "border-white/10 text-slate-400 hover:bg-white/5"}`}
              key={item}
              onClick={() => setStatus(item)}
              type="button"
            >
              {item === "all" ? "全部" : item === "pending" ? "待审批" : item === "approved" ? "已批准" : "已拒绝"}
            </button>
          ))}
          <button className="ml-auto rounded-md border border-white/10 px-3 py-2 text-xs text-slate-300 hover:bg-white/5" onClick={() => void loadInbox()} type="button">
            刷新
          </button>
        </div>

        {error ? <p className="rounded-md border border-rose-300/25 bg-rose-300/10 px-4 py-3 text-sm text-rose-100">{error}</p> : null}
        {notice ? <p className="rounded-md border border-emerald-300/25 bg-emerald-300/10 px-4 py-3 text-sm text-emerald-100">{notice}</p> : null}

        <div className="grid min-h-[560px] gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
          <aside className="overflow-hidden rounded-lg border border-white/10 bg-white/[0.025]">
            <div className="border-b border-white/10 px-4 py-3 text-sm font-semibold text-white">
              写入提议 · {proposals.length}
            </div>
            <div className="max-h-[720px] overflow-y-auto p-2">
              {loading ? <p className="px-3 py-8 text-center text-sm text-slate-500">加载中...</p> : null}
              {!loading && proposals.length === 0 ? (
                <p className="px-3 py-12 text-center text-sm leading-6 text-slate-500">当前筛选下没有提议。</p>
              ) : null}
              {proposals.map((proposal) => (
                <button
                  className={`mb-2 w-full rounded-md border px-3 py-3 text-left ${selected?.proposal_id === proposal.proposal_id ? "border-sky-300/35 bg-sky-300/10" : "border-white/10 bg-white/[0.02] hover:bg-white/5"}`}
                  key={proposal.proposal_id}
                  onClick={() => setSelectedId(proposal.proposal_id)}
                  type="button"
                >
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-sm font-semibold text-white">{proposal.title}</span>
                    <span className={`rounded-full border px-2 py-0.5 text-[10px] ${statusClass(proposal.status)}`}>{proposal.status}</span>
                  </div>
                  <p className="mt-2 line-clamp-2 text-xs leading-5 text-slate-400">{proposal.content}</p>
                  <p className="mt-2 text-[10px] text-slate-500">{timestamp(proposal.updated_at)}</p>
                </button>
              ))}
            </div>
          </aside>

          <main className="rounded-lg border border-white/10 bg-white/[0.025] p-5">
            {!selected ? (
              <div className="flex min-h-[480px] items-center justify-center text-sm text-slate-500">选择一条提议查看详情。</div>
            ) : (
              <div className="space-y-5">
                <div className="flex flex-wrap items-center gap-2 border-b border-white/10 pb-4">
                  <span className={`rounded-full border px-2 py-1 text-xs ${statusClass(selected.status)}`}>{selected.status}</span>
                  {selected.build_status ? <span className={`rounded-full border px-2 py-1 text-xs ${statusClass(selected.build_status)}`}>build: {selected.build_status}</span> : null}
                  <span className="text-xs text-slate-500">revision {selected.revision}</span>
                </div>

                <label className="block text-xs font-semibold text-slate-300">
                  标题
                  <input className="mt-2 w-full rounded-md border border-white/10 bg-slate-950/70 px-3 py-2 text-sm text-white outline-none focus:border-sky-300/50" disabled={selected.status !== "pending"} maxLength={160} onChange={(event) => setTitle(event.target.value)} value={title} />
                </label>
                <label className="block text-xs font-semibold text-slate-300">
                  内容
                  <textarea className="mt-2 min-h-64 w-full resize-y rounded-md border border-white/10 bg-slate-950/70 px-3 py-3 text-sm leading-6 text-white outline-none focus:border-sky-300/50" disabled={selected.status !== "pending"} maxLength={20000} onChange={(event) => setContent(event.target.value)} value={content} />
                  <span className="mt-1 block text-right text-[10px] text-slate-500">{content.length} / 20,000</span>
                </label>
                <label className="block text-xs font-semibold text-slate-300">
                  标签（逗号分隔）
                  <input className="mt-2 w-full rounded-md border border-white/10 bg-slate-950/70 px-3 py-2 text-sm text-white outline-none focus:border-sky-300/50" disabled={selected.status !== "pending"} onChange={(event) => setTags(event.target.value)} value={tags} />
                </label>

                <div className="grid gap-3 border-y border-white/10 py-4 text-xs text-slate-400 sm:grid-cols-2 xl:grid-cols-3">
                  <p>来源智能体：<span className="text-slate-200">{selected.source_xpert_id || "-"}</span></p>
                  <p>Goal / Handoff：<span className="text-slate-200">{selected.source_goal_id || selected.source_handoff_id || "-"}</span></p>
                  <p>Run：<span className="font-mono text-slate-200">{selected.source_run_id || "-"}</span></p>
                  <p>Job：<span className="font-mono text-slate-200">{selected.job_id || "-"}</span></p>
                  <p>候选版本：<span className="font-mono text-slate-200">{selected.candidate_version_id || "-"}</span></p>
                  <p>活动状态：<span className="text-slate-200">{selected.candidate_active ? "已推广" : "未推广"}</span></p>
                </div>

                {selected.last_error ? <p className="rounded-md border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-sm text-rose-100">{selected.last_error}</p> : null}
                {selected.decision_reason ? <p className="text-sm text-slate-400">拒绝原因：{selected.decision_reason}</p> : null}

                {selected.status === "pending" ? (
                  <div className="flex flex-wrap items-end gap-3">
                    <button className="rounded-md border border-white/10 px-4 py-2 text-sm text-slate-200 hover:bg-white/5 disabled:opacity-50" disabled={Boolean(busy) || !title.trim() || !content.trim()} onClick={() => void saveProposal()} type="button">
                      {busy === "save" ? "保存中..." : "保存修改"}
                    </button>
                    <button className="rounded-md bg-emerald-300 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-emerald-200 disabled:opacity-50" disabled={Boolean(busy) || !title.trim() || !content.trim()} onClick={() => void decide("approve")} type="button">
                      {busy === "approve" ? "正在创建候选..." : "批准并构建候选"}
                    </button>
                    <label className="min-w-64 flex-1 text-xs text-slate-400">
                      拒绝原因（可选）
                      <input className="mt-2 w-full rounded-md border border-white/10 bg-slate-950/70 px-3 py-2 text-sm text-white outline-none" onChange={(event) => setRejectReason(event.target.value)} value={rejectReason} />
                    </label>
                    <button className="rounded-md border border-rose-300/25 px-4 py-2 text-sm text-rose-100 hover:bg-rose-300/10 disabled:opacity-50" disabled={Boolean(busy)} onClick={() => void decide("reject")} type="button">
                      {busy === "reject" ? "处理中..." : "拒绝"}
                    </button>
                  </div>
                ) : null}

                {selected.status === "approved" ? (
                  <p className="rounded-md border border-sky-300/20 bg-sky-300/10 px-4 py-3 text-sm leading-6 text-sky-100">
                    候选构建完成后，请在评估页运行 Evaluation Gate，并通过“推广”切换活动版本。直接激活已被服务端阻断。
                  </p>
                ) : null}
              </div>
            )}
          </main>
        </div>
      </div>
    </PageContainer>
  );
}
