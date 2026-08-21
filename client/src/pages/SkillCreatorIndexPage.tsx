import {
  ArrowLeft,
  ArrowRight,
  Clock3,
  FileText,
  LockKeyhole,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import { useSkillCreatorStatus } from "../hooks/useSkillCreatorStatus";
import {
  createSkillCreatorSession,
  listSkillCreatorSessions,
  SkillCreatorApiError,
  type SkillCreatorSession,
  type SkillCreatorSessionState,
} from "../utils/skillCreatorApi";

const STATE_LABELS: Record<SkillCreatorSessionState, string> = {
  defining: "定义用途",
  selecting_evidence: "确认素材",
  editing_draft: "编辑草稿",
  designing_tests: "设计测试",
  reviewing_results: "评审结果",
  iterating: "迭代中",
  completed: "已完成",
  archived: "已归档",
};

function formatTime(value: number) {
  if (!value) return "刚刚";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value * (value < 10_000_000_000 ? 1000 : 1)));
}

function StatusSkeleton() {
  return (
    <div aria-label="正在读取 Skill Creator 状态" className="space-y-4">
      <div className="h-28 animate-pulse rounded-lg bg-white/[0.055] motion-reduce:animate-none" />
      <div className="h-44 animate-pulse rounded-lg bg-white/[0.045] motion-reduce:animate-none" />
    </div>
  );
}

function DisabledState({ reason }: { reason?: string | null }) {
  return (
    <section className="mx-auto max-w-2xl rounded-lg border border-amber-300/25 bg-amber-300/[0.07] p-6 sm:p-8">
      <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-amber-300/10 text-amber-100">
        <LockKeyhole aria-hidden="true" size={22} />
      </div>
      <h1 className="mt-5 text-2xl font-semibold text-white">Skill Creator 尚未启用</h1>
      <p className="mt-3 max-w-xl text-sm leading-6 text-slate-300">
        {reason || "当前实例仍在完成 Creator 的质量评测闭环。已安装 Skill 和技能市场不受影响。"}
      </p>
      <div className="mt-6 flex flex-wrap gap-3">
        <Link
          className="inline-flex items-center gap-2 rounded-full bg-hire-300 px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200"
          to="/skills"
        >
          <ArrowLeft aria-hidden="true" size={16} />
          返回 Skill 货架
        </Link>
      </div>
    </section>
  );
}

