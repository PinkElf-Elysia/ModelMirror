import {
  ArrowLeft,
  CheckCircle2,
  CircleAlert,
  Clock3,
  FilePenLine,
  FileSearch,
  RefreshCw,
  Send,
  ShieldCheck,
  Square,
} from "lucide-react";
import {
  type FormEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import ReactMarkdown from "react-markdown";
import { Link } from "react-router-dom";
import remarkGfm from "remark-gfm";
import CodingChangesPanel from "../components/CodingChangesPanel";
import PageContainer from "../components/PageContainer";
import type {
  CodingCapabilities,
  CodingDraftChanges,
  CodingEvent,
  CodingPlanEntry,
} from "../types/coding";
import {
  cancelCodingTurn,
  CodingApiError,
  connectCodingEvents,
  createCodingSession,
  discardCodingChanges,
  getCodingCapabilities,
  getCodingChanges,
  getCodingPatch,
  startCodingTurn,
  validateCodingChanges,
} from "../utils/codingApi";

type RunState = "idle" | "starting" | "running" | "stopping" | "error";
type CapabilityState = "loading" | "ready" | "error";

interface ToolActivity {
  id: string;
  kind: string;
  status: string;
  title: string;
}

const capabilityReason: Record<string, string> = {
  disabled: "管理员尚未启用代码助手，其他功能不受影响。",
  not_configured: "代码服务已启动，但所需的模型连接信息尚未配置。",
  worker_unavailable: "代码服务当前不可用，请确认服务是否已启动。",
  mode_mismatch: "代码服务的运行模式与页面配置不一致，请检查服务配置。",
};

const errorMessage: Record<string, string> = {
  concurrency_limit: "代码助手正在处理另一个问题，请稍后再试。",
  turn_in_progress: "当前问题仍在处理，请先停止或等待完成。",
  prompt_too_long: "问题超过 20,000 字符，请缩短后重试。",
  session_not_found: "本次使用记录已经过期，请重新提交问题。",
  worker_unavailable: "无法连接代码服务，请检查服务状态。",
  agent_turn_failed: "代码分析未完成，请稍后重试。",
  draft_policy_violation: "本轮修改超出安全范围，已自动撤销。",
  draft_busy: "代码助手仍在处理，请等待本轮结束。",
  validation_failed: "修改检查尚未通过，请先修正后再下载。",
};

function describeError(error: unknown) {
  if (error instanceof CodingApiError) {
    return errorMessage[error.code] ?? "代码助手暂时无法回答，请检查服务状态后重试。";
  }
  return "代码助手暂时无法回答，请检查服务状态后重试。";
}

function statusLabel(state: RunState) {
  if (state === "starting") return "正在准备分析";
  if (state === "running") return "正在分析";
  if (state === "stopping") return "正在停止";
  if (state === "error") return "本轮失败";
  return "等待问题";
}

function planStatusLabel(status: string) {
  if (status === "completed") return "完成";
  if (status === "in_progress") return "进行中";
  if (status === "cancelled") return "已取消";
  return "等待";
}

function toolKindLabel(kind: string) {
  if (kind === "read") return "读取文件";
  if (kind === "list") return "查看目录";
  if (kind === "glob") return "查找文件";
  if (kind === "grep") return "搜索内容";
  if (kind === "lsp") return "分析代码结构";
  if (kind === "edit") return "准备修改文件";
  return "查阅代码";
}

function CodingSidebar({ isDraft }: { isDraft: boolean }) {
  return (
    <div>
      <Link
        className="inline-flex items-center gap-2 text-sm font-semibold text-slate-300 transition hover:text-white"
        to="/studio"
      >
        <ArrowLeft aria-hidden="true" size={16} />
        返回 Studio
      </Link>
      <div className="mt-5">
        <p className="text-sm font-semibold text-white">
          {isDraft ? "修改范围清晰可控" : "你可以放心提问"}
        </p>
        <ul className="mt-3 space-y-3 text-xs leading-5 text-slate-400">
          <li>
            {isDraft
              ? "所有修改只保存在临时副本，不会直接改变真实项目。"
              : "只查看固定的 ModelMirror 项目代码。"}
          </li>
          <li>不会执行命令、运行测试或访问外部网站。</li>
          <li>
            {isDraft
              ? "只新增或修改文本文件，不会删除、重命名或提交代码。"
              : "不会修改文件、生成变更或提交代码。"}
          </li>
          <li>问题与回答只临时保留，服务重启后清除。</li>
        </ul>
      </div>
    </div>
  );
}

export default function CodingPage() {
  const [capabilityState, setCapabilityState] =
    useState<CapabilityState>("loading");
  const [capabilities, setCapabilities] = useState<CodingCapabilities | null>(
    null,
  );
  const [runState, setRunState] = useState<RunState>("idle");
  const [prompt, setPrompt] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [events, setEvents] = useState<CodingEvent[]>([]);
  const [error, setError] = useState("");
  const [transportWarning, setTransportWarning] = useState("");
  const [draftChanges, setDraftChanges] =
    useState<CodingDraftChanges | null>(null);
  const [draftLoading, setDraftLoading] = useState(false);
  const [draftError, setDraftError] = useState("");
  const [draftNotice, setDraftNotice] = useState("");
  const closeStreamRef = useRef<null | (() => void)>(null);
  const lastSeqRef = useRef(0);
  const isDraftMode = capabilities?.mode === "draft";

  const loadCapabilities = useCallback(async () => {
    setCapabilityState("loading");
    setError("");
    try {
      const result = await getCodingCapabilities();
      setCapabilities(result);
      if (result.mode !== "draft") {
        setDraftChanges(null);
        setDraftError("");
        setDraftNotice("");
      }
      setCapabilityState("ready");
    } catch {
      setCapabilities(null);
      setCapabilityState("error");
    }
  }, []);

  useEffect(() => {
    void loadCapabilities();
    return () => closeStreamRef.current?.();
  }, [loadCapabilities]);

  const answer = useMemo(
    () =>
      events
        .filter((event) => event.type === "answer_delta")
        .map((event) => event.data.text ?? "")
        .join(""),
    [events],
  );

  const plan = useMemo<CodingPlanEntry[]>(() => {
    const latest = [...events].reverse().find((event) => event.type === "plan");
    return latest?.data.entries ?? [];
  }, [events]);

  const refreshDraftChanges = useCallback(
    async (activeSessionId: string, runValidation: boolean) => {
      setDraftLoading(true);
      setDraftError("");
      try {
        const result = runValidation
          ? await validateCodingChanges(activeSessionId)
          : await getCodingChanges(activeSessionId);
        setDraftChanges(result);
      } catch (requestError) {
        setDraftError(describeError(requestError));
      } finally {
        setDraftLoading(false);
      }
    },
    [],
  );

  const tools = useMemo<ToolActivity[]>(() => {
    const byId = new Map<string, ToolActivity>();
    events
      .filter((event) => event.type === "tool_status")
      .forEach((event, index) => {
        const id = event.data.tool_call_id || `tool-${index}`;
        byId.set(id, {
          id,
          title:
            event.data.kind === "edit"
              ? "准备修改文件"
              : event.data.title || "代码读取",
          kind: event.data.kind || "read",
          status: event.data.status || "pending",
        });
      });
    return [...byId.values()];
  }, [events]);

  const handleCodingEvent = useCallback(
    (event: CodingEvent) => {
      if (event.seq <= lastSeqRef.current) return;
      lastSeqRef.current = event.seq;
      setTransportWarning("");
      setEvents((current) => [...current, event]);
      if (event.type === "turn_completed") {
        setRunState("idle");
        if (isDraftMode) {
          setDraftNotice("本轮已完成，修改草稿和检查结果已更新。");
          window.setTimeout(
            () => void refreshDraftChanges(event.session_id, true),
            80,
          );
        }
      } else if (event.type === "cancelled") {
        setRunState("idle");
        if (isDraftMode) {
          setDraftNotice("本轮修改已撤销，此前保留的草稿不受影响。");
          window.setTimeout(
            () => void refreshDraftChanges(event.session_id, false),
            80,
          );
        }
      } else if (event.type === "failed") {
        closeStreamRef.current?.();
        setRunState("error");
        setError(
          errorMessage[event.data.code ?? ""] ??
            "本轮处理未完成，请查看提示后重试。",
        );
        if (isDraftMode) {
          setDraftNotice("本轮修改已撤销，此前保留的草稿不受影响。");
          window.setTimeout(
            () => void refreshDraftChanges(event.session_id, false),
            80,
          );
        } else {
          setSessionId(null);
          lastSeqRef.current = 0;
        }
      } else if (event.type === "turn_started") {
        setRunState("running");
      }
    },
    [isDraftMode, refreshDraftChanges],
  );

  const submitPrompt = async (event?: FormEvent) => {
    event?.preventDefault();
    const question = prompt.trim();
    if (
      !question ||
      !capabilities?.available ||
      runState === "starting" ||
      runState === "running" ||
      runState === "stopping"
    ) {
      return;
    }

    closeStreamRef.current?.();
    setRunState("starting");
    setError("");
    setTransportWarning("");
    setDraftError("");
    setDraftNotice("");
    setEvents([]);
    try {
      let activeSessionId = sessionId;
      if (!activeSessionId) {
        const session = await createCodingSession();
        activeSessionId = session.id;
        setSessionId(session.id);
        if (isDraftMode) {
          setDraftChanges(null);
        }
      }
      const after = lastSeqRef.current;
      await startCodingTurn(activeSessionId, question);
      setPrompt("");
      setRunState("running");
      closeStreamRef.current = connectCodingEvents(activeSessionId, after, {
        onEvent: handleCodingEvent,
        onTransportError: () => {
          setTransportWarning("回答连接暂时中断，页面正在自动恢复。");
        },
      });
    } catch (requestError) {
      if (
        !isDraftMode ||
        (requestError instanceof CodingApiError &&
          requestError.code === "session_not_found")
      ) {
        setSessionId(null);
        lastSeqRef.current = 0;
        setDraftChanges(null);
      }
      setRunState("error");
      setError(describeError(requestError));
    }
  };

  const stopTurn = async () => {
    if (!sessionId || (runState !== "running" && runState !== "starting")) return;
    setRunState("stopping");
    setError("");
    try {
      const result = await cancelCodingTurn(sessionId);
      if (!result.accepted) {
        setRunState("idle");
      }
    } catch (requestError) {
      setRunState("error");
      setError(describeError(requestError));
    }
  };

  const handlePromptKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      void submitPrompt();
    }
  };

  const checkDraft = async () => {
    if (!sessionId) return;
    const result = await validateCodingChanges(sessionId);
    setDraftChanges(result);
    setDraftNotice("检查结果已更新。");
  };

  const discardDraft = async () => {
    if (!sessionId) return;
    const result = await discardCodingChanges(sessionId);
    setDraftChanges(result);
    setDraftNotice("修改草稿已放弃，临时副本已恢复到最初状态。");
  };

  const downloadDraft = async () => {
    if (!sessionId || !draftChanges) return;
    const { blob, filename } = await getCodingPatch(
      sessionId,
      draftChanges.revision,
    );
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    setDraftNotice("Diff 已下载，真实项目仍未发生改变。");
  };

  const serviceAvailable = capabilities?.available === true;
  const isBusy = ["starting", "running", "stopping"].includes(runState);

  return (
    <PageContainer
      contentClassName="min-w-0"
      maxWidthClassName="max-w-[1360px]"
      sidebar={<CodingSidebar isDraft={isDraftMode} />}
    >
      <header className="mb-5 border-b border-white/10 pb-5">
        <Link
          className="mb-4 inline-flex items-center gap-2 text-sm font-semibold text-slate-300 transition hover:text-white xl:hidden"
          to="/studio"
        >
          <ArrowLeft aria-hidden="true" size={16} />
          返回 Studio
        </Link>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1.5 rounded-full border border-cyan-300/30 bg-cyan-300/10 px-2.5 py-1 text-xs font-semibold text-cyan-100">
                {isDraftMode ? (
                  <FilePenLine aria-hidden="true" size={14} />
                ) : (
                  <ShieldCheck aria-hidden="true" size={14} />
                )}
                {isDraftMode ? "修改草稿，不会直接改变项目" : "只读实验"}
              </span>
              <span className="rounded-full border border-white/10 bg-white/[0.045] px-2.5 py-1 text-xs text-slate-300">
                固定项目：ModelMirror
              </span>
            </div>
            <h1 className="mt-3 text-2xl font-semibold tracking-[-0.025em] text-white sm:text-3xl">
              {isDraftMode ? "代码协作工作台" : "代码问答工作台"}
            </h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
              {isDraftMode
                ? "描述希望调整的内容。代码助手会在临时副本中准备修改，你可以逐个文件查看，再下载 Diff。修改不会自动写回项目。"
                : "你可以询问功能如何实现、页面与服务如何配合，或某段代码的作用。代码助手只能查看项目并回答，不会修改文件或执行命令。"}
            </p>
          </div>
          <button
            className="inline-flex min-h-10 items-center justify-center gap-2 self-start rounded-lg border border-white/10 bg-white/[0.045] px-3 text-sm font-semibold text-slate-200 transition hover:border-cyan-300/35 hover:bg-cyan-300/10 hover:text-cyan-100 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={capabilityState === "loading" || isBusy}
            onClick={() => void loadCapabilities()}
            type="button"
          >
            <RefreshCw
              aria-hidden="true"
              className={capabilityState === "loading" ? "animate-spin" : ""}
              size={16}
            />
            刷新服务状态
          </button>
        </div>
      </header>

      <section
        aria-live="polite"
        className={`mb-5 flex items-start gap-3 rounded-lg px-4 py-3 ${
          capabilityState === "loading"
            ? "bg-white/[0.045] text-slate-300"
            : serviceAvailable
              ? "bg-emerald-300/10 text-emerald-100"
              : "bg-amber-300/10 text-amber-100"
        }`}
      >
        {capabilityState === "loading" ? (
          <Clock3 aria-hidden="true" className="mt-0.5 shrink-0" size={18} />
        ) : serviceAvailable ? (
          <CheckCircle2 aria-hidden="true" className="mt-0.5 shrink-0" size={18} />
        ) : (
          <CircleAlert aria-hidden="true" className="mt-0.5 shrink-0" size={18} />
        )}
        <div>
          <p className="text-sm font-semibold">
            {capabilityState === "loading"
              ? "正在检查代码服务"
              : serviceAvailable
                ? isDraftMode
                  ? "代码助手可以准备修改草稿"
                  : "代码助手可以使用"
                : "代码助手暂时不可用"}
          </p>
          <p className="mt-1 text-xs leading-5 opacity-80">
            {capabilityState === "loading"
              ? "确认代码服务安全可用后，输入框会自动开放。"
              : serviceAvailable
                ? isDraftMode
                  ? "修改只保存在临时副本。回答结束后，页面会自动列出文件并检查常见问题。"
                  : "一次只处理一个问题，最长可输入 20,000 字符，闲置 30 分钟后会自动清理。"
                : capabilityReason[capabilities?.reason ?? ""] ??
                  (error || "暂时无法确认代码服务状态。")}
          </p>
        </div>
      </section>

      <div className="grid min-w-0 gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="flex min-w-0 flex-col gap-5">
          <section
            className={`order-3 rounded-lg bg-ink-950/72 ${
              isDraftMode ? "" : "min-h-[360px]"
            }`}
          >
            <div className="flex items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
              <div>
                <h2 className="text-sm font-semibold text-white">分析回答</h2>
                <p className="mt-1 text-xs text-slate-500">
                  回答会在生成时逐步显示，内部运行记录不会出现在页面中。
                </p>
              </div>
              <span className="rounded-full bg-white/[0.055] px-2.5 py-1 text-xs text-slate-300">
                {answer && runState === "idle"
                  ? "回答完成"
                  : statusLabel(runState)}
              </span>
            </div>
            <div
              aria-live="polite"
              className={`${isDraftMode ? "min-h-24" : "min-h-[286px]"} p-4 sm:p-5`}
            >
              {answer ? (
                <div className="max-w-none break-words text-sm leading-7 text-slate-200 [&_a]:text-cyan-200 [&_a]:underline [&_blockquote]:my-4 [&_blockquote]:border-l [&_blockquote]:border-white/20 [&_blockquote]:pl-4 [&_code]:text-cyan-100 [&_h1]:mb-4 [&_h1]:text-xl [&_h1]:font-semibold [&_h1]:text-white [&_h2]:mb-3 [&_h2]:mt-6 [&_h2]:text-lg [&_h2]:font-semibold [&_h2]:text-white [&_h3]:mb-2 [&_h3]:mt-5 [&_h3]:font-semibold [&_h3]:text-white [&_li]:my-1 [&_ol]:my-3 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:mb-3 [&_pre]:my-4 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-black/35 [&_pre]:p-4 [&_table]:my-4 [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto [&_td]:border [&_td]:border-white/10 [&_td]:px-3 [&_td]:py-2 [&_th]:border [&_th]:border-white/10 [&_th]:px-3 [&_th]:py-2 [&_ul]:my-3 [&_ul]:list-disc [&_ul]:pl-5">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{answer}</ReactMarkdown>
                  {runState === "running" ? (
                    <span
                      aria-label="回答生成中"
                      className="ml-1 inline-block h-4 w-1 animate-pulse bg-cyan-300 align-middle"
                    />
                  ) : null}
                </div>
              ) : isBusy ? (
                <div className="space-y-3" aria-label="代码分析中">
                  <div className="h-4 w-4/5 animate-pulse rounded bg-white/10" />
                  <div className="h-4 w-full animate-pulse rounded bg-white/10" />
                  <div className="h-4 w-2/3 animate-pulse rounded bg-white/10" />
                </div>
              ) : (
                <div
                  className={`flex flex-col items-center justify-center text-center ${
                    isDraftMode ? "min-h-24" : "min-h-[250px]"
                  }`}
                >
                  {isDraftMode ? (
                    <FilePenLine
                      aria-hidden="true"
                      className="text-cyan-200"
                      size={28}
                    />
                  ) : (
                    <FileSearch
                      aria-hidden="true"
                      className="text-cyan-200"
                      size={28}
                    />
                  )}
                  <p className="mt-4 text-sm font-semibold text-white">
                    {isDraftMode
                      ? "说清楚想调整什么"
                      : "从一个可验证的问题开始"}
                  </p>
                  <p className="mt-2 max-w-md text-sm leading-6 text-slate-400">
                    {isDraftMode
                      ? "例如：把代码助手页面的空白提示改得更容易理解，并说明修改原因。"
                      : "例如：说明聊天回答如何从服务端显示到页面，并指出出现错误时由哪里处理。"}
                  </p>
                </div>
              )}
            </div>
          </section>

          <form
            className="order-1 rounded-lg border border-white/10 bg-surface-900/88 p-4"
            onSubmit={(event) => void submitPrompt(event)}
          >
            <label className="text-sm font-semibold text-white" htmlFor="coding-prompt">
              {isDraftMode ? "描述希望调整的内容" : "提交代码问题"}
            </label>
            <textarea
              aria-describedby="coding-prompt-help"
              className="mt-3 min-h-32 w-full resize-y rounded-lg border border-white/10 bg-ink-950/75 px-3 py-3 text-sm leading-6 text-white outline-none transition placeholder:text-slate-500 hover:border-white/20 focus:border-cyan-300/70 focus:ring-4 focus:ring-cyan-300/10 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={!serviceAvailable || isBusy}
              id="coding-prompt"
              maxLength={capabilities?.limits.max_prompt_chars ?? 20_000}
              onChange={(event) => setPrompt(event.target.value)}
              onKeyDown={handlePromptKeyDown}
              placeholder={
                isDraftMode
                  ? "用日常语言说明目标和期望结果，不需要填写命令。"
                  : "描述想了解的功能或问题，不需要填写路径和命令。"
              }
              value={prompt}
            />
            <div
              className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
              id="coding-prompt-help"
            >
              <div className="text-xs text-slate-500">
                Ctrl/⌘ + Enter 提交
                <span className="ml-3">
                  {prompt.length.toLocaleString("zh-CN")} /{" "}
                  {(capabilities?.limits.max_prompt_chars ?? 20_000).toLocaleString(
                    "zh-CN",
                  )}
                </span>
              </div>
              <div className="flex gap-2">
                {isBusy ? (
                  <button
                    className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-rose-300/35 bg-rose-300/10 px-4 text-sm font-semibold text-rose-100 transition hover:bg-rose-300/20 disabled:cursor-not-allowed disabled:opacity-50"
                    disabled={runState === "stopping"}
                    onClick={() => void stopTurn()}
                    type="button"
                  >
                    <Square aria-hidden="true" fill="currentColor" size={13} />
                    停止分析
                  </button>
                ) : (
                  <button
                    className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-cyan-300 px-4 text-sm font-semibold text-ink-950 transition hover:bg-cyan-200 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
                    disabled={!serviceAvailable || !prompt.trim()}
                    type="submit"
                  >
                    <Send aria-hidden="true" size={16} />
                    {isDraftMode ? "开始处理" : "提交问题"}
                  </button>
                )}
              </div>
            </div>
          </form>

          {error || transportWarning ? (
            <div
              aria-live={error ? "assertive" : "polite"}
              className={`order-2 rounded-lg px-4 py-3 text-sm leading-6 ${
                error
                  ? "bg-rose-300/10 text-rose-100"
                  : "bg-amber-300/10 text-amber-100"
              }`}
              role={error ? "alert" : "status"}
            >
              {error || transportWarning}
            </div>
          ) : null}

          {draftNotice && isDraftMode ? (
            <div
              aria-live="polite"
              className="order-2 flex items-start gap-2 rounded-lg bg-cyan-300/10 px-4 py-3 text-sm leading-6 text-cyan-100"
              role="status"
            >
              <CheckCircle2
                aria-hidden="true"
                className="mt-0.5 shrink-0"
                size={17}
              />
              {draftNotice}
            </div>
          ) : null}

          {draftError && isDraftMode ? (
            <div
              aria-live="assertive"
              className="order-2 flex items-start gap-2 rounded-lg bg-rose-300/10 px-4 py-3 text-sm leading-6 text-rose-100"
              role="alert"
            >
              <CircleAlert
                aria-hidden="true"
                className="mt-0.5 shrink-0"
                size={17}
              />
              {draftError}
            </div>
          ) : null}

          {isDraftMode ? (
            <div className="order-4">
              <CodingChangesPanel
                changes={draftChanges}
                disabled={isBusy}
                loading={draftLoading}
                onDiscard={discardDraft}
                onDownload={downloadDraft}
                onValidate={checkDraft}
                sessionId={sessionId}
              />
            </div>
          ) : null}
        </div>

        <aside className="min-w-0 space-y-5">
          <section className="rounded-lg border border-white/10 bg-ink-950/72">
            <div className="border-b border-white/10 px-4 py-3">
              <h2 className="text-sm font-semibold text-white">分析计划</h2>
              <p className="mt-1 text-xs text-slate-500">
                如果问题较复杂，代码助手会在这里列出准备查看的内容。
              </p>
            </div>
            {plan.length ? (
              <ol className="space-y-3 p-4">
                {plan.map((entry, index) => (
                  <li className="flex gap-3 text-sm" key={`${entry.content}-${index}`}>
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white/[0.065] text-xs font-semibold text-slate-300">
                      {index + 1}
                    </span>
                    <div className="min-w-0">
                      <p className="break-words leading-6 text-slate-200">
                        {entry.content}
                      </p>
                      <span className="mt-1 inline-block text-xs text-slate-500">
                        {planStatusLabel(entry.status)}
                      </span>
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="p-4 text-sm leading-6 text-slate-500">
                提交问题后，分析步骤会在需要时出现。简单问题可能直接返回回答。
              </p>
            )}
          </section>

          <section className="rounded-lg border border-white/10 bg-ink-950/72">
            <div className="border-b border-white/10 px-4 py-3">
              <h2 className="text-sm font-semibold text-white">查阅记录</h2>
              <p className="mt-1 text-xs text-slate-500">
                显示代码助手查看过的内容，不展示文件原文。
              </p>
            </div>
            {tools.length ? (
              <div className="divide-y divide-white/10">
                {tools.map((tool) => (
                  <details className="group px-4 py-3" key={tool.id}>
                    <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-sm">
                      <span className="min-w-0 truncate font-medium text-slate-200">
                        {tool.title}
                      </span>
                      <span
                        className={`shrink-0 rounded-full px-2 py-1 text-[11px] font-semibold ${
                          tool.status === "completed"
                            ? "bg-emerald-300/10 text-emerald-100"
                            : "bg-white/[0.06] text-slate-300"
                        }`}
                      >
                        {tool.status === "completed" ? "完成" : "进行中"}
                      </span>
                    </summary>
                    <dl className="mt-3 grid grid-cols-[72px_minmax(0,1fr)] gap-2 text-xs">
                      <dt className="text-slate-500">查阅方式</dt>
                      <dd className="break-words text-slate-300">
                        {toolKindLabel(tool.kind)}
                      </dd>
                    </dl>
                  </details>
                ))}
              </div>
            ) : (
              <p className="p-4 text-sm leading-6 text-slate-500">
                文件查看、目录浏览、内容搜索和代码结构分析会按步骤显示在这里。
              </p>
            )}
          </section>
        </aside>
      </div>
    </PageContainer>
  );
}