export default function SkillCreatorIndexPage() {
  const navigate = useNavigate();
  const { status, loading: statusLoading, error: statusError, reload: reloadStatus } =
    useSkillCreatorStatus();
  const [sessions, setSessions] = useState<SkillCreatorSession[]>([]);
  const [intent, setIntent] = useState("");
  const [loadingSessions, setLoadingSessions] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  const loadSessions = useCallback(async () => {
    if (!status?.enabled) return;
    setLoadingSessions(true);
    setError("");
    try {
      const response = await listSkillCreatorSessions();
      setSessions(response.items);
    } catch (caught) {
      setError(
        caught instanceof SkillCreatorApiError
          ? caught.message
          : "最近的 Creator 会话加载失败。",
      );
    } finally {
      setLoadingSessions(false);
    }
  }, [status?.enabled]);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  const activeSessions = useMemo(
    () => sessions.filter((item) => item.state !== "archived"),
    [sessions],
  );

  async function createSession() {
    setCreating(true);
    setError("");
    try {
      const session = await createSkillCreatorSession({
        mode: "blank",
        intent: intent.trim(),
      });
      navigate(`/skills/create/${encodeURIComponent(session.session_id)}`);
    } catch (caught) {
      setError(
        caught instanceof SkillCreatorApiError
          ? caught.message
          : "Creator 会话创建失败。",
      );
    } finally {
      setCreating(false);
    }
  }

  return (
    <PageContainer activeResource="skills" hideSidebar maxWidthClassName="max-w-[1320px]">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <Link
          className="inline-flex items-center gap-2 text-sm font-semibold text-slate-300 transition hover:text-white"
          to="/skills"
        >
          <ArrowLeft aria-hidden="true" size={16} />
          Skill 货架
        </Link>
        {status?.enabled ? (
          <span className="inline-flex items-center gap-2 rounded-full border border-emerald-300/25 bg-emerald-300/10 px-3 py-1.5 text-xs font-semibold text-emerald-100">
            <ShieldCheck aria-hidden="true" size={14} />
            私有控制台 · 草稿不会自动安装
          </span>
        ) : null}
      </div>

      {statusLoading ? <StatusSkeleton /> : null}

      {!statusLoading && statusError ? (
        <section className="mx-auto max-w-2xl rounded-lg border border-rose-300/25 bg-rose-300/10 p-6" role="alert">
          <h1 className="text-xl font-semibold text-white">无法确认 Creator 状态</h1>
          <p className="mt-2 text-sm leading-6 text-rose-50">{statusError}</p>
          <button
            className="mt-5 inline-flex items-center gap-2 rounded-full border border-rose-200/30 px-4 py-2 text-sm font-semibold text-rose-50 transition hover:bg-rose-200/10"
            onClick={() => void reloadStatus()}
            type="button"
          >
            <RefreshCw aria-hidden="true" size={15} />
            重新检查
          </button>
        </section>
      ) : null}

      {!statusLoading && status && !status.enabled ? (
        <DisabledState reason={status.disabled_reason} />
      ) : null}

      {!statusLoading && status?.enabled ? (
        <>
          <header className="border-y border-brand-300/20 py-8 sm:py-10">
            <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px] lg:items-end">
              <div>
                <div className="flex items-center gap-3 text-brand-100">
                  <Sparkles aria-hidden="true" size={22} />
                  <span className="text-sm font-semibold">Skill Creator</span>
                </div>
                <h1 className="mt-4 text-3xl font-semibold text-white sm:text-4xl">
                  说一句需求，让 AI 帮你做成 Skill
                </h1>
                <p className="mt-4 max-w-3xl text-sm leading-7 text-slate-300 sm:text-base">
                  {status.resource_authoring_enabled
                    ? "不需要先懂触发条件、测试夹具或文件结构。AI 会先理解并给出方案，只有在信息不足时才追问。"
                    : "先明确用途和触发条件，再确认素材、审阅文件。模型只能提交类型化提案，批准后仍只是待评测草稿。"}
                </p>
              </div>
              <div className="rounded-lg border border-white/10 bg-surface-900/80 p-4">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm text-slate-400">生成助手</span>
                  <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                    status.model_available
                      ? "bg-emerald-300/10 text-emerald-100"
                      : "bg-amber-300/10 text-amber-100"
                  }`}>
                    {status.model_available ? "可用" : "未配置模型"}
                  </span>
                </div>
                <p className="mt-3 break-all font-mono text-xs text-slate-300">
                  {status.assistant_agent_id}
                </p>
                {!status.model_available ? (
                  <p className="mt-3 text-xs leading-5 text-slate-400">
                    仍可手工创建和编辑空白 Skill，配置模型后再使用生成提案。
                  </p>
                ) : null}
              </div>
            </div>
          </header>

          <section className="mt-8 grid gap-7 lg:grid-cols-[minmax(0,1fr)_minmax(320px,0.72fr)]">
            <div className="rounded-lg border border-brand-300/20 bg-surface-900/80 p-5 sm:p-6">
              <div className="flex items-start gap-4">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-brand-300/10 text-brand-100">
                  <Plus aria-hidden="true" size={20} />
                </div>
                <div>
                  <h2 className="text-xl font-semibold text-white">创建新 Skill</h2>
                  <p className="mt-1 text-sm leading-6 text-slate-400">
                    像向同事交代任务一样写一句话就够了。
                  </p>
                </div>
              </div>
              <label className="mt-6 block" htmlFor="creator-intent">
                <span className="text-sm font-semibold text-slate-200">你希望它帮你完成什么？</span>
                <textarea
                  className="mt-2 min-h-32 w-full resize-y rounded-lg border border-white/10 bg-ink-950/75 px-4 py-3 text-sm leading-6 text-white placeholder:text-slate-400 focus:border-brand-300/60 focus:outline-none"
                  id="creator-intent"
                  maxLength={2000}
                  onChange={(event) => setIntent(event.target.value)}
                  placeholder="例如：每次收到竞品 PDF 时，提取定价、功能差异和证据页码，输出中文对比表。"
                  value={intent}
                />
              </label>
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                <p className="text-xs text-slate-500">内容只保存在服务端 Creator 会话，不写入浏览器存储。</p>
                <button
                  className="inline-flex items-center gap-2 rounded-full bg-brand-200 px-5 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-white disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
                  disabled={creating}
                  onClick={() => void createSession()}
                  type="button"
                >
                  {creating ? "正在准备…" : "开始创建"}
                  <ArrowRight aria-hidden="true" size={16} />
                </button>
              </div>
            </div>

            <aside className="rounded-lg border border-white/10 bg-white/[0.035] p-5 sm:p-6">
              <div className="flex items-center gap-3">
                <FileText aria-hidden="true" className="text-hire-200" size={20} />
                <h2 className="text-base font-semibold text-white">你只负责做决定</h2>
              </div>
              <ol className="mt-5 space-y-4 text-sm">
                {[
                  ["AI 先给方案", "复杂任务才拆资源，简单任务保持简单。"],
                  ["用真实任务试用", "同一条件下比较使用前后的结果。"],
                  ["你确认后才安装", "生成、改进和安装都不会偷偷发生。"],
                ].map(([title, detail], index) => (
                  <li className="flex gap-3" key={title}>
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-hire-300 text-xs font-bold text-ink-950">
                      {index + 1}
                    </span>
                    <div>
                      <p className="font-semibold text-slate-100">{title}</p>
                      <p className="mt-1 leading-5 text-slate-400">{detail}</p>
                    </div>
                  </li>
                ))}
              </ol>
              <p className="mt-5 border-t border-white/10 pt-4 text-xs leading-5 text-slate-500">
                评测与安装始终分离；通过质量门不会自动安装 Skill。
              </p>
            </aside>
          </section>

          <section className="mt-10" aria-labelledby="recent-creator-sessions">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 pb-4">
              <div>
                <h2 className="text-xl font-semibold text-white" id="recent-creator-sessions">最近会话</h2>
                <p className="mt-1 text-sm text-slate-400">刷新页面后可从服务端继续编辑。</p>
              </div>
              <button
                className="inline-flex items-center gap-2 rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06] disabled:opacity-50"
                disabled={loadingSessions}
                onClick={() => void loadSessions()}
                type="button"
              >
                <RefreshCw aria-hidden="true" className={loadingSessions ? "animate-spin motion-reduce:animate-none" : ""} size={15} />
                刷新会话
              </button>
            </div>

            {error ? (
              <p className="mt-4 rounded-lg border border-rose-300/25 bg-rose-300/10 px-4 py-3 text-sm text-rose-50" role="alert">
                {error}
              </p>
            ) : null}

            {loadingSessions ? (
              <div className="mt-4 space-y-2" aria-label="正在加载 Creator 会话">
                {[0, 1, 2].map((item) => (
                  <div className="h-20 animate-pulse rounded-lg bg-white/[0.045] motion-reduce:animate-none" key={item} />
                ))}
              </div>
            ) : null}

            {!loadingSessions && activeSessions.length === 0 ? (
              <div className="mt-4 rounded-lg border border-dashed border-white/15 px-6 py-10 text-center">
                <p className="text-sm font-semibold text-white">还没有 Creator 会话</p>
                <p className="mt-2 text-sm text-slate-400">从上方填写目标，或直接创建一个空白会话。</p>
              </div>
            ) : null}

            {!loadingSessions && activeSessions.length > 0 ? (
              <ul className="mt-4 divide-y divide-white/10 border-y border-white/10">
                {activeSessions.map((session) => (
                  <li key={session.session_id}>
                    <Link
                      className="group flex min-w-0 flex-col gap-3 py-4 transition hover:bg-white/[0.025] sm:flex-row sm:items-center sm:justify-between sm:px-3"
                      to={`/skills/create/${encodeURIComponent(session.session_id)}`}
                    >
                      <div className="min-w-0">
                        <p className="line-clamp-1 font-semibold text-white group-hover:text-brand-100">
                          {session.intent || "未命名 Skill 会话"}
                        </p>
                        <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-400">
                          <span className="inline-flex items-center gap-1.5">
                            <Clock3 aria-hidden="true" size={13} />
                            {formatTime(session.updated_at)}
                          </span>
                          <span>revision {session.session_revision}</span>
                          {session.draft_id ? <span className="text-emerald-200">已生成草稿</span> : null}
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-3">
                        <span className="rounded-full border border-white/10 bg-white/[0.055] px-3 py-1 text-xs font-semibold text-slate-300">
                          {STATE_LABELS[session.state]}
                        </span>
                        <ArrowRight aria-hidden="true" className="text-slate-500 group-hover:text-brand-100" size={17} />
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : null}
          </section>
        </>
      ) : null}
    </PageContainer>
  );
}
